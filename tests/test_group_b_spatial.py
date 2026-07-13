"""
Group B — spatial statistics. **A number without a null is not evidence.**

The hypothesis before any code was run: *the Ripley/PCF null machinery exists (1.5.397, 419, 420)
and none of these modules use it — so they report clustering without a significance test.*

It held for ``organizational_metrics_tools``, and **not** for ``spatial_randomness_tools``, which
turned out to be one of the strongest modules in the codebase.

"We found 8 clusters" is not evidence of clustering
----------------------------------------------------
A **complete-spatial-randomness** pattern — 120 uniformly distributed points, *by definition not
clustered* — produces clusters every single time. Measured over 20 realisations:

    clusters found: 11, 8, 7, 8, 8, 13, 10, 9, 6, 10, 9, 9, 11, 8, 9, 10, 10, 7, 7, 10
    **mean 9.0, range 6-13**

**And the COUNT points the wrong way.** Clustering makes FEWER, BIGGER clusters; randomness
scatters many small accidental ones:

============  ============  =================  ==============
pattern       n_clusters    LARGEST cluster    noise points
============  ============  =================  ==============
CSR           8.0           **9.6**            **76.7**
CLUSTERED     **4.8**       **45.6**           **2.5**
============  ============  =================  ==============

**The clustered pattern has FEWER clusters than the random one.** A count-based test is not
merely underpowered — it is *anti-correlated with the truth*, and a first version of the
significance test got the answer exactly backwards (CSR "clustered" at p = 0.030; a real cluster
"random" at p = 0.790).

The statistic that works is the **fraction of points in any cluster at all** (36 % vs 98 %),
against a **compartment-constrained** null. **False positives 4 %, power 100 %.**

*(And a note on testing a null: an early run showed a 40 % false-positive rate, and it was **my
test**, not the null — the CSR pattern was drawn from a 180×180 box while the mask was 200×200, so
the data was 1.23× denser than the compartment. The null was correctly reporting that. **A null
can only be validated against a process drawn from the same region it re-scatters into.**)*
"""

import numpy as np
import pytest


_SIZE = 200


def _region_mask():
    mask = np.zeros((_SIZE, _SIZE), bool)
    mask[10:_SIZE - 10, 10:_SIZE - 10] = True
    return mask


def _csr_points(n=120, seed=0):
    """Complete spatial randomness, drawn **from the mask itself**.

    This matters: a CSR pattern drawn from a smaller box than the mask is *denser* than the
    compartment, and a correct null will (rightly) call it clustered.
    """
    mask = _region_mask()
    inside = np.argwhere(mask).astype(float)
    rng = np.random.default_rng(seed)
    return inside[rng.integers(0, len(inside), n)] + rng.uniform(0, 1, (n, 2))


def _clustered_points(n=120, n_groups=6, spread=8.0, seed=0):
    rng = np.random.default_rng(seed)
    centres = rng.uniform(30, _SIZE - 30, size=(n_groups, 2))
    points = [centres[i % n_groups] + rng.normal(0, spread, 2) for i in range(n)]
    return np.clip(np.asarray(points), 11, _SIZE - 11)


@pytest.mark.core
def test_a_random_pattern_produces_clusters_by_chance():
    """The premise. **If this fails, the significance test is unnecessary.**"""
    org = pytest.importorskip("pycat.toolbox.organizational_metrics_tools")

    counts = [len(org.cluster_size_distribution(_csr_points(seed=s), eps_um=12.0, min_samples=4))
              for s in range(10)]

    assert min(counts) > 0, (
        f"a CSR pattern produced no clusters in some realisation ({counts}). The whole point of "
        f"a spatial null is that randomness DOES produce clusters — if it did not, a raw count "
        f"would be evidence on its own."
    )


