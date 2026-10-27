"""Local persona cards and bounded, editable long-term facts for conversation."""
from __future__ import annotations

import json
import re
import sqlite3
from contextlib import contextmanager
from dataclasses import asdict, dataclass, fields
from datetime import datetime, timezone
from pathlib import Path

MAX_MEMORIES = 200
MAX_SHARED_EPISODES = 120
MAX_LIFE_THREADS = 30
MAX_PENDING_TURNS = 60
SENSITIVE = re.compile(r"api[ _-]?key|password|secret|access[ _-]?token|密码|口令|密钥|银行卡|身份证|私钥|验证码|sk-[a-z0-9_-]{8,}", re.I)


@dataclass(frozen=True)
class Persona:
    name: str = "Ameath"
    identity: str = "陪伴用户学习、工作和生活的桌面伙伴。"
    personality: str = "温和、好奇、坦诚，尊重用户的判断，有自己的思考。"
    speaking_style: str = "使用自然简洁的中文，认真回应，不刻意卖萌或说教；需要时再详细解释。"
    relationship: str = "像平等的朋友一样相处，支持用户，也可以有理有据地表达不同看法。"
    background: str = "喜欢和用户一起理解新概念、阅读和探索游戏。"

    @classmethod
    def from_dict(cls, data: dict):
        if not isinstance(data, dict) or data.get("version", 1) != 1:
            raise ValueError("人设卡格式或版本不受支持。")
        defaults = cls()
        values = {}
        for field in fields(cls):
            value = data.get(field.name, getattr(defaults, field.name))
            maximum = 40 if field.name == "name" else 2000
            if not isinstance(value, str) or len(value) > maximum:
                raise ValueError(f"人设字段 {field.name} 需要为 {maximum} 字以内的文字。")
            values[field.name] = value.strip()
        if not values["name"]:
            raise ValueError("请填写角色名称。")
        return cls(**values)

    def to_dict(self):
        return {"version": 1, **asdict(self)}


class PersonaCard:
    def __init__(self, directory: Path):
        self.path = directory / "persona.json"

    def load(self) -> Persona:
        try:
            return self.read(self.path)
        except (OSError, ValueError, TypeError):
            return Persona()

    @staticmethod
    def read(path: Path) -> Persona:
        if path.stat().st_size > 32000:
            raise ValueError("请选择 32 KB 以内的人设卡。")
        return Persona.from_dict(json.loads(path.read_text(encoding="utf-8-sig")))

    def save(self, persona: Persona):
        persona = Persona.from_dict(persona.to_dict())
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(persona.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self.path)


def valid_memory(key: str, value: str) -> bool:
    return (isinstance(key, str) and isinstance(value, str) and 0 < len(key.strip()) <= 80
            and 0 < len(value.strip()) <= 360 and not SENSITIVE.search(key + " " + value))


def memory_updates(data) -> tuple[tuple[str, str], ...]:
    if not isinstance(data, list):
        return ()
    updates = []
    for item in data[:3]:
        if isinstance(item, dict):
            key, value = item.get("key"), item.get("value")
            if valid_memory(key, value):
                updates.append((key.strip(), value.strip()))
    return tuple(updates)


