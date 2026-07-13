"""
Every QC metric, against the defect it names — and the defects it must ignore.

``data_qc_tools`` is the manuscript's enabling layer: the claim is that PyCAT tells a scientist
*"can I trust this data, and if not, how do I improve it?"*. **Four bugs were fixed in it
(1.5.403–406) and it had zero tests.**

The test that matters for a QC metric is not "does it return a number". It is:

* **does it move when its OWN defect is present**, and
* **does it stay put when a DIFFERENT defect is present?**

That second half is what catches the failures that actually occurred. A focus score that rises
with noise is measuring noise (1.5.405). A vignetting score that reads cells-in-the-centre as
bad illumination is measuring object placement (1.5.404). **Both returned confident numbers.**

Audited: all 13 public QC functions. **12 were correct.** The one that was not is below.
"""

import numpy as np
import pytest
from scipy import ndimage as ndi

from tests.imaging_realism import blur, illumination_gradient, drift, photobleach


def _scene(size=192, seed=0):
    """Puncta on a dim background — the thing every QC metric is pointed at.

    **This is deliberately NOT diffraction-limited** (sigma 3 px, where 1.4 NA at 0.065 um/px
    permits ~1.2). It is the scene the SNR, vignetting, drift, vibration and saturation checks
    were all validated against, and changing it to satisfy the focus check broke four of them.

    The focus check has its own scene (``_focus_scene``), because it is the only one that
    compares against an ABSOLUTE optical standard. **One fixture cannot serve every check**, and
    forcing it to is how a test suite starts lying.
    """
    yy, xx = np.mgrid[0:size, 0:size]
    rng = np.random.default_rng(seed)

    img = np.full((size, size), 50.0)
    for _ in range(25):
        cy, cx = rng.integers(30, size - 30), rng.integers(30, size - 30)
        img += 500 * np.exp(-(((yy - cy) ** 2 + (xx - cx) ** 2)) / (2 * 3.0 ** 2))
    return img


def _focus_scene(size=192, seed=0, sigma_px=1.2):
    """A DIFFRACTION-LIMITED scene, for the check that compares against the diffraction limit.

    ``sigma_px = 1.2`` is what 1.4 NA at 0.065 um/px permits. A scene drawn with wider objects is
    **soft by construction**, and the focus check would correctly say so — which makes it useless
    as a positive control.
    """
    yy, xx = np.mgrid[0:size, 0:size]
    rng = np.random.default_rng(seed)

    img = np.full((size, size), 50.0)
    for _ in range(25):
        cy, cx = rng.integers(30, size - 30), rng.integers(30, size - 30)
        img += 700 * np.exp(-(((yy - cy) ** 2 + (xx - cx) ** 2)) / (2 * sigma_px ** 2))
    return img + rng.normal(0, 6, img.shape)


def _stack(n=10, seed=0):
    rng = np.random.default_rng(seed)
    base = _scene(seed=seed)
    return np.stack([base + rng.normal(0, 8, base.shape) for _ in range(n)])


# ── The bug: the container's max is not the sensor's ceiling ─────────────────────────────

