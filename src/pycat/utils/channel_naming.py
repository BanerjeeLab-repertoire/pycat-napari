"""
PyCAT Channel Identification Utilities
========================================
Identifies fluorophore/channel identity from heterogeneous microscope
metadata, with a deterministic fallback when no metadata is available.

Different microscopes and software (Zeiss/ZEN, Andor/Fusion, Nikon/NIS,
Leica/LAS X, ImageJ/Fiji, etc.) all record channel information differently —
some store fluorophore names, some store excitation/emission wavelengths,
some store nothing useful at all. This module centralizes the logic for
inferring a human-readable, consistent channel label from whatever is
available, with a position-based fallback so behavior is always predictable.

Design
------
Three-tier strategy, in priority order:
  1. Explicit fluorophore name in metadata (e.g. "DAPI", "EGFP", "mCherry")
     — matched against a lookup table of common fluorophore name patterns.
  2. Emission wavelength in metadata — bucketed into spectral regions
     (UV/blue, green, red, far-red) and mapped to a common name.
  3. Position-based fallback — channel 0 assumed DAPI/blue, channel 1
     assumed green, channel 2 assumed red, channel 3+ assumed far-red,
     following the most common convention in fluorescence imaging.

This keeps behavior deterministic and microscope-agnostic: any file with
proper metadata gets accurate names, and any file without metadata still
gets sensible, consistent default names rather than "Channel 0/1/2".

Author
------
    Gable Wadsworth / Christian Neureuter, Banerjee Lab, SUNY Buffalo

Date
----
    2025
"""

from __future__ import annotations
import re
from typing import Optional


# ---------------------------------------------------------------------------
# Fluorophore name pattern lookup
# ---------------------------------------------------------------------------
# Each entry: (regex pattern matched case-insensitively against any
# fluorophore/channel name string, display label, spectral bucket)

_FLUOROPHORE_PATTERNS = [
    # Boundaries use (?:^|[^a-z0-9]) ... (?:[^a-z0-9]|$) rather than \b, because
    # real channel names embed the fluorophore between underscores/digits
    # (e.g. "488_GFP_CF40um_z", "594_mCherry_CF40") where \b does NOT fire
    # (underscore is a word char). This matches the token wherever it sits.
    # UV / blue (nuclear stains)
    (r"dapi",                              "DAPI",      "blue"),
    (r"hoechst",                           "Hoechst",   "blue"),
    (r"(?:^|[^a-z])draq5?(?:[^a-z]|$)",    "DRAQ",      "blue"),

    # Green
    (r"(?:^|[^a-z])egfp(?:[^a-z]|$)|(?:^|[^a-z])gfp(?:[^a-z]|$)", "EGFP", "green"),
    (r"(?:^|[^a-z0-9])alexa\s*488(?:[^0-9]|$)|(?:^|[^a-z])af488(?:[^0-9]|$)", "Alexa488", "green"),
    (r"(?:^|[^a-z])fitc(?:[^a-z]|$)",      "FITC",      "green"),
    (r"(?:^|[^a-z])green(?:[^a-z]|$)",     "Green",     "green"),

    # Red / orange
    (r"(?:^|[^a-z])mcherry(?:[^a-z]|$)",   "mCherry",   "red"),
    (r"(?:^|[^a-z])mscarlet(?:[^a-z]|$)",  "mScarlet",  "red"),
    (r"(?:^|[^a-z])tdtomato(?:[^a-z]|$)",  "tdTomato",  "red"),
    (r"(?:^|[^a-z])rfp(?:[^a-z]|$)",       "RFP",       "red"),
    (r"(?:^|[^a-z0-9])alexa\s*568(?:[^0-9]|$)|(?:^|[^a-z])af568(?:[^0-9]|$)", "Alexa568", "red"),
    (r"(?:^|[^a-z0-9])alexa\s*594(?:[^0-9]|$)|(?:^|[^a-z])af594(?:[^0-9]|$)", "Alexa594", "red"),
    (r"texas\s*red",                       "TexasRed",  "red"),
    (r"(?:^|[^a-z])tritc(?:[^a-z]|$)",     "TRITC",     "red"),
    (r"(?:^|[^a-z])red(?:[^a-z]|$)",       "Red",       "red"),

    # Far-red
    (r"(?:^|[^a-z])cy5(?:[^a-z]|$)",       "Cy5",       "far_red"),
    (r"(?:^|[^a-z0-9])alexa\s*647(?:[^0-9]|$)|(?:^|[^a-z])af647(?:[^0-9]|$)", "Alexa647", "far_red"),
    (r"(?:^|[^a-z])apc(?:[^a-z]|$)",       "APC",       "far_red"),
    (r"far\s*red",                         "FarRed",    "far_red"),

    # Transmitted / brightfield / DIC — not fluorescence. BFPreAm / BFPreAmp
    # is the Andor/Fusion brightfield-pre-amplifier channel name.
    (r"(?:^|[^a-z])dic(?:[^a-z]|$)|(?:^|[^a-z])phase(?:[^a-z]|$)|brightfield|"
     r"bfpreamp?|(?:^|[^a-z])bf(?:[^a-z]|$)|(?:^|[^a-z])pmt(?:[^a-z]|$)|transmit",
                                            "Transmitted", "transmitted"),
]

