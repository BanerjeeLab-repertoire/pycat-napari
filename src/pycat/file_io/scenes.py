"""**Multi-scene (multi-position) helpers — Qt-free, so they can be tested headlessly.**

A multi-position acquisition (CZI/IMS/OME-TIFF) holds several *scenes* — positions, wells, tiles. The
loader used to materialise every selected scene at once; the scene switcher loads **one at a time,
lazily**, and switches in place. The data-layer machinery for that lives here (the Qt switcher widget
is a thin consumer of these functions):

- `list_scenes` / `scene_index` — enumerate positions and locate one by name;
- `build_scene_stack` — construct the lazy `_SceneStack` for one scene, reading only its dims;
- `tag_scene_layer` — record WHICH position a layer holds, so results/exports carry it and it is
  joinable to the comparative-phenotyping sample metadata (a position is often a condition).

Everything here is import-clean without Qt/napari (`_SceneStack` and `read_plane` are Qt-free), which is
the contract the switcher relies on and the headless tests exercise.
"""

from __future__ import annotations

from pycat.file_io.lazy_sources import _SceneStack


# The layer-tag key + source used to record a layer's scene. `from_metadata` matches how `channel` is
# tagged on open (tagging.py) — it is a fact read off the file, not an inference.
SCENE_TAG_KEY = 'scene'
_SCENE_TAG_SOURCE = 'from_metadata'


def list_scenes(image) -> list:
    """The scene (position) names a reader exposes, as a list. ``[]`` for a single-scene file."""
    return list(getattr(image, 'scenes', []) or [])


def scene_index(image, scene) -> int:
    """The index of ``scene`` (a name) within the reader's scene list; ``0`` if it cannot be found."""
    scenes = list_scenes(image)
    try:
        return scenes.index(scene)
    except (ValueError, AttributeError):
        return 0


def _scene_dims(image):
    """``(n_t, n_z, n_c, H, W)`` for the reader's CURRENTLY selected scene, defaulting missing axes to
    1. Reads ``image.dims`` only — no pixel data is touched."""
    dims = getattr(image, 'dims', None)
    n_t = int(getattr(dims, 'T', 1) or 1)
    n_z = int(getattr(dims, 'Z', 1) or 1)
    n_c = int(getattr(dims, 'C', 1) or 1)
    H = int(getattr(dims, 'Y', 0) or 0)
    W = int(getattr(dims, 'X', 0) or 0)
    return n_t, n_z, n_c, H, W


def build_scene_stack(image, scene, *, channel_idx=0, z=0, src_dtype=None, plane_reader=None):
    """Build the lazy ``_SceneStack`` for one scene, reading **only that scene's dimensions**.

    Pins the reader to ``scene`` (so ``image.dims`` reflects it), reads the frame count and frame size,
    and hands back a wrapper that reads one plane at a time from that position. No pixel data is
    materialised. ``plane_reader`` is injected for tests; production uses the scene-pinning
    ``image_reader.read_plane``.
    """
    if scene is not None and hasattr(image, 'set_scene'):
        image.set_scene(scene)

    n_t, _n_z, _n_c, H, W = _scene_dims(image)
    if src_dtype is None:
        src_dtype = getattr(getattr(image, 'dtype', None), 'name', None) or 'uint16'

    return _SceneStack(image, scene, n_t=n_t, H=H, W=W, dtype=src_dtype,
                       channel_idx=channel_idx, z=z, plane_reader=plane_reader)


def tag_scene_layer(layer, scene):
    """Record on ``layer`` which scene (position) it holds — a tagged fact, not a name-suffix guess.

    Multi-position experiments need the position on every results row and export, and the
    comparative-phenotyping sample-metadata join keys on identity, so the position must be a queryable
    tag, not a display-only string in the layer name. Returns True if the tag was written.
    """
    if scene is None:
        return False
    try:
        from pycat.utils.layer_tags import tag_layer
        return bool(tag_layer(layer, SCENE_TAG_KEY, str(scene), source=_SCENE_TAG_SOURCE))
    except Exception as exc:
        from pycat.utils.general_utils import debug_log
        debug_log('scenes: could not tag the layer with its scene', exc)
        return False


def scene_of(layer):
    """The scene a layer is tagged with, or ``None`` — the read side of :func:`tag_scene_layer`."""
    try:
        from pycat.utils.layer_tags import get_tag
        return get_tag(layer, SCENE_TAG_KEY)
    except Exception:
        return None
