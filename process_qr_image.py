#!/usr/bin/env python3
# process_qr_image.py
# Decode OTP secret from a QR image. Supports standard "otpauth://"
# and Google "otpauth-migration://" exports (no external script required).

from typing import Optional, List
from pathlib import Path
import subprocess, base64, urllib.parse as up

# Optional Pillow/pyzbar fallback if zbar CLI isn't available
try:
    from PIL import Image
    from pyzbar.pyzbar import decode as pyzbar_decode
except Exception:
    Image = None
    pyzbar_decode = None

def _b64url_decode(data: str) -> bytes:
    data = data.strip().replace(" ", "+")
    data = data + ("=" * ((4 - len(data) % 4) % 4))
    return base64.urlsafe_b64decode(data)

def extract_raw_qr_strings(image_path: str) -> List[str]:
    # Prefer zbarimg for speed/robustness
    try:
        out = subprocess.check_output(["zbarimg", "--raw", image_path], text=True)
        lines = [ln.strip() for ln in out.splitlines() if ln.strip()]
        if lines:
            return lines
    except Exception:
        pass
    # Fallback to pyzbar
    if Image and pyzbar_decode:
        try:
            img = Image.open(image_path).convert("RGB")
            dec = pyzbar_decode(img)
            return [d.data.decode("utf-8", errors="ignore") for d in dec]
        except Exception:
            return []
    return []

# --- minimal protobuf wire parser (just enough to read MigrationPayload -> OtpParameters.secret) ---

def _read_varint(buf: bytes, i: int):
    shift = 0
    x = 0
    while True:
        b = buf[i]; i += 1
        x |= (b & 0x7F) << shift
        if not (b & 0x80):
            return x, i
        shift += 7

def _read_len(buf: bytes, i: int):
    n, i = _read_varint(buf, i)
    s, e = i, i + n
    return buf[s:e], e

def _parse_migration_for_secret(payload: bytes) -> Optional[bytes]:
    """Return first OtpParameters.secret bytes from MigrationPayload."""
    i = 0
    L = len(payload)
    while i < L:
        key, i = _read_varint(payload, i)
        field, wtype = (key >> 3), (key & 7)
        if field == 1 and wtype == 2:  # otp_parameters (repeated message)
            msg, i = _read_len(payload, i)
            # parse OtpParameters
            j, M = 0, len(msg)
            while j < M:
                k, j = _read_varint(msg, j)
                f, wt = (k >> 3), (k & 7)
                if f == 1 and wt == 2:  # secret bytes
                    sec, j = _read_len(msg, j)
                    return sec
                else:
                    if wt == 0: _, j = _read_varint(msg, j)
                    elif wt == 1: j += 8
                    elif wt == 2: _, j = _read_len(msg, j)
                    elif wt == 5: j += 4
                    else: return None
        else:
            if wtype == 0: _, i = _read_varint(payload, i)
            elif wtype == 1: i += 8
            elif wtype == 2: _, i = _read_len(payload, i)
            elif wtype == 5: i += 4
            else: return None
    return None

def _decode_migration(url: str) -> Optional[str]:
    try:
        qs = up.urlparse(url).query
        params = dict(up.parse_qsl(qs))
        data_b64 = params.get("data", "")
        if not data_b64:
            return None
        payload = _b64url_decode(data_b64)
        sec_bytes = _parse_migration_for_secret(payload)
        if not sec_bytes:
            return None
        # Return Base32 uppercase (no padding) as common TOTP secret format
        return base64.b32encode(sec_bytes).decode("ascii").strip("=").upper()
    except Exception:
        return None

def extract_secret_from_image(image_path: str) -> Optional[str]:
    strings = extract_raw_qr_strings(image_path)
    if not strings:
        return None

    # 1) Migration payload
    for s in strings:
        if s.startswith("otpauth-migration://"):
            sec = _decode_migration(s)
            if sec:
                return sec

    # 2) Standard otpauth://... ?secret=XXXX
    for s in strings:
        if s.startswith("otpauth://"):
            try:
                qs = up.urlparse(s).query
                sec = dict(up.parse_qsl(qs)).get("secret", "").strip()
                if sec:
                    return sec.upper()
            except Exception:
                pass

    # 3) Any other URL-ish string with secret=...
    for s in strings:
        if "secret=" in s:
            try:
                qs = up.urlparse(s).query
                sec = dict(up.parse_qsl(qs)).get("secret", "").strip()
                if sec:
                    return sec.upper()
            except Exception:
                pass

    return None
