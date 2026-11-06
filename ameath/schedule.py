"""A small local schedule: events with an optional reminder and simple repetition.

Times are naive local times ("YYYY-MM-DD HH:MM"); the desktop app runs in the user's time zone.
"""
from __future__ import annotations

import re
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from .memory import SENSITIVE

TIME_FORMAT = "%Y-%m-%d %H:%M"
REPEATS = {"none": "不重复", "daily": "每天", "weekdays": "工作日", "weekly": "每周"}
MAX_EVENTS = 500
# A reminder that was missed (app closed) is still shown if the event started at most this long ago.
LATE_WINDOW = timedelta(hours=2)


@dataclass(frozen=True)
class Event:
    id: int
    title: str
    start: datetime
    remind_minutes: int = 10
    repeat: str = "none"
    note: str = ""
    done: bool = False
    reminded: bool = False
    source: str = "manual"

    @property
    def remind_at(self) -> datetime:
        return self.start - timedelta(minutes=self.remind_minutes)

    def to_context(self) -> dict:
        row = {"title": self.title, "start": self.start.strftime(TIME_FORMAT)}
        if self.repeat != "none":
            row["repeat"] = REPEATS[self.repeat]
        if self.note:
            row["note"] = self.note[:80]
        return row


def parse_time(value) -> datetime | None:
    if not isinstance(value, str):
        return None
    text = value.strip().replace("T", " ")
    text = re.sub(r"(:\d\d):\d\d(\.\d+)?$", r"\1", re.sub(r"([+-]\d\d:\d\d|Z)$", "", text))
    try:
        return datetime.strptime(text, TIME_FORMAT)
    except ValueError:
        try:
            return datetime.strptime(text, "%Y-%m-%d").replace(hour=9)
        except ValueError:
            return None


def next_occurrence(start: datetime, repeat: str, after: datetime) -> datetime | None:
    if repeat == "none":
        return None
    step = timedelta(weeks=1) if repeat == "weekly" else timedelta(days=1)
    value = start + step
    while value <= after or (repeat == "weekdays" and value.weekday() >= 5):
        value += step
    return value


def valid_event(title: str, note: str = "") -> bool:
    return (isinstance(title, str) and 0 < len(title.strip()) <= 80 and isinstance(note, str) and len(note) <= 400
            and not SENSITIVE.search(title + " " + note))


def schedule_updates(data, now: datetime | None = None) -> tuple[dict, ...]:
    """Validated events proposed by the chat model; at most three, never in the past."""
    if not isinstance(data, list):
        return ()
    now = now or datetime.now()
    result = []
    for item in data[:3]:
        if not isinstance(item, dict):
            continue
        title, note = item.get("title"), item.get("note", "")
        start = parse_time(item.get("start"))
        if start is None or not valid_event(title, note if isinstance(note, str) else ""):
            continue
        if start < now - timedelta(minutes=1):
            continue
        remind = item.get("remind_minutes", 10)
        remind = max(0, min(1440, remind)) if type(remind) is int else 10
        repeat = item.get("repeat") if item.get("repeat") in REPEATS else "none"
        result.append({"title": title.strip(), "start": start, "remind_minutes": remind, "repeat": repeat,
                       "note": note.strip() if isinstance(note, str) else ""})
    return tuple(result)


