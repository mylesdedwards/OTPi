#!/usr/bin/env python3
# encoder.py – FIXED VERSION with enhanced resource management and GPIO cleanup
# rotary encoder with threaded high-rate sampling
# Backends: lgpio → periphery (cdev) → periphery (sysfs) → dummy

import os, time, threading
from typing import Optional

def _envint(name: str, default: int) -> int:
    try: return int(float(os.environ.get(name, str(default))))
    except Exception: return default

CLK_PIN = _envint("OTPI_ENC_CLK", 23)
DT_PIN  = _envint("OTPI_ENC_DT", 24)
SW_PIN  = _envint("OTPI_ENC_SW", 25)
BTN_ACTIVE_LOW = os.environ.get("OTPI_ENC_BTN_ACTIVE_LOW", "1").lower() not in ("0","false","no","off")
PPR = max(1, _envint("OTPI_ENC_PPR", 4))  # transitions per detent (2 or 4)
POLL_S = max(0.0003, float(os.environ.get("OTPI_ENC_POLL_MS", "1"))/1000.0)  # seconds
BTN_DEBOUNCE_S = max(0.001, float(os.environ.get("OTPI_ENC_BTN_DEBOUNCE_MS", "5"))/1000.0)

# ---------- FIXED: Enhanced Backends with proper resource management ----------
class _PinBase:
    def read(self) -> int: return 1
    def close(self): pass

class _LgpioPin(_PinBase):
    _chip = None
    _claimed_pins = set()  # FIXED: Track claimed pins to avoid conflicts
    _lock = threading.Lock()  # Thread safety for shared resources
    
    def __init__(self, line: int):
        import lgpio
        self._lg = lgpio
        self._line = int(line)
        
        with _LgpioPin._lock:
            # Check if pin already claimed
            if self._line in _LgpioPin._claimed_pins:
                raise RuntimeError(f"GPIO {self._line} already in use")
                
            # Initialize chip only once
            if _LgpioPin._chip is None:
                try:
                    _LgpioPin._chip = self._lg.gpiochip_open(0)
                except Exception as e:
                    raise RuntimeError(f"Failed to open GPIO chip: {e}")
            
            try:
                # FIXED: Configure input with pullup resistor (critical for encoder stability)
                self._lg.gpio_claim_input(_LgpioPin._chip, self._line, self._lg.SET_PULL_UP)
                _LgpioPin._claimed_pins.add(self._line)
                print(f"[DEBUG] Claimed GPIO {self._line} with pullup")
            except Exception as e:
                raise RuntimeError(f"Failed to claim GPIO {self._line}: {e}")
    
    def read(self) -> int:
        try:
            return 1 if self._lg.gpio_read(_LgpioPin._chip, self._line) else 0
        except Exception:
            return 1  # Fail safe to high (with pullups, this is the default state)
    
    def close(self):
        with _LgpioPin._lock:
            if hasattr(self, '_line') and self._line in _LgpioPin._claimed_pins:
                try:
                    self._lg.gpio_free(_LgpioPin._chip, self._line)
                    _LgpioPin._claimed_pins.remove(self._line)
                    print(f"[DEBUG] Released GPIO {self._line}")
                except Exception as e:
                    print(f"[DEBUG] Failed to release GPIO {self._line}: {e}")
    
    @classmethod
    def cleanup_all(cls):
        """FIXED: Clean up all claimed pins"""
        with cls._lock:
            if cls._chip is not None:
                for pin in list(cls._claimed_pins):
                    try:
                        cls._lg.gpio_free(cls._chip, pin)
                        print(f"[DEBUG] Force-released GPIO {pin}")
                    except Exception:
                        pass
                cls._claimed_pins.clear()
                try:
                    cls._lg.gpiochip_close(cls._chip)
                    print("[DEBUG] Closed GPIO chip")
                except Exception:
                    pass
                cls._chip = None

class _PeriphCdevPin(_PinBase):
    def __init__(self, line: int, chip: str="/dev/gpiochip0"):
        from periphery import GPIO as _GPIO
        self._g = _GPIO(chip, int(line), "in")
    def read(self) -> int: return 1 if self._g.read() else 0
    def close(self): 
        try: self._g.close()
        except Exception: pass

class _PeriphSysfsPin(_PinBase):
    def __init__(self, line: int):
        from periphery import GPIO as _GPIO
        self._g = _GPIO(int(line), "in")
    def read(self) -> int: return 1 if self._g.read() else 0
    def close(self):
        try: self._g.close()
        except Exception: pass

class _DummyPin(_PinBase): pass

def _make_pin(line: int) -> _PinBase:
    force = os.environ.get("OTPI_GPIO_BACKEND", "").lower().strip()
    order = (["lgpio"] if force=="lgpio" else
             ["periph_cdev"] if force in ("periphery_cdev","periphery-cdev","cdev") else
             ["periph_sysfs"] if force in ("periphery_sysfs","periphery-sysfs","sysfs") else
             ["lgpio","periph_cdev","periph_sysfs"])
    last = None
    for b in order:
        try:
            if b=="lgpio": return _LgpioPin(line)
            if b=="periph_cdev": return _PeriphCdevPin(line)
            if b=="periph_sysfs": return _PeriphSysfsPin(line)
        except Exception as e:
            last = e
    if last: print(f"[DEBUG] Encoder backends failed ({last}); using dummy")
    return _DummyPin()

