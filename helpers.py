from __future__ import annotations

from datetime import datetime, timezone

from rpc_client import CONFIG

ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
HASHRATE_UNITS = ["H/s", "kH/s", "MH/s", "GH/s", "TH/s", "PH/s", "EH/s"]


def format_amount(liners: int) -> str:
    return f"{liners / 100_000_000:.8f}"


def format_amount_us(liners: int) -> str:
    return f"{liners / 100_000_000:,.8f}"


def format_timestamp(ts: int | None) -> str:
    if not ts:
        return "-"
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def format_lock_time(lock_time: int | None) -> str:
    if lock_time is None:
        return "-"
    if lock_time < 500_000_000:
        return f"height {lock_time}"
    return format_timestamp(lock_time)


def human_delta(ts: int | None) -> str:
    if not ts:
        return "-"
    now = datetime.now(timezone.utc)
    then = datetime.fromtimestamp(ts, tz=timezone.utc)
    delta = now - then
    seconds = int(delta.total_seconds())

    if seconds < 0:
        seconds = abs(seconds)
        if seconds < 60:
            return f"in {seconds}s"
        minutes = seconds // 60
        if minutes < 60:
            return f"in {minutes}m"
        hours = minutes // 60
        if hours < 24:
            return f"in {hours}h"
        days = hours // 24
        return f"in {days}d"
    if seconds < 60:
        return f"{seconds}s ago"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m ago"
    hours = minutes // 60
    if hours < 24:
        return f"{hours}h ago"
    days = hours // 24
    return f"{days}d ago"


def format_hashrate(rate: float | int | None) -> str:
    if rate is None:
        return "-"
    value = float(rate)
    unit_index = 0
    while value >= 1000 and unit_index < len(HASHRATE_UNITS) - 1:
        value /= 1000
        unit_index += 1
    return f"{value:.2f} {HASHRATE_UNITS[unit_index]}"


def address_from_script(script_hex: str) -> str | None:
    script = bytes.fromhex(script_hex)
    if (
        len(script) == 25
        and script[0] == 0x76
        and script[1] == 0xA9
        and script[2] == 0x14
        and script[-2:] == b"\x88\xac"
    ):
        version = CONFIG["display"].get("address_version", 0x35)
        payload = bytes([version]) + script[3:-2]
        return base58check_encode(payload)
    return None


def base58check_encode(payload: bytes) -> str:
    checksum = double_sha256(payload)[:4]
    data = payload + checksum
    num = int.from_bytes(data, "big")
    encoded = ""
    while num > 0:
        num, rem = divmod(num, 58)
        encoded = ALPHABET[rem] + encoded
    leading_zeros = len(data) - len(data.lstrip(b"\x00"))
    return "1" * leading_zeros + encoded


def double_sha256(data: bytes) -> bytes:
    import hashlib

    return hashlib.sha256(hashlib.sha256(data).digest()).digest()


__all__ = [
    "address_from_script",
    "base58check_encode",
    "double_sha256",
    "format_amount",
    "format_amount_us",
    "format_hashrate",
    "format_timestamp",
    "format_lock_time",
    "human_delta",
]
