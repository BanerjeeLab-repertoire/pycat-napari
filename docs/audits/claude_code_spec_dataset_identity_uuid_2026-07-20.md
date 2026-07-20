> **🟡 STATUS — the mechanism DONE, shipped in 1.6.190; the `dataset_id_for` re-routing REMAINS.**
> `utils/dataset_identity.py` — `DatasetIdentity` / `DatasetFingerprint`, `bounded_partial_hash` (head +
> interior + tail, never the whole file), `compute_fingerprint`, `fingerprints_match` (OME UUID
> authoritative; else size AND partial-hash; a borderline size-only match is NOT a match), and a persistent
> `DatasetRegistry.mint_or_recognise` (path hit → fingerprint match → new). `tests/test_dataset_identity.py`
> incl. the moved-file-keeps-UUID, borderline-is-new, and bounded-read (measured) tests. **Remaining (the
> integration):** route `entity_ref.dataset_id_for` through the registry so `dataset_id` becomes the UUID —
> this CHANGES what every entity id embeds and needs a one-time migration of old path-based session ids, so
> it is a deliberate follow-on, not a side effect. The mechanism it will use is delivered.

# Claude Code spec — Persistent dataset identity (UUID + fingerprint)

**Date:** 2026-07-20 · **Target tree:** 1.6.176 · Verified against the 1.6.176 tree. The brushing
audit's §2: dataset identity is currently the **file path**, which breaks the moment a dataset moves,
is remounted, or is opened cross-platform. This replaces path-as-identity with a durable UUID, keeping
the path as a *location* attribute.

## The problem (verified)
`entity_ref.py:202` — `dataset_id_for(source_path)` returns the path. Every `EntityKey` therefore
embeds the path as `dataset_id`. That is stable only while the file stays put. It breaks across: moving
the acquisition, a different drive letter, Windows-vs-Linux path spelling, symlinks, copies, session
relocation, and files opened from a temp cache. When it breaks, saved selections, session identity, and
cross-session entity references silently stop resolving — or worse, resolve to the wrong dataset if two
files share a path.

The audit's framing is exactly right: **the path should be a location attribute, not the identity.**

## Design
```python
@dataclass(frozen=True)
class DatasetIdentity:
    uuid: str                       # the durable identity
    original_path: str              # location, not identity
    fingerprint: DatasetFingerprint # for re-recognition when the path changed

@dataclass(frozen=True)
class DatasetFingerprint:
    size: int
    mtime: float | None
    ome_uuid: str | None            # if the file carries one (OME-TIFF/CZI often do)
    partial_hash: str               # hash of a bounded byte sample, NOT the whole file
```

### How identity is assigned and recovered
1. **On first load**, mint a UUID and compute the fingerprint. Persist the mapping
   (`uuid → DatasetIdentity`) in a small sidecar/registry so the same file gets the same UUID next time.
2. **On subsequent load**, try to recognise the dataset: exact path hit → same UUID; path miss →
   fingerprint match (size + ome_uuid + partial_hash) → **same UUID, updated path**. This is what makes
   identity survive a move.
3. **No match** → new UUID. Honest: an unrecognisable file is a new dataset, not a guessed old one.

### The fingerprint must be cheap and honest
- **`partial_hash` samples bounded bytes** (e.g. head + tail + a few interior blocks), never the whole
  multi-GB acquisition — hashing a 1.5 GB bead file on every load is unacceptable.
- **`ome_uuid` is the strongest signal** when present (OME metadata carries a real UUID); prefer it.
- Size+mtime alone is weak (a copy shares them; an edit changes mtime) — use them as fast pre-filters,
  confirm with `ome_uuid`/`partial_hash`.
- A fingerprint match is a **recognition**, reported as such; if confidence is borderline (size matches
  but hash differs), treat as a NEW dataset rather than risk merging two datasets' identities.

## Migration — keep it backward-compatible
- `dataset_id_for(path)` keeps working: it now resolves the path to a `DatasetIdentity` and returns its
  UUID. Existing `EntityKey` construction is unchanged at the call sites — only what `dataset_id`
  *contains* changes (path → UUID).
- **Old saved sessions carry path-based ids.** Provide a one-time migration: a session with a
  path-shaped `dataset_id` is recognised, its dataset fingerprinted, and a UUID assigned — with the old
  path retained so nothing dangling. Do not break old sessions; upgrade them on open.

## Tests (`core`, synthetic)
- Same file loaded twice → same UUID.
- File "moved" (same bytes, different path) → recognised via fingerprint → same UUID, updated path.
- A copy at a new path with identical bytes but a distinct `ome_uuid` → treated per the ome_uuid
  (same molecule → same; genuinely different → new). Document which rule wins and test it.
- Borderline (size matches, partial_hash differs) → NEW dataset, not a merge.
- `partial_hash` reads bounded bytes (assert it does not read the whole file — mock/measure).
- An old path-based session id migrates to a UUID on load without losing the reference.

## Steps
1. `DatasetIdentity` + `DatasetFingerprint` + a small persistent registry.
2. Mint-or-recognise logic (path hit → fingerprint match → new), with confidence handling.
3. Route `dataset_id_for` through the registry (returns UUID; path becomes an attribute).
4. Session migration for old path-based ids.
5. Tests above.
6. Full `pytest -m core` green.
7. Ship: version + PyPI push + commit (EXPLICIT filenames) + CHANGELOG (dataset identity is now a
   durable UUID; path is a location; old sessions migrate on open).

## Definition of done
- Datasets carry a persistent UUID; the path is a location attribute, not the identity.
- A moved/remounted/cross-platform dataset is recognised by fingerprint and keeps its UUID.
- Fingerprinting is bounded-cost (no whole-file hashing).
- Borderline matches are treated as new datasets, never silently merged.
- Old path-based sessions migrate on open.
- Full `pytest -m core` green.

## Cautions
- **Never hash the whole file** — bounded sample only; the bead files are gigabytes.
- **Prefer a real `ome_uuid`** when present; size+mtime are pre-filters, not identity.
- **A borderline match becomes a NEW dataset** — merging two datasets' identities is far worse than
  minting a fresh UUID.
- Keep `dataset_id_for`'s signature; change what it returns, not how it's called.
- Migrate old sessions; do not strand path-based ids.
