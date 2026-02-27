#!/usr/bin/env python3
# main.py – boot flow for Wi-Fi + OTP secret + TOTP/LED UI
# FIXED VERSION: Proper resource management and restart handling

from __future__ import annotations
import os, sys, time, inspect, subprocess, threading
from pathlib import Path
from typing import Tuple, Optional
import time

PROJECT_DIR   = Path(__file__).resolve().parent
SECRETS_DIR   = PROJECT_DIR / "secrets"
WIFI_CONFIG   = PROJECT_DIR / "wifi_config.txt"
SECRET_FILE   = SECRETS_DIR / "otp_secret.txt"
SECRET_QR_PNG = SECRETS_DIR / "otp_qr.png"

# ---------------- utils (from your project) ----------------
try:
    from utils import debug_print, connect_wifi, get_ntp_time, restart_program, get_wifi_status, reconnect_wifi
except Exception:
    # safe fallbacks if utils import fails
    def debug_print(msg: str): print(f"[DEBUG] {msg}")
    def connect_wifi(ssid: str, pwd: str) -> bool: return False
    def get_ntp_time(): pass
    def restart_program():
        py = sys.executable
        os.execv(py, [py, str(PROJECT_DIR / "main.py")])
    def get_wifi_status(iface="wlan0"): return {"connected": False, "ssid": "", "ip": ""}
    def reconnect_wifi(iface="wlan0"): return False

# --- FIXED: Simplified captive portal import ---
try:
    from wifi_web import run_captive_portal
    debug_print("Successfully imported run_captive_portal")
except ImportError as e:
    debug_print(f"Failed to import wifi_web: {e}")
    # Create a dummy function to prevent crashes
    def run_captive_portal(need_wifi: bool = True, need_qr: bool = True):
        debug_print("Using dummy captive portal - wifi_web.py not available")
        time.sleep(2)

# --- i18n support ---
try:
    import lang
    from lang import t
except Exception:
    def t(key: str) -> str: return key

# --- FIXED: Enhanced OLED Manager with proper cleanup ---
class OLEDManager:
    """Manages OLED device lifecycle with proper cleanup"""

    def __init__(self):
        self.device = None
        self.serial_interface = None
        self._initialized = False

    def initialize(self):
        """Initialize OLED with proper error handling"""
        if self._initialized:
            return self.device

        try:
            from luma.core.interface.serial import i2c
            from luma.oled.device import ssd1306, sh1106

            # Try common I2C addresses
            addresses = []
            env_addr = os.environ.get("OTPI_OLED_ADDR")
            if env_addr:
                try:
                    addresses = [int(env_addr, 0)]
                except Exception:
                    pass

            if not addresses:
                addresses = [0x3C, 0x3D]

            for addr in addresses:
                try:
                    debug_print(f"Trying OLED at address 0x{addr:02X}")

                    # Create serial interface
                    self.serial_interface = i2c(port=1, address=addr)

                    # Try SSD1306 first, then SH1106
                    try:
                        self.device = ssd1306(self.serial_interface)
                        device_type = "SSD1306"
                    except Exception:
                        self.device = sh1106(self.serial_interface)
                        device_type = "SH1106"

                    # Test the device works
                    from luma.core.render import canvas
                    with canvas(self.device) as draw:
                        draw.text((0, 0), "OLED Test", fill=1)

                    debug_print(f"OLED init OK at 0x{addr:02X} ({device_type})")
                    self._initialized = True
                    return self.device

                except Exception as e:
                    debug_print(f"OLED address 0x{addr:02X} failed: {e}")
                    # Clean up failed attempt
                    if self.device:
                        try:
                            self.device.cleanup()
                        except:
                            pass
                        self.device = None

                    if self.serial_interface:
                        try:
                            self.serial_interface.cleanup()
                        except:
                            pass
                        self.serial_interface = None

                    continue

            debug_print("No OLED device found")
            return None

        except Exception as e:
            debug_print(f"OLED initialization failed: {e}")
            self.cleanup()
            return None

    def cleanup(self):
        """Properly clean up OLED resources"""
        if self.device:
            try:
                # Clear display before cleanup
                from luma.core.render import canvas
                with canvas(self.device) as draw:
                    pass  # Empty canvas = clear screen
            except:
                pass

            try:
                self.device.cleanup()
            except:
                pass
            self.device = None

        if self.serial_interface:
            try:
                self.serial_interface.cleanup()
            except:
                pass
            self.serial_interface = None

        self._initialized = False
        debug_print("OLED interface cleaned")

    def clear(self):
        """Clear the OLED display"""
        if self.device:
            try:
                from luma.core.render import canvas
                with canvas(self.device) as draw:
                    pass  # Empty canvas = clear screen
            except Exception as e:
                debug_print(f"OLED clear failed: {e}")

