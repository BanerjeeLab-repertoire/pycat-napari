# Tag-system Phase-1: what the codebase ground-truthing changed

**Date:** 2026-07-15
**Companion to:** `codebase_audit_2026-07-15.md` and `PyCAT_Scientific_Navigator_Architecture.md`
**Purpose:** The architecture doc's §5 laid out a Phase-1 plan for the tag foundation, written before
the codebase was read. Reading the real code (this session) changed several of its assumptions —
mostly in PyCAT's favour. This note records the corrected Phase-1 status so the plan isn't followed
as if the code were still in its pre-verification state.

---

## The headline: Phase-1 is mostly already done, and the rest is now shipped

The architecture doc's Phase-1 was: *land `representation` + `state` tags, the extra lineage
relations, the `source='pipeline'` fix, a central `TaggedLayerFactory`, and a CI lint against direct
`viewer.add_*`; migrate call sites behind the factory.*

Against the real code, that breaks down as:

| Phase-1 item | Assumed state (doc) | **Verified reality** | Action taken |
|---|---|---|---|
| `source='pipeline'` fix | needed | **live bug** — silently downgraded to `inferred` | **FIXED** (1.6.35) |
| `representation` tag | needed | genuinely absent | **ADDED** (1.6.36) |
| `state` tag | needed | genuinely absent | **ADDED** (1.6.36, ordered) |
| extra lineage relations | needed | 4 of them absent (`pairs_with` already added) | **ADDED** (1.6.36) |
| QC writes onto layer | (not in doc's P1, but PDF8 #4) | confirmed gap — verdict stranded in a table | **FIXED** (1.6.36) |
| central `TaggedLayerFactory` | build it, migrate ~122 call sites | **already solved differently** — a viewer-level *hook* (`layer_tag_hook.py`) wraps every `add_*` so all sites auto-tag | **no action needed** |
| CI lint vs direct `viewer.add_*` | add it | **would fight the architecture** — the hook makes direct `add_*` *correct*, not a bypass | **not added, by design** |

So of the seven Phase-1 items, **four were real and are now shipped**, **one QC gap was found and
fixed**, and **two (the factory + the lint) were premised on a factory model PyCAT doesn't use** — it
uses a hook that gives the same "no call site can forget" guarantee structurally.

---

## Why the factory/lint items don't apply (the one worth internalising)

The architecture doc assumed the sanctioned way to tag would be a `TaggedLayerFactory` — a single
constructor every module must call, enforced by a lint that fails the build on any direct
`viewer.add_*`. That's a reasonable design, but it's **not the one PyCAT took**, and PyCAT's is
arguably better:

- `layer_tag_hook.py` wraps `add_image`/`add_labels`/`add_points`/`add_shapes`/`add_tracks` **at the
  viewer**, once. Interception happens below every call site.
- Therefore a direct `viewer.add_image(...)` is **not** a tagging bypass — it is auto-tagged on the
  way through. "A new call site is tagged automatically, because it does not know it is being
  tagged" (the hook's own header).
- A CI lint forbidding direct `add_*` would then be forbidding the *normal, correct* thing. It would
  protect a factory that doesn't exist and harass call sites that are already covered.

The factory and the hook are two solutions to the same problem ("no layer escapes tagging"). PyCAT
chose the hook. The Phase-1 plan should drop the factory-migration and the lint entirely — not defer
them, *drop* them — because the invariant they were meant to establish already holds.

---

## Net effect on the Scientific Navigator roadmap

The Navigator design (question → intent → planner → gates → **tag resolver**) leans on the tag
system being able to (a) distinguish representations, (b) prefer the most-processed layer, (c) carry
measurement/tracking lineage, and (d) honour QC verdicts. Before this session, **none of those four
were expressible** in the real tag vocabulary. After 1.6.35–1.6.36, **all four are**:

- (a) `representation` + `representation_satisfies()` lattice → typed capability matching.
- (b) `state` + `state_rank()` ordering → "prefer refined over raw".
- (c) `registered_to`/`measured_from`/`tracks`/`reference_for` → the VPT/MSD brushing + coloc links.
- (d) `quality_status` on the layer + `analysis_ready_for` → QC as a queryable gate.

So the tag *foundation* the Navigator was waiting on is now in place. The next real Navigator step is
no longer "fix the tags" — it's Phase-2 in the doc (populate `ModuleContract`s for the 75 modules and
add the canonical-13-workflow regression), which is data entry + validation against the real
`workflow_checklist.py`, and is genuinely blocked on nothing but time.

---

## Honest note on scope

Two audit items were investigated this session and deliberately **not** turned into code changes,
because the investigation showed the change would be wrong:

1. **devbio-napari extra** — looked like unused cruft (zero imports), but it's a *documented*
   user-facing convenience (README + install docs + conda env files) for installing the devbio
   plugin suite alongside PyCAT. Keep it. (Corrected in the audit doc, C1.)
2. **`np.asarray(layer.data)` frame-0 sweep** — ~30 sites exist, but most are 2D cellular/in-vitro
   workflows where the layer's `.data` is already a full array and `asarray` is correct. Which of the
   remainder are genuinely lazy-stack consumers depends on runtime layer types that can't be
   determined by static reading alone. This one needs Gable's look-at-the-actual-data judgment per
   site, not a speculative flag list — so it is left as a **pinned investigation**, not a change. The
   known-real instance (QC UI) was already fixed earlier (it uses `materialize_stack`).
