"""
Stack access — the pure-numpy core of PyCAT's lazy/streaming data layer.

Why this module exists
----------------------
``materialize_stack``, ``iter_frames``, ``layer_is_stack`` and ``extract_2d_plane`` are
the functions every analysis module needs in order to read a possibly-lazy stack safely.
They are **pure numpy**: none of them touches AICSImage, Qt or napari.

They used to live in ``file_io.py``, which imports ``AICSImage``, ``PyQt5``, ``napari``
and ``pycat.ui.ui_utils`` at module scope. **Fifteen toolbox modules import from
``file_io`` purely to reach these helpers**, and therefore drag the entire GUI and
file-format stack into memory just to iterate frames over an array they already hold.
That is why the scientific tests could not be collected in a minimal environment, and it
is a measurable startup cost for every run.

So the helpers live here, importable with nothing but numpy. ``file_io`` re-exports them,
so existing imports keep working.

The access-pattern contract
---------------------------
``get_array_source`` makes the *access pattern* explicit, because that is the decision
that actually matters and it is currently made by accident:

* ``framewise``  — read one frame at a time, never hold the movie. The default, and the
  right choice for almost every per-frame analysis.
* ``full``       — genuinely need the whole array in memory at once (an FFT over t, a
  global sort). Must be requested deliberately: ``allow_materialize=True``.
* ``roi``        — read a spatial sub-region per frame.

The failure mode this prevents is real and has bitten repeatedly: ``np.asarray(layer.data)``
on one of PyCAT's lazy wrappers returns **frame 0 only** (``__array__`` is deliberately
truncated so napari's thumbnail requests don't materialise a multi-gigabyte movie). Nothing
errors; the analysis simply runs on one frame and reports it as the whole movie.
"""

from __future__ import annotations

import numpy as np


def layer_is_stack(layer_data):
    """True if this layer data is a multi-frame (T/Z, H, W) stack — whether a
    plain 3D numpy array or one of PyCAT's lazy wrappers. Used by 2D analyses to
    decide whether an input is genuinely 2D or a stack that needs a frame chosen
    (or sequential processing). Safe on anything (returns False on failure)."""
    try:
        shp = getattr(layer_data, 'shape', None)
        return shp is not None and len(shp) == 3 and int(shp[0]) > 1
    except Exception:
        return False

def extract_2d_plane(layer_data, frame_index=0, dtype=np.float32):
    """Safely extract ONE 2D plane from possibly-lazy layer data.

    This is the correct way for a 2D analysis to read a single frame from a layer
    that MIGHT be a lazy stack — using ``np.asarray(layer.data)`` there would
    trigger the deliberately-truncated ``__array__`` and silently return frame 0
    regardless of which frame the user is viewing. This indexes the requested
    frame explicitly (the fast per-frame path on lazy wrappers) and returns a real
    2D array. A genuinely 2D input is returned as-is.

    frame_index : which frame to take from a stack (e.g. the current viewer step).
    """
    try:
        shp = getattr(layer_data, 'shape', None)
        if shp is not None and len(shp) == 3:
            n = int(shp[0])
            t = int(frame_index)
            t = 0 if t < 0 else (n - 1 if t >= n else t)
            frame = layer_data[t]
            if hasattr(frame, 'compute'):
                frame = frame.compute()
            out = np.asarray(frame)
            return out if dtype is None else out.astype(dtype)
        # 2D (or unknown) — materialize a single plane defensively.
        out = np.asarray(layer_data)
        if out.ndim == 3:                       # a wrapper that slipped through
            out = out[min(int(frame_index), out.shape[0] - 1)]
        return out if dtype is None else out.astype(dtype)
    except Exception:
        out = np.asarray(layer_data)
        return out if dtype is None else out.astype(dtype)

