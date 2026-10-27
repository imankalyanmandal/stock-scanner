"""NSE trading calendar: which session the latest data should be for."""
from datetime import date, datetime, time, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))
DATA_READY = time(16, 0)   # after this (IST) today's session counts as complete


def is_trading_day(d: date, holidays: set[str]) -> bool:
    return d.weekday() < 5 and d.isoformat() not in holidays


def expected_session(cfg: dict, now: datetime | None = None) -> date:
    """Latest NSE session that should be complete at `now` (IST)."""
    now = (now or datetime.now(IST)).astimezone(IST)
    holidays = {str(h) for h in cfg["market"].get("holidays", [])}
    d = now.date() if now.time() >= DATA_READY else now.date() - timedelta(days=1)
    while not is_trading_day(d, holidays):
        d -= timedelta(days=1)
    return d
