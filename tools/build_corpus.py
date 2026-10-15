"""Build the real-data test corpus (Completion Spec Phase 0.1) from the lab's Box test folder.

    python tools/build_corpus.py                       # build into the default corpus dir, rewrite the registry
    python tools/build_corpus.py --out D:/corpus       # elsewhere
    python tools/build_corpus.py --source "C:/.../Test"

One real acquisition per pipeline, each the SMALLEST cut that still exercises the pipeline (crop, decimate frames,
drop channels), plus the negative fixtures the audits found live defects behind. Pixels are copied, never
rescaled; every transform is written into the registry's provenance column. The corpus is NOT committed: the
registry (tests/data/registry.txt, sha256 per file) is, and tests/data/fetch.py resolves files from a local
corpus dir or a release URL and verifies them. A pipeline with no suitable acquisition in the source folder is
listed as MISSING in the registry, so the route matrix says so instead of going blank.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / 'tests' / 'data' / 'registry.txt'
DEFAULT_SOURCE = Path(os.path.expanduser('~')) / 'Box' / '[0] PyCAT manuscript' / 'Test'
UNKNOWN = 'unknown'


def default_corpus_dir() -> Path:
    env = os.environ.get('PYCAT_CORPUS_DIR')
    return Path(env) if env else Path(os.path.expanduser('~')) / '.cache' / 'pycat' / 'corpus' / 'v1'


def _write_tiff(path: Path, data, *, pixel_um=None, axes=None, finterval_s=None, extra=None):
    import tifffile
    kwargs = {'compression': 'zlib'}
    meta = {}
    if axes:
        meta['axes'] = axes
    if finterval_s:
        meta['finterval'] = float(finterval_s)
    if pixel_um:
        kwargs['resolution'] = (1.0 / pixel_um, 1.0 / pixel_um)
        meta['unit'] = 'um'
    if extra:
        meta.update(extra)
    tifffile.imwrite(path, data, imagej=bool(meta) and data.dtype != np.int16 and data.ndim <= 6,
                     metadata=meta or None, **kwargs)


def _read(path: Path):
    import tifffile
    with tifffile.TiffFile(path) as tf:
        return tf.series[0].asarray(), tf


def _pixel_um_from_tags(path: Path):
    """The pixel size the file's resolution tags state, in µm, or None (no tags / a 0 or 2^32-1 sentinel)."""
    import tifffile
    with tifffile.TiffFile(path) as tf:
        page = tf.pages[0]
        res, unit = page.tags.get('XResolution'), page.tags.get('ResolutionUnit')
        if res is None:
            return None
        num, den = res.value
        if num in (0, 4294967295) or den == 0:
            return None
        per_unit = num / den
        unit = unit.value if unit else 1
        um_per_unit = {2: 25400.0, 3: 10000.0}.get(int(unit))
        if um_per_unit is None:   # ResolutionUnit NONE: ImageJ writes the unit in its own metadata
            ij = tf.imagej_metadata or {}
            um_per_unit = 1.0 if str(ij.get('unit', '')).lower() in ('micron', 'um', '\\u00b5m', 'µm') else None
        return (um_per_unit / per_unit) if um_per_unit else None


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, 'rb') as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def _brightest_window(stack2d, size):
    """Top-left corner of the ``size`` x ``size`` window with the highest mean (where the specimen is)."""
    from scipy.ndimage import uniform_filter
    m = uniform_filter(stack2d.astype(np.float32), size=size, mode='constant')
    h = size // 2
    m[:h, :] = m[-h:, :] = -np.inf
    m[:, :h] = m[:, -h:] = -np.inf
    y, x = np.unravel_index(np.argmax(m), m.shape)
    return max(0, y - h), max(0, x - h)


# ── one builder per corpus entry: (src, out) -> (filename, transform, provenance dict) ───────────────────────

def _copy_tiff(rel, name, note='copied unchanged (zlib)'):
    def build(src: Path, out: Path):
        data, _ = _read(src / rel)
        px = _pixel_um_from_tags(src / rel)
        _write_tiff(out / name, data, pixel_um=px)
        return name, note, {'pixel_um': px, 'dtype': str(data.dtype), 'shape': list(data.shape)}
    return build