@pytest.mark.core
def test_the_cluster_count_points_the_WRONG_WAY():
    """**Clustering makes FEWER, BIGGER clusters.** The count is anti-correlated with the truth.

    This is why a count-based significance test got the answer backwards, and it is worth
    guarding: anyone reaching for ``n_clusters`` as evidence is reaching for a statistic that
    moves the wrong way.
    """
    org = pytest.importorskip("pycat.toolbox.organizational_metrics_tools")

    csr_counts = [len(org.cluster_size_distribution(_csr_points(seed=s), eps_um=12.0,
                                                    min_samples=4)) for s in range(10)]
    clustered_counts = [len(org.cluster_size_distribution(_clustered_points(seed=s), eps_um=12.0,
                                                          min_samples=4)) for s in range(10)]

    assert np.mean(clustered_counts) < np.mean(csr_counts), (
        f"a CLUSTERED pattern averaged {np.mean(clustered_counts):.1f} clusters and a RANDOM one "
        f"{np.mean(csr_counts):.1f}. The count is expected to point the WRONG way — if that has "
        f"changed, the warning in cluster_count_significance should be revisited."
    )


@pytest.mark.core
def test_the_clustering_null_has_the_right_false_positive_rate():
    """**4 % false positives on CSR**, against a nominal 5 %.

    A null that over-fires is worse than none: every "the condensates are clustered" claim
    becomes unfalsifiable.
    """
    org = pytest.importorskip("pycat.toolbox.organizational_metrics_tools")

    mask = _region_mask()
    false_positives = sum(
        1 for s in range(25)
        if org.cluster_count_significance(_csr_points(seed=s), mask, eps_um=12.0,
                                          min_samples=4, n_simulations=99,
                                          seed=s)["clustered"])

    assert false_positives <= 4, (
        f"{false_positives}/25 CSR patterns were called clustered — a "
        f"{100 * false_positives / 25:.0f}% false-positive rate against a nominal 5%."
    )


@pytest.mark.core
def test_the_clustering_null_detects_real_clustering():
    """**100 % power.** A null with no power is a null that never says anything."""
    org = pytest.importorskip("pycat.toolbox.organizational_metrics_tools")

    mask = _region_mask()
    detected = sum(
        1 for s in range(15)
        if org.cluster_count_significance(_clustered_points(seed=s), mask, eps_um=12.0,
                                          min_samples=4, n_simulations=99,
                                          seed=s)["clustered"])

    assert detected >= 14, (
        f"only {detected}/15 genuinely clustered patterns (6 tight groups) were detected"
    )


# ── spatial_randomness_tools: the hypothesis did NOT hold, and that is the finding ─────────

@pytest.mark.core
def test_morans_I_is_textbook_correct():
    """0.0001 on white noise, 0.97 on smoothed, **exactly −1.0 on a checkerboard.**"""
    from scipy import ndimage as ndi

    sr = pytest.importorskip("pycat.toolbox.spatial_randomness_tools")

    size = 128
    rng = np.random.default_rng(0)

    white = sr.morans_I(rng.normal(0, 1, (size, size)).astype(np.float32))
    smooth = sr.morans_I(ndi.gaussian_filter(
        rng.normal(0, 1, (size, size)), 3).astype(np.float32))
    checker = sr.morans_I((np.indices((size, size)).sum(0) % 2 * 2.0 - 1).astype(np.float32))

    assert abs(float(white)) < 0.02, f"white noise gave Moran's I = {float(white):.4f}"
    assert float(smooth) > 0.9, f"smoothed noise gave {float(smooth):.4f}"
    assert float(checker) == pytest.approx(-1.0, abs=0.01), (
        f"a checkerboard is the textbook maximum-negative case; got {float(checker):.4f}"
    )


