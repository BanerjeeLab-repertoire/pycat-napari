"""Pure 2-D image reader — extracted from ``FileIOClass.open_2d_image`` (god-class decomposition
piece #2, see docs/audits/fileio_godclass_roadmap_2026-07-15.md).

This holds ONLY the file-path → channel-arrays logic and the per-channel identity extraction. (The
NumPy-2.0 / tifffile ``newbyteorder`` PIL fallback was removed in 1.7.005: tifffile fixed the bug upstream.) The dialog, ``filePath`` bookkeeping,
``update_metadata`` / ``extract_metadata`` repository updates, and napari-layer construction stay in
the controller. Behaviour is preserved exactly: the returned ``(channel_data, file_path, key)``
tuples are byte-identical to what the inline loop produced, in the same order.
"""

from __future__ import annotations

import os

from pycat.file_io.image_reader import open_image, read_plane


def read_2d_image_channels(file_path):
    """Open a 2-D image file and return ``(channels, channel_info, image)``.

    * ``channels``    : list of ``(channel_data, file_path, key)`` in load order — page-major then
                        channel for multi-page (``S > 1``, 1-based running key), else channel index.
    * ``channel_info``: list of per-channel identity dicts (fluorophore / emission / position
                        fallback) from ``extract_channel_info``.
    * ``image``       : the opened reader object, so the controller can run ``update_metadata`` /
                        ``extract_metadata`` (repository side-effects stay in the controller).

    Raises
    ------
    ValueError
        If the structured reader reports neither channels nor pages — same message as the original.
    """
    from pycat.utils.channel_naming import extract_channel_info

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
    single_page = (num_pages <= 1)
    # The file stem is often the ONLY channel identity a plain 2D TIFF carries ('Image1-GFP.tif'):
    # pass it so naming prefers the user's own label over a generic pixel/position guess.
    _stem = os.path.splitext(os.path.basename(file_path))[0] if file_path else None
    for ch_num in range(num_channels):
        try:
            # Give the identifier this channel's pixels so, when metadata is
            # silent, it can classify the modality (fluorescence / brightfield /
            # DIC / phase) instead of falling to a meaningless position guess.
            # Only the single-page path maps channel index → channels[ch_num]
            # positionally; on the multi-page path the mapping is ambiguous, so
            # pass no frame there (metadata/position tiers still apply).
            _frame = channels[ch_num][0] if (single_page and ch_num < len(channels)) else None
            channel_info.append(extract_channel_info(image, ch_num, pixel_frame=_frame, file_stem=_stem))
        except Exception:
            pass

    return channels, channel_info, image
