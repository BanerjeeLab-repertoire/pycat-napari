## Installation

### Using Pip

For minimal installation:

```bash
pip install -r requirements-base.txt
pip install .
```

For full installation with plugins:

```bash
pip install -r requirements-devbio-napari.txt
pip install .
```

For Mac-specific dependencies (ARM/Apple Silicon/M1, M2 etc.):

```bash
pip install -r requirements-arm-mac.txt
# or
pip install -r requirements-devbio-napari-arm-mac.txt
```

Unfortunately, I do not have access to an Intel based Mac and therefore cannot provide specific requirements for that platform. 

For Mac (Apple Silicon) and Windows, you can create an environment using the provided YAML files:

```bash
conda env create -f pycat-devbio-napari-env-arm-mac.yml  # For Mac (Apple Silicon)
conda env create -f pycat-devbio-napari-env-x86-windows.yml  # For Windows
```