@pytest.mark.core
def test_structure_beyond_optics_separates_biology_from_the_PSF():
    """**The hypothesis did NOT hold here — and this module is a model for the others.**

    *Every* microscope image is autocorrelated: the PSF puts structure there for free. The
    question a microscopist actually has is whether there is structure **beyond** that, and
    ``structure_beyond_optics`` answers it — its docstring explains, correctly, why a
    pixel-shuffled null cannot.

    ==========================================  ==========  ============
    field                                       p           kurtosis
    ==========================================  ==========  ============
    PSF-blurred noise (**no** real structure)   0.119       0.19
    **bright blobs + the same PSF blur**        **0.005**   **4.85**
    ==========================================  ==========  ============

    **False positives 4-12 %, power 100 %** — detecting real structure even at a blob amplitude
    of 0.5 against a noise sd of 0.3.
    """
    from scipy import ndimage as ndi

    sr = pytest.importorskip("pycat.toolbox.spatial_randomness_tools")

    size = 128
    yy, xx = np.mgrid[0:size, 0:size]
    rng = np.random.default_rng(0)

    psf_only = ndi.gaussian_filter(rng.normal(0, 1, (size, size)), 2.0).astype(np.float32)

    blobs = np.zeros((size, size))
    for _ in range(12):
        cy, cx = rng.integers(15, size - 15, size=2)
        blobs += 5.0 * np.exp(-(((yy - cy) ** 2 + (xx - cx) ** 2)) / (2 * 5.0 ** 2))
    real = ndi.gaussian_filter(blobs + rng.normal(0, 0.3, (size, size)), 2.0).astype(np.float32)

    on_psf = sr.structure_beyond_optics(psf_only, n_permutations=200)
    on_real = sr.structure_beyond_optics(real, n_permutations=200)

    assert on_psf["p_value"] > 0.05, (
        f"PSF-blurred noise — which has NO biological structure — was called structured "
        f"(p = {on_psf['p_value']:.3f}). Every image is autocorrelated by the optics; a test "
        f"that cannot see past that makes every clustering claim unfalsifiable."
    )
    assert on_real["p_value"] < 0.05, (
        f"obvious bright blobs were NOT detected (p = {on_real['p_value']:.3f})"
    )


# ── Morphological complexity: fractal D has EXACT analytic ground truth ───────────────────

@pytest.mark.core
def test_fractal_dimension_is_exact_on_a_sierpinski_triangle():
    """**1.5850 against an analytic log(3)/log(2) = 1.5850.**

    This is the strongest evidence the box-counting algorithm is right, and it is worth having:
    fractal dimension is one of the few measurements in the codebase with an *exact* closed-form
    answer to check against.
    """
    mc = pytest.importorskip("pycat.toolbox.morphological_complexity_tools")

    size = 256
    yy, xx = np.mgrid[0:size, 0:size]
    sierpinski = ((xx & yy) == 0)          # the bitwise-AND rule gives the triangle

    measured = float(mc.fractal_dimension_box_counting(sierpinski))
    analytic = np.log(3) / np.log(2)

    assert measured == pytest.approx(analytic, abs=0.02), (
        f"D = {measured:.4f} against an exact {analytic:.4f}"
    )


@pytest.mark.core
def test_the_fractal_dimension_of_a_DISC_depends_on_its_SIZE():
    """**A 57 % difference in D, from geometry alone — and it would test as significant.**

    Box counting on a finite image has a finite range of box sizes, and D approaches its true
    value only as the object grows. A **filled square** — true dimension exactly 2.0 — measures
    1.751 at 64 px and 1.881 at 512 px. *It never gets there.*

    The consequence is a **false conclusion that looks solid**. Two conditions containing nothing
    but DISCS — identical shape, only the size differs:

    ===========================  ==================
    condition                    D
    ===========================  ==================
    small discs (r = 6 px)       **0.966** (sd 0.021)
    large discs (r = 20 px)      **1.516** (sd 0.016)
    ===========================  ==================

    The scatter is tiny, so this tests as overwhelmingly significant — and *"condition B is more
    space-filling"* is **completely false. A disc is a disc.**

    This test asserts the confound EXISTS, so that the warning attached to
    ``fractal_dimension_box_counting`` cannot be quietly dropped. If box counting is ever made
    size-invariant, this test should fail — and that would be a real improvement.
    """
    mc = pytest.importorskip("pycat.toolbox.morphological_complexity_tools")

    size = 256
    yy, xx = np.mgrid[0:size, 0:size]

    def _discs(radius, seed):
        rng = np.random.default_rng(seed)
        mask = np.zeros((size, size), bool)
        for _ in range(8):
            cy, cx = rng.integers(radius + 5, size - radius - 5, size=2)
            mask |= (np.sqrt((yy - cy) ** 2 + (xx - cx) ** 2) < radius)
        return mask

    small = [float(mc.fractal_dimension_box_counting(_discs(6, s))) for s in range(5)]
    large = [float(mc.fractal_dimension_box_counting(_discs(20, s))) for s in range(5)]

    assert np.mean(large) - np.mean(small) > 0.3, (
        f"small discs gave D = {np.mean(small):.3f} and large discs {np.mean(large):.3f}. The "
        f"SHAPE is identical — only the size differs. If this confound has been fixed, the "
        f"warning on fractal_dimension_box_counting should be updated."
    )