@pytest.mark.core
@pytest.mark.parametrize("ceiling,expected_pct", [(4095, 1.2), (1000, 9.1)])
def test_saturation_is_detected_below_the_dtype_maximum(ceiling, expected_pct):
    """A 12-bit camera in a uint16 array clips at 4095 — not at 65535.

    ``_dtype_max`` returned ``np.iinfo(uint16).max`` = **65535**, so a check against it found
    **nothing**. Measured, on a uint16 image whose two brightest objects are genuinely
    flat-topped:

    ==================================  ==============  ================
    image                               truly clipped   reported
    ==================================  ==============  ================
    clipped at 65535 (the dtype max)    0.0 %           0.00 % good
    clipped at 4095 (a 12-bit sensor)   **1.2 %**       **0.00 % good**
    clipped at 1000 (gain-limited)      **9.1 %**       **0.00 % good**
    ==================================  ==============  ================

    **Nine percent of the pixels destroyed, reported as "good"** — and saturation is the one
    defect that cannot be recovered downstream. A clipped intensity is *gone*, and every
    measurement built on it (Kp, enrichment, brightness, molecule counting) inherits a number
    that is not a lower bound on anything (1.5.392).

    The ceiling is now detected from the DATA: a pile-up of pixels at *exactly* the image
    maximum is the signature of a flat top, wherever the ceiling sits.
    """
    qc = pytest.importorskip("pycat.toolbox.data_qc_tools")

    yy, xx = np.mgrid[0:128, 0:128]
    img = np.full((128, 128), 200.0)
    for cy, cx in [(40, 40), (90, 90)]:
        img += 5000 * np.exp(-(((yy - cy) ** 2 + (xx - cx) ** 2)) / (2 * 8.0 ** 2))

    clipped = np.minimum(img, ceiling).astype(np.uint16)
    result = qc.qc_saturation(clipped)

    assert result["value"] == pytest.approx(expected_pct, abs=0.3), (
        f"{expected_pct:.1f}% of pixels are flat-topped at {ceiling}, and qc_saturation "
        f"reported {result['value']:.2f}%. The sensor ceiling is NOT the container maximum: a "
        f"12-bit camera in a uint16 array clips at 4095, and np.iinfo(uint16).max is 65535."
    )
    assert result["status"] == "bad"


@pytest.mark.core
def test_saturation_does_not_cry_wolf_on_an_unclipped_image():
    """An image with a brightest pixel is not a clipped image."""
    qc = pytest.importorskip("pycat.toolbox.data_qc_tools")

    yy, xx = np.mgrid[0:128, 0:128]
    img = np.full((128, 128), 200.0)
    for cy, cx in [(40, 40), (90, 90)]:
        img += 5000 * np.exp(-(((yy - cy) ** 2 + (xx - cx) ** 2)) / (2 * 8.0 ** 2))

    result = qc.qc_saturation(img.astype(np.uint16))     # peaks at ~5200, nothing clipped

    assert result["value"] == pytest.approx(0.0, abs=0.01), (
        f"an UNCLIPPED image reported {result['value']:.2f}% saturation. Detecting the ceiling "
        f"from the data must not fire on a scene that merely has a brightest pixel — a guard "
        f"that cries wolf will be ignored when it matters."
    )
    assert result["status"] == "good"


# ── The discrimination tests: each metric must ignore the OTHER defects ──────────────────

@pytest.mark.core
def test_focus_finds_a_defocused_frame_and_ignores_a_noisy_one():
    """A focus score that rises with noise is measuring noise (the 1.5.405 bug).

    ``var(Laplacian)`` is a **high-pass** filter, so broadband noise inflates it — a *noisier*
    image scored as *better focused*. The fix (1.5.405) is a difference-of-Gaussians band-pass,
    which responds to real edge content at object scale.

    Verified here on the path that actually judges — across a stack, comparing each frame to the
    median.
    """
    qc = pytest.importorskip("pycat.toolbox.data_qc_tools")

    rng = np.random.default_rng(1)
    base = _scene()
    sharp = np.stack([base + rng.normal(0, 8, base.shape) for _ in range(10)])

    defocused = sharp.copy()
    defocused[5] = blur(base, 4.0) + rng.normal(0, 8, base.shape)

    noisy_but_sharp = sharp.copy()
    noisy_but_sharp[5] = base + rng.normal(0, 45, base.shape)

    assert qc.qc_focus(sharp)["status"] == "good"

    assert qc.qc_focus(defocused)["status"] in ("warn", "bad"), (
        "a genuinely DEFOCUSED frame among sharp ones was not flagged — the metric is not "
        "measuring focus"
    )
    assert qc.qc_focus(noisy_but_sharp)["status"] == "good", (
        "a NOISY but perfectly in-focus frame was flagged as a focus problem. This is the "
        "1.5.405 failure: var(Laplacian) is a high-pass filter, so noise inflates it and a "
        "worse image scores as better focused."
    )