class Schedule:
    def __init__(self, directory: Path):
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory / "schedule.sqlite3"
        with self._connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, start TEXT NOT NULL,
                remind_minutes INTEGER NOT NULL DEFAULT 10, repeat TEXT NOT NULL DEFAULT 'none',
                note TEXT NOT NULL DEFAULT '', done INTEGER NOT NULL DEFAULT 0, reminded INTEGER NOT NULL DEFAULT 0,
                source TEXT NOT NULL DEFAULT 'manual', created TEXT NOT NULL)""")

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.path)
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def _event(row) -> Event:
        return Event(row[0], row[1], datetime.strptime(row[2], TIME_FORMAT), row[3], row[4], row[5],
                     bool(row[6]), bool(row[7]), row[8])

    COLUMNS = "id, title, start, remind_minutes, repeat, note, done, reminded, source"

    def add(self, title: str, start: datetime, *, remind_minutes: int = 10, repeat: str = "none", note: str = "",
            source: str = "manual") -> int:
        if not valid_event(title, note) or repeat not in REPEATS:
            raise ValueError("日程标题需在 80 字以内，不能包含密码、密钥等敏感信息。")
        with self._connect() as db:
            if db.execute("SELECT COUNT(*) FROM events").fetchone()[0] >= MAX_EVENTS:
                db.execute("DELETE FROM events WHERE id IN (SELECT id FROM events WHERE done = 1 ORDER BY start LIMIT 50)")
            cursor = db.execute(
                "INSERT INTO events (title, start, remind_minutes, repeat, note, source, created) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (title.strip(), start.strftime(TIME_FORMAT), max(0, min(1440, int(remind_minutes))), repeat, note.strip(),
                 source, datetime.now().strftime(TIME_FORMAT)))
            return cursor.lastrowid

    def update(self, event_id: int, title: str, start: datetime, *, remind_minutes: int, repeat: str, note: str = ""):
        if not valid_event(title, note) or repeat not in REPEATS:
            raise ValueError("日程标题需在 80 字以内，不能包含密码、密钥等敏感信息。")
        with self._connect() as db:
            db.execute("UPDATE events SET title=?, start=?, remind_minutes=?, repeat=?, note=?, reminded=0 WHERE id=?",
                       (title.strip(), start.strftime(TIME_FORMAT), max(0, min(1440, int(remind_minutes))), repeat,
                        note.strip(), event_id))

    def set_done(self, event_id: int, done: bool = True):
        with self._connect() as db:
            db.execute("UPDATE events SET done=? WHERE id=?", (int(done), event_id))

    def delete(self, event_id: int):
        with self._connect() as db:
            db.execute("DELETE FROM events WHERE id=?", (event_id,))

    def get(self, event_id: int) -> Event | None:
        with self._connect() as db:
            row = db.execute(f"SELECT {self.COLUMNS} FROM events WHERE id=?", (event_id,)).fetchone()
        return self._event(row) if row else None

    def events(self, start: datetime | None = None, end: datetime | None = None, *, include_done=True) -> list[Event]:
        query, args = f"SELECT {self.COLUMNS} FROM events WHERE 1=1", []
        if start is not None:
            query += " AND start >= ?"
            args.append(start.strftime(TIME_FORMAT))
        if end is not None:
            query += " AND start < ?"
            args.append(end.strftime(TIME_FORMAT))
        if not include_done:
            query += " AND done = 0"
        with self._connect() as db:
            return [self._event(row) for row in db.execute(query + " ORDER BY start, id", args)]

    def upcoming(self, now: datetime | None = None, days: int = 7, limit: int = 10) -> list[Event]:
        now = now or datetime.now()
        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return self.events(day_start, now + timedelta(days=days), include_done=False)[:limit]

    def due(self, now: datetime | None = None) -> list[Event]:
        """Events whose reminder time has come and that have not been reminded yet."""
        now = now or datetime.now()
        rows = self.events(now - LATE_WINDOW, now + timedelta(days=2), include_done=False)
        return [event for event in rows if not event.reminded and event.remind_at <= now]

    def mark_reminded(self, event: Event, now: datetime | None = None):
        """Repeating events move on to their next occurrence; others are flagged once."""
        now = now or datetime.now()
        following = next_occurrence(event.start, event.repeat, now)
        with self._connect() as db:
            if following is None:
                db.execute("UPDATE events SET reminded=1 WHERE id=?", (event.id,))
            else:
                db.execute("UPDATE events SET start=?, reminded=0 WHERE id=?", (following.strftime(TIME_FORMAT), event.id))

    def advance_repeats(self, now: datetime | None = None):
        """Repeating events left in the past (app was closed) move to their next occurrence."""
        now = now or datetime.now()
        for event in self.events(None, now - LATE_WINDOW, include_done=False):
            following = next_occurrence(event.start, event.repeat, now)
            if following is not None:
                with self._connect() as db:
                    db.execute("UPDATE events SET start=?, reminded=0 WHERE id=?", (following.strftime(TIME_FORMAT), event.id))
