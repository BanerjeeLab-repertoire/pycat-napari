# -*- coding: utf-8 -*-
"""
Unified metadata extraction across microscopy file formats.

PyCAT reads several formats (TIFF/OME-TIFF, CZI, IMS, generic HDF5), each of
which stores acquisition metadata in a completely different structure:

    - TIFF: baseline TIFF tags (XResolution/ResolutionUnit/DateTime/Software/
      ImageDescription) plus optional ImageJ / OME-XML blocks.
    - CZI / OME-TIFF: OME-XML via AICSImage.
    - IMS (Imaris HDF5): DataSetInfo/Image HDF5 attributes (ExtMax/ExtMin
      extents, LensPower, NumericalAperture, MicroscopeMode, Excitation/
      EmissionWavelength, RecordingDate, Channels, ...).

This module normalises all of these into a single dict with two parts:

    {
      'common': {  # curated, comparable-across-formats fields
          'file_name', 'file_type', 'dimensions' {t,c,z,y,x},
          'pixel_size_um', 'pixel_size_source', 'bit_depth',
          'n_channels', 'n_timepoints', 'n_z',
          'objective', 'numerical_aperture', 'modality',
          'excitation_nm', 'emission_nm', 'acquisition_date', 'software',
      },
      'raw': { ... every key/value the file exposes, as strings ... },
    }

The 'common' block drives the default metadata display and the results-export
JSON; the 'raw' block backs the "show everything" toggle. Every extractor is
defensive: a missing or malformed field yields None rather than raising, so a
partial file still produces a usable record.
"""

import os


def _safe_float(x):
    try:
        if x is None:
            return None
        if hasattr(x, 'tobytes'):
            x = x.tobytes()
        if isinstance(x, (bytes, bytearray)):
            x = x.decode('ascii', errors='ignore')
        s = str(x).strip().strip('\x00').strip()
        # Strip a trailing unit like "521 nm" -> "521"
        s = s.split()[0] if s and s.split() else s
        return float(s) if s else None
    except Exception:
        return None


def _safe_str(x):
    try:
        if x is None:
            return None
        if hasattr(x, 'tobytes'):
            x = x.tobytes()
        if isinstance(x, (bytes, bytearray)):
            x = x.decode('ascii', errors='ignore')
        s = str(x).strip().strip('\x00').strip()
        return s or None
    except Exception:
        return None


def _empty_common(file_path):
    return {
        'file_name': os.path.basename(file_path) if file_path else None,
        'file_type': (os.path.splitext(file_path)[1].lower().lstrip('.')
                      if file_path else None),
        'dimensions': None,
        'pixel_size_um': None,
        'pixel_size_source': None,
        'bit_depth': None,
        'n_channels': None,
        'n_timepoints': None,
        'n_z': None,
        'objective': None,
        'numerical_aperture': None,
        'modality': None,
        'excitation_nm': None,
        'emission_nm': None,
        'acquisition_date': None,
        'software': None,
    }


# ---------------------------------------------------------------------------
# IMS (Imaris HDF5)
# ---------------------------------------------------------------------------

# DataSetInfo/Image attributes we surface into the curated 'common' block.
_IMS_IMAGE_ATTRS = (
    'RecordingDate', 'LensPower', 'NumericalAperture', 'MicroscopeMode',
    'ExcitationWavelength', 'EmissionWavelength', 'Name', 'Description',
    'Unit', 'ManufactorString', 'ManufactorType', 'Channels',
    'NumberOfTimePoints',
)


