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
        def tokens(text):
            parts = set(re.findall(r"[a-z0-9_]{2,}", text.lower()))
            for phrase in re.findall(r"[\u4e00-\u9fff]+", text):
                parts.update(phrase[index:index + 2] for index in range(len(phrase) - 1))
            return parts

        terms = tokens(query)
        memories = self.all()
        memories.sort(key=lambda item: len(terms & tokens(item["key"] + " " + item["value"])), reverse=True)
        return [{"key": item["key"], "value": item["value"]} for item in memories[:max(0, min(12, limit))]]
