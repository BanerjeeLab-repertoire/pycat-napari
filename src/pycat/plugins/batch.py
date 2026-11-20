"""Stable helpers for a plugin's batch steps — the part of PyCAT's batch internals a step needs, behind names that
are part of the plugin API (the ``batch.steps._common`` helpers they wrap are private and may change).
"""
from __future__ import annotations

from pathlib import Path


def resolve_layer(state: dict, layer_name, fallback=None):
    """The array for a layer name the panel RECORDED, resolved in this file's batch ``state`` the way PyCAT's own
    steps resolve it (channel, processing stage, napari's ``' [N]'`` duplicates). ``fallback`` if unresolved."""
    from pycat.batch.steps._common import _resolve_image_layer
    return _resolve_image_layer(state, layer_name, fallback)


def data_repository(state: dict) -> dict:
    """This file's data repository (``state['data_instance'].data_repository``): pixel size, metadata, tables."""
    di = state.get('data_instance')
    return di.data_repository if di is not None else state.setdefault('_plugin_repository', {})


def write_object_table(state: dict, table, key: str, image_path, output_dir) -> Path:
    """Store a per-object table under ``key`` (``'<type>_df'``) in this file's data repository and write it where
    the consolidated table reads it (``<output_dir>/<stem>_<key>.csv``). Returns the CSV path. Declare the key
    once with ``api.add_object_table(key)`` for it to reach ``consolidated_long.csv``."""
    if not str(key).endswith('_df'):
        raise ValueError(f"object table key {key!r} must be '<type>_df'")
    di = state.get('data_instance')
    if di is not None and hasattr(di, 'set_data'):
        di.set_data(key, table)
    else:
        data_repository(state)[key] = table
    path = Path(output_dir) / f"{Path(image_path).stem}_{key}.csv"
    table.to_csv(path, index=False)
    return path