def _timeseries(src, out):
    import tifffile
    rel = 'Cell and object analysis/Time series cellular object analysis/dissolution.tif'
    with tifffile.TiffFile(src / rel) as tf:
        series = tf.series[0]
        frames = list(range(0, series.shape[0], 2))                      # 60 -> 30 frames (every 2nd)
        stack = np.stack([tf.pages[i].asarray() for i in frames])
    y, x = _brightest_window(stack.mean(axis=0), 512)
    crop = np.ascontiguousarray(stack[:, y:y + 512, x:x + 512])
    px = _pixel_um_from_tags(src / rel)
    _write_tiff(out / 'timeseries_dissolution.tif', crop, pixel_um=px, axes='TYX', finterval_s=20.0)
    return ('timeseries_dissolution.tif', f'every 2nd of 60 frames (20 s apart); crop 512x512 at y={y},x={x}',
            {'pixel_um': px, 'dtype': str(crop.dtype), 'shape': list(crop.shape), 'frame_interval_s': 20.0,
             'emission_nm': 'unknown (561 nm excitation)'})


def _coloc_cyx(src, out):
    base = src / 'Colocalization analysis' / '2d colocalization'
    chans = [_read(base / f)[0] for f in ('im-2_Ch1.tif', 'im-2_Ch2.tif', 'im-2-dapi.tif')]
    cyx = np.stack(chans)
    px = _pixel_um_from_tags(base / 'im-2_Ch1.tif')
    _write_tiff(out / 'coloc_im2_cyx.tif', cyx, pixel_um=px)
    return ('coloc_im2_cyx.tif', 'Ch1, Ch2, DAPI of im-2 stacked as (C, Y, X), pixels unchanged',
            {'pixel_um': px, 'dtype': str(cyx.dtype), 'shape': list(cyx.shape), 'channels': ['Ch1', 'Ch2', 'DAPI']})


def _invitro_bf(src, out):
    rel = 'Cell and object analysis/In-Vitro object analysis (Brightfield)/Tirths data/C=0mM_MMStack_Pos0.ome.tif'
    data, _ = _read(src / rel)
    crop = np.ascontiguousarray(data[688:1712, 688:1712])                 # central 1024 x 1024
    _write_tiff(out / 'invitro_bf_tirth_c0.tif', crop)                    # no pixel size: the source has none
    return ('invitro_bf_tirth_c0.tif', 'central 1024x1024 crop',
            {'pixel_um': None, 'dtype': str(crop.dtype), 'shape': list(crop.shape), 'bit_depth': '12-bit in a 16-bit '
             'container (max 4095)', 'saturated_fraction': float(np.mean(crop == 4095))})


def _vpt(src, out):
    import tifffile
    rel = 'Biophysics/VPT/5 mgml Rg5-5 mgml T40_27C_1_MMStack_Pos0.ome.tif'
    with tifffile.TiffFile(src / rel) as tf:
        stack = np.stack([tf.pages[i].asarray() for i in range(120)])     # first 120 of 1000 frames
    y, x = _brightest_window(stack.std(axis=0), 320)
    crop = np.ascontiguousarray(stack[:, y:y + 320, x:x + 320])
    _write_tiff(out / 'vpt_rg5_t40.tif', crop, axes='TYX')
    return ('vpt_rg5_t40.tif', f'first 120 of 1000 frames; 320x320 crop of highest temporal variance at y={y},x={x}',
            {'pixel_um': None, 'dtype': str(crop.dtype), 'shape': list(crop.shape)})


def _frap(src, out):
    rel = 'Biophysics/FRAP/Ctrap data/RGYGG5-dT40 with Cy5 dT40/20221101-125544 Marker FRAP 32FRAP FIJI_REC.tiff'
    data, _ = _read(src / rel)                                            # (T, Y, X, C) float32
    cut = np.ascontiguousarray(data[::2])
    _write_tiff(out / 'frap_ctrap_rec.tif', cut)
    return ('frap_ctrap_rec.tif', 'every 2nd frame (364 -> 182), all channels', {
        'pixel_um': None, 'dtype': str(cut.dtype), 'shape': list(cut.shape)})


