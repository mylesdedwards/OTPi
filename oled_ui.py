# oled_ui.py - Final working version with settings persistence and i18n
from __future__ import annotations
import os, sys, time, subprocess, json
from pathlib import Path
from typing import Tuple, Optional
from lang import t
import lang

# --- Debug/event logging toggles ---
ENC_DBG_EVENTS = os.environ.get("OTPI_DEBUG_ENCODER_EVENTS", "0").lower() not in ("0","false","no","off")

# --- Encoder burst sampling (to avoid missed steps between frames) ---
def _env_int(name: str, default: int) -> int:
    try: return int(os.environ.get(name, str(default)))
    except Exception: return default

_BURST_MS       = _env_int("OTPI_ENC_BURST_MS", 12)          # total burst duration per frame
_BURST_INT_MS   = _env_int("OTPI_ENC_BURST_INTERVAL_MS", 1)  # interval between reads
_BURST_SEC      = max(0.0, _BURST_MS / 1000.0)
_BURST_INT_SEC  = max(0.0005, _BURST_INT_MS / 1000.0)

# --- Project paths ---
PROJECT_DIR = Path(__file__).resolve().parent
WIFI_CONFIG = PROJECT_DIR / "wifi_config.txt"
SECRET_FILE = PROJECT_DIR / "secrets" / "otp_secret.txt"
SETTINGS_FILE = PROJECT_DIR / "user_settings.json"

MAX_LED_BRIGHT = 0.80  # user 100% -> actual 80%
SETTINGS_SAVE_DELAY = 2.0  # seconds to wait before saving
OLED_SLEEP_SECS = 10  # blank OLED after this many seconds of inactivity

class ResetAction:
    NONE = "none"
    WIFI = "wifi"
    QR   = "qr"
    BOTH = "both"