@pytest.mark.core
def test_snr_and_vignetting_are_invariant_to_the_camera_pedestal():
    """A pedestal adds a constant. It is not noise, and it is not uneven illumination."""
    qc = pytest.importorskip("pycat.toolbox.data_qc_tools")

    img = _scene()
    with_pedestal = img + 800.0

    snr_clean = qc.qc_snr(img)["value"]
    snr_ped = qc.qc_snr(with_pedestal)["value"]
    assert snr_ped == pytest.approx(snr_clean, rel=0.05), (
        f"SNR moved from {snr_clean:.1f} to {snr_ped:.1f} when an 800-count pedestal was "
        f"added. A pedestal shifts the signal AND the background by the same amount — it "
        f"changes neither the contrast nor the noise."
    )

    vig_clean = qc.qc_vignetting(img)["value"]
    vig_ped = qc.qc_vignetting(with_pedestal)["value"]
    assert vig_ped == pytest.approx(vig_clean, rel=0.15), (
        f"the vignetting score moved from {vig_clean:.3f} to {vig_ped:.3f} on a pedestal"
    )


@pytest.mark.core
def test_snr_falls_with_noise_and_vignetting_fires_on_a_gradient():
    """Each metric must actually respond to its OWN defect."""
    qc = pytest.importorskip("pycat.toolbox.data_qc_tools")

    rng = np.random.default_rng(2)
    img = _scene()

    noisy = img + rng.normal(0, 40, img.shape)
    assert qc.qc_snr(noisy)["value"] < 0.5 * qc.qc_snr(img)["value"], (
        "adding substantial noise did not reduce the SNR — the metric is not measuring SNR"
    )

    vignetted = illumination_gradient(img, 0.5)
    assert qc.qc_vignetting(vignetted)["status"] in ("warn", "bad"), (
        "a 50% illumination fall-off was not flagged"
    )


@pytest.mark.core
def test_drift_and_vibration_ignore_bleaching():
    """A bleaching sample gets DIMMER. It does not MOVE.

    ``phase_cross_correlation`` is *supposed* to be intensity-robust. **It is not robust enough
    when the frame is globally scaled** — the sub-pixel peak fit is biased by the changing DC
    term and noise floor.

    Measured, before the fix: a photobleaching stack that **does not move at all** drove
    ``qc_vibration`` to **p = 0.010, status "bad"** — a confident report of a *periodic
    vibration source*. The shift trace was tracking the exponential intensity decay, which is
    smooth and monotonic and therefore concentrated in the low-frequency bins: **exactly the
    signature the permutation test looks for.**

    **The user is sent to check their pumps and fans, and the stage is fine.**

    The fix is to z-score each frame before correlating (``_shift_normalise``), so only
    STRUCTURE drives the registration.
    """
    qc = pytest.importorskip("pycat.toolbox.data_qc_tools")

    bleaching = photobleach(_stack(n=32), 15.0)

    vibration = qc.qc_vibration(bleaching)
    assert vibration["status"] == "good", (
        f"a BLEACHING stack was flagged as vibrating (p = {vibration['p_value']:.3f}). It gets "
        f"dimmer; it does not move. The phase correlation is tracking the intensity decay — "
        f"which is smooth and monotonic, and therefore looks exactly like a low-frequency "
        f"periodic component."
    )
    assert qc.qc_drift(bleaching)["status"] == "good", (
        "a BLEACHING stack was flagged as drifting"
    )


