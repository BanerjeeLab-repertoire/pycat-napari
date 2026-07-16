# Claude Code — test-failure triage (7 failed, 560 passed)

**Date:** 2026-07-15 · Tree 1.6.59 · Written by chat-side Claude after investigating each failure
against the tree. **Bottom line: likely ZERO of these are production-code regressions** — one is a
self-resolving budget tripwire, and the other two look like STALE TESTS, not broken code. Verify each
below before "fixing" anything; do NOT change working production code to satisfy a stale test.

---

## 1. `test_complexity_budget` — 147 functions > 120 lines (ceiling 139)
**Not a bug — a tripwire, and it's self-resolving.** This test guards against unreviewable long
functions. The top offenders are exactly the decomposition targets:
`_open_stack_generic` (538), `_open_stack_ims` (246), `save_and_clear_all` (227), `open_2d_image` (207).
As the god-class decomposition lands (#3 IMS in progress; #5 `_open_stack_generic` queued after CZI),
these shrink and the count drops back under 139.
**Action:** do NOT bump the ceiling to hide it. Let the decomposition work bring it down. If it's
blocking CI merges *right now* and you need green, the honest move is to knock ONE existing offender
under 120 (or note the budget will clear when #3/#5 land) — not to raise the ceiling. This failure is
the codebase correctly reporting "decomposition isn't finished yet."

---

## 2. `test_image_reader_2d_extraction` — 5× `assert 0 == N` (empty channels)
**Verified NOT a logic regression.** `read_2d_image_channels`
(`src/pycat/file_io/readers/image_reader_2d.py`) is logically correct: reproduced headlessly with the
test's exact fakes (monkeypatched `open_image`/`read_plane`/`extract_channel_info`), `S=2,C=1`
returns 2 channels, `S=1,C=3` returns 3, etc. The loop at lines 93–105 builds `channels` correctly.
So the extracted function is fine — the empty `channels` in the CI run comes from the TEST
ENVIRONMENT, not the code.

**Where to look (run `pytest tests/test_image_reader_2d_extraction.py -x --tb=long -m core`):**
- The function does a local `from pycat.utils.channel_naming import extract_channel_info` (line 63).
  If `channel_naming` pulls a GUI/napari dep at import in the `core` (headless) env, that import
  raises → and the test's `monkeypatch.setattr(cn, "extract_channel_info", ...)` patches the module
  attribute, but the FUNCTION re-imports it fresh, so a module-import failure there would surface as
  an error, not empty channels. Check whether `channel_naming` imports cleanly under `-m core`
  (conftest.py lists the headless-excluded packages).
- More likely: confirm the monkeypatch targets still match. The test patches `ir.open_image` and
  `ir.read_plane` (module-level names) — verify those are still module-level in
  `image_reader_2d.py` (they are: imported at line 15 `from pycat.file_io.image_reader import
  open_image, read_plane`). If a refactor changed them to call `image_reader.open_image(...)`
  qualified, the monkeypatch would miss and the real `open_image` would run → return something the
  fake `read_plane` can't process → empty. **This is the prime suspect if #3 or another change touched
  the import style.**
- The real traceback will show it in one run. Fix the TEST or restore the module-level import — do
  not change `read_2d_image_channels`'s verified-correct logic.

---

## 3. `test_no_time_axis_no_warning::test_an_UNKNOWN_time_axis_FAILS_TOWARD_THE_LOUD_SIDE`
**This is a STALE TEST, not a code bug — confirm then update the test.** The test calls
`has_time_axis({})` (empty dict) and asserts `True`. But `has_time_axis`
(`src/pycat/utils/frame_interval.py:173`) deliberately returns `False` for an empty dict, and the
docstring + logic explain why (lines 180–191): *"No image loaded at all → NO. A warning that fires
where it cannot apply is how real warnings get trained away."* The nuanced current contract is:
- nothing loaded (no `file_metadata`, no `n_t`) → `False` (silent — nothing to warn about)
- image loaded, `n_t` unknown → `True` (loud)
- `n_t` known → `int(n_t) > 1`

`has_time_axis({})` = "nothing loaded" → `False` is CORRECT under this contract. The test asserts the
OLD contract ("unknown always loud, even with nothing loaded"). Someone tightened `has_time_axis` to
not fire on an empty session and didn't update this test.
**Action:** confirm via `git log -p src/pycat/utils/frame_interval.py` that the empty-session→False
behaviour was an intentional change (the docstring strongly implies it). If so, **update the test** to
pass a "loaded but unknown" repo — e.g. `has_time_axis({'file_metadata': {}})` should be `True`
(image loaded, `n_t` unknown → loud), and `has_time_axis({})` should be `False` (nothing loaded). Do
NOT revert `has_time_axis` — the empty-session-silence is the correct, deliberate behaviour. (Check
the sibling test `test_...214-frame movie...did NOT warn` still encodes the loaded-movie→loud case.)

---

## Summary
| failure | verdict | action |
|---|---|---|
| complexity_budget 147>139 | self-resolving tripwire | let #3/#5 decomposition shrink it; don't raise ceiling |
| image_reader_2d ×5 | code correct, test-env issue | get real traceback; fix monkeypatch/import, not the reader |
| unknown_time_axis loud | stale test vs deliberate code | update the test to the loaded-vs-empty contract; don't revert code |

Net: don't touch `read_2d_image_channels` or `has_time_axis` production logic on the strength of these
— verify with the real traceback + `git log`, then fix the tests / test-env. The only thing that
touches production is the complexity budget, and that clears as decomposition lands.
