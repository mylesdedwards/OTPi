#!/usr/bin/env python3
# wifi_web.py – Enhanced with SSID scanning, hidden network support, country selection, and i18n
from __future__ import annotations
import os, sys, io, json, threading, subprocess
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs
import cgi

try:
    import lang
    from lang import t
except Exception:
    def t(key: str) -> str: return key

PROJECT_DIR   = Path(__file__).resolve().parent
SECRETS_DIR   = PROJECT_DIR / "secrets"
WIFI_CONFIG   = PROJECT_DIR / "wifi_config.txt"
SECRET_QR_PNG = SECRETS_DIR / "otp_qr.png"

# ── WiFi country codes (ISO 3166-1 alpha-2) ──────────────────────────
# Sorted by likely usage; the full list covers regulatory domains that
# affect which channels / power levels the radio is allowed to use.
COUNTRY_CODES = [
    ("US", "United States"),
    ("GB", "United Kingdom"),
    ("CA", "Canada"),
    ("AU", "Australia"),
    ("DE", "Germany"),
    ("FR", "France"),
    ("NL", "Netherlands"),
    ("IT", "Italy"),
    ("ES", "Spain"),
    ("SE", "Sweden"),
    ("NO", "Norway"),
    ("DK", "Denmark"),
    ("FI", "Finland"),
    ("CH", "Switzerland"),
    ("AT", "Austria"),
    ("BE", "Belgium"),
    ("IE", "Ireland"),
    ("PT", "Portugal"),
    ("PL", "Poland"),
    ("CZ", "Czech Republic"),
    ("NZ", "New Zealand"),
    ("JP", "Japan"),
    ("KR", "South Korea"),
    ("TW", "Taiwan"),
    ("SG", "Singapore"),
    ("HK", "Hong Kong"),
    ("IN", "India"),
    ("BR", "Brazil"),
    ("MX", "Mexico"),
    ("AR", "Argentina"),
    ("CL", "Chile"),
    ("CO", "Colombia"),
    ("ZA", "South Africa"),
    ("IL", "Israel"),
    ("AE", "United Arab Emirates"),
    ("SA", "Saudi Arabia"),
    ("TR", "Turkey"),
    ("RU", "Russia"),
    ("UA", "Ukraine"),
    ("RO", "Romania"),
    ("HU", "Hungary"),
    ("GR", "Greece"),
    ("HR", "Croatia"),
    ("BG", "Bulgaria"),
    ("MY", "Malaysia"),
    ("TH", "Thailand"),
    ("PH", "Philippines"),
    ("ID", "Indonesia"),
    ("VN", "Vietnam"),
    ("EG", "Egypt"),
    ("NG", "Nigeria"),
    ("KE", "Kenya"),
]

# ── WiFi scanning ─────────────────────────────────────────────────────

