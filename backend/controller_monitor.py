"""Low-level controller button monitor for Steam Deck (hidraw) and external pads (evdev).

Provides raw button state (including rear grip buttons L4/L5/R4/R5) which Steam normally
consumes before regular browser window event listeners.
"""

import glob
import mmap
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

# DroidDeck RBIF (fakeinput-rings/ring0) shared-memory input constants
RBIF_MAGIC = b"RBIF"
RBIF_VERSION = 2
RBIF_HEADER_SIZE = 64
RBIF_IMU_OFFSET = 0x18040
RBIF_IMU_MAGIC = b"IMU1"
RBIF_MIN_TAIL_SIZE = 0x18070

# Bit positions in RBIF header +0x28 (uint32 LE)
RBIF_BASE_BUTTONS = (
    (0x001, "A"),
    (0x002, "B"),
    (0x004, "X"),
    (0x008, "Y"),
    (0x010, "L1"),
    (0x020, "R1"),
    (0x040, "SELECT"),
    (0x080, "START"),
    (0x100, "L3"),
    (0x200, "R3"),
    (0x400, "STEAM"),
    (0x800, "QAM"),
)

# Bit positions in RBIF IMU1 tail +0x28 (ring0 +0x18068, uint32 LE)
RBIF_EXTRA_BUTTONS = (
    (0x01, "L4"),
    (0x02, "R4"),
    (0x04, "L5"),
    (0x08, "R5"),
    (0x10, "LEFT_PAD_TOUCH"),
    (0x20, "RIGHT_PAD_TOUCH"),
)


