#!/usr/bin/env python3
# led_display.py – FIXED VERSION with proper LED reset handling and initialization
# 6x seven-seg TOTP + OLED UI + smooth countdown + encoder debug + LED reset fix

import os
import time
import colorsys
from typing import Tuple, List

# ---------- Seven-segment mapping (your layout) ----------

SEGMENT_MAP = {
    '0': ['A', 'B', 'C', 'E', 'F', 'G'],
    '1': ['A', 'B'],
    '2': ['G', 'A', 'D', 'E', 'C'],
    '3': ['G', 'A', 'D', 'B', 'C'],
    '4': ['F', 'D', 'A', 'B'],
    '5': ['G', 'F', 'D', 'B', 'C'],
    '6': ['G', 'F', 'E', 'D', 'C', 'B'],
    '7': ['G', 'A', 'B'],
    '8': ['A', 'B', 'C', 'D', 'E', 'F', 'G'],
    '9': ['G', 'F', 'D', 'A', 'B', 'C'],
}

# Local (per-digit, 1-based) LED indices for each segment (21 LEDs per digit)
SEGMENT_TO_LEDS = {
    'A': [1, 2, 3],
    'B': [4, 5, 6],
    'C': [7, 8, 9],
    'D': [10, 11, 12],
    'E': [13, 14, 15],
    'F': [16, 17, 18],
    'G': [19, 20, 21],
}

NUM_DIGITS       = 6
LEDS_PER_DIGIT   = 21
TIMER_LED_COUNT  = 25
TOTAL_PIXELS     = NUM_DIGITS * LEDS_PER_DIGIT + TIMER_LED_COUNT  # 151

# ---------- Config / env ----------

LED_PIN_BCM      = int(os.environ.get("OTPI_LED_PIN", 18))
LED_COUNT_ENV    = int(os.environ.get("OTPI_LED_COUNT", TOTAL_PIXELS))
DEFAULT_BRIGHT   = float(os.environ.get("OTPI_LED_BRIGHT", 0.5))
PIXEL_ORDER_ENV  = os.environ.get("OTPI_PIXEL_ORDER", "GRB").upper()
MAX_LED_BRIGHT   = 0.80  # user 100% == 0.80 actual output

DEFAULT_SETTINGS = {"brightness": DEFAULT_BRIGHT, "hue": 0.33}

# Encoder debug toggle (prints backend, raw levels, hue/bright changes)
ENC_DBG          = os.environ.get("OTPI_DEBUG_ENCODER", "0").lower() not in ("0", "false", "no", "off")

# ---------- OLED UI (4 screens) ----------

from oled_ui import OledUI, ResetAction, perform_reset

# ---------- Hardware abstraction (Blinka NeoPixel) ----------

def _board_pin_from_bcm(bcm: int):
    import board
    # map common BCM pins to Blinka pins; default to D18
    m = {
        18: getattr(board, "D18", None),
        10: getattr(board, "D10", None),
        12: getattr(board, "D12", None),
        21: getattr(board, "D21", None),
    }
    return m.get(bcm) or getattr(board, "D18")

class _Strip:
    def __init__(self, count: int, brightness: float, pin_bcm: int):
        import neopixel
        # pixel order mapping (works whether provided by neopixel or pixelbuf)
        order_map = {}
        try:
            import adafruit_pixelbuf as pixelbuf
        except Exception:
            pixelbuf = None

        for name in ("GRB", "RGB", "BGR", "GBR", "RBG", "BRG"):
            val = getattr(neopixel, name, None)
            if val is None and pixelbuf is not None:
                val = getattr(pixelbuf, name, None)
            if val is not None:
                order_map[name] = val

        order = order_map.get(PIXEL_ORDER_ENV, order_map.get("GRB", next(iter(order_map.values()))))
        pin = _board_pin_from_bcm(pin_bcm)

        self.n = count
        self._np = neopixel.NeoPixel(
            pin, count, auto_write=False,
            pixel_order=order,
            brightness=max(0.0, min(1.0, float(brightness)))
        )
        try:
            current_order = getattr(self._np, "pixel_order", PIXEL_ORDER_ENV)
        except Exception:
            current_order = PIXEL_ORDER_ENV
        print(f"[LED] backend=neopixel pin={pin_bcm} count={count} order={current_order} bright={self._np.brightness:.2f}")

    def set_brightness(self, b: float):
        self._np.brightness = max(0.0, min(1.0, float(b)))

    def set(self, i: int, rgb):
        if 0 <= i < self.n:
            r, g, b = (int(max(0, min(255, c))) for c in rgb)
            self._np[i] = (r, g, b)

    def fill(self, rgb):
        r, g, b = (int(max(0, min(255, c))) for c in rgb)
        self._np.fill((r, g, b))

    def show(self):
        self._np.show()

    def deinit(self):
        try:
            self.fill((0, 0, 0)); self.show()
        except Exception:
            pass
        try:
            self._np.deinit()
        except Exception:
            pass

