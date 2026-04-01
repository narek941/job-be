"""Экспорт событий в формат iCalendar (.ics) для импорта в Google Calendar / Apple Calendar."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone


def escape_ics_text(s: str) -> str:
    return (
        s.replace("\\", "\\\\")
        .replace("\n", "\\n")
        .replace(",", "\\,")
        .replace(";", "\\;")
    )


def _ics_utc(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    dt = dt.astimezone(timezone.utc)
    return dt.strftime("%Y%m%dT%H%M%SZ")


def events_to_ics(events: list[dict], calendar_name: str = "ArmApply") -> str:
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//ArmApply//Calendar//RU",
        f"X-WR-CALNAME:{escape_ics_text(calendar_name)}",
        "CALSCALE:GREGORIAN",
    ]
    now = _ics_utc(datetime.now(timezone.utc))
    for ev in events:
        uid = str(ev.get("id", uuid.uuid4()))
        title = escape_ics_text(str(ev.get("title", "Interview")))
        start = ev.get("starts_at", "")
        end = ev.get("ends_at") or start
        notes = escape_ics_text(str(ev.get("notes") or ""))
        try:
            dt_start = datetime.fromisoformat(str(start).replace("Z", "+00:00"))
            dt_end = datetime.fromisoformat(str(end).replace("Z", "+00:00"))
        except ValueError:
            continue
        lines.append("BEGIN:VEVENT")
        lines.append(f"UID:armapply-{uid}@armapply.local")
        lines.append(f"DTSTAMP:{now}")
        lines.append(f"DTSTART:{_ics_utc(dt_start)}")
        lines.append(f"DTEND:{_ics_utc(dt_end)}")
        lines.append(f"SUMMARY:{title}")
        if notes:
            lines.append(f"DESCRIPTION:{notes}")
        if ev.get("job_url"):
            lines.append(f"URL:{ev['job_url']}")
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"
