"""**The last-resort channel-identity prompt (sidecar_metadata Part 4).**

Shown ONLY when — after in-file metadata AND sidecar discovery AND the pixel classifier — a channel still has
no usable identity (it fell to the position guess, ``channel_needs_identity``). Modelled on the pixel-size
prompt's discipline: it asks **only when genuinely missing**, every field is **optional**, a skipped field
stays unset, and it is **never shown in batch/headless** (``build`` returns ``None`` without Qt, and the
caller must not prompt in a headless run). A given answer round-trips and is remembered for future same-layout
files through :mod:`utils.channel_designations` (signature-keyed, reversible) — a human answer fills a
genuinely-empty identity, it never overwrites real metadata.

Logic (which channels, and the persistence) is Qt-free (``channel_naming`` / ``channel_designations``); this
file only builds the dialog and harvests the answers, so it is thin and Qt-smoke tested. ``build_...`` returns
the dialog with a ``harvest()`` seam (no modal ``exec`` needed to test it)."""
from __future__ import annotations


def channels_needing_identity(channel_infos):
    """The indices of channels with no usable identity (a Qt-free convenience for the caller/tests)."""
    from pycat.utils.channel_naming import channel_needs_identity
    return [i for i, ci in enumerate(channel_infos or []) if channel_needs_identity(ci)]


def build_channel_identity_dialog(channel_infos, *, needing=None, parent=None):
    """Build the identity prompt for the channels that need one, or ``None`` when none do or Qt is
    unavailable. The returned ``QDialog`` carries ``_fields`` (index → line edit) and a ``harvest()`` that
    returns ``{index: name}`` for the fields the user filled — the seam a Qt-smoke test drives without a modal
    ``exec``."""
    try:
        from PyQt5.QtWidgets import (QDialog, QVBoxLayout, QFormLayout, QLineEdit, QLabel,
                                     QDialogButtonBox)
    except Exception:      # broad-ok: optional_probe — no Qt (headless/batch) → no prompt
        return None

    idxs = list(needing) if needing is not None else channels_needing_identity(channel_infos)
    if not idxs:
        return None

    dialog = QDialog(parent)
    dialog.setWindowTitle("Name the unidentified channels (optional)")
    root = QVBoxLayout(dialog)
    intro = QLabel("These channels carry no fluorophore, emission, or name in the file — name any you can "
                   "(optional). Blanks are left unset; your answers are remembered for files acquired the "
                   "same way.")
    intro.setWordWrap(True)
    root.addWidget(intro)

    form = QFormLayout()
    dialog._fields = {}
    for i in idxs:
        current = ((channel_infos or [{}])[i] if i < len(channel_infos or []) else {}) or {}
        edit = QLineEdit()
        edit.setPlaceholderText(str(current.get("label") or f"Channel {i}"))
        form.addRow(f"Channel {i}:", edit)
        dialog._fields[i] = edit
    root.addLayout(form)

    buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    root.addWidget(buttons)

    def harvest():
        return {i: e.text().strip() for i, e in dialog._fields.items() if e.text().strip()}
    dialog.harvest = harvest
    return dialog


def prompt_channel_identities(channel_infos, *, parent=None):
    """Show the prompt modally and return ``{index: name}`` for the channels the user named — ``{}`` if there
    is nothing to ask or the user cancels, ``None`` when Qt is unavailable (headless/batch: the caller skips
    it). This is the production entry; the dialog itself is smoke-tested via :func:`build_channel_identity_dialog`."""
    dialog = build_channel_identity_dialog(channel_infos, parent=parent)
    if dialog is None:
        return {} if channels_needing_identity(channel_infos) == [] else None
    try:
        from PyQt5.QtWidgets import QDialog
    except Exception:      # broad-ok: optional_probe — headless
        return None
    if dialog.exec_() != QDialog.Accepted:
        return {}
    return dialog.harvest()
