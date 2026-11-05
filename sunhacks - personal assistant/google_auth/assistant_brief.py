"""Turn Gmail, Calendar, and place-lookup results into a daily brief.

This module only reads the dictionaries already returned by
``fetch_emails``, ``fetch_events``, and ``lookup_organization``. It does not
authenticate, send mail, or change those functions.

Run a sample brief with no Google credentials::

    python assistant_brief.py

Pull live data with the existing helpers (run from this folder)::

    python assistant_brief.py --live
"""

from __future__ import annotations

import argparse
import re
from datetime import datetime, time, timedelta, tzinfo
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

ZERO = timedelta(0)
HOUR = timedelta(hours=1)


def _first_sunday_on_or_after(moment: datetime) -> datetime:
    days_to_go = 6 - moment.weekday()
    if days_to_go:
        moment += timedelta(days=days_to_go)
    return moment


class MountainTime(tzinfo):
    """America/Denver using US daylight-saving rules in effect since 2007.

    The standard-library zone database is not installed on every Windows
    Python, and this folder already treats Denver as the local calendar zone.
    A small tzinfo keeps the brief runnable without adding a dependency.
    """

    def tzname(self, dt: Optional[datetime]) -> str:
        return "MDT" if self.dst(dt) else "MST"

    def utcoffset(self, dt: Optional[datetime]) -> timedelta:
        return timedelta(hours=-7) + self.dst(dt)

    def dst(self, dt: Optional[datetime]) -> timedelta:
        if dt is None or dt.tzinfo is None:
            return ZERO
        # Second Sunday in March, 02:00, through first Sunday in November, 02:00.
        start = _first_sunday_on_or_after(datetime(dt.year, 3, 8, 2))
        end = _first_sunday_on_or_after(datetime(dt.year, 11, 1, 2))
        wall = dt.replace(tzinfo=None)
        if start <= wall < end:
            return HOUR
        return ZERO


DENVER = MountainTime()

Email = Dict[str, Any]
Event = Dict[str, Any]
Place = Dict[str, Any]
Interval = Tuple[datetime, datetime]

# Weighted phrases. Longer or more specific phrases are checked as written;
# matching is case-insensitive on the subject and snippet together.
URGENT_PATTERNS: Sequence[Tuple[str, int, str]] = (
    (r"\basap\b", 5, "asks for a response as soon as possible"),
    (r"\bimmediately\b", 5, "asks for an immediate response"),
    (r"\burgent\b", 5, "marked urgent"),
    (r"\boverdue\b", 4, "something is overdue"),
    (r"\bdeadline\b", 4, "mentions a deadline"),
    (r"\bdue\b", 4, "mentions a due date"),
    (r"\beod\b", 4, "needs attention by end of day"),
    (r"action required", 3, "says action is required"),
    (r"response needed", 3, "needs a response"),
    (r"please respond", 3, "asks you to respond"),
    (r"\brsvp\b", 3, "asks for an RSVP"),
    (r"\binvoice\b", 3, "looks like an invoice"),
    (r"\bpayment\b", 3, "mentions a payment"),
    (r"\bbill\b", 3, "mentions a bill"),
    (r"\bmeeting\b", 2, "is about a meeting"),
    (r"\binvite\b", 2, "looks like an invite"),
    (r"\bzoom\b", 2, "includes a Zoom link or meeting"),
    (r"\bteams\b", 2, "includes a Teams meeting"),
)

LOW_PRIORITY_PATTERNS: Sequence[Tuple[str, int, str]] = (
    (r"\bunsubscribe\b", -2, "looks like a mailing list"),
    (r"\bnewsletter\b", -2, "looks like a newsletter"),
    (r"\bpromotional\b", -2, "looks promotional"),
    (r"\bno[\s-]?reply\b", -1, "comes from a no-reply address"),
)

ATTENTION_THRESHOLD = 3


