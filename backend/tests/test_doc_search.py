"""Documentation index used by the Q&A assistant: splitting, tokens and BM25 ranking."""

from pathlib import Path

import pytest

from app.doc_search import DocSection, load_sections, search, tokenize


def write_docs(tmp_path: Path, files: dict[str, str]) -> Path:
    docs = tmp_path / "docs"
    docs.mkdir()
    for name, text in files.items():
        (docs / name).write_text(text, encoding="utf-8")
    return docs


def test_sections_split_on_level_one_and_two_headings(tmp_path):
    docs = write_docs(
        tmp_path,
        {
            "terrain.md": "# Terrain\nIntro text.\n\n## Horizon\nRay casting.\n### Detail\nInner.\n\n## Empty\n\n",
        },
    )
    sections = load_sections(docs)
    assert sections == [
        DocSection("docs/terrain.md", "Terrain", "Intro text."),
        DocSection("docs/terrain.md", "Horizon", "Ray casting.\n### Detail\nInner."),
    ]


def test_preamble_uses_file_stem_and_files_are_sorted(tmp_path):
    docs = write_docs(
        tmp_path,
        {
            "b.md": "# Bee\nSecond file.",
            "a.md": "Preamble before any heading.\n# Ay\nFirst file.",
            "notes.txt": "# Ignored\nNot markdown.",
        },
    )
    sections = load_sections(docs)
    assert [(s.path, s.heading) for s in sections] == [
        ("docs/a.md", "a"),
        ("docs/a.md", "Ay"),
        ("docs/b.md", "Bee"),
    ]
    assert sections[0].text == "Preamble before any heading."


def test_utf8_content_is_preserved(tmp_path):
    docs = write_docs(tmp_path, {"tr.md": "# Güneş\nDünya görünürlüğü."})
    assert load_sections(docs)[0] == DocSection(
        "docs/tr.md", "Güneş", "Dünya görünürlüğü."
    )


def test_missing_or_empty_docs_directory_fails_loudly(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_sections(tmp_path / "missing")
    empty = write_docs(tmp_path, {"readme.txt": "no markdown"})
    with pytest.raises(FileNotFoundError):
        load_sections(empty)


def test_tokenize_lowercases_and_splits_on_punctuation():
    assert tokenize("Sun-Earth visibility, 48 h DELAY!") == [
        "sun",
        "earth",
        "visibility",
        "48",
        "h",
        "delay",
    ]
    assert tokenize("GÜNEŞ ışığı") == ["güneş", "ışığı"]
    assert tokenize("  ...  ") == []


SECTIONS = [
    DocSection("docs/a.md", "Horizon", "terrain horizon ray casting over LOLA terrain"),
    DocSection(
        "docs/b.md", "Ephemeris", "JPL Horizons observer ephemerides for Sun and Earth"
    ),
    DocSection("docs/c.md", "Risk", "seeded Monte Carlo landing risk"),
    DocSection("docs/d.md", "Fuel", "propellant margin at touchdown"),
]


def test_search_ranks_the_most_relevant_section_first():
    result = search(SECTIONS, "terrain horizon")
    assert result[0] == SECTIONS[0]


def test_search_excludes_sections_without_matching_terms():
    result = search(SECTIONS, "monte carlo")
    assert result == [SECTIONS[2]]
    assert search(SECTIONS, "quasar") == []


def test_heading_terms_count_toward_the_score():
    result = search(SECTIONS, "fuel")
    assert result == [SECTIONS[3]]


def test_search_respects_limit_and_breaks_ties_deterministically():
    tied = [
        DocSection("docs/z.md", "Same", "sunlight"),
        DocSection("docs/a.md", "Same", "sunlight"),
        DocSection("docs/a.md", "Other", "sunlight"),
    ]
    assert search(tied, "sunlight", limit=2) == [tied[2], tied[1]]
    assert search(tied, "sunlight", limit=10) == [tied[2], tied[1], tied[0]]


def test_rarer_terms_weigh_more_than_common_terms():
    sections = [
        DocSection("docs/a.md", "One", "sun sun sun"),
        DocSection("docs/b.md", "Two", "sun earthset"),
        DocSection("docs/c.md", "Three", "sun"),
    ]
    assert search(sections, "sun earthset")[0] == sections[1]


def test_search_rejects_empty_query_and_bad_limit():
    with pytest.raises(ValueError):
        search(SECTIONS, "")
    with pytest.raises(ValueError):
        search(SECTIONS, " ?! ")
    with pytest.raises(ValueError):
        search(SECTIONS, "terrain", limit=0)


def test_search_is_deterministic():
    assert search(SECTIONS, "sun earth terrain") == search(
        SECTIONS, "sun earth terrain"
    )


def test_real_project_documentation_is_indexed():
    docs = Path(__file__).resolve().parents[2] / "docs"
    sections = load_sections(docs)
    assert len(sections) > 20
    assert all(s.path.startswith("docs/") and s.text.strip() for s in sections)
    top = search(sections, "terrain horizon ray LOLA", limit=4)
    assert any(s.path == "docs/terrain-horizon.md" for s in top)