# --- FIXED: Enhanced Progress Tracking Integration ---
class ProgressiveSetupManager:
    """Manages the progressive setup instructions shown on OLED"""

    def __init__(self, oled):
        self.oled = oled
        self.current_step = "welcome"
        self.running = False
        self.thread = None
        self.lock = threading.Lock()

        # Completion flags
        self.webpage_accessed = False
        self.form_submitted = False

        # Step definitions — advancement is event-driven, not timer-based
        # (welcome and waiting use brief fixed delays; all others wait for events)
        self.step_order = ["welcome", "connect_wifi", "open_browser", "fill_form", "waiting"]

    def _get_messages(self, step_name: str) -> list:
        """Build step messages dynamically so they use the current language."""
        ap_ssid, ap_password = self.get_ap_info()
        msg_map = {
            "welcome":      [t("setup_title"), t("setup_starting"), t("please_wait")],
            "connect_wifi": [t("setup_step1"), t("setup_connect"), f"Wi-Fi: {ap_ssid}", f"Pass: {ap_password}"],
            "open_browser": [t("setup_step2"), t("setup_browser"), t("setup_navigate"), "192.168.4.1"],
            "fill_form":    [t("setup_step3"), t("setup_form"), t("setup_on_web"), "192.168.4.1"],
            "waiting":      [t("setup_complete"), t("setup_saved"), t("setup_restarting"), t("please_wait")],
        }
        return msg_map.get(step_name, ["Unknown step"])

    def get_ap_info(self) -> tuple:
        """Get the actual AP name and password.
        Reads the runtime .ap_ssid file first (unique per board),
        falls back to parsing hostapd.conf."""
        ssid = None
        password = None

        # 1) Read unique SSID from runtime file (written by start_ap_mode)
        try:
            ap_ssid_file = PROJECT_DIR / ".ap_ssid"
            if ap_ssid_file.exists():
                ssid = ap_ssid_file.read_text(encoding="utf-8").strip()
                if ssid:
                    debug_print(f"AP SSID from .ap_ssid: {ssid}")
        except Exception:
            pass

        # 2) Parse hostapd.conf for password (and SSID fallback)
        try:
            hostapd_paths = [
                "/etc/hostapd/hostapd.conf",
                "/etc/hostapd.conf",
                PROJECT_DIR / "hostapd.conf"
            ]

            for path in hostapd_paths:
                try:
                    content = Path(str(path)).read_text()
                    for line in content.splitlines():
                        line = line.strip()
                        if line.startswith("ssid=") and not ssid:
                            ssid = line.split("=", 1)[1].strip()
                        elif line.startswith("wpa_passphrase="):
                            password = line.split("=", 1)[1].strip()
                    if ssid:
                        break
                except Exception:
                    continue

        except Exception as e:
            debug_print(f"Could not read AP info from config: {e}")

        return ssid or "OTPi-Setup", password or "setup1234"

    def start(self):
        """Start the progressive instruction display"""
        with self.lock:
            if self.running:
                return
            self.running = True

        self.thread = threading.Thread(target=self._run_instructions, daemon=True)
        self.thread.start()
        debug_print("Progressive setup manager started")

    def stop(self):
        """Stop the instruction display"""
        with self.lock:
            self.running = False

        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=2.0)

        debug_print("Stopping instruction manager")

    def mark_webpage_accessed(self):
        """Called when someone accesses the web portal"""
        debug_print("mark_webpage_accessed() called")
        with self.lock:
            self.webpage_accessed = True

    def mark_form_submitted(self):
        """Called when form is successfully submitted"""
        debug_print("mark_form_submitted() called")
        with self.lock:
            self.form_submitted = True

    def _advance_step(self):
        """Advance to the next step"""
        try:
            current_idx = self.step_order.index(self.current_step)
            if current_idx < len(self.step_order) - 1:
                self.current_step = self.step_order[current_idx + 1]
                debug_print(f"Step '{self.step_order[current_idx]}' completed, advancing to '{self.current_step}'")
                return True
        except ValueError:
            pass
        return False

    def _wait_for_device_connection(self):
        """Wait indefinitely for a device to connect to our AP."""
        debug_print("Waiting for device to connect to AP...")

        while True:
            try:
                result = subprocess.run(['arp', '-a'], capture_output=True, text=True, timeout=2)
                if result.returncode == 0:
                    for line in result.stdout.strip().split('\n'):
                        if '192.168.4.' in line and 'wlan0' in line:
                            debug_print(f"Device connected: {line.strip()}")
                            return True
            except Exception:
                pass

            time.sleep(1)

            with self.lock:
                if not self.running:
                    return False

    def _run_instructions(self):
        """Main instruction display loop — steps advance only on real events."""
        try:
            while True:
                with self.lock:
                    if not self.running:
                        break
                    messages = self._get_messages(self.current_step)

                # Display the current step
                self._show_step_on_oled(messages)

                # Handle step-specific logic
                if self.current_step == "welcome":
                    # Brief splash — only fixed-time step
                    time.sleep(3)
                    self._advance_step()

                elif self.current_step == "connect_wifi":
                    # Wait until a device actually joins the AP
                    if self._wait_for_device_connection():
                        self._advance_step()

                elif self.current_step == "open_browser":
                    # Wait until the web page is actually opened
                    while True:
                        with self.lock:
                            if not self.running:
                                return
                            if self.webpage_accessed:
                                self._advance_step()
                                break
                        time.sleep(0.5)

                elif self.current_step == "fill_form":
                    # Wait until the form is actually submitted
                    while True:
                        with self.lock:
                            if not self.running:
                                return
                            if self.form_submitted:
                                self._advance_step()
                                break
                        time.sleep(0.5)

                elif self.current_step == "waiting":
                    # Completion message before restart
                    time.sleep(3)
                    break  # Final step

                else:
                    break

        except Exception as e:
            debug_print(f"Instruction manager error: {e}")
        finally:
            with self.lock:
                self.running = False

    def _show_step_on_oled(self, messages):
        """Display step messages on OLED, with QR code for the connect step."""
        if not self.oled:
            return

        try:
            from luma.core.render import canvas

            # On the "connect_wifi" step, show a WiFi QR code
            if self.current_step == "connect_wifi":
                qr_img = self._make_wifi_qr()
                if qr_img is not None:
                    with canvas(self.oled) as draw:
                        # Paste QR onto the canvas's underlying image
                        draw._image.paste(qr_img, (0, 2))
                        # Text on the right side
                        ap_ssid, _ = self.get_ap_info()
                        draw.text((62, 0),  t("setup_step1"), fill=1)
                        ssid_short = ap_ssid if len(ap_ssid) <= 11 else ap_ssid[:10] + "\u2026"
                        draw.text((62, 14), ssid_short, fill=1)
                        draw.text((62, 30), "Scan QR", fill=1)
                        draw.text((62, 44), t("setup_connect"), fill=1)
                    return

            # Default: show text messages
            with canvas(self.oled) as draw:
                y = 0
                for msg in messages[:4]:  # Max 4 lines on 64px display
                    draw.text((0, y), msg, fill=1)
                    y += 16

        except Exception as e:
            debug_print(f"OLED display error: {e}")

    def _make_wifi_qr(self):
        """
        Generate a WiFi QR code as a 1-bit PIL Image sized for the OLED.
        Format: WIFI:T:nopass;S:<SSID>;; (auto-connects phone to open network).
        Returns a PIL Image or None if qrcode library is unavailable.
        """
        if not hasattr(self, '_cached_qr'):
            self._cached_qr = None
            try:
                import qrcode
                from PIL import Image

                ap_ssid, _ = self.get_ap_info()
                payload = f"WIFI:T:nopass;S:{ap_ssid};;"

                qr = qrcode.QRCode(
                    version=None,
                    error_correction=qrcode.constants.ERROR_CORRECT_L,
                    box_size=2,
                    border=1,
                )
                qr.add_data(payload)
                qr.make(fit=True)

                # Generate and convert to 1-bit
                img = qr.make_image(fill_color="white", back_color="black")
                img = img.convert("1")

                # Scale to fit OLED height (64px) with margin
                target_h = 58
                w, h = img.size
                scale = target_h / h
                new_w = int(w * scale)
                new_h = int(h * scale)
                img = img.resize((new_w, new_h), Image.NEAREST)

                self._cached_qr = img
                debug_print(f"WiFi QR generated: {new_w}x{new_h}px, payload={payload}")
            except ImportError:
                debug_print("qrcode library not installed — run: pip install qrcode")
            except Exception as e:
                debug_print(f"QR generation failed: {e}")
                import traceback; traceback.print_exc()

        return self._cached_qr