@pytest.mark.core
def test_vibration_fires_on_a_PERIODIC_source_and_not_on_random_jitter():
    """``qc_vibration`` claims to find *"a vibration source (pump, fan, footsteps)"*.

    That is a claim about **periodicity**, not about motion in general — and the metric tests it
    with a permutation null (1.5.419/420) that destroys periodicity while keeping the jitter
    amplitudes. So random jitter must **not** fire, and a smooth drift must **not** fire; a
    periodic oscillation must.

    Measured: periodic (a pump at a 6-frame period) → **p = 0.005, "bad"**. Random jitter of the
    same amplitude → p = 0.519, "good". Drift → p = 0.459, "good".

    **The discrimination is the point.** A metric that fired on any motion would send the user
    hunting for a vibration source when the real problem is a drifting stage — a different
    cause, and a different fix.
    """
    from scipy import ndimage as ndi

    qc = pytest.importorskip("pycat.toolbox.data_qc_tools")
    rng = np.random.default_rng(3)

    frames = _stack(n=32)

    periodic = np.stack([
        ndi.shift(f, (2.5 * np.sin(2 * np.pi * t / 6.0), 0), order=1, mode="nearest")
        for t, f in enumerate(frames)])
    jitter = np.stack([
        ndi.shift(f, (rng.normal(0, 2.5), rng.normal(0, 2.5)), order=1, mode="nearest")
        for f in frames])

    assert qc.qc_vibration(periodic)["status"] in ("warn", "bad"), (
        "a PERIODIC oscillation (a pump at a 6-frame period) was not detected"
    )
    assert qc.qc_vibration(jitter)["status"] == "good", (
        "RANDOM jitter was called a vibration source. The metric tests for PERIODICITY against "
        "a permutation null — aperiodic jitter must not fire, or the user is sent looking for a "
        "pump that does not exist."
    )
    assert qc.qc_vibration(drift(_stack(n=32), (0.5, 0.3)))["status"] == "good", (
        "a smooth DRIFT was called a vibration. Drift and vibration have different causes and "
        "different fixes."
    )


@pytest.mark.core
def test_drift_fires_on_drift():
    """The positive control for qc_drift."""
    qc = pytest.importorskip("pycat.toolbox.data_qc_tools")

    assert qc.qc_drift(_stack(n=32))["status"] == "good"
    assert qc.qc_drift(drift(_stack(n=32), (0.5, 0.3)))["status"] in ("warn", "bad"), (
        "0.5 px/frame of stage drift was not flagged"
    )


@pytest.mark.core
def test_nyquist_uses_the_abbe_limit():
    """d = λ/(2·NA), and Nyquist wants at least two samples across it.

    This is pure physics and must be exactly right — it is the check that tells a user their
    pixel size is wrong, which is the failure that costs a **1435×** area error (1.5.443).
    """
    qc = pytest.importorskip("pycat.toolbox.data_qc_tools")

    # 1.4 NA at 520 nm: d = 0.186 µm, so Nyquist needs <= 0.093 µm/px.
    undersampled = qc.qc_nyquist(0.10, 1.4, 520)
    adequate = qc.qc_nyquist(0.065, 1.4, 520)

    assert undersampled["status"] in ("warn", "bad"), (
        "0.10 µm/px at 1.4 NA is UNDER-sampled (Nyquist wants <= 0.093) and was not flagged"
    )
    assert adequate["status"] == "good", (
        "0.065 µm/px at 1.4 NA satisfies Nyquist and was flagged anyway"
    )


# ── The report as a whole: no false alarms, and no checks run on data that cannot answer ──

def _brightfield(size=160, seed=0):
    """DARK objects on a BRIGHT field — the intensity convention is inverted."""
    yy, xx = np.mgrid[0:size, 0:size]
    rng = np.random.default_rng(seed)
    img = np.full((size, size), 3200.0)
    for cy, cx in [(50, 50), (50, 110), (110, 50), (110, 110)]:
        img -= 1400 * np.exp(-(((yy - cy) ** 2 + (xx - cx) ** 2)) / (2 * 14.0 ** 2))
    return np.clip(img + rng.normal(0, 30, img.shape), 0, 4095).astype(np.uint16)


