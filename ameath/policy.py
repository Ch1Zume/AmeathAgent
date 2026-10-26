from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class AttentionPolicy:
    enabled: bool = False
    cooldown: float = 120
    dwell: float = 35
    last_prompt: float = float("-inf")
    page_since: float = field(default_factory=time.monotonic)
    page_key: str = ""
    seen: set[str] = field(default_factory=set)
    muted_until: float = 0

    def visit(self, key: str, now: float | None = None):
        if key != self.page_key:
            self.page_key = key
            self.page_since = time.monotonic() if now is None else now

    def candidate(self, active: bool, busy: bool, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        return bool(self.enabled and active and not busy and self.page_key
                    and self.page_key not in self.seen and now >= self.muted_until
                    and now - self.page_since >= self.dwell and now - self.last_prompt >= self.cooldown)

    def prompted(self, now: float | None = None):
        self.last_prompt = time.monotonic() if now is None else now
        self.seen.add(self.page_key)

    def feedback(self, useful: bool, now: float | None = None):
        now = time.monotonic() if now is None else now
        if useful:
            self.cooldown = max(60, self.cooldown * 0.9)
        else:
            self.cooldown = min(1800, self.cooldown * 1.5)
            self.muted_until = now + self.cooldown

    def pause(self):
        self.enabled = False

    def resume(self, now: float | None = None):
        self.enabled = True
        self.page_since = time.monotonic() if now is None else now
