"""**One place to see and change the app's user preferences.**

PyCAT accumulated persisted user preferences (`app_mode`'s beginner/advanced, `dock_space`'s results-dock
reflow) that were settable only in code — each owned by its own module, with no surface a user could reach.
This is the small, data-driven **registry** those surfaces were missing: it enumerates every user-adjustable
preference with its label, help text, discrete options, and current value, and dispatches a change back to the
owning module. A settings UI renders this list and writes through `set_preference`; nothing here mounts a
widget, so it is Qt-free and core-tested, and adding a new preference is one registry entry — the UI needs no
change.

Deliberately scoped to **discrete, low-risk choices** (each preference is a small fixed set of options), which
is what the two existing preferences are and what a first preferences panel should expose. It does not own the
values — `app_mode` and `dock_space` remain the source of truth; this only presents and forwards."""
from __future__ import annotations

import dataclasses


@dataclasses.dataclass(frozen=True)
class PreferenceOption:
    """One selectable value for a preference, with the human label a UI shows for it."""
    value: str
    label: str


@dataclasses.dataclass(frozen=True)
class PreferenceView:
    """A preference as a settings UI needs it: stable ``key``, a short ``label`` and longer ``description``,
    the discrete ``options``, and the ``current`` stored value. Pure data — no widgets, no callbacks."""
    key: str
    label: str
    description: str
    options: tuple
    current: str


def _mode_prefs():
    """The (key, label, description, options, getter, setter) tuples for each preference. Kept as a function so
    the owning modules are imported lazily and this module stays import-cheap and Qt-free."""
    from pycat.utils import app_mode, dock_space

    return [
        (
            app_mode._KEY,
            "Interface level",
            "Beginner shows a guided surface with only the essentials; Advanced shows the full menu-first "
            "workbench. You can switch at any time.",
            (PreferenceOption(app_mode.AppMode.BEGINNER.value, "Beginner — guided, essentials only"),
             PreferenceOption(app_mode.AppMode.ADVANCED.value, "Advanced — full workbench")),
            lambda store: app_mode.current_mode(store).value,
            lambda value, store: app_mode.set_mode(value, store),
        ),
        (
            dock_space.PREF_KEY,
            "Results dock placement",
            "When an analysis mounts its results, how to give them room beside a tall method panel. Tabify "
            "makes the results a tab at full height (recommended); Collapse shrinks the method panel to give "
            "room; Stack keeps the classic even split.",
            (PreferenceOption('tabify', "Tabify — results as a tab, full height (recommended)"),
             PreferenceOption('collapse', "Collapse — shrink the method panel to give room"),
             PreferenceOption('stack', "Stack — even split (classic)")),
            lambda store: dock_space.reflow_mode(store),
            lambda value, store: dock_space.set_reflow_mode(store, value),
        ),
    ]


def _registry():
    return {key: (label, desc, options, getter, setter)
            for (key, label, desc, options, getter, setter) in _mode_prefs()}


def list_preferences(store=None) -> list:
    """Every user-adjustable preference as a :class:`PreferenceView`, in display order, with its current value
    resolved from ``store`` (the process-wide settings when ``None``). This is the whole model a settings UI
    needs — render one control per view, its choices from ``options``, its selection from ``current``."""
    views = []
    for (key, label, desc, options, getter, _setter) in _mode_prefs():
        views.append(PreferenceView(key=key, label=label, description=desc,
                                    options=tuple(options), current=getter(store)))
    return views


def set_preference(key, value, store=None):
    """Forward a change for ``key`` to its owning module (``app_mode`` / ``dock_space``), which validates and
    persists it. Raises ``KeyError`` for an unknown preference and ``ValueError`` (from the owner) for a value
    outside that preference's options — a settings UI offers only valid options, so either is a programming
    error worth surfacing, not swallowing."""
    reg = _registry()
    if key not in reg:
        raise KeyError(f"unknown preference {key!r}; known: {sorted(reg)}")
    valid = {o.value for o in reg[key][2]}
    if value not in valid:
        raise ValueError(f"{value!r} is not a valid option for {key!r}; expected one of {sorted(valid)}")
    reg[key][4](value, store)      # the owner's setter