def scan_wifi_networks(iface: str = "wlan0") -> list[dict]:
    """
    Scan for visible WiFi networks.  Returns a de-duplicated list sorted by
    signal strength, each entry: {"ssid": str, "signal": int, "security": str}.
    Tries nmcli first, falls back to iwlist.
    """
    networks: dict[str, dict] = {}

    # ── Method 1: nmcli ──
    try:
        # Trigger a fresh scan (ignore errors — AP mode may block it)
        subprocess.run(
            ["nmcli", "-w", "5", "dev", "wifi", "rescan", "ifname", iface],
            capture_output=True, timeout=10,
        )
        out = subprocess.check_output(
            ["nmcli", "-t", "-f", "SSID,SIGNAL,SECURITY", "dev", "wifi", "list", "--rescan", "no"],
            text=True, timeout=10,
        )
        for line in out.strip().splitlines():
            parts = line.split(":")
            if len(parts) >= 3:
                ssid = parts[0].strip()
                if not ssid:
                    continue
                try:
                    signal = int(parts[1])
                except ValueError:
                    signal = 0
                security = parts[2].strip() or "Open"
                # Keep strongest signal per SSID
                if ssid not in networks or signal > networks[ssid]["signal"]:
                    networks[ssid] = {"ssid": ssid, "signal": signal, "security": security}
        if networks:
            return sorted(networks.values(), key=lambda n: n["signal"], reverse=True)
    except Exception as e:
        print(f"[DEBUG] nmcli scan failed: {e}")

    # ── Method 2: iwlist (works even without NetworkManager) ──
    try:
        out = subprocess.check_output(
            ["iwlist", iface, "scan"], text=True, timeout=15, stderr=subprocess.DEVNULL,
        )
        current_ssid = None
        current_signal = 0
        current_security = "Open"
        for line in out.splitlines():
            line = line.strip()
            if "ESSID:" in line:
                # Save previous network
                if current_ssid:
                    if current_ssid not in networks or current_signal > networks[current_ssid]["signal"]:
                        networks[current_ssid] = {
                            "ssid": current_ssid,
                            "signal": current_signal,
                            "security": current_security,
                        }
                ssid_part = line.split("ESSID:")[-1].strip().strip('"')
                current_ssid = ssid_part if ssid_part else None
                current_signal = 0
                current_security = "Open"
            elif "Signal level=" in line:
                try:
                    sig_str = line.split("Signal level=")[-1].split(" ")[0].split("/")[0]
                    current_signal = int(sig_str)
                    # Normalise dBm to rough 0-100
                    if current_signal < 0:
                        current_signal = max(0, min(100, 2 * (current_signal + 100)))
                except ValueError:
                    pass
            elif "WPA" in line or "WEP" in line:
                current_security = "WPA" if "WPA" in line else "WEP"
        # Don't forget the last one
        if current_ssid:
            if current_ssid not in networks or current_signal > networks[current_ssid]["signal"]:
                networks[current_ssid] = {
                    "ssid": current_ssid,
                    "signal": current_signal,
                    "security": current_security,
                }
        if networks:
            return sorted(networks.values(), key=lambda n: n["signal"], reverse=True)
    except Exception as e:
        print(f"[DEBUG] iwlist scan failed: {e}")

    return []


# ── Portal handler for progress tracking ──────────────────────────────

class PortalInstructionHandler:
    """Handles communication between web portal and OLED instructions"""

    def __init__(self):
        self.instruction_manager = None

    def set_manager(self, manager):
        self.instruction_manager = manager
        print("[DEBUG] Portal handler manager set")

    def on_webpage_access(self):
        print("[DEBUG] on_webpage_access() called")
        if self.instruction_manager:
            self.instruction_manager.mark_webpage_accessed()

    def on_form_submit(self):
        print("[DEBUG] on_form_submit() called")
        if self.instruction_manager:
            self.instruction_manager.mark_form_submitted()


# FIXED: Initialize global handler immediately
portal_handler = PortalInstructionHandler()
print("[DEBUG] Global portal handler created")


# ── HTML helpers ──────────────────────────────────────────────────────

def _html_page(body: str, title: str = None):
    if title is None:
        title = t("web_title")
    return f"""<!doctype html>
<html><head>
  <meta charset="utf-8">
  <title>{title}</title>
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <style>
    body {{ font-family: system-ui, sans-serif; max-width: 640px; margin: 1.5rem auto; padding: 0 1rem; }}
    h1 {{ font-size: 1.4rem; }}
    h2 {{ font-size: 1.15rem; margin-top: 1.2rem; }}
    form {{ border: 1px solid #ddd; border-radius: 8px; padding: 1rem; margin: 1rem 0; }}
    label {{ display:block; margin:.5rem 0 .25rem; font-weight: 500; }}
    input[type=text],input[type=password],select{{width:100%; padding:.5rem; font-size:1rem; box-sizing:border-box;}}
    input[type=file]{{margin:.5rem 0;}}
    button{{padding:.6rem 1rem; font-size:1rem; cursor:pointer;}}
    .ok{{color:#0a0}}
    .err{{color:#a00}}
    .hint{{color:#555; font-size:.9rem}}
    .footer{{margin-top:1rem; color:#666; font-size:.9rem}}
    .scan-btn{{padding:.35rem .7rem; font-size:.85rem; margin-left:.5rem; vertical-align:middle;}}
    .ssid-row{{display:flex; align-items:center; gap:.4rem;}}
    .ssid-row select{{flex:1;}}
    #manualSsidWrap{{display:none; margin-top:.35rem;}}
  </style>
</head><body>
{body}
<div class="footer">{t('web_footer')}: <code>http://192.168.4.1</code></div>
</body></html>"""