def _h5_reduced(rel, name, max_elements, note):
    def build(src, out):
        import h5py
        with h5py.File(src / rel, 'r') as fin, h5py.File(out / name, 'w') as fout:
            for k, v in fin.attrs.items():
                fout.attrs[k] = v
            dropped = []

            def visit(path, obj):
                if isinstance(obj, h5py.Group):
                    grp = fout.require_group(path)
                    for k, v in obj.attrs.items():
                        grp.attrs[k] = v
                elif obj.size <= max_elements:
                    fin.copy(obj, fout, name=path)
                else:
                    dropped.append(path)
            fin.visititems(visit)
        return name, note + (f'; dropped high-rate datasets {dropped}' if dropped else ''), {}
    return build


def _alias(name, note):
    """A fixture another corpus file already IS -- registered under its own role, pointing at that file."""
    def build(src, out):
        return name, f'same file as {name}: {note}', {}
    return build


def _bg_subtracted_float(src, out):
    from skimage.restoration import rolling_ball
    rel = 'Cell and object analysis/Cellular object analysis (Fluorscence)/In Cell 1-GFP.tif'
    data, _ = _read(src / rel)
    img = data.astype(np.float32)
    sub = np.clip(img - rolling_ball(img, radius=25), 0, None).astype(np.float32)
    _write_tiff(out / 'neg_bg_subtracted_float.tif', sub, pixel_um=_pixel_um_from_tags(src / rel))
    return ('neg_bg_subtracted_float.tif', 'DERIVED: In Cell 1-GFP minus a rolling-ball (r=25) background, '
            'clipped at 0 -> float32 with a hard zero floor', {
                'dtype': 'float32', 'zero_fraction': float(np.mean(sub == 0))})


# (pipeline or fixture, builder or None for MISSING, source, why) -- one real acquisition per pipeline
ENTRIES = [
    ('condensate', _copy_tiff('Cell and object analysis/Cellular object analysis (Fluorscence)/In Cell 1-GFP.tif',
                              'condensate_incell1_gfp.tif'), 'In Cell 1-GFP.tif'),
    ('condensate', _copy_tiff('Cell and object analysis/Cellular object analysis (Fluorscence)/In Cell 1-DAPI.tif',
                              'condensate_incell1_dapi.tif'), 'In Cell 1-DAPI.tif'),
    ('timeseries', _timeseries, 'dissolution.tif'),
    ('coloc', _coloc_cyx, '2d colocalization/im-2_*'),
    ('cellular_bf', None, 'no cellular brightfield acquisition in the test folder'),
    ('invitro_fluor', _copy_tiff('Cell and object analysis/In-Vitro object analysis (Fluroscence)/Abhradeep/'
                                 'RG5 T40 rgygg3594 300nM client-100percent laser/image 1.tif',
                                 'invitro_fluor_rg5_t40_image1.tif'), 'Abhradeep/.../image 1.tif'),
    ('timeseries_invitro_fluor', None, 'the "Time series in-vitro object analysis" folder is empty'),
    ('invitro_bf', _invitro_bf, 'Tirths data/C=0mM_MMStack_Pos0.ome.tif'),
    ('fibril', _copy_tiff('Fibirl analysis/fibrils.tif', 'fibril_fibrils.tif'), 'Fibirl analysis/fibrils.tif'),
    ('zstack', None, 'no z-stack acquisition in the test folder'),
    ('vpt', _vpt, 'VPT/5 mgml Rg5-5 mgml T40_27C_1_MMStack_Pos0.ome.tif'),
    ('frap', _frap, 'FRAP/Ctrap data/... FIJI_REC.tiff'),
    ('fusion', _h5_reduced('Biophysics/Droplet fusion (C-Trap)/Poly(rA) condensates/20231023-173750 Marker 4.h5',
                           'fusion_marker4.h5', 10 ** 9, 'copied (all datasets)'), 'Marker 4.h5'),
    ('temperature', _copy_tiff('Biophysics/Temperature dependent microscopy/2d slice test polyA 3 mgpmL - 1000 mM '
                               'LiCl - 50mM HEPES pH 7p5_3_MMStack_Pos0.omeSubstack (109).tif',
                               'temperature_polya_substack.tif',
                               'copied unchanged; NO matching temperature CSV (stack 2024-12-28, CSVs 2021-22)'),
     'Temperature dependent microscopy/2d slice test ... Substack (109).tif'),
    ('fd_curve', _h5_reduced('Biophysics/Force distance curve (DNA-tethering)/DNA extension and relaxation cycles/'
                             '20240725-151943 Marker 3.h5', 'fd_marker3_lf.h5', 100_000,
                             'low-rate datasets copied unchanged'), 'Marker 3.h5'),
    # ── negative fixtures (Completion Spec 0.1) ──
    ('neg:no_pixel_size', _copy_tiff('Fibirl analysis/A488 1 hr.tif', 'neg_no_pixel_size_a488_1hr.tif',
                                     'copied unchanged: the file has NO resolution tags; 8-bit data in a 16-bit '
                                     'container, 1.8% of pixels at 255 (also the saturated fixture)'),
     'Fibirl analysis/A488 1 hr.tif'),
    ('neg:bg_subtracted_float', _bg_subtracted_float, 'derived from In Cell 1-GFP.tif'),
    ('neg:zstack', None, 'no real z-stack in the test folder'),
    ('neg:cyx_multichannel', _alias('coloc_im2_cyx.tif', 'real (C, Y, X) three-channel file'), 'coloc entry'),
    ('neg:saturated', _alias('neg_no_pixel_size_a488_1hr.tif', 'real: 1.8% of pixels at 255'), 'A488 1 hr.tif'),
    ('neg:12bit_in_16bit', _alias('invitro_bf_tirth_c0.tif', 'real: 12-bit camera data in uint16, max 4095, '
                                  '0.06% at the ceiling'), 'Tirths C=0mM'),
]

