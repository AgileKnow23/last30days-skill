"""Date utilities for last30days skill."""

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Optional, Tuple


def parse_as_of_date(as_of_date: Optional[str]) -> Optional[str]:
    """Validate and normalize an --as-of date.

    Args:
        as_of_date: Date string in YYYY-MM-DD format.

    Returns:
        Normalized YYYY-MM-DD string, or None when no date was provided.

    Raises:
        ValueError: If the date is not in YYYY-MM-DD format.
    """
    if as_of_date is None:
        return None

    if not as_of_date.strip():
        raise ValueError("--as-of must be in YYYY-MM-DD format.")

    try:
        parsed = datetime.strptime(as_of_date, "%Y-%m-%d").date()
    except ValueError as exc:
        raise ValueError(
            f"Invalid --as-of date: {as_of_date}. Expected YYYY-MM-DD."
        ) from exc

    return parsed.isoformat()


def get_date_range(days: int = 30, as_of_date: Optional[str] = None) -> Tuple[str, str]:
    """Get the date range for the last N days.

    When as_of_date is provided, the range ends at that date instead of today.

    Args:
        days: Number of days to look back.
        as_of_date: Optional end date in YYYY-MM-DD format.

    Returns:
        Tuple of (from_date, to_date) as YYYY-MM-DD strings.
    """
    normalized_as_of = parse_as_of_date(as_of_date)

    if normalized_as_of:
        to_date = datetime.strptime(normalized_as_of, "%Y-%m-%d").date()
    else:
        to_date = datetime.now(timezone.utc).date()

    from_date = to_date - timedelta(days=days)
    return from_date.isoformat(), to_date.isoformat()


def parse_date(date_str: Optional[str]) -> Optional[datetime]:
    """Parse a date string in various formats.

    Supports: YYYY-MM-DD, ISO 8601, Unix timestamp
    """
    if not date_str:
        return None

    # Try Unix timestamp (from Reddit)
    try:
        ts = float(date_str)
        return datetime.fromtimestamp(ts, tz=timezone.utc)
    except (ValueError, TypeError):
        pass

    # Try ISO formats
    formats = [
        "%Y-%m-%d",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S.%fZ",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%S.%f%z",
    ]

    for fmt in formats:
        try:
            dt = datetime.strptime(date_str, fmt)
            if dt.tzinfo is not None:
                return dt.astimezone(timezone.utc)
            return dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue

    return None


def timestamp_to_date(ts: Optional[float]) -> Optional[str]:
    """Convert Unix timestamp to YYYY-MM-DD string."""
    if ts is None:
        return None
    try:
        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        return dt.date().isoformat()
    except (ValueError, TypeError, OSError):
        return None


def get_date_confidence(date_str: Optional[str], from_date: str, to_date: str) -> str:
    """Determine confidence level for a date.

    Args:
        date_str: The date to check (YYYY-MM-DD or None)
        from_date: Start of valid range (YYYY-MM-DD)
        to_date: End of valid range (YYYY-MM-DD)

    Returns:
        'high', 'med', or 'low'
    """
    if not date_str:
        return 'low'

    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d").date()
        start = datetime.strptime(from_date, "%Y-%m-%d").date()
        end = datetime.strptime(to_date, "%Y-%m-%d").date()

        return 'high' if start <= dt <= end else 'low'
    except ValueError:
        return 'low'


def days_ago(date_str: Optional[str], reference_date: Optional[str] = None) -> Optional[int]:
    """Calculate how many days before the reference date a date is.

    If reference_date is None, use real today for backward compatibility.
    Returns None if date is invalid or missing.
    """
    if not date_str:
        return None

    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d").date()
        if reference_date:
            today = datetime.strptime(reference_date, "%Y-%m-%d").date()
        else:
            today = datetime.now(timezone.utc).date()
        delta = today - dt
        return delta.days
    except ValueError:
        return None


def recency_score(
    date_str: Optional[str],
    max_days: int = 30,
    reference_date: Optional[str] = None,
) -> int:
    """Calculate recency score (0-100).

    0 days before reference_date = 100, max_days before reference_date = 0.
    If reference_date is None, use real today for backward compatibility.
    """
    age = days_ago(date_str, reference_date=reference_date)
    if age is None:
        return 0

    if age < 0:
        return 100
    if age >= max_days:
        return 0

    return int(100 * (1 - age / max_days))