class OledUI:
    """
    6-screen UI:
      0 Basic (OTP, hue°, %)
      1 Color (rotate -> hue)
      2 Brightness (rotate 0..100%, capped to 80% actual)
      3 Language (rotate to pick, press to confirm)
      4 Reset menu (Next, Reset Wi-Fi, Reset QR, Reset Both)
      5 Confirm screen (PRESS = confirm; ROTATE = cancel/back)
    """
    def __init__(self, oled, initial_hue: float, user_brightness_pct: int):
        self.oled = oled
        self.screen = 0  # Start on info screen
        self.selection = 0
        self.confirm_for: Optional[str] = None

        # Load saved settings or use provided defaults
        saved_settings = self._load_settings()
        self.hue = float(saved_settings.get('hue', initial_hue)) % 1.0
        self.user_pct = int(max(0, min(100, saved_settings.get('brightness', user_brightness_pct))))

        # Language state – index into lang.LANGUAGES
        self._lang_codes = [code for code, _, _ in lang.LANGUAGES]
        cur_lang = saved_settings.get('language', lang.get_language())
        try:
            self._lang_idx = self._lang_codes.index(cur_lang)
        except ValueError:
            self._lang_idx = 0
        self._last_saved_lang = self._lang_codes[self._lang_idx]

        self._last_draw_ts = 0.0

        # Button state tracking for proper edge detection
        self._btn_was_pressed = False
        self._btn_debounce_time = 0.0
        self._last_screen_change = time.perf_counter()

        # Force initial draw of info screen
        self._force_draw_next = True

        # Scrolling text for reset screen
        self._scroll_pos = 0
        self._scroll_timer = 0.0
        self._scroll_speed = 0.5  # seconds between character advances

        # Settings auto-save tracking
        self._last_setting_change = 0.0
        self._settings_dirty = False
        self._last_saved_hue = self.hue
        self._last_saved_brightness = self.user_pct

        # OLED sleep/wake tracking
        self._last_activity = time.perf_counter()
        self._oled_sleeping = False

        # WiFi status (updated externally via set_wifi_status)
        self._wifi_ssid = ""
        self._wifi_connected = False

        print(f"[UI] Initialized on screen {self.screen}")
        print(f"[UI] Loaded settings: hue={self.hue:.3f}, brightness={self.user_pct}%, lang={self._lang_codes[self._lang_idx]}")

    def set_wifi_status(self, connected: bool, ssid: str = ""):
        """Called externally to update WiFi status shown on info screen."""
        self._wifi_connected = connected
        self._wifi_ssid = ssid

    def _load_settings(self) -> dict:
        """Load user settings from JSON file"""
        try:
            if SETTINGS_FILE.exists():
                with open(SETTINGS_FILE, 'r') as f:
                    settings = json.load(f)
                print(f"[UI] Loaded settings from {SETTINGS_FILE}")
                return settings
        except Exception as e:
            print(f"[UI] Failed to load settings: {e}")
        return {}

    def _save_settings(self):
        """Save current settings to JSON file"""
        try:
            settings = {
                'hue': float(self.hue),
                'brightness': int(self.user_pct),
                'language': self._lang_codes[self._lang_idx],
                'saved_at': time.time()
            }

            # Ensure directory exists
            SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)

            # Write atomically (write to temp file, then rename)
            temp_file = SETTINGS_FILE.with_suffix('.json.tmp')
            with open(temp_file, 'w') as f:
                json.dump(settings, f, indent=2)
            temp_file.replace(SETTINGS_FILE)

            cur_lang = self._lang_codes[self._lang_idx]
            print(f"[UI] Saved settings: hue={self.hue:.3f}, brightness={self.user_pct}%, lang={cur_lang}")

            # Also update wifi_config.txt line 4 so next boot picks it up
            self._persist_language_to_wifi_config(cur_lang)

            # Update tracking
            self._last_saved_hue = self.hue
            self._last_saved_brightness = self.user_pct
            self._last_saved_lang = cur_lang
            self._settings_dirty = False

        except Exception as e:
            print(f"[UI] Failed to save settings: {e}")

    def _persist_language_to_wifi_config(self, lang_code: str):
        """Update line 4 of wifi_config.txt without touching WiFi credentials."""
        try:
            if not WIFI_CONFIG.exists():
                return
            lines = WIFI_CONFIG.read_text(encoding="utf-8").splitlines()
            # Ensure at least 4 lines (ssid, pwd, country, lang)
            while len(lines) < 4:
                lines.append("")
            lines[3] = lang_code
            WIFI_CONFIG.write_text("\n".join(lines) + "\n", encoding="utf-8")
        except Exception as e:
            print(f"[UI] Failed to update wifi_config language: {e}")

    def _check_auto_save(self):
        """Check if settings need to be auto-saved after delay"""
        now = time.perf_counter()

        # Check if settings changed
        hue_changed = abs(self.hue - self._last_saved_hue) > 0.001
        brightness_changed = self.user_pct != self._last_saved_brightness
        lang_changed = self._lang_codes[self._lang_idx] != self._last_saved_lang

        if hue_changed or brightness_changed or lang_changed:
            # FIXED: Only update timestamp if we weren't already dirty
            if not self._settings_dirty:
                print(f"[UI] Settings became dirty, starting timer")
                self._settings_dirty = True
                self._last_setting_change = now
            # If already dirty, don't reset the timer - keep the original timestamp

        # Auto-save if dirty and delay elapsed
        time_since_change = now - self._last_setting_change
        if (self._settings_dirty and time_since_change >= SETTINGS_SAVE_DELAY):
            print(f"[UI] Auto-saving settings after {time_since_change:.1f}s delay")
            self._save_settings()

    # --- main entry each frame ---
    def handle(self, encoder, code: str, secs_left: int) -> Tuple[float,int,str]:
        """
        Reads encoder with burst sampling, updates UI, draws OLED.
        Returns (hue, user_pct, reset_action).
        """
        if not encoder:
            # No encoder available — still handle sleep timer
            now = time.perf_counter()
            if (now - self._last_activity) >= OLED_SLEEP_SECS:
                if not self._oled_sleeping:
                    self._sleep_oled()
            elif not self._oled_sleeping:
                self._draw(code, secs_left)
            self._check_auto_save()
            return (self.hue, self.user_pct, ResetAction.NONE)

        step, raw_press = self._read_inputs_safer(encoder)

        # Improved button edge detection with debouncing
        now = time.perf_counter()
        pressed_edge = False

        if raw_press and not self._btn_was_pressed:
            if now - self._btn_debounce_time > 0.1:  # 100ms debounce
                pressed_edge = True
                self._btn_debounce_time = now

        self._btn_was_pressed = raw_press

        has_activity = bool(step) or pressed_edge

        # ── OLED sleep/wake ──
        if self._oled_sleeping:
            if has_activity:
                # Wake up — consume the input so it doesn't also change screens
                self._wake_oled()
            self._check_auto_save()
            return (self.hue, self.user_pct, ResetAction.NONE)

        if has_activity:
            self._last_activity = now
        elif (now - self._last_activity) >= OLED_SLEEP_SECS:
            self._sleep_oled()
            self._check_auto_save()
            return (self.hue, self.user_pct, ResetAction.NONE)

        if ENC_DBG_EVENTS:
            if step:
                print(f"[ENC] step={step}")
            if pressed_edge:
                print("[ENC] pressed(edge)")

        reset_action = ResetAction.NONE
        old_screen = self.screen

        # Prevent screen changes too quickly
        screen_change_delay = 0.2  # 200ms minimum between screen changes
        can_change_screen = (now - self._last_screen_change) > screen_change_delay

        # Screen logic
        if self.screen == 0:  # Basic/Info
            if pressed_edge and can_change_screen:
                self.screen = 1
                self._last_screen_change = now

        elif self.screen == 1:  # Color
            if step:
                # Reverse direction: positive step = clockwise = increase hue
                self.hue = (self.hue + step * 0.01) % 1.0
            if pressed_edge and can_change_screen:
                self.screen = 2
                self._last_screen_change = now

        elif self.screen == 2:  # Brightness
            if step:
                # Reverse direction: positive step = clockwise = increase brightness
                self.user_pct = int(max(0, min(100, self.user_pct + step)))
            if pressed_edge and can_change_screen:
                self.screen = 3
                self._last_screen_change = now

        elif self.screen == 3:  # Language
            if step:
                self._lang_idx = (self._lang_idx + step) % len(self._lang_codes)
                # Apply immediately so the screen redraws in the new language
                lang.set_language(self._lang_codes[self._lang_idx])
            if pressed_edge and can_change_screen:
                self.screen = 4
                self._last_screen_change = now

        elif self.screen == 4:  # Reset menu
            if step:
                # Reverse direction: positive step = clockwise = next item (down the list)
                self.selection = (self.selection + step) % 4
            if pressed_edge and can_change_screen:
                if self.selection == 0:
                    self.screen = 0
                else:
                    self.screen = 5
                    self.confirm_for = [ResetAction.WIFI, ResetAction.QR, ResetAction.BOTH][self.selection-1]
                self._last_screen_change = now

        elif self.screen == 5:  # Confirm
            # ROTATE = cancel/back, PRESS(edge) = confirm
            if step and can_change_screen:
                self.screen = 0
                self.confirm_for = None
                self._last_screen_change = now
            elif pressed_edge and can_change_screen:
                if self.confirm_for:
                    reset_action = self.confirm_for
                    print(f"[UI] Reset action confirmed: {reset_action}")
                # after signaling action, app will restart; reset state here for safety
                self.screen = 0
                self.confirm_for = None
                self._last_screen_change = now

        if self.screen != old_screen:
            print(f"[UI] screen {old_screen} → {self.screen}")
            self._force_draw_next = True
            # Reset scroll position when entering reset screen
            if self.screen == 4:
                self._scroll_pos = 0
                self._scroll_timer = time.time()

        self._draw(code, secs_left)
        self._check_auto_save()
        return (self.hue, self.user_pct, reset_action)

    def actual_brightness(self) -> float:
        return (self.user_pct / 100.0) * MAX_LED_BRIGHT

    def _get_scrolling_text(self, text: str, max_width: int = 21) -> str:
        """Get scrolling text that fits within max_width characters"""
        if len(text) <= max_width:
            return text

        now = time.time()
        if now - self._scroll_timer >= self._scroll_speed:
            self._scroll_pos = (self._scroll_pos + 1) % (len(text) + 3)  # +3 for spacing
            self._scroll_timer = now

        # Create scrolling effect with padding
        extended_text = text + "   " + text  # Add spacing and repeat
        start_pos = self._scroll_pos % len(extended_text)
        visible_text = extended_text[start_pos:start_pos + max_width]

        # If we don't have enough characters, wrap around
        if len(visible_text) < max_width:
            visible_text += extended_text[:max_width - len(visible_text)]

        return visible_text

    # --- Safer encoder reading with better error handling ---
    def _read_inputs_safer(self, encoder) -> Tuple[int, bool]:
        total_steps = 0
        any_press = False

        # Read multiple times over a short period for better responsiveness
        samples = 5
        for _ in range(samples):
            try:
                s = encoder.steps()
                if s:
                    total_steps += s
            except Exception as e:
                if ENC_DBG_EVENTS:
                    print(f"[ENC] steps() error: {e}")

            try:
                if encoder.pressed():
                    any_press = True
            except Exception as e:
                if ENC_DBG_EVENTS:
                    print(f"[ENC] pressed() error: {e}")

            time.sleep(0.002)  # 2ms between samples

        return total_steps, any_press

    # --- OLED sleep/wake ---
    def _sleep_oled(self):
        """Blank the OLED to prevent burn-in."""
        if self._oled_sleeping:
            return
        self._oled_sleeping = True
        if self.oled:
            try:
                from luma.core.render import canvas
                with canvas(self.oled) as draw:
                    pass  # draw nothing → blank screen
                print("[UI] OLED sleeping (burn-in protection)")
            except Exception:
                pass

    def _wake_oled(self):
        """Wake the OLED and force a redraw."""
        self._oled_sleeping = False
        self._last_activity = time.perf_counter()
        self._force_draw_next = True
        print("[UI] OLED waking up")

    # --- drawing ---
    def _draw(self, code: str, secs_left: int):
        if not self.oled:
            return
        if self._oled_sleeping:
            return

        now = time.time()

        # Force draw on screen changes or info screen updates
        should_draw = (self._force_draw_next or
                      self.screen == 0 or  # Always update info screen
                      now - self._last_draw_ts > 0.05)  # 50ms max for other screens

        if not should_draw:
            return

        self._last_draw_ts = now
        self._force_draw_next = False

        try:
            from luma.core.render import canvas

            with canvas(self.oled) as draw:
                if self.screen == 0:
                    # Info screen - show current status
                    draw.text((0, 0),  f"{t('otp')}  : {code}", fill=1)
                    draw.text((0, 14), f"{t('time')} : {secs_left:2d}s", fill=1)
                    draw.text((0, 25), f"{t('hue')}  : {int(self.hue*360):3d}\xb0", fill=1)
                    draw.text((0, 37), f"{t('bright')}: {self.user_pct:3d}%", fill=1)
                    # WiFi status line
                    wifi_txt = "WiFi: OK" if self._wifi_connected else "WiFi: --"
                    draw.text((0, 52), wifi_txt, fill=1)

                elif self.screen == 1:
                    draw.text((0, 0), t("color_title"), fill=1)
                    draw.text((0, 14), f"{t('hue')}: {int(self.hue*360):3d}\xb0", fill=1)
                    draw.text((0, 28), t("rotate_color"), fill=1)
                    draw.text((0, 42), t("press_next"), fill=1)

                elif self.screen == 2:
                    draw.text((0, 0), t("bright_title"), fill=1)
                    draw.text((0, 14), f"{t('level')}: {self.user_pct:3d}%", fill=1)
                    draw.text((0, 28), t("press_next"), fill=1)

                elif self.screen == 3:
                    # Language picker
                    code = self._lang_codes[self._lang_idx]
                    _, native, english = lang.LANGUAGES[self._lang_idx]
                    draw.text((0, 0), t("lang_title"), fill=1)
                    draw.text((0, 14), f"> {native}", fill=1)
                    draw.text((0, 28), f"  ({english})", fill=1)
                    draw.text((0, 42), t("rotate_lang"), fill=1)
                    draw.text((0, 54), t("press_next"), fill=1)

                elif self.screen == 4:
                    opts = [t("next_screen"), t("reset_wifi"), t("reset_qr"), t("reset_both")]
                    draw.text((0, 0), t("reset_title"), fill=1)
                    y = 14
                    for i, s in enumerate(opts):
                        prefix = ">" if i == self.selection else " "
                        text = f"{prefix} {s}"
                        if len(text) > 20:  # Truncate if too long
                            text = text[:19] + "..."
                        draw.text((0, y), text, fill=1)
                        y += 10

                    # Scrolling bottom text
                    scroll_text = self._get_scrolling_text(t("scroll_hint"))
                    draw.text((0, 54), scroll_text, fill=1)

                elif self.screen == 5:
                    label = {
                        ResetAction.WIFI: t("confirm_wifi"),
                        ResetAction.QR:   t("confirm_qr"),
                        ResetAction.BOTH: t("confirm_both"),
                    }.get(self.confirm_for, "Reset?")
                    draw.text((0, 0), t("confirm_title"), fill=1)
                    draw.text((0, 14), label, fill=1)
                    draw.text((0, 28), t("press_yes"), fill=1)
                    draw.text((0, 40), t("rotate_cancel"), fill=1)
                    draw.text((0, 52), t("restarts_after"), fill=1)

        except Exception as e:
            print(f"[UI] Draw error: {e}")

