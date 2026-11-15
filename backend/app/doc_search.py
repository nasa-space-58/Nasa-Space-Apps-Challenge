"""Deterministic keyword (BM25) search over the project's Markdown documentation.

The Q&A assistant uses this as a lightweight retrieval step. There is no
embedding model or vector store: the corpus is a few dozen files, so an
in-process lexical index is enough and keeps results reproducible.
"""

import math
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

# BM25 term-frequency saturation and length normalisation (Robertson & Zaragoza,
# "The Probabilistic Relevance Framework: BM25 and Beyond", 2009; common defaults).
BM25_K1 = 1.5
BM25_B = 0.75

_HEADING = re.compile(r"^(#{1,2})\s+(.*?)\s*#*\s*$")
_TOKEN = re.compile(r"[^\W_]+")


@dataclass(frozen=True)
class DocSection:
    """One level-1 or level-2 heading section of a Markdown file."""

    path: str  # repository-relative POSIX path, e.g. "docs/terrain-horizon.md"
    heading: str  # heading text without leading '#' characters
    text: str  # section body, without the heading line


def load_sections(docs_dir: Path) -> list[DocSection]:
    """Split every ``*.md`` file directly in ``docs_dir`` into heading sections.

    Files are read in sorted order as UTF-8. A ``#`` or ``##`` heading starts a
    new section; deeper headings stay inside their parent section. Text before
    the first heading becomes a section whose heading is the file stem. Sections
    whose body is blank are dropped. Raises ``FileNotFoundError`` when
    ``docs_dir`` does not exist or holds no Markdown file.
    """
    docs_dir = Path(docs_dir)
    if not docs_dir.is_dir():
        raise FileNotFoundError(f"Documentation directory not found: {docs_dir}")
    files = sorted(p for p in docs_dir.glob("*.md") if p.is_file())
    if not files:
        raise FileNotFoundError(f"No Markdown files in {docs_dir}")
    sections = []
    for file in files:
        rel = f"{docs_dir.name}/{file.name}"
        heading, lines, fence = file.stem, [], False
        for line in file.read_text(encoding="utf-8").splitlines():
            if line.lstrip().startswith(("```", "~~~")):
                fence = not fence
            match = None if fence else _HEADING.match(line)
            if match:
                sections.append(DocSection(rel, heading, "\n".join(lines).strip()))
                heading, lines = match.group(2), []
            else:
                lines.append(line)
        sections.append(DocSection(rel, heading, "\n".join(lines).strip()))
    return [s for s in sections if s.text]


def tokenize(text: str) -> list[str]:
    """Lower-case alphanumeric tokens (Unicode letters and digits), in order."""
    return _TOKEN.findall(text.lower())


def search(sections: list[DocSection], query: str, limit: int = 4) -> list[DocSection]:
    """Return up to ``limit`` sections ranked by BM25 score over heading + body.

    Sections with a zero score are excluded. Equal scores are ordered by
    ``(path, heading)`` so the result is deterministic. Raises ``ValueError``
    when ``limit`` is below 1 or the query contains no tokens.
    """
    if limit < 1:
        raise ValueError("limit must be at least 1")
    terms = sorted(set(tokenize(query)))
    if not terms:
        raise ValueError("Query contains no searchable words")
    if not sections:
        return []
    counts = [Counter(tokenize(f"{s.heading}\n{s.text}")) for s in sections]
    lengths = [sum(c.values()) for c in counts]
    average = sum(lengths) / len(lengths) or 1.0
    total = len(sections)
    scored = []
    for section, count, length in zip(sections, counts, lengths):
        score = 0.0
        for term in terms:
            tf = count.get(term, 0)
            if not tf:
                continue
            df = sum(1 for c in counts if term in c)
            # Lucene-style IDF stays positive for terms present in every section.
            idf = math.log(1 + (total - df + 0.5) / (df + 0.5))
            norm = BM25_K1 * (1 - BM25_B + BM25_B * length / average)
            score += idf * tf * (BM25_K1 + 1) / (tf + norm)
        if score > 0:
            scored.append((-score, section.path, section.heading, section))
    scored.sort(key=lambda item: item[:3])
    return [item[3] for item in scored[:limit]]