def _zstack(asymmetry=0.0, size=160, n_planes=21, seed=0):
    """A focal series. ``asymmetry`` > 0 makes the through-focus response one-sided."""
    yy, xx = np.mgrid[0:size, 0:size]
    rng = np.random.default_rng(seed)
    planes = []
    for k in range(n_planes):
        d = k - n_planes // 2
        sigma = 1.5 + abs(d) * 0.55 + asymmetry * max(d, 0) * 0.55
        f = np.full((size, size), 40.0)
        for cy, cx in [(50, 50), (50, 110), (110, 50), (110, 110)]:
            f += 800 * np.exp(-(((yy - cy) ** 2 + (xx - cx) ** 2)) / (2 * sigma ** 2))
        planes.append(np.clip(f + rng.normal(0, 6, f.shape), 0, 4095))
    return np.stack(planes).astype(np.uint16)


@pytest.mark.core
@pytest.mark.parametrize("label", ["2d_fluor", "brightfield", "zstack", "timeseries"])
def test_clean_data_of_every_type_raises_no_alarms(label):
    """**Any warn or bad on clean data is a false alarm, by definition.**

    A confident false alarm is worse than no check: the user goes and fixes something that is
    not broken, and **learns to distrust the whole report** — including the checks that are
    right.

    Audited across 2D fluorescence, brightfield, z-stacks and time series. **All four false
    alarms were on the z-stack**, and every one of them was a check being asked a question the
    data cannot answer:

    ==========================  ==========  ====================================================
    check                       z-stack     why it was a false alarm
    ==========================  ==========  ====================================================
    Drift                       **bad**     Measuring the sharp-vs-blurred mismatch between
                                            focal planes, not displacement. On a stack with
                                            **zero** drift it reported **89 px**; a full pixel
                                            per plane of REAL drift moved it only to 100.
                                            **Blind to the thing it names.**
    Focus / sharpness           **warn**    A z-stack is SUPPOSED to have blurred planes. It
                                            flagged 2/21 as defective — which is what a focal
                                            series IS.
    Ghosting                    **warn**    Out-of-focus signal is not a double image.
    Spherical aberration        **warn**    Inverted — see the dedicated test below.
    ==========================  ==========  ====================================================
    """
    qc = pytest.importorskip("pycat.toolbox.data_qc_tools")

    data, kwargs = {
        "2d_fluor": (_scene(), {}),
        "brightfield": (_brightfield(), {}),
        "zstack": (_zstack(), dict(is_zstack=True)),
        "timeseries": (_stack(n=20), {}),
    }[label]

    report = qc.run_full_qc(data, pixel_um=0.065, na=1.4, wavelength_nm=520, **kwargs)

    alarms = [f"{r['name']} = {r['status']} ({r['headline']})"
              for r in report if r["status"] in ("warn", "bad")]

    assert not alarms, (
        f"CLEAN {label} data raised {len(alarms)} alarm(s):\n\n  " + "\n  ".join(alarms)
        + "\n\nThis data has no defects. Every one of these is a false alarm, and a confident "
          "false alarm teaches the user to ignore the report."
    )


@pytest.mark.core
def test_inapplicable_checks_say_so_rather_than_passing():
    """A check that cannot apply must not report 'good'. That is a quiet lie.

    Reporting *good* for a question the data cannot answer tells the user their data **passed a
    test that was never run**. So the check appears, marked ``n/a``, **with the reason** — the
    anti-black-box answer: PyCAT considered it and declined, rather than silently skipping it.
    """
    qc = pytest.importorskip("pycat.toolbox.data_qc_tools")

    on_2d = {r["name"]: r for r in qc.run_full_qc(_scene())}
    for name in ("Drift", "Vibration", "Photobleaching"):
        assert on_2d[name]["status"] == "na", (
            f"'{name}' reported '{on_2d[name]['status']}' on a SINGLE IMAGE. There is no time "
            f"axis — it cannot pass, and it must not appear to."
        )
        assert on_2d[name]["how"], f"'{name}' is marked n/a but does not say WHY"

    on_z = {r["name"]: r for r in qc.run_full_qc(_zstack(), is_zstack=True)}
    for name in ("Drift", "Focus / sharpness", "Ghosting (double image)"):
        assert on_z[name]["status"] == "na", (
            f"'{name}' reported '{on_z[name]['status']}' on a z-stack"
        )


