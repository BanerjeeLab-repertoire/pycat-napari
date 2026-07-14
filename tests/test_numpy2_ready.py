"""
**``numpy<2.0`` was never PyCAT's choice — it was aicsimageio's.**

``aicsimageio`` is **frozen in maintenance mode** and pins ``zarr<2.16``, ``tifffile<2023.3.15``,
``fsspec<2023.9``, ``lxml<5``. Those pins are what held the whole stack at numpy 1 and zarr 2.

***Removing it FREES the pins rather than fighting them*** — which is why the 1.6.0 migration is a
**removal**, not an addition.

But freeing a pin is only safe if the code underneath it is actually ready. **This checks that it
is, rather than assuming it.**

What numpy 2 removed
--------------------
A long list of aliases and functions that had been deprecated for years — ``np.float_``,
``np.NaN``, ``np.alltrue``, ``np.product``, ``np.in1d``, ``np.trapz``, and more. **Any one of them
in PyCAT's code would break the moment numpy 2 is installed**, and it would break at *runtime*, in
whichever analysis happened to touch it — *not at import, and not in CI unless that path is
exercised.*

Measured across all 122 modules: **zero occurrences.** The pin can go.
"""

import pathlib
import re

import pytest


_SOURCE = pathlib.Path(__file__).resolve().parents[1] / "src" / "pycat"


# Everything numpy 2 removed, and what replaced it. The value is what the fix is — so a failure
# tells you what to write, not merely that something is wrong.
_REMOVED_IN_NUMPY_2 = {
    r'\bnp\.float_\b': 'np.float64',
    r'\bnp\.bool8\b': 'np.bool_',
    r'\bnp\.unicode_\b': 'np.str_',
    r'\bnp\.string_\b': 'np.bytes_',
    r'\bnp\.NaN\b': 'np.nan',
    r'\bnp\.Inf\b': 'np.inf',
    r'\bnp\.infty\b': 'np.inf',
    r'\bnp\.alltrue\b': 'np.all',
    r'\bnp\.sometrue\b': 'np.any',
    r'\bnp\.cumproduct\b': 'np.cumprod',
    r'\bnp\.product\b': 'np.prod',
    r'\bnp\.round_\b': 'np.round',
    r'\bnp\.in1d\b': 'np.isin',
    r'\bnp\.row_stack\b': 'np.vstack',
    r'\bnp\.trapz\b': 'np.trapezoid',
    r'\bnp\.issubsctype\b': '(removed — use np.issubdtype)',
    r'\bnp\.find_common_type\b': '(removed — use np.result_type)',
    r'\bnp\.set_string_function\b': '(removed)',
    r'\bnp\.mat\b': 'np.asmatrix',
}


@pytest.mark.core
def test_no_module_uses_anything_numpy_2_REMOVED():
    """**These break at RUNTIME, in whichever analysis touches them** — not at import.

    So a green import proves nothing, and this has to be a source scan.
    """
    offenders = []

    for path in sorted(_SOURCE.rglob("*.py")):
        source = path.read_text(encoding='utf-8', errors='ignore')

        for pattern, replacement in _REMOVED_IN_NUMPY_2.items():
            for match in re.finditer(pattern, source):
                line = source[:match.start()].count('\n') + 1
                offenders.append(
                    f"{path.relative_to(_SOURCE)}:{line}  {match.group(0)}  ->  {replacement}")

    assert not offenders, (
        "these use numpy APIs that **numpy 2 removed**:\n  " + "\n  ".join(offenders)
        + "\n\nPyCAT no longer pins `numpy<2.0` — that pin belonged to aicsimageio, which is gone "
          "as of 1.6.0. **These would break at runtime, in whichever analysis touched them.**"
    )


@pytest.mark.core
def test_numpy_is_no_longer_PINNED_BELOW_2():
    """**The pin was aicsimageio's, and aicsimageio is gone.**

    *(The remaining ceiling comes from **cellpose**, and that is a deliberate scientific choice, not
    a technical one: PyCAT pins ``cellpose<4`` because Cellpose 4 **removed the cyto2 CNN** and
    replaces it with a ViT-L transformer that is very slow on CPU — which matters for the lab
    machines without a GPU.)*
    """
    pyproject = (pathlib.Path(__file__).resolve().parents[1] / "pyproject.toml").read_text(
        encoding='utf-8', errors='ignore')

    start = pyproject.find('dependencies = [')
    end = pyproject.find(']', start)
    block = pyproject[start:end]

    assert '"numpy>=1.22,<2.0"' not in block, (
        "numpy is still pinned below 2.0. That pin existed to satisfy aicsimageio, which is no "
        "longer a dependency."
    )
    assert '"zarr>=2.12,<3.0"' not in block, (
        "zarr is still pinned below 3.0. That pin existed to satisfy aicsimageio (which pinned "
        "<2.16). `pycat.file_io.zarr_compat` handles both zarr 2 and zarr 3."
    )
