"""Pure 2-D mask reader — extracted from ``FileIOClass.open_2d_mask`` (god-class decomposition
piece #1, see docs/audits/fileio_godclass_roadmap_2026-07-15.md).

This holds ONLY the file-path → channel-arrays logic. The dialog, filePath bookkeeping, and
napari-layer construction stay in the controller. Behaviour is preserved exactly: the returned list
of ``(channel_data, file_path, key)`` tuples is byte-identical to what the inline loop produced.
"""

from __future__ import annotations

from pycat.file_io.image_reader import open_image, read_plane


def read_2d_mask_channels(file_path):
    """Open a mask file and return its channels as ``(channel_data, file_path, key)`` tuples.

    Mirrors the exact iteration order and keying of the original ``open_2d_mask`` inline loop:

    * multi-page masks (``S > 1``) iterate page-major then channel, with a 1-based running ``key``;
    * single-page masks iterate channels with the channel index as ``key``.

    Parameters
    ----------
    file_path : str
        Path to the mask file (TIFF/PNG/JPG).

    Returns
    -------
    list[tuple]
        ``(channel_data, file_path, key)`` for each channel, in load order.

    Raises
    ------
    ValueError
        If the reader reports neither channels nor pages (unreadable / wrong format) —
        same message as the original.
    """
    mask = open_image(file_path)

    num_pages = getattr(mask.dims, 'S', 1)
    num_channels = getattr(mask.dims, 'C', 1)

    if not hasattr(mask.dims, 'S') and not hasattr(mask.dims, 'C'):
        raise ValueError("Image does not have any channels or pages. Check file format.")

    channels = []
    if num_pages > 1:
        k = 0
        for page_num in range(num_pages):
            for channel_num in range(num_channels):
                k += 1
                channel_data = read_plane(mask, path=file_path, scene=page_num, c=channel_num, t=0)
                channels.append((channel_data, file_path, k))
    else:
        for channel_num in range(num_channels):
            channel_data = read_plane(mask, path=file_path, c=channel_num, t=0)
            channels.append((channel_data, file_path, channel_num))

    return channels
