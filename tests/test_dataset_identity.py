"""**Dataset identity — a durable UUID that survives a move, never merges two datasets, never hashes whole.**

Identity was the file path, which breaks on move/remount/cross-platform. These pin the UUID mechanism:
the same file loads to the same UUID; a moved file (same bytes, new path) is recognised by fingerprint and
KEEPS its UUID; a borderline match (same size, different bytes) becomes a NEW dataset, never a merge; an
OME UUID is authoritative when present; and the partial hash reads BOUNDED bytes, never the whole
multi-gigabyte file.
"""
import os

import pytest

from pycat.utils.dataset_identity import (DatasetRegistry, compute_fingerprint, fingerprints_match,
                                          bounded_partial_hash, DatasetFingerprint)

pytestmark = pytest.mark.core


def _write(path, data):
    with open(path, 'wb') as f:
        f.write(data)
    return str(path)


def test_the_same_file_loaded_twice_gets_the_same_uuid(tmp_path):
    reg = DatasetRegistry()
    p = _write(tmp_path / 'a.tif', b'HELLO' * 1000)
    u1 = reg.mint_or_recognise(p).uuid
    u2 = reg.mint_or_recognise(p).uuid
    assert u1 == u2 and len(reg) == 1


def test_a_moved_file_is_RECOGNISED_by_fingerprint_and_keeps_its_uuid(tmp_path):
    reg = DatasetRegistry()
    data = bytes(range(256)) * 500
    a = _write(tmp_path / 'orig.tif', data)
    u_orig = reg.mint_or_recognise(a).uuid
    # "move": same bytes, different path
    b = _write(tmp_path / 'moved' / 'orig.tif' if False else tmp_path / 'renamed.tif', data)
    ident = reg.mint_or_recognise(b)
    assert ident.uuid == u_orig, "a moved dataset (same bytes) must keep its UUID"
    assert ident.original_path == b and len(reg) == 1          # path updated to the new location


def test_a_borderline_match_same_size_different_bytes_is_a_NEW_dataset(tmp_path):
    reg = DatasetRegistry()
    a = _write(tmp_path / 'a.tif', b'A' * 5000)
    b = _write(tmp_path / 'b.tif', b'B' * 5000)                # same size, different content
    ua = reg.mint_or_recognise(a).uuid
    ub = reg.mint_or_recognise(b).uuid
    assert ua != ub, "same-size-different-bytes must NOT merge two datasets' identities"
    assert len(reg) == 2


def test_the_ome_uuid_is_authoritative_when_present(tmp_path):
    reg = DatasetRegistry()
    a = _write(tmp_path / 'a.tif', b'X' * 3000)
    b = _write(tmp_path / 'b.tif', b'Y' * 4000)                # different bytes AND size...
    ua = reg.mint_or_recognise(a, ome_uuid='OME-123').uuid
    # ...but the same OME UUID → the same molecule → the same identity
    ident = reg.mint_or_recognise(b, ome_uuid='OME-123')
    assert ident.uuid == ua, "a shared OME UUID must be recognised as the same dataset"


def test_the_partial_hash_reads_BOUNDED_bytes_not_the_whole_file(tmp_path):
    """The cardinal rule: a multi-GB acquisition must not be hashed whole. Measure the bytes read."""
    big = tmp_path / 'big.bin'
    with open(big, 'wb') as f:
        f.write(os.urandom(4_000_000))                          # 4 MB stand-in for a huge file

    read_total = {'n': 0}
    import builtins
    real_open = builtins.open

    class _CountingFile:
        def __init__(self, f):
            self._f = f
        def read(self, *a):
            data = self._f.read(*a)
            read_total['n'] += len(data)
            return data
        def seek(self, *a):
            return self._f.seek(*a)
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return self._f.__exit__(*a)

    def _counting_open(path, *a, **k):
        return _CountingFile(real_open(path, *a, **k))

    builtins.open = _counting_open
    try:
        bounded_partial_hash(str(big))
    finally:
        builtins.open = real_open

    assert read_total['n'] < 1_000_000, (
        f"partial_hash read {read_total['n']} of 4,000,000 bytes — it must sample, not hash the whole file")


def test_fingerprints_match_rules():
    base = DatasetFingerprint(size=100, mtime=1.0, ome_uuid=None, partial_hash='abc')
    same = DatasetFingerprint(size=100, mtime=2.0, ome_uuid=None, partial_hash='abc')   # mtime differs
    diff_hash = DatasetFingerprint(size=100, mtime=1.0, ome_uuid=None, partial_hash='xyz')
    assert fingerprints_match(base, same) is True               # size + hash agree; mtime irrelevant
    assert fingerprints_match(base, diff_hash) is False         # same size, different hash → not a match


def test_persistence_across_registry_instances(tmp_path):
    store = tmp_path / 'registry.json'
    p = _write(tmp_path / 'a.tif', b'DATA' * 800)
    u = DatasetRegistry(store_path=str(store)).mint_or_recognise(p).uuid
    # a fresh registry loading the same sidecar recognises the file → same UUID
    assert DatasetRegistry(store_path=str(store)).mint_or_recognise(p).uuid == u