# ---------- FIXED: Enhanced Encoder with proper cleanup ----------
class Encoder:
    """
    FIXED VERSION: Enhanced resource management and GPIO cleanup
    High-rate sampled quadrature with button latch.
    steps() returns signed detents since last call.
    pressed() returns True once per physical press (debounced) since last call.
    """
    def __init__(self, clk_pin: int = CLK_PIN, dt_pin: int = DT_PIN, btn_pin: Optional[int] = SW_PIN):
        self._ok = False
        self._clk = self._dt = self._sw = None
        self._stop = False
        self._t = None
        
        try:
            # FIXED: Clean up any existing pins first to avoid conflicts
            _LgpioPin.cleanup_all()
            
            self._clk = _make_pin(clk_pin)
            self._dt = _make_pin(dt_pin)
            self._sw = _make_pin(btn_pin) if btn_pin is not None else None
            
            # Verify initial readings are sensible
            a = self._clk.read()
            b = self._dt.read()
            sw = self._sw.read() if self._sw else (1 if BTN_ACTIVE_LOW else 0)
            
            print(f"[DEBUG] Encoder initial levels: CLK={a}, DT={b}, SW={sw}")
            
            # Sanity check - with pullups, we should see mostly 1s at rest
            if a == b == sw == 0:
                print("[DEBUG] Warning: All encoder pins read LOW - check wiring and pullups")
            
            # Initialize state
            self._state = (a << 1) | b
            self._accum = 0       # transitions accumulator
            self._steps = 0       # detents accumulator (thread-safe via lock)
            self._press_latch = False
            self._btn_last = sw
            self._btn_last_change = time.perf_counter()
            self._lock = threading.Lock()
            
            # Transition table for quadrature decoding
            self._tbl = [0,-1,+1,0,  +1,0,0,-1,  -1,0,0,+1,  0,+1,-1,0]
            
            # Start background thread
            self._t = threading.Thread(target=self._poll_loop, daemon=True)
            self._t.start()
            
            self._ok = True
            print(f"[DEBUG] Encoder initialized successfully")
            
        except Exception as e:
            print(f"[DEBUG] Encoder init failed ({e}); continuing without encoder")
            self._cleanup()
            self._ok = False

    # Background sampler with enhanced error handling
    def _poll_loop(self):
        next_t = time.perf_counter()
        consecutive_errors = 0
        max_errors = 10  # Stop after too many consecutive errors
        
        while not self._stop:
            try:
                # quadrature reading
                a = self._clk.read()
                b = self._dt.read()
                s = (a<<1)|b
                idx = ((self._state<<2)|s) & 0xF
                self._state = s
                delta = self._tbl[idx]
                
                if delta:
                    self._accum += delta
                    # convert transitions -> detents
                    det = int(self._accum / PPR)
                    if det:
                        with self._lock:
                            self._steps += det
                        self._accum -= det * PPR
                
                # Reset error counter on successful read
                consecutive_errors = 0
                        
            except Exception as e:
                consecutive_errors += 1
                if consecutive_errors > max_errors:
                    print(f"[DEBUG] Encoder thread stopping due to {consecutive_errors} consecutive errors")
                    break

            # button latch (debounced) with error handling
            if self._sw:
                try:
                    lvl = self._sw.read()
                    now = time.perf_counter()
                    if lvl != self._btn_last:
                        self._btn_last = lvl
                        self._btn_last_change = now
                    else:
                        active = (lvl==0) if BTN_ACTIVE_LOW else (lvl==1)
                        if active and (now - self._btn_last_change) >= BTN_DEBOUNCE_S:
                            # latch once per press; clear on release
                            self._press_latch = True
                            # wait for release before latching again
                            while not self._stop:
                                lvl2 = self._sw.read()
                                if ((lvl2==1) if BTN_ACTIVE_LOW else (lvl2==0)):
                                    self._btn_last = lvl2
                                    self._btn_last_change = time.perf_counter()
                                    break
                                time.sleep(max(POLL_S, 0.001))
                except Exception:
                    consecutive_errors += 1
                    if consecutive_errors > max_errors:
                        break

            # sleep to target poll interval
            next_t += POLL_S
            delay = next_t - time.perf_counter()
            if delay > 0:
                time.sleep(delay)
            else:
                next_t = time.perf_counter()  # missed; resync

    # API
    def steps(self) -> int:
        if not self._ok: return 0
        with self._lock:
            s = self._steps
            self._steps = 0
        return s

    def pressed(self) -> bool:
        if not self._ok or self._sw is None: return False
        if self._press_latch:
            self._press_latch = False
            return True
        return False

    def _cleanup(self):
        """FIXED: Enhanced cleanup with proper resource management"""
        self._stop = True
        
        # Wait for thread to finish
        if self._t and self._t.is_alive():
            self._t.join(timeout=1.0)
            if self._t.is_alive():
                print("[DEBUG] Warning: Encoder thread did not stop cleanly")
        
        # Clean up GPIO pins
        for pin in (self._clk, self._dt, self._sw):
            if pin:
                try:
                    pin.close()
                except Exception as e:
                    print(f"[DEBUG] Pin cleanup error: {e}")
        
        # Clean up lgpio resources
        _LgpioPin.cleanup_all()
        
        print("[DEBUG] Encoder cleanup completed")

    def close(self):
        self._cleanup()

    # --- DEBUG HELPERS ---
    def backend_name(self) -> str:
        try:
            if isinstance(self._clk, _LgpioPin):      return "lgpio"
            if isinstance(self._clk, _PeriphCdevPin): return "periph_cdev"
            if isinstance(self._clk, _PeriphSysfsPin):return "periph_sysfs"
            if isinstance(self._clk, _DummyPin):      return "dummy"
        except Exception:
            pass
        return "unknown"

    def raw_levels(self):
        """Get current pin levels for debugging"""
        try:
            a = self._clk.read() if self._clk else None
            b = self._dt.read()  if self._dt  else None
            s = self._sw.read()  if self._sw  else None
            return a, b, s
        except Exception:
            return None
