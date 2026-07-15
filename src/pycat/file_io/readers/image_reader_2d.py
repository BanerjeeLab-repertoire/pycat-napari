"""Pure 2-D image reader — extracted from ``FileIOClass.open_2d_image`` (god-class decomposition
piece #2, see docs/audits/fileio_godclass_roadmap_2026-07-15.md).

This holds ONLY the file-path → channel-arrays logic, including the NumPy-2.0 / tifffile PIL
fallback and the per-channel identity extraction. The dialog, ``filePath`` bookkeeping,
``update_metadata`` / ``extract_metadata`` repository updates, and napari-layer construction stay in
the controller. Behaviour is preserved exactly: the returned ``(channel_data, file_path, key)``
tuples are byte-identical to what the inline loop produced, in the same order.
"""

from __future__ import annotations

import os

from pycat.file_io.image_reader import open_image, read_plane


def _read_channels_via_pil(file_path):
    """NumPy-2.0 / tifffile fallback: PIL has an independent TIFF reader that avoids the
    ``newbyteorder`` conflict. Returns the same ``(array, file_path, key)`` tuples as the normal
    path, or ``None`` if PIL also fails. Mirrors the original inline fallback exactly (float32,
    per-frame seek, 1-based-ish keying: single frame keyed 0, multi-frame keyed by frame index)."""
    try:
        from PIL import Image as _PILImage
        import numpy as _np
        _pil_img = _PILImage.open(file_path)
        _frames = []
        try:
            while True:
                _frames.append(_np.array(_pil_img).astype('float32'))
                _pil_img.seek(_pil_img.tell() + 1)
        except EOFError:
            pass
        channels = []
        if len(_frames) == 1:
            channels.append((_frames[0], file_path, 0))
        else:
            for _ci, _frame in enumerate(_frames):
                channels.append((_frame, file_path, _ci))
        return channels
    except Exception:
        return None


def read_2d_image_channels(file_path):
    """Open a 2-D image file and return ``(channels, channel_info, image, used_pil_fallback)``.

    * ``channels``    : list of ``(channel_data, file_path, key)`` in load order — page-major then
                        channel for multi-page (``S > 1``, 1-based running key), else channel index.
    * ``channel_info``: list of per-channel identity dicts (fluorophore / emission / position
                        fallback) from ``extract_channel_info`` — empty on the PIL fallback path.
    * ``image``       : the opened reader object, so the controller can run ``update_metadata`` /
                        ``extract_metadata`` (repository side-effects stay in the controller). ``None``
                        on the PIL fallback path.
    * ``used_pil_fallback`` : True when the NumPy-2.0 PIL path was taken (the controller emits the
                        user-facing warning, exactly as before).

    Raises
    ------
    ValueError
        If the structured reader reports neither channels nor pages — same message as the original.
    """
    from pycat.utils.channel_naming import extract_channel_info

    # Detect the NumPy-2.0 / tifffile ``newbyteorder`` conflict lazily — only touch dask-backed
    # metadata so no full read happens.
    _use_fallback = False
    try:
        image = open_image(file_path)
        _ = image.xarray_dask_data.dims
    except AttributeError as _e:
        if "newbyteorder" not in str(_e):
            raise
        _use_fallback = True
        print(f"[PyCAT] NumPy 2.0 tifffile fallback for {os.path.basename(file_path)}")
    except Exception:
        # Any other error on metadata access — try the normal path anyway.
        image = open_image(file_path)

    if _use_fallback:
        channels = _read_channels_via_pil(file_path)
        return channels, [], None, True

    # Normal structured-reader path. Re-open to match the original code exactly (it called
    # open_image again after the metadata probe).
    image = open_image(file_path)

    num_pages = getattr(image.dims, 'S', 1)
    num_channels = getattr(image.dims, 'C', 1)
    if not hasattr(image.dims, 'S') and not hasattr(image.dims, 'C'):
        raise ValueError("Image does not have any channels or pages. Check file format.")

    channels = []
    if num_pages > 1:
        k = 0
        for page_num in range(num_pages):
            for channel_num in range(num_channels):
                k += 1
                channel_data = read_plane(image, path=file_path, scene=page_num,
                                          c=channel_num, t=0)
                channels.append((channel_data, file_path, k))
    else:
        for channel_num in range(num_channels):
            channel_data = read_plane(image, path=file_path, c=channel_num, t=0)
            channels.append((channel_data, file_path, channel_num))

    channel_info = []
    for ch_num in range(num_channels):
        try:
            channel_info.append(extract_channel_info(image, ch_num))
        except Exception:
            pass

    return channels, channel_info, image, False
