from __future__ import annotations

import sqlite3
import json
from pathlib import Path


class Notebook:
    def __init__(self, path: Path):
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.execute("""CREATE TABLE IF NOT EXISTS cards (
            id INTEGER PRIMARY KEY, paper_id TEXT NOT NULL, title TEXT NOT NULL,
            page INTEGER NOT NULL, selection TEXT NOT NULL, explanation TEXT NOT NULL,
            created TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            sources TEXT NOT NULL DEFAULT '[]')""")
        if "sources" not in {row[1] for row in self.db.execute("PRAGMA table_info(cards)")}:
            self.db.execute("ALTER TABLE cards ADD COLUMN sources TEXT NOT NULL DEFAULT '[]'")
        self.db.commit()

    def save(self, paper_id: str, title: str, page: int, selection: str, explanation: str, sources=None):
        references = [{"paper_id": s.paper_id, "title": s.title, "page": s.page} for s in (sources or [])]
        self.db.execute("INSERT INTO cards(paper_id,title,page,selection,explanation,sources) VALUES(?,?,?,?,?,?)",
                        (paper_id, title, page, selection, explanation, json.dumps(references, ensure_ascii=False)))
        self.db.commit()

    def cards(self):
        return list(self.db.execute("SELECT * FROM cards ORDER BY id DESC"))

    def delete(self, card_id: int):
        self.db.execute("DELETE FROM cards WHERE id=?", (card_id,))
        self.db.commit()

    def close(self):
        self.db.close()