_ANNOTATION_CAVEAT = ('Box masks: boundaries were drawn to measure objects; counts are loosely accurate; the '
                      'boundary is what is at question (Completion Spec 0.4) -- count metrics may be asserted, '
                      'boundary metrics only recorded.')


def build(source: Path, out: Path) -> list[dict]:
    out.mkdir(parents=True, exist_ok=True)
    rows = []
    for pipeline, builder, origin in ENTRIES:
        row = {'pipeline': pipeline, 'source': origin, 'instrument': UNKNOWN, 'objective': UNKNOWN, 'na': UNKNOWN,
               'acquired_by': UNKNOWN, 'annotation_caveat': _ANNOTATION_CAVEAT}
        if builder is None:
            row.update(filename='MISSING', sha256='-', size=0, transform=origin)
            rows.append(row)
            print(f'  {pipeline:28s} MISSING  ({origin})')
            continue
        name, transform, meta = builder(source, out)
        path = out / name
        row.update(filename=name, sha256=_sha256(path), size=path.stat().st_size, transform=transform, **meta)
        rows.append(row)
        print(f'  {pipeline:28s} {name:40s} {path.stat().st_size / 1e6:6.2f} MB')
    return rows


def write_registry(rows: list[dict]) -> None:
    lines = ['# PyCAT real-data corpus registry (Completion Spec 0.1). GENERATED by tools/build_corpus.py.',
             '# filename<TAB>sha256<TAB>size<TAB>pipeline<TAB>provenance(JSON)',
             '# Provenance "unknown" fields are honest gaps -- fill them from the acquisition records.']
    for r in rows:
        prov = {k: v for k, v in r.items() if k not in ('filename', 'sha256', 'size', 'pipeline')}
        lines.append('\t'.join([r['filename'], r['sha256'], str(r['size']), r['pipeline'],
                                json.dumps(prov, sort_keys=True, default=str)]))
    REGISTRY.parent.mkdir(parents=True, exist_ok=True)
    REGISTRY.write_text('\n'.join(lines) + '\n', encoding='utf-8', newline='\n')
    total = sum(r['size'] for r in rows) / 1e6
    print(f'wrote {REGISTRY.relative_to(ROOT)} ({len(rows)} entries, {total:.1f} MB)')


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('--source', type=Path, default=DEFAULT_SOURCE)
    ap.add_argument('--out', type=Path, default=default_corpus_dir())
    args = ap.parse_args(argv)
    if not args.source.is_dir():
        print(f'source folder not found: {args.source}')
        return 1
    print(f'building corpus from {args.source}\n          into {args.out}')
    write_registry(build(args.source, args.out))
    return 0


if __name__ == '__main__':
    sys.exit(main())
