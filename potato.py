#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
POTATO POC

Reads cycling power from a BLE home trainer (e.g. Wahoo KICKR),
maps it through a tanh curve, and feeds it into the right trigger of
a virtual Xbox 360 controller. Optionally binds Left/Right arrow keys
to the D‐pad (for compatibility with Zwift Play).

changes in this Version -> its pressed  the A Button instead of trigger use. 
"""

# Standard lib
import asyncio
import threading
import math
import argparse

# Third-party
import keyboard      # pip install keyboard
import vgamepad      # pip install vgamepad
from vgamepad import XUSB_BUTTON
from bleak import BleakClient, BleakScanner

# UUID for the BLE Cycling Power Measurement characteristic
CPM_UUID = "00002a63-0000-1000-8000-00805f9b34fb"

def parse_cycling_power(data: bytearray) -> int:
    """
    Extract instantaneous power (in watts) from the raw Cycling Power Measurement packet.
    Format (little‐endian, signed) at bytes 2–3.

    :param data: bytearray from BLE notification
    :return: signed integer power in watts, or 0 if packet is too short
    """
    if len(data) < 4:
        return 0
    return int.from_bytes(data[2:4], byteorder='little', signed=True)

class KickrController:
    """
    Manages BLE connection to the KICKR, receives power notifications,
    and presses a virtual gamepad button when power exceeds a threshold.
    """

    def __init__(self, ftp, device_name, threshold, update_callback):
        """
        :param ftp: functional threshold power (not used for button press, but kept for context)
        :param device_name: BLE name (partial match) of the trainer
        :param threshold: minimum power (watts) required to engage the button
        :param update_callback: function(power_watts: int, is_pressed: bool)
        """
        self.ftp = ftp
        self.device_name = device_name.upper()
        self.threshold = threshold
        self.client = None
        self.power = 0
        # NEU: Zustand der Taste verfolgen, um Befehls-Spam zu vermeiden
        self.button_pressed = False
        self.update_callback = update_callback
        self.gamepad = vgamepad.VX360Gamepad()

    async def connect(self) -> bool:
        """
        Scan for BLE devices, find one whose name matches, and attempt to connect.
        """
        print("Scanning for BLE devices...")
        try:
            devices = await asyncio.wait_for(BleakScanner.discover(), timeout=10)
        except asyncio.TimeoutError:
            print("Scan timed out.")
            return False

        device = next(
            (d for d in devices if d.name and self.device_name in d.name.upper()),
            None)

        if not device:
            print(f"No device named '{self.device_name}' found.")
            return False

        print(f"Found {device.name} @ {device.address}. Connecting…")
        self.client = BleakClient(device.address)

        try:
            await self.client.connect()
            print(f"Connected to {device.name}")
            return True
        except Exception as e:
            print(f"Connection error: {e}")
            return False

    async def handle_power_notify(self, sender, data: bytearray):
        """
        Callback invoked on each power notification.
        Parses power and presses or releases a virtual button based on the threshold.

        :param sender: BLE characteristic UUID (unused)
        :param data: raw notification bytes
        """
        self.power = parse_cycling_power(data)
        
        # Prüfen, ob die Leistung den Schwellenwert überschreitet
        is_active = self.power >= self.threshold

        # NUR den Zustand des Gamepads aktualisieren, WENN sich etwas ändert.
        # Dies ist der entscheidende Teil, um Fehler zu vermeiden und die Logik sauber zu halten.
        if is_active and not self.button_pressed:
            # Zustand wechselt zu "gedrückt"
            self.gamepad.press_button(button=XUSB_BUTTON.XUSB_GAMEPAD_A)
            self.gamepad.update()
            self.button_pressed = True
            self.update_callback(self.power, self.button_pressed)
        elif not is_active and self.button_pressed:
            # Zustand wechselt zu "losgelassen"
            self.gamepad.release_button(button=XUSB_BUTTON.XUSB_GAMEPAD_A)
            self.gamepad.update()
            self.button_pressed = False
            self.update_callback(self.power, self.button_pressed)

    async def start_notifications(self):
        """
        Subscribe to BLE power notifications.
        """
        try:
            await self.client.start_notify(CPM_UUID, self.handle_power_notify)
            print("Started power notifications.")
        except Exception as e:
            print(f"Failed to start notifications: {e}")

    async def run(self):
        """
        Full lifecycle: connect → subscribe → stay alive.
        """
        if not await self.connect():
            return
        await self.start_notifications()
        while True:
            await asyncio.sleep(1)

def setup_keyboard_mapping(gamepad: vgamepad.VX360Gamepad):
    """
    Bind the left/right arrow keys to D-Pad buttons on the virtual controller.
    """
    keyboard.on_press_key("left", lambda e: (
        gamepad.press_button(XUSB_BUTTON.XUSB_GAMEPAD_DPAD_LEFT),
        gamepad.update()
    ))
    keyboard.on_release_key("left", lambda e: (
        gamepad.release_button(XUSB_BUTTON.XUSB_GAMEPAD_DPAD_LEFT),
        gamepad.update()
    ))
    keyboard.on_press_key("right", lambda e: (
        gamepad.press_button(XUSB_BUTTON.XUSB_GAMEPAD_DPAD_RIGHT),
        gamepad.update()
    ))
    keyboard.on_release_key("right", lambda e: (
        gamepad.release_button(XUSB_BUTTON.XUSB_GAMEPAD_DPAD_RIGHT),
        gamepad.update()
    ))

def start_loop(loop: asyncio.AbstractEventLoop):
    """
    Dedicated thread event loop for BLE.
    """
    asyncio.set_event_loop(loop)
    loop.run_forever()

def main():
    """
    Entry point: parse CLI, start BLE, map keyboard.
    """
    parser = argparse.ArgumentParser(description="BLE to Gamepad bridge")
    parser.add_argument("--ftp", type=float, default=230.0,
                        help="Functional Threshold Power in watts")
    parser.add_argument("--device-name", type=str, default="KICKR BIKE 2CFA",
                        help="Partial name of BLE device (e.g. KICKR)")
    # GEÄNDERT: Ein sinnvollerer Standard-Schwellenwert, um "Leerlauf" zu vermeiden
    parser.add_argument("--threshold", type=float, default=50,
                        help="Ignore power below this wattage")
    parser.add_argument("--disable-dpad", action="store_true",
                        help="Disable arrow key mapping to D-Pad")
    args = parser.parse_args()

    # New thread & loop for BLE
    loop = asyncio.new_event_loop()
    threading.Thread(target=start_loop, args=(loop,), daemon=True).start()

    controller = KickrController(
        ftp=args.ftp,
        device_name=args.device_name,
        threshold=args.threshold,
        # GEÄNDERT: Die Ausgabe an die neue Logik (Taste gedrückt/losgelassen) anpassen
        update_callback=lambda p, pressed: print(f"{p} W → Key A {'pressed' if pressed else 'released'}")
    )

    # Keyboard mapping (optional) - DIESER TEIL BLEIBT UNVERÄNDERT
    if not args.disable_dpad:
        setup_keyboard_mapping(controller.gamepad)

    asyncio.run_coroutine_threadsafe(controller.run(), loop)

    # Keep script alive
    keyboard.wait()

if __name__ == "__main__":
    main()