# Emission wavelength → spectral bucket (nm ranges)
_WAVELENGTH_BUCKETS = [
    (300, 480, "blue",    "DAPI/Blue"),
    (480, 560, "green",   "Green"),
    (560, 620, "red",     "Red"),
    (620, 750, "far_red", "FarRed"),
]

# Position-based fallback — index 0,1,2,3... → (bucket, label)
_POSITION_FALLBACK = [
    ("blue",    "DAPI"),
    ("green",   "Green"),
    ("red",     "Red"),
    ("far_red", "FarRed"),
]


def _match_fluorophore_name(name: Optional[str]) -> Optional[tuple[str, str]]:
    """Try to match a free-text channel/fluorophore name against known patterns.
    Returns (label, bucket) or None if no match."""
    if not name:
        return None
    name_lower = name.lower()
    for pattern, label, bucket in _FLUOROPHORE_PATTERNS:
        if re.search(pattern, name_lower):
            return label, bucket
    return None


def _match_wavelength(emission_nm: Optional[float]) -> Optional[tuple[str, str]]:
    """Bucket an emission wavelength into a spectral region.
    Returns (label, bucket) or None if no match / out of range."""
    if emission_nm is None:
        return None
    try:
        emission_nm = float(emission_nm)
    except (TypeError, ValueError):
        return None
    for lo, hi, bucket, label in _WAVELENGTH_BUCKETS:
        if lo <= emission_nm < hi:
            return label, bucket
    return None


def identify_channel(
    channel_index: int,
    fluorophore_name: Optional[str] = None,
    channel_name: Optional[str] = None,
    emission_wavelength: Optional[float] = None,
    excitation_wavelength: Optional[float] = None,
    pixel_frame=None,
) -> dict:
    """
    Identify a channel's display label using a three-tier strategy.

    Parameters
    ----------
    channel_index : int
        Zero-based channel index — used for the position-based fallback
        and always included in the output label for disambiguation.
    fluorophore_name : str, optional
        Explicit fluorophore name from metadata (e.g. OME 'Fluor' attribute).
    channel_name : str, optional
        Generic channel name from metadata (e.g. OME 'Name' attribute,
        IMS 'ChannelName'). Checked if fluorophore_name doesn't match.
    emission_wavelength : float, optional
        Emission wavelength in nm, used if name-based matching fails.
    excitation_wavelength : float, optional
        Excitation wavelength in nm — used only for the transmitted-light
        heuristic when emission is unavailable (PMT/transmitted channels
        often only record excitation).

    Returns
    -------
    dict with keys:
        label    : str   — short display label, e.g. "DAPI", "EGFP", "C2-Red"
        source   : str   — how it was determined: "name", "wavelength", or "position"
        bucket   : str   — spectral bucket: blue/green/red/far_red/transmitted/unknown
        layer_name : str — full suggested napari layer name, e.g. "C0-DAPI"
    """
    # Tier 1: explicit fluorophore name
    result = _match_fluorophore_name(fluorophore_name)
    source = "name"

    # Tier 1b: generic channel name (some software only populates this)
    if result is None:
        result = _match_fluorophore_name(channel_name)

    # Tier 2: emission wavelength
    if result is None:
        result = _match_wavelength(emission_wavelength)
        source = "wavelength"

    # Tier 2b: if no emission but excitation suggests transmitted light
    # (PMT detectors used for transmitted/DIC often share the excitation
    # laser with a fluorescence channel but have no emission filter)
    if result is None and excitation_wavelength is not None and channel_name:
        if re.search(r"\bpmt\b|transmit|\bt\s*pmt\b", channel_name.lower()):
            result = ("Transmitted", "transmitted")
            source = "name"

    # Tier 2c: measure the PIXELS when metadata is silent. Camera-only
    # acquisitions carry no fluor/emission/name, so rather than fall straight to
    # a meaningless position guess, classify the modality from a frame
    # (fluorescence vs brightfield/DIC/phase). Only used when we have pixels and
    # nothing better matched.
    if result is None and pixel_frame is not None:
        try:
            from pycat.utils.channel_modality import classify_channel_from_pixels
            modality, conf = classify_channel_from_pixels(pixel_frame)
            if modality is not None and conf >= 0.5:
                _MODALITY_LABEL = {
                    'fluorescence': ('Fluorescence', 'unknown'),
                    'brightfield':  ('Brightfield', 'transmitted'),
                    'dic':          ('DIC', 'transmitted'),
                    'phase':        ('Phase', 'transmitted'),
                    'transmitted':  ('Transmitted', 'transmitted'),
                }
                result = _MODALITY_LABEL.get(modality)
                source = "pixels"
        except Exception:
            pass

    # Tier 3: position-based fallback
    if result is None:
        source = "position"
        if channel_index < len(_POSITION_FALLBACK):
            result = _POSITION_FALLBACK[channel_index]
        else:
            result = (f"Ch{channel_index}", "unknown")

    label, bucket = result
    layer_name = f"C{channel_index}-{label}"

    return {
        "label": label,
        "source": source,
        "bucket": bucket,
        "layer_name": layer_name,
        "raw_name": channel_name or fluorophore_name,
    }