# ---------------- QR → secret extraction ----------------
try:
    from process_qr_image import extract_secret_from_image
except Exception:
    extract_secret_from_image = None  # type: ignore

# ---------------- LED / UI runtime ----------------
from led_display import run_totp_display
try:
    from led_display import DEFAULT_SETTINGS  # {"brightness": 0.50, "hue": 0.33}
except Exception:
    DEFAULT_SETTINGS = {"brightness": 0.50, "hue": 0.33}

# ---------------- Encoder (optional) ----------------
try:
    from encoder import Encoder
except Exception:
    Encoder = None  # type: ignore

# ================= helpers =================

# ---------------- WiFi watchdog (keeps connection alive + periodic NTP) ----
class WifiWatchdog:
    """
    Background thread that:
      - Checks WiFi status every CHECK_INTERVAL seconds
      - Reconnects if connection drops
      - Re-syncs NTP every NTP_INTERVAL seconds
      - Exposes status for the OLED info screen
    """
    CHECK_INTERVAL = 30       # seconds between WiFi checks
    NTP_INTERVAL = 30 * 60    # 30 minutes between NTP syncs

    def __init__(self):
        self.connected = False
        self.ssid = ""
        self._thread = None
        self._stop = False
        self._last_ntp = time.time()  # assume we just synced at boot

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop = False
        self._thread = threading.Thread(target=self._run, daemon=True, name="wifi-watchdog")
        self._thread.start()
        debug_print("WiFi watchdog started")

    def stop(self):
        self._stop = True

    def _run(self):
        # Initial status check
        self._check_status()

        while not self._stop:
            time.sleep(self.CHECK_INTERVAL)
            if self._stop:
                break

            self._check_status()

            if not self.connected:
                debug_print("WiFi watchdog: connection lost, attempting reconnect...")
                if reconnect_wifi():
                    self._check_status()
                    if self.connected:
                        # Successful reconnect — resync NTP immediately
                        debug_print("WiFi watchdog: reconnected, syncing NTP...")
                        get_ntp_time()
                        self._last_ntp = time.time()

            # Periodic NTP resync
            if self.connected and (time.time() - self._last_ntp) >= self.NTP_INTERVAL:
                debug_print("WiFi watchdog: periodic NTP resync...")
                get_ntp_time()
                self._last_ntp = time.time()

    def _check_status(self):
        status = get_wifi_status()
        self.connected = status["connected"]
        self.ssid = status["ssid"]

