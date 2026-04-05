import calendar
import time


def utc_now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def iso_to_epoch(value: str | None) -> float:
    if not value:
        return 0.0
    try:
        return float(calendar.timegm(time.strptime(value, "%Y-%m-%dT%H:%M:%SZ")))
    except Exception:
        return 0.0