def _country_options_html(selected: str = "US") -> str:
    """Build <option> tags for the country dropdown."""
    opts = []
    for code, name in COUNTRY_CODES:
        sel = ' selected' if code == selected else ''
        opts.append(f'<option value="{code}"{sel}>{name} ({code})</option>')
    return "\n".join(opts)


# ── Supported OLED languages (must match lang.py) ────────────────────
OLED_LANGUAGES = [
    ("en", "English"),
    ("es", "Español (Spanish)"),
    ("fr", "Français (French)"),
    ("de", "Deutsch (German)"),
    ("it", "Italiano (Italian)"),
    ("pt", "Português (Portuguese)"),
    ("nl", "Nederlands (Dutch)"),
    ("sv", "Svenska (Swedish)"),
    ("da", "Dansk (Danish)"),
    ("no", "Norsk (Norwegian)"),
]


def _language_options_html(selected: str = "en") -> str:
    """Build <option> tags for the language dropdown."""
    opts = []
    for code, name in OLED_LANGUAGES:
        sel = ' selected' if code == selected else ''
        opts.append(f'<option value="{code}"{sel}>{name}</option>')
    return "\n".join(opts)


def _wifi_form_block(ssid_val: str = "", country_val: str = "US",
                     language_val: str = "en") -> str:
    """
    Reusable HTML block for Wi-Fi fields — all labels use t() for translation.
    """
    return f"""
      <h2>{t('web_wifi_title')}</h2>

      <label for="countrySelect">{t('web_country_label')}</label>
      <select name="country" id="countrySelect">
        {_country_options_html(country_val)}
      </select>
      <p class="hint">{t('web_country_hint')}</p>

      <label for="langSelect">{t('web_lang_label')}</label>
      <select name="language" id="langSelect">
        {_language_options_html(language_val)}
      </select>
      <p class="hint">{t('web_lang_hint')}</p>

      <label for="ssidSelect">{t('web_ssid_label')}</label>
      <div class="ssid-row">
        <select name="ssid_select" id="ssidSelect">
          <option value="" disabled selected>{t('web_scanning')}</option>
        </select>
        <button type="button" class="scan-btn" id="refreshBtn" title="Rescan">&#x21bb; Scan</button>
      </div>
      <div id="manualSsidWrap">
        <label for="manualSsid">{t('web_ssid_manual_label')}</label>
        <input name="ssid_manual" type="text" id="manualSsid" placeholder="{t('web_ssid_placeholder')}" value="{ssid_val}">
      </div>

      <label for="passwordInput">{t('web_password_label')}</label>
      <input name="password" type="password" required id="passwordInput">

      <!-- hidden field populated by JS with the effective SSID -->
      <input type="hidden" name="ssid" id="ssidHidden">
"""


def _wifi_js() -> str:
    """Generate WiFi scan JS with translated strings injected."""
    js_scanning = t('web_scanning').replace("'", "\\'")
    js_no_nets  = t('web_no_networks').replace("'", "\\'")
    js_select   = t('web_select_network').replace("'", "\\'")
    js_not_shown = t('web_not_shown').replace("'", "\\'")

    return f"""
<script>
(function() {{
  var ssidSelect  = document.getElementById('ssidSelect');
  var refreshBtn  = document.getElementById('refreshBtn');
  var manualWrap  = document.getElementById('manualSsidWrap');
  var manualInput = document.getElementById('manualSsid');
  var ssidHidden  = document.getElementById('ssidHidden');
  var isManual    = false;

  function signalIcon(pct) {{
    if (pct >= 70) return '\u2587\u2587\u2587';
    if (pct >= 40) return '\u2585\u2585 ';
    return '\u2583  ';
  }}

  function populateDropdown(networks) {{
    ssidSelect.innerHTML = '';

    if (networks.length === 0) {{
      var o = document.createElement('option');
      o.value = ''; o.disabled = true; o.selected = true;
      o.textContent = '{js_no_nets}';
      ssidSelect.appendChild(o);
    }} else {{
      var placeholder = document.createElement('option');
      placeholder.value = ''; placeholder.disabled = true; placeholder.selected = true;
      placeholder.textContent = '{js_select}';
      ssidSelect.appendChild(placeholder);

      networks.forEach(function(n) {{
        var o = document.createElement('option');
        o.value = n.ssid;
        o.textContent = n.ssid + '  ' + signalIcon(n.signal) + '  ' + (n.security || '');
        ssidSelect.appendChild(o);
      }});
    }}

    var hidden = document.createElement('option');
    hidden.value = '__manual__';
    hidden.textContent = '\u270e  {js_not_shown}';
    ssidSelect.appendChild(hidden);

    syncHidden();
  }}

  function syncHidden() {{
    if (isManual) {{
      ssidHidden.value = manualInput.value.trim();
    }} else {{
      var v = ssidSelect.value;
      ssidHidden.value = (v === '__manual__' || v === '') ? '' : v;
    }}
    if (typeof checkFormComplete === 'function') checkFormComplete();
  }}

  ssidSelect.addEventListener('change', function() {{
    if (ssidSelect.value === '__manual__') {{
      isManual = true;
      manualWrap.style.display = 'block';
      manualInput.focus();
    }} else {{
      isManual = false;
      manualWrap.style.display = 'none';
    }}
    syncHidden();
  }});

  manualInput.addEventListener('input', syncHidden);

  function doScan() {{
    ssidSelect.innerHTML = '<option disabled selected>{js_scanning}</option>';
    refreshBtn.disabled = true;
    fetch('/scan')
      .then(function(r) {{ return r.json(); }})
      .then(function(data) {{ populateDropdown(data.networks || []); }})
      .catch(function() {{ populateDropdown([]); }})
      .finally(function() {{ refreshBtn.disabled = false; }});
  }}

  refreshBtn.addEventListener('click', doScan);

  doScan();
}})();
</script>
"""


