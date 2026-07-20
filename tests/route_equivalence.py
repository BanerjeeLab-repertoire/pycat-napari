"""**The same workflow, the same numbers — through every route PyCAT offers.**

PyCAT deliberately exposes each operation through several routes: an interactive/headless call, the
batch recorder→replayer, session save→reload. **Each route assembles its parameters independently.**
Isolated tests protect each route's *internals*; nothing proves the routes *agree with each other* —
and a disagreement is the highest-severity class of bug PyCAT can have: the same analysis silently
yielding **different numbers depending on how it was launched**. That is exactly what a reviewer asks
about reproducibility.

Precedent that this is real, not hypothetical: batch preprocessing once passed a *normalised* image
where the interactive path passed **raw counts**, and the rolling-ball radius is not scale-invariant —
the two routes computed different backgrounds. It was found by a test written for that one step. This
harness generalises that test to a matrix.

── How to read a workflow row ────────────────────────────────────────────────────────────────
A `Workflow` names, per route, a thunk that runs the workflow *that way* and returns a comparable
result (an array or a DataFrame). `run_all_routes` executes them; `assert_routes_agree` compares each
route against a reference route and **names which route diverged and by how much**. A route that
cannot be driven headlessly is not silently skipped — it is declared an `Unavailable` **documented
gap**, and the harness asserts the set of gaps is exactly what was declared, so a route quietly
disappearing (or a gap quietly closing) fails the test.

**A divergence is a finding, not a tolerance problem.** Loosening a tolerance to make a route pass
defeats the whole purpose. Every non-zero tolerance here carries a written justification; the default
is exact equality.
"""

import numpy as np
import pandas as pd

# The routes, in the order results are reported. Not every workflow drives every route.
ROUTE_ORDER = ('headless', 'batch', 'session')


class Unavailable:
    """A route that cannot be driven for this workflow **here**, carrying the reason WHY.

    This is the difference between a documented gap and a silent omission. The reason is a statement
    about where the headless API stops — e.g. "batch replay of cellpose needs torch" — and reading
    the list of gaps tells you where the routes are not yet unifiable.
    """

    def __init__(self, reason):
        self.reason = reason

    def __repr__(self):
        return f"Unavailable({self.reason!r})"


class Workflow:
    """One canonical analysis, plus how to drive it through each route and how to compare results.

    - ``routes``: ``{route_name: thunk}`` where ``thunk()`` runs the workflow that way and returns a
      comparable result. A route absent from this dict is a gap and MUST appear in ``documented_gaps``.
    - ``compare``: ``(reference_result, other_result) -> (agree: bool, detail: str)`` — ``detail``
      names the magnitude of any disagreement, so a failure says *by how much*.
    - ``documented_gaps``: ``{route_name: reason}`` — the routes that cannot run here, declared.
    """

    def __init__(self, name, *, routes, compare, documented_gaps=None, compare_metadata=None):
        self.name = name
        self.routes = dict(routes)
        self.compare = compare
        self.documented_gaps = dict(documented_gaps or {})
        # Optional SECOND comparator for scientifically-important metadata (schema, units, NaN policy,
        # tags). The audit's sharpest point: "two routes can produce numerically similar arrays while
        # differing in scientifically important metadata." Existing workflows pass None and are unaffected.
        self.compare_metadata = compare_metadata


def run_all_routes(workflow):
    """Execute ``workflow`` through each route. A route with no thunk becomes an ``Unavailable``."""
    results = {}
    for route in ROUTE_ORDER:
        thunk = workflow.routes.get(route)
        if thunk is None:
            results[route] = Unavailable(workflow.documented_gaps.get(route, 'no route defined'))
        else:
            results[route] = thunk()
    return results


def assert_routes_agree(workflow, results, *, reference='headless'):
    """Every route that ran must agree with ``reference``; gaps must be exactly the declared ones."""
    # 1) No SILENT omission. The routes that did not run are exactly the documented gaps — so a route
    #    that vanishes (an import breaks) or a gap that quietly closes both fail here, loudly.
    did_not_run = {r for r, v in results.items() if isinstance(v, Unavailable)}
    declared = set(workflow.documented_gaps)
    assert did_not_run == declared, (
        f"{workflow.name}: routes that did not run {sorted(did_not_run)} != declared gaps "
        f"{sorted(declared)}. A route disappeared without a reason, or a gap closed without the "
        f"record being updated — either way the matrix no longer describes reality.")

    # 2) The reference route MUST run; there is nothing to compare against otherwise.
    ref = results[reference]
    assert not isinstance(ref, Unavailable), (
        f"{workflow.name}: the reference route {reference!r} did not run ({ref.reason}).")

    # 3) Every other route that ran must AGREE with the reference — naming who diverged, by how much.
    #    Both the numeric comparator AND (when declared) the metadata comparator must pass: a route that
    #    emits the right numbers with the wrong schema/units/NaN policy is still a divergence.
    diverged = []
    for route, value in results.items():
        if route == reference or isinstance(value, Unavailable):
            continue
        agree, detail = workflow.compare(ref, value)
        if not agree:
            diverged.append(f"route {route!r} diverged from {reference!r}: {detail}")
        if workflow.compare_metadata is not None:
            meta_agree, meta_detail = workflow.compare_metadata(ref, value)
            if not meta_agree:
                diverged.append(f"route {route!r} metadata diverged from {reference!r}: {meta_detail}")
    assert not diverged, f"{workflow.name}: " + "; ".join(diverged)