# ---------- Mapping helpers ----------

def _digit_base(digit_index: int) -> int:
    """0-based physical offset of the given digit (0..5)."""
    return digit_index * LEDS_PER_DIGIT

def _local_to_phys(digit_index: int, local_led_1based: int) -> int:
    """Convert per-digit local index (1..21) to global physical index (0-based)."""
    return _digit_base(digit_index) + (local_led_1based - 1)

def _segments_for_digit_char(ch: str) -> List[str]:
    return SEGMENT_MAP.get(ch, [])

def _leds_for_segments(digit_index: int, segments: List[str]) -> List[int]:
    """Return list of 0-based physical indices to light for given segments on a digit."""
    phys: List[int] = []
    for seg in segments:
        for local in SEGMENT_TO_LEDS.get(seg, []):
            phys.append(_local_to_phys(digit_index, local))
    return phys

def _timer_range() -> range:
    """0-based indices for the countdown strip (last 25 LEDs)."""
    start = NUM_DIGITS * LEDS_PER_DIGIT  # 126
    return range(start, start + TIMER_LED_COUNT)  # 126..150

# ---------- Color / draw helpers ----------

def _hsv2rgb(h, s, v):
    r, g, b = colorsys.hsv_to_rgb(h % 1.0, max(0, min(1, s)), max(0, min(1, v)))
    return int(r * 255), int(g * 255), int(b * 255)

def _scale(c: Tuple[int, int, int], f: float) -> Tuple[int, int, int]:
    return tuple(int(max(0, min(255, x * f))) for x in c)

def _lerp_color(c1: Tuple[int,int,int], c2: Tuple[int,int,int], t: float) -> Tuple[int,int,int]:
    """Linear blend from c1 to c2, t in [0,1]."""
    t = 0.0 if t < 0 else (1.0 if t > 1 else t)
    return (
        int(c1[0] + (c2[0] - c1[0]) * t),
        int(c1[1] + (c2[1] - c1[1]) * t),
        int(c1[2] + (c2[2] - c1[2]) * t),
    )

def _draw_digits(strip: _Strip, code6: str, color_on: Tuple[int, int, int], color_off: Tuple[int, int, int]):
    # Clear all digit LEDs first
    for i in range(NUM_DIGITS * LEDS_PER_DIGIT):
        strip.set(i, color_off)
    # Draw each digit
    for d in range(NUM_DIGITS):
        ch = code6[d] if d < len(code6) else ' '
        segs = _segments_for_digit_char(ch)
        for idx in _leds_for_segments(d, segs):
            strip.set(idx, color_on)

def _draw_timer(strip: _Strip, seconds_left: float,
                color: Tuple[int,int,int], period: float = 30.0):
    """
    Countdown bar: start FULL and turn off from highest index (151) to lowest (127).
    Smooth boundary by partially lighting the next LED; finished LEDs are OFF.
    """
    rng = list(_timer_range())            # [126,127,...,150] (0-based)
    total = len(rng)                      # 25
    sec = max(0.0, min(period, float(seconds_left)))

    # remaining fraction (1.0 → full; 0.0 → empty)
    rem = sec / period
    exact = rem * total                   # e.g., 17.4 => 17 full + 1 partial
    full  = int(exact)                    # fully ON from the low end (127 upward)
    frac  = exact - full                  # partial brightness for boundary LED

    for i, idx in enumerate(rng):
        if i < full:
            strip.set(idx, color)         # fully on
        elif i == full and full < total and frac > 0:
            strip.set(idx, _scale(color, frac))  # boundary dim
        else:
            strip.set(idx, (0, 0, 0))     # fully off