class ControllerButtonMonitor:
    """Monitors controller inputs via /dev/hidraw for Steam Deck or DroidDeck RBIF rings."""

    def __init__(self, logger=None):
        self.logger = logger
        self.device_path: Optional[str] = None
        self.device_fd: Optional[int] = None
        self.ring_path: Optional[str] = None
        self.ring_fd: Optional[int] = None
        self.ring_mmap: Optional[mmap.mmap] = None
        self.ring_size: int = 0
        self.running: bool = False
        self.thread: Optional[threading.Thread] = None
        self.lock = threading.Lock()
        self.current_buttons: Set[str] = set()
        self.last_buttons_l: int = 0
        self.last_buttons_h: int = 0
        self.last_ring_buttons: Set[str] = set()

    def _log(self, level: str, msg: str):
        if self.logger:
            getattr(self.logger, level, self.logger.info)(f"[ControllerMonitor] {msg}")

    def find_hidraw_device(self) -> Optional[str]:
        """Find the Steam Deck gamepad hidraw device (:1.2 interface)."""
        indices = set(range(32))
        for p in glob.glob("/dev/hidraw*"):
            suffix = p.removeprefix("/dev/hidraw")
            if suffix.isdigit():
                indices.add(int(suffix))

        candidates = []
        for i in sorted(indices):
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

    def find_fakeinput_ring(self) -> Optional[str]:
        """Locate DroidDeck's shared-memory input ring when no hidraw device exists.

        DroidDeck only exposes its virtual /dev/hidraw16 device to Steam itself, so
        the plugin reads the underlying RBIF v2 ring buffer directly instead.
        """
        candidates = []
        memfd_paths = os.environ.get("FAKE_EVDEV_MEMFD_PATHS", "")
        for part in memfd_paths.split(";"):
            if "=" in part:
                slot, path = part.split("=", 1)
                if slot.strip() == "0" and path.strip():
                    candidates.append(path.strip())
        fake_evdev_dir = os.environ.get("FAKE_EVDEV_DIR", "")
        if fake_evdev_dir:
            candidates.append(
                os.path.join(os.path.dirname(os.path.normpath(fake_evdev_dir)), "fakeinput-rings", "ring0")
            )
        candidates.append("/data/user/0/com.droiddeck.launcher/files/session/dev/fakeinput-rings/ring0")

        seen = set()
        for path in candidates:
            if not path or path in seen:
                continue
            seen.add(path)
            if not os.path.isfile(path):
                continue
            try:
                if os.path.getsize(path) < RBIF_HEADER_SIZE:
                    continue
                with open(path, "rb") as f:
                    hdr = f.read(16)
                magic, ver, entry_size, _cap = struct.unpack("<4sIII", hdr)
                if magic == RBIF_MAGIC and ver == RBIF_VERSION and entry_size == 24:
                    return path
            except Exception:
                continue
        return None

    def _start_hidraw(self) -> bool:
        self.device_path = self.find_hidraw_device()
        if not self.device_path:
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

    def _start_fakeinput_ring(self) -> bool:
        self.ring_path = self.find_fakeinput_ring()
        if not self.ring_path:
            return False
        try:
            size = os.path.getsize(self.ring_path)
            fd = os.open(self.ring_path, os.O_RDONLY)
            mm = mmap.mmap(fd, size, access=mmap.ACCESS_READ)
            self.ring_fd = fd
            self.ring_mmap = mm
            self.ring_size = size
            self.running = True
            self.thread = threading.Thread(target=self._ring_read_loop, daemon=True)
            self.thread.start()
            self._log("info", f"Started monitoring DroidDeck RBIF ring at {self.ring_path}")
            return True
        except Exception as e:
            self._log("error", f"Failed to open DroidDeck ring {self.ring_path}: {e}")
            self._close_ring()
            return False

    def _close_ring(self):
        if self.ring_mmap is not None:
            try:
                self.ring_mmap.close()
            except Exception:
                pass
            self.ring_mmap = None
        if self.ring_fd is not None:
            try:
                os.close(self.ring_fd)
            except Exception:
                pass
            self.ring_fd = None
        self.ring_size = 0

    def start(self) -> bool:
        if self.running:
            return True

        if self._start_hidraw():
            return True
        if self._start_fakeinput_ring():
            return True

        self._log("warning", "No compatible Steam Deck hidraw or DroidDeck ring device found")
        return False

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

        self._close_ring()

        with self.lock:
            self.current_buttons.clear()
        self._log("info", "Stopped controller monitor")

    @staticmethod
    def _read_seqlock(mm: mmap.mmap, seq_offset: int, reader):
        # Odd sequence numbers mean the Android input bridge is mid-update; retry until consistent.
        for _ in range(4):
            seq1 = struct.unpack_from("<Q", mm, seq_offset)[0]
            if seq1 & 1:
                continue
            val = reader(mm)
            seq2 = struct.unpack_from("<Q", mm, seq_offset)[0]
            if seq1 == seq2:
                return val
        return None

    def _sample_ring_buttons(self) -> Optional[Set[str]]:
        mm = self.ring_mmap
        if mm is None or self.ring_size < RBIF_HEADER_SIZE:
            return None

        state = self._read_seqlock(
            mm, 0x20, lambda buf: struct.unpack_from("<I4x6h", buf, 0x28)
        )
        if state is None:
            return None
        buttons_mask, _ax0, _ax1, trig_r2, trig_l2, dpad_x, dpad_y = state

        active: Set[str] = set()
        for mask, name in RBIF_BASE_BUTTONS:
            if buttons_mask & mask:
                active.add(name)
        if trig_l2 > 0x7F:
            active.add("L2")
        if trig_r2 > 0x7F:
            active.add("R2")
        if dpad_x < 0:
            active.add("DPAD_LEFT")
        elif dpad_x > 0:
            active.add("DPAD_RIGHT")
        if dpad_y < 0:
            active.add("DPAD_UP")
        elif dpad_y > 0:
            active.add("DPAD_DOWN")

        if self.ring_size >= RBIF_MIN_TAIL_SIZE and mm[RBIF_IMU_OFFSET:RBIF_IMU_OFFSET + 4] == RBIF_IMU_MAGIC:
            extra_mask = self._read_seqlock(
                mm, RBIF_IMU_OFFSET + 8, lambda buf: struct.unpack_from("<I", buf, RBIF_IMU_OFFSET + 0x28)[0]
            )
            if extra_mask is not None:
                for mask, name in RBIF_EXTRA_BUTTONS:
                    if extra_mask & mask:
                        active.add(name)

        return active

    def _ring_read_loop(self):
        while self.running and self.ring_mmap is not None:
            try:
                active = self._sample_ring_buttons()
                if active is not None and active != self.last_ring_buttons:
                    self.last_ring_buttons = set(active)
                    with self.lock:
                        self.current_buttons = set(active)
                    if active:
                        self._log("info", f"Buttons active: {sorted(active)}")
                time.sleep(0.016)
            except Exception as e:
                self._log("warning", f"Ring read loop error: {e}")
                time.sleep(0.05)

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
