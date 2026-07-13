"""
A reassuring claim must state its scope.

The failure this prevents
-------------------------
1.5.459 found a message that was **true and incomplete**::

    "Kp is pedestal-independent. Validated: 29.65 recovered against a true 30.0."

Every word is correct. The scope is **the pedestal, and nothing else** — and it printed
unchanged while an over-inclusive droplet mask collapsed Kp by **7×**. The pedestal correction
was perfectly sound the whole time. *The user reads the reassurance.*

1.5.461 found that **I then did exactly the same thing myself**: ``dense_dilute_contrast`` was
described as *"exact — the pedestal cancels in the difference"*, in three places and every
release for thirty-five versions. Exact against the pedestal; **degraded 22 % by the PSF halo.**

And this test exists because the 1.5.459 correction **did not reach the second copy of the same
claim** — ``partition_measurement`` carried the unscoped version for two more releases. *A
true-but-unscoped claim gets fixed where you are looking and lives on where you are not.*

The rule
--------
A user-facing message that says a number is **validated**, **exact**, or **independent** of
something must, in the same message, say **what it is not validated against**. The words that
do that are "only", "not", "says nothing about", "but", "however", "specifically".

This is not a style check. **A reassurance whose scope is unstated is read as a guarantee**, and
it is the most dangerous kind of message there is — more dangerous than no message, because it
actively suppresses the user's own doubt.
"""

import ast
import pathlib
import re

import pytest

_SRC = pathlib.Path(__file__).resolve().parents[1] / "src" / "pycat"

# A claim that the number can be trusted.
_REASSURANCE = re.compile(
    r"\bvalidated\b|\bis exact\b|\bis EXACT\b|-independent\b|\bproven\b|\bguaranteed\b",
    re.IGNORECASE,
)

# Words that bound the claim — that say what it does NOT cover.
_SCOPE = re.compile(
    r"\bonly\b|\bnot\b|\bsays nothing\b|\bbut\b|\bhowever\b|\bspecifically\b|"
    r"\bagainst the\b|\bdoes not\b|\bcaveat\b|\bexcept\b",
    re.IGNORECASE,
)


def _user_facing_messages(path):
    """Every string handed to show_info / show_warning, with its line number."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"))
    except SyntaxError:
        return []

    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = ""
        if isinstance(node.func, ast.Name):
            name = node.func.id
        elif isinstance(node.func, ast.Attribute):
            name = node.func.attr
        if "show_info" not in name and "show_warning" not in name:
            continue

        # Concatenate every string literal in the call — f-strings, implicit joins, `+`.
        text = " ".join(
            n.value for n in ast.walk(node)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
        )
        if text.strip():
            out.append((node.lineno, text))
    return out


@pytest.mark.core
def test_reassuring_messages_state_their_scope():
    """A message saying a number is validated must say what it is NOT validated against."""
    offenders = []

    for path in sorted(_SRC.rglob("*.py")):
        for lineno, text in _user_facing_messages(path):
            if not _REASSURANCE.search(text):
                continue
            if _SCOPE.search(text):
                continue
            offenders.append(f"{path.name}:{lineno}\n      {text.strip()[:120]}...")

    assert not offenders, (
        "These user-facing messages tell the scientist a number is validated / exact / "
        "independent, and NEVER say what they do not cover:\n\n  "
        + "\n\n  ".join(offenders)
        + "\n\nA reassurance whose scope is unstated is read as a GUARANTEE. "
          "'Kp is pedestal-independent. Validated: 29.65 recovered against a true 30.0' is "
          "entirely true — about the PEDESTAL — and it printed unchanged while a bad mask "
          "collapsed Kp by 7x (1.5.459). The pedestal correction was sound the whole time.\n\n"
          "State the scope IN the sentence: what it was validated against, and what it says "
          "nothing about."
    )