def _is_image_file(filename: str, content: bytes) -> bool:
    if not filename:
        return False
    filename_lower = filename.lower()
    valid_extensions = ['.png', '.jpg', '.jpeg', '.gif', '.bmp', '.tiff', '.webp']
    has_valid_ext = any(filename_lower.endswith(ext) for ext in valid_extensions)
    image_signatures = [
        b'\x89PNG\r\n\x1a\n', b'\xff\xd8\xff', b'GIF87a', b'GIF89a',
        b'BM', b'II*\x00', b'MM\x00*', b'RIFF',
    ]
    has_valid_signature = any(content.startswith(sig) for sig in image_signatures)
    return has_valid_ext or has_valid_signature


def _convert_to_png(image_data: bytes, original_filename: str) -> bytes:
    try:
        from PIL import Image
        img = Image.open(io.BytesIO(image_data))
        if img.mode in ('RGBA', 'LA', 'P'):
            if img.mode == 'P' and 'transparency' in img.info:
                img = img.convert('RGBA')
        elif img.mode != 'RGB':
            img = img.convert('RGB')
        png_buffer = io.BytesIO()
        img.save(png_buffer, format='PNG', optimize=True)
        png_data = png_buffer.getvalue()
        print(f"[DEBUG] Converted {original_filename} ({len(image_data)} bytes) to PNG ({len(png_data)} bytes)")
        return png_data
    except ImportError:
        print("[DEBUG] Pillow not available, saving original image as .png")
        return image_data
    except Exception as e:
        print(f"[DEBUG] Image conversion failed: {e}, saving original")
        return image_data


# ── HTTP Handler ──────────────────────────────────────────────────────

