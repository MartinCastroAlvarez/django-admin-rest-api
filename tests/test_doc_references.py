"""Guard against dangling documentation references in shipped source (#54).

The package source frequently cites design docs by path (``docs/api-contract.md``,
``SECURITY.md``) and by section (``§5.2``). When a doc is renamed or a section is
removed, those cites silently rot. This test fails the build when source cites a
``*.md`` file that does not exist, or a ``docs/api-contract.md`` ``§N``/``§N.M``
section heading that the contract does not contain.

Scope is intentionally narrow (keep it simple): ``.md`` files cited anywhere under
the package, and numbered ``§`` sections of ``docs/api-contract.md`` (the only doc
that uses numbered headings).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
PACKAGE_ROOT = REPO_ROOT / "django_admin_rest_api"
CONTRACT = REPO_ROOT / "docs" / "api-contract.md"

# ``foo/bar.md`` or ``../foo.md`` — captured wherever it appears in source text.
_MD_RE = re.compile(r"[\w./-]+\.md")
# A ``§N`` / ``§N.M`` citation that immediately follows an api-contract mention.
_CONTRACT_SECTION_RE = re.compile(r"§(\d+(?:\.\d+)*)")
# Headings in the contract: ``## §1. ...`` or ``### 1.2 ...``.
_HEADING_RE = re.compile(r"^#{1,6}\s+§?(\d+(?:\.\d+)*)\b")


def _source_files() -> list[Path]:
    files: list[Path] = []
    for pattern in ("**/*.py", "**/*.md"):
        files.extend(p for p in PACKAGE_ROOT.glob(pattern) if "migrations" not in p.parts)
    return sorted(files)


def _contract_sections() -> set[str]:
    sections: set[str] = set()
    for line in CONTRACT.read_text(encoding="utf-8").splitlines():
        match = _HEADING_RE.match(line)
        if match:
            sections.add(match.group(1))
    return sections


def test_cited_markdown_files_exist() -> None:
    """Every ``*.md`` path cited in package source resolves to a real file."""
    missing: list[str] = []
    for path in _source_files():
        text = path.read_text(encoding="utf-8")
        for raw in _MD_RE.findall(text):
            cite = raw.lstrip("/")  # ``/docs/...`` is repo-root-relative.
            candidates = [
                REPO_ROOT / cite,
                path.parent / raw,  # relative links like ``../README.md``.
            ]
            if not any(c.exists() for c in candidates):
                missing.append(f"{path.relative_to(REPO_ROOT)}: '{raw}'")
    assert not missing, "Dangling .md references in source:\n" + "\n".join(missing)


def test_cited_contract_sections_exist() -> None:
    """Every ``docs/api-contract.md §N`` cite points at a heading that exists."""
    sections = _contract_sections()
    assert sections, "Failed to parse any section headings from the contract."

    bad: list[str] = []
    for path in _source_files():
        for line in path.read_text(encoding="utf-8").splitlines():
            # Only validate § cites that are about the api-contract. SECURITY.md
            # uses named (un-numbered) headings, so its ``§3`` cites are skipped.
            if "api-contract" not in line and "contract §" not in line:
                continue
            for sec in _CONTRACT_SECTION_RE.findall(line):
                if sec not in sections:
                    rel = path.relative_to(REPO_ROOT)
                    bad.append(f"{rel}: §{sec} — not in docs/api-contract.md")
    assert not bad, "Dangling contract-section references:\n" + "\n".join(bad)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