# --- Hard reset approach for reliable LED recovery ---
def perform_reset(which: str, oled=None, led_strip=None):
    """
    CRITICAL FIX: Hard system reset for reliable hardware state recovery
    """
    print(f"[RESET] Starting reset action: {which}")

    # Clear LED strip immediately so stale codes aren't visible during reboot
    if led_strip is not None:
        try:
            led_strip.fill((0, 0, 0))
            led_strip.show()
            print("[RESET] LED strip cleared")
        except Exception as e:
            print(f"[RESET] LED strip clear failed: {e}")

    # Show reset message on OLED
    if oled:
        try:
            from luma.core.render import canvas
            with canvas(oled) as draw:
                draw.text((0, 0), t("resetting"), fill=1)
                draw.text((0, 16), f"{t('action')}: {which}", fill=1)
                draw.text((0, 32), t("system_reboot"), fill=1)
                draw.text((0, 48), t("please_wait"), fill=1)
            time.sleep(2.0)  # Give user time to read
        except Exception as e:
            print(f"[RESET] OLED message failed: {e}")

    # Perform the actual reset operations (delete files)
    try:
        files_deleted = []

        if which in (ResetAction.WIFI, ResetAction.BOTH):
            try:
                if WIFI_CONFIG.exists():
                    WIFI_CONFIG.unlink()
                    files_deleted.append("wifi_config.txt")
                    print(f"[RESET] Deleted {WIFI_CONFIG}")
            except Exception as e:
                print(f"[RESET] Failed to delete wifi config: {e}")

        if which in (ResetAction.QR, ResetAction.BOTH):
            try:
                if SECRET_FILE.exists():
                    SECRET_FILE.unlink()
                    files_deleted.append("otp_secret.txt")
                    print(f"[RESET] Deleted {SECRET_FILE}")

                # Also delete QR image if it exists
                qr_file = PROJECT_DIR / "secrets" / "otp_qr.png"
                if qr_file.exists():
                    qr_file.unlink()
                    files_deleted.append("otp_qr.png")
                    print(f"[RESET] Deleted {qr_file}")
            except Exception as e:
                print(f"[RESET] Failed to delete QR/secret files: {e}")

        print(f"[RESET] Files deleted: {files_deleted}")

        # Final OLED message before reboot
        if oled:
            try:
                from luma.core.render import canvas
                with canvas(oled) as draw:
                    draw.text((0, 0), t("files_deleted"), fill=1)
                    draw.text((0, 16), t("rebooting"), fill=1)
                    draw.text((0, 32), t("wait_reboot"), fill=1)
                time.sleep(2.0)
            except Exception:
                pass

    except Exception as e:
        print(f"[RESET] Reset operation failed: {e}")

    # FIXED: Trigger hard system reboot for reliable hardware reset
    print("[RESET] Triggering system reboot for complete hardware reset...")

    # Final LED cleanup before reboot
    if led_strip is not None:
        try:
            led_strip.deinit()
            print("[RESET] LED strip deinitialized")
        except Exception:
            pass

    try:
        # Sync filesystems first
        subprocess.run(["sync"], timeout=5)

        # Clean system reboot
        subprocess.run(["sudo", "reboot"], timeout=5)

    except Exception as e:
        print(f"[RESET] Reboot command failed: {e}")
        # Fallback: force reboot
        try:
            subprocess.run(["sudo", "reboot", "-f"], timeout=5)
        except Exception:
            # Last resort: immediate reboot
            with open("/proc/sys/kernel/sysrq", "w") as f:
                f.write("1")
            with open("/proc/sysrq-trigger", "w") as f:
                f.write("b")  # Immediate reboot

    # This should never be reached due to reboot
    time.sleep(10)
