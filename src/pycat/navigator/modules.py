"""
modules.py
==========

A seed set of PyCAT module *contracts*, reconstructed from PDF7's information-
contract table ("I built the table for all 75 modules in pycat/toolbox").

IMPORTANT — these contracts are reconstructed from the PDF summaries, not read
from the actual ``PyCAT_module_information_contracts.xlsx`` (which was an
unreadable binary blob in this environment) or the codebase (not attached).
Module names follow the PDF7 examples (``condensate_physics_tools``,
``frap_tools``, ``segmentation_tools``, ``spatial_metrology_tools``). Treat the
``requires``/``provides``/``observables`` rows as a faithful *shape* to be
reconciled against the real workbook — that reconciliation is data entry, not
redesign.

The design principle enforced here (PDF4): **preprocessing modules are NOT
generic providers in the dependency graph.** Segmentation requires a plain
``intensity_field``, which the loaded acquisition supplies directly. Background
subtraction / CLAHE are registered and available, but they are inserted only
when a QC gate detects the corresponding defect — not pulled in automatically.
"""
from __future__ import annotations

from .capabilities import Capability, InformationRole, Observable, Representation, cap
from .contracts import Assumption, CostModel, ModuleContract
from .context import AnalysisContext
from .gates import probe_gate, static_gate
from .registry import ModuleRegistry

R = Representation