# ---------- CRITICAL FIX: Enhanced LED initialization for post-reset recovery ----------

def init_led_strip_post_reset(count, brightness, pin_bcm, max_retries=5):
    """
    Enhanced LED initialization that handles post-reset GPIO issues
    """
    import subprocess
    
    # Check if we're starting after a reset
    post_reset = os.environ.pop("OTPI_POST_RESET", None)
    if post_reset:
        print("[LED] Detected post-reset startup, forcing GPIO cleanup...")
        
        # Extra cleanup for post-reset state
        try:
            # Force kill anything using GPIO
            subprocess.run(["fuser", "-k", f"/dev/gpiochip0"], capture_output=True)
            time.sleep(0.5)
        except Exception:
            pass
    
    for attempt in range(max_retries):
        try:
            if attempt > 0:
                print(f"[LED] Retry attempt {attempt + 1}/{max_retries}")
                
                # Progressive cleanup between attempts
                if attempt == 1:
                    # Try to release the specific GPIO pin
                    try:
                        gpio_path = f"/sys/class/gpio/gpio{pin_bcm}"
                        if os.path.exists(f"{gpio_path}/direction"):
                            with open(f"{gpio_path}/direction", "w") as f:
                                f.write("in")
                        if os.path.exists("/sys/class/gpio/unexport"):
                            with open("/sys/class/gpio/unexport", "w") as f:
                                f.write(str(pin_bcm))
                        print(f"[LED] Unexported GPIO {pin_bcm}")
                    except Exception:
                        pass
                
                elif attempt >= 2:
                    # Try alternative approaches
                    print(f"[LED] Trying alternative approach on attempt {attempt + 1}")
                    # Small delay to let system settle
                    time.sleep(0.5 * attempt)
            
            # Try to initialize
            strip = _Strip(count, brightness, pin_bcm)
            print(f"[LED] Successfully initialized on attempt {attempt + 1}")
            return strip
            
        except Exception as e:
            print(f"[LED] Attempt {attempt + 1} failed: {e}")
            if attempt == max_retries - 1:
                print(f"[LED] All {max_retries} attempts failed, continuing without LEDs")
                return None
            
    return None

# ---------- Public entry ----------

