"""
PyCAT — does BioIO read your files identically to aicsimageio?

    conda activate pycat-dev312
    pip install bioio bioio-ome-tiff bioio-tifffile bioio-czi
    python compare_image_readers.py <folder-or-file> [more files...]

Why this exists
---------------
``aicsimageio`` is in **maintenance mode**. Its maintainers name ``bioio`` as the *"compatible
successor"*, and the compatibility is real: ``BioImage`` exposes the same names, the same semantics
and the same **TCZYX** ordering as ``AICSImage``.

So the substitution is small. **What is not small is the risk of doing it in one irreversible
step.**

This project has been bitten twice by exactly that:

* the **rolling-ball normalisation** that made batch disagree with the recording
* the **frame-zero collapse** that told users their movie was a still image

***Both were invisible until someone compared two runs.*** Neither would have been caught by a test
on a synthetic file — they needed **real data**, and so does this.

What it checks, and why each one matters
-----------------------------------------
=====================  ==================================================================
**dimension order**    A reader returning ``CTZYX`` instead of ``TCZYX`` **would not
                       crash.** It would return the **wrong channel**, and every number
                       downstream would be confidently wrong.
**physical pixel size**  Every **length, area, and diffusion coefficient** PyCAT reports
                       depends on this. PyCAT already treats a pixel size of exactly 1 as
                       a **sentinel**, because a wrong one is worse than a missing one.
**scenes**             A multi-position CZI whose scenes are enumerated differently would
                       silently analyse a **different field of view.**
**THE PIXELS**         Everything above can match while the data differs — a byte-order
                       bug, an off-by-one at a chunk boundary, a scene resolved
                       differently. ***The only claim worth making is that the pixels are
                       identical.***
=====================  ==================================================================

What to run it on
-----------------
**The files that have actually exposed loader bugs in this project**, not a convenient sample:

* a **Zeiss CZI** (the one format that genuinely requires the library)
* a **Micro-Manager OME-TIFF** — the frame interval lives in a non-standard tag, and PyCAT has
  already been burned by defaulting it to 0.1 s when the real value was 0.5
* the **astigmatic bead movie** (``3_30_hr_1_MMStack_Pos0``) — this file has exposed two loader
  bugs already
* an **Imaris .ims**, to confirm the IMS path is genuinely untouched *(it should be: it has its own
  HDF5 reader and never went through aicsimageio at all)*
* anything with **multiple scenes / positions**

Reading the result
------------------
**IDENTICAL on every file** → flip the default. Nothing else is needed; the seam is already in.

**Any difference** → *that is the finding*, and it is worth more than a clean run. Paste it back.

Safe to delete afterwards.
"""

import sys
import pathlib


_EXTENSIONS = ('.czi', '.tif', '.tiff', '.ome.tif', '.ome.tiff', '.nd2', '.lif', '.ims')


def _collect(arguments):
    """Files to test. A folder is walked; a file is taken as given."""
    files = []

    for argument in arguments:
        path = pathlib.Path(argument)

        if path.is_dir():
            for candidate in sorted(path.rglob('*')):
                if candidate.suffix.lower() in _EXTENSIONS:
                    files.append(candidate)
        elif path.is_file():
            files.append(path)
        else:
            print(f"  (skipping {argument} — not found)")

    return files


def main(arguments):
    print(__doc__)
    print("=" * 78)

    try:
        import aicsimageio
        import bioio
    except ImportError as exc:
        print(f"""
STOPPING — both libraries must be installed to compare them.

    pip install bioio bioio-ome-tiff bioio-tifffile bioio-czi

({exc})
""")
        return 1

    print(f"  aicsimageio {getattr(aicsimageio, '__version__', '?')}")
    print(f"  bioio       {getattr(bioio, '__version__', '?')}\n")

    # PyCAT's own comparison, so the two paths cannot drift apart.
    try:
        from pycat.file_io.image_reader import compare_readers
    except ImportError:
        print("STOPPING — pycat is not importable. Run this inside the pycat environment.")
        return 1

    files = _collect(arguments)
    if not files:
        print("No image files found. Point this at a folder or name the files directly.")
        return 1

    print(f"  {len(files)} file(s) to compare\n")
    print("=" * 78)

    identical = []
    differing = []
    failed = []

    for path in files:
        print(f"\n--- {path.name} " + "-" * max(0, 60 - len(path.name)))

        try:
            report = compare_readers(path, verbose=True)
        except Exception as exc:
            print(f"  *** FAILED: {type(exc).__name__}: {exc}")
            failed.append((path, f"{type(exc).__name__}: {exc}"))
            continue

        if report.get('error'):
            failed.append((path, report['error']))
        elif report.get('differences'):
            differing.append((path, report['differences']))
        else:
            identical.append(path)

    print("\n" + "=" * 78)
    print("THE VERDICT")
    print("=" * 78 + "\n")

    print(f"  identical : {len(identical)}")
    print(f"  DIFFERENT : {len(differing)}")
    print(f"  failed    : {len(failed)}\n")

    if differing:
        print("  === FILES WHERE THE TWO LIBRARIES DISAGREE ===\n")
        for path, differences in differing:
            print(f"    {path.name}")
            for line in differences:
                print(f"        {line}")
        print()

    if failed:
        print("  === FILES NEITHER COULD BE COMPARED ON ===\n")
        for path, reason in failed:
            print(f"    {path.name}: {reason}")
        print("""
  A failure on a .czi almost certainly means `bioio-czi` is not installed — that is a
  MISSING PLUGIN, not a broken file. BioIO's readers are separate packages by design.
""")

    print("=" * 78)

    if differing:
        print("""
  **The differences above are the finding**, and they are worth more than a clean run.

  A DIMENSION ORDER difference is the dangerous one: it would not crash, it would return
  the wrong channel. A PIXEL SIZE difference changes every length, area and diffusion
  coefficient PyCAT reports.

  Do not flip the default. Paste this output back.""")
    elif identical and not failed:
        print("""
  **BioIO reads every one of these files identically — including the pixels.**

  The seam is already in the code, so flipping the default is a one-line change:
  `_DEFAULT_BACKEND = 'bioio'` in `src/pycat/file_io/image_reader.py`.

  Try it first without committing:

      PYCAT_IMAGE_READER=bioio run-pycat""")
    else:
        print("  Nothing conclusive — read the failures above first.")

    print("=" * 78)
    return 0 if (identical and not differing and not failed) else 1


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print(__doc__)
        raise SystemExit(
            "usage: python compare_image_readers.py <folder-or-file> [more...]")
    raise SystemExit(main(sys.argv[1:]))
