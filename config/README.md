# Environment files

## The four conda lockfiles were DELETED in 1.6.0, and they had to be

`pycat-napari-env-arm-mac.yaml`, `pycat-devbio-napari-env-arm-mac.yaml`,
`pycat-napari-env-x86-windows.yaml`, `pycat-devbio-napari-env-x86-windows.yaml`

They were **exported conda lockfiles pinned to Python 3.9** — and PyCAT requires
**`>=3.12`**. ***They could not have worked.***

Worse, they pinned the entire frozen 2023 stack:

```
aicsimageio=4.10.0    numpy=1.23.5    tifffile=2023.2.28    lxml=4.9.4    fsspec=2024.3.1
```

**`aicsimageio` is exactly what 1.6.0 removed** — it is in maintenance mode, frozen in 2023, and
its pins (`zarr<2.16`, `tifffile<2023.3`, `fsspec<2023.9`, `lxml<5`) are **what held `numpy<2` and
`zarr<3` in place.**

So anyone following the README's `mamba env create -f ...` instruction would have built **the world
1.6.0 exists to escape** — with the old reader, the old pins, and a Python PyCAT does not support.

***And no performance report from such an environment would have been interpretable.***

## Install PyCAT the normal way

```bash
conda create -n pycat python=3.12 -y
conda activate pycat
pip install pycat-napari
```

For GPU (Cellpose), replace the CPU torch afterwards:

```bash
pip uninstall torch torchvision -y
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu118
```

## The single source of truth is `pyproject.toml`

`requirements-base.txt` and `meta.yaml` are generated from it and kept in step. **A dependency
declared in one place and not another is how an install route silently produces a different
environment** — which is what happened here.