def score_email(email: Email) -> Dict[str, Any]:
    """Score one email dict from ``fetch_emails``.

    Uses ``subject``, ``snippet``, and ``from``. If the dict also has
    ``is_read`` set to False, or ``unread`` set to True, unread mail gets a
    small boost. ``fetch_emails`` does not currently include that field, so
    the boost only applies when a caller adds it.
    """
    subject = str(email.get("subject") or "")
    snippet = str(email.get("snippet") or "")
    sender = str(email.get("from") or "")
    text = f"{subject}\n{snippet}\n{sender}".lower()

    score = 0
    reasons: List[str] = []
    for pattern, weight, reason in URGENT_PATTERNS:
        if re.search(pattern, text):
            score += weight
            reasons.append(reason)
    for pattern, weight, reason in LOW_PRIORITY_PATTERNS:
        if re.search(pattern, text):
            score += weight
            reasons.append(reason)

    if email.get("is_read") is False or email.get("unread") is True:
        score += 1
        reasons.append("is unread")

    score = max(score, 0)
    return {
        "id": email.get("id"),
        "subject": subject or "No Subject",
        "from": sender or "Unknown Sender",
        "score": score,
        "reasons": reasons,
    }


def rank_emails(emails: Iterable[Email], limit: int = 3) -> List[Dict[str, Any]]:
    """Return the highest-scoring emails that clear the attention threshold."""
    ranked = [score_email(email) for email in emails]
    ranked = [item for item in ranked if item["score"] >= ATTENTION_THRESHOLD]
    ranked.sort(key=lambda item: item["score"], reverse=True)
    return ranked[:limit]


def _parse_moment(value: Any, zone: tzinfo = DENVER) -> Optional[datetime]:
    """Parse a Calendar start/end string into an aware datetime.

    Accepts the two shapes ``fetch_events`` stores: a full date-time, or an
    all-day ``YYYY-MM-DD`` date.
    """
    if not value or not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    try:
        if "T" not in text:
            day = datetime.strptime(text[:10], "%Y-%m-%d")
            return day.replace(tzinfo=zone)
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=zone)
        return parsed.astimezone(zone)
    except ValueError:
        return None


def event_interval(event: Event, zone: tzinfo = DENVER) -> Optional[Interval]:
    """Return ``(start, end)`` for one ``fetch_events`` dict, if both parse."""
    start = _parse_moment(event.get("start"), zone)
    end = _parse_moment(event.get("end"), zone)
    if start is None or end is None or end <= start:
        return None
    return start, end


def find_conflicts(events: Sequence[Event], zone: tzinfo = DENVER) -> List[Dict[str, Any]]:
    """Find pairs of events whose times overlap.

    An event that ends when the next one starts is not a conflict.
    """
    parsed: List[Tuple[Event, Interval]] = []
    for event in events:
        interval = event_interval(event, zone)
        if interval is not None:
            parsed.append((event, interval))
    parsed.sort(key=lambda item: item[1][0])

    conflicts: List[Dict[str, Any]] = []
    for index, (left, left_span) in enumerate(parsed):
        for right, right_span in parsed[index + 1 :]:
            if right_span[0] >= left_span[1]:
                break
            overlap_start = max(left_span[0], right_span[0])
            overlap_end = min(left_span[1], right_span[1])
            conflicts.append(
                {
                    "event_a": left.get("summary") or "No Summary",
                    "event_b": right.get("summary") or "No Summary",
                    "overlap_start": overlap_start.isoformat(),
                    "overlap_end": overlap_end.isoformat(),
                }
            )
    return conflicts


def _merge_intervals(intervals: Sequence[Interval]) -> List[Interval]:
    if not intervals:
        return []
    ordered = sorted(intervals, key=lambda span: span[0])
    merged: List[Interval] = [ordered[0]]
    for start, end in ordered[1:]:
        last_start, last_end = merged[-1]
        if start <= last_end:
            merged[-1] = (last_start, max(last_end, end))
        else:
            merged.append((start, end))
    return merged