# ---------------------------------------------------------------------------
# Relative dates ("5 days ago") resolved against an explicit retrieval time
# ---------------------------------------------------------------------------

# Search providers label fresh results relatively ("10 hours ago", "2 weeks
# ago") instead of with a calendar date. Resolving them needs the retrieval
# instant, and the label is rounded, so the result is an interval, never a
# point: "5 days ago" may be anywhere from 5 to just under 6 days back.
_RELATIVE_DATE_RE = re.compile(
    r"^\s*(?P<amount>\d{1,4}|an?|one)\s+"
    r"(?P<unit>minute|min|hour|hr|day|week|wk|month|mo)s?\s+ago\s*$",
    re.IGNORECASE,
)
_UNIT_ALIASES = {
    "min": "minute", "minute": "minute",
    "hr": "hour", "hour": "hour",
    "day": "day",
    "wk": "week", "week": "week",
    "mo": "month", "month": "month",
}
# Days per unit for the point estimate ("latest"), plus the rounding slack
# that bounds the earliest plausible date. Months are calendar-agnostic on
# purpose: the label carries no more precision than "about 30N days".
_UNIT_DAYS = {"day": 1, "week": 7, "month": 30}
# Rounding slack per unit: a label is rounded down, so "5 days ago" can be
# almost 6 days back, "2 weeks ago" almost 3 weeks, "1 month ago" almost 2
# months. The earliest plausible calendar date is therefore one whole unit
# before the point estimate, for every unit alike.
_UNIT_SLACK_DAYS = {"day": 1, "week": 7, "month": 30}
_UNIT_PRECISION = {"minute": "day", "hour": "day", "day": "day", "week": "week", "month": "month"}

DATE_PRECISION_DAY = "day"
DATE_PRECISION_WEEK = "week"
DATE_PRECISION_MONTH = "month"


@dataclass(frozen=True)
class RelativeDate:
    """A relative label resolved against a UTC retrieval instant."""

    text: str
    amount: int
    unit: str
    precision: str
    latest: date
    earliest: date
    retrieved_at: datetime

    @property
    def iso(self) -> str:
        """Point estimate (latest plausible calendar date) in YYYY-MM-DD."""
        return self.latest.isoformat()

    def within_window(self, from_date: str, to_date: str) -> bool:
        """True only when every plausible calendar date falls inside the window."""
        return from_date <= self.earliest.isoformat() and self.latest.isoformat() <= to_date


def utc_now(value: Optional[datetime] = None) -> datetime:
    """Return an aware UTC datetime; naive inputs are taken as UTC."""
    if value is None:
        return datetime.now(timezone.utc)
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def parse_relative_date(text: Optional[str], *, now: Optional[datetime] = None) -> Optional[RelativeDate]:
    """Parse "N minutes|hours|days|weeks|months ago" against ``now`` (UTC).

    Returns None for anything else (absolute dates, "yesterday", garbage), so
    callers fall through to their absolute-date parsing or to "unknown".
    """
    if not text:
        return None
    match = _RELATIVE_DATE_RE.match(str(text))
    if not match:
        return None
    raw_amount = match.group("amount").lower()
    amount = 1 if raw_amount in ("a", "an", "one") else int(raw_amount)
    if amount <= 0:
        return None
    unit = _UNIT_ALIASES[match.group("unit").lower()]
    reference = utc_now(now)
    if unit == "minute":
        latest_dt = reference - timedelta(minutes=amount)
        earliest_dt = latest_dt
    elif unit == "hour":
        latest_dt = reference - timedelta(hours=amount)
        earliest_dt = latest_dt
    else:
        latest_dt = reference - timedelta(days=amount * _UNIT_DAYS[unit])
        earliest_dt = latest_dt - timedelta(days=_UNIT_SLACK_DAYS[unit])
    return RelativeDate(
        text=str(text).strip(),
        amount=amount,
        unit=unit,
        precision=_UNIT_PRECISION[unit],
        latest=latest_dt.date(),
        earliest=earliest_dt.date(),
        retrieved_at=reference,
    )


def relative_date_confidence(relative: RelativeDate, from_date: str, to_date: str) -> str:
    """Confidence for a derived date: never 'high' (that is reserved for
    source-provided dates); 'med' when the whole plausible interval sits inside
    the window, otherwise 'low'."""
    if relative.precision == DATE_PRECISION_MONTH:
        return "low"
    return "med" if relative.within_window(from_date, to_date) else "low"