def build_registry() -> ModuleRegistry:
    reg = ModuleRegistry()

    # --------------------------------------------------------------- #
    # Source: the loaded acquisition. Root of every product chain.     #
    # Provides an intensity field for ANY target (target:* wildcard).  #
    # --------------------------------------------------------------- #
    reg.register(ModuleContract(
        name="acquisition",
        info_role=InformationRole.INFRASTRUCTURE,
        purpose="The loaded image data (root of all product lineages).",
        provides=[cap(R.INTENSITY_FIELD, "target:*")],
        preference=0.9,
        public_api="io_tools.load()",
        source="pycat/toolbox/io_tools.py",
    ))

    # --------------------------------------------------------------- #
    # Coordinate / QC — a probe that measures data-quality observables #
    # so probe-gates elsewhere can be decided (staged gating, #4).     #
    # --------------------------------------------------------------- #
    reg.register(ModuleContract(
        name="qc_tools",
        info_role=InformationRole.COORDINATE,
        purpose="Assess signal, SNR, sampling, saturation; annotate layers.",
        requires_inputs=[cap(R.INTENSITY_FIELD, "target:*")],
        provides=[cap(R.MEASUREMENT_TABLE, "kind:qc")],
        observables=["snr", "sampling", "saturation", "segmentation_confidence"],
        questions=["is this dataset suitable"],
        preference=0.5,
        public_api="qc_tools.assess()",
        source="pycat/toolbox/qc_tools.py",
    ))

    # --------------------------------------------------------------- #
    # Transform: preprocessing (registered, NOT auto-inserted).        #
    # --------------------------------------------------------------- #
    reg.register(ModuleContract(
        name="background_subtraction",
        info_role=InformationRole.TRANSFORM,
        purpose="Rolling-ball / model background removal.",
        requires_inputs=[cap(R.INTENSITY_FIELD, "target:*")],
        provides=[cap(R.INTENSITY_FIELD, "target:*", "state:corrected")],
        propagates_tags=frozenset({"target"}),
        preference=0.4,
        cost=CostModel(base_seconds=2, per_megapixel=0.3),
        public_api="preprocessing_tools.subtract_background()",
        source="pycat/toolbox/preprocessing_tools.py",
    ))
    reg.register(ModuleContract(
        name="clahe",
        info_role=InformationRole.TRANSFORM,
        purpose="Contrast-limited adaptive histogram equalization.",
        requires_inputs=[cap(R.INTENSITY_FIELD, "target:*")],
        provides=[cap(R.INTENSITY_FIELD, "target:*", "state:enhanced")],
        propagates_tags=frozenset({"target"}),
        preference=0.3,
        public_api="preprocessing_tools.clahe()",
        source="pycat/toolbox/preprocessing_tools.py",
    ))

    # --------------------------------------------------------------- #
    # Create: segmentation. image -> instance labels.                  #
    # --------------------------------------------------------------- #
    reg.register(ModuleContract(
        name="segmentation_tools",
        info_role=InformationRole.CREATE,
        purpose="Convert image signal into discrete labelled objects.",
        requires_inputs=[cap(R.INTENSITY_FIELD, "target:*")],
        provides=[cap(R.INSTANCE_LABELS, "target:*")],
        propagates_tags=frozenset({"target"}),
        observables=[],   # segmentation is a means, not an observable
        preference=0.7,
        cost=CostModel(base_seconds=5, per_megapixel=1.5),
        assumptions=[
            probe_gate("seg.snr", "Segmentation needs adequate signal-to-noise.",
                       observable="snr", threshold_key="snr", min_value=3.0,
                       rationale="Below SNR≈3 object boundaries are unreliable."),
        ],
        public_api="segmentation_tools.segment()",
        source="pycat/toolbox/segmentation_tools.py",
    ))

    # --------------------------------------------------------------- #
    # Create/Transform: tracking. labels + time -> trajectories.       #
    # --------------------------------------------------------------- #
    reg.register(ModuleContract(
        name="tracking_tools",
        info_role=InformationRole.CREATE,
        purpose="Link objects across frames into trajectories.",
        requires_inputs=[cap(R.INSTANCE_LABELS, "target:*")],
        requires_context=["time_series"],
        provides=[cap(R.TRAJECTORIES, "target:*")],
        propagates_tags=frozenset({"target"}),
        observables=[Observable.MOTION.value],
        preference=0.7,
        cost=CostModel(base_seconds=4, per_frame=0.2),
        assumptions=[
            static_gate("track.sampling",
                        "Temporal sampling must resolve the dynamics of interest.",
                        predicate=lambda ctx: (None if not ctx.known("time_points")
                                               else ctx.get("time_points") >= 10),
                        rationale="Too few frames and motion/fusion cannot be fit."),
        ],
        public_api="tracking_tools.track()",
        source="pycat/toolbox/tracking_tools.py",
    ))

    # --------------------------------------------------------------- #
    # Measure: morphology. labels -> feature table.                    #
    # --------------------------------------------------------------- #
    reg.register(ModuleContract(
        name="morphology_tools",
        info_role=InformationRole.MEASURE,
        purpose="Per-object size, shape, count, intensity.",
        requires_inputs=[cap(R.INSTANCE_LABELS, "target:*")],
        provides=[cap(R.MEASUREMENT_TABLE, "target:*", "observable:*")],
        propagates_tags=frozenset({"target"}),
        observables=[Observable.COUNT.value, Observable.SIZE.value,
                     Observable.SHAPE.value, Observable.INTENSITY.value,
                     Observable.MORPHOLOGY.value],
        questions=["quantify structures inside cells"],
        preference=0.7,
        public_api="morphology_tools.regionprops_table()",
        source="pycat/toolbox/morphology_tools.py",
    ))

    # --------------------------------------------------------------- #
    # Measure/Interpret: condensate physics. trajectories -> fits.     #
    # --------------------------------------------------------------- #
    reg.register(ModuleContract(
        name="condensate_physics_tools",
        info_role=InformationRole.INTERPRET,
        purpose="Infer condensate dynamics and physical behaviour.",
        requires_inputs=[cap(R.TRAJECTORIES, "target:*")],
        provides=[cap(R.MODEL_FIT, "target:*", "observable:*")],
        propagates_tags=frozenset({"target"}),
        observables=[Observable.FUSION.value, Observable.COARSENING.value,
                     Observable.DIFFUSION.value, Observable.VISCOSITY.value,
                     Observable.MOTION.value],
        questions=["how something changes over time",
                   "measure phase separation or material properties"],
        preference=0.7,
        cost=CostModel(base_seconds=6, per_frame=0.1),
        assumptions=[
            static_gate("phys.calibrated",
                        "Physical parameters (diffusion, viscosity) require calibration.",
                        predicate=lambda ctx: ctx.context_requirement("calibrated"),
                        severity="warning",
                        rationale="Without voxel size, results are in pixels, not physical units."),
        ],
        public_api="condensate_physics_tools.fit()",
        source="pycat/toolbox/condensate_physics_tools.py",
    ))

    # --------------------------------------------------------------- #
    # Measure: colocalization. two channels -> overlap metrics.        #
    # --------------------------------------------------------------- #
    reg.register(ModuleContract(
        name="colocalization_tools",
        info_role=InformationRole.MEASURE,
        purpose="Quantify spatial/pixel/object association between channels.",
        requires_inputs=[cap(R.INTENSITY_FIELD, "target:*")],
        requires_context=["two_channels"],
        provides=[cap(R.MEASUREMENT_TABLE, "observable:colocalization")],
        observables=[Observable.COLOCALIZATION.value],
        questions=["whether two things are spatially related"],
        preference=0.7,
        public_api="colocalization_tools.manders()",
        source="pycat/toolbox/colocalization_tools.py",
    ))

    # --------------------------------------------------------------- #
    # Measure: spatial metrology. coordinates -> clustering metrics.   #
    # --------------------------------------------------------------- #
    reg.register(ModuleContract(
        name="spatial_metrology_tools",
        info_role=InformationRole.MEASURE,
        purpose="NND, Ripley, Voronoi, density and null-model metrics.",
        requires_inputs=[cap(R.INSTANCE_LABELS, "target:*")],
        provides=[cap(R.MEASUREMENT_TABLE, "target:*", "observable:*")],
        propagates_tags=frozenset({"target"}),
        observables=[Observable.CLUSTERING.value, Observable.NEAREST_NEIGHBOR.value,
                     Observable.SPATIAL_ORGANIZATION.value],
        questions=["whether two things are spatially related"],
        preference=0.6,
        public_api="spatial_metrology_tools.ripley()",
        source="pycat/toolbox/spatial_metrology_tools.py",
    ))

    # --------------------------------------------------------------- #
    # Measure: FRAP. bleach stack -> recovery kinetics.                #
    # --------------------------------------------------------------- #
    reg.register(ModuleContract(
        name="frap_tools",
        info_role=InformationRole.MEASURE,
        purpose="Measure molecular exchange and mobile fraction.",
        requires_inputs=[cap(R.INTENSITY_FIELD, "target:*")],
        requires_context=["time_series"],
        provides=[cap(R.MODEL_FIT, "observable:mobile_fraction")],
        observables=[Observable.MOBILE_FRACTION.value, Observable.DIFFUSION.value],
        questions=["measure phase separation or material properties"],
        preference=0.6,
        public_api="frap_tools.fit_recovery()",
        source="pycat/toolbox/frap_tools.py",
    ))

    # --------------------------------------------------------------- #
    # Communicate: statistics, plots, methods text (publication tail). #
    # Registered for realism; the demo appends them explicitly.        #
    # --------------------------------------------------------------- #
    reg.register(ModuleContract(
        name="statistics_tools", info_role=InformationRole.INTERPRET,
        purpose="Compare distributions, control vs treatment.",
        requires_inputs=[cap(R.MEASUREMENT_TABLE)],
        provides=[cap(R.MEASUREMENT_TABLE, "kind:comparison")],
        questions=["statistical comparison"], preference=0.5,
        public_api="statistics_tools.compare()", source="pycat/toolbox/statistics_tools.py"))
    reg.register(ModuleContract(
        name="plotting_tools", info_role=InformationRole.COMMUNICATE,
        purpose="Publication-ready figures.",
        requires_inputs=[cap(R.MEASUREMENT_TABLE)],
        provides=[cap(R.TABLE, "kind:figure")],
        questions=["figures"], preference=0.5,
        public_api="plotting_tools.figure()", source="pycat/toolbox/plotting_tools.py"))
    reg.register(ModuleContract(
        name="methods_writer", info_role=InformationRole.COMMUNICATE,
        purpose="Generate a methods paragraph from the executed plan.",
        provides=[cap(R.TABLE, "kind:methods_text")],
        questions=["methods text"], preference=0.5,
        public_api="reporting_tools.methods_text()", source="pycat/toolbox/reporting_tools.py"))

    return reg