def suggest_free_windows(
    events: Sequence[Event],
    duration_minutes: int = 30,
    day: Optional[datetime] = None,
    work_start: int = 9,
    work_end: int = 18,
    zone: tzinfo = DENVER,
) -> List[Dict[str, Any]]:
    """Find gaps inside one workday that can fit ``duration_minutes``.

    The day defaults to today in America/Denver, matching ``calendar_access``.
    All-day events block the whole workday. Timed events block only the
    portion that falls inside the workday.
    """
    if day is None:
        day = datetime.now(zone)
    elif day.tzinfo is None:
        day = day.replace(tzinfo=zone)
    else:
        day = day.astimezone(zone)

    window_start = datetime.combine(day.date(), time(work_start), tzinfo=zone)
    window_end = datetime.combine(day.date(), time(work_end), tzinfo=zone)
    if window_end <= window_start:
        return []

    busy: List[Interval] = []
    for event in events:
        interval = event_interval(event, zone)
        if interval is None:
            continue
        start, end = interval
        all_day = isinstance(event.get("start"), str) and "T" not in event["start"]
        if all_day and start.date() <= day.date() < end.date():
            busy.append((window_start, window_end))
            continue
        clipped_start = max(start, window_start)
        clipped_end = min(end, window_end)
        if clipped_end > clipped_start:
            busy.append((clipped_start, clipped_end))

    gaps: List[Dict[str, Any]] = []
    cursor = window_start
    needed = timedelta(minutes=duration_minutes)
    for start, end in _merge_intervals(busy):
        if start - cursor >= needed:
            gaps.append(_gap_dict(cursor, start))
        cursor = max(cursor, end)
    if window_end - cursor >= needed:
        gaps.append(_gap_dict(cursor, window_end))
    return gaps


def _gap_dict(start: datetime, end: datetime) -> Dict[str, Any]:
    minutes = int((end - start).total_seconds() // 60)
    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "minutes": minutes,
    }


def assess_place(details: Optional[Place]) -> Dict[str, Any]:
    """Add a confidence note to a ``lookup_organization`` result.

    The original keys are copied through. A failed lookup stays a failure
    with confidence 0.
    """
    if not details or details.get("error"):
        return {
            "phone": None,
            "address": None,
            "hours": None,
            "website": None,
            "confidence": 0.0,
            "missing": ["phone", "address", "hours", "website"],
            "note": str(details.get("error")) if details and details.get("error") else "No place details.",
        }

    fields = ("phone", "address", "hours", "website")
    present = [name for name in fields if _filled(details.get(name))]
    missing = [name for name in fields if name not in present]
    confidence = round(len(present) / len(fields), 2)

    notes: List[str] = []
    if "phone" in present:
        notes.append("phone found")
    if "hours" in missing:
        notes.append("hours missing")
    address = details.get("address")
    if address and not re.search(r"\d{5}", str(address)):
        notes.append("address looks partial")
        confidence = round(max(confidence - 0.15, 0), 2)
    if not notes:
        notes.append("contact details look complete")

    assessed = {name: details.get(name) for name in fields}
    assessed.update(
        {
            "confidence": confidence,
            "missing": missing,
            "note": "; ".join(notes),
        }
    )
    return assessed


def _filled(value: Any) -> bool:
    return value is not None and str(value).strip() != ""


def meetings_on(events: Sequence[Event], day: datetime, zone: tzinfo = DENVER) -> List[Dict[str, str]]:
    """Events that overlap the given calendar day."""
    if day.tzinfo is None:
        day = day.replace(tzinfo=zone)
    else:
        day = day.astimezone(zone)
    day_start = datetime.combine(day.date(), time.min, tzinfo=zone)
    day_end = day_start + timedelta(days=1)

    meetings: List[Dict[str, str]] = []
    for event in events:
        interval = event_interval(event, zone)
        if interval is None:
            continue
        start, end = interval
        if start < day_end and end > day_start:
            meetings.append(
                {
                    "summary": str(event.get("summary") or "No Summary"),
                    "start": start.isoformat(),
                    "end": end.isoformat(),
                }
            )
    meetings.sort(key=lambda item: item["start"])
    return meetings


