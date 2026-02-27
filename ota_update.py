#!/usr/bin/env python3
"""
ota_update.py – Over-the-Air updater for OTPi devices.

Checks a remote server (GitHub by default) for a newer firmware version,
downloads the update bundle, backs up the current installation, applies the
update, and restarts the service.

Usage:
    python3 ota_update.py              # check + update if newer
    python3 ota_update.py --check      # just check, don't apply
    python3 ota_update.py --force      # update even if same version
    python3 ota_update.py --rollback   # revert to previous backup

Configuration:
    Edit OTA_CONFIG below, or place ota_config.json next to this script.

Remote layout expected (GitHub release or any HTTP server):
    <base_url>/version.txt       – single line, e.g. "1.2.0"
    <base_url>/otpi_update.tar.gz – tar.gz of project .py files
"""

from __future__ import annotations
import os, sys, json, shutil, tarfile, hashlib, subprocess, time
from pathlib import Path
from datetime import datetime
from typing import Optional

PROJECT_DIR  = Path(__file__).resolve().parent
VERSION_FILE = PROJECT_DIR / "version.txt"
BACKUP_DIR   = PROJECT_DIR / "backups"
OTA_LOG_FILE = PROJECT_DIR / "ota_update.log"
CONFIG_FILE  = PROJECT_DIR / "ota_config.json"

# ── Default configuration ──────────────────────────────────────────
# Override by creating ota_config.json next to this script.
OTA_CONFIG = {
    # GitHub raw URL — change <OWNER>/<REPO>/<BRANCH> to your repo
    "version_url": "https://raw.githubusercontent.com/mylesdedwards/OTPi/master/version.txt",
    "bundle_url":  "https://raw.githubusercontent.com/mylesdedwards/OTPi/master/otpi_update.tar.gz",

    # Alternatively, use GitHub Releases (uncomment and edit):
    # "version_url": "https://github.com/OWNER/REPO/releases/latest/download/version.txt",
    # "bundle_url":  "https://github.com/OWNER/REPO/releases/latest/download/otpi_update.tar.gz",

    # Or any HTTP server:
    # "version_url": "https://updates.example.com/otpi/version.txt",
    # "bundle_url":  "https://updates.example.com/otpi/otpi_update.tar.gz",

    # Service name to restart after update (set to "" to skip restart)
    "service_name": "otpi.service",

    # How many backups to keep
    "max_backups": 3,

    # Files to NEVER overwrite (user data / secrets)
    "protected_files": [
        "wifi_config.txt",
        "user_settings.json",
        "ota_config.json",
        "version.txt",
        "secrets/otp_secret.txt",
        "secrets/otp_qr.png",
    ],

    # Enable/disable OTA updates entirely
    "enabled": True,
}


