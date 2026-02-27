#!/usr/bin/env python3
#start-ap_mode.py
import subprocess, time, os, re, tempfile
from pathlib import Path
from typing import Sequence, Union, List
from utils import debug_print

DEFAULT_IFACE = "wlan0"
DEFAULT_AP_CIDR = "192.168.4.1/24"
DEFAULT_HOSTAPD_CONF = "/etc/hostapd/hostapd.conf"
DEFAULT_DNSMASQ_CONF = "/etc/dnsmasq.conf"

# Runtime hostapd conf with unique SSID (written each boot)
_RUNTIME_HOSTAPD_CONF = "/tmp/otpi_hostapd.conf"

# File that stores the active AP SSID so other code (OLED, web) can read it
PROJECT_DIR = Path(__file__).resolve().parent
AP_SSID_FILE = PROJECT_DIR / ".ap_ssid"


def get_board_id() -> str:
    """
    Return a short (4-char) hex string unique to this Pi board.
    Tries: /proc/cpuinfo Serial, then wlan0 MAC, then hostname hash.
    """
    # Method 1: Pi CPU serial (most reliable on Raspberry Pi)
    try:
        with open("/proc/cpuinfo") as f:
            for line in f:
                if line.strip().startswith("Serial"):
                    serial = line.split(":")[-1].strip()
                    if serial and serial != "0" * len(serial):
                        suffix = serial[-4:].upper()
                        debug_print(f"Board ID from CPU serial: {suffix}")
                        return suffix
    except Exception:
        pass

    # Method 2: wlan0 MAC address last 4 hex chars
    try:
        mac_path = f"/sys/class/net/{DEFAULT_IFACE}/address"
        mac = open(mac_path).read().strip().replace(":", "")
        if mac and len(mac) >= 4:
            suffix = mac[-4:].upper()
            debug_print(f"Board ID from MAC: {suffix}")
            return suffix
    except Exception:
        pass

    # Method 3: hostname hash fallback
    try:
        import hashlib
        hostname = subprocess.check_output(["hostname"], text=True).strip()
        h = hashlib.md5(hostname.encode()).hexdigest()[:4].upper()
        debug_print(f"Board ID from hostname hash: {h}")
        return h
    except Exception:
        pass

    debug_print("Board ID: using fallback 0000")
    return "0000"


def get_unique_ssid(base_ssid: str = "OTPi-Setup") -> str:
    """Return a unique SSID like 'OTPi-Setup-A1B2'."""
    return f"{base_ssid}-{get_board_id()}"


def _make_runtime_hostapd_conf(template_conf: str) -> str:
    """
    Read the template hostapd.conf, replace the ssid= line with a unique
    SSID, write to a temp file, and return the path.
    Also stores the SSID in AP_SSID_FILE for other code to read.
    """
    try:
        with open(template_conf) as f:
            content = f.read()
    except Exception as e:
        debug_print(f"Cannot read {template_conf}: {e}")
        return template_conf  # fall back to original

    # Extract the base SSID from the template (default: OTPi-Setup)
    match = re.search(r'^ssid=(.+)$', content, re.MULTILINE)
    base_ssid = match.group(1).strip() if match else "OTPi-Setup"

    unique_ssid = get_unique_ssid(base_ssid)

    # Replace the ssid= line
    new_content = re.sub(r'^ssid=.+$', f'ssid={unique_ssid}', content, flags=re.MULTILINE)

    # Write runtime config
    with open(_RUNTIME_HOSTAPD_CONF, "w") as f:
        f.write(new_content)

    # Write the active SSID so OLED / web portal can read it
    try:
        AP_SSID_FILE.write_text(unique_ssid, encoding="utf-8")
    except Exception:
        pass

    debug_print(f"Unique AP SSID: {unique_ssid} (written to {_RUNTIME_HOSTAPD_CONF})")
    return _RUNTIME_HOSTAPD_CONF

def sh(cmd: Union[str, Sequence[str]], ignore_error: bool = True) -> int:
    if isinstance(cmd, str): result = subprocess.run(cmd, shell=True)
    else: result = subprocess.run(cmd)
    if result.returncode != 0 and not ignore_error:
        raise RuntimeError(f"Command failed ({result.returncode}): {cmd}")
    return result.returncode

def _systemctl(args: Sequence[str]) -> subprocess.CompletedProcess:
    return subprocess.run(["systemctl", *args], text=True, capture_output=True)

def service_exists(unit: str) -> bool:
    cp = _systemctl(["status", unit]); return cp.returncode in (0, 3)

def service_is_active(unit: str) -> bool:
    cp = _systemctl(["is-active", unit])
    return cp.returncode == 0 and cp.stdout.strip() in ("active", "activating")

def detect_wpa_units() -> List[str]:
    candidates = ["wpa_supplicant@wlan0", "wpa_supplicant"]
    active = [u for u in candidates if service_is_active(u)]
    if active: return active
    enabled = []
    for u in candidates:
        cp = _systemctl(["is-enabled", u])
        if cp.returncode == 0 and cp.stdout.strip() in ("enabled", "static", "generated", "indirect"):
            enabled.append(u)
    return enabled or candidates

def nm_set_managed_wlan0(managed: bool):
    if service_exists("NetworkManager"):
        sh(["nmcli", "dev", "set", "wlan0", "managed", "yes" if managed else "no"])

def stop_station_services():
    nm_set_managed_wlan0(False)
    units = detect_wpa_units()
    debug_print(f"Stopping station services ({', '.join(units)}) …")
    for unit in units: sh(["systemctl", "stop", unit])
    if service_exists("dhcpcd"): sh(["systemctl", "stop", "dhcpcd"])

