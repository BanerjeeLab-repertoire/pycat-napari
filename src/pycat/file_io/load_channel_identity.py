"""**Channel identity on load — the live-path orchestration (sidecar_metadata Part 4/5).**

Two already-built mechanisms are joined here for the file loader, deterministically and Qt-free:

1. **Sidecar enrichment** — when a companion file (`sidecar_discovery.sidecar_metadata_for`) carries a
   channel's emission band, feed it into naming ABOVE the pixel/position guess, so an ISS Vista `_Ch1`/`_Ch2`
   pair is named from its 647/525 nm filters (far-red / green) and **never falls to `Brightfield`**.
2. **Remembered identities** — apply a user's past answer for this acquisition layout
   (`channel_designations.recall_channel_identities`), so a second file of the same layout is named
   automatically and never re-asked; and persist a fresh answer the user typed into the naming dialog.

Everything here operates on the per-channel `identify_channel` dicts (`{label, source, bucket, layer_name}`)
and is **non-gating**: on any failure it returns the channel list unchanged so the image still loads. The
actual Qt prompt lives in `ui/channel_identity_dialog`; nothing in this module touches Qt, so it is fully
headless-testable. Imports are kept lazy so importing this module stays light (the naming tier is numpy-free;
the persistence store reaches the scientific stack).
"""
from __future__ import annotations

# A channel identity is "weak" — open to being improved by a sidecar or a remembered answer — when it came
# from a pixel/position guess or the file's own name, not from real acquisition metadata (a fluorophore name
# or an emission wavelength). Real in-file metadata is never overwritten.
_WEAK_SOURCES = frozenset({None, "position", "pixels", "filename"})


def _with_identity(info, index, name, source):
    """A copy of ``info`` renamed to ``name`` with the given provenance ``source`` — the bucket/colormap is
    preserved so a recalled name keeps its spectral colour."""
    out = dict(info or {})
    out["label"] = name
    out["source"] = source
    out["layer_name"] = f"C{index}-{name}"
    out["raw_name"] = name
    return out


def enrich_with_sidecar(channel_info, sidecar):
    """Improve WEAK channel names using a discovered sidecar's per-channel emission, re-running the same naming
    tiers so the result carries ``source='wavelength'``. A channel already named from real in-file metadata
    (``name``/``wavelength``) is left untouched; a sidecar that identifies nothing changes nothing. Returns a
    new list (or the input unchanged on any problem — never gates the load)."""
    if not channel_info or not sidecar:
        return channel_info
    try:
        sc_by_index = {
            c.get("index"): c
            for c in (sidecar.get("channels") or [])
            if isinstance(c, dict) and c.get("index") is not None
        }
        if not sc_by_index:
            return channel_info
        from pycat.utils.channel_naming import identify_channel

        out = []
        for i, info in enumerate(channel_info):
            info = info or {}
            sc = sc_by_index.get(i)
            emission = (sc or {}).get("emission_nm")
            if sc is not None and emission is not None and (info.get("source") in _WEAK_SOURCES):
                improved = identify_channel(channel_index=i, emission_wavelength=emission)
                if improved.get("source") != "position":   # only adopt when the sidecar actually identified it
                    out.append(improved)
                    continue
            out.append(info)
        return out
    except Exception:      # broad-ok: optional_probe — sidecar enrichment is best-effort; the image still loads
        return channel_info


def apply_recalled_identities(channel_info):
    """Overlay identities the user remembered for THIS acquisition layout onto the channel list, marking each
    ``source='user'``. Applied only to WEAK channels, so a remembered answer fills a genuinely-empty identity
    and never overwrites real metadata found in the file. Returns a new list (input unchanged on any problem)."""
    if not channel_info:
        return channel_info
    try:
        from pycat.utils.channel_designations import recall_channel_identities

        recalled = recall_channel_identities(channel_info)
        if not recalled:
            return channel_info
        out = []
        for i, info in enumerate(channel_info):
            info = info or {}
            name = recalled.get(i)
            if name and (info.get("source") in _WEAK_SOURCES):
                out.append(_with_identity(info, i, name, "user"))
            else:
                out.append(info)
        return out
    except Exception:      # broad-ok: optional_probe — recall is best-effort; the image still loads
        return channel_info


def resolve_channel_identity_on_load(file_path, channel_info):
    """The single non-gating call the loader makes after building ``channel_info``: discover a sidecar and
    enrich naming from it, then apply any remembered identities for this layout. Never raises."""
    sidecar = None
    try:
        from pycat.file_io.sidecar_discovery import sidecar_metadata_for

        sidecar = sidecar_metadata_for(file_path)
    except Exception:      # broad-ok: optional_probe — no sidecar is the normal case, not an error
        sidecar = None
    return apply_recalled_identities(enrich_with_sidecar(channel_info, sidecar))


def remember_user_channel_names(channel_info, assigned_names):
    """Persist a real, user-typed name for any channel that had NO recoverable identity (it fell to the position
    guess) — keyed to the acquisition layout so a future same-layout file recalls it. A blank name, or one left
    at the auto-derived default, is not stored (a human answer only fills a genuinely-empty identity). Returns
    the indices actually remembered. Non-gating and Qt-free."""
    remembered = []
    if not channel_info or not assigned_names:
        return remembered
    try:
        from pycat.utils.channel_naming import channel_needs_identity
        from pycat.utils.channel_designations import remember_channel_identity

        for i, name in enumerate(assigned_names):
            if i >= len(channel_info):
                break
            info = channel_info[i] or {}
            if not channel_needs_identity(info):
                continue                                   # identified from evidence → nothing to remember
            name = (name or "").strip()
            if not name:
                continue
            default = str(info.get("layer_name") or info.get("label") or "").strip()
            if name == default:
                continue                                   # user left the prefilled default → not an answer
            if remember_channel_identity(channel_info, i, name):
                remembered.append(i)
    except Exception:      # broad-ok: write — persistence is best-effort; a failure must not break the load
        pass
    return remembered
