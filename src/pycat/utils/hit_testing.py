"""**Honest hit-testing for spaghetti plots — the nearest curve, or NOTHING when it's ambiguous.**

Matplotlib's per-artist ``set_picker`` returns whichever line it hits first — arbitrary, and in a dense
tangle of curves scientifically dishonest: a click near a crossing point selects *a* curve, giving the user
a confident answer the data does not support. This replaces that with one deliberate hit-test over the
curves' coordinate arrays: the nearest curve by point-to-segment distance, and — the point — a **refusal**
when two curves are too close to tell apart, naming the candidates instead of guessing.

Pure geometry, Qt-free and backend-neutral: the caller passes curve coordinates already in DISPLAY space (a
log-log MSD plot must be hit-tested in display pixels, not data units, or the distances are meaningless).
The same primitive backs the matplotlib wiring and any future backend adapter.
"""
from __future__ import annotations

import dataclasses

import numpy as np


@dataclasses.dataclass(frozen=True)
class HitResult:
    """The outcome of a click. ``primary`` is the selected entity id, or ``None`` for a miss OR a refused
    ambiguous click. ``candidates`` names the curve(s) in contention (>1 ⇒ ambiguous, report them, select
    nothing). ``distance_px`` is the distance to the best curve; ``ambiguity_px`` is second-best − best (a
    small value means the click could not distinguish them)."""
    primary: "str | None"
    candidates: tuple
    distance_px: float
    ambiguity_px: float

    @property
    def is_hit(self) -> bool:
        return self.primary is not None

    @property
    def is_ambiguous(self) -> bool:
        return self.primary is None and len(self.candidates) > 1


def point_segment_distance(px, py, ax, ay, bx, by) -> float:
    """Distance from point ``p`` to segment ``ab`` — the projection clamped to the segment
    (``t = clip(dot(p−a, b−a)/dot(b−a, b−a), 0, 1)``), so a click past an endpoint measures to the endpoint,
    not the infinite line."""
    abx, aby = bx - ax, by - ay
    denom = abx * abx + aby * aby
    if denom == 0.0:                       # degenerate segment → distance to the point
        return float(np.hypot(px - ax, py - ay))
    t = ((px - ax) * abx + (py - ay) * aby) / denom
    t = min(1.0, max(0.0, t))
    cx, cy = ax + t * abx, ay + t * aby
    return float(np.hypot(px - cx, py - cy))


def nearest_distance_to_curve(px, py, xs, ys) -> float:
    """The minimum point-to-segment distance from ``p`` to the polyline ``(xs, ys)``. A single point (no
    segment) measures straight to it. Non-finite vertices are skipped."""
    xs = np.asarray(xs, dtype=float)
    ys = np.asarray(ys, dtype=float)
    n = min(xs.size, ys.size)
    if n == 0:
        return float('inf')
    if n == 1:
        return float(np.hypot(px - xs[0], py - ys[0])) if np.isfinite(xs[0]) and np.isfinite(ys[0]) \
            else float('inf')
    best = float('inf')
    for i in range(n - 1):
        ax, ay, bx, by = xs[i], ys[i], xs[i + 1], ys[i + 1]
        if not (np.isfinite(ax) and np.isfinite(ay) and np.isfinite(bx) and np.isfinite(by)):
            continue
        d = point_segment_distance(px, py, ax, ay, bx, by)
        if d < best:
            best = d
    return best


def hit_test(curves, click_xy, *, tolerance_px=6.0, ambiguity_px=3.0) -> HitResult:
    """The nearest curve to ``click_xy``, or a refusal.

    ``curves`` maps an entity id → its ``(xs, ys)`` DISPLAY-space coordinates. Returns a :class:`HitResult`:
    - **miss** — the nearest curve is beyond ``tolerance_px`` → ``primary=None``, no candidates.
    - **ambiguous** — the best and second-best are within ``ambiguity_px`` of each other → ``primary=None``,
      ``candidates`` names both (the caller reports them; selecting a guess is the dishonesty this removes).
    - **clean hit** — ``primary`` is the nearest curve, ``candidates=(primary,)``.

    A direct scan; with ~100 displayed curves it is instant, so there is no spatial index to keep in sync."""
    px, py = float(click_xy[0]), float(click_xy[1])
    scored = sorted(((nearest_distance_to_curve(px, py, xs, ys), key)
                     for key, (xs, ys) in curves.items()), key=lambda t: t[0])
    if not scored or not np.isfinite(scored[0][0]) or scored[0][0] > tolerance_px:
        best = scored[0][0] if scored else float('inf')
        return HitResult(primary=None, candidates=(), distance_px=best, ambiguity_px=float('inf'))
    best_d, best_key = scored[0]
    second_d = scored[1][0] if len(scored) > 1 else float('inf')
    ambiguity = second_d - best_d
    if second_d <= tolerance_px and ambiguity < ambiguity_px:
        return HitResult(primary=None, candidates=(best_key, scored[1][1]),
                         distance_px=best_d, ambiguity_px=ambiguity)
    return HitResult(primary=best_key, candidates=(best_key,),
                     distance_px=best_d, ambiguity_px=ambiguity)
