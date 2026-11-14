"""Deterministic keyword (BM25) search over the project's Markdown documentation.

The Q&A assistant uses this as a lightweight retrieval step. There is no
embedding model or vector store: the corpus is a few dozen files, so an
in-process lexical index is enough and keeps results reproducible.
"""

from dataclasses import dataclass
from pathlib import Path

# BM25 term-frequency saturation and length normalisation (Robertson & Zaragoza,
# "The Probabilistic Relevance Framework: BM25 and Beyond", 2009; common defaults).
BM25_K1 = 1.5
BM25_B = 0.75


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
    raise NotImplementedError


def tokenize(text: str) -> list[str]:
    """Lower-case alphanumeric tokens (Unicode letters and digits), in order."""
    raise NotImplementedError


def search(sections: list[DocSection], query: str, limit: int = 4) -> list[DocSection]:
    """Return up to ``limit`` sections ranked by BM25 score over heading + body.

    Sections with a zero score are excluded. Equal scores are ordered by
    ``(path, heading)`` so the result is deterministic. Raises ``ValueError``
    when ``limit`` is below 1 or the query contains no tokens.
    """
    raise NotImplementedError
