from __future__ import annotations

import hashlib
import re
from pathlib import Path

import pymupdf

from .models import Source


def tokens(text: str) -> set[str]:
    latin = re.findall(r"[a-z][a-z0-9-]{2,}", text.lower())
    chinese = re.findall(r"[\u4e00-\u9fff]+", text)
    return set(latin) | {part[i:i + 2] for part in chinese for i in range(len(part) - 1)}


class Paper:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        document = pymupdf.open(self.path)
        try:
            if document.needs_pass:
                raise ValueError("这份 PDF 有密码保护，请先打开解密后的副本。")
            if not document.page_count:
                raise ValueError("这份 PDF 没有可阅读的页面。")
            self.title = (document.metadata or {}).get("title") or self.path.stem
            self.id = hashlib.sha256(self.path.read_bytes()).hexdigest()[:24]
            self.pages = [page.get_text("text", sort=True) for page in document]
            self.sizes = [(page.rect.width, page.rect.height) for page in document]
            self._tokens = [tokens(text) for text in self.pages]
        except Exception:
            document.close()
            raise
        self.document = document

    @property
    def count(self):
        return len(self.pages)

    def close(self):
        self.document.close()

    def text_in_rect(self, page: int, rect: tuple[float, float, float, float]) -> str:
        return self.document[page].get_text("text", clip=pymupdf.Rect(rect), sort=True).strip()

    def image(self, page: int, scale: float = 1.3):
        return self.document[page].get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False)

    def context(self, query: str, page: int, budget: int = 14000) -> list[Source]:
        """Current page first, then lexical evidence; bound every request's context."""
        query_tokens = tokens(query)
        rankings = sorted(range(self.count), key=lambda i: (-len(query_tokens & self._tokens[i]), abs(i - page)))
        candidates = [page] + [i for i in rankings if i != page and query_tokens & self._tokens[i]][:3]
        if len(candidates) == 1 and page > 0:
            candidates.append(page - 1)
        sources = []
        for i in candidates:
            if budget <= 0:
                break
            text = self.pages[i][:min(5000, budget)].strip()
            if text:
                sources.append(Source(self.id, self.title, i + 1, text))
                budget -= len(text)
        return sources


def make_sample(path: Path) -> Path:
    if path.exists():
        return path
    doc = pymupdf.open()
    contents = [
        ("Attention, one idea at a time", "01 / INTUITION", [
            ("A reading note, made for Ameath", "This is an original teaching example, not a published research paper. Use it to try selecting a paragraph and asking your companion to explain it."),
            ("1. What is attention?", "Attention builds a context-dependent summary of a sequence. Instead of treating every token equally, it assigns a weight to each value vector and takes their weighted sum. The weights depend on how well a query matches each key."),
            ("2. Query, key, and value", "A query represents the information the current position is looking for. A key represents how another position can be matched. A value contains the information that position contributes. Think of a library: a question is the query, catalog entries are keys, and the books are values."),
            ("Try this", "Drag a rectangle over the attention paragraph, then choose Explain. Ameath will connect the selected text to the rest of this note. The local demonstration uses fixed concept cards; connect a model for open-ended explanations."),
        ]),
        ("From a score to a useful summary", "02 / EQUATION", [
            ("3. Scaled dot-product attention", "Attention(Q, K, V) = softmax(QK^T / sqrt(d_k))V"),
            ("Why divide by the square root?", "The dot product of high-dimensional query and key vectors can have a large variance. Dividing by sqrt(d_k) moderates the scale of the scores before softmax. This helps avoid very sharp distributions caused only by the dimension."),
            ("4. What softmax does", "Softmax converts real-valued scores into positive weights that sum to one. Larger scores receive larger weights. A weighted sum then combines value vectors into a representation suited to the query."),
            ("An example", "Suppose two scores are 0 and log(3). Their softmax weights are 1/4 and 3/4. For values 2 and 6, the weighted sum is 0.25 * 2 + 0.75 * 6 = 5. These numbers illustrate weighting, not a complete language model."),
        ]),
        ("Connect the ideas", "03 / REFLECTION", [
            ("5. Self-attention", "In self-attention, queries, keys, and values are derived from the same input sequence, usually through different learned linear projections. This lets positions exchange information within the sequence."),
            ("6. Multi-head attention", "Multiple attention heads use separate projections. Their outputs are combined so that the representation can use several learned relationships. A head does not necessarily correspond to a human-interpretable linguistic role."),
            ("Questions worth asking", "How does a query differ from a key? Why can large raw scores make softmax too sharp? What changes when queries come from one sequence and keys and values come from another?"),
            ("Keep a concept card", "Save an explanation you want to revisit. A saved card keeps its page reference. Mark an explanation as useful to give feedback, or choose Later to reduce interruptions. Your companion should support your attention, not compete with it."),
        ]),
    ]
    for index, (title, kicker, sections) in enumerate(contents):
        page = doc.new_page(width=595, height=842)
        page.draw_rect(pymupdf.Rect(0, 0, 595, 9), color=None, fill=(0.46, 0.35, 0.76))
        page.insert_text((48, 55), "A M E A T H   /   F I E L D   N O T E S", fontsize=10, color=(0.45, 0.37, 0.65))
        page.insert_text((48, 100), title, fontsize=23, fontname="hebo", color=(0.13, 0.12, 0.20))
        page.insert_text((48, 128), kicker, fontsize=9, color=(0.5, 0.5, 0.56))
        y = 177
        for heading, body in sections:
            page.insert_text((48, y), heading, fontsize=13, fontname="hebo", color=(0.28, 0.22, 0.43))
            rect = pymupdf.Rect(48, y + 14, 545, y + 132)
            remaining = page.insert_textbox(rect, body, fontsize=11.5, lineheight=1.6, color=(0.23, 0.24, 0.30))
            if remaining < 0:
                raise ValueError("Sample page overflow")
            y += 146
        page.draw_line((48, 782), (545, 782), color=(0.88, 0.87, 0.92))
        page.insert_text((48, 803), "ORIGINAL DEMO MATERIAL  /  NO NETWORK REQUIRED", fontsize=8, color=(0.55, 0.53, 0.61))
        page.insert_text((530, 803), str(index + 1), fontsize=9, color=(0.45, 0.37, 0.65))
    doc.set_metadata({"title": "Attention, one idea at a time", "author": "Ameath demo"})
    doc.save(path)
    doc.close()
    return path