def build_daily_brief(
    emails: Sequence[Email],
    events: Sequence[Event],
    place: Optional[Place] = None,
    now: Optional[datetime] = None,
    zone: tzinfo = DENVER,
) -> Dict[str, Any]:
    """Combine scored mail, today's meetings, conflicts, and one next step."""
    if now is None:
        now = datetime.now(zone)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=zone)
    else:
        now = now.astimezone(zone)

    attention = rank_emails(emails)
    today = meetings_on(events, now, zone)
    conflicts = find_conflicts(events, zone)
    windows = suggest_free_windows(events, day=now, zone=zone)
    place_assessment = assess_place(place) if place is not None else None

    brief = {
        "generated_at": now.isoformat(),
        "needs_attention": attention,
        "meetings_today": today,
        "conflicts": conflicts,
        "free_windows": windows,
        "place": place_assessment,
        "next_step": _next_step(attention, today, conflicts, windows, now),
    }
    return brief


def _next_step(
    attention: Sequence[Dict[str, Any]],
    today: Sequence[Dict[str, str]],
    conflicts: Sequence[Dict[str, Any]],
    windows: Sequence[Dict[str, Any]],
    now: datetime,
) -> str:
    if conflicts:
        first = conflicts[0]
        return f"Resolve the overlap between {first['event_a']} and {first['event_b']}."
    if attention:
        top = attention[0]
        why = top["reasons"][0] if top["reasons"] else "it scored as needing you"
        return f"Read “{top['subject']}” from {top['from']}: {why}."
    upcoming = [meeting for meeting in today if meeting["start"] >= now.isoformat()]
    if windows and upcoming:
        gap = windows[0]
        start = _clock(gap["start"])
        return (
            f"You have {gap['minutes']} minutes free starting {start}, "
            f"before {upcoming[0]['summary']}."
        )
    if upcoming:
        return f"Nothing urgent. Next meeting is {upcoming[0]['summary']} at {_clock(upcoming[0]['start'])}."
    if today:
        return "Nothing urgent. Today's meetings are already underway or finished."
    return "Nothing urgent in mail, and the workday is open."


def _clock(iso_value: str) -> str:
    parsed = datetime.fromisoformat(iso_value)
    clock = parsed.strftime("%I:%M %p").lstrip("0")
    return clock


def format_brief_for_voice(brief: Dict[str, Any]) -> str:
    """Turn a brief into one short paragraph a voice assistant can read."""
    sentences: List[str] = []

    attention = brief.get("needs_attention") or []
    if attention:
        count = len(attention)
        noun = "email needs" if count == 1 else "emails need"
        top = attention[0]
        sentences.append(
            f"{count} {noun} you. The first is “{top['subject']}” from {top['from']}."
        )
    else:
        sentences.append("No emails need you right now.")

    meetings = brief.get("meetings_today") or []
    if meetings:
        names = ", ".join(meeting["summary"] for meeting in meetings[:3])
        sentences.append(f"Today you have {names}.")
    else:
        sentences.append("You have no meetings today.")

    conflicts = brief.get("conflicts") or []
    if conflicts:
        first = conflicts[0]
        sentences.append(f"{first['event_a']} overlaps {first['event_b']}.")

    place = brief.get("place") or {}
    if place:
        note = str(place.get("note") or "").strip()
        if note and not note.endswith("."):
            note = f"{note}."
        if place.get("phone"):
            sentences.append(f"The place lookup found phone {place['phone']}. {note}".strip())
        elif note:
            sentences.append(f"Place lookup: {note}")

    next_step = brief.get("next_step")
    if next_step:
        sentences.append(next_step)

    return " ".join(sentences)