@pytest.mark.core
def test_the_fractal_table_carries_the_object_size():
    """**Without the size beside it, a size-driven D difference is invisible.**"""
    mc = pytest.importorskip("pycat.toolbox.morphological_complexity_tools")

    size = 128
    yy, xx = np.mgrid[0:size, 0:size]
    cells = np.zeros((size, size), np.int32)
    cells[8:size - 8, 8:size - 8] = 1

    rng = np.random.default_rng(0)
    puncta = np.zeros((size, size), bool)
    for _ in range(6):
        cy, cx = rng.integers(20, size - 20, size=2)
        puncta |= np.sqrt((yy - cy) ** 2 + (xx - cx) ** 2) < 8

    table = mc.fractal_dimension_per_cell(puncta, cells)

    assert 'mean_object_area_px' in table.columns, (
        "the fractal table must carry the object SIZE, because D depends on it — a 57% "
        "difference in D can come from size alone, and this table is what a user compares "
        "between conditions"
    )


# ── Topology: one good statistic beside a broken one ──────────────────────────────────────

@pytest.mark.core
def test_topo_cov_responds_to_structure_and_n_basins_does_not():
    """``topo_n_basins`` is **a constant**, and it is **anti-correlated** with the truth.

    ``peak_local_max`` with only a ``min_distance`` accepts every local maximum however small.
    On a **flat field with nothing but noise** it reports **6.3 basins** — and it reports 6.3 at
    a noise sd of 5, 20 and 60 alike. **It is measuring how many points of separation
    ``min_distance`` fit inside the mask.**

    ``topo_cov`` on the same data behaves perfectly: **0.001 flat → 0.42 with real structure.**

    A global prominence gate was attempted and **made it worse** (the flat field still reported 4;
    6 genuine peaks dropped to 2.3) — real structure raises the median, which then excludes the
    structure. **A correct fix needs a topological prominence, not a threshold.** Written up in
    ``docs/audits/DEV_NOTES.md``.
    """
    topo = pytest.importorskip("pycat.toolbox.topology_tools")

    size = 128
    yy, xx = np.mgrid[0:size, 0:size]
    mask = np.ones((size, size), bool)

    def _field(n_peaks, seed=0):
        rng = np.random.default_rng(seed)
        img = np.full((size, size), 200.0)
        for i in range(n_peaks):
            cy = 25 + (i // 3) * 40
            cx = 25 + (i % 3) * 40
            img += 800 * np.exp(-(((yy - cy) ** 2 + (xx - cx) ** 2)) / (2 * 10.0 ** 2))
        return np.clip(img + rng.normal(0, 20, img.shape), 0, 4095).astype(np.float32)

    flat = topo.topology_metrics(
        topo.compute_topology_envelope(_field(0), ball_radius=15), mask)
    structured = topo.topology_metrics(
        topo.compute_topology_envelope(_field(6), ball_radius=15), mask)

    assert flat['topo_cov'] < 0.1 < structured['topo_cov'], (
        f"topo_cov is the statistic that WORKS here: {flat['topo_cov']:.3f} on a flat field "
        f"against {structured['topo_cov']:.3f} with real structure"
    )
    assert flat.get('topo_n_basins_is_unreliable'), (
        "topo_n_basins counts noise on a flat field (6.3 basins, regardless of the noise level) "
        "and must be flagged as unreliable so a consumer knows to prefer topo_cov"
    )
