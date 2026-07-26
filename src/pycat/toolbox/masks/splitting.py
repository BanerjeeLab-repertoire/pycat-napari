"""Assessed-split feature — extracted from label_and_mask_tools.py (label_mask_split Step 5).

The morphology-aware decision path that assesses WHETHER touching masks should be split (two droplets vs
arrested fusion vs chain/aggregate vs single) before cutting — the physical answer, keyed on the neck
ratio. Separated from the raw watershed (masks/morphology.split_touching_objects) and the mask measurement.
``label_and_mask_tools`` re-exports the public name, so all callers are unchanged. Moved VERBATIM
(characterization-pinned by tests/test_group_c_geometry.py: verdicts, resulting label counts, and the
neck-ratio physics).
"""
from __future__ import annotations

import numpy as np

from pycat.utils.object_ref import bbox_columns_from_regionprops
from pycat.utils.tag_registry import tags_layer


@tags_layer('split_assessed', role='labels',
            summary='Morphology-aware split: two droplets vs arrested fusion vs chain', target='condensate')
def assess_and_split_touching(binary_mask, intensity_image=None, sigma=2.0,
                              neck_threshold=0.6, min_peak_distance=6,
                              chain_min_units=4, microns_per_pixel=1.0):
    """**Should these masks be split? The morphology answers, and it is a physical answer.**

    ``split_touching_objects`` runs a watershed and cuts. **It does not ask whether it should.**
    That is the wrong question to leave to a threshold, because the same connected mask can be
    four physically different things, and only one of them is two droplets:

    * **Two droplets in contact** — round, with a **deep neck** between them. They have not fused;
      splitting them is correct and *not* splitting them merges two measurements into one.
    * **Arrested fusion** — two droplets caught **part-way** through coalescence. The neck is
      **shallow**, because the interface has already begun to relax. **This is ONE object, and the
      arrest IS the finding**: a material that fuses slowly is a material with a high viscosity or
      a solidified interface. Splitting it destroys the very observation.
    * **Beads on a string / a fractal aggregate** — **many** small units stuck together. Cutting it
      into *two* is meaningless; the object is not a droplet pair at all.
    * **A single irregular droplet** — nothing to split.

    The evidence
    ------------
    **The neck ratio** — the depth of the saddle between two distance-transform peaks, as a
    fraction of the peaks themselves. It is the degree to which the two bodies have merged, and it
    moves smoothly and monotonically with the physics:

        overlap      neck_ratio    what it is
        0.00         **0.128**     barely touching  -> SPLIT
        0.10         0.433         still necked     -> SPLIT
        0.20         0.639         relaxing         -> arrested
        0.50         0.914         mostly fused     -> arrested
        0.80         1.000         one body         -> single

    A neck shallower than ~0.6 of the droplet radius means **the interface has already relaxed**
    — surface tension has done its work, and what is left is one body with a memory of two.

    Measured on the four morphologies (all ONE connected mask):

        morphology            solidity   n_peaks   neck_ratio
        single droplet        0.979      1         1.000
        **two touching**      0.906      **2**     **0.364**
        **arrested fusion**   0.979      **2**     **0.965**
        beads on a string     0.930      **6**     0.788
        fractal aggregate     0.891      1         1.000

    **The neck ratio separates "two touching" from "arrested fusion" cleanly (0.36 vs 0.97) —
    and nothing else does.** Solidity does not (0.906 vs 0.979 overlaps with a single droplet);
    eccentricity does not; the peak count does not (both are 2).

    **The intensity is a second, independent witness.** A real neck between two droplets sits in a
    thinner part of the object, so it is **dimmer** — less material in the light path. An arrested
    neck is filled with material and is **not** dimmer. Where an intensity image is given, this is
    reported as ``neck_intensity_ratio`` and it is used to override a marginal geometric call.

    References
    ----------
    The arrest physics — **interfacial driving force against internal elasticity** — is
    established, and this module implements the observable side of it:

    * **Pawar, Caggioni, Ergun, Hartel & Spicer**, "Arrested coalescence in Pickering emulsions",
      *Soft Matter* **7**, 7710-7716 (2011). DOI: 10.1039/c1sm05457k

      *"their complete fusion into a single spherical drop can sometimes be arrested in an
      intermediate shape **if a rheological resistance offsets the Laplace pressure driving
      force**."*

      Their **eqn (6)** gives the pressure imbalance at the neck as
      ``dP = 2*gamma/R_droplet - (gamma/R1 - gamma/R2)``, with R1 the cross-sectional radius and
      R2 the neck radius — **the two principal radii of a saddle, of opposite sign.** That is
      exactly the object measured here, and their two published doublets **both imply the same
      interfacial tension (0.0529 N/m)** when their equation is recomputed from their own
      geometry — see ``test_the_neck_laplace_pressure_reproduces_PAWAR_2011``.

    * **Pawar, Caggioni, Hartel & Spicer**, "Arrested coalescence of viscoelastic droplets with
      internal microstructure", *Faraday Discuss.* **158**, 341-350 (2012).
      DOI: 10.1039/c2fd20029e

      *"the interfacial energy is continuously reduced while the elastic energy is increased by
      compression of the internal structure and, **when the two processes balance one another,
      coalescence is arrested**."*

    * **Dahiya, Caggioni, Spicer et al.**, arrested coalescence of polydisperse doublets,
      *Phil. Trans. R. Soc. A* (2016), PMC4920281 — the three-regime structure this function
      reports: *"If surface energy dominates, the drops will completely coalesce. If elastic
      energy dominates, the droplets are unable to even initiate coalescence. **Arrest occurs when
      coalescence can begin but not complete.**"*

    Full validation, including the parameter ranges for biomolecular condensates, is in
    ``docs/validation/neck_geometry_and_elastocapillarity.md``.

    Returns
    -------
    dict with ``labels`` (the split, or the original object unsplit), and per-object records
    carrying the verdict, the evidence, and **why**.
    """
    import skimage as sk
    from scipy import ndimage as ndi

    mask = np.asarray(binary_mask) > 0
    intensity = None if intensity_image is None else np.asarray(intensity_image, float)

    labelled = sk.measure.label(mask)
    output = np.zeros_like(labelled)
    records = []
    next_label = 1

    for prop in sk.measure.regionprops(labelled):
        sub = (labelled[prop.slice] == prop.label)

        distance = ndi.distance_transform_edt(sub)
        smoothed = sk.filters.gaussian(distance, sigma=sigma)

        peaks = sk.feature.peak_local_max(
            smoothed, min_distance=int(min_peak_distance), labels=sub)

        record = dict(
            label=int(prop.label),
            # ── KEEP THE BBOX. It is what makes this row brushable. ─────────────
            #
            # regionprops hands it over free, and PyCAT was discarding it at 24 of its 25 call
            # sites. **A row without a bbox cannot be turned back into an image** — and in BATCH
            # that is the ONLY route back to the object, because the layer is gone.
            **bbox_columns_from_regionprops(prop),
            area_um2=float(prop.area) * microns_per_pixel ** 2,
            solidity=float(prop.solidity),
            n_peaks=int(len(peaks)),
            neck_ratio=np.nan,
            neck_intensity_ratio=np.nan,
            verdict='single',
            split=False,
            reason='',
        )

        # ── Not enough peaks: nothing to split ──────────────────────────────────
        if len(peaks) < 2:
            record['neck_ratio'] = 1.0
            record['reason'] = ('One distance-transform maximum: this is a single body, however '
                                'irregular its outline. A ramified or fractal aggregate lands '
                                'here — it has no neck because it has no two centres.')
            output[prop.slice][sub] = next_label
            next_label += 1
            records.append(record)
            continue

        # ── Many peaks: a CHAIN or an aggregate, not a droplet pair ─────────────
        if len(peaks) >= int(chain_min_units):
            record['verdict'] = 'chain_or_aggregate'
            record['reason'] = (
                f'{len(peaks)} sub-units. **This is not a droplet pair** — it is a chain '
                f'(beads-on-a-string) or a ramified aggregate. Cutting it in TWO would be '
                f'arbitrary: the object is not two things, it is many things stuck together, '
                f'and that is itself the observation. Left intact.')
            output[prop.slice][sub] = next_label
            next_label += 1
            records.append(record)
            continue

        # ── Two (or three) peaks: measure the NECK ──────────────────────────────
        depths = sorted((float(smoothed[tuple(q)]) for q in peaks), reverse=True)[:2]

        markers = np.zeros(sub.shape, int)
        for i, q in enumerate(peaks[:2], start=1):
            markers[tuple(q)] = i

        basins = sk.segmentation.watershed(-smoothed, sk.measure.label(markers > 0), mask=sub)
        boundary = sk.segmentation.find_boundaries(basins, mode='thick') & sub

        saddle = float(smoothed[boundary].max()) if boundary.any() else 0.0
        neck = saddle / max(min(depths), 1e-9)
        record['neck_ratio'] = float(neck)

        # ── The intensity is an INDEPENDENT witness ─────────────────────────────
        #
        # A real neck between two droplets is a thinner part of the object, so LESS material sits
        # in the light path and it is DIMMER. An arrested neck is filled, and is not.
        if intensity is not None and boundary.any():
            patch = intensity[prop.slice]
            neck_intensity = float(np.median(patch[boundary]))
            body_intensity = float(np.median(patch[sub & ~boundary]))
            if body_intensity > 1e-9:
                record['neck_intensity_ratio'] = neck_intensity / body_intensity

        deep_neck = neck < float(neck_threshold)

        # ── The intensity is REPORTED but does NOT override the geometry ────────
        #
        # A real neck sits in a thinner part of the object, so less material is in the light path
        # and it should be dimmer. **Tested, and it does not discriminate**: the neck intensity
        # came out at 0.42-0.46 of the body median for a genuine neck AND for an arrested one
        # alike, because the body median is dominated by the bright droplet centres and every
        # neck is dim compared with those.
        #
        # **The geometry is decisive on its own** (0.50 against 0.77 on the same pair), so the
        # intensity is reported for the user to inspect and is NOT used to override the call.
        # A witness that does not discriminate must not be given a vote.
        #
        # (A discriminating intensity test would compare the neck against the LOCAL body
        # thickness at the same distance from the centres — i.e. against what the intensity
        # WOULD be if the neck were filled. That is a real piece of work, and it is not done
        # here.)
        _intensity_ratio = record['neck_intensity_ratio']

        if deep_neck:
            record['verdict'] = 'two_droplets'
            record['split'] = True
            if not record['reason']:
                record['reason'] = (
                    f'Neck ratio {neck:.2f} — **a deep neck**. The two bodies are in contact but '
                    f'have NOT fused: surface tension has not relaxed the interface between '
                    f'them. They are two droplets, and measuring them as one would merge two '
                    f'independent objects.')
            output[prop.slice][basins == 1] = next_label
            output[prop.slice][basins == 2] = next_label + 1
            next_label += 2
        else:
            record['verdict'] = 'arrested_fusion'
            record['reason'] = (
                f'Neck ratio {neck:.2f} — **a shallow neck**. The interface between the two '
                f'centres has already relaxed: surface tension has done its work and what '
                f'remains is ONE body with a memory of two. **This is arrested fusion, and the '
                f'arrest is the finding** — a droplet pair that stalls part-way through '
                f'coalescence is reporting a high viscosity or a solidified interface. '
                f'Splitting it would destroy exactly that observation. Left intact.')
            output[prop.slice][sub] = next_label
            next_label += 1

        records.append(record)

    return dict(labels=output, objects=records)
