from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Source:
    paper_id: str
    title: str
    page: int
    text: str


@dataclass
class Question:
    text: str
    selection: str = ""
    sources: list[Source] = field(default_factory=list)
    image_png: bytes | None = None
    history: list[tuple[str, str]] = field(default_factory=list)
    external: bool = False


@dataclass
class Answer:
    text: str
    sources: list[Source]
    demo: bool = False