class _PortalHandler(BaseHTTPRequestHandler):
    server_version = "OTPiPortal/1.0"

    def log_message(self, fmt, *args):
        pass  # keep stdout quiet

    def _apply_language(self):
        """Set the global language from user_settings.json so t() works."""
        try:
            settings_file = PROJECT_DIR / "user_settings.json"
            if settings_file.exists():
                with open(settings_file) as f:
                    saved = json.load(f)
                if saved.get("language"):
                    lang.set_language(saved["language"])
                    return
            # Fallback: check wifi_config.txt line 4
            if WIFI_CONFIG.exists():
                lines = WIFI_CONFIG.read_text(encoding="utf-8").splitlines()
                if len(lines) >= 4 and lines[3].strip():
                    lang.set_language(lines[3].strip().lower())
        except Exception:
            pass

    def _write(self, code=200, body="", content_type="text/html; charset=utf-8"):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.end_headers()
        if isinstance(body, str):
            body = body.encode("utf-8", "replace")
        self.wfile.write(body)

    # ── GET ────────────────────────────────────────────────────────────
    def do_GET(self):
        # Set language from saved settings so all t() calls use it
        self._apply_language()

        # Notify progress tracker
        try:
            if portal_handler:
                portal_handler.on_webpage_access()
                print("[DEBUG] Webpage accessed — notified instruction manager")
        except Exception as e:
            print(f"[DEBUG] Failed to notify webpage access: {e}")

        if self.path == "/favicon.ico":
            self._write(404, b"")
            return

        # ── /scan endpoint — returns JSON list of visible SSIDs ──
        if self.path == "/scan":
            try:
                nets = scan_wifi_networks()
                payload = json.dumps({"networks": nets})
            except Exception as e:
                print(f"[DEBUG] /scan error: {e}")
                payload = json.dumps({"networks": []})
            self._write(200, payload, content_type="application/json")
            return

        # ── Load stored values to pre-populate form ──
        ssid_val = ""
        country_val = "US"
        language_val = "en"
        if self.server.need_wifi:
            try:
                if WIFI_CONFIG.exists():
                    lines = WIFI_CONFIG.read_text(encoding="utf-8").splitlines()
                    if lines:
                        ssid_val = lines[0]
                    if len(lines) >= 3 and lines[2].strip():
                        country_val = lines[2].strip().upper()
                    if len(lines) >= 4 and lines[3].strip():
                        language_val = lines[3].strip().lower()
            except Exception:
                pass
            # Also check user_settings.json (set by OLED language picker)
            try:
                settings_file = PROJECT_DIR / "user_settings.json"
                if settings_file.exists():
                    import json as _json
                    with open(settings_file) as _f:
                        saved = _json.load(_f)
                    if saved.get("language"):
                        language_val = saved["language"]
            except Exception:
                pass

        # ── Build page ──
        if not (self.server.need_wifi or self.server.need_qr):
            body = f"<h1>{t('web_title')}</h1><p>{t('web_nothing')}</p>"
            self._write(200, _html_page(body))
            return

        # Translated JS strings for inline form validation
        js_saving     = t('web_status_saving').replace("'", "\\'")
        js_processing = t('web_status_processing').replace("'", "\\'")
        js_ready      = t('web_status_ready').replace("'", "\\'")
        js_ready_s    = t('web_status_ready_short').replace("'", "\\'")
        js_missing    = t('web_missing_prefix').replace("'", "\\'")
        js_f_ssid     = t('web_field_ssid').replace("'", "\\'")
        js_f_pwd      = t('web_field_password').replace("'", "\\'")
        js_f_qr       = t('web_field_qr').replace("'", "\\'")

        if self.server.need_wifi and self.server.need_qr:
            msg = f"<p>{t('web_msg_both')}</p>"
            setup_form = f"""
            <form method="POST" enctype="multipart/form-data" id="setupForm">
              {_wifi_form_block(ssid_val, country_val, language_val)}

              <h2>{t('web_qr_title')}</h2>
              <label>{t('web_qr_label')}</label>
              <input name="qr" type="file" accept="image/*" required id="qrInput">
              <p class="hint">{t('web_qr_hint')}</p>

              <button type="submit" id="saveButton" disabled>{t('web_btn_setup')}</button>
              <p class="hint" id="statusText">{t('web_status_fill')}</p>
            </form>
            {_wifi_js()}
            <script>
            function checkFormComplete() {{
                var ssid     = document.getElementById('ssidHidden').value.trim();
                var password = document.getElementById('passwordInput').value.trim();
                var qr       = document.getElementById('qrInput').files.length > 0;
                var btn      = document.getElementById('saveButton');
                var status   = document.getElementById('statusText');

                var ok = ssid && password && qr;
                btn.disabled = !ok;

                if (ok) {{
                    status.textContent = '{js_ready}';
                    status.className = "hint ok";
                }} else {{
                    var m = [];
                    if (!ssid)     m.push('{js_f_ssid}');
                    if (!password) m.push('{js_f_pwd}');
                    if (!qr)       m.push('{js_f_qr}');
                    status.textContent = '{js_missing}: ' + m.join(', ');
                    status.className = "hint";
                }}
            }}
            document.getElementById('passwordInput').addEventListener('input', checkFormComplete);
            document.getElementById('qrInput').addEventListener('change', checkFormComplete);
            document.getElementById('setupForm').addEventListener('submit', function(e) {{
                var btn = document.getElementById('saveButton');
                if (btn.disabled) {{ e.preventDefault(); return false; }}
                btn.disabled = true;
                btn.textContent = '{js_saving}';
                document.getElementById('statusText').textContent = '{js_processing}';
            }});
            </script>"""

        elif self.server.need_wifi:
            msg = f"<p>{t('web_msg_wifi')}</p>"
            setup_form = f"""
            <form method="POST" enctype="application/x-www-form-urlencoded" id="setupForm">
              {_wifi_form_block(ssid_val, country_val, language_val)}
              <button type="submit" name="action" value="save_wifi" id="saveButton" disabled>{t('web_btn_wifi')}</button>
              <p class="hint" id="statusText">{t('web_status_select')}</p>
            </form>
            {_wifi_js()}
            <script>
            function checkFormComplete() {{
                var ssid     = document.getElementById('ssidHidden').value.trim();
                var password = document.getElementById('passwordInput').value.trim();
                var btn      = document.getElementById('saveButton');
                var status   = document.getElementById('statusText');
                var ok = ssid && password;
                btn.disabled = !ok;
                if (ok) {{
                    status.textContent = '{js_ready_s}';
                    status.className = "hint ok";
                }} else {{
                    var m = [];
                    if (!ssid)     m.push('{js_f_ssid}');
                    if (!password) m.push('{js_f_pwd}');
                    status.textContent = '{js_missing}: ' + m.join(', ');
                    status.className = "hint";
                }}
            }}
            document.getElementById('passwordInput').addEventListener('input', checkFormComplete);
            document.getElementById('setupForm').addEventListener('submit', function(e) {{
                var btn = document.getElementById('saveButton');
                if (btn.disabled) {{ e.preventDefault(); return false; }}
                btn.disabled = true; btn.textContent = '{js_saving}';
            }});
            </script>"""

        else:  # need_qr only
            msg = f"<p>{t('web_msg_qr')}</p>"
            setup_form = f"""
            <form method="POST" enctype="multipart/form-data">
              <h2>{t('web_qr_title')}</h2>
              <label>{t('web_qr_label')}</label>
              <input name="qr" type="file" accept="image/*" required>
              <p class="hint">{t('web_qr_hint')}</p>
              <button type="submit" name="action" value="save_qr">{t('web_btn_qr')}</button>
            </form>"""

        body = f"<h1>{t('web_title')}</h1>\n{msg}\n{setup_form}"
        self._write(200, _html_page(body))

    # ── POST ───────────────────────────────────────────────────────────
    def do_POST(self):
        self._apply_language()
        ctype = self.headers.get("Content-Type", "")
        saved_wifi = False
        saved_qr   = False
        err_msg    = None

        try:
            if ctype.startswith("multipart/form-data"):
                form = cgi.FieldStorage(
                    fp=self.rfile, headers=self.headers,
                    environ={"REQUEST_METHOD": "POST", "CONTENT_TYPE": ctype},
                )

                # ── QR upload ──
                fitem = form["qr"] if "qr" in form else None
                if fitem is not None and getattr(fitem, "file", None):
                    data = fitem.file.read()
                    filename = getattr(fitem, "filename", "unknown")
                    if not data:
                        err_msg = t("web_error_empty")
                    elif not _is_image_file(filename, data):
                        err_msg = f"'{filename}' {t('web_error_not_image')}"
                    else:
                        png_data = _convert_to_png(data, filename)
                        SECRETS_DIR.mkdir(parents=True, exist_ok=True)
                        SECRET_QR_PNG.write_bytes(png_data)
                        print(f"[DEBUG] Saved QR image '{filename}' to {SECRET_QR_PNG}")
                        saved_qr = True

                # ── Wi-Fi credentials ──
                ssid    = (form.getfirst("ssid") or "").strip()
                pwd     = (form.getfirst("password") or "").strip()
                country = (form.getfirst("country") or "US").strip().upper()
                language = (form.getfirst("language") or "en").strip().lower()
                if ssid and pwd:
                    WIFI_CONFIG.write_text(
                        f"{ssid}\n{pwd}\n{country}\n{language}\n", encoding="utf-8",
                    )
                    print(f"[DEBUG] Saved Wi-Fi to {WIFI_CONFIG} (country={country}, lang={language})")
                    saved_wifi = True

            else:
                # x-www-form-urlencoded
                length = int(self.headers.get("Content-Length", "0") or "0")
                raw = self.rfile.read(length).decode("utf-8", "replace")
                params = parse_qs(raw, keep_blank_values=True)

                ssid    = (params.get("ssid", [""])[0] or "").strip()
                pwd     = (params.get("password", [""])[0] or "").strip()
                country = (params.get("country", ["US"])[0] or "US").strip().upper()
                language = (params.get("language", ["en"])[0] or "en").strip().lower()
                if ssid and pwd:
                    WIFI_CONFIG.write_text(
                        f"{ssid}\n{pwd}\n{country}\n{language}\n", encoding="utf-8",
                    )
                    print(f"[DEBUG] Saved Wi-Fi to {WIFI_CONFIG} (country={country}, lang={language})")
                    saved_wifi = True

        except Exception as e:
            err_msg = f"Error processing form: {e}"
            print(f"[DEBUG] Form processing error: {e}")

        # ── Check completeness ──
        got_what_we_need = True
        if self.server.need_wifi and not saved_wifi:
            got_what_we_need = False
        if self.server.need_qr and not saved_qr:
            got_what_we_need = False

        if not got_what_we_need:
            missing = []
            if self.server.need_wifi and not saved_wifi:
                missing.append(t("web_error_missing_wifi"))
            if self.server.need_qr and not saved_qr:
                missing.append(t("web_error_missing_qr"))
            error_detail = err_msg or f"{t('web_missing_prefix')}: {', '.join(missing)}"
            msg = f"<p class='err'>{t('web_error_incomplete')} {error_detail}</p><p><a href='/'>{t('web_error_back')}</a></p>"
            self._write(200, _html_page(msg))
            return

        # ── Success ──
        try:
            if portal_handler:
                portal_handler.on_form_submit()
                print("[DEBUG] Form submitted — notified instruction manager")
        except Exception as e:
            print(f"[DEBUG] Failed to notify form submission: {e}")

        done_bits = []
        if saved_wifi: done_bits.append("Wi-Fi")
        if saved_qr:   done_bits.append("QR Code")

        body = f"""
        <h1>{t('web_success_title')}</h1>
        <p class="ok">{t('web_success_saved')}: {', '.join(done_bits)}</p>
        <p>{t('web_success_disconnect')}</p>
        <p>{t('web_success_restart')}</p>
        <p>{t('web_success_totp')}</p>
        """
        self._write(200, _html_page(body))

        def _shutdown_later(srv: HTTPServer):
            try:
                import time; time.sleep(0.1)
                srv.shutdown()
            except Exception:
                pass
        threading.Thread(target=_shutdown_later, args=(self.server,), daemon=True).start()


