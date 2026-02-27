#!/usr/bin/env python3
#utils.py

import subprocess, time, os
from typing import Sequence, Union, List
from pathlib import Path
import os, sys, time


PROJECT_DIR = Path(__file__).resolve().parent

def restart_program():
    """Hard restart this Python process into main.py."""
    py = sys.executable
    main_py = str(PROJECT_DIR / "main.py")
    try:
        print("[DEBUG] Restarting process into main.py …")
        sys.stdout.flush(); sys.stderr.flush()
    except Exception:
        pass
    try:
        os.execv(py, [py, main_py])
    except Exception as e:
        # If execv fails (shouldn't), exit non-zero so a service can restart us.
        print(f"[DEBUG] execv failed: {e}")
        os._exit(1)

def debug_print(msg: str) -> None:
    print(f"[DEBUG] {msg}", flush=True)

def sh(cmd: Union[str, Sequence[str]], check: bool = False, **popen_kwargs) -> subprocess.CompletedProcess:
    if isinstance(cmd, str):
        cp = subprocess.run(cmd, shell=True, text=True, capture_output=True, **popen_kwargs)
    else:
        cp = subprocess.run(cmd, text=True, capture_output=True, **popen_kwargs)
    if check and cp.returncode != 0:
        raise RuntimeError(f"Command failed ({cp.returncode}): {cmd}\nSTDOUT:\n{cp.stdout}\nSTDERR:\n{cp.stderr}")
    return cp

def _systemctl(args: Sequence[str]) -> subprocess.CompletedProcess:
    return subprocess.run(["systemctl", *args], text=True, capture_output=True)

def service_exists(unit: str) -> bool:
    cp = _systemctl(["status", unit]); return cp.returncode in (0, 3)

def detect_wpa_units() -> List[str]:
    candidates = ["wpa_supplicant@wlan0", "wpa_supplicant"]
    active = [u for u in candidates if _systemctl(["is-active", u]).stdout.strip() in ("active", "activating")]
    if active: return active
    enabled = [u for u in candidates if _systemctl(["is-enabled", u]).stdout.strip() in ("enabled", "static", "generated", "indirect")]
    return enabled or candidates

def _wifi_ready_check(iface: str = "wlan0") -> bool:
    ssid = sh(["iwgetid", "-r"]).stdout.strip()
    ip4 = sh(["sh", "-c", f"ip -4 addr show {iface} | grep -q 'inet ' && echo OK || true"]).stdout.strip()
    return bool(ssid) and ip4 == "OK"

def _set_wifi_country(country: str = "US") -> None:
    """Apply the WiFi regulatory domain country code system-wide."""
    country = (country or "US").strip().upper()[:2]
    debug_print(f"Setting WiFi regulatory domain to: {country}")
    # Method 1: iw reg set (immediate)
    sh(["iw", "reg", "set", country])
    # Method 2: raspi-config nonint (persists across reboots on Raspberry Pi OS)
    sh(["raspi-config", "nonint", "do_wifi_country", country])

def _write_wpa_supplicant_conf(ssid: str, password: str, country: str = "US") -> None:
    debug_print("Writing /etc/wpa_supplicant/wpa_supplicant.conf …")
    cp = sh(["wpa_passphrase", ssid, password], check=True)
    body = cp.stdout
    country = (country or "US").strip().upper()[:2]
    content = f"""country={country}
ctrl_interface=DIR=/var/run/wpa_supplicant GROUP=netdev
update_config=1

{body.strip()}
"""
    tmp = "/etc/wpa_supplicant/wpa_supplicant.conf.tmp"
    with open(tmp, "w") as f: f.write(content)
    os.replace(tmp, "/etc/wpa_supplicant/wpa_supplicant.conf")
    try: os.chmod("/etc/wpa_supplicant/wpa_supplicant.conf", 0o600)
    except Exception: pass

