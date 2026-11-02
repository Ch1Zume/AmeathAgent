"""Local record of service-reported model usage, shared by every entry point.

Only counts, the model name and a short purpose label are kept: no prompts,
replies, keys or reasoning. A request whose response carried no usage is still
counted as a request with unknown tokens rather than as zero.
"""
from __future__ import annotations

import sqlite3
import threading
from contextlib import closing
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

CATEGORIES = {
    "chat": "对话",
    "game": "游戏",
    "agent": "Agent 工作",
    "companion": "陪伴与观察",
    "memory": "后台整理",
    "other": "其他",
}
PERIODS = {"day": "日", "week": "周", "month": "月"}


def _int(value) -> int:
    return value if type(value) is int and value >= 0 else 0


def usage_numbers(usage: dict) -> dict | None:
    """Normalize Chat Completions, DeepSeek and Responses usage; None when nothing was reported."""
    if not isinstance(usage, dict) or not usage:
        return None
    prompt = _int(usage.get("prompt_tokens", usage.get("input_tokens")))
    completion = _int(usage.get("completion_tokens", usage.get("output_tokens")))
    total = _int(usage.get("total_tokens")) or prompt + completion
    if not total:
        return None
    prompt_details = usage.get("prompt_tokens_details") or usage.get("input_tokens_details") or {}
    output_details = usage.get("completion_tokens_details") or usage.get("output_tokens_details") or {}
    cached = _int(usage.get("prompt_cache_hit_tokens")) or _int(prompt_details.get("cached_tokens")
                                                                if isinstance(prompt_details, dict) else 0)
    reasoning = _int(output_details.get("reasoning_tokens") if isinstance(output_details, dict) else 0)
    return {"prompt": prompt, "completion": completion, "total": total, "cached": cached, "reasoning": reasoning}


@dataclass
class Totals:
    requests: int = 0
    unknown: int = 0
    prompt: int = 0
    completion: int = 0
    total: int = 0
    cached: int = 0
    reasoning: int = 0

    def add(self, row):
        self.requests += row["requests"]
        self.unknown += row["unknown"] or 0
        for name in ("prompt", "completion", "total", "cached", "reasoning"):
            setattr(self, name, getattr(self, name) + (row[name] or 0))


@dataclass
class PeriodSummary:
    label: str
    start: date
    end: date  # exclusive
    totals: Totals = field(default_factory=Totals)
    by_category: dict[str, Totals] = field(default_factory=dict)
    by_detail: dict[tuple[str, str], Totals] = field(default_factory=dict)


def period_start(period: str, day: date) -> date:
    if period == "week":
        return day - timedelta(days=day.weekday())
    if period == "month":
        return day.replace(day=1)
    return day


def _next_start(period: str, start: date) -> date:
    if period == "week":
        return start + timedelta(days=7)
    if period == "month":
        return (start.replace(day=28) + timedelta(days=4)).replace(day=1)
    return start + timedelta(days=1)


def _previous_start(period: str, start: date) -> date:
    return period_start(period, start - timedelta(days=1))


def _label(period: str, start: date) -> str:
    if period == "week":
        return f"{start.month}/{start.day} 周"
    if period == "month":
        return f"{start.year}.{start.month:02d}"
    return f"{start.month}/{start.day}"


class UsageLedger:
    def __init__(self, directory: Path):
        self.path = directory / "usage.db"
        self._lock = threading.Lock()
        self._ready = False

    def _connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        if not self._ready:
            db.execute("""CREATE TABLE IF NOT EXISTS usage (
                id INTEGER PRIMARY KEY, created TEXT NOT NULL, day TEXT NOT NULL,
                category TEXT NOT NULL, detail TEXT NOT NULL, model TEXT NOT NULL,
                known INTEGER NOT NULL, prompt INTEGER NOT NULL, completion INTEGER NOT NULL,
                total INTEGER NOT NULL, cached INTEGER NOT NULL, reasoning INTEGER NOT NULL)""")
            db.execute("CREATE INDEX IF NOT EXISTS usage_day ON usage(day)")
            self._ready = True
        return db

    def record(self, category: str, detail: str, model: str, usage: dict, *, when: datetime | None = None):
        category = category if category in CATEGORIES else "other"
        numbers = usage_numbers(usage)
        when = when or datetime.now(timezone.utc)
        local = when.astimezone()
        values = numbers or {"prompt": 0, "completion": 0, "total": 0, "cached": 0, "reasoning": 0}
        with self._lock, closing(self._connect()) as db, db:
            db.execute("INSERT INTO usage(created, day, category, detail, model, known, prompt, completion, total, cached, reasoning)"
                       " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                       (when.isoformat(), local.date().isoformat(), category, str(detail)[:40], str(model)[:80],
                        1 if numbers else 0, values["prompt"], values["completion"], values["total"],
                        values["cached"], values["reasoning"]))

    def _rows(self, start: date, end: date):
        if not self.path.exists():
            return []
        with self._lock, closing(self._connect()) as db:
            return db.execute(
                "SELECT day, category, detail, COUNT(*) AS requests, SUM(1 - known) AS unknown,"
                " SUM(prompt) AS prompt, SUM(completion) AS completion, SUM(total) AS total,"
                " SUM(cached) AS cached, SUM(reasoning) AS reasoning FROM usage"
                " WHERE day >= ? AND day < ? GROUP BY day, category, detail",
                (start.isoformat(), end.isoformat())).fetchall()

    def history(self, period: str, count: int, today: date | None = None) -> list[PeriodSummary]:
        """The last `count` periods, oldest first; the final one contains today."""
        today = today or datetime.now().astimezone().date()
        starts = [period_start(period, today)]
        while len(starts) < count:
            starts.insert(0, _previous_start(period, starts[0]))
        summaries = [PeriodSummary(_label(period, s), s, _next_start(period, s)) for s in starts]
        for row in self._rows(summaries[0].start, summaries[-1].end):
            day = date.fromisoformat(row["day"])
            summary = next(s for s in summaries if s.start <= day < s.end)
            summary.totals.add(row)
            summary.by_category.setdefault(row["category"], Totals()).add(row)
            summary.by_detail.setdefault((row["category"], row["detail"]), Totals()).add(row)
        return summaries
