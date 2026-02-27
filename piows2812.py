# piows2812.py
# Thin Python wrapper around the Raspberry Pi utils 'ws2812' PIO example binary.
# Drives WS2812/NeoPixel on *any* GPIO using the Pi 5 RP1 PIO (via piolib).
#
# Runtime dependency (Pi 5 only):
#   sudo apt install -y cmake device-tree-compiler libfdt-dev
#   git clone https://github.com/raspberrypi/utils.git
#   cd utils && cmake . && make && sudo make install
# (Installs 'ws2812' example; see repo build notes.)  # <- ref: raspberrypi/utils
#
from __future__ import annotations
import os, shutil, subprocess
from typing import Sequence, Tuple, Optional, List

Color = Tuple[int, int, int]  # (R,G,B)

class PIOWS2812:
    def __init__(self, gpio: int, length: int, *, exe: Optional[str] = None,
                 grb: bool = True, brightness: float = 0.5):
        self.gpio = int(gpio)
        self.length = int(length)
        self.grb = bool(grb)
        self.brightness = max(0.0, min(1.0, float(brightness)))
        self.exe = exe or shutil.which("ws2812") or "/usr/local/bin/ws2812"
        if not os.path.exists(self.exe):
            raise FileNotFoundError(
                f"PIO ws2812 helper not found at {self.exe}. "
                "Build Raspberry Pi 'utils' (piolib) to install it."
            )
        self._proc = None
        self._start()

    def _build_argv(self) -> List[str]:
        # Primary guess: long options used by current example
        return [self.exe, "--gpio", str(self.gpio), "--length", str(self.length)]

    def _start(self):
        argv = self._build_argv()
        try:
            self._proc = subprocess.Popen(argv, stdin=subprocess.PIPE, close_fds=True)
        except Exception:
            # Fallback for positional-args variants
            argv = [self.exe, str(self.gpio), str(self.length)]
            self._proc = subprocess.Popen(argv, stdin=subprocess.PIPE, close_fds=True)

        if not self._proc or not self._proc.stdin:
            raise RuntimeError("Failed to launch ws2812 helper")

        # Clear once
        self.clear(); self.show()

    def set_brightness(self, b: float):
        self.brightness = max(0.0, min(1.0, float(b)))

    def _scale(self, c: Color) -> Color:
        if self.brightness >= 0.999: return c
        r, g, b = c; s = self.brightness
        return (int(r*s), int(g*s), int(b*s))

    def _to_bytes(self, pixels: Sequence[Color]) -> bytes:
        buf = bytearray()
        for r, g, b in pixels:
            r, g, b = self._scale((r, g, b))
            # WS2812 is GRB on the wire
            if self.grb:
                buf += bytes((g & 0xFF, r & 0xFF, b & 0xFF))
            else:
                buf += bytes((r & 0xFF, g & 0xFF, b & 0xFF))
        return bytes(buf)

    def clear(self): self._frame = [(0,0,0)] * self.length
    def fill(self, color: Color): self._frame = [tuple(map(int, color))] * self.length
    def set_pixel(self, i: int, color: Color):
        if 0 <= i < self.length: self._frame[i] = tuple(map(int, color))

    def show(self):
        if not self._proc or not self._proc.stdin: return
        try:
            self._proc.stdin.write(self._to_bytes(self._frame))
            self._proc.stdin.flush()
        except BrokenPipeError:
            raise RuntimeError("ws2812 helper exited. Is the GPIO valid and PIO available?")

    def close(self):
        try:
            if self._proc and self._proc.stdin: self._proc.stdin.close()
        finally:
            if self._proc:
                try: self._proc.terminate()
                except Exception: pass
            self._proc = None

    def __enter__(self): return self
    def __exit__(self, *exc): self.close()