def connect_wifi(ssid: str, password: str, iface: str = "wlan0",
                 timeout: int = 45, country: str = "US") -> bool:
    """
    Connect to Wi-Fi robustly.
    - Applies the regulatory-domain country code before connecting.
    - With NetworkManager: start NM, normalize iface, rescan until SSID appears, connect by SSID.
      If the first attempt fails, restart NM once and retry.
    - Without NM: fall back to wpa_supplicant path.
    """
    if not ssid or not password:
        return False

    # Apply country code before any connection attempt
    _set_wifi_country(country)

    # ---------- NetworkManager path ----------
    if service_exists("NetworkManager"):
        import time as _t

        def _nm_active(deadline=10.0):
            start = _t.time()
            while _t.time() - start < deadline:
                if _systemctl(["is-active", "NetworkManager"]).stdout.strip() == "active":
                    return True
                _t.sleep(0.5)
            return False

        def _prep_iface():
            sh(["nmcli", "dev", "set", iface, "managed", "yes"])
            sh(["nmcli", "radio", "wifi", "on"])
            sh(["rfkill", "unblock", "wifi"])
            sh(["iw", "dev", "p2p-dev-" + iface, "del"])  # ignore if missing
            sh(["iw", "dev", iface, "set", "type", "managed"])
            sh(["ip", "link", "set", iface, "down"])
            sh(["ip", "addr", "flush", "dev", iface])
            sh(["ip", "link", "set", iface, "up"])

        def _nm_scan_until_visible(target_ssid: str, deadline=30.0) -> bool:
            start = _t.time()
            while _t.time() - start < deadline:
                sh(["nmcli", "-w", "10", "dev", "wifi", "rescan"])
                out = sh(["nmcli", "-t", "-f", "SSID", "dev", "wifi"]).stdout
                ssids = [ln.strip() for ln in out.splitlines() if ln.strip()]
                if target_ssid in ssids:
                    return True
                _t.sleep(2.0)
            return False

        def _try_once(wait=timeout) -> bool:
            _prep_iface()
            if not _nm_scan_until_visible(ssid, deadline=35.0):
                debug_print(f"(NM) SSID '{ssid}' not visible after rescans.")
                return False
            cp = sh(["nmcli", "-w", "40", "dev", "wifi", "connect", ssid,
                     "password", password, "ifname", iface])
            if cp.returncode != 0:
                debug_print(f"(NM) nmcli connect error: {cp.stderr.strip() or cp.stdout.strip()}")
                return False
            # Wait for IP
            start = _t.time()
            while _t.time() - start < wait:
                if _wifi_ready_check(iface):
                    ip = sh(["hostname", "-I"]).stdout.strip()
                    debug_print(f"(NM) Connected. IP(s): {ip}")
                    return True
                _t.sleep(1.0)
            debug_print("(NM) Associated but no IP (timeout).")
            return False

        debug_print(f"(NM) Connecting to SSID: {ssid} (country={country})")
        sh(["systemctl", "enable", "--now", "NetworkManager"])
        _nm_active(10.0)

        if _try_once():
            return True

        debug_print("(NM) First attempt failed; restarting NM then retrying…")
        sh(["systemctl", "restart", "NetworkManager"])
        _t.sleep(3.0)
        _nm_active(10.0)
        return _try_once()

    # ---------- Fallback (no NetworkManager) ----------
    debug_print(f"Connecting to SSID: {ssid} (country={country})")
    try:
        sh(["rfkill", "unblock", "wifi"])
        sh(["ip", "link", "set", iface, "down"])
        sh(["ip", "addr", "flush", "dev", iface])
        sh(["ip", "link", "set", iface, "up"])
        _write_wpa_supplicant_conf(ssid, password, country)
        debug_print("Restarting networking via wpa_supplicant path")
        if service_exists("dhcpcd"):
            sh(["systemctl", "restart", "dhcpcd"])
        for unit in detect_wpa_units():
            sh(["systemctl", "restart", unit])
        start = time.time()
        while time.time() - start < timeout:
            if _wifi_ready_check(iface):
                ip = sh(["hostname", "-I"]).stdout.strip()
                debug_print(f"Connected. IP(s): {ip}")
                return True
            time.sleep(1.0)
        debug_print("Wi-Fi connect timeout (wpa_supplicant path).")
        return False
    except Exception as e:
        debug_print(f"connect_wifi error: {e}")
        return False

def get_ntp_time(server: str = "pool.ntp.org", timeout: int = 10) -> None:
    """Synchronize time via NTP with timeout and better error handling"""
    debug_print("Synchronizing time via NTP...")
    
    try:
        # Method 1: Try ntpdate with timeout
        if sh(["which", "ntpdate"]).returncode == 0:
            debug_print(f"Using ntpdate with server {server}")
            result = sh(["timeout", str(timeout), "ntpdate", "-u", server])
            if result.returncode == 0:
                debug_print("NTP sync successful via ntpdate")
                return
            else:
                debug_print(f"ntpdate failed (exit {result.returncode}): {result.stderr}")
        
        # Method 2: Try systemd-timesyncd
        debug_print("Trying systemd-timesyncd...")
        sh(["timedatectl", "set-ntp", "true"])
        
        # Wait briefly for sync, but don't block forever
        for i in range(5):  # Wait max 5 seconds
            result = sh(["timedatectl", "show", "--property=NTPSynchronized"])
            if "NTPSynchronized=yes" in result.stdout:
                debug_print("NTP sync successful via systemd-timesyncd")
                return
            time.sleep(1)
        
        debug_print("NTP sync attempt completed (may not be synchronized)")
        
    except Exception as e:
        debug_print(f"NTP sync error: {e}")
    
    debug_print("Continuing without NTP sync")


def get_wifi_status(iface: str = "wlan0") -> dict:
    """
    Quick, non-blocking WiFi status check.
    Returns {"connected": bool, "ssid": str, "ip": str}.
    """
    status = {"connected": False, "ssid": "", "ip": ""}
    try:
        ssid = sh(["iwgetid", "-r"]).stdout.strip()
        if ssid:
            status["ssid"] = ssid
            ip_out = sh(["hostname", "-I"]).stdout.strip()
            if ip_out:
                status["ip"] = ip_out.split()[0]
                status["connected"] = True
    except Exception:
        pass
    return status


def reconnect_wifi(iface: str = "wlan0", timeout: int = 30) -> bool:
    """
    Try to bring WiFi back up using the saved config.
    Much lighter than the full connect_wifi() — just nudges NM or wpa_supplicant.
    """
    debug_print("Attempting WiFi reconnect...")
    try:
        if service_exists("NetworkManager"):
            # Nudge NM to reconnect the last-known connection
            sh(["nmcli", "dev", "set", iface, "managed", "yes"])
            sh(["nmcli", "radio", "wifi", "on"])
            sh(["nmcli", "dev", "connect", iface])
        else:
            sh(["ip", "link", "set", iface, "up"])
            for unit in detect_wpa_units():
                sh(["systemctl", "restart", unit])
            if service_exists("dhcpcd"):
                sh(["systemctl", "restart", "dhcpcd"])

        # Wait for connectivity
        import time as _t
        start = _t.time()
        while _t.time() - start < timeout:
            if _wifi_ready_check(iface):
                debug_print("WiFi reconnected successfully")
                return True
            _t.sleep(2.0)
    except Exception as e:
        debug_print(f"WiFi reconnect error: {e}")
    debug_print("WiFi reconnect failed")
    return False
