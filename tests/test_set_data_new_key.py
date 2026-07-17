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


pytestmark = pytest.mark.core


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


def test_a_TYPE_MISMATCH_on_an_existing_key_still_warns_and_keeps_the_old_value(monkeypatch):
    """**Behaviour deliberately UNCHANGED.** The original mismatch branch warned and did not store;
    this preserves that exactly.

    The spec asked to store-anyway here, on the stated grounds of "preserving current behaviour" —
    but the current behaviour rejects, so storing would be a semantic *change*, not a preservation.
    Left as a separate decision (a real one: whether a mismatched write should overwrite, and the
    related quirk that int-seeded numeric keys reject a float update). Pinning the current contract
    so that decision is made deliberately, not drifted into.
    """
    import pycat.data.data_modules as dm

    warnings = []
    monkeypatch.setattr(dm, 'napari_show_warning', lambda msg: warnings.append(msg))

    dc = _data_class()                              # 'cell_df' seeded as an (empty) DataFrame
    dc.set_data('cell_df', "not a dataframe")       # str vs DataFrame — a mismatch

    assert warnings, "a type mismatch must still warn"
    assert isinstance(dc.data_repository['cell_df'], pd.DataFrame), (
        "a mismatched write overwrote the old value — that is a behaviour change, not this fix")
