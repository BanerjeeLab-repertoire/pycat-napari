"""
fix_tifffile.py — PyCAT NumPy 2.0 compatibility patch
======================================================
Run this script once if you are using NumPy 2.0+ and encounter the error:
    AttributeError: `newbyteorder` was removed from the ndarray class in NumPy 2.0

PyCAT requires numpy<2.0 by default. If you intentionally installed NumPy 2.0
(e.g. for other packages), this script patches tifffile in-place so that both
aicsimageio and tifffile work correctly with NumPy 2.0.

Usage
-----
    python fix_tifffile.py

What it does
------------
Replaces the single line in tifffile that calls the removed
ndarray.newbyteorder() method with the NumPy 2.0 equivalent:
    result.view(result.dtype.newbyteorder('='))

A backup of the original file is saved as tifffile.py.bak.
"""

import sys
from pathlib import Path

try:
    import tifffile
    tifffile_path = Path(tifffile.__file__).parent / "tifffile.py"
except ImportError:
    print("ERROR: tifffile is not installed in this environment.")
    sys.exit(1)

print(f"tifffile location: {tifffile_path}")
print(f"tifffile version:  {tifffile.__version__}")

import numpy as np
print(f"NumPy version:     {np.__version__}")

if tuple(int(x) for x in np.__version__.split(".")[:2]) < (2, 0):
    print("\nNumPy < 2.0 detected — this patch is not needed.")
    print("If you are still seeing errors, check your environment for multiple NumPy installs.")
    sys.exit(0)

content = tifffile_path.read_text(encoding="utf-8")

OLD = "result = result.newbyteorder()"
NEW = "result = result.view(result.dtype.newbyteorder('='))  # NumPy 2.0 compat fix"

count = content.count(OLD)
if count == 0:
    if "NumPy 2.0 compat fix" in content:
        print("\ntifffile is already patched — nothing to do.")
    else:
        print("\nNo occurrences of result.newbyteorder() found.")
        print("Your tifffile version may already have this fixed upstream.")
    sys.exit(0)

print(f"\nFound {count} occurrence(s) to patch.")

# Write backup
backup = tifffile_path.with_suffix(".py.bak")
backup.write_text(content, encoding="utf-8")
print(f"Backup saved to: {backup}")

# Apply patch
fixed = content.replace(OLD, NEW)
tifffile_path.write_text(fixed, encoding="utf-8")
print(f"Patched {count} occurrence(s) successfully.")
print("\nDone — restart PyCAT and the error should be gone.")
print("If you want to revert, rename tifffile.py.bak back to tifffile.py.")