class CompanionMemory:
    """Each operation owns its connection, so model workers never share a SQLite handle."""
    def __init__(self, directory: Path):
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory / "companion.sqlite3"
        with self._connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS memories (id INTEGER PRIMARY KEY, key TEXT NOT NULL UNIQUE COLLATE NOCASE, value TEXT NOT NULL, source TEXT NOT NULL, updated TEXT NOT NULL)")
            # Shared episodes are real conversations; life threads are the character's
            # own invented stories. Neither is a user fact, so they live apart from memories.
            db.execute("CREATE TABLE IF NOT EXISTS episodes (id INTEGER PRIMARY KEY, kind TEXT NOT NULL, topic TEXT NOT NULL, summary TEXT NOT NULL, created TEXT NOT NULL, updated TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS pending_turns (id INTEGER PRIMARY KEY, user TEXT NOT NULL, assistant TEXT NOT NULL, created TEXT NOT NULL)")

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def all(self) -> list[dict]:
        with self._connect() as db:
            return [dict(row) for row in db.execute("SELECT * FROM memories ORDER BY updated DESC, id DESC")]

    def remember(self, key: str, value: str, *, source="manual", identifier: int | None = None):
        if not valid_memory(key, value):
            raise ValueError("记忆名称需为 1–80 字，内容需为 1–360 字；请勿保存密码、密钥或证件信息。")
        key, value = key.strip(), value.strip()
        updated = datetime.now(timezone.utc).isoformat(timespec="microseconds")
        with self._connect() as db:
            existing = db.execute("SELECT id FROM memories WHERE key = ?", (key,)).fetchone()
            if identifier is None and existing is None and db.execute("SELECT COUNT(*) FROM memories").fetchone()[0] >= MAX_MEMORIES:
                raise ValueError("长期记忆已达到 200 条，请先整理或删除一些内容。")
            if identifier is not None:
                try:
                    cursor = db.execute("UPDATE memories SET key = ?, value = ?, source = 'manual', updated = ? WHERE id = ?",
                                        (key, value, updated, identifier))
                except sqlite3.IntegrityError:
                    raise ValueError("已有同名记忆，请修改名称或编辑已有条目。") from None
                if cursor.rowcount == 0:
                    raise ValueError("这条记忆已被删除，请重新选择。")
            else:
                db.execute("INSERT INTO memories (key,value,source,updated) VALUES (?,?,?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value,source=excluded.source,updated=excluded.updated",
                           (key, value, "auto" if source == "auto" else "manual", updated))

    def delete(self, identifier: int):
        with self._connect() as db:
            db.execute("DELETE FROM memories WHERE id = ?", (identifier,))

    def relevant(self, query: str, limit=12) -> list[dict]:
        terms = _tokens(query)
        memories = self.all()
        memories.sort(key=lambda item: len(terms & _tokens(item["key"] + " " + item["value"])), reverse=True)
        return [{"key": item["key"], "value": item["value"]} for item in memories[:max(0, min(12, limit))]]

    # Conversation turns wait here only until they are summarized into episodes.
    def log_turn(self, user: str, assistant: str):
        if SENSITIVE.search(user + " " + assistant):
            return
        created = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self._connect() as db:
            db.execute("INSERT INTO pending_turns (user,assistant,created) VALUES (?,?,?)",
                       (user[:2000], assistant[:2000], created))
            db.execute("DELETE FROM pending_turns WHERE id NOT IN (SELECT id FROM pending_turns ORDER BY id DESC LIMIT ?)",
                       (MAX_PENDING_TURNS,))

    def pending_turns(self) -> list[dict]:
        with self._connect() as db:
            return [dict(row) for row in db.execute("SELECT * FROM pending_turns ORDER BY id")]

    def clear_pending(self, through: int | None = None):
        with self._connect() as db:
            if through is None:
                db.execute("DELETE FROM pending_turns")
            else:
                db.execute("DELETE FROM pending_turns WHERE id <= ?", (through,))

    def episodes(self, kind: str | None = None) -> list[dict]:
        with self._connect() as db:
            if kind is None:
                rows = db.execute("SELECT * FROM episodes ORDER BY updated DESC, id DESC")
            else:
                rows = db.execute("SELECT * FROM episodes WHERE kind = ? ORDER BY updated DESC, id DESC", (kind,))
            return [dict(row) for row in rows]

    def add_episodes(self, shared: list[str], life: list[tuple[str, str]], *, when: str | None = None):
        now = datetime.now(timezone.utc).isoformat(timespec="microseconds")
        when = when or now
        with self._connect() as db:
            for summary in shared:
                if valid_episode("对话", summary):
                    db.execute("INSERT INTO episodes (kind,topic,summary,created,updated) VALUES ('shared','对话',?,?,?)",
                               (summary.strip(), when, now))
            for topic, summary in life:
                if not valid_episode(topic, summary):
                    continue
                topic, summary = topic.strip(), summary.strip()
                existing = db.execute("SELECT id FROM episodes WHERE kind = 'life' AND topic = ? COLLATE NOCASE", (topic,)).fetchone()
                if existing:
                    db.execute("UPDATE episodes SET summary = ?, updated = ? WHERE id = ?", (summary, now, existing["id"]))
                else:
                    db.execute("INSERT INTO episodes (kind,topic,summary,created,updated) VALUES ('life',?,?,?,?)",
                               (topic, summary, when, now))
            for kind, limit in (("shared", MAX_SHARED_EPISODES), ("life", MAX_LIFE_THREADS)):
                db.execute("DELETE FROM episodes WHERE kind = ? AND id NOT IN (SELECT id FROM episodes WHERE kind = ? ORDER BY updated DESC, id DESC LIMIT ?)",
                           (kind, kind, limit))

    def delete_episode(self, identifier: int):
        with self._connect() as db:
            db.execute("DELETE FROM episodes WHERE id = ?", (identifier,))

    def clear_episodes(self):
        with self._connect() as db:
            db.execute("DELETE FROM episodes")
            db.execute("DELETE FROM pending_turns")

    def episode_context(self, query: str, *, shared=6, life=4) -> dict:
        """Recent conversations plus the ones that share words with this message."""
        records = self.episodes("shared")
        terms = _tokens(query)
        chosen = records[:max(0, shared - 2)]
        related = sorted(records[len(chosen):], key=lambda item: len(terms & _tokens(item["summary"])), reverse=True)
        chosen += [item for item in related[:2] if terms & _tokens(item["summary"])]
        chosen.sort(key=lambda item: item["created"])
        return {"shared": [{"date": item["created"][:10], "summary": item["summary"]} for item in chosen],
                "life": [{"topic": item["topic"], "summary": item["summary"]} for item in self.episodes("life")[:life]]}


def valid_episode(topic: str, summary: str) -> bool:
    return (isinstance(topic, str) and isinstance(summary, str) and 0 < len(topic.strip()) <= 24
            and 0 < len(summary.strip()) <= 200 and not SENSITIVE.search(topic + " " + summary))


def _tokens(text: str) -> set[str]:
    parts = set(re.findall(r"[a-z0-9_]{2,}", text.lower()))
    for phrase in re.findall(r"[一-鿿]+", text):
        parts.update(phrase[index:index + 2] for index in range(len(phrase) - 1))
    return parts
