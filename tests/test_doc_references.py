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
README = REPO_ROOT / "README.md"

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


# --------------------------------------------------------------------------- #
# Verb / path drift guard (#64 / F1-F3)                                       #
# --------------------------------------------------------------------------- #
# A documented endpoint row carries a method and a path. Both the README
# endpoints table and the api-contract tables (and section headings) cite these
# as ``METHOD /api/v1/<app>/<model>/...`` or, in the contract sub-resource
# tables, ``METHOD <app>/<model>/<pk>/...``. The two old audit findings (#64
# F1-F3) were verb/path drift that the existing filename / §-number checks could
# not catch. The guard below resolves every documented row against the actual
# ``api/urls.py`` route table so a wrong verb or path fails CI.

# Any ``<...>``-style placeholder (``<app>``, ``<str:pk>``, ``<name>``) is
# collapsed to a single token so doc and route placeholders compare regardless
# of name / converter (the doc uses ``<app>``; the route uses ``<str:app_label>``).
_PLACEHOLDER_RE = re.compile(r"<[^>]+>")
# A documented endpoint row: a method verb, then (after an optional table-cell
# pipe / spaces) a backtick-quoted path. The path token is everything up to the
# closing backtick, so multi-segment paths (``<app>/<model>/<pk>/history/``) are
# captured whole. Matches both README cells and contract tables / headings.
_ENDPOINT_RE = re.compile(
    r"`(GET|POST|PATCH|DELETE|PUT)`\s*\|?\s*" r"`(?:/)?(?:api/v1/)?([\w<>:/.?=&…-]+?/?)`",
)
# Only path tokens that actually look like API routes (not arbitrary backtick
# code spans on the same line) are validated.
_API_PATH_RE = re.compile(r"^(<[^>]+>/<[^>]+>/?|registry|schema|recent-actions|login|logout)")


def _normalize_path(path: str) -> str:
    """Collapse placeholders + trim query strings / trailing punctuation."""
    path = path.split("?", 1)[0]
    path = _PLACEHOLDER_RE.sub("<>", path)
    return path.strip().strip("/").rstrip(".")


def _route_table() -> set[tuple[str, str]]:
    """Return the canonical ``{(METHOD, normalized_path)}`` from ``api/urls.py``."""
    import django

    django.setup()
    from django_admin_rest_api.api import urls

    table: set[tuple[str, str]] = set()
    for pattern in urls.urlpatterns:
        view_class = getattr(pattern.callback, "view_class", None)
        methods = getattr(view_class, "http_method_names", []) if view_class else []
        norm = _normalize_path(str(pattern.pattern))
        for method in methods:
            if method == "options":
                continue
            table.add((method.upper(), norm))
    return table


def _documented_endpoints(text: str) -> list[tuple[str, str, str]]:
    """Yield ``(method, normalized_path, raw_line)`` for each documented row."""
    found: list[tuple[str, str, str]] = []
    for line in text.splitlines():
        for match in _ENDPOINT_RE.finditer(line):
            method, raw_path = match.group(1), match.group(2)
            if not _API_PATH_RE.match(raw_path):
                continue
            found.append((method.upper(), _normalize_path(raw_path), line.strip()))
    return found


@pytest.mark.parametrize("doc", [README, CONTRACT], ids=["README", "api-contract"])
def test_documented_endpoints_match_routes(doc: Path) -> None:
    """Every documented ``METHOD /path`` row resolves to a real route + verb.

    Catches the verb/path drift class (#64): e.g. ``POST .../bulk-update/``
    when the route is ``PATCH .../bulk/``, or a delete-preview row that drops
    the ``<pk>`` segment. The check normalizes path placeholders so doc names
    (``<app>``) and route converters (``<str:app_label>``) compare equal.
    """
    routes = _route_table()
    drift: list[str] = []
    for method, path, line in _documented_endpoints(doc.read_text(encoding="utf-8")):
        if (method, path) not in routes:
            drift.append(f"{method} {path!r}  (from: {line})")
    assert not drift, (
        f"Documented endpoints in {doc.name} that do not match a route in "
        f"api/urls.py (wrong verb or path):\n" + "\n".join(drift)
    )


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
