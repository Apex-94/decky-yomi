"""Low-level controller button monitor for Steam Deck (hidraw) and external pads (evdev).

Provides raw button state (including rear grip buttons L4/L5/R4/R5) which Steam normally
consumes before regular browser window event listeners.
"""

import os
import select
import struct
import threading
import time
from typing import Set, Optional

# Steam Deck Controller constants
VALVE_VID = 0x28DE
STEAMDECK_PID = 0x1205
INPUTPLUMBER_PID = 0x12FB
PACKET_SIZE = 64

# Button masks in ButtonsL (bytes 8-11, uint32 little-endian)
BUTTONS_L = {
    "R2": 0x00000001,
    "L2": 0x00000002,
    "R1": 0x00000004,
    "L1": 0x00000008,
    "Y": 0x00000010,
    "B": 0x00000020,
    "X": 0x00000040,
    "A": 0x00000080,
    "DPAD_UP": 0x00000100,
    "DPAD_RIGHT": 0x00000200,
    "DPAD_LEFT": 0x00000400,
    "DPAD_DOWN": 0x00000800,
    "SELECT": 0x00001000,
    "STEAM": 0x00002000,
    "START": 0x00004000,
    "L5": 0x00008000,
    "R5": 0x00010000,
    "LEFT_PAD_TOUCH": 0x00080000,
    "RIGHT_PAD_TOUCH": 0x00100000,
    "L3": 0x00400000,
    "R3": 0x04000000,
}

# Button masks in ButtonsH (bytes 12-15, uint32 little-endian)
BUTTONS_H = {
    "L4": 0x00000200,
    "R4": 0x00000400,
    "QAM": 0x00040000,
}


class ControllerButtonMonitor:
    """Monitors controller inputs via /dev/hidraw for Steam Deck."""

    def __init__(self, logger=None):
        self.logger = logger
        self.device_path: Optional[str] = None
        self.device_fd: Optional[int] = None
        self.running: bool = False
        self.thread: Optional[threading.Thread] = None
        self.lock = threading.Lock()
        self.current_buttons: Set[str] = set()
        self.last_buttons_l: int = 0
        self.last_buttons_h: int = 0

    def _log(self, level: str, msg: str):
        if self.logger:
            getattr(self.logger, level, self.logger.info)(f"[ControllerMonitor] {msg}")

    def find_hidraw_device(self) -> Optional[str]:
        """Find the Steam Deck gamepad hidraw device (:1.2 interface)."""
        candidates = []
        for i in range(16):
            path = f"/dev/hidraw{i}"
            if not os.path.exists(path):
                continue
            uevent_path = f"/sys/class/hidraw/hidraw{i}/device/uevent"
            try:
                if os.path.exists(uevent_path):
                    with open(uevent_path, "r") as f:
                        content = f.read().upper()
                    if "28DE" in content and ("1205" in content or "12FB" in content):
                        candidates.append((i, path))
            except Exception:
                pass

        # Check for interface :1.2
        for i, path in candidates:
            try:
                link = os.readlink(f"/sys/class/hidraw/hidraw{i}")
                if ":1.2/" in link:
                    self._log("info", f"Found Steam Deck gamepad interface at {path}")
                    return path
            except Exception:
                pass

        # Fallback to readable candidate
        for _, path in candidates:
            try:
                fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
                readable, _, _ = select.select([fd], [], [], 0.05)
                if readable:
                    os.read(fd, PACKET_SIZE)
                    os.close(fd)
                    self._log("info", f"Found active controller at {path}")
                    return path
                os.close(fd)
            except Exception:
                pass

        if candidates:
            return candidates[-1][1]
        return None

    def start(self) -> bool:
        if self.running:
            return True

        self.device_path = self.find_hidraw_device()
        if not self.device_path:
            self._log("warning", "No compatible Steam Deck hidraw device found")
            return False

        # Attempt O_RDWR so we can send CLEAR_DIGITAL_MAPPINGS; fall back to O_RDONLY
        try:
            self.device_fd = os.open(self.device_path, os.O_RDWR | os.O_NONBLOCK)
            self._log("info", f"Opened {self.device_path} with O_RDWR")
        except Exception:
            try:
                self.device_fd = os.open(self.device_path, os.O_RDONLY | os.O_NONBLOCK)
                self._log("info", f"Opened {self.device_path} with O_RDONLY")
            except Exception as e:
                self._log("error", f"Failed to open {self.device_path}: {e}")
                self.device_fd = None
                return False

        # Send CLEAR_DIGITAL_MAPPINGS (0x81) if device is writable
        try:
            import fcntl
            HIDIOCSFEATURE = lambda size: (0xC0000000 | (size << 16) | (ord('H') << 8) | 0x06)
            buf = bytes([0x81]) + bytes(63)
            fcntl.ioctl(self.device_fd, HIDIOCSFEATURE(64), buf)
            self._log("info", "Sent CLEAR_DIGITAL_MAPPINGS feature report to controller")
        except Exception as e:
            self._log("debug", f"Feature report skipped or failed: {e}")

        self.running = True
        self.thread = threading.Thread(target=self._read_loop, daemon=True)
        self.thread.start()
        self._log("info", f"Started monitoring {self.device_path}")
        return True

    def stop(self):
        self.running = False
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=1.0)
        self.thread = None

        if self.device_fd is not None:
            try:
                os.close(self.device_fd)
            except Exception:
                pass
            self.device_fd = None

        with self.lock:
            self.current_buttons.clear()
        self._log("info", "Stopped controller monitor")

    def _read_loop(self):
        while self.running and self.device_fd is not None:
            try:
                r, _, _ = select.select([self.device_fd], [], [], 0.1)
                if not r:
                    continue

                packet = os.read(self.device_fd, PACKET_SIZE)
                if len(packet) >= 16:
                    self._process_packet(packet)
            except (OSError, ValueError):
                time.sleep(0.1)
            except Exception as e:
                self._log("warning", f"Read loop error: {e}")
                time.sleep(0.1)

    def _process_packet(self, data: bytes):
        try:
            buttons_l = struct.unpack("<I", data[8:12])[0]
            buttons_h = struct.unpack("<I", data[12:16])[0]
        except Exception:
            return

        if buttons_l == self.last_buttons_l and buttons_h == self.last_buttons_h:
            return

        self.last_buttons_l = buttons_l
        self.last_buttons_h = buttons_h

        active = set()
        for name, mask in BUTTONS_L.items():
            if buttons_l & mask:
                active.add(name)

        for name, mask in BUTTONS_H.items():
            if buttons_h & mask:
                active.add(name)

        with self.lock:
            self.current_buttons = active

        if active:
            self._log("info", f"Buttons active: {sorted(active)}")

    def get_pressed_buttons(self) -> Set[str]:
        with self.lock:
            return set(self.current_buttons)
