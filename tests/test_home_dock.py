"""**Qt-smoke: the Home dock renders the capability cards + navigator, and its toggle flips the app mode.**

Integration (needs Qt + qtbot). The composed logic — navigator drive, the card catalogue, app-mode
persistence — is core/base-tested elsewhere; this proves the widget is wired: cards render grouped by
category, an advanced-only card is hidden in the beginner view, the Guided/Full toggle flips (and persists)
the mode and re-renders, and clicking a card invokes its real entry.
"""
import types

import pytest


def _store(tmp_path):
    from pycat.utils.user_settings import UserSettings
    return UserSettings(path=tmp_path / "settings.json")


@pytest.mark.integration
def test_home_renders_cards_and_the_toggle_flips_the_mode(qtbot, tmp_path):
    from pycat.ui.home_dock import build_home_widget
    from pycat.utils.feature_registry import FeatureRegistry, FeatureCard
    from pycat.utils.feature_cards import register_default_feature_cards, EXPECTED_CARD_KEYS
    from pycat.utils.app_mode import AppMode, current_mode

    store = _store(tmp_path)
    reg = FeatureRegistry()
    register_default_feature_cards(types.SimpleNamespace(), reg=reg)        # fake cm; entries not invoked here
    reg.register(FeatureCard(key="adv_only", title="Advanced Only", summary="experts only",
                             category="Report", min_mode=AppMode.ADVANCED, entry=lambda: None))

    widget = build_home_widget(types.SimpleNamespace(), reg=reg, store=store)
    assert widget is not None
    qtbot.addWidget(widget)

    # beginner view: every default card shows; the advanced-only card is hidden; the navigator is embedded
    shown = {c.key for c, _ in widget._cards}
    assert set(EXPECTED_CARD_KEYS) <= shown
    assert "adv_only" not in shown
    assert widget._mode_button is not None and widget._navigator is not None

    # flip to Full via the toggle → the mode persists AND the advanced-only card appears
    widget._mode_button.click()
    assert current_mode(store) is AppMode.ADVANCED
    assert "adv_only" in {c.key for c, _ in widget._cards}

    widget.detach()


@pytest.mark.integration
def test_clicking_a_card_invokes_its_real_entry(qtbot, tmp_path):
    from pycat.ui.home_dock import build_home_widget
    from pycat.utils.feature_registry import FeatureRegistry, FeatureCard

    opened = []
    reg = FeatureRegistry()
    reg.register(FeatureCard(key="k", title="Thing", summary="does a thing", category="Measure",
                             entry=lambda: opened.append(True)))
    widget = build_home_widget(types.SimpleNamespace(), reg=reg, store=_store(tmp_path))
    qtbot.addWidget(widget)

    btn = next(b for c, b in widget._cards if c.key == "k")
    btn.click()
    assert opened == [True]                                                 # the home invoked the real entry

    widget.detach()


@pytest.mark.integration
def test_the_home_splits_guided_and_explore_into_tabs(qtbot, tmp_path):
    from pycat.ui.home_dock import build_home_widget
    from pycat.utils.feature_registry import FeatureRegistry
    from pycat.utils.feature_cards import register_default_feature_cards

    reg = FeatureRegistry()
    register_default_feature_cards(types.SimpleNamespace(), reg=reg)
    widget = build_home_widget(types.SimpleNamespace(), reg=reg, store=_store(tmp_path))
    qtbot.addWidget(widget)
    assert widget._tabs is not None
    labels = [widget._tabs.tabText(i) for i in range(widget._tabs.count())]
    assert any("Guided" in t for t in labels) and any("Explore" in t for t in labels)


@pytest.mark.integration
def test_every_interactive_element_carries_explanatory_text(qtbot, tmp_path):
    """Item 3b: the panel shipped without an explanatory layer at all. This is the missing-layer guard — the
    mode toggle and every capability card button must carry a tooltip, and every plan step-colour must have a
    stated meaning (items 4/5), so the layer can't go missing wholesale again."""
    from pycat.ui.home_dock import build_home_widget
    from pycat.ui.navigator_dock import _STATE_STYLE, _STATE_MEANING
    from pycat.utils.feature_registry import FeatureRegistry
    from pycat.utils.feature_cards import register_default_feature_cards

    reg = FeatureRegistry()
    register_default_feature_cards(types.SimpleNamespace(), reg=reg)
    widget = build_home_widget(types.SimpleNamespace(), reg=reg, store=_store(tmp_path))
    qtbot.addWidget(widget)

    assert widget._mode_button.toolTip(), "the Guided/Full toggle must explain itself"
    assert widget._cards, "the explore tab must have capability cards"
    for card, btn in widget._cards:
        assert btn.toolTip(), f"card {card.key!r} has no explanatory tooltip"
    assert set(_STATE_STYLE) <= set(_STATE_MEANING), "every plan step colour must have a stated meaning"