def _log(msg: str):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    try:
        with open(OTA_LOG_FILE, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass


def _load_config() -> dict:
    """Load OTA config from file, falling back to defaults."""
    config = dict(OTA_CONFIG)
    try:
        if CONFIG_FILE.exists():
            with open(CONFIG_FILE) as f:
                user_cfg = json.load(f)
            config.update(user_cfg)
            _log(f"Loaded config from {CONFIG_FILE}")
    except Exception as e:
        _log(f"Config load error: {e}, using defaults")
    return config


def _get_local_version() -> str:
    """Read local version string."""
    try:
        return VERSION_FILE.read_text(encoding="utf-8").strip()
    except Exception:
        return "0.0.0"


def _set_local_version(version: str):
    """Write local version string."""
    VERSION_FILE.write_text(version.strip() + "\n", encoding="utf-8")


def _fetch_url(url: str, timeout: int = 30) -> Optional[bytes]:
    """Download URL contents. Tries urllib (stdlib), falls back to curl."""
    # Method 1: urllib (no external deps)
    try:
        from urllib.request import urlopen, Request
        req = Request(url, headers={"User-Agent": "OTPi-Updater/1.0"})
        with urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except Exception as e:
        _log(f"urllib fetch failed: {e}")

    # Method 2: curl fallback
    try:
        result = subprocess.run(
            ["curl", "-fsSL", "--max-time", str(timeout), url],
            capture_output=True, timeout=timeout + 5,
        )
        if result.returncode == 0:
            return result.stdout
        _log(f"curl failed: {result.stderr.decode(errors='replace').strip()}")
    except Exception as e:
        _log(f"curl error: {e}")

    return None


def _get_remote_version(config: dict) -> Optional[str]:
    """Fetch remote version string."""
    data = _fetch_url(config["version_url"])
    if data:
        ver = data.decode("utf-8", errors="replace").strip().splitlines()[0].strip()
        return ver
    return None


def _download_bundle(config: dict) -> Optional[Path]:
    """Download the update bundle to a temp file."""
    _log(f"Downloading update bundle from {config['bundle_url']}")
    data = _fetch_url(config["bundle_url"], timeout=120)
    if not data:
        _log("Bundle download failed")
        return None

    tmp = PROJECT_DIR / ".ota_update.tar.gz"
    tmp.write_bytes(data)
    _log(f"Downloaded {len(data)} bytes -> {tmp}")
    return tmp


def _create_backup(config: dict) -> Optional[Path]:
    """Backup current .py files to a timestamped directory."""
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    local_ver = _get_local_version()
    backup_path = BACKUP_DIR / f"v{local_ver}_{ts}"
    backup_path.mkdir(parents=True, exist_ok=True)

    count = 0
    for f in PROJECT_DIR.glob("*.py"):
        shutil.copy2(f, backup_path / f.name)
        count += 1

    # Also backup version.txt
    if VERSION_FILE.exists():
        shutil.copy2(VERSION_FILE, backup_path / "version.txt")

    _log(f"Backed up {count} files to {backup_path}")

    # Prune old backups
    _prune_backups(config.get("max_backups", 3))

    return backup_path


def _prune_backups(max_keep: int):
    """Remove oldest backups beyond max_keep."""
    if not BACKUP_DIR.exists():
        return
    backups = sorted(BACKUP_DIR.iterdir(), key=lambda p: p.name)
    while len(backups) > max_keep:
        old = backups.pop(0)
        if old.is_dir():
            shutil.rmtree(old, ignore_errors=True)
            _log(f"Pruned old backup: {old.name}")


def _apply_update(bundle_path: Path, config: dict) -> bool:
    """Extract the update bundle, skipping protected files."""
    protected = set(config.get("protected_files", []))

    try:
        with tarfile.open(bundle_path, "r:gz") as tar:
            members = tar.getmembers()
            applied = 0

            for member in members:
                # Security: skip absolute paths or path traversal
                if member.name.startswith("/") or ".." in member.name:
                    _log(f"  SKIP (unsafe path): {member.name}")
                    continue

                # Skip directories
                if member.isdir():
                    continue

                # Skip protected files
                if member.name in protected:
                    _log(f"  SKIP (protected): {member.name}")
                    continue

                # Extract to project dir
                target = PROJECT_DIR / member.name
                target.parent.mkdir(parents=True, exist_ok=True)

                with tar.extractfile(member) as src:
                    if src:
                        target.write_bytes(src.read())
                        applied += 1
                        _log(f"  Updated: {member.name}")

        _log(f"Applied {applied} files from update bundle")
        return True

    except Exception as e:
        _log(f"Update extraction failed: {e}")
        return False


def _restart_service(config: dict):
    """Restart the OTPi systemd service."""
    svc = config.get("service_name", "")
    if not svc:
        _log("No service configured, skipping restart")
        return

    _log(f"Restarting {svc}...")
    try:
        result = subprocess.run(
            ["systemctl", "restart", svc],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            _log(f"{svc} restarted successfully")
        else:
            _log(f"Restart failed: {result.stderr.strip()}")
    except Exception as e:
        _log(f"Restart error: {e}")


def rollback() -> bool:
    """Roll back to the most recent backup."""
    if not BACKUP_DIR.exists():
        _log("No backups directory found")
        return False

    backups = sorted(BACKUP_DIR.iterdir(), key=lambda p: p.name)
    if not backups:
        _log("No backups available")
        return False

    latest = backups[-1]
    _log(f"Rolling back to: {latest.name}")

    count = 0
    for f in latest.glob("*.py"):
        shutil.copy2(f, PROJECT_DIR / f.name)
        count += 1

    ver_file = latest / "version.txt"
    if ver_file.exists():
        shutil.copy2(ver_file, VERSION_FILE)

    _log(f"Rolled back {count} files from {latest.name}")
    return True


def check_for_update(config: dict = None) -> dict:
    """
    Check if an update is available.
    Returns {"available": bool, "local": str, "remote": str}.
    """
    if config is None:
        config = _load_config()

    local_ver = _get_local_version()
    remote_ver = _get_remote_version(config)

    if remote_ver is None:
        _log("Could not reach update server")
        return {"available": False, "local": local_ver, "remote": None, "error": "unreachable"}

    available = remote_ver != local_ver
    _log(f"Version check: local={local_ver}, remote={remote_ver}, update={'YES' if available else 'no'}")

    return {"available": available, "local": local_ver, "remote": remote_ver}


def do_update(force: bool = False) -> bool:
    """
    Full update flow: check -> backup -> download -> apply -> restart.
    Returns True if update was applied successfully.
    """
    config = _load_config()

    if not config.get("enabled", True):
        _log("OTA updates are disabled in config")
        return False

    # Check version
    status = check_for_update(config)
    if status.get("error"):
        return False

    if not status["available"] and not force:
        _log("Already up to date")
        return False

    remote_ver = status["remote"]
    _log(f"Starting update to v{remote_ver}...")

    # Backup current installation
    backup_path = _create_backup(config)
    if not backup_path:
        _log("Backup failed, aborting update")
        return False

    # Download bundle
    bundle = _download_bundle(config)
    if not bundle:
        _log("Download failed, aborting update")
        return False

    # Apply update
    if not _apply_update(bundle, config):
        _log("Apply failed, rolling back...")
        rollback()
        return False

    # Update local version
    _set_local_version(remote_ver)
    _log(f"Version updated to {remote_ver}")

    # Clean up temp file
    try:
        bundle.unlink()
    except Exception:
        pass

    # Restart service
    _restart_service(config)

    _log("Update complete!")
    return True


# ── CLI entry point ────────────────────────────────────────────────
if __name__ == "__main__":
    args = sys.argv[1:]

    if "--help" in args or "-h" in args:
        print(__doc__)
        sys.exit(0)

    if "--rollback" in args:
        config = _load_config()
        if rollback():
            _restart_service(config)
            print("Rollback complete")
        else:
            print("Rollback failed")
        sys.exit(0)

    if "--check" in args:
        status = check_for_update()
        if status.get("error"):
            print(f"Error: {status['error']}")
            sys.exit(1)
        print(f"Local:  {status['local']}")
        print(f"Remote: {status['remote']}")
        print(f"Update: {'available' if status['available'] else 'up to date'}")
        sys.exit(0)

    force = "--force" in args
    success = do_update(force=force)
    sys.exit(0 if success else 1)
