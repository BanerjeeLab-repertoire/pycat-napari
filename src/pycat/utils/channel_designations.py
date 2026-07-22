"""Persistent, opt-in **channel designations** — teach PyCAT once which channel is the
condensate, and have it remembered for future files with the same acquisition layout.

Why this exists
---------------
When a fluorescence file's metadata does NOT identify its channels (no fluorophore name, no
emission wavelength), PyCAT cannot know which channel holds the condensates and which is, say, DAPI.
Both load as generic "Fluorescence Image" / "Fluorescence Image (1)", so **the only thing telling
them apart is load order** — whichever landed first drives what gets segmented. That is the bug this
addresses.

The honest fix is NOT to guess. It is to let the user state the fact ONCE, opt-in, and remember it —
keyed to the *acquisition signature* (how many channels, their spectral buckets / order), NOT the
file path (which breaks when files move). A new file with the same channel layout gets the remembered
designation applied automatically; a different layout does not.

This mirrors the pixel-size acquisition-profiles design: metadata can't recover a per-experiment
fact, so the user supplies it once and it persists.

Scope
-----
Pure logic + a small JSON store on disk (reuses the per-user PyCAT config dir). No napari, no Qt —
fully unit-testable. The UI that offers the opt-in and the load-time tagging call into this.
"""

from __future__ import annotations

import json
import os
from typing import Dict, List, Optional

from pycat.utils.general_utils import debug_log

_STORE_NAME = 'channel_designations.json'
_CACHE: Optional[Dict[str, dict]] = None


def _store_path() -> str:
    """The on-disk JSON path, in the per-user PyCAT config dir (reused from local_cache)."""
    try:
        from pycat.file_io.local_cache import _config_dir
        return os.path.join(_config_dir(), _STORE_NAME)
    except Exception as exc:
        debug_log('channel_designations: config dir unavailable', exc)
        return os.path.join(os.path.expanduser('~'), '.pycat_' + _STORE_NAME)


def acquisition_signature(channel_infos: List[dict]) -> str:
    """A stable key describing a file's CHANNEL LAYOUT, independent of file path.

    Built from the per-channel ``identify_channel`` dicts: the ordered tuple of spectral buckets
    (blue/green/red/…), which is what actually distinguishes 'a DAPI+GFP acquisition' from 'a
    single-green acquisition'. Two files acquired the same way share a signature; a different layout
    does not — so a remembered designation only auto-applies where it is actually meaningful.

    Falls back to channel COUNT + ordered labels when buckets are unknown, so even metadata-poor
    files get a (weaker but usable) signature rather than colliding into one bucket.
    """
    buckets = [str((ci or {}).get('bucket') or 'unknown') for ci in channel_infos]
    # if every bucket is unknown the buckets alone don't distinguish anything — fold in labels
    if all(b == 'unknown' for b in buckets):
        labels = [str((ci or {}).get('label') or f'C{i}') for i, ci in enumerate(channel_infos)]
        return 'n{}|labels:{}'.format(len(channel_infos), ','.join(labels))
    return 'n{}|buckets:{}'.format(len(channel_infos), ','.join(buckets))


def _load() -> Dict[str, dict]:
    global _CACHE
    if _CACHE is not None:
        return _CACHE
    try:
        path = _store_path()
        if os.path.exists(path):
            with open(path, 'r', encoding='utf-8') as f:
                _CACHE = json.load(f)
        else:
            _CACHE = {}
    except Exception as exc:
        debug_log('channel_designations: could not read store', exc)
        _CACHE = {}
    return _CACHE


def _save(store: Dict[str, dict]) -> bool:
    global _CACHE
    _CACHE = store
    try:
        path = _store_path()
        tmp = path + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(store, f, indent=2)
        os.replace(tmp, path)   # atomic
        return True
    except Exception as exc:  # broad-ok: write — returns False, so the caller surfaces the failed designation persist rather than it vanishing silently
        debug_log('channel_designations: could not write store', exc)
        return False


def remember_designation(channel_infos: List[dict], condensate_channel_index: int) -> bool:
    """Persist 'in this acquisition layout, channel N is the condensate channel'.

    Opt-in: only called when the user explicitly designates. Keyed to the acquisition signature so
    it recalls for future same-layout files.
    """
    if channel_infos is None or condensate_channel_index is None:
        return False
    if not (0 <= condensate_channel_index < len(channel_infos)):
        return False
    store = dict(_load())
    sig = acquisition_signature(channel_infos)
    store[sig] = {
        'condensate_channel_index': int(condensate_channel_index),
        'n_channels': len(channel_infos),
        'buckets': [str((ci or {}).get('bucket') or 'unknown') for ci in channel_infos],
    }
    return _save(store)


def recall_designation(channel_infos: List[dict]) -> Optional[int]:
    """Return the remembered condensate-channel index for this acquisition layout, or None.

    None means 'nothing remembered for this layout' — the caller must NOT guess; it leaves the
    channel unmarked (or asks the user), which is the correct behaviour for an undecidable case.
    """
    if not channel_infos:
        return None
    store = _load()
    sig = acquisition_signature(channel_infos)
    entry = store.get(sig)
    if not entry:
        return None
    idx = entry.get('condensate_channel_index')
    # guard against a store that no longer matches this file's channel count
    if isinstance(idx, int) and 0 <= idx < len(channel_infos):
        return idx
    return None


def forget_designation(channel_infos: List[dict]) -> bool:
    """Remove the remembered designation for this acquisition layout."""
    store = dict(_load())
    sig = acquisition_signature(channel_infos)
    if sig in store:
        del store[sig]
        return _save(store)
    return False


def _reset_cache_for_tests():
    global _CACHE
    _CACHE = None
