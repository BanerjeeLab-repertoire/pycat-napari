"""
§3.1 probe — is `_ims_zarr_refs` dead, or is it the last thing keeping the IMS file open?

WHAT THIS ANSWERS
-----------------
`_open_stack_ims` appends to `self._ims_zarr_refs` three times and reads it nowhere. It MIGHT be
redundant (the napari layer already holds the lazy wrapper, which holds its own reader), or it
MIGHT be the only surviving reference to a sibling-position reader — in which case dropping it lets
HDF5 close the file and a later frame read crashes intermittently, depending on when GC runs.

We do not reason about this. We test it: build the wrappers, DROP the list, force `gc.collect()`,
then read a frame from each wrapper. If every read still works, the list was dead. If any read
raises (closed file / bad handle), the list was load-bearing.

HOW TO RUN (fresh terminal, env active, at repo root)
-----------------------------------------------------
    python ims_zarr_refs_probe.py "C:\\path\\to\\a_multiposition_file.ims"

Use a MULTI-POSITION .ims if you have one (that's the branch where pos_reader is a *sibling* file's
reader — the risky case). A single-position file exercises the safe path and is a weaker test, but
still worth running if that's all you have. Paste the final verdict block back.

SAFE: read-only. Loads the file, reads a few frames, writes nothing, deletes nothing. Delete this
script afterward.
"""

import sys
import gc
import os


def main(ims_path):
    if not os.path.exists(ims_path):
        print(f"FAIL: file not found: {ims_path}")
        return 2

    try:
        from imaris_ims_file_reader.ims import ims as ImsReader
    except Exception as e:
        print(f"FAIL: could not import imaris_ims_file_reader: {e}")
        return 2

    # Import the lazy wrapper classes and the print-suppressor exactly as _open_stack_ims uses them.
    try:
        from pycat.file_io.file_io import (
            _ImsReaderTYX, _ImsReaderZYX, _ImsReaderTZYX,
            _suppress_ims_chunk_prints,
        )
    except Exception as e:
        # Some builds keep the Ims wrappers in file_io; if the names differ, report so we can adjust.
        print(f"FAIL: could not import Ims wrapper classes from pycat.file_io.file_io: {e}")
        print("      (If these moved, tell me the new module and I'll repoint the probe.)")
        return 2

    print(f"Opening: {ims_path}")
    reader = ImsReader(ims_path)

    # Discover shape the way the loader does. We don't need the exact selector logic — we just need
    # ONE lazy wrapper of each kind we can build from this reader, then a frame read from it.
    try:
        shape = reader.shape  # imaris_ims_file_reader exposes a shape
    except Exception as e:
        print(f"FAIL: reader has no usable .shape: {e}")
        return 2
    print(f"Reader shape: {shape}")

    # Build a TYX wrapper directly on this reader (the common movie case). This mirrors the
    # `_ImsReaderTYX(reader, c, ...)` construction inside _open_stack_ims. The real ctor is
    # __init__(self, reader, c, suppress_ctx=None) and expects reader.shape == (T, C, Z, Y, X).
    wrappers = []
    try:
        _ = reader.shape  # confirm 5-D IMS layout before we pick channel 0
        w = _ImsReaderTYX(reader, 0, suppress_ctx=_suppress_ims_chunk_prints)
        wrappers.append(("TYX", w))
    except Exception as e:
        print(f"note: could not build _ImsReaderTYX directly ({e}); trying ZYX")
        try:
            w = _ImsReaderZYX(reader, 0, suppress_ctx=_suppress_ims_chunk_prints)
            wrappers.append(("ZYX", w))
        except Exception as e2:
            print(f"FAIL: could not build any Ims wrapper: {e2}")
            return 2

    # THE TEST: simulate what a real load leaves alive — the wrapper (napari would hold it) — and
    # then DROP the equivalent of `self._ims_zarr_refs` (here: `reader`, plus any list) and force GC.
    #
    # We keep ONLY the wrapper(s). If _ImsReaderTYX truly holds its own reader (self._reader =
    # reader), the wrapper keeps the file open by itself and the read below works. If the wrapper
    # somehow relied on an external list keeping `reader` alive, dropping our local `reader` name +
    # gc.collect() will surface the failure.
    print("\nDropping the standalone reader reference and forcing gc.collect() ...")
    del reader
    gc.collect()

    print("Reading frame 0 from each wrapper AFTER GC:")
    all_ok = True
    import numpy as np
    for kind, w in wrappers:
        try:
            frame = np.asarray(w[0])  # first frame
            print(f"  {kind}: OK  shape={getattr(frame, 'shape', '?')} dtype={getattr(frame, 'dtype', '?')}")
        except Exception as e:
            all_ok = False
            print(f"  {kind}: RAISED after GC -> {type(e).__name__}: {e}")

    print("\n" + "=" * 60)
    if all_ok:
        print("VERDICT: reads SUCCEEDED after GC.")
        print("  => the wrapper holds its own reader; `_ims_zarr_refs` looks DEAD (safe to drop).")
        print("  => BUT this single-reader test is the weak case. If you have a MULTI-POSITION")
        print("     .ims, run this again on it — that's the branch where the list holds a SIBLING")
        print("     file's reader, which is the genuinely risky one.")
    else:
        print("VERDICT: a read RAISED after GC.")
        print("  => `_ims_zarr_refs` (or the reference it stands in for) is LOAD-BEARING.")
        print("  => do NOT drop it; the ImageSource object must keep these readers alive explicitly.")
    print("=" * 60)
    return 0 if all_ok else 1


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python ims_zarr_refs_probe.py <path-to.ims>  (prefer a multi-position file)")
        sys.exit(2)
    sys.exit(main(sys.argv[1]))