def start_station_services():
    # Give wlan0 back to NM and clean iface
    nm_set_managed_wlan0(True)

    # Make sure the interface is back to 'managed' type after hostapd
    sh(["iw", "dev", DEFAULT_IFACE, "set", "type", "managed"])
    sh(["ip", "addr", "flush", "dev", DEFAULT_IFACE])
    sh(["ip", "link", "set", DEFAULT_IFACE, "up"])

    if service_exists("NetworkManager"):
        # IMPORTANT: don't run system wpa_supplicant units when NM is present
        for unit in detect_wpa_units():
            sh(["systemctl", "stop", unit])

        # Ensure NM is running and will manage wlan0
        sh(["systemctl", "enable", "--now", "NetworkManager"])
        sh(["nmcli", "dev", "set", DEFAULT_IFACE, "managed", "yes"])
        sh(["nmcli", "radio", "wifi", "on"])
    else:
        # Fallback path when NM is not installed
        if service_exists("dhcpcd"):
            sh(["systemctl", "restart", "dhcpcd"])
        for unit in detect_wpa_units():
            sh(["systemctl", "restart", unit])

    debug_print("Re-enabled station services.")

def configure_ap_ip(iface: str, ap_cidr: str):
    debug_print(f"Configuring {iface} with static IP {ap_cidr} …")
    sh(["ip", "link", "set", iface, "down"])
    sh(["ip", "addr", "flush", "dev", iface])
    sh(["rfkill", "unblock", "wifi"])
    sh(["ip", "addr", "add", ap_cidr, "dev", iface], ignore_error=False)
    sh(["ip", "link", "set", iface, "up"], ignore_error=False)

def start_hostapd(hostapd_conf: str):
    debug_print(f"Starting hostapd with {hostapd_conf} …")
    sh(["pkill", "hostapd"]); rc = sh(["hostapd", "-B", hostapd_conf])
    if rc != 0: debug_print("hostapd failed; check config and `journalctl -u hostapd`.")

# Runtime dnsmasq conf with captive-portal DNS redirect
_RUNTIME_DNSMASQ_CONF = "/tmp/otpi_dnsmasq.conf"


def _make_runtime_dnsmasq_conf(template_conf: str, ap_ip: str = "192.168.4.1") -> str:
    """
    Read the template dnsmasq.conf if it exists, then append captive-portal
    DNS redirect rules so phones/tablets auto-detect the portal.
    """
    content = ""
    try:
        with open(template_conf) as f:
            content = f.read()
    except Exception:
        pass

    # Ensure captive portal redirect is present
    if "address=/#/" not in content:
        content += f"""
# ── OTPi captive portal ──
# Redirect ALL DNS queries to the AP IP so phones auto-detect the portal
address=/#/{ap_ip}
"""

    with open(_RUNTIME_DNSMASQ_CONF, "w") as f:
        f.write(content)

    debug_print(f"Captive-portal dnsmasq config written to {_RUNTIME_DNSMASQ_CONF}")
    return _RUNTIME_DNSMASQ_CONF


def start_dnsmasq(dnsmasq_conf: str):
    debug_print(f"Starting dnsmasq with captive portal…")
    sh(["pkill", "dnsmasq"])
    # Generate runtime config with captive portal
    runtime_conf = _make_runtime_dnsmasq_conf(dnsmasq_conf)
    rc = sh(["dnsmasq", f"--conf-file={runtime_conf}"])
    if rc != 0: debug_print("dnsmasq failed; check config and `journalctl -u dnsmasq`.")

def start_ap_mode(iface: str = DEFAULT_IFACE, ap_cidr: str = DEFAULT_AP_CIDR,
                  hostapd_conf: str = DEFAULT_HOSTAPD_CONF, dnsmasq_conf: str = DEFAULT_DNSMASQ_CONF):
    debug_print("=== Enabling Access Point mode ===")

    # Fully stop NetworkManager and wpa_supplicant so nothing else touches wlan0.
    if service_exists("NetworkManager"):
        sh(["systemctl", "stop", "NetworkManager"])
    for unit in detect_wpa_units():
        sh(["systemctl", "stop", unit])

    # Static IP + up for AP
    configure_ap_ip(iface, ap_cidr)

    # Generate runtime hostapd config with unique SSID
    runtime_conf = _make_runtime_hostapd_conf(hostapd_conf)

    # Start AP daemons
    start_hostapd(runtime_conf)
    time.sleep(0.5)
    start_dnsmasq(dnsmasq_conf)

    debug_print("AP active: connect to the SSID shown on the OLED and browse to http://192.168.4.1")

def stop_ap_mode():
    debug_print("=== Disabling Access Point mode ===")
    debug_print("Stopping hostapd and dnsmasq …")
    sh(["pkill", "hostapd"])
    sh(["pkill", "dnsmasq"])

    # Clean up runtime AP config
    for tmp in [_RUNTIME_HOSTAPD_CONF, _RUNTIME_DNSMASQ_CONF, str(AP_SSID_FILE)]:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass

    # Give firmware a real chance to leave AP mode
    time.sleep(5.0)

    # Hand wlan0 back to NetworkManager (client mode, clean IP)
    sh(["iw", "dev", DEFAULT_IFACE, "set", "type", "managed"])
    sh(["ip", "addr", "flush", "dev", DEFAULT_IFACE])
    sh(["ip", "link", "set", DEFAULT_IFACE, "up"])

    if service_exists("NetworkManager"):
        sh(["systemctl", "start", "NetworkManager"])

    debug_print("Re-enabled station services.")
    debug_print("Back to station mode.")