def materialize_stack(stack_like, dtype=np.float32, progress_callback=None):
    """Return a real (T, H, W) numpy array from any stack-like layer data.

    Handles PyCAT's lazy wrappers (_TiffPageStack, _ZarrTYX_generic, IMS
    readers) whose __array__ is deliberately truncated to one frame for napari's
    sake, plus plain numpy/dask arrays. Analysis code that needs every frame
    should call THIS, not np.asarray(layer.data) — the latter can silently
    return a single 2D frame from a lazy wrapper and make a (T,H,W) stack look
    2D (breaking shape checks and per-frame analysis).

    progress_callback : optional callable(done, total) invoked as frames are
        read, so a caller can show a determinate "Materializing…" bar. Only the
        frame-by-frame rebuild path reports progress (the only genuinely slow
        case); eager arrays return immediately.
    """
    # Lazy wrappers expose as_full_array() or are safely indexable by frame.
    if hasattr(stack_like, 'as_full_array'):
        return stack_like.as_full_array(dtype=dtype, progress_callback=progress_callback)
    # dask
    if hasattr(stack_like, 'compute'):
        out = np.asarray(stack_like.compute())
        return out if dtype is None else out.astype(dtype)
    arr = np.asarray(stack_like)
    # If __array__ truncated a 3D wrapper to 2D but it advertises a 3D shape,
    # rebuild by indexing frames.
    shp = getattr(stack_like, 'shape', None)
    if arr.ndim == 2 and shp is not None and len(shp) == 3:
        # Preserve source dtype when dtype is None (e.g. integer label masks
        # must NOT be floated). Infer from the first frame otherwise.
        _f0 = np.asarray(stack_like[0])
        _dt = _f0.dtype if dtype is None else dtype
        out = np.empty(shp, dtype=_dt)
        out[0] = _f0.astype(_dt)
        n = shp[0]
        if progress_callback is not None:
            try: progress_callback(1, n)
            except Exception: pass
        for t in range(1, n):
            out[t] = np.asarray(stack_like[t]).astype(_dt)
            if progress_callback is not None:
                try: progress_callback(t + 1, n)
                except Exception: pass
        return out
    return arr if dtype is None else arr.astype(dtype)

def iter_frames(stack_like, dtype=np.float32, indices=None):
    """Yield frames of a (T, H, W) stack ONE AT A TIME, without ever holding
    the whole stack in memory.

    This is the streaming counterpart to materialize_stack(): use it for
    per-frame analysis (e.g. bead detection) that only needs one frame at a
    time, so a long movie never has to be fully materialised. Handles PyCAT's
    lazy wrappers (_TiffPageStack, _ZarrTYX_generic, IMS readers) by indexing
    them frame-by-frame, dask arrays by computing one frame at a time, and plain
    numpy arrays by iterating rows of the T axis. A 2D input yields a single
    frame.

    Parameters
    ----------
    stack_like : array-like or lazy wrapper with .shape and __getitem__.
    dtype : frames are returned as this dtype.
    indices : optional iterable of frame indices to yield (e.g. a keyframe
        subset). If None, all frames are yielded in order.

    Yields
    ------
    (t, frame) : the frame index and the 2D frame as a numpy array.
    """
    shp = getattr(stack_like, 'shape', None)
    # 2D single frame
    if shp is not None and len(shp) == 2:
        yield 0, np.asarray(stack_like).astype(dtype)
        return

    if shp is not None and len(shp) == 3:
        n = shp[0]
        idxs = range(n) if indices is None else list(indices)
        is_dask = hasattr(stack_like, 'compute') and not hasattr(stack_like, 'as_full_array')
        for t in idxs:
            if t < 0 or t >= n:
                continue
            frame = stack_like[t]
            if is_dask and hasattr(frame, 'compute'):
                frame = frame.compute()
            yield int(t), np.asarray(frame).astype(dtype)
        return

    # Fallback: unknown shape — materialise once and iterate (last resort).
    arr = materialize_stack(stack_like, dtype=dtype)
    if arr.ndim == 2:
        arr = arr[np.newaxis, ...]
    idxs = range(arr.shape[0]) if indices is None else list(indices)
    for t in idxs:
        if 0 <= t < arr.shape[0]:
            yield int(t), arr[t]

def warn_if_assumed_axis(data_repository, operation="this analysis"):
    """**A wrong axis label makes every RATE meaningless, and nothing about the number looks wrong.**

    An undeclared multipage TIFF has no axis metadata, so the user labels it **T or Z at load**.
    **T and Z load identically** — a wrong label is completely harmless for viewing, and there is
    nothing on screen to tell you it happened.

    But a step that treats frames as **TIME** (an MSD, a diffusion coefficient, a coarsening rate,
    a recovery half-time) is computing a rate **per frame**, and if those frames are actually
    **Z-slices**, the rate is a fiction. Ten UIs run a time-dependent analysis; **four warned.**
    Five of the other six compute an **MSD**.

    Fires at most once per stack per session. **Safe no-op if the axis was declared in metadata** —
    it only speaks when the label really was a guess.

    (Original docstring: flash a one-time warning if the active stack's axis type was ASSUMED (an
    undeclared multipage TIFF the user labelled T or Z at load; see 1.5.351) and
    an axis-dependent operation is about to use it. T and Z load identically, so a
    wrong label is harmless for loading/viewing, but a step that treats frames as
    TIME (rates, MSD, recovery) or as Z (projections, 3-D metrics) should remind
    the user the axis was assumed. Fires at most once per stack per session.
    Safe no-op if the axis was declared in metadata or the repo is unavailable.

    This is a module-level function (not a FileIOClass method) so any analysis UI
    can call it directly with the data_repository it already holds, without
    reaching back into the file-IO instance.)"""
    try:
        dr = data_repository
        if not dr or not dr.get('stack_axis_assumed') or dr.get('_axis_warned'):
            return
        label = dr.get('stack_axis_label', '?')
        try:
            from napari.utils.notifications import show_warning
            show_warning(
                f"Note: this stack's axis was assumed to be '{label}' (the file "
                f"had no axis metadata). {operation} depends on the axis type — "
                f"if it's actually the other kind, reopen and relabel.")
        except Exception:
            pass
        dr['_axis_warned'] = True
    except Exception:
        pass

