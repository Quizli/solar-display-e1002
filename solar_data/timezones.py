"""Timezone compatibility for supported Python versions."""

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover - exercised on Python 3.8
    from backports.zoneinfo import ZoneInfo


ZURICH = ZoneInfo("Europe/Zurich")

__all__ = ["ZoneInfo", "ZURICH"]
