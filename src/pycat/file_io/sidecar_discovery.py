"""**Discover a companion 'sidecar' file that describes an image, and parse it — bounded, never gating.**

sidecar_metadata spec Parts 2–3. Some formats (a plain 2-D TIFF from ISS Vista) carry no channel identity at
all, but a companion file next to them does (`im-1-FUS-PLD-1_fbs.xml` beside `im-1-FUS-PLD-1_Ch1.tif`). This
looks for that companion — in the image's OWN directory only, no recursion, a hard cap — and hands it to a
registered parser. **A sidecar is an enrichment, never a precondition:** discovery never raises and never
gates a load; a miss, a failure, or a slow scan simply means the image opens with whatever in-file metadata
it has.

New instruments are added by *registering a parser*, not by editing the loader. Shipped with the ISS Vista
`_fbs.xml` parser (Part 3), which proves the mechanism on a real file.
"""
from __future__ import annotations

import dataclasses
import pathlib
import re
from typing import Callable, Optional

from pycat.utils.general_utils import debug_log


@dataclasses.dataclass(frozen=True)
class SidecarParser:
    """A registered sidecar reader. ``matches`` is a CHEAP test (extension + a header sniff); ``parse``
    returns the normalised common-metadata schema (plus a ``channels`` list) for a file it matched."""
    name: str
    matches: Callable[[pathlib.Path], bool]
    parse: Callable[[pathlib.Path], dict]


_PARSERS: list = []


def register_parser(parser: SidecarParser):
    _PARSERS.append(parser)


# A channel/position/time suffix on the image stem, stripped so `im-1-FUS-PLD-1_Ch1` also finds
# `im-1-FUS-PLD-1_fbs`. Kept tight — only the well-known acquisition suffixes.
_SUFFIX = re.compile(r'_(?:Ch|C|ch|s|t|z|Pos|XY)\d+$')
_SIDECAR_TAGS = ('', '_fbs', '_metadata', '_settings', '_properties', '_info', '_log')
_KNOWN_NAMES = ('metadata.txt', 'displaysettings.json', 'experiment.xml')


def _candidate_stems(stem: str):
    yield stem
    stripped = _SUFFIX.sub('', stem)
    if stripped != stem:
        yield stripped


def discover_sidecar(image_path, *, max_entries: int = 200):
    """Return ``(path, parser)`` for a companion file describing ``image_path``, or ``(None, None)``.

    Searches the image's **own directory only** — no recursion — examining at most ``max_entries`` entries,
    and matches a registered parser by a cheap header sniff. Never raises: on any error it returns
    ``(None, None)`` so the load proceeds.
    """
    try:
        image_path = pathlib.Path(image_path)
        directory = image_path.parent
        if not directory.is_dir():
            return None, None

        candidates = {s + tag for s in _candidate_stems(image_path.stem) for tag in _SIDECAR_TAGS}
        seen = 0
        for entry in directory.iterdir():
            seen += 1
            if seen > max_entries:                       # bounded — never walk a huge directory
                break
            if not entry.is_file() or entry == image_path:
                continue
            if entry.stem in candidates or entry.name.lower() in _KNOWN_NAMES:
                for parser in _PARSERS:
                    try:
                        if parser.matches(entry):
                            return entry, parser
                    except Exception as exc:             # broad-ok: a parser's match probe must never crash discovery
                        debug_log(f'sidecar: parser {parser.name} match failed', exc)
        return None, None
    except Exception as exc:                             # broad-ok: discovery is best-effort; a failure just means no sidecar
        debug_log('sidecar: discovery failed', exc)
        return None, None


def sidecar_metadata_for(image_path) -> Optional[dict]:
    """Discover AND parse a sidecar for ``image_path`` — the normalised metadata dict, or ``None``. The
    one-call convenience the loader uses; bounded and non-gating like :func:`discover_sidecar`."""
    path, parser = discover_sidecar(image_path)
    if path is None or parser is None:
        return None
    try:
        return parser.parse(path)
    except Exception as exc:                             # broad-ok: a parse failure is recorded; the image still loads
        debug_log(f'sidecar: parser {parser.name} failed on {path.name}', exc)
        return None


