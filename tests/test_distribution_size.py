"""**A ratchet so the distribution cannot silently regrow.**

PyPI enforces a total project-size quota, and PyCAT was burning it at ~25 MB per release — dominated by
18 MB of `docs/` (mostly screenshots) shipped inside the sdist, plus ~880 KB of unreferenced logo PNGs
in every wheel, plus stray `.DS_Store`. The slimming (1.6.145) cut the sdist to ~7 MB. Without a guard,
assets creep back (a new screenshot here, a spare logo there), so this pins the wins as build-config +
filesystem assertions — no actual build required, so it runs `core`. Mirrors the *does not GROW* idiom
of `test_complexity_budget.py`.
"""
import pathlib

import pytest

pytestmark = pytest.mark.core

_ROOT = pathlib.Path(__file__).resolve().parents[1]
_ICONS = _ROOT / "src" / "pycat" / "icons"
#: Icons allowed to ship WITHOUT a source reference, each with a reason. Empty today — both shipped
#: icons are referenced in code; a future decorative-but-unreferenced icon must be listed here on purpose.
_ICON_ALLOWLIST: dict[str, str] = {}
#: Total shipped-icon budget. The launcher needs a mark + a splash logo; a gallery of logos does not
#: belong inside the installed package.
_ICON_BYTES_CEILING = 250_000


def _pyproject():
    import tomllib
    with open(_ROOT / "pyproject.toml", "rb") as fh:
        return tomllib.load(fh)


def _sdist_include():
    return _pyproject()["tool"]["hatch"]["build"]["targets"]["sdist"]["include"]


def _wheel_exclude():
    return _pyproject()["tool"]["hatch"]["build"]["targets"]["wheel"].get("exclude", [])


def test_sdist_does_not_ship_docs_or_notebooks():
    """`docs/` (18 MB) and `notebooks/` (816 KB) live in the repo + docs site, not in a `pip install`.
    Re-adding either to the sdist include is the regression this guards."""
    include = set(_sdist_include())
    offenders = include & {"docs", "notebooks"}
    assert not offenders, (
        f"these heavy trees are back in the sdist include: {sorted(offenders)} — they belong in the "
        f"repo/docs site, not the released tarball (they were the PyPI-quota cost).")


def test_shipped_icons_are_small_and_referenced():
    """Every icon that ships is either referenced in source or explicitly allow-listed with a reason,
    and the icon directory stays under budget."""
    icons = sorted(_ICONS.glob("*.png"))
    total = sum(p.stat().st_size for p in icons)
    assert total < _ICON_BYTES_CEILING, (
        f"shipped icons total {total} bytes (ceiling {_ICON_BYTES_CEILING}) — a spare logo crept into "
        f"src/pycat/icons/; large images belong in docs/logos/, not the installed package.")

    src_text = "\n".join(
        p.read_text(encoding="utf-8", errors="ignore")
        for p in (_ROOT / "src").rglob("*.py"))
    unreferenced = [p.name for p in icons
                    if p.stem not in src_text and p.name not in _ICON_ALLOWLIST]
    assert not unreferenced, (
        f"these icons ship but are not referenced in source (and are not allow-listed): {unreferenced}. "
        f"Delete them, or add each to _ICON_ALLOWLIST with a reason.")


def test_no_DS_Store_under_src():
    """`.DS_Store` is macOS cruft — never in the tree, never in an artifact."""
    stragglers = [str(p.relative_to(_ROOT)) for p in (_ROOT / "src").rglob(".DS_Store")]
    assert not stragglers, f".DS_Store files are back under src/: {stragglers} (git rm them)."


def test_wheel_excludes_DS_Store():
    """The wheel packaging path did not apply the sdist's `.DS_Store` exclusion — pin that it does now."""
    assert "**/.DS_Store" in _wheel_exclude(), (
        "the wheel target must exclude '**/.DS_Store' so it can never ship in an installed package.")