# ── Comparators — each carries its tolerance and the justification for it ──────────────────────

def compare_arrays(*, rtol=0.0, atol=0.0):
    """Compare two arrays. **Default is exact** (``rtol=atol=0``): two routes running the same
    deterministic float operation on the same input have no numerical reason to differ, so any
    difference is a real divergence, not float noise."""
    def _cmp(ref, other):
        ref = np.asarray(ref)
        other = np.asarray(other)
        if ref.shape != other.shape:
            return False, f"shape {ref.shape} vs {other.shape}"
        if np.array_equal(ref, other):
            return True, "exact"
        delta = float(np.max(np.abs(ref.astype(np.float64) - other.astype(np.float64))))
        return np.allclose(ref, other, rtol=rtol, atol=atol, equal_nan=True), f"max|delta|={delta:g}"
    return _cmp


def compare_dataframes(columns, *, rtol=0.0, atol=0.0):
    """Compare the named ``columns`` of two DataFrames. **Default is exact.**

    Only the named columns are compared, and only they need exist in both frames — so an added
    bookkeeping column (a restored session gains an ``Unnamed: 0`` index column from the CSV
    round-trip) is ignored, while any change to the scientific numbers fails and is named.
    """
    def _cmp(ref, other):
        missing = [c for c in columns if c not in other.columns]
        if missing:
            return False, f"result is missing columns {missing}"
        if len(ref) != len(other):
            return False, f"row count {len(ref)} vs {len(other)}"
        worst_col, worst_delta = None, 0.0
        for col in columns:
            a = np.asarray(ref[col].values, dtype=np.float64)
            b = np.asarray(other[col].values, dtype=np.float64)
            if not np.allclose(a, b, rtol=rtol, atol=atol, equal_nan=True):
                delta = float(np.nanmax(np.abs(a - b)))
                if delta >= worst_delta:
                    worst_col, worst_delta = col, delta
        if worst_col is not None:
            return False, f"column {worst_col!r} max|delta|={worst_delta:g}"
        return True, f"{len(columns)} columns exact over {len(ref)} rows"
    return _cmp


def compare_frame_metadata(columns, *, units_column=None):
    """Compare the scientifically-important METADATA of two result frames — beyond the numbers.

    Checks, for the named ``columns``: they are present in both in the SAME relative order (a reordered
    schema is a divergence a reader would not notice from spot-checking values); the dtype KIND matches
    (an int column vs a float column is a route casting differently); and the **NaN pattern** is identical
    (a route emitting 0.0 where another emits NaN is a real divergence the audit called out explicitly).
    When ``units_column`` is given, the set of unit strings must match too — a route that drops or renames
    a units column changes what the numbers MEAN."""
    def _cmp(ref, other):
        ref_order = [c for c in ref.columns if c in columns]
        other_order = [c for c in other.columns if c in columns]
        if ref_order != other_order:
            return False, f"column order/presence differs: {ref_order} vs {other_order}"
        for col in columns:
            if col not in ref.columns or col not in other.columns:
                return False, f"column {col!r} missing from one route"
            ref_numeric = ref[col].dtype.kind in 'fiu'
            other_numeric = other[col].dtype.kind in 'fiu'
            # An int-vs-float cast is a real route divergence; an object-vs-CSV-string difference on a text
            # column is serialization noise, so the dtype-kind check applies only when either side is numeric.
            if (ref_numeric or other_numeric) and ref[col].dtype.kind != other[col].dtype.kind:
                return False, (f"column {col!r} dtype kind {ref[col].dtype.kind} vs "
                               f"{other[col].dtype.kind} — a route cast differently")
            # NaN policy is a numeric-column concern; a string column's "policy" is its unit set (below).
            if ref_numeric and other_numeric:
                ref_nan = np.isnan(np.asarray(ref[col].values, dtype=np.float64))
                other_nan = np.isnan(np.asarray(other[col].values, dtype=np.float64))
                if not np.array_equal(ref_nan, other_nan):
                    return False, (f"column {col!r} NaN pattern differs ({int(ref_nan.sum())} vs "
                                   f"{int(other_nan.sum())} NaNs) — a route emits a number where another emits NaN")
        if units_column is not None:
            ru = set(ref[units_column]) if units_column in ref.columns else None
            ou = set(other[units_column]) if units_column in other.columns else None
            if ru != ou:
                return False, f"units column {units_column!r} differs: {ru} vs {ou}"
        return True, f"schema/dtype/NaN{'/units' if units_column else ''} agree over {len(columns)} columns"
    return _cmp