class _PortalServer(HTTPServer):
    def __init__(self, addr, Handler, need_wifi=True, need_qr=True):
        super().__init__(addr, Handler)
        self.need_wifi = bool(need_wifi)
        self.need_qr   = bool(need_qr)


def run_captive_portal(need_wifi: bool = True, need_qr: bool = True,
                       host: str = "0.0.0.0", port: int = None):
    global portal_handler
    if portal_handler is None:
        portal_handler = PortalInstructionHandler()
        print("[DEBUG] Portal handler initialized in run_captive_portal")

    if port is None:
        try:
            port = int(os.environ.get("OTPI_PORTAL_PORT", "80"))
        except Exception:
            port = 80
    SECRETS_DIR.mkdir(parents=True, exist_ok=True)

    srv = _PortalServer((host, port), _PortalHandler, need_wifi=need_wifi, need_qr=need_qr)
    print(f"[DEBUG] Captive portal listening on http://{host}:{port} (need_wifi={need_wifi}, need_qr={need_qr})")

    try:
        srv.serve_forever()
    except Exception as e:
        print(f"[DEBUG] Portal server error: {e}")
    finally:
        try:
            srv.server_close()
        except Exception:
            pass
    print("[DEBUG] Captive portal stopped")


# Back-compat aliases
def serve_upload(*args, **kwargs):
    return run_captive_portal(*args, **kwargs)

def start_portal(*args, **kwargs):
    return run_captive_portal(*args, **kwargs)