def suggest_colormap(bucket: str) -> str:
    """
    Suggest a napari colormap name matching the spectral bucket, so
    channels display in roughly physiologically accurate colors.
    """
    return {
        "blue":        "blue",
        "green":       "green",
        "red":         "red",
        "far_red":     "magenta",
        "transmitted": "gray",
        "unknown":     "gray",
    }.get(bucket, "gray")


# ---------------------------------------------------------------------------
# Metadata extraction helpers for specific sources
# ---------------------------------------------------------------------------

def extract_channel_info(image, channel_index: int, pixel_frame=None) -> dict:
    """
    Extract whatever channel metadata AICSImage exposes (works for OME-TIFF,
    CZI, and other Bio-Formats-compatible formats) and run it through
    identify_channel().

    Parameters
    ----------
    image : AICSImage instance
    channel_index : int

    Returns
    -------
    dict — see identify_channel() return value
    """
    fluor_name = None
    chan_name  = None
    emission   = None
    excitation = None

    try:
        # aicsimageio exposes OME metadata via image.ome_metadata when available
        ome = getattr(image, "ome_metadata", None)
        if ome is not None:
            pixels = ome.images[0].pixels
            if channel_index < len(pixels.channels):
                ch = pixels.channels[channel_index]
                fluor_name = getattr(ch, "fluor", None)
                chan_name  = getattr(ch, "name", None)
                emission   = getattr(ch, "emission_wavelength", None)
                excitation = getattr(ch, "excitation_wavelength", None)
    except Exception:
        pass

    # Fallback: try channel_names property (works even without full OME parse)
    if chan_name is None:
        try:
            names = image.channel_names
            if names and channel_index < len(names):
                chan_name = names[channel_index]
        except Exception:
            pass

    return identify_channel(
        channel_index=channel_index,
        fluorophore_name=fluor_name,
        channel_name=chan_name,
        emission_wavelength=emission,
        excitation_wavelength=excitation,
        pixel_frame=pixel_frame,
    )


def extract_channel_info_from_ims(reader, channel_index: int) -> dict:
    """
    Extract channel metadata from an imaris_ims_file_reader `ims` instance
    and run it through identify_channel().

    IMS files store channel metadata in HDF5 attributes under
    DataSetInfo/Channel N — commonly 'Name', 'Color', 'LSMExcitationWavelength',
    'LSMEmissionWavelength' or similar, though exact attribute names vary
    by acquisition software (Andor Fusion, Imaris itself, etc.).

    Parameters
    ----------
    reader : ims instance (from imaris_ims_file_reader.ims)
    channel_index : int

    Returns
    -------
    dict — see identify_channel() return value
    """
    chan_name  = None
    emission   = None
    excitation = None

    def _decode(v):
        try:
            if hasattr(v, 'tobytes'):
                v = v.tobytes()
            if isinstance(v, (bytes, bytearray)):
                v = v.decode('ascii', errors='ignore')
            s = str(v).strip().strip('\x00').strip()
            return s or None
        except Exception:
            return None

    def _first_float(v):
        s = _decode(v)
        if not s:
            return None
        m = re.findall(r"[\d.]+", s)
        try:
            return float(m[0]) if m else None
        except (IndexError, ValueError):
            return None

    # Primary: read directly from the HDF5 DataSetInfo/Channel N group attributes.
    # This is where Imaris/Fusion actually store per-channel Name (e.g.
    # '488_GFP_CF40um_z', '594_mCherry_CF40', 'BFPreAm') and
    # LSMExcitation/LSMEmissionWavelength. The reader.metaData dict is unreliable
    # and often omits these, which forced the positional fallback.
    hf = getattr(reader, "hf", None)
    if hf is not None:
        try:
            grp = hf["DataSetInfo"][f"Channel {channel_index}"]
            attrs = grp.attrs
            chan_name = _decode(attrs.get("Name"))
            emission = (_first_float(attrs.get("LSMEmissionWavelength"))
                        or _first_float(attrs.get("EmissionWavelength")))
            excitation = (_first_float(attrs.get("LSMExcitationWavelength"))
                          or _first_float(attrs.get("ExcitationWavelength")))
        except Exception:
            pass

    # Fallback: the older reader.metaData dict scan, for readers that expose
    # metadata that way but not via a usable h5py handle.
    if chan_name is None and emission is None and excitation is None:
        try:
            meta = getattr(reader, "metaData", None) or {}
            for key, value in meta.items():
                key_str = str(key).lower()
                if str(channel_index) not in key_str:
                    continue
                if "name" in key_str and chan_name is None:
                    chan_name = str(value)
                elif "emission" in key_str and emission is None:
                    emission = _first_float(value)
                elif "excitation" in key_str and excitation is None:
                    excitation = _first_float(value)
        except Exception:
            pass

    return identify_channel(
        channel_index=channel_index,
        channel_name=chan_name,
        emission_wavelength=emission,
        excitation_wavelength=excitation,
    )