def ensure_dirs():
    SECRETS_DIR.mkdir(parents=True, exist_ok=True)

def read_wifi_config() -> Tuple[Optional[str], Optional[str], str, str]:
    """Read SSID/password/country/language from wifi_config.txt (lines 1-4).
    Line 3 (country code) is optional and defaults to 'US'.
    Line 4 (language code) is optional and defaults to 'en'."""
    try:
        lines = WIFI_CONFIG.read_text(encoding="utf-8").splitlines()
        if len(lines) >= 2:
            ssid = lines[0].strip()
            pwd  = lines[1].strip()
            country = lines[2].strip().upper() if len(lines) >= 3 and lines[2].strip() else "US"
            language = lines[3].strip().lower() if len(lines) >= 4 and lines[3].strip() else "en"
            if ssid and pwd:
                return ssid, pwd, country, language
    except FileNotFoundError:
        pass
    except Exception as e:
        debug_print(f"wifi_config read error: {e}")
    return None, None, "US", "en"

def have_secret_text() -> bool:
    try:
        s = SECRET_FILE.read_text(encoding="utf-8").strip()
        return bool(s)
    except FileNotFoundError:
        return False
    except Exception:
        return False

def try_extract_secret_from_qr() -> bool:
    """If otp_secret.txt missing and otp_qr.png exists, try to extract & save."""
    if extract_secret_from_image is None:
        debug_print("QR extractor not available in this environment.")
        return False
    if not SECRET_QR_PNG.exists():
        return False
    try:
        debug_print(f"Extracting OTP secret from {SECRET_QR_PNG}")
        secret = extract_secret_from_image(str(SECRET_QR_PNG))
        if secret:
            SECRET_FILE.write_text(secret.strip(), encoding="utf-8")
            debug_print("Secret extracted and saved.")
            return True
        else:
            debug_print("Failed to extract OTP secret from QR.")
            return False
    except Exception as e:
        debug_print(f"QR extraction error: {e}")
        return False

