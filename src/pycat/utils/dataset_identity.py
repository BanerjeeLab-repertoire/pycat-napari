"""**Persistent dataset identity — a durable UUID, with the path demoted to a location attribute.**

Dataset identity was the FILE PATH, which breaks the moment a dataset moves, is remounted, is opened
cross-platform (drive letter / path spelling), symlinked, copied, or opened from a temp cache. When it
breaks, saved selections, session identity, and cross-session entity references silently stop resolving —
or worse, resolve to the WRONG dataset if two files share a path.

This mints a durable **UUID** per dataset and keeps the path as a *location*. Identity survives a move by
**re-recognition**: on load, an exact-path hit reuses the UUID; a path miss falls back to a cheap
fingerprint (size + OME UUID + a bounded partial hash) → same UUID with an updated path; no match → a NEW
UUID (an unrecognisable file is a new dataset, not a guessed old one).

**The fingerprint is cheap and honest:**
- ``partial_hash`` samples BOUNDED bytes (head + interior blocks + tail) — never the whole multi-GB
  acquisition; hashing a 1.5 GB bead file on every load is unacceptable.
- ``ome_uuid`` (OME-TIFF/CZI often carry one) is the STRONGEST signal — preferred when present.
- size+mtime are fast pre-filters, not identity (a copy shares them; an edit changes mtime).
- **A borderline match becomes a NEW dataset.** Merging two datasets' identities is far worse than minting
  a fresh UUID, so size-matches-but-hash-differs is treated as new, never merged.

This module is the mechanism. Routing ``entity_ref.dataset_id_for`` through it (which changes what an
entity id embeds, and needs a one-time migration of old path-based session ids) is the integration
follow-on — done deliberately, not as a side effect.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import uuid as _uuid


@dataclasses.dataclass(frozen=True)
class DatasetFingerprint:
    """A cheap, bounded signature for re-recognising a dataset whose path changed."""
    size: int
    mtime: "float | None"
    ome_uuid: "str | None"
    partial_hash: str

    def to_dict(self):
        return dataclasses.asdict(self)

    @staticmethod
    def from_dict(d):
        return DatasetFingerprint(size=int(d['size']), mtime=d.get('mtime'),
                                  ome_uuid=d.get('ome_uuid'), partial_hash=d['partial_hash'])


@dataclasses.dataclass(frozen=True)
class DatasetIdentity:
    """The durable identity of a dataset: a UUID (the identity), the original path (a LOCATION attribute,
    updated when the file is recognised at a new path), and the fingerprint that enables re-recognition."""
    uuid: str
    original_path: str
    fingerprint: DatasetFingerprint


_BLOCK = 65536
_N_INTERIOR = 3


def bounded_partial_hash(path, *, block=_BLOCK, n_interior=_N_INTERIOR):
    """A hash of a BOUNDED sample of the file — head, a few evenly-spaced interior blocks, and the tail —
    plus the size. Reads at most ``(n_interior + 2) × block`` bytes regardless of file size, so a
    multi-gigabyte acquisition is fingerprinted in kilobytes, never hashed whole."""
    size = os.path.getsize(path)
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        h.update(f.read(block))                                  # head
        for k in range(1, n_interior + 1):
            pos = int(size * k / (n_interior + 1)) - block // 2
            f.seek(max(0, min(pos, max(0, size - block))))
            h.update(f.read(block))                              # interior
        if size > block:
            f.seek(max(0, size - block))
            h.update(f.read(block))                              # tail
    h.update(str(size).encode())
    return h.hexdigest()[:32]


def compute_fingerprint(path, *, ome_uuid=None) -> DatasetFingerprint:
    """The dataset's fingerprint: size + mtime (fast pre-filters), the OME UUID if the file carries one
    (the strongest signal), and the bounded partial hash."""
    return DatasetFingerprint(size=int(os.path.getsize(path)),
                              mtime=float(os.path.getmtime(path)),
                              ome_uuid=(str(ome_uuid) if ome_uuid else None),
                              partial_hash=bounded_partial_hash(path))


def fingerprints_match(a: DatasetFingerprint, b: DatasetFingerprint) -> bool:
    """Whether two fingerprints identify the SAME dataset. The OME UUID is authoritative when BOTH carry
    one (same molecule → same; different → different). Otherwise a match requires BOTH the size AND the
    bounded partial hash — a size-only agreement (a coincidental collision, or a same-size different file)
    is NOT a match, so two datasets are never merged on a weak signal."""
    if a.ome_uuid and b.ome_uuid:
        return a.ome_uuid == b.ome_uuid
    return a.size == b.size and a.partial_hash == b.partial_hash


class DatasetRegistry:
    """``uuid → DatasetIdentity``, optionally persisted to a small JSON sidecar so the same file gets the
    same UUID across sessions. Pass ``store_path=None`` for an in-memory registry (tests)."""

    def __init__(self, store_path=None):
        self.store_path = str(store_path) if store_path else None
        self._by_uuid: dict = {}
        self._load()

    # ── persistence ─────────────────────────────────────────────────────────────────────────────
    def _load(self):
        if not self.store_path or not os.path.exists(self.store_path):
            return
        try:
            with open(self.store_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            for u, rec in data.get('datasets', {}).items():
                self._by_uuid[u] = DatasetIdentity(
                    uuid=u, original_path=rec['original_path'],
                    fingerprint=DatasetFingerprint.from_dict(rec['fingerprint']))
        except Exception:      # broad-ok: a corrupt registry must not block opening a dataset
            self._by_uuid = {}

    def _save(self):
        if not self.store_path:
            return
        try:
            data = {'datasets': {u: {'original_path': ident.original_path,
                                     'fingerprint': ident.fingerprint.to_dict()}
                                 for u, ident in self._by_uuid.items()}}
            with open(self.store_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2)
        except Exception:      # broad-ok: failing to persist identity must not cost the user their data
            pass

    def _store(self, ident: DatasetIdentity):
        self._by_uuid[ident.uuid] = ident
        self._save()

    # ── mint or recognise ───────────────────────────────────────────────────────────────────────
    def mint_or_recognise(self, path, *, ome_uuid=None, new_uuid=None) -> DatasetIdentity:
        """Return this file's durable identity: an exact-path hit reuses its UUID; a path miss that
        fingerprint-matches an existing dataset reuses THAT UUID with the new path (identity survives the
        move); otherwise a fresh UUID (an unrecognised file is a new dataset). ``new_uuid`` overrides the
        minted id (tests)."""
        path = str(path)
        for ident in self._by_uuid.values():
            if ident.original_path == path:
                return ident                                    # exact-path hit → same UUID

        fp = compute_fingerprint(path, ome_uuid=ome_uuid)
        for ident in self._by_uuid.values():
            if fingerprints_match(ident.fingerprint, fp):
                moved = DatasetIdentity(uuid=ident.uuid, original_path=path, fingerprint=fp)
                self._store(moved)                              # recognised → same UUID, updated path
                return moved

        fresh = DatasetIdentity(uuid=(str(new_uuid) if new_uuid else str(_uuid.uuid4())),
                                original_path=path, fingerprint=fp)
        self._store(fresh)                                      # no match → a genuinely new dataset
        return fresh

    def __len__(self):
        return len(self._by_uuid)


# ── the process-wide registry + the integration entry point ─────────────────────────────────────

_DEFAULT_REGISTRY = None


def default_registry() -> "DatasetRegistry":
    """The process-wide dataset registry, persisted to a user-writable sidecar (``~/.pycat/
    dataset_registry.json``) so a dataset keeps its UUID across sessions. Created lazily; falls back to an
    in-memory registry if the sidecar directory cannot be created."""
    global _DEFAULT_REGISTRY
    if _DEFAULT_REGISTRY is None:
        store = None
        try:
            d = os.path.join(os.path.expanduser('~'), '.pycat')
            os.makedirs(d, exist_ok=True)
            store = os.path.join(d, 'dataset_registry.json')
        except Exception:      # broad-ok: no writable config dir → fall back to an in-memory registry
            store = None
        _DEFAULT_REGISTRY = DatasetRegistry(store_path=store)
    return _DEFAULT_REGISTRY


def uuid_for_path(path):
    """The durable UUID for a **readable** dataset file, or ``None`` when the path is absent/unreadable (the
    caller then falls back to the path as a location-id — backward-compatible). Cheap on a repeat: a
    known path returns its UUID without re-fingerprinting."""
    try:
        if path and os.path.isfile(str(path)):
            return default_registry().mint_or_recognise(str(path)).uuid
    except Exception:      # broad-ok: a durable id is optional — an unreadable file falls back to its path
        return None
    return None
