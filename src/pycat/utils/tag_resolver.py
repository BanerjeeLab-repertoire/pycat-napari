"""
**Which layer does this step want?** Ask the tags, and be honest when the answer is unclear.

The problem
-----------
Every workflow step in PyCAT has a layer dropdown, and **the user fills every one of them by
hand, in every step, on every run.** ``field_status`` tracks *whether* a dropdown is filled;
**nothing fills it.**

The tag vocabulary (1.5.492–493) means a step can now *ask for what it needs*:

=====================================  ==========================================
what the step wants                    the query
=====================================  ==========================================
the raw image                          ``role=image, provenance=raw``
a binary mask                          ``role=mask``
the cell labels                        ``role=labels, target=cell``
the CLAHE result                       ``op=clahe``
the most recent mask                   ``role=mask``, ``prefer='newest'``
=====================================  ==========================================

The thing that must not happen
------------------------------
**A wrong auto-selection that the user does not notice is worse than an empty dropdown.** They
run the analysis on the wrong layer, get a number, and **never know.**

So the resolver never chooses silently when it is guessing. It returns a **confidence** and a
**reason**, and the caller decides what to do with each:

===========  ==========================================  ============================
confidence   what it means                               what the UI should do
===========  ==========================================  ============================
``certain``  **exactly one** layer matches               auto-select it
``likely``   several match, one is clearly best          pre-select, **and say so**
``ambiguous``  several match, no clear winner            **do not choose** — list them
``none``     nothing matches                             say what was looked for
===========  ==========================================  ============================

**The reason is what makes it non-black-box.** A user who sees *"chose 'CLAHE' because it is the
only preprocessed image"* can tell instantly whether it is right. A user who sees a layer name
appear in a box cannot.
"""

from __future__ import annotations

from pycat.utils.general_utils import debug_log
from pycat.utils.layer_tags import get_tag, get_tags


CERTAIN = 'certain'
LIKELY = 'likely'
AMBIGUOUS = 'ambiguous'
NONE = 'none'


def matches(layer, query):
    """Does this layer satisfy every term of the query?

    A query is a dict of tag key → value. **A missing tag is a non-match**, not a wildcard: a
    layer that does not say it is a mask is not a mask, and treating silence as agreement is how
    a resolver ends up choosing the wrong thing.
    """
    for key, wanted in query.items():
        if key in ('prefer', 'exclude'):
            continue
        actual = get_tag(layer, key)
        if actual is None:
            return False
        if isinstance(wanted, (list, tuple, set)):
            if actual not in wanted:
                return False
        elif actual != wanted:
            return False
    return True


def _describe(query):
    """The query, in words. This is what the user is told when nothing matches."""
    parts = []
    for key, value in query.items():
        if key in ('prefer', 'exclude'):
            continue
        if isinstance(value, (list, tuple, set)):
            parts.append(f"{key} is one of {sorted(value)}")
        else:
            parts.append(f"{key} = {value}")
    return ', '.join(parts) or 'anything'