def _ip_addrs() -> list[str]:
    try:
        out = subprocess.check_output(["hostname", "-I"], text=True).strip()
        return out.split() if out else []
    except Exception:
        return []

def need_setup(ssid: Optional[str], pwd: Optional[str], secret_present: bool,
               country: str = "US") -> Tuple[bool, bool, bool]:
    """
    Returns (need_any, need_wifi, need_qr)
    - need_wifi if no creds or connection fails
    - need_qr   if no secret text file
    """
    need_wifi = False
    need_qr   = not secret_present

    if ssid and pwd:
        debug_print(f"(NM) Connecting to SSID: {ssid} (country={country})")
        ok = connect_wifi(ssid, pwd, country=country)
        if ok:
            ips = " ".join(_ip_addrs())
            debug_print(f"(NM) Connected. IP(s): {ips}")
        else:
            debug_print("(NM) connect failed")
            need_wifi = True
    else:
        need_wifi = True

    return (need_wifi or need_qr), need_wifi, need_qr

def load_secret_text() -> str:
    try:
        return SECRET_FILE.read_text(encoding="utf-8").strip()
    except Exception:
        return ""

def load_user_settings():
    """Load user preferences with fallback to defaults"""
    settings_file = PROJECT_DIR / "user_settings.json"
    default_settings = {"brightness": 0.50, "hue": 0.33}

    try:
        if settings_file.exists():
            import json
            with open(settings_file, 'r') as f:
                saved = json.load(f)

            # Validate and merge with defaults
            settings = default_settings.copy()
            if 'hue' in saved:
                settings['hue'] = max(0.0, min(1.0, float(saved['hue'])))
            if 'brightness' in saved:
                settings['brightness'] = max(0.0, min(1.0, float(saved['brightness'])))

            debug_print(f"Loaded user settings: {settings}")
            return settings

    except Exception as e:
        debug_print(f"Failed to load user settings: {e}")

    debug_print(f"Using default settings: {default_settings}")
    return default_settings

# --- Language picker shown before first-time setup ---
def run_language_picker(oled_device) -> str:
    """
    Blocking OLED + encoder screen: rotate to pick a language, press to confirm.
    Returns the chosen language code (e.g. 'en', 'fr').
    Falls back to 'en' if no encoder or OLED is available.
    """
    if not oled_device:
        debug_print("No OLED for language picker, defaulting to 'en'")
        return "en"

    # Try to initialise an encoder for this picker
    enc = None
    if Encoder is not None:
        try:
            enc = Encoder()
            debug_print("Encoder available for language picker")
        except Exception as e:
            debug_print(f"Encoder unavailable for picker: {e}")

    if enc is None:
        debug_print("No encoder for language picker, defaulting to 'en'")
        return "en"

    codes = [code for code, _, _ in lang.LANGUAGES]
    idx = 0  # start on English

    try:
        from luma.core.render import canvas
        import time as _time

        # Button state for edge detection
        btn_was_pressed = False
        confirmed = False

        debug_print("Language picker started — rotate to browse, press to select")

        while not confirmed:
            # Read encoder
            step = enc.steps()
            raw_press = enc.pressed()

            if step:
                idx = (idx + step) % len(codes)
                lang.set_language(codes[idx])

            # Edge-detect the press
            if raw_press and not btn_was_pressed:
                confirmed = True
            btn_was_pressed = raw_press

            # Draw
            _, native, english = lang.LANGUAGES[idx]
            with canvas(oled_device) as draw:
                draw.text((0, 0),  t("lang_title"), fill=1)
                draw.text((0, 16), f"> {native}", fill=1)
                draw.text((0, 30), f"  ({english})", fill=1)
                draw.text((0, 48), t("press_next"), fill=1)

            _time.sleep(0.05)

    except Exception as e:
        debug_print(f"Language picker error: {e}")
    finally:
        try:
            enc.close()
        except Exception:
            pass

    chosen = codes[idx]
    lang.set_language(chosen)
    debug_print(f"Language selected: {chosen}")
    return chosen


