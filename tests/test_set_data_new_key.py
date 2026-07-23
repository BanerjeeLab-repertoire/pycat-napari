"""**`set_data` crashed on a genuinely new key.**

`BaseDataClass.set_data` checked the stored value's class *before* checking whether the key existed:

    if self.data_repository[key].__class__ != data.__class__:   # KeyError if key is new
        ...
    elif key not in self.data_repository:                        # too late
        ...

`data_repository` is a plain dict, so the first line raised `KeyError` for any key not already in it.
It was masked only because the repository is pre-seeded with the common keys, so most callers happen
to hit an existing one — a real crash waiting for the first caller that stores something new.

Fix: check existence first.
"""

# Third party imports
import pandas as pd
import pytest


pytestmark = pytest.mark.base


def _data_class():
    from pycat.data.data_modules import BaseDataClass
    return BaseDataClass()


def test_storing_a_BRAND_NEW_key_does_not_crash():
    """The bug, directly: a key the repository was not seeded with."""
    dc = _data_class()
    assert 'a_key_nobody_seeded' not in dc.data_repository

    dc.set_data('a_key_nobody_seeded', pd.DataFrame({'x': [1, 2]}))   # used to raise KeyError

    assert 'a_key_nobody_seeded' in dc.data_repository
    assert list(dc.data_repository['a_key_nobody_seeded']['x']) == [1, 2]


def test_a_new_key_of_ANY_type_is_stored():
    dc = _data_class()
    dc.set_data('an_int', 7)
    dc.set_data('a_dict', {'a': 1})
    dc.set_data('an_array', [1, 2, 3])

    assert dc.data_repository['an_int'] == 7
    assert dc.data_repository['a_dict'] == {'a': 1}
    assert dc.data_repository['an_array'] == [1, 2, 3]


def test_updating_an_EXISTING_key_of_the_same_type_still_deepcopies():
    """Behaviour-preserving: an existing key, same class, is stored as a deepcopy (so the caller's
    later mutations do not reach into the repository)."""
    dc = _data_class()
    df = pd.DataFrame({'y': [1]})
    dc.set_data('cell_df', df)                      # 'cell_df' is a seeded key (empty DataFrame)

    df.loc[0, 'y'] = 999                            # mutate the caller's copy
    assert dc.data_repository['cell_df'].loc[0, 'y'] == 1, "the store was not a deep copy"


def test_a_TYPE_MISMATCH_on_an_existing_key_WARNS_and_STORES(monkeypatch):
    """**`set_data` is a setter: it warns on a type change, then still sets.**

    The decision flagged when the reorder first shipped is resolved here in this direction. The old
    reject (warn, keep the old value) was its own silent failure — a caller believed it had updated
    the key while the stale value persisted.
    """
    import pycat.data.data_modules as dm

    warnings = []
    monkeypatch.setattr(dm, 'napari_show_warning', lambda msg: warnings.append(msg))

    dc = _data_class()                              # 'cell_df' seeded as an (empty) DataFrame
    dc.set_data('cell_df', "not a dataframe")       # str vs DataFrame — a mismatch

    assert warnings, "a type mismatch must warn"
    assert dc.data_repository['cell_df'] == "not a dataframe", (
        "a mismatched write must still STORE — the warning is advisory, not a rejection")


def test_an_INT_seeded_key_accepts_a_FLOAT_update(monkeypatch):
    """The concrete win from storing on mismatch: a key seeded as `int` (`microns_per_pixel_sq`)
    used to reject a legitimate `float` update, silently keeping the stale int."""
    import pycat.data.data_modules as dm
    monkeypatch.setattr(dm, 'napari_show_warning', lambda msg: None)

    dc = _data_class()                              # 'microns_per_pixel_sq' seeded as int 1
    assert isinstance(dc.data_repository['microns_per_pixel_sq'], int)

    dc.set_data('microns_per_pixel_sq', 0.0425)     # int -> float
    assert dc.data_repository['microns_per_pixel_sq'] == 0.0425, (
        "the float update was rejected, leaving the stale int")