def _sample_inputs() -> Tuple[List[Email], List[Event], Place, datetime]:
    """Fixed sample so the demo can be checked without Google credentials."""
    now = datetime(2026, 11, 5, 10, 0, tzinfo=DENVER)
    emails = [
        {
            "id": "1",
            "subject": "Action required: invoice due today",
            "from": "billing@studio.example",
            "snippet": "Please respond by EOD. This payment is overdue.",
            "is_read": False,
        },
        {
            "id": "2",
            "subject": "Weekly newsletter",
            "from": "news@lists.example",
            "snippet": "Unsubscribe anytime. Here is what shipped this week.",
        },
        {
            "id": "3",
            "subject": "Zoom invite: design review",
            "from": "alex@team.example",
            "snippet": "Meeting tomorrow. Please RSVP.",
        },
    ]
    events = [
        {
            "id": "a",
            "summary": "Standup",
            "start": "2026-11-05T09:00:00-07:00",
            "end": "2026-11-05T09:30:00-07:00",
        },
        {
            "id": "b",
            "summary": "Sponsor call",
            "start": "2026-11-05T13:00:00-07:00",
            "end": "2026-11-05T14:00:00-07:00",
        },
        {
            "id": "c",
            "summary": "Demo rehearsal",
            "start": "2026-11-05T13:30:00-07:00",
            "end": "2026-11-05T14:30:00-07:00",
        },
    ]
    place = {
        "phone": "(480) 555-0142",
        "address": "Tempe, AZ",
        "hours": None,
        "website": "https://example.com",
    }
    return emails, events, place, now


def _check_sample(brief: Dict[str, Any]) -> List[str]:
    """Return a list of failed check names. Empty means the sample held."""
    failures: List[str] = []
    subjects = [item["subject"] for item in brief["needs_attention"]]
    if not subjects or "invoice" not in subjects[0].lower():
        failures.append("urgent invoice should rank first")
    if any("newsletter" in subject.lower() for subject in subjects):
        failures.append("newsletter should stay below the attention threshold")
    if len(brief["conflicts"]) != 1:
        failures.append("sponsor call and demo rehearsal should overlap once")
    if not any(window["minutes"] >= 30 for window in brief["free_windows"]):
        failures.append("the morning gap should be at least 30 minutes")
    if brief["place"]["confidence"] <= 0:
        failures.append("partial place details should still have some confidence")
    if "hours missing" not in brief["place"]["note"]:
        failures.append("missing hours should be called out")
    return failures


def _live_brief() -> Dict[str, Any]:
    """Build a brief from the existing Gmail, Calendar, and lookup helpers."""
    import sys
    from pathlib import Path

    folder = str(Path(__file__).resolve().parent)
    if folder not in sys.path:
        sys.path.insert(0, folder)

    from calendar_access import fetch_events
    from google_auth import authenticate_gmail, authenticate_google
    from organization_lookup import lookup_organization
    from read_emails import fetch_emails

    gmail = authenticate_gmail()
    calendar = authenticate_google("calendar", version="v3")
    emails = fetch_emails(gmail, is_read=False, max_results=10)
    events = fetch_events(calendar, max_results=15)
    place = lookup_organization("Arizona State University")
    return build_daily_brief(emails, events, place=place)


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a daily assistant brief.")
    parser.add_argument(
        "--live",
        action="store_true",
        help="Fetch unread mail, calendar events, and one place lookup.",
    )
    args = parser.parse_args()

    if args.live:
        brief = _live_brief()
    else:
        emails, events, place, now = _sample_inputs()
        brief = build_daily_brief(emails, events, place=place, now=now)
        failures = _check_sample(brief)
        if failures:
            print("Sample checks failed:")
            for failure in failures:
                print(f"- {failure}")
        else:
            print("Sample checks passed.")
        print()

    print(format_brief_for_voice(brief))
    print()
    print(f"Next step: {brief['next_step']}")


if __name__ == "__main__":
    main()
