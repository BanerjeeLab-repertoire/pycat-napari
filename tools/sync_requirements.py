"""Regenerate ``config/requirements-base.txt`` from ``pyproject.toml`` (the single source of truth).

    python tools/sync_requirements.py           # rewrite the file
    python tools/sync_requirements.py --check   # exit 1 if it has drifted

The file had drifted by 9 missing dependencies and 5 dropped constraints (Completion Spec D1) -- among them
``cellpose<4``, so a requirements install took Cellpose 4, which removed the cyto2 CNN PyCAT segments with.
``tests/test_install_routes_agree.py`` fails when the two disagree.
"""
from __future__ import annotations

import pathlib
import sys
import tomllib

ROOT = pathlib.Path(__file__).resolve().parents[1]
TARGET = ROOT / 'config' / 'requirements-base.txt'
HEADER = ('# GENERATED from pyproject.toml by tools/sync_requirements.py -- do not edit by hand.\n'
          '# pyproject.toml is the single source of truth; the comments explaining each pin live there.\n')


def pyproject_dependencies() -> list[str]:
    with open(ROOT / 'pyproject.toml', 'rb') as fh:
        return list(tomllib.load(fh)['project']['dependencies'])


def render() -> str:
    return HEADER + ''.join(f'{dep}\n' for dep in pyproject_dependencies())


def main(argv: list[str]) -> int:
    text = render()
    if '--check' in argv:
        current = TARGET.read_text(encoding='utf-8') if TARGET.exists() else ''
        if current != text:
            print(f'{TARGET.relative_to(ROOT)} has drifted from pyproject.toml; run tools/sync_requirements.py')
            return 1
        return 0
    TARGET.write_text(text, encoding='utf-8', newline='\n')
    print(f'wrote {TARGET.relative_to(ROOT)} ({len(pyproject_dependencies())} dependencies)')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
