"""Local time for the operation: time zones and daily windows such as finish windows and quiet hours.

Core: the operation file's planning windows, alert quiet hours, `ledger sql`'s local_time() and the
industry plugin's job timing all read local time through here.
"""

from __future__ import annotations

import re
from datetime import timedelta, timezone, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def parse_windows(spec: str) -> list[tuple[int, int]]:
    """"08-10,20-22" -> [(480, 600), (1200, 1320)] in minutes after local midnight; HH:MM allowed."""
    def minutes(text: str) -> int:
        hh, _, mm = text.strip().partition(":")
        value = int(hh) * 60 + (int(mm) if mm else 0)
        if not 0 <= value <= 24 * 60:
            raise ValueError
        return value

    windows = []
    for part in spec.split(","):
        lo, sep, hi = part.partition("-")
        try:
            if not sep:
                raise ValueError
            window = (minutes(lo), minutes(hi))
        except ValueError:
            raise RuntimeError(f"--finish-window expects ranges like 08-10,20-22 or 08:30-10:00, got '{part}'")
        if window[0] >= window[1]:
            raise RuntimeError(f"--finish-window range '{part}' must start before it ends")
        windows.append(window)
    if not windows:
        raise RuntimeError("--finish-window needs at least one range")
    return windows


_OFFSET = re.compile(r"^(?:UTC|GMT)?\s*([+-])(\d{1,2})(?::?(\d{2}))?$", re.IGNORECASE)


def zone(name: str | None) -> tzinfo:
    """UTC, a fixed offset (`+03:00`, `UTC+3`, `-0530`) or an IANA name (`Europe/Helsinki`).

    An IANA zone follows daylight saving; it needs a time zone database, which Linux and macOS ship
    and Windows does not - there Python only finds one when the optional `tzdata` package is
    installed, and this tool adds no dependencies. A fixed offset works everywhere but never
    switches between summer and winter time."""
    if not name or name.strip().upper() in ("UTC", "GMT", "Z"):
        return timezone.utc
    match = _OFFSET.match(name.strip())
    if match:
        sign, hours, minutes = match.groups()
        delta = timedelta(hours=int(hours), minutes=int(minutes or 0))
        if delta > timedelta(hours=14):
            raise RuntimeError(f"time zone offset '{name}' is outside -14:00..+14:00")
        return timezone(-delta if sign == "-" else delta)
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        if not zone_database_available():
            raise RuntimeError(
                f"no time zone database on this machine to look up '{name}' (Windows ships none) - "
                f"install the optional tzdata package (pip install tzdata), or pass a fixed offset "
                f"such as +03:00, which does not follow daylight saving") from None
        raise RuntimeError(f"unknown time zone '{name}' - use an IANA name such as Europe/Helsinki, "
                           f"or a fixed offset such as +03:00") from None


def zone_database_available() -> bool:
    """Whether IANA names can be resolved here at all (system database or the tzdata package)."""
    try:
        ZoneInfo("Europe/London")
        return True
    except (ZoneInfoNotFoundError, ValueError):
        return False
