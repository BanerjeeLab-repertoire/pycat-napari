"""**Old path-based session entity-ids migrate to the durable UUID on load (dataset_identity_uuid step 4).**

Pre-1.6.191 sessions embedded the file PATH as the `dataset_id` prefix of every `_pycat_entity_id`; the id is
now UUID-prefixed. Because brushing and the entity registry match the WHOLE id string exactly, an un-migrated
`path/…` id never matches a freshly-derived `uuid/…` id for the same object — resolution silently fails. These
pin the prefix-swap migration: it upgrades the path prefix to the UUID while leaving operation/type/frame/label
byte-identical, tolerates `/` vs `\\` path spellings, is idempotent, and — the load parity that matters — a
migrated id equals what a fresh stamp now produces for the same object.
"""
import numpy as np
import pandas as pd
import pytest

from pycat.utils.entity_ref import (
    ENTITY_ID_COLUMN, entity_id_column, migrate_entity_id_dataset)

pytestmark = pytest.mark.base


def _df(ids):
    return pd.DataFrame({ENTITY_ID_COLUMN: ids, "area": np.arange(len(ids), dtype=float)})


def test_a_path_prefix_is_swapped_for_the_uuid_leaving_the_rest_intact():
    old = "C:/data/im-1.tif"
    ids = [entity_id_column(old, "cell_analysis", "cell", 0, 1),
           entity_id_column(old, "cell_analysis", "cell", 0, 2)]
    df = _df(ids)
    n = migrate_entity_id_dataset(df, old, "UUID-123")
    assert n == 2
    # the whole id now equals what a fresh stamp under the UUID produces → exact-string resolution matches
    assert df[ENTITY_ID_COLUMN].tolist() == [
        entity_id_column("UUID-123", "cell_analysis", "cell", 0, 1),
        entity_id_column("UUID-123", "cell_analysis", "cell", 0, 2)]


def test_the_suffix_after_the_dataset_id_is_untouched():
    old = "/mnt/exp/a.tif"
    one = entity_id_column(old, "puncta_analysis", "puncta", 3, 7, parent=2)
    df = _df([one])
    migrate_entity_id_dataset(df, old, "U")
    # everything after the swapped prefix is byte-identical
    assert df[ENTITY_ID_COLUMN][0] == "U/" + one[len(old) + 1:]


def test_spelling_tolerant_backslash_path_migrates_when_old_given_with_forward_slashes():
    # id was stamped on Windows (backslashes); the manifest hands us the same path forward-slashed.
    win_id = "C:\\data\\im.tif" + "/cell_analysis/cell/0/1"
    df = _df([win_id])
    n = migrate_entity_id_dataset(df, "C:/data/im.tif", "UUID-9")
    assert n == 1 and df[ENTITY_ID_COLUMN][0] == "UUID-9/cell_analysis/cell/0/1"


def test_rows_not_under_the_old_prefix_are_left_alone():
    keep = entity_id_column("OTHER-DATASET", "op", "cell", 0, 1)
    df = _df([entity_id_column("C:/a.tif", "op", "cell", 0, 1), keep])
    n = migrate_entity_id_dataset(df, "C:/a.tif", "U")
    assert n == 1 and df[ENTITY_ID_COLUMN][1] == keep      # the other dataset's id is untouched


def test_migration_is_idempotent_and_a_noop_when_already_migrated():
    df = _df([entity_id_column("U", "op", "cell", 0, 1)])
    assert migrate_entity_id_dataset(df, "C:/a.tif", "U") == 0   # nothing carries the old path prefix
    assert migrate_entity_id_dataset(df, "U", "U") == 0          # old == new → no-op


def test_a_df_without_the_column_or_empty_inputs_is_safe():
    assert migrate_entity_id_dataset(pd.DataFrame({"area": [1.0]}), "C:/a.tif", "U") == 0
    assert migrate_entity_id_dataset(None, "C:/a.tif", "U") == 0
    assert migrate_entity_id_dataset(_df([entity_id_column("C:/a.tif", "op", "cell", 0, 1)]), None, "U") == 0


def test_migration_uses_the_registry_uuid_so_resolution_matches_a_fresh_stamp(tmp_path):
    # End-to-end: the UUID the migration targets is the registry's, so a migrated id equals a freshly-stamped
    # one for the moved/reopened file — which is exactly what brushing/registry look-ups compare.
    from pycat.utils.dataset_identity import DatasetRegistry
    p = str(tmp_path / "acq.tif")
    with open(p, "wb") as f:
        f.write(b"OMEDATA" * 500)
    uuid = DatasetRegistry().mint_or_recognise(p).uuid
    df = _df([entity_id_column(p, "cell_analysis", "cell", 0, 5)])
    migrate_entity_id_dataset(df, p, uuid)
    assert df[ENTITY_ID_COLUMN][0] == entity_id_column(uuid, "cell_analysis", "cell", 0, 5)