def resolve(viewer, query, *, exclude=None):
    """**Which layer does this step want?** Returns ``(layer, confidence, reason)``.

    ``query`` is a dict of tag key → value, plus an optional ``prefer``:

    * ``prefer='newest'`` — the most recently added matching layer. The usual case: a step that
      wants "the mask" almost always wants **the one you just made.**
    * ``prefer='oldest'`` — the first, e.g. the raw acquisition.
    * ``prefer='head_of_lineage'`` — the layer nothing was derived *from*, i.e. the source.

    **Without a ``prefer``, several matches means AMBIGUOUS and nothing is chosen.** That is the
    point: *the resolver would rather say "I don't know" than pick one and be wrong quietly.*
    """
    if viewer is None:
        return None, NONE, 'There is no viewer to look in.'

    excluded = set(exclude or ())
    prefer = query.get('prefer')

    try:
        candidates = [l for l in viewer.layers
                      if l not in excluded and matches(l, query)]
    except Exception as exc:
        debug_log('tag_resolver: could not scan the layers', exc)
        return None, NONE, f'The layers could not be scanned: {exc}'

    described = _describe(query)

    if not candidates:
        return None, NONE, (
            f"**No layer matches {described}.**\n\n"
            f"Either the step it depends on has not been run, or the layer it produced was not "
            f"tagged. Every layer PyCAT creates is tagged automatically (1.5.493) — a layer "
            f"loaded or made by another plugin may not be.")

    if len(candidates) == 1:
        layer = candidates[0]
        return layer, CERTAIN, (
            f"'{layer.name}' is the **only** layer where {described}.")

    # ── Several match. Is one of them clearly the right one? ────────────────────
    #
    # Only a PREFERENCE makes that decidable. Without one, choosing would be a guess — and a
    # guess the user does not notice is the failure this whole module exists to prevent.
    if prefer == 'newest':
        layer = candidates[-1]
        return layer, LIKELY, (
            f"{len(candidates)} layers match {described}; chose **'{layer.name}'** because it is "
            f"the most recent. *Check this is the one you meant.*")

    if prefer == 'oldest':
        layer = candidates[0]
        return layer, LIKELY, (
            f"{len(candidates)} layers match {described}; chose **'{layer.name}'** because it is "
            f"the earliest. *Check this is the one you meant.*")

    if prefer == 'head_of_lineage':
        # ── The SOURCE is what provenance says, not what the lineage graph says ──
        #
        # A first version looked for layers with no lineage EDGE, and it got the answer wrong:
        # it returned the most recently added derived layer, because **no edges exist at all.**
        # The auto-tagging hook (1.5.493) cannot record a parent — by the time a UI calls
        # ``viewer.add_image(result)``, the transform that made ``result`` has already returned
        # and is off the stack.
        #
        # But the hook DOES know something better: whether a layer was the **first image into an
        # empty viewer**, which it records as ``provenance='raw'``. That is exactly the question
        # "is this the source?", and it is answered with certainty rather than inferred from an
        # absence.
        #
        # *An empty lineage graph is not evidence that a layer is a source. It is evidence that
        # nobody recorded the lineage.*
        heads = [l for l in candidates
                 if get_tag(l, 'provenance') == 'raw' or not _has_parent(l)]
        raw_heads = [l for l in candidates if get_tag(l, 'provenance') == 'raw']

        if len(raw_heads) == 1:
            layer = raw_heads[0]
            return layer, CERTAIN, (
                f"'{layer.name}' is the **source** — it came straight from the microscope, and "
                f"{described}.")
        if len(heads) == 1:
            layer = heads[0]
            return layer, CERTAIN, (
                f"'{layer.name}' is the **source** — nothing was derived to make it, and "
                f"{described}.")
        if raw_heads:
            layer = raw_heads[-1]
            return layer, LIKELY, (
                f"{len(raw_heads)} source layers match {described}; chose **'{layer.name}'** "
                f"(the most recent). *Check this is the one you meant.*")

    names = ', '.join(f"'{l.name}'" for l in candidates)
    return None, AMBIGUOUS, (
        f"**{len(candidates)} layers match {described}**: {names}.\n\n"
        f"**Nothing was selected**, because choosing one would be a guess — and an analysis run "
        f"on the wrong layer gives a number that looks fine. Pick the one you want.")


def _has_parent(layer):
    """Was this layer derived from another? A lineage edge says so."""
    try:
        from pycat.utils.layer_tags import get_edges
        return bool(get_edges(layer))
    except Exception as exc:
        debug_log('tag_resolver: could not read the lineage', exc)
        return False


def explain(layer):
    """**Why is this layer what it is?** The tags, in words.

    The anti-black-box move: a user who can see *"this is a mask, made by `otsu`, from the raw
    image"* can tell at a glance whether the pipeline did what they meant.
    """
    if layer is None:
        return 'No layer.'

    try:
        tags = {t['key']: t['value'] for t in get_tags(layer)}
    except Exception as exc:
        debug_log('tag_resolver: could not read the tags', exc)
        return f"'{getattr(layer, 'name', '?')}' — its tags could not be read."

    if not tags:
        return (f"'{layer.name}' carries **no tags**. PyCAT tags every layer it makes "
                f"automatically, so this one probably came from elsewhere.")

    bits = []
    if 'role' in tags:
        bits.append(f"a **{tags['role']}**")
    if 'target' in tags:
        bits.append(f"of **{tags['target']}s**")
    if 'op' in tags:
        try:
            from pycat.utils.tag_registry import get_operation
            entry = get_operation(tags['op'])
            summary = entry['summary'] if entry else tags['op']
        except Exception:
            summary = tags['op']
        bits.append(f"made by **{tags['op']}** ({summary})")
    if tags.get('provenance') == 'raw':
        bits.append('**straight from the microscope**')

    return f"'{layer.name}' is " + ', '.join(bits) + '.' if bits else f"'{layer.name}'."