@pytest.mark.core
def test_spherical_aberration_was_inverted():
    """It fired on CLEAN stacks and passed the aberration it exists to detect.

    Two bugs, stacked:

    1. **The axial profile did not peak at best focus.** ``_axial_sharp`` is a
       difference-of-Gaussians band-pass at sigma 1–2, and when the in-focus objects are
       *sharper* than that band, the response **dips at best focus** — the sharpest plane is a
       local *minimum*. ``argmax`` then landed one plane off, the moments were taken about the
       wrong origin, and a **perfectly symmetric stack** (left energy = right energy = 544,
       exactly) reported a skew of **+0.577 → "warn"**.

    2. **The normalised third moment is the wrong statistic.** Fixing the origin exposed a false
       negative: a stack with **half the energy on one side of focus** (right/left = 0.499)
       reported |skew| = 0.080 against a threshold of 0.4, and **passed as good**. The
       ``m2**1.5`` denominator grows with the axial spread — and spherical aberration *is* a
       one-sided spread, so the normalisation **cancels the very asymmetry it should expose.**

    The physical question is simpler: *does the response fall off at the same rate above and
    below focus?* That is an energy ratio, and it is what a bead z-stack is inspected for by eye.
    """
    qc = pytest.importorskip("pycat.toolbox.data_qc_tools")

    symmetric = qc.qc_spherical_aberration(_zstack(asymmetry=0.0), is_zstack=True)
    severe = qc.qc_spherical_aberration(_zstack(asymmetry=3.0), is_zstack=True)

    assert symmetric["status"] == "good", (
        f"a perfectly SYMMETRIC z-stack was flagged ({symmetric['headline']}). The old code "
        f"reported skew +0.577 on exactly this data, because argmax landed one plane off the "
        f"true focus — and the sharpest plane is a local MINIMUM of the DoG band-pass when the "
        f"objects are sharper than the band."
    )
    assert severe["status"] in ("warn", "bad"), (
        f"a SEVERELY aberrated z-stack passed as '{severe['status']}' "
        f"({severe['headline']}). The normalised third moment cancels the asymmetry it is "
        f"supposed to detect: the m2**1.5 denominator grows with the very one-sided spread that "
        f"IS the aberration."
    )
    assert severe["value"] > symmetric["value"] + 0.2, (
        "the aberrated stack must score visibly worse than the symmetric one"
    )


@pytest.mark.core
def test_the_verdict_says_how_many_checks_actually_ran():
    """*"All assessed metrics look good"* is technically true and practically a trap.

    On an image with no pixel size, no NA and no frame interval, **only 4 of 12 checks
    actually run**. Nyquist, time sampling, chromatic aberration, drift, vibration,
    photobleaching and spherical aberration are all skipped — and the report used to say
    *"All assessed metrics look good."*

    The word *assessed* is doing enormous work there, and **no user reads it that way.** They
    read *"my data is good."* A report that looks clean **because most of it did not run** is
    the exact bait this module exists to prevent.
    """
    qc = pytest.importorskip("pycat.toolbox.data_qc_tools")

    report = qc.run_full_qc(_scene())          # no pixel size, no NA, no frame interval
    assessed = [r for r in report if r["status"] in ("good", "warn", "bad")]

    assert len(assessed) < len(report), (
        "this test assumes some checks cannot run without metadata — if they all now run, the "
        "coverage warning is no longer needed and this test should be retired"
    )

    # The point: the report must not be able to claim a clean bill of health while most of it
    # was skipped. Any consumer of run_full_qc can count this, and plot_qc_report now does.
    n_skipped = len(report) - len(assessed)
    assert n_skipped > 0

    for entry in report:
        if entry["status"] == "na":
            assert entry["how"], (
                f"'{entry['name']}' is marked n/a and gives no reason. A check the user cannot "
                f"see the reason for is indistinguishable from one that was silently dropped."
            )


