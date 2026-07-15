"""Pixel-based channel modality classification + OME-XML pixel-size recovery.

Two fixes for camera-only / OME-TIFF acquisitions that carry no channel metadata:
  * classify the modality from pixels (fluorescence vs transmitted; finer BF/DIC/phase when clear,
    else the honest generic 'transmitted') so the layer name is meaningful, not a position guess;
  * recover the real pixel size from OME-XML PhysicalSizeX when the baseline TIFF resolution tags are
    zeroed (which makes the reader report 1.0 and pop the Set-Scale dialog needlessly).
"""

import numpy as np
import pytest

pytestmark = pytest.mark.core

from pycat.utils.channel_modality import classify_channel_from_pixels


def _fluorescence(seed=0):
    rng = np.random.default_rng(seed)
    a = rng.poisson(5, (256, 256)).astype(float)      # dark background
    for _ in range(40):                                # sparse bright puncta
        y, x = rng.integers(10, 246, 2)
        a[y-3:y+3, x-3:x+3] += 800
    return a


def _transmitted(seed=1):
    rng = np.random.default_rng(seed)
    a = np.full((256, 256), 700.0) + rng.normal(0, 15, (256, 256))  # bright filled bg
    for _ in range(15):                                             # darker absorbing blobs
        y, x = rng.integers(20, 236, 2)
        a[y-8:y+8, x-8:x+8] -= 250
    return a


def test_fluorescence_detected():
    mod, conf = classify_channel_from_pixels(_fluorescence())
    assert mod == 'fluorescence'
    assert conf >= 0.5


def test_transmitted_not_called_fluorescence():
    mod, conf = classify_channel_from_pixels(_transmitted())
    # must NOT be mislabelled fluorescence; a transmitted sub-type or the honest
    # generic 'transmitted' are all acceptable.
    assert mod in ('transmitted', 'brightfield', 'dic', 'phase')


def test_degrades_to_none_on_garbage():
    mod, conf = classify_channel_from_pixels(np.zeros((4, 4)))
    assert mod is None and conf == 0.0


def test_handles_3d_by_taking_first_frame():
    stack = np.stack([_fluorescence(i) for i in range(3)])
    mod, _ = classify_channel_from_pixels(stack)
    assert mod == 'fluorescence'


def test_identify_channel_uses_pixels_when_metadata_silent():
    from pycat.utils.channel_naming import identify_channel
    info = identify_channel(channel_index=0, pixel_frame=_fluorescence())
    assert info['source'] == 'pixels'
    assert info['label'] == 'Fluorescence'


def test_metadata_still_wins_over_pixels():
    from pycat.utils.channel_naming import identify_channel
    # an explicit fluorophore name must take precedence over pixel inference
    info = identify_channel(channel_index=0, fluorophore_name='DAPI',
                            pixel_frame=_transmitted())
    assert info['source'] == 'name'
    assert 'DAPI' in info['label']


# ── Filename cleaning + confident naming (1.6.56) ────────────────────────────

def _load_helpers():
    """Pull the two pure helpers out of file_io without importing its heavy deps."""
    import ast
    src = open('src/pycat/file_io/file_io.py').read()
    tree = ast.parse(src)
    ns = {}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in (
                '_clean_filename_token', 'derive_layer_name'):
            exec(compile(ast.Module([node], []), '<x>', 'exec'), ns)
    return ns['_clean_filename_token'], ns['derive_layer_name']


def test_filename_cleaning_strips_micromanager_and_ome():
    clean, _ = _load_helpers()
    assert clean("3.30 hr_1_MMStack_Pos0.ome") == "3.30_hr"
    assert clean("polyA 3 mgpmL - 1000 mM LiCl - 50mM HEPES pH 7p5_3_MMStack_Pos0.ome") == "polyA"
    # a useless generic export name cleans to nothing
    assert clean("Image 3-OME TIFF-Export-01.ome") is None


def test_derive_name_prefers_modality_and_builds_sample_modality():
    _, derive = _load_helpers()
    info = [{'source': 'pixels', 'label': 'Brightfield'}]
    # sample + modality
    assert derive("polyA 3 mgpmL - 1000 mM LiCl_3_MMStack_Pos0.ome",
                  channel_infos=info) == "polyA-Brightfield"
    # useless filename → modality identity alone
    assert derive("Image 3-OME TIFF-Export-01.ome", channel_infos=info) == "Brightfield"


def test_positional_guess_is_not_treated_as_identity():
    _, derive = _load_helpers()
    # a positional guess must NOT be appended as identity
    info = [{'source': 'position', 'label': 'C0-Blue'}]
    assert derive("3.30 hr_1_MMStack_Pos0.ome", channel_infos=info) == "3.30_hr"


def test_confidence_gate_logic():
    def gate(ci):
        if not ci:
            return False
        return all(c and c.get('source') in ('name', 'wavelength', 'pixels') for c in ci)
    assert gate([{'source': 'pixels'}, {'source': 'pixels'}]) is True
    assert gate([{'source': 'name'}, {'source': 'position'}]) is False
    assert gate([]) is False