# ── The ISS Vista `_fbs.xml` parser (Part 3) ────────────────────────────────────────────────────────
def _parse_sections(blob: str) -> dict:
    """`<fromComments>` is sectioned free text, not structured XML: ``[Section]`` headers over lines of the
    form ``Key  -   : value`` (a trailing ``-`` and tabs are noise). Returns ``{section: {key: value}}``."""
    sections: dict = {}
    current = None
    for raw in blob.splitlines():
        line = raw.strip()
        header = re.match(r'\[([^\]]+)\]', line)
        if header:
            current = header.group(1).strip().lower()
            sections.setdefault(current, {})
            continue
        if current is not None and ':' in line:
            key, _, value = line.partition(':')
            key = re.sub(r'\s*-\s*$', '', key.strip()).strip().lower()
            value = value.strip()
            if key and value:
                sections[current][key] = value
    return sections


def _iss_matches(path: pathlib.Path) -> bool:
    if path.suffix.lower() != '.xml':
        return False
    try:
        head = path.read_text(encoding='utf-8', errors='ignore')[:4000]
    except Exception:                                    # broad-ok: unreadable file → not a match
        return False
    return ('fromComments' in head) or ('[Ch1]' in head) or ('ISS' in head and 'Vista' in head)


def _iss_parse(path: pathlib.Path) -> dict:
    """Parse an ISS Vista `_fbs.xml` into the common schema + a per-channel list. `[Ch\\d+]` sections become
    channels; everything else is system-level. modality is **fluorescence**, justified by emission filters +
    APD detectors (recorded with that reason), never asserted bare."""
    text = path.read_text(encoding='utf-8', errors='ignore')
    comments = re.findall(r'<fromComments>(.*?)</fromComments>', text, re.S | re.I)
    blob = '\n'.join(comments) if comments else text
    sections = _parse_sections(blob)

    channels = []
    lasers = []
    common: dict = {'metadata_source': 'iss_vista_fbs'}
    has_emission_filter = has_apd = False

    for name, kv in sections.items():
        ch_match = re.match(r'ch(\d+)$', name)
        if ch_match:
            channel = {'index': int(ch_match.group(1)) - 1, 'name': f'Ch{ch_match.group(1)}'}
            emission = kv.get('emission filter') or kv.get('emission')
            if emission:
                band = re.search(r'(\d+)\s*/\s*(\d+)', emission)
                if band:
                    channel['emission_nm'] = int(band.group(1))
                    channel['emission_bandwidth_nm'] = int(band.group(2))
                    has_emission_filter = True
            if 'pinhole' in kv:
                pin = re.search(r'[\d.]+', kv['pinhole'])
                if pin:
                    channel['pinhole_um'] = float(pin.group())
            detector = kv.get('detector') or kv.get('detector module')
            if detector:
                channel['detector'] = detector
                if 'apd' in detector.lower() or 'spcm' in detector.lower():
                    has_apd = True
            channels.append(channel)
        else:
            if 'microscope objective magnification' in kv:
                mag = re.search(r'[\d.]+', kv['microscope objective magnification'])
                if mag:
                    common['nominal_magnification'] = float(mag.group())
            if 'pixeldwelltime' in kv or 'pixel dwell time' in kv:
                dwell = re.search(r'[\d.]+', kv.get('pixeldwelltime') or kv.get('pixel dwell time') or '')
                if dwell:
                    common['pixel_dwell_time_ms'] = float(dwell.group())
            for key, value in kv.items():
                if 'laser' in key or 'excitation' in name:
                    nm = re.search(r'(\d{3})\s*nm', value) or re.search(r'^(\d{3})\b', value)
                    if nm:
                        lasers.append(int(nm.group(1)))

    if channels:
        common['channels'] = channels
    if lasers:
        common['excitation_lines_nm'] = sorted(set(lasers))
    if has_emission_filter or has_apd:
        common['modality'] = 'fluorescence'
        common['modality_reason'] = (
            'emission filters + APD photon-counting detectors — brightfield has neither')
    return common


register_parser(SidecarParser(name='iss_vista_fbs', matches=_iss_matches, parse=_iss_parse))