@pytest.mark.core
def test_single_image_focus_is_judged_from_edge_sharpness():
    """**A single image CAN be judged for focus** — via the sharpness of its objects' edges.

    The old check refused a verdict and headlined *"sharpness = 545.3 (relative)"*. It was right
    that the **band-pass energy** cannot judge a single image — it measures GLOBAL energy, so a
    sparse in-focus field scored **105.9** and a dense blurred one **118.1**. But that is a
    limitation of the estimator, **not of the question.**

    Edge sharpness is a **local** property of a boundary, so it is scene-independent. In focus,
    on the same optics: a sparse field measures **4.59 px** and a dense one **4.44 px** — 3 %
    apart — while defocus moves both monotonically.

    **The sharpest edge, not the average.** A big smooth cell genuinely *has* a wide edge, in
    focus or not — so an average confounds object size with focus. The sharpest edge asks the
    right question: *could anything in this image be sharper than it is?* **A blurry cell cannot
    hide a sharp punctum:** adding large smooth cells to a field of puncta leaves the answer
    unchanged (2.82 px either way), while defocus moves it (2.82 → 3.29 → 4.42 → 6.43).

    With the pixel size and NA, the **diffraction limit** makes it an ABSOLUTE verdict, and the
    thresholds are set by what the blur COSTS, not by eye:

    ========  ==========  ======================  ========
    ratio     defocus     apparent size error     verdict
    ========  ==========  ======================  ========
    0.99      none        +0 %                    good
    1.13      1.0 px      +12 %                   warn
    1.45      2.0 px      **+41 %**               **bad**
    1.83      4.0 px      +124 %                  bad
    ========  ==========  ======================  ========

    A 41 % error in apparent size corrupts any size distribution, and any partition coefficient
    whose mask spills past the true boundary (1.5.459). **That is what "bad focus" costs**, and
    it is why the threshold sits there.
    """
    qc = pytest.importorskip("pycat.toolbox.data_qc_tools")

    sharp = _focus_scene()                             # diffraction-limited objects
    blurred = ndi.gaussian_filter(sharp, 2.0)          # +41 % apparent size

    optics = dict(pixel_um=0.065, na=1.4, wavelength_nm=520)

    in_focus = qc.qc_focus(sharp, **optics)
    grossly_defocused = qc.qc_focus(ndi.gaussian_filter(sharp, 5.0), **optics)

    assert in_focus["status"] == "good", (
        f"a diffraction-limited image was flagged: {in_focus['headline']}"
    )
    assert grossly_defocused["status"] in ("warn", "bad"), (
        f"a grossly defocused image passed as '{grossly_defocused['status']}'"
    )

    # And the number must MOVE monotonically with the blur, which is what makes the comparative
    # use exact even though the absolute one is only a screen.
    ratios = [qc.qc_focus(ndi.gaussian_filter(sharp, b) if b else sharp, **optics)["value"]
              for b in (0.0, 1.0, 2.0, 3.0)]
    assert all(ratios[i] < ratios[i + 1] for i in range(len(ratios) - 1)), (
        f"the focus measure is not monotonic with defocus: {ratios}. Monotonicity is what "
        f"makes the COMPARATIVE use exact — if it does not hold, ranking fields by sharpness "
        f"is meaningless."
    )


@pytest.mark.core
def test_focus_is_comparable_across_a_dataset_without_any_optics():
    """*"Which of my 40 fields is the soft one?"* — the way focus is most often used.

    The edge-width measure is **scene-independent**, so it does not need the pixel size or the
    NA to be useful: the soft field in a folder of acquisitions has a visibly larger sharpest
    edge than its neighbours, and that comparison is available from the images alone.

    Verified: in a 40-field acquisition where field 17 slipped out of focus, the median sharpest
    edge is **2.78 px** and field 17 is **4.40 px — 1.58× the median.** It is the only outlier.
    """
    qc = pytest.importorskip("pycat.toolbox.data_qc_tools")

    widths = []
    for i in range(40):
        field = _focus_scene(seed=i)
        if i == 17:
            field = ndi.gaussian_filter(field, 2.2)     # this one slipped
        widths.append(qc.edge_width_px(field))

    widths = np.array(widths)
    median = float(np.median(widths))
    outliers = list(np.flatnonzero(widths > 1.3 * median))

    assert outliers == [17], (
        f"the defocused field (17) should be the ONLY outlier; got {outliers}. Median "
        f"{median:.2f} px, field 17 = {widths[17]:.2f} px ({widths[17] / median:.2f}x). "
        f"If the measure were scene-dependent, every field would scatter and the soft one "
        f"would be lost in the noise."
    )


