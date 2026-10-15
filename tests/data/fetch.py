"""Resolve a real-data corpus file by name, verified against ``registry.txt`` (Completion Spec 0.1).

Lookup order: ``$PYCAT_CORPUS_DIR``, then the default cache (``~/.cache/pycat/corpus/v1``). If the file is not
there and ``$PYCAT_CORPUS_URL`` (or the registry's ``# url:`` line) names a release, it is downloaded into the
cache. A file whose SHA-256 does not match the registry is refused -- a silently different input would make
every golden master meaningless. Nothing is downloaded when no URL is configured; callers skip instead.

``tools/build_corpus.py`` builds the corpus from the lab's source folder and writes the registry.
"""
from __future__ import annotations

import hashlib
import json
import os
import urllib.request
from dataclasses import dataclass
from pathlib import Path

REGISTRY = Path(__file__).with_name('registry.txt')


@dataclass(frozen=True)
class CorpusEntry:
    filename: str
    sha256: str
    size: int
    pipeline: str
    provenance: dict

    @property
    def missing(self) -> bool:
        return self.filename == 'MISSING'


def corpus_dir() -> Path:
    env = os.environ.get('PYCAT_CORPUS_DIR')
    return Path(env) if env else Path(os.path.expanduser('~')) / '.cache' / 'pycat' / 'corpus' / 'v1'


def entries() -> list[CorpusEntry]:
    out = []
    if not REGISTRY.exists():
        return out
    for line in REGISTRY.read_text(encoding='utf-8').splitlines():
        if not line.strip() or line.startswith('#'):
            continue
        name, sha, size, pipeline, prov = line.split('\t', 4)
        out.append(CorpusEntry(name, sha, int(size), pipeline, json.loads(prov)))
    return out


def for_pipeline(pipeline: str) -> list[CorpusEntry]:
    return [e for e in entries() if e.pipeline == pipeline]


def _url() -> str | None:
    env = os.environ.get('PYCAT_CORPUS_URL')
    if env:
        return env.rstrip('/')
    if REGISTRY.exists():
        for line in REGISTRY.read_text(encoding='utf-8').splitlines():
            if line.startswith('# url:'):
                return line.split(':', 1)[1].strip().rstrip('/')
    return None


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, 'rb') as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


class CorpusUnavailable(Exception):
    """The file is not present locally and cannot be fetched (no URL configured, or offline)."""


class CorpusMismatch(Exception):
    """The file is present but is not the registered file (its SHA-256 differs)."""


def fetch(filename: str) -> Path:
    """The verified local path of corpus file ``filename``."""
    entry = next((e for e in entries() if e.filename == filename), None)
    if entry is None or entry.missing:
        raise CorpusUnavailable(f'{filename!r} is not a registered corpus file')
    path = corpus_dir() / filename
    if not path.exists():
        url = _url()
        if not url:
            raise CorpusUnavailable(f'{filename} is not in {corpus_dir()} and no corpus URL is configured '
                                    '(set PYCAT_CORPUS_DIR, or PYCAT_CORPUS_URL, or run tools/build_corpus.py)')
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            urllib.request.urlretrieve(f'{url}/{filename}', path)
        except OSError as exc:
            path.unlink(missing_ok=True)
            raise CorpusUnavailable(f'could not download {filename}: {exc}') from exc
    actual = sha256_of(path)
    if actual != entry.sha256:
        raise CorpusMismatch(f'{path} has sha256 {actual}, the registry says {entry.sha256}')
    return path
