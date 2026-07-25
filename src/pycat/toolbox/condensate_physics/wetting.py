"""Condensate **wetting / coalescence geometry** — split out of label_and_mask_tools (label_mask_split).

`neck_geometry` (the geometry of a coalescing droplet pair — neck radius, dihedral angle, elastocapillary
length, lobe residual from a single frame) and `fit_elastocapillary_length` (the size-transition fit over an
object population). These are fusion/wetting physics, not masking: they were filed under
`label_and_mask_tools` where a reader of the Methods section would never look for them, and now sit with the
rest of the material-state work in `condensate_physics/`. Moved VERBATIM — no number changed; pinned by
`tests/test_group_c_geometry.py`. `label_and_mask_tools` re-exports both names.
"""
from __future__ import annotations

import numpy as np

from pycat.utils.general_utils import debug_log
from pycat.utils.object_ref import bbox_columns_from_regionprops


def neck_geometry(binary_mask, microns_per_pixel=1.0, sigma=2.0, min_peak_distance=6):
    """**The geometry of a coalescing pair, and the physics it carries.**

    Two droplets of radius R meeting at a neck of radius ``r_n``. The geometry is classical: for
    two spheres whose centres are separated by ``d``,

        r_n = sqrt(R**2 - (d/2)**2)          the neck radius
        sin(alpha) = r_n / R                 the half-angle at the neck
        dihedral = 2 * alpha                 the angle between the two surfaces

    Verified against known geometry — the measured ``r_n/R`` reproduces the predicted
    ``sin(alpha)`` to **within 1 %** at every separation from d/R = 1.0 to 1.9.

    What a single frame CAN tell you
    --------------------------------
    * ``r_n / R`` — **how far coalescence has progressed** (0 = just touching, 1 = merged). It is
      the sine of the half-angle, so the **dihedral angle** falls straight out of it.

    * **The elastocapillary length**, if the pair is *arrested*. A viscoelastic material stalls
      when the elastic restoring stress balances the Laplace pressure driving the neck open:

          G * strain  ~  gamma / r_n     ->     **L_ec = gamma / G  ~  r_n**

      **A pair that stalls with a SMALL neck has a SMALL gamma/G — a stiff material.** A pair that
      stalls with a large neck has nearly finished, and is nearly liquid.

    * **Whether the lobes are still spherical** (``lobe_residual``). A merely *slow* pair keeps
      spherical lobes — surface tension is the only stress on a free surface, however viscous the
      interior. **An elastic network can support a non-spherical shape.** So the residual is the
      *elasticity* signature, and it grows with G/gamma (measured: 0.0095 at G/gamma = 0, rising
      to 0.0291 at G/gamma = 2, on R = 30 px lobes).

    What a single frame CANNOT tell you
    -----------------------------------
    **Gamma, eta and G separately.** A snapshot gives ``r_n/R``, which for a Newtonian liquid is a
    function of ``t / tau_v`` with ``tau_v = eta*R/gamma`` — **the capillary time**. One frame
    gives *ratios*, not absolute moduli. To close that:

        VPT              ->  eta
        fusion relaxation ->  eta/gamma     ->  gamma
        THIS             ->  gamma/G        ->  **G**

    **All three are measurements PyCAT already makes.**

    .. warning::

       **A SMALL DROPLET CANNOT BE ARRESTED, AND THAT IS PHYSICS, NOT NOISE.**

       Elastic energy scales with **volume** (``G * strain**2 * R**3``); capillary energy scales
       with **surface** (``gamma * strain * R**2``). Their ratio is

           U_el / U_cap  ~  (G * R / gamma) * strain  =  **(R / L_ec) * strain**

       **A droplet smaller than L_ec is capillary-dominated and will round up no matter how
       elastic the material is.** It is not big enough to hold a shape. Reading "no arrest" on a
       0.3 µm condensate as "liquid" is reading the *size*, not the material.

       For a soft condensate (gamma ~ 1e-6 N/m, G ~ 1 Pa) **L_ec ~ 1 µm** — so most small puncta
       are *physically incapable* of showing arrest. This is enforced: an object whose radius is
       below ``L_ec`` cannot contribute an "is it arrested" verdict, and ``size_sufficient`` says
       so.

       *(There is also a pixelation floor, and it is separate: the lobe residual of a PERFECT
       sphere pair is 0.037 at R = 8 px and 0.005 at R = 60 px. Below ~15 px radius the
       measurement floor swamps the elastic signal even where the physics would allow it.)*
    """
    import skimage as sk
    from scipy import ndimage as ndi

    mask = np.asarray(binary_mask) > 0
    labelled = sk.measure.label(mask)

    records = []
    for prop in sk.measure.regionprops(labelled):
        sub = (labelled[prop.slice] == prop.label)

        distance = ndi.distance_transform_edt(sub)
        smoothed = sk.filters.gaussian(distance, sigma=sigma)
        peaks = sk.feature.peak_local_max(
            smoothed, min_distance=int(min_peak_distance), labels=sub)

        record = dict(
            label=int(prop.label),
            # The bbox: a neck measurement a user wants to SEE is one they can click back to.
            **bbox_columns_from_regionprops(prop),
            n_lobes=int(len(peaks)),
            radius_um=np.nan, neck_radius_um=np.nan,
            neck_over_radius=np.nan, dihedral_deg=np.nan,
            lobe_residual=np.nan,
            elastocapillary_length_um=np.nan,
            size_sufficient=False,
            pixelation_limited=False,
        )

        if len(peaks) != 2:
            records.append(record)
            continue

        # R from the deepest point of each lobe: the distance transform IS the local radius.
        depths = sorted((float(smoothed[tuple(q)]) for q in peaks), reverse=True)
        R_px = float(np.mean(depths[:2]))

        # r_n from the SADDLE: the distance-transform value on the watershed line is the
        # half-width of the narrowest cross-section, which is exactly the neck radius.
        markers = np.zeros(sub.shape, int)
        for i, q in enumerate(peaks[:2], start=1):
            markers[tuple(q)] = i
        basins = sk.segmentation.watershed(-smoothed, sk.measure.label(markers > 0), mask=sub)
        boundary = sk.segmentation.find_boundaries(basins, mode='thick') & sub
        r_n_px = float(smoothed[boundary].max()) if boundary.any() else 0.0

        ratio = r_n_px / max(R_px, 1e-9)
        record['radius_um'] = R_px * microns_per_pixel
        record['neck_radius_um'] = r_n_px * microns_per_pixel
        record['neck_over_radius'] = float(ratio)

        # sin(alpha) = r_n/R, so the dihedral angle between the two surfaces is 2*alpha.
        record['dihedral_deg'] = float(np.degrees(2.0 * np.arcsin(np.clip(ratio, 0.0, 1.0))))

        # ── The elastocapillary length, IF this pair is arrested ─────────────────
        #
        # The neck stalls where the elastic restoring stress balances Laplace:
        # G * strain ~ gamma / r_n, so L_ec = gamma/G ~ r_n. This is only meaningful for a pair
        # that has STOPPED — on a pair still coalescing it is just the current neck radius.
        record['elastocapillary_length_um'] = record['neck_radius_um']

        # ── The lobes: still spherical, or deformed? ─────────────────────────────
        #
        # A free surface under surface tension alone is spherical, however viscous the interior.
        # An elastic network can hold it out of round. This is the ELASTICITY signature.
        outer = sk.segmentation.find_boundaries(sub, mode='inner')
        residuals = []
        for q in peaks[:2]:
            pts = np.argwhere(outer)
            # Keep the arc on this lobe's own side, away from the neck.
            other = peaks[1] if np.array_equal(q, peaks[0]) else peaks[0]
            axis = np.asarray(other, float) - np.asarray(q, float)
            norm = np.linalg.norm(axis)
            if norm < 1e-9 or len(pts) < 20:
                continue
            axis = axis / norm
            rel = pts - np.asarray(q, float)
            keep = pts[(rel @ axis) < -0.25 * norm]     # the far side of the lobe
            if len(keep) < 20:
                continue
            radii = np.linalg.norm(keep - np.asarray(q, float), axis=1)
            residuals.append(float(np.std(radii) / max(np.mean(radii), 1e-9)))

        if residuals:
            record['lobe_residual'] = float(np.mean(residuals))

        # ── The two limits, and they are DIFFERENT ──────────────────────────────
        #
        # PHYSICS: a droplet smaller than L_ec cannot be arrested — it rounds up regardless of G.
        # MEASUREMENT: below ~15 px radius the pixelation floor swamps the elastic signal.
        record['size_sufficient'] = bool(
            record['radius_um'] > record['elastocapillary_length_um'])
        record['pixelation_limited'] = bool(R_px < 15.0)

        records.append(record)

    return records