# ── The binding table: a step DECLARES what it needs ──────────────────────────────────────

_BINDINGS = None


def _load_bindings():
    """The declarations, from ``layer_bindings.json``. **Data, not code.**

    A step's binding can be corrected without touching the UI that reads it — which matters,
    because the right binding for a step is a *scientific* judgement (does this want the raw image
    or the filtered one?) and it will be revised as the workflows are curated.
    """
    global _BINDINGS
    if _BINDINGS is not None:
        return _BINDINGS

    import json
    import pathlib

    try:
        path = pathlib.Path(__file__).with_name('layer_bindings.json')
        raw = json.loads(path.read_text(encoding='utf-8'))
        _BINDINGS = {k: v for k, v in raw.items() if not k.startswith('_')}
    except Exception as exc:
        debug_log('tag_resolver: the binding table could not be loaded', exc)
        _BINDINGS = {}

    return _BINDINGS


def resolve_binding(viewer, binding_key, *, exclude=None):
    """**Resolve a step's declared need.** ``binding_key`` is e.g. ``'puncta_analysis.cell_labels'``.

    Returns ``(layer, confidence, reason)``, exactly as ``resolve`` does — so a caller handles a
    binding and an ad-hoc query the same way.

    A key with **no entry** returns ``NONE`` and says so. That is not a failure: **a field that is
    genuinely ambiguous should not be autopopulated**, and leaving it out of the table is how that
    is expressed.
    """
    bindings = _load_bindings()
    binding = bindings.get(binding_key)

    if binding is None:
        return None, NONE, (
            f"No binding is declared for '{binding_key}'.\n\n"
            f"**That may be correct** — a field whose right layer cannot be decided from tags "
            f"alone should not be autopopulated, and leaving it out of the table is how that is "
            f"said. Add it to ``layer_bindings.json`` if it should be.")

    query = {k: v for k, v in binding.items() if k != 'why'}
    layer, confidence, reason = resolve(viewer, query, exclude=exclude)

    # The binding's own 'why' is the SCIENTIFIC reason the step wants this layer. The resolver's
    # reason is the MECHANICAL one (how it was found). Both matter, and they are different.
    why = binding.get('why')
    if why:
        reason = f"{reason}\n\n*{why}*"

    return layer, confidence, reason


def autopopulate(viewer, dropdown, binding_key, *, auto_select_likely=True):
    """**Fill a dropdown from the tags — or leave it, and say why.**

    This is the function a UI calls. It returns ``(confidence, reason)`` so the caller can show
    the reason in a tooltip.

    **It only auto-selects on ``certain``.** On ``likely`` it selects *and marks the field* so the
    user can see it was inferred — unless ``auto_select_likely=False``, in which case it does not
    touch the dropdown at all.

    *A wrong auto-selection the user does not notice is worse than an empty dropdown: they run the
    analysis on the wrong layer, get a number, and never know.*
    """
    layer, confidence, reason = resolve_binding(viewer, binding_key)

    if layer is None:
        return confidence, reason

    # ── A `likely` result that selects NOTHING is the worst outcome ─────────────
    #
    # A first version only selected on CERTAIN. So a binding with ``prefer='newest'`` — which is
    # most of them — resolved to LIKELY, **selected nothing, and said nothing.** The dropdown sat
    # empty while the resolver knew perfectly well which layer was wanted.
    #
    # That is worse than either alternative: it is the feature **silently not working.**
    #
    # So a LIKELY match IS selected — and the tooltip **says it was inferred and asks the user to
    # check.** The user still sees a filled dropdown, and still has the information to catch it if
    # it is wrong.
    #
    # AMBIGUOUS still selects nothing, and that remains correct: there, the resolver genuinely
    # does not know, and *a wrong auto-selection the user does not notice is worse than an empty
    # dropdown.*
    if confidence == CERTAIN or (confidence == LIKELY and auto_select_likely):
        try:
            index = dropdown.findText(layer.name)
            if index >= 0:
                dropdown.setCurrentIndex(index)
            else:
                return NONE, (
                    f"'{layer.name}' is the right layer, but it is not in this dropdown's list. "
                    f"The list is probably stale — reopen the panel.")
        except Exception as exc:
            debug_log('tag_resolver: could not set the dropdown', exc)
            return NONE, f'The dropdown could not be set: {exc}'

    try:
        dropdown.setToolTip(reason)
    except Exception:
        pass

    return confidence, reason
