# Validation: neck geometry, arrested coalescence, and the elastocapillary length

**Module:** `pycat.toolbox.label_and_mask_tools`
**Functions:** `assess_and_split_touching`, `neck_geometry`, `fit_elastocapillary_length`
**Tests:** `tests/test_group_c_geometry.py`
**Status:** validated against analytic geometry, against published experimental data, and
against literature parameter ranges for biomolecular condensates.

---

## 1. The question

Two condensates form **one connected mask**. Should it be split?

`split_touching_objects` runs a watershed and cuts. **It never asks whether it should.** The same
mask can be four physically different things, and only one of them is two droplets:

| morphology | what it is | correct action |
|---|---|---|
| two droplets in contact | a **deep neck** — they have not fused | **split** |
| **arrested coalescence** | caught part-way through fusion; the neck is **shallow** | **do not split — the arrest is the finding** |
| beads-on-a-string / fractal aggregate | **many** units, not two | do not cut in two |
| a single irregular droplet | nothing to split | leave alone |

---

## 2. The physics of arrest

> *"When two emulsion drops begin to coalesce, their complete fusion into a single spherical drop
> can sometimes be arrested in an intermediate shape **if a rheological resistance offsets the
> Laplace pressure driving force**."*
> — **Pawar, Caggioni, Ergun, Hartel & Spicer (2011)**, *Soft Matter* **7**, 7710–7716.
> DOI: [10.1039/c1sm05457k](https://doi.org/10.1039/c1sm05457k)

> *"During coalescence of structured droplets the interfacial energy is continuously reduced while
> the elastic energy is increased by compression of the internal structure and, **when the two
> processes balance one another, coalescence is arrested**."*
> — **Pawar, Caggioni, Hartel & Spicer (2012)**, *Faraday Discussions* **158**, 341–350.
> DOI: [10.1039/c2fd20029e](https://doi.org/10.1039/c2fd20029e)

And the three-regime structure, which is exactly what this module reports:

> *"**If surface energy dominates, the drops will completely coalesce** into a sphere. **If elastic
> energy dominates, the droplets are unable to even initiate coalescence.** **Arrest occurs when
> coalescence can begin but not complete** because surface and elastic contributions balance one
> another at an intermediate state."*
> — **Dahiya, Caggioni, Spicer et al.**, *Phil. Trans. R. Soc. A* (2016), arrested coalescence of
> polydisperse doublets. [PMC4920281](https://pmc.ncbi.nlm.nih.gov/articles/PMC4920281/)

---

## 3. VALIDATION 1 — the Laplace pressure at the neck, against Pawar's published data

Pawar et al. (2011) give the pressure imbalance in an arrested doublet as their **eqn (6)**:

```
ΔP = 2γ/R_droplet − (γ/R₁ − γ/R₂)
```

where **R₁ is the cross-sectional radius and R₂ the neck radius** — *"the two principal radii
characterizing the curvature of the neck"*. **The two have opposite sign: the neck is a saddle.**

They publish two arrested doublets with full geometry and the ΔP they computed. Recomputing their
equation from their own geometry:

| case | R_droplet | R₁ | R₂ | **their ΔP** | implied γ |
|---|---|---|---|---|---|
| Fig 5(b.3) | 100 µm | 48 µm | 73 µm | **6.81 × 10² Pa** | **0.0529 N/m** |
| Fig 5(c.3) | 94 µm | 94 µm | ∞ | **5.63 × 10² Pa** | **0.0529 N/m** |

**Two independent geometries give the identical implied interfacial tension.** The structure of
the equation is confirmed exactly, and the saddle that `neck_geometry` measures **is the same
object** as their R₁ and R₂.

*(The implied 0.0529 N/m is slightly above the 0.042 N/m they quote for the bare hexadecane/water
interface — expected, since their interface is laden with silica.)*

---

## 4. VALIDATION 2 — the neck geometry against exact analytic truth

For two spheres of radius R with centres separated by d:

```
r_n = √(R² − (d/2)²)          the neck radius
sin(α) = r_n / R              the half-angle
dihedral = 2α                 the angle between the two surfaces
```

Measured from synthetic masks of known geometry:

| d/R | true r_n/R | **measured** | true dihedral | **measured** |
|---|---|---|---|---|
| 1.8 | 0.436 | **0.458** | 51.7° | **54.5°** |
| 1.5 | 0.661 | **0.671** | 82.8° | **84.3°** |
| 1.2 | 0.800 | **0.815** | 106.3° | **109.1°** |

**Within a few percent on the ratio, and within 3° on the dihedral angle.**

---

## 5. VALIDATION 3 — the discriminator

Four morphologies, all producing **one connected mask**:

| morphology | solidity | n_peaks | **neck_ratio** |
|---|---|---|---|
| single droplet | 0.979 | 1 | 1.000 |
| **two touching** | 0.906 | **2** | **0.364** |
| **arrested fusion** | 0.979 | **2** | **0.965** |
| beads on a string | 0.930 | **6** | 0.788 |
| fractal aggregate | 0.891 | 1 | 1.000 |

**Solidity cannot separate them** — arrested fusion (0.979) is identical to a single droplet.
**The peak count cannot** — both are 2. **Only the depth of the neck can**, and it moves smoothly
and monotonically with the degree of fusion:

| overlap | 0.00 | 0.10 | 0.20 | 0.50 | 0.80 |
|---|---|---|---|---|---|
| **neck_ratio** | **0.128** | 0.433 | 0.639 | 0.914 | **1.000** |

A neck shallower than ~0.6 of the droplet radius means **surface tension has already relaxed the
interface**. That is a physical statement, not a tuned threshold.

---

## 6. VALIDATION 4 — the elastocapillary length

### The scaling

Elastic energy scales with **volume**; capillary energy with **surface**:

```
U_el  ~ G · ε² · R³
U_cap ~ γ · ε  · R²

U_el / U_cap ~ (G·R/γ) · ε = (R / L_ec) · ε     where   L_ec = γ/G
```

**A droplet smaller than L_ec is capillary-dominated and rounds up whatever the modulus is.**
This is Gable's objection — *small objects are essentially all surface* — and **it is not a
limitation, it is the measurement.**

The elastocapillary length γ/G is standard in the wetting-on-soft-solids literature:

- **Style & Dufresne**, *Soft Matter* **8**, 726 (2012) — static wetting on compliant substrates.
- **Style, Jagota, Hui & Dufresne**, *Annu. Rev. Condens. Matter Phys.* **8**, 99–118 (2017),
  "Elastocapillarity: Surface Tension and the Mechanics of Soft Solids".
  DOI: [10.1146/annurev-conmatphys-031016-025326](https://doi.org/10.1146/annurev-conmatphys-031016-025326)
- **Bico, Reyssat & Roman**, *Annu. Rev. Fluid Mech.* **50**, 629–659 (2018), "Elastocapillarity:
  When Surface Tension Deforms Elastic Solids".
  DOI: [10.1146/annurev-fluid-122316-050130](https://doi.org/10.1146/annurev-fluid-122316-050130)
- **Weijs, Andreotti & Snoeijer**, *Soft Matter* **9**, 8494 (2013) — the drop-size transition at
  γ/ER, which is the same dimensionless group used here.

**⚠ The substrate-wetting geometry is NOT the same as two coalescing droplets.** These references
establish the *length scale and the scaling*; the *arrest* physics comes from Pawar et al.

### The population measurement

**The size at which condensates stop being round IS L_ec.** Every condensate is a bounded
observation:

- arrested at radius R → **R > L_ec** → **G > γ/R**  *(a lower bound on the modulus)*
- rounded up at radius R → **R < L_ec** → **G < γ/R**  *(an upper bound)*

Fitting the fraction irregular against log R gives a sigmoid whose **midpoint is L_ec**.

**Validated on simulated populations of 400 condensates spanning 0.3–10 µm:**

| TRUE L_ec | **fitted** | 95 % CI | error |
|---|---|---|---|
| 0.80 µm | **0.79** | ± 0.07 | −1 % |
| 2.00 µm | **1.97** | ± 0.28 | −2 % |
| 5.00 µm | **4.92** | ± 0.74 | −2 % |

**Recovered to within 2 % across a 6× range, with a real confidence interval, from a single
field.**

---

## 7. VALIDATION 5 — is the accessible window the right one?

**Condensate interfacial tension, from the literature: γ ≈ 0.1–100 µN/m** (i.e. 1e-7 to 1e-4 N/m).

- **Jawerth et al.**, *Phys. Rev. Lett.* **121**, 258101 (2018) — PGL-3 condensates, γ = 1–5 µN/m,
  decreasing with salt per Overbeek–Voorn.
- **Alshareedah, Thurston & Banerjee**, *Biophys. J.* **120**, 1161–1169 (2021) — "Quantifying
  viscosity and surface tension of multicomponent protein–nucleic acid condensates".
  DOI: [10.1016/j.bpj.2021.01.005](https://doi.org/10.1016/j.bpj.2021.01.005)
- **Wang, Choi, Holehouse, ... Brangwynne, Pappu** and the condensate-rheology reviews for the
  0.1–100 µN/m range.

**Condensate elastic modulus: G′ ≈ 0.1 Pa (liquid-like) to ~1 kPa (aged / solid-like).**

`L_ec = γ/G`, in microns:

| γ ↓ / G → | 0.1 Pa | 1 Pa | 10 Pa | 100 Pa | 1 kPa |
|---|---|---|---|---|---|
| 0.1 µN/m | 1.0 | 0.1 | 0.01 | 0.001 | — |
| **1 µN/m** (PGL-3) | 10.0 | **1.0** | 0.1 | 0.01 | — |
| **10 µN/m** | 100 | **10.0** | **1.0** | 0.1 | 0.01 |
| **100 µN/m** | — | 100 | **10.0** | **1.0** | 0.1 |

**The light-microscopy window is ~0.3–10 µm** (diffraction limit to a large condensate). **L_ec
falls inside it for G ≈ 0.1–100 Pa — which is precisely the aged / maturing / disease-associated
regime.**

### The two ways it fails, and both are informative

- **A truly liquid condensate (G → 0):** L_ec → ∞. Nothing arrests. Correctly reported as *"all
  round → L_ec is **bounded below** by the largest condensate"* — **a soft material.**
- **A hard solid (G ~ 1 kPa):** L_ec ≈ 1e-4 to 0.1 µm, **below the diffraction limit**. Everything
  is arrested; nothing rounds up. Correctly reported as *"all irregular → L_ec is **bounded above**
  by the smallest"* — **a stiff material.**

**Both land in the bounded case the code already handles, and both are still measurements.**

---

## 8. The chain this closes

| measurement | PyCAT module | gives |
|---|---|---|
| **VPT** | `vpt_tools` (Stokes–Einstein) | **η** |
| **fusion relaxation** | `fusion_tools` (inverse capillary velocity) | **η/γ** → **γ** |
| **this** | `label_and_mask_tools` | **γ/G** → **G** |

**An absolute elastic modulus from three measurements PyCAT already makes.**

The fusion-relaxation leg is itself standard:

> *"Fusion-relaxation time is a measure of the inverse capillary velocity (**the ratio of
> viscosity/surface tension**)."*
> — **Alshareedah, Kaur & Banerjee**, *Methods in Enzymology* **646**, 143–183 (2021),
> "Methods for characterizing the material properties of biomolecular condensates".
> DOI: [10.1016/bs.mie.2020.06.009](https://doi.org/10.1016/bs.mie.2020.06.009)

with the τ = ηl/γ relation from **Brangwynne, Mitchison & Hyman**, *PNAS* **108**, 4334–4339
(2011). DOI: [10.1073/pnas.1017150108](https://doi.org/10.1073/pnas.1017150108)

---

## 9. What a SINGLE FRAME cannot give — stated plainly

**γ, η and G separately.** A snapshot gives `r_n/R`, which for a Newtonian liquid is a function of
`t/τ_v` with `τ_v = ηR/γ` — the **capillary time** (Frenkel; Eggers, Lister & Stone, *J. Fluid
Mech.* **401**, 293 (1999)). **One frame gives ratios, not absolute moduli.**

---

## 10. Two limits, and they are DIFFERENT

| limit | what it is | reported as |
|---|---|---|
| **PHYSICS** | A droplet below L_ec **cannot** be arrested — it rounds up regardless of G. Reading *"no arrest"* on a 0.3 µm punctum as *"liquid"* is **reading the size, not the material.** | `size_sufficient` |
| **MEASUREMENT** | The lobe residual of a **perfect** sphere pair is **0.037 at R = 8 px** against **0.005 at R = 60 px**. Below ~15 px radius the pixelation floor swamps the elastic signal *even where the physics allows it*. | `pixelation_limited` |

---

## 11. The lobe residual reads ELASTICITY, not viscosity

**A merely *slow* pair keeps spherical lobes** — surface tension is the only stress on a free
surface, **however viscous the interior**. **An elastic network can hold it out of round.**

Measured, on R = 30 px lobes:

| G/γ | 0 | 0.5 | 1.0 | 2.0 |
|---|---|---|---|---|
| **lobe residual** | **0.0095** | 0.0131 | 0.0181 | **0.0291** |

Monotonic. **This is why the residual is reported: it is the elasticity signature, and it is
independent of the neck.**

This is the same distinction Dekker et al. draw in the coalescence dynamics:

- **Dekker, Hack, Tewes, Datt, Bouillant & Snoeijer**, "When Elasticity Affects Drop Coalescence",
  *Phys. Rev. Lett.* **128**, 028004 (2022).
  DOI: [10.1103/PhysRevLett.128.028004](https://doi.org/10.1103/PhysRevLett.128.028004)
- **Varma, Rajput & Kumar**, "Rheocoalescence: Relaxation time through coalescence of droplets",
  *Macromolecules* **55**, 6031–6039 (2022).

---

## 12. Honest limitations

1. **Validated on synthetic geometry and against published experimental *numbers*, not against a
   condensate dataset with independently-measured G.** The next step is a real one: a condensate
   preparation whose G is known from micropipette aspiration or active microrheology, and check
   that the size crossover lands where γ/G says it should.
2. **The intensity witness does not discriminate and is not given a vote.** A real neck *should*
   be dimmer (less material in the light path); measured, it is 0.42–0.46 of the body median for a
   genuine neck **and** an arrested one alike, because the body median is dominated by the bright
   droplet centres. **Reported for inspection; the geometry decides.**
3. **2D projections of 3D objects.** Everything here is measured on a projected mask. A pair whose
   axis is out of plane will read a smaller apparent neck. This is not handled.
4. **The `neck_ratio` threshold (0.6) is physically motivated but not experimentally calibrated**
   on condensates. It separates the four morphologies cleanly in simulation; it has not been
   checked against a population of condensates scored by eye.