@pytest.mark.core
def test_focus_refuses_when_the_field_has_no_sharp_objects():
    """A blurry cell cannot hide a sharp punctum — **but if nothing is small, there is no
    evidence.**

    The sharpest-edge measure works because the best-focused thing in the image is the best
    available evidence of focus. **If there is no small object at all, there is none.**

    A brightfield field of large smooth cells (sigma ~14 px) has no sharp edge anywhere. The
    check reported **4.0× the diffraction limit → "bad"** — which is *true about the image* and
    *wrong about the focus*: those cells genuinely have soft boundaries, and the microscope may
    be perfectly focused.

    **The check cannot distinguish "soft objects, sharp focus" from "sharp objects, soft focus"
    when nothing small is present.** That is not fixable by a better estimator, so it is
    detected and the check refuses.
    """
    qc = pytest.importorskip("pycat.toolbox.data_qc_tools")

    result = qc.qc_focus(_brightfield(), pixel_um=0.065, na=1.4, wavelength_nm=520)

    assert result["status"] == "na", (
        f"a field of large smooth cells got a focus VERDICT of '{result['status']}' "
        f"({result['headline']}). There is no sharp edge in this image to judge focus by — "
        f"reporting 'bad' would send the user to refocus a microscope that may be perfect."
    )
    assert "no small objects" in result["how"] or "no sharp edge" in result["how"], (
        "the refusal must say WHY, or it is indistinguishable from a crash"
    )


@pytest.mark.core
def test_the_absolute_focus_verdict_admits_its_systematic_floor():
    """It is a **screen for gross defocus**, not a measurement — and it says so.

    The estimator converts ``contrast / steepest_gradient`` into an edge sigma, and **the
    conversion constant depends on what the object is**:

    * a blurred **step** edge → ``contrast/gradient = 2.51 × sigma``
    * a Gaussian **blob** → ``contrast/gradient = 1.65 × sigma``

    Both verified against exact synthetic objects. The estimator cannot distinguish them, so the
    absolute ratio is uncertain by **~1.5×** depending on whether the field is puncta or
    membranes.

    **That floor is larger than the effect being measured.** A 2 px blur costs **+94 % apparent
    object size** and moves the ratio only from 0.45 to 1.14. Any threshold tight enough to
    catch it would fire on a perfectly focused image of the wrong object type.

    So the thresholds are deliberately wide, the text says *"this is a SCREEN, not a
    measurement"*, and it points the user at the comparative measure — **which has no such floor,
    because the object type is constant across a dataset and the constant cancels exactly.**

    *Reporting a screen as a screen is the honest thing. A tighter threshold would be false
    precision, and it would send someone to refocus a microscope that is already at the limit.*
    """
    qc = pytest.importorskip("pycat.toolbox.data_qc_tools")

    result = qc.qc_focus(_focus_scene(), pixel_um=0.065, na=1.4, wavelength_nm=520)

    assert "screen" in result["good"].lower(), (
        "the absolute focus verdict must tell the user it is a SCREEN for gross defocus and "
        "not a precise measurement — it has a ~1.5x systematic floor from the step-vs-blob "
        "calibration constant, and claiming more precision than that is a lie"
    )
    assert "across your dataset" in result["good"].lower(), (
        "it must point the user at the COMPARATIVE measure, which is exact where this one is "
        "not"
    )