def extract_ims_metadata(file_path, reader=None, width_px=None):
    """Extract normalised metadata from an Imaris .ims file.

    If a live imaris_ims_file_reader ``reader`` is supplied its h5py handle is
    reused; otherwise the file is opened read-only via h5py.
    """
    common = _empty_common(file_path)
    common['file_type'] = 'ims'
    raw = {}

    hf = None
    opened_here = False
    try:
        hf = getattr(reader, 'hf', None) if reader is not None else None
        if hf is None:
            import h5py
            hf = h5py.File(file_path, 'r')
            opened_here = True

        # DataSetInfo/Image attributes.
        try:
            img = hf['DataSetInfo']['Image']
            attrs = img.attrs
            for k in attrs:
                raw[f'Image/{k}'] = _safe_str(attrs.get(k))
            # Curated fields.
            common['acquisition_date'] = _safe_str(attrs.get('RecordingDate'))
            lp = _safe_float(attrs.get('LensPower'))
            common['objective'] = (f"{lp:g}x" if lp else _safe_str(attrs.get('LensPower')))
            common['numerical_aperture'] = _safe_float(attrs.get('NumericalAperture'))
            common['modality'] = _safe_str(attrs.get('MicroscopeMode'))
            common['excitation_nm'] = _safe_float(attrs.get('ExcitationWavelength'))
            common['emission_nm'] = _safe_float(attrs.get('EmissionWavelength'))
            common['software'] = (_safe_str(attrs.get('ManufactorString'))
                                  or _safe_str(attrs.get('ManufactorType')))

            # Pixel size from extents: (ExtMax0 - ExtMin0) / width.
            ext_min = _safe_float(attrs.get('ExtMin0'))
            ext_max = _safe_float(attrs.get('ExtMax0'))
            # Prefer the passed width; else read ImageSizeX.
            w = width_px
            if w is None:
                w = _safe_float(attrs.get('X')) or _safe_float(attrs.get('ImageSizeX'))
            if (ext_min is not None and ext_max is not None
                    and w and w > 0):
                px = abs(ext_max - ext_min) / float(w)
                if 1e-4 < px < 1e4:
                    common['pixel_size_um'] = px
                    common['pixel_size_source'] = 'ims_extents'
        except Exception:
            pass

        # Channel / timepoint counts and dimensions from the reader if present.
        if reader is not None:
            try:
                common['n_channels'] = int(getattr(reader, 'Channels', None)) \
                    if getattr(reader, 'Channels', None) is not None else None
                common['n_timepoints'] = int(getattr(reader, 'TimePoints', None)) \
                    if getattr(reader, 'TimePoints', None) is not None else None
                shp = getattr(reader, 'shape', None)  # (T, C, Z, Y, X)
                if shp is not None and len(shp) == 5:
                    common['dimensions'] = {
                        't': int(shp[0]), 'c': int(shp[1]), 'z': int(shp[2]),
                        'y': int(shp[3]), 'x': int(shp[4]),
                    }
                    common['n_z'] = int(shp[2])
                dt = getattr(reader, 'dtype', None)
                if dt is not None:
                    import numpy as _np
                    common['bit_depth'] = int(_np.dtype(dt).itemsize * 8)
            except Exception:
                pass

    except Exception:
        pass
    finally:
        if opened_here and hf is not None:
            try:
                hf.close()
            except Exception:
                pass

    return {'common': common, 'raw': raw}


# ---------------------------------------------------------------------------
# TIFF / OME-TIFF
# ---------------------------------------------------------------------------

def extract_tiff_metadata(file_path):
    """Extract normalised metadata from a TIFF / OME-TIFF using tifffile."""
    common = _empty_common(file_path)
    raw = {}
    try:
        import tifffile
    except Exception:
        return {'common': common, 'raw': raw}

    try:
        with tifffile.TiffFile(file_path) as t:
            page = t.pages[0]
            for tag in page.tags:
                raw[tag.name] = _safe_str(tag.value)

            # Dimensions / bit depth.
            w = _safe_float(page.tags.get('ImageWidth').value) if page.tags.get('ImageWidth') else None
            h = _safe_float(page.tags.get('ImageLength').value) if page.tags.get('ImageLength') else None
            bits = page.tags.get('BitsPerSample')
            if bits is not None:
                bv = bits.value
                common['bit_depth'] = int(bv[0]) if isinstance(bv, (tuple, list)) else int(bv)

            # Pixel size from XResolution/ResolutionUnit.
            xres = page.tags.get('XResolution')
            unit_tag = page.tags.get('ResolutionUnit')
            if xres is not None and xres.value is not None:
                val = xres.value
                if isinstance(val, (tuple, list)) and len(val) == 2 and val[1] != 0:
                    ppu = float(val[0]) / float(val[1])
                else:
                    ppu = float(val)
                if ppu > 0:
                    if unit_tag is not None and unit_tag.value is not None:
                        unit = int(unit_tag.value)
                    else:
                        unit = 2
                    microns_per_unit = {3: 10000.0, 2: 25400.0}.get(unit)
                    if microns_per_unit:
                        px = microns_per_unit / ppu
                        if 1e-4 < px < 1e4:
                            common['pixel_size_um'] = px
                            common['pixel_size_source'] = 'tiff_tags'

            # Curated string fields.
            def _tagval(name):
                tg = page.tags.get(name)
                return _safe_str(tg.value) if tg is not None else None
            common['software'] = _tagval('Software')
            common['acquisition_date'] = _tagval('DateTime')
            desc = _tagval('ImageDescription')
            if desc:
                common['modality'] = desc

            # Dimensions (single-page TIFF is 2D; series may add T/C/Z).
            n_pages = len(t.pages)
            common['dimensions'] = {
                't': None, 'c': None, 'z': None,
                'y': int(h) if h else None, 'x': int(w) if w else None,
            }
            if n_pages > 1:
                raw['n_pages'] = str(n_pages)
    except Exception:
        pass

    return {'common': common, 'raw': raw}


