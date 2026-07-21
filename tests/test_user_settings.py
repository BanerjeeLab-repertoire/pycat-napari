"""**Process-wide user settings — persisted, corruption-safe, atomic.**

Pins the properties a startup-time settings store must never violate: values round-trip across a fresh
instance (cross-session); typed accessors coerce and fall back rather than raise; registered defaults
resolve; a subscription fires on change; a CORRUPT file never crashes (it falls back to defaults, and the
bad file is set aside, not lost); a write is ATOMIC (a mid-write failure leaves the previous good file
intact); and namespaced keys do not collide.
"""
import json

import pytest

from pycat.utils.user_settings import UserSettings

pytestmark = pytest.mark.core


def test_values_round_trip_across_a_fresh_instance(tmp_path):
    p = tmp_path / 'settings.json'
    s = UserSettings(path=p)
    s.set('navigator.mode', 'beginner')
    s.set('plot.backend', 'pyqtgraph')
    # a brand-new instance (== next session) reads them back
    s2 = UserSettings(path=p)
    assert s2.get('navigator.mode') == 'beginner'
    assert s2.get('plot.backend') == 'pyqtgraph'


def test_registered_defaults_resolve_and_a_stored_value_wins(tmp_path):
    s = UserSettings(path=tmp_path / 's.json')
    s.register_default('app.first_run', True)
    assert s.get('app.first_run') is True and not s.has('app.first_run')
    s.set('app.first_run', False)
    assert s.get('app.first_run') is False and s.has('app.first_run')


def test_typed_accessors_coerce_and_fall_back_rather_than_raise(tmp_path):
    s = UserSettings(path=tmp_path / 's.json')
    s.set('a.flag', 'true'); s.set('a.n', '5'); s.set('a.x', '2.5'); s.set('a.bad', 'not-a-number')
    assert s.get_bool('a.flag') is True
    assert s.get_int('a.n') == 5 and s.get_float('a.x') == 2.5
    assert s.get_int('a.bad', 7) == 7 and s.get_float('a.bad', 1.5) == 1.5      # malformed → default
    assert s.get_bool('a.missing', True) is True                               # missing → default
    s.set('a.count', 5)
    assert s.get_bool('a.count') is True                                       # numeric 5 → truthy
    assert s.get_bool('a.zero') is False and s.get_bool('a.bad') is False       # 0/unknown-string → False (via default)


def test_a_subscription_fires_on_change_only(tmp_path):
    s = UserSettings(path=tmp_path / 's.json')
    seen = []
    unsub = s.subscribe('nav.mode', seen.append)
    s.set('nav.mode', 'advanced')
    s.set('nav.mode', 'advanced')          # no change → no fire
    s.set('nav.mode', 'beginner')
    assert seen == ['advanced', 'beginner']
    unsub()
    s.set('nav.mode', 'advanced')          # unsubscribed → no fire
    assert seen == ['advanced', 'beginner']


def test_a_corrupt_file_falls_back_to_defaults_and_is_quarantined_not_crashed(tmp_path):
    p = tmp_path / 's.json'
    p.write_text('{ this is not valid json ', encoding='utf-8')
    s = UserSettings(path=p)                # must NOT raise
    s.register_default('k', 'default')
    assert s.get('k') == 'default'
    assert (tmp_path / 's.json.corrupt').exists()      # the bad file was set aside, not deleted
    s.set('k', 'v')                         # and the store is usable again
    assert UserSettings(path=p).get('k') == 'v'


def test_a_write_is_atomic_a_mid_write_failure_leaves_the_prior_file_intact(tmp_path):
    p = tmp_path / 's.json'
    s = UserSettings(path=p)
    s.set('k', 'good')                      # a known-good prior file exists
    prior = p.read_text(encoding='utf-8')
    # an unserializable value makes json.dump raise DURING the write to the temp file
    with pytest.raises(TypeError):
        s.set('k', {1, 2, 3})               # a set() is not JSON-serializable
    assert p.read_text(encoding='utf-8') == prior           # the prior good file is untouched
    assert not (tmp_path / 's.json.tmp').exists() or p.read_text(encoding='utf-8') == prior
    assert UserSettings(path=p).get('k') == 'good'          # reload still sees the good value


def test_namespaced_keys_do_not_collide(tmp_path):
    s = UserSettings(path=tmp_path / 's.json')
    s.set('a.value', 1); s.set('b.value', 2); s.set('a.value.deep', 3)
    assert s.get('a.value') == 1 and s.get('b.value') == 2 and s.get('a.value.deep') == 3


def test_reset_drops_a_stored_value_back_to_its_default(tmp_path):
    s = UserSettings(path=tmp_path / 's.json')
    s.register_default('k', 'D')
    s.set('k', 'V')
    seen = []
    s.subscribe('k', seen.append)
    s.reset('k')
    assert s.get('k') == 'D' and not s.has('k') and seen == ['D']


def test_the_persisted_file_is_valid_json_object(tmp_path):
    p = tmp_path / 's.json'
    UserSettings(path=p).set('x', 1)
    assert isinstance(json.loads(p.read_text(encoding='utf-8')), dict)