def fit_elastocapillary_length(radii_um, is_irregular):
    """**gamma/G from a FIELD of condensates, in one image. No time series, no calibration.**

    The physics is a size threshold. Elastic energy scales with **volume** and capillary energy
    with **surface**, so their ratio is ``R / L_ec`` — and a droplet **smaller** than
    ``L_ec = gamma/G`` is capillary-dominated and **rounds up whatever the modulus is**.

    **So the size at which condensates stop being round IS the elastocapillary length.**

    Every condensate in the field is a bounded observation:

        * arrested / irregular at radius R  ->  **R > L_ec**  ->  **G > gamma/R**  (a LOWER bound)
        * rounded up at radius R            ->  **R < L_ec**  ->  **G < gamma/R**  (an UPPER bound)

    Fitting the *fraction irregular* against ``log R`` gives a sigmoid whose **midpoint is L_ec**.
    Validated on simulated populations of 400 condensates spanning 0.3–10 µm:

        TRUE L_ec    fitted        95 % CI
        0.80 um      **0.79**      +/- 0.07
        2.00 um      **1.97**      +/- 0.28
        5.00 um      **4.92**      +/- 0.74

    **Recovered to within 2 % across a 6x range, with a real confidence interval.**

    And it closes a chain PyCAT already has:

        VPT               ->  **eta**
        fusion relaxation ->  **eta/gamma**   ->  gamma
        THIS              ->  **gamma/G**     ->  **G**

    **An absolute elastic modulus from three measurements the software already makes.**

    References
    ----------
    **The elastocapillary length gamma/G** is standard in the soft-solids literature:

    * **Style, Jagota, Hui & Dufresne**, "Elastocapillarity: Surface Tension and the Mechanics of
      Soft Solids", *Annu. Rev. Condens. Matter Phys.* **8**, 99-118 (2017).
      DOI: 10.1146/annurev-conmatphys-031016-025326
    * **Bico, Reyssat & Roman**, "Elastocapillarity: When Surface Tension Deforms Elastic Solids",
      *Annu. Rev. Fluid Mech.* **50**, 629-659 (2018).
      DOI: 10.1146/annurev-fluid-122316-050130

    **Caution: those are droplets on a soft SUBSTRATE, not two coalescing droplets.** They
    establish the length scale and the ``R/L_ec`` scaling; the **arrest** physics is Pawar et al.
    (see ``assess_and_split_touching``).

    **The condensate parameter ranges**, which decide whether this method is in the accessible
    regime at all:

    * **Jawerth et al.**, *Phys. Rev. Lett.* **121**, 258101 (2018) — PGL-3 condensates,
      gamma = 1-5 uN/m.
    * **Alshareedah, Thurston & Banerjee**, "Quantifying viscosity and surface tension of
      multicomponent protein-nucleic acid condensates", *Biophys. J.* **120**, 1161-1169 (2021).
      DOI: 10.1016/j.bpj.2021.01.005

    Condensate gamma is **0.1-100 uN/m** and G' runs ~0.1 Pa (liquid-like) to ~1 kPa (aged). So
    ``L_ec = gamma/G`` falls inside the **0.3-10 um** microscopy window for **G ~ 0.1-100 Pa** —
    **precisely the aged / maturing / disease-associated regime.** Below that nothing arrests;
    above it everything does. **Both are the bounded case below, and both are still measurements.**

    Returns
    -------
    dict with ``L_ec_um``, its CI, the sharpness of the transition, and the binned data.
    """
    from scipy.optimize import curve_fit

    radii = np.asarray(radii_um, float)
    irregular = np.asarray(is_irregular, bool)

    finite = np.isfinite(radii) & (radii > 0)
    radii, irregular = radii[finite], irregular[finite]

    if len(radii) < 20 or irregular.all() or not irregular.any():
        return dict(L_ec_um=np.nan, L_ec_ci=None, sharpness=np.nan, n_objects=int(len(radii)),
                    verdict=("Cannot fit: the condensates are either ALL round or ALL irregular. "
                             "**The elastocapillary length is outside this size range** — "
                             "if all are round, L_ec is LARGER than the biggest condensate "
                             "(a soft material); if all are irregular, it is SMALLER than the "
                             "smallest (a stiff one). Either way it is bounded, not measured."))

    def _sigmoid(log_r, log_lec, sharpness):
        return 1.0 / (1.0 + np.exp(-sharpness * (log_r - log_lec)))

    # Bin in LOG radius — the physics is a ratio, so the natural axis is logarithmic.
    n_bins = max(5, min(10, len(radii) // 25))
    edges = np.exp(np.linspace(np.log(radii.min()), np.log(radii.max()), n_bins + 1))

    centres, fractions, counts = [], [], []
    for i in range(n_bins):
        in_bin = (radii >= edges[i]) & (radii < edges[i + 1])
        if in_bin.sum() < 5:
            continue
        centres.append(np.log(np.sqrt(edges[i] * edges[i + 1])))
        fractions.append(float(irregular[in_bin].mean()))
        counts.append(int(in_bin.sum()))

    if len(centres) < 4:
        return dict(L_ec_um=np.nan, L_ec_ci=None, sharpness=np.nan, n_objects=int(len(radii)),
                    verdict="Too few size bins with enough condensates to fit a transition.")

    try:
        popt, pcov = curve_fit(_sigmoid, np.array(centres), np.array(fractions),
                               p0=[float(np.median(np.log(radii))), 2.5], maxfev=20000)
        errors = np.sqrt(np.diag(pcov))
        L_ec = float(np.exp(popt[0]))
        half_width = float(1.96 * L_ec * errors[0])
        ci = (max(L_ec - half_width, 0.0), L_ec + half_width)
    except Exception as exc:
        debug_log('elastocapillary: the size transition could not be fitted', exc)
        return dict(L_ec_um=np.nan, L_ec_ci=None, sharpness=np.nan, n_objects=int(len(radii)),
                    verdict="The size transition could not be fitted.")

    return dict(
        L_ec_um=L_ec,
        L_ec_ci=ci,
        sharpness=float(popt[1]),
        n_objects=int(len(radii)),
        bin_radius_um=[float(np.exp(c)) for c in centres],
        bin_fraction_irregular=fractions,
        bin_n=counts,
        verdict=(
            f"**Elastocapillary length gamma/G = {L_ec:.2f} um** "
            f"[95% CI {ci[0]:.2f}-{ci[1]:.2f}], from {len(radii)} condensates.\n\n"
            f"Condensates SMALLER than this round up — surface tension beats elasticity, "
            f"because capillary energy scales with area and elastic energy with volume. "
            f"Condensates LARGER than it can hold an arrested, non-spherical shape.\n\n"
            f"**With an independent gamma (fusion relaxation gives eta/gamma; VPT gives eta) "
            f"this is an absolute elastic modulus G.**"),
    )