# ─────────────────────────── explicit access patterns ───────────────────────

def stack_shape(stack_like):
    """(T, H, W) of a stack-like object without materialising it."""
    shp = getattr(stack_like, 'shape', None)
    if shp is not None:
        return tuple(int(x) for x in shp)
    return tuple(int(x) for x in np.asarray(stack_like).shape)


def read_frame(stack_like, t, dtype=None):
    """Read a SINGLE frame, without materialising the stack.

    Safe on lazy wrappers, zarr, dask and plain arrays alike. This is the primitive a
    framewise algorithm should use.
    """
    frame = stack_like[int(t)]
    if hasattr(frame, 'compute'):          # dask
        frame = frame.compute()
    arr = np.asarray(frame)
    return arr if dtype is None else arr.astype(dtype, copy=False)


def get_array_source(layer_or_data, access_pattern='framewise',
                     allow_materialize=False, dtype=None):
    """Acquire data for analysis with the access pattern stated up front.

    Parameters
    ----------
    layer_or_data : a napari layer, or raw stack-like data.
    access_pattern : 'framewise' | 'full' | 'roi'
        ``framewise`` returns a *source* to be read one frame at a time via
        ``read_frame`` / ``iter_frames``. ``full`` returns a materialised array, and
        requires ``allow_materialize=True`` — so that pulling a multi-gigabyte movie into
        RAM is always a deliberate act, visible at the call site, rather than the silent
        default it is today.
    allow_materialize : bool
        Guard for ``access_pattern='full'``.

    Returns
    -------
    dict with:
      source        : the object to read from (NOT materialised for 'framewise')
      shape         : (T, H, W) or (H, W)
      is_stack      : True when the data has a leading axis to iterate
      n_frames      : T, or 1 for a 2-D image
      materialized  : True only when a full array was actually built

    Raises
    ------
    ValueError when ``access_pattern='full'`` is requested without
    ``allow_materialize=True``. That is intentional: the whole point is that
    materialising a large movie should never happen by accident.
    """
    data = getattr(layer_or_data, 'data', layer_or_data)
    shp = stack_shape(data)
    is_stack = len(shp) >= 3
    n = int(shp[0]) if is_stack else 1

    if access_pattern == 'full':
        if not allow_materialize:
            raise ValueError(
                "get_array_source(access_pattern='full') requires "
                "allow_materialize=True. A full-stack materialisation can allocate "
                "gigabytes; if the algorithm genuinely needs every frame in memory at "
                "once, say so explicitly. If it processes frames one at a time, use "
                "access_pattern='framewise' and iter_frames/read_frame instead.")
        arr = materialize_stack(data, dtype=dtype)
        return dict(source=arr, shape=arr.shape, is_stack=arr.ndim >= 3,
                    n_frames=(arr.shape[0] if arr.ndim >= 3 else 1),
                    materialized=True)

    if access_pattern not in ('framewise', 'roi'):
        raise ValueError(f"unknown access_pattern {access_pattern!r}")

    return dict(source=data, shape=shp, is_stack=is_stack, n_frames=n,
                materialized=False)