def run_totp_display(secret: str, settings: dict, oled, encoder, wifi_watchdog=None):
    """
    FIXED VERSION: Better initialization order and LED reset handling
    """
    try:
        import pyotp
    except ModuleNotFoundError:
        raise SystemExit("pyotp is required for TOTP: pip install pyotp")

    import time as _time
    import traceback

    period = 30.0  # TOTP window (seconds)

    # Force LED count to match mapping
    if LED_COUNT_ENV != TOTAL_PIXELS:
        print(f"[DEBUG] Overriding OTPI_LED_COUNT={LED_COUNT_ENV} -> {TOTAL_PIXELS} (based on mapping)")
    count = TOTAL_PIXELS

    # Settings with safer defaults
    hue = float(settings.get("hue", 0.33))
    init_bright = float(settings.get("brightness", DEFAULT_BRIGHT))
    init_bright = max(0.0, min(1.0, init_bright))

    # UI initial user brightness % (0..100 view) respecting cap
    user_pct = int(round(min(1.0, (init_bright / MAX_LED_BRIGHT) if MAX_LED_BRIGHT > 1e-6 else 0.0) * 100.0))

    # Initialize TOTP first (fast operation)
    totp = pyotp.TOTP(secret)
    
    # CRITICAL FIX: Initialize UI BEFORE attempting LED init!
    ui = OledUI(oled, hue, user_pct)
    print("[DEBUG] UI initialized successfully")
    
    # Show initial info screen immediately - prevents restart issues
    if oled:
        try:
            initial_code = totp.now()
            ui._draw(initial_code, int(30.0 - (_time.time() % 30.0)))
        except Exception as e:
            print(f"[DEBUG] Initial OLED draw failed: {e}")

    # ENHANCED LED init function with post-reset recovery
    def _try_init_strip(first: bool = False):
        try:
            if first:
                # Use enhanced initialization for first attempt
                s = init_led_strip_post_reset(count, init_bright, LED_PIN_BCM)
            else:
                # Standard initialization for retries
                s = _Strip(count, init_bright, LED_PIN_BCM)
            
            if s:
                who = "startup" if first else "reinit"
                print(f"[LED] {who}: pin={LED_PIN_BCM}, count={count}, bright={init_bright:.2f}")
                # Visual confirmation only after successful init
                try:
                    s.fill((16, 16, 16)); s.show(); _time.sleep(0.15)
                    s.fill((0, 0, 0));    s.show()
                except Exception as e:
                    print(f"[LED] blink failed: {e}")
            return s
        except Exception as e:
            print(f"[LED] init error: {e}")
            return None

    # Try LED init (but don't block if it fails)
    strip = _try_init_strip(first=True)
    last_code = None
    last_reinit_attempt = 0.0
    
    print("[DEBUG] Starting main display loop...")

    try:
        loop_count = 0
        while True:
            loop_count += 1
            
            # Time & code calculation
            now = _time.time()
            secs_left = period - (now % period)
            if secs_left < 0: 
                secs_left = 0.0
                
            code = totp.now()
            if code != last_code:
                print(f"[DEBUG] New TOTP code: {code} (loop {loop_count})")
                last_code = code

            # UI handling - this should ALWAYS work even if LEDs fail
            try:
                hue, user_pct, action = ui.handle(encoder, code, int(secs_left))
            except Exception as e:
                print(f"[DEBUG] UI handle error: {e}")
                # Create safe defaults if UI fails
                hue, user_pct, action = 0.33, 50, ResetAction.NONE

            # Pipe WiFi status from watchdog to OLED UI
            if wifi_watchdog is not None:
                try:
                    ui.set_wifi_status(wifi_watchdog.connected, wifi_watchdog.ssid)
                except Exception:
                    pass
                
            # CRITICAL FIX: Execute reset with proper LED cleanup
            if action in (ResetAction.WIFI, ResetAction.QR, ResetAction.BOTH):
                print(f"[DEBUG] Executing reset action: {action}")
                # Pass the LED strip to reset function for proper cleanup
                perform_reset(action, oled, strip)
                return  # This should never be reached due to execv

            # LED handling (optional - can fail without breaking UI)
            if strip is None and (now - last_reinit_attempt) > 2.0:
                print("[LED] attempting reinit…")
                strip = _try_init_strip(first=False)
                last_reinit_attempt = now

            if strip is not None:
                try:
                    # Apply settings
                    base_color = _hsv2rgb(hue, 1.0, 1.0)
                    actual_bright = max(0.0, min(MAX_LED_BRIGHT, (user_pct / 100.0) * MAX_LED_BRIGHT))
                    
                    strip.set_brightness(actual_bright)

                    # Draw digits
                    _draw_digits(strip, code[-NUM_DIGITS:], base_color, (0, 0, 0))

                    # Draw countdown timer
                    rem_frac = max(0.0, min(1.0, secs_left / period))
                    timer_color = _lerp_color((0, 255, 0), (255, 0, 0), 1.0 - rem_frac)
                    _draw_timer(strip, secs_left, timer_color, period)

                    strip.show()

                except Exception as e:
                    print(f"[LED] runtime error: {e}")
                    try:
                        strip.deinit()
                    except Exception:
                        pass
                    strip = None
                    last_reinit_attempt = now

            # Sleep for responsive UI - this was critical for stability
            _time.sleep(0.05)  # 50ms = 20 FPS, good balance of responsiveness and CPU usage

    except KeyboardInterrupt:
        print("\n[DEBUG] Keyboard interrupt received")
    except Exception as e:
        print(f"[DEBUG] Main loop error: {e}")
        traceback.print_exc()
    finally:
        print("[DEBUG] Cleaning up...")
        if strip is not None:
            try:
                strip.deinit()
            except Exception as e:
                print(f"[DEBUG] Strip cleanup error: {e}")
        print("[DEBUG] Cleanup complete")
