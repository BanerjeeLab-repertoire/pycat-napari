# Real-data corpus

The files are **not in git**. `registry.txt` is: one line per file — name, SHA-256, size, the pipeline it
exercises, and its provenance (source file, every transform applied, and the acquisition facts the file could
not supply, recorded as `unknown`).

| task | command |
|---|---|
| build the corpus from the lab's Box test folder | `python tools/build_corpus.py` (writes `~/.cache/pycat/corpus/v1`, or `$PYCAT_CORPUS_DIR`) |
| run every pipeline × route and write the matrix | `python tools/route_matrix.py` → `docs/pipeline_route_matrix.md` |
| re-pin the golden masters after a deliberate change | `python tools/route_matrix.py --repin` (and say why in the CHANGELOG) |
| run it as tests | `pytest -m realdata` (or `PYCAT_REALDATA=1 pytest …`) |

`fetch.py` looks in `$PYCAT_CORPUS_DIR`, then the cache, then downloads from `$PYCAT_CORPUS_URL` (a release-asset
base URL) and refuses any file whose hash differs from the registry. Without a corpus the tests skip.

Pipelines with no real acquisition in the source folder (cellular brightfield, the in-vitro time series, any
z-stack) are registered as `MISSING`, so the matrix reports `no data` for them rather than a blank. Masks drawn by
hand are loose on boundaries: count metrics may be asserted, boundary metrics (IoU, Dice) are recorded only.