# ── Session-route mechanics — the real writer, then reload ─────────────────────────────────────
# Imports are inside the functions on purpose: `pycat.file_io` is only importable where its (Qt-free)
# writer deps are present, and keeping it out of module scope lets this helper module import in any
# headless collection pass.

class Image:
    """A stand-in napari Image layer. ``type(layer).__name__`` IS the layer type the writer branches
    on, so the class is literally named ``Image`` — see ``writers._save_layer``."""

    def __init__(self, name, data):
        self.name = name
        self.data = data
        self.metadata = {}


class _ActiveDataClass:
    def __init__(self):
        self.data_repository = {}


class _CentralManager:
    def __init__(self):
        self.active_data_class = _ActiveDataClass()


def session_roundtrip_dataframe(df, key):
    """Write ``df`` into a session with the real writer, reload it, return the restored DataFrame.

    This is the persistence route: it proves a workflow's table survives ``Save & Clear`` → reopen
    with the numbers unchanged. Uses a temp dir; nothing is left behind.
    """
    import tempfile
    import pathlib
    from pycat.file_io.writers import write_session_outputs
    from pycat.file_io import session_manifest as sm

    with tempfile.TemporaryDirectory() as tmp:
        session_dir = pathlib.Path(tmp) / 'session'
        session_dir.mkdir()
        write_session_outputs(
            _CentralManager(), {}, selected_layers=[], selected_dataframes=[key],
            dataframes={key: df}, file_metadata=None, save_name=str(session_dir / 'expt'),
            session_dir=session_dir, source_path=None, stem='expt')
        manifest = sm.read_manifest(session_dir)
        repo = {}
        sm.restore_dataframes_from_manifest(manifest, session_dir, repo)
        return repo.get(key)


def session_roundtrip_image(arr, layer_name):
    """Write ``arr`` as an image layer with the real writer, read the layer file back, return it.

    Float image layers are written as float32 TIFF (the writer keeps continuous data as float rather
    than quantising) and read back losslessly — so this proves the workflow's image output persists
    without a silent cast.
    """
    import tempfile
    import pathlib
    import tifffile
    from pycat.file_io.writers import write_session_outputs

    layer = Image(layer_name, np.asarray(arr))
    with tempfile.TemporaryDirectory() as tmp:
        session_dir = pathlib.Path(tmp) / 'session'
        session_dir.mkdir()
        write_session_outputs(
            _CentralManager(), {layer.name: layer}, selected_layers=[layer_name],
            selected_dataframes=[], dataframes={}, file_metadata=None,
            save_name=str(session_dir / 'expt'), session_dir=session_dir,
            source_path=None, stem='expt')
        written = list(session_dir.glob('*.tiff')) + list(session_dir.glob('*.tif'))
        if not written:
            raise AssertionError(f"the writer produced no image file (files: "
                                 f"{[p.name for p in session_dir.glob('*')]})")
        return np.asarray(tifffile.imread(str(written[0])))


# ── Batch-route mechanics — the real replay registry, then one step ────────────────────────────

def batch_replay(config_steps, state):
    """Run recorded ``config_steps`` through the REAL replay registry, mutating ``state`` in place.

    Populates the registry the production way (`register_all_steps`) and then runs the same loop
    `BatchProcessor._process_file` runs — each step's replay function called with the shared state.
    Returns ``state``. The Qt-bound ``BatchProcessor`` class itself is not imported (it pulls in
    PyQt5); only the import-clean ``batch_step_registry`` is.
    """
    import tempfile
    import pathlib
    from pycat.batch_step_registry import register_all_steps

    class _Recorder:
        def __init__(self):
            self.step_registry = {}

        def register_step(self, name, fn):
            self.step_registry[name] = fn

    recorder = _Recorder()
    register_all_steps(recorder)

    with tempfile.TemporaryDirectory() as tmp:
        out_dir = pathlib.Path(tmp)
        image_path = out_dir / 'sample.tif'
        for step in config_steps:
            fn = recorder.step_registry.get(step['step'])
            if fn is None:
                raise AssertionError(f"batch replay: step {step['step']!r} is not registered")
            fn(state, image_path, step.get('params', {}), out_dir)
    return state
