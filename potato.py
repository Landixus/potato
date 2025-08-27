#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
POTATO POC

Reads cycling power from a BLE home trainer (e.g. Wahoo KICKR),
maps it linearly based on FTP, and feeds it into the right trigger of
a virtual Xbox 360 controller. Optionally binds Left/Right arrow keys
to the D‐pad (for compatibility with Zwift Play).
"""

# Standard lib
import asyncio
import threading
import math
import argparse
import configparser
import os
import sys  # Wichtig für die .exe-Pfad-Ermittlung

# Third-party
import keyboard
import vgamepad
from vgamepad import XUSB_BUTTON
from bleak import BleakClient, BleakScanner

# UUID for the BLE Cycling Power Measurement characteristic
CPM_UUID = "00002a63-0000-1000-8000-00805f9b34fb"

def parse_cycling_power(data: bytearray) -> int:
    """
    Extract instantaneous power (in watts) from the raw Cycling Power Measurement packet.
    Format (little‐endian, signed) at bytes 2–3.
    """
    if len(data) < 4:
        return 0
    return int.from_bytes(data[2:4], byteorder='little', signed=True)

class KickrController:
    """
    Manages BLE connection to the KICKR, receives power notifications,
    applies a linear mapping, and updates a virtual gamepad.
    """

    def __init__(self, ftp, device_names, threshold, update_callback):
        self.ftp = ftp
        self.device_names = [name.upper() for name in device_names]
        self.threshold = threshold
        self.client = None
        self.power = 0
        self.trigger = 0.0
        self.update_callback = update_callback
        self.gamepad = vgamepad.VX360Gamepad()

    async def connect(self) -> bool:
        print("Scanning for BLE devices...")
        try:
            devices = await asyncio.wait_for(BleakScanner.discover(), timeout=10)
        except asyncio.TimeoutError:
            print("Scan timed out.")
            return False

        device = next(
            (d for d in devices if d.name and any(name in d.name.upper() for name in self.device_names)),
            None)

        if not device:
            print(f"No configured devices found. Searched for: {self.device_names}")
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
        self.power = parse_cycling_power(data)

        if self.power < self.threshold:
            self.trigger = 0.0
        else:
            trigger_ratio = self.power / self.ftp
            self.trigger = min(trigger_ratio, 1.0)

        trigger_value = int(self.trigger * 255)
        self.gamepad.right_trigger(trigger_value)
        self.gamepad.update()
        self.update_callback(self.power, self.trigger)

    async def start_notifications(self):
        try:
            await self.client.start_notify(CPM_UUID, self.handle_power_notify)
            print("Started power notifications.")
        except Exception as e:
            print(f"Failed to start notifications: {e}")

    async def run(self):
        if not await self.connect():
            return
        await self.start_notifications()
        while True:
            await asyncio.sleep(1)

def setup_keyboard_mapping(gamepad: vgamepad.VX360Gamepad):
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
    asyncio.set_event_loop(loop)
    loop.run_forever()

# --- KORRIGIERTE FUNKTION ---
def load_config():
    """
    Loads settings from config.ini.
    Correctly finds the path whether running as a script or a PyInstaller .exe.
    """
    if getattr(sys, 'frozen', False):
        # Wenn als .exe ausgeführt (via PyInstaller)
        application_path = os.path.dirname(sys.executable)
    else:
        # Wenn als normales .py-Skript ausgeführt
        application_path = os.path.dirname(os.path.abspath(__file__))

    config_path = os.path.join(application_path, 'config.ini')
    config = configparser.ConfigParser()

    if not os.path.exists(config_path):
        print("config.ini not found, creating a default one.")
        config['devices'] = {'names': 'KICKR, WAHOO'}
        config['settings'] = {'ftp': '250', 'threshold': '10'}
        with open(config_path, 'w') as configfile:
            config.write(configfile)
    
    config.read(config_path)
    
    device_names_str = config.get('devices', 'names', fallback='KICKR')
    device_names = [name.strip() for name in device_names_str.split(',')]
    
    ftp = config.getfloat('settings', 'ftp', fallback=250.0)
    threshold = config.getfloat('settings', 'threshold', fallback=10.0)
    
    return device_names, ftp, threshold

def main():
    device_names, ftp, threshold = load_config()
    print(f"Configuration loaded: Searching for {device_names}, FTP set to {ftp}W, Threshold {threshold}W")

    parser = argparse.ArgumentParser(description="BLE to Gamepad bridge")
    parser.add_argument("--disable-dpad", action="store_true",
                        help="Disable arrow key mapping to D-Pad")
    args = parser.parse_args()

    loop = asyncio.new_event_loop()
    threading.Thread(target=start_loop, args=(loop,), daemon=True).start()

    controller = KickrController(
        ftp=ftp,
        device_names=device_names,
        threshold=threshold,
        update_callback=lambda p, t: print(f"{p} W → Trigger: {t:.2f}", end='\r')
    )

    if not args.disable_dpad:
        setup_keyboard_mapping(controller.gamepad)

    asyncio.run_coroutine_threadsafe(controller.run(), loop)

    print("Setup complete. Press Ctrl+C to exit.")
    try:
        keyboard.wait()
    except KeyboardInterrupt:
        print("\nExiting.")

if __name__ == "__main__":
    main()