# ---------------------------------------------------------------------------
# AICSImage (CZI, OME-TIFF, and TIFFs AICSImage can parse)
# ---------------------------------------------------------------------------

def extract_aicsimage_metadata(file_path, image=None):
    """Extract normalised metadata from an AICSImage object (CZI/OME-TIFF).

    Falls back to opening the file if no image is supplied. The OME model
    exposes structured pixel size, dimensions, and channel info.
    """
    common = _empty_common(file_path)
    raw = {}
    try:
        if image is None:
            from aicsimageio import AICSImage
            image = AICSImage(file_path)

        # Dimensions.
        try:
            dims = image.dims
            common['dimensions'] = {
                't': int(getattr(dims, 'T', 1)),
                'c': int(getattr(dims, 'C', 1)),
                'z': int(getattr(dims, 'Z', 1)),
                'y': int(getattr(dims, 'Y', 0)) or None,
                'x': int(getattr(dims, 'X', 0)) or None,
            }
            common['n_channels'] = common['dimensions']['c']
            common['n_timepoints'] = common['dimensions']['t']
            common['n_z'] = common['dimensions']['z']
        except Exception:
            pass

        # Pixel size.
        try:
            px = image.physical_pixel_sizes
            y = getattr(px, 'Y', None)
            if y:
                common['pixel_size_um'] = float(y)
                common['pixel_size_source'] = 'ome_metadata'
        except Exception:
            pass

        # Channel names.
        try:
            ch = list(getattr(image, 'channel_names', []) or [])
            if ch:
                raw['channel_names'] = ', '.join(str(c) for c in ch)
        except Exception:
            pass

        # Bit depth from dtype.
        try:
            import numpy as _np
            common['bit_depth'] = int(_np.dtype(image.dtype).itemsize * 8)
        except Exception:
            pass

        # Raw OME metadata as a string (best-effort).
        try:
            md = image.metadata
            if md is not None:
                raw['ome_metadata'] = str(md)[:20000]
        except Exception:
            pass
    except Exception:
        pass

    return {'common': common, 'raw': raw}


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

def extract_metadata(file_path, reader=None, image=None, width_px=None):
    """Return normalised {'common', 'raw'} metadata for any supported file.

    Routes by extension. ``reader`` (IMS) and ``image`` (AICSImage) let callers
    reuse an already-open handle instead of re-opening the file.
    """
    ext = os.path.splitext(file_path)[1].lower() if file_path else ''
    if ext == '.ims':
        return extract_ims_metadata(file_path, reader=reader, width_px=width_px)
    if image is not None:
        return extract_aicsimage_metadata(file_path, image=image)
    if ext in ('.tif', '.tiff'):
        # Prefer tifffile for plain TIFFs (reads baseline tags AICSImage skips).
        result = extract_tiff_metadata(file_path)
        # If pixel size still missing, try AICSImage as a secondary source.
        if result['common'].get('pixel_size_um') is None:
            try:
                aics = extract_aicsimage_metadata(file_path)
                if aics['common'].get('pixel_size_um') is not None:
                    result['common']['pixel_size_um'] = aics['common']['pixel_size_um']
                    result['common']['pixel_size_source'] = aics['common']['pixel_size_source']
            except Exception:
                pass
        return result
    # Fallback: try AICSImage for anything else (czi, lif, nd2, ...).
    return extract_aicsimage_metadata(file_path, image=image)