def stream_stats(stack_like, percentiles=(1, 99)):
    """Global statistics in ONE streaming pass — never materialising the movie.

    Computing a global min/max by ``np.asarray(src[:]).max()`` allocates the entire stack
    to obtain a single scalar, which defeats the whole lazy-loading design. Worse, the
    pipeline then makes *separate* passes for normalisation, QC and preprocessing — so a
    large movie is read from disk several times over, and I/O dominates.

    One pass, many answers: min, max, mean, variance (Welford), the saturated fraction and
    approximate percentiles (from a coarse histogram, refined against the true range).
    """
    n_frames = stack_shape(stack_like)[0] if len(stack_shape(stack_like)) >= 3 else 1
    gmin, gmax = np.inf, -np.inf
    count = 0
    mean = 0.0
    m2 = 0.0

    for t in range(n_frames):
        f = read_frame(stack_like, t).astype(np.float64, copy=False)
        f = f[np.isfinite(f)]
        if f.size == 0:
            continue
        gmin = min(gmin, float(f.min()))
        gmax = max(gmax, float(f.max()))
        # Welford, batched
        k = f.size
        delta = float(f.mean()) - mean
        new_count = count + k
        mean += delta * k / new_count
        m2 += float(((f - f.mean()) ** 2).sum()) + delta ** 2 * count * k / new_count
        count = new_count

    if count == 0:
        return dict(min=np.nan, max=np.nan, mean=np.nan, std=np.nan,
                    n=0, percentiles={})

    # second (cheap) pass only for percentiles, and only over a histogram
    nbins = 512
    hist = np.zeros(nbins, dtype=np.int64)
    if gmax > gmin:
        for t in range(n_frames):
            f = read_frame(stack_like, t).astype(np.float64, copy=False).ravel()
            f = f[np.isfinite(f)]
            if f.size:
                h, _ = np.histogram(f, bins=nbins, range=(gmin, gmax))
                hist += h
        cdf = np.cumsum(hist) / max(hist.sum(), 1)
        centres = np.linspace(gmin, gmax, nbins)
        pct = {p: float(np.interp(p / 100.0, cdf, centres)) for p in percentiles}
    else:
        pct = {p: float(gmin) for p in percentiles}

    return dict(min=float(gmin), max=float(gmax), mean=float(mean),
                std=float(np.sqrt(m2 / count)) if count > 1 else 0.0,
                n=int(count), percentiles=pct)


# ── The accessors that RAISE. A test guard erodes; a type is a wall. ──────────────────────

class NotAStack(TypeError):
    """**A time-series analysis was handed something that is not a time-series.**

    Or — far more likely — it was handed a **lazy stack that `np.asarray` had already collapsed to
    frame 0**, and it is about to conclude the data is 2D.
    """


class NotAPlane(TypeError):
    """A 2-D analysis was handed a stack, and would have silently used frame 0."""


def require_stack(layer_or_data, *, context='this analysis', dtype=np.float32):
    """**Get the whole movie, or RAISE.** For anything that measures across time.

    Why this exists
    ---------------
    Four separate bugs this year had the same shape::

        data = np.asarray(layer.data)     # a lazy wrapper returns FRAME 0. Nothing errors.
        if data.ndim < 3:
            warn("this layer is 2D")      # ...on a correct time-series.

    N&B (which measures a **variance across time** — zero on one frame) told users their movie was
    2D. SpIDA silently analysed frame 0 while the user was looking at frame 40. VPT collapsed a
    1000-frame bead movie. The temperature UI did the same.

    **The shape is the thing that lies.** ``layer_is_stack`` answers the question correctly, and
    **27 toolbox modules re-derive it by hand from ``.ndim``** — on an array that may already have
    been collapsed.

    A test can catch a bad call site. **This catches it at the moment it happens**, with a message
    that says what went wrong instead of a plausible-looking number.
    """
    data = getattr(layer_or_data, 'data', layer_or_data)

    if not layer_is_stack(data):
        shape = getattr(data, 'shape', None)
        raise NotAStack(
            f"{context} needs a time-series (a stack of frames), and this layer is "
            f"{'2-D' if shape and len(shape) < 3 else f'shaped {shape}'}.\n\n"
            f"**If you loaded a movie, this is a bug, not your data.** PyCAT's lazy wrappers "
            f"return frame 0 from `np.asarray`, so a stack can silently look 2-D — which is "
            f"exactly what this check exists to catch.")

    return materialize_stack(data, dtype=dtype)


def require_plane(layer_or_data, *, frame=0, context='this analysis', dtype=np.float32):
    """**Get ONE frame, deliberately.** For anything that measures on a single image.

    The counterpart trap: a 2-D analysis handed a stack **silently uses frame 0** — and the user,
    who is looking at frame 40, never finds out. SpIDA did exactly this.

    Passing ``frame`` explicitly makes the choice visible. Getting frame 0 because nobody thought
    about it is not a choice.
    """
    data = getattr(layer_or_data, 'data', layer_or_data)

    if not layer_is_stack(data):
        return np.asarray(data, dtype=dtype)

    return extract_2d_plane(data, frame_index=int(frame), dtype=dtype)