# --- FIXED: Enhanced setup with progress tracking ---
def run_setup_with_progress_tracking(need_wifi: bool, need_qr: bool, oled_manager: OLEDManager):
    """Enhanced setup with proper progress tracking"""

    # Set up progress tracking
    progress_manager = ProgressiveSetupManager(oled_manager.device)

    # Connect the portal handler to our progress manager
    try:
        import wifi_web
        if hasattr(wifi_web, 'portal_handler') and wifi_web.portal_handler:
            wifi_web.portal_handler.set_manager(progress_manager)
            debug_print("Portal handler manager set: True")
        else:
            debug_print("Portal handler not available")
    except Exception as e:
        debug_print(f"Failed to set up portal handler: {e}")

    # Start progress display
    progress_manager.start()

    try:
        # Run the captive portal
        run_captive_portal(need_wifi=need_wifi, need_qr=need_qr)

    except Exception as e:
        debug_print(f"Setup error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        # Clean up progress tracking
        progress_manager.stop()
        # Give a moment for cleanup
        time.sleep(0.2)

    return True

# ================= entry =================

def main():
    ensure_dirs()

    # FIXED: Enhanced cleanup sequence on startup
    debug_print("Starting enhanced cleanup sequence")

    # Count active threads before cleanup
    active_count = threading.active_count()
    debug_print(f"Active threads before cleanup: {active_count}")

    # Initialize OLED manager early
    oled_manager = OLEDManager()

    try:
        # 1) Try to load/derive requirements BEFORE deciding to portal
        ssid, pwd, country, language = read_wifi_config()

        # Set OLED language (user_settings.json takes priority over wifi_config.txt)
        try:
            effective_lang = language
            settings_file = PROJECT_DIR / "user_settings.json"
            if settings_file.exists():
                import json as _json
                with open(settings_file) as _f:
                    saved = _json.load(_f)
                if saved.get("language"):
                    effective_lang = saved["language"]
                    debug_print(f"Language from user_settings.json: {effective_lang}")
            lang.set_language(effective_lang)
        except Exception:
            pass

        # Ensure secret exists (try to derive from a QR image if text missing)
        secret_present = have_secret_text()
        if not secret_present:
            if try_extract_secret_from_qr():
                secret_present = True

        # 2) Decide whether we need the setup portal (Wi-Fi and/or QR)
        need_any, need_wifi, need_qr = need_setup(ssid, pwd, secret_present, country=country)
        debug_print(f"Setup decision → need_any={need_any} need_wifi={need_wifi} need_qr={need_qr}")

        if need_any:
            debug_print("Starting Access Point mode...")

            # Initialize OLED for setup instructions
            oled_device = oled_manager.initialize()
            if oled_device:
                debug_print("OLED available for progressive setup instructions")

            # Language picker — let user choose before the rest of setup
            chosen_lang = run_language_picker(oled_device)

            # Persist the choice so the web portal pre-selects it and
            # subsequent boots remember it even before WiFi is configured
            try:
                import json as _json
                sf = PROJECT_DIR / "user_settings.json"
                saved = {}
                if sf.exists():
                    with open(sf) as _f:
                        saved = _json.load(_f)
                saved["language"] = chosen_lang
                with open(sf, "w") as _f:
                    _json.dump(saved, _f, indent=2)
                debug_print(f"Saved language '{chosen_lang}' to user_settings.json")
            except Exception as e:
                debug_print(f"Failed to persist language choice: {e}")

            # Start AP mode
            from start_ap_mode import start_ap_mode, stop_ap_mode
            start_ap_mode()

            try:
                # Run enhanced setup with progress tracking
                run_setup_with_progress_tracking(need_wifi, need_qr, oled_manager)

                # Show completion message
                if oled_device:
                    try:
                        from luma.core.render import canvas
                        with canvas(oled_device) as draw:
                            draw.text((0, 0), t("setup_complete"), fill=1)
                            draw.text((0, 16), t("setup_saved"), fill=1)
                            draw.text((0, 32), t("setup_restarting"), fill=1)
                        time.sleep(2)
                    except Exception as e:
                        debug_print(f"Completion message error: {e}")

            except Exception as e:
                debug_print(f"Setup process error: {e}")

                # Show error message
                if oled_device:
                    try:
                        from luma.core.render import canvas
                        with canvas(oled_device) as draw:
                            draw.text((0, 0), t("setup_error"), fill=1)
                            draw.text((0, 16), t("setup_check"), fill=1)
                            draw.text((0, 32), "192.168.4.1", fill=1)
                        time.sleep(2)
                    except Exception:
                        pass

            finally:
                debug_print("Shutting down captive portal")

                # FIXED: Enhanced cleanup sequence
                debug_print("Starting enhanced cleanup sequence")
                active_count = threading.active_count()
                debug_print(f"Active threads before cleanup: {active_count}")

                # Clean up OLED
                oled_manager.cleanup()

                # Force GPIO subsystem reset attempt
                try:
                    import subprocess
                    subprocess.run(["gpio", "reset"], capture_output=True)
                    debug_print("GPIO subsystem reset attempted")
                except Exception:
                    pass

                final_count = threading.active_count()
                debug_print(f"Final thread count: {final_count}")

                stop_ap_mode()

                # FIXED: Waiting for system cleanup...
                debug_print("Waiting for system cleanup...")
                time.sleep(1.0)

                # FIXED: Forcing I2C bus reset...
                debug_print("Forcing I2C bus reset...")
                try:
                    subprocess.run(["i2cdetect", "-y", "1"], capture_output=True, timeout=2)
                except Exception:
                    pass

                debug_print("About to restart program...")
                time.sleep(0.25)
                restart_program()
                return  # not reached

        # 3) All set: we have Wi-Fi and an OTP secret
        debug_print("Synchronizing time via NTP…")
        get_ntp_time()
        debug_print("NTP sync completed")

        # Initialize OLED for normal operation
        debug_print("Initializing OLED for splash screen...")
        oled_device = oled_manager.initialize()
        if oled_device:
            debug_print("OLED initialized, showing splash")
            try:
                from luma.core.render import canvas
                with canvas(oled_device) as draw:
                    draw.text((0, 0),  t("wifi_ok"), fill=1)
                    draw.text((0, 12), t("time_ok"), fill=1)
                    draw.text((0, 24), t("starting"), fill=1)
                debug_print("Splash screen displayed")
            except Exception as e:
                debug_print(f"OLED splash failed: {e}")

        # Initialize encoder (safe to continue without)
        debug_print("Initializing encoder...")
        encoder = None
        try:
            if Encoder is not None:
                encoder = Encoder()
                debug_print("Encoder initialized successfully")
        except Exception as e:
            debug_print(f"Encoder init failed ({e}); continuing without encoder")

        # Load secret
        debug_print("Loading OTP secret...")
        secret = load_secret_text()
        if not secret:
            print("Fatal: OTP secret missing unexpectedly."); sys.exit(1)

        debug_print(f"Secret loaded, length: {len(secret)}")
        debug_print("About to launch TOTP/LED display...")

        # 4) Run the LED/OLED TOTP display with user settings
        print("→ Launching TOTP/LED display (Ctrl-C to quit)\n")

        # Start WiFi watchdog (monitors connectivity + periodic NTP resync)
        watchdog = WifiWatchdog()
        watchdog.start()

        debug_print("Calling run_totp_display...")
        user_settings = load_user_settings()
        try:
            run_totp_display(secret, user_settings, oled=oled_device, encoder=encoder,
                             wifi_watchdog=watchdog)
        except KeyboardInterrupt:
            print("\n→ Exiting on user interrupt")
        finally:
            watchdog.stop()
            try:
                if encoder:
                    encoder.close()
            except Exception:
                pass

            # Clean up OLED manager
            oled_manager.cleanup()

    except Exception as e:
        debug_print(f"Main function error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        # Final cleanup
        try:
            oled_manager.cleanup()
        except Exception:
            pass

if __name__ == "__main__":
    main()
