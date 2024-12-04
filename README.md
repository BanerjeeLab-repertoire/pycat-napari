# PyCAT-Napari

PyCAT (Python Condensate Analysis Toolbox) is an open-source application built on [napari](https://napari.org/) for analyzing biomolecular condensates in biological images. It provides a comprehensive suite of tools for fluorescence image analysis, particularly focused on condensate detection, measurement, and characterization.


## Table of Contents

- [Features](#features)
- [Installation](#installation)
  - [Using Pip](#using-pip)
  - [Platform-Specific Installations](#platform-specific-installations)
  - [Optional Features](#optional-features)
- [Quick Start](#quick-start)
  - [Launch the GUI](#launch-the-gui)
  - [Basic Python Usage](#basic-python-usage)
- [Documentation](#documentation)
- [Examples](#examples)
- [Notebooks](#notebooks)
- [Contributing](#contributing)
- [License](#license)
  - [Third-Party Libraries](#third-party-libraries)
- [Citation](#citation)
- [Support](#support)
- [Project Status](#project-status)
- [Acknowledgments](#acknowledgments)

### Documentation is in progress, please return soon for the completed docs. 


## Features

- **Interactive GUI**: Built on napari viewer for intuitive image visualization and analysis
- **Comprehensive Analysis Tools**:
  - Condensate detection and segmentation
  - Fluorescence intensity analysis
  - Feature extraction and measurement
  - Multi-channel analysis capabilities
- **Flexible Data Handling**:
  - Support for common microscopy file formats
  - Data export in standard formats
  - Integration with popular scientific Python libraries
- **Extensible Architecture**:
  - Python API for programmatic access
  - Integration with scientific workflows
  - Custom analysis pipeline creation

## Installation

PyCAT-Napari requires Python 3.9 or later.


### Using Pip
Install the package using `pip`:

```bash
pip install pycat-napari
```

### Platform-Specific Installations
To ensure functionality on different platforms, install as follows:

### Windows
```bash
pip install pycat-napari
```

### macOS (Intel)
```bash
pip install pycat-napari
```

### macOS (Apple Silicon/M1/ARM)
```bash
pip install pycat-napari[arm-mac]
```

### Optional Features

For development and additional features:
```bash
pip install pycat-napari[dev]      # Development tools
pip install pycat-napari[devbio]   # Additional bio-image analysis tools
```

## Quick Start

### Launch the GUI
After installation, launch the PyCAT-Napari GUI with:
```bash
run-pycat
```

### Basic Python Usage
Import PyCAT and utilize its analysis tools programmatically:
```python
import pycat

# Launch the GUI programmatically
from pycat import run_pycat_func
run_pycat_func()

# Use analysis tools programmatically
from pycat.analysis import analyze_condensates  # Example function
results = analyze_condensates(image_data)
```

## Documentation
### Full documentation is in progress please check back later for the updated docs...

Comprehensive documentation is available [here](https://github.com/BanerjeeLab-repertoire/pycat-napari/wiki). It includes:
- **Tutorial Guides**: Step-by-step instructions to get started.- API reference
- **API Reference**: Detailed descriptions of all available functions and classes.
- **Example Workflows**: Practical examples to demonstrate usage.
- **Advanced Usage Scenarios**: In-depth guides for specialized tasks.

## Examples
Example workflows to be updated soon...

## Notebooks
Interactive Jupyter notebooks are available in the `notebooks` directory:

### Analysis Notebooks
- **pycat_plotting.ipynb**: Comprehensive notebook for:
  - Reading and combining output dataframes
  - Generating various plots
  - Estimating saturation concentrations (C-sat) using our generalized ReLU function and custom fitting
  - Visualizing intensity-concentration relationships
  - Statistical analysis of condensate properties

### Data Processing Notebooks
- **int_truncated_dfs.ipynb**: Tools for:
  - Selecting specific intensity ranges
  - Filtering data based on custom parameters
  - Batch processing of multiple datasets

These notebooks provide examples that can be used as templates for your own analysi and data visualization workflows.

To use the notebooks:
- Install any other required packages imported in them, then test them on your own pycat-analyzed data. 

## Contributing

We welcome and encourage community contributions! Please see [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines on how to get started.

## License

PyCAT-Napari is licensed under the BSD 3-Clause License. See [LICENSE](LICENSE) for details.

### Third-Party Libraries
PyCAT-Napari builds upon several open-source libraries. See [THIRD_PARTY_LICENSES.txt](THIRD_PARTY_LICENSES.txt) for details.

## Citation

If you use PyCAT-Napari in your research, please cite:

```bibtex
@software{neureuter2024pycat,
  author = {Neureuter, Christian},
  title = {PyCAT-Napari: Python Condensate Analysis Toolbox},
  year = {2024},
  publisher = {GitHub},
  url = {https://github.com/BanerjeeLab-repertoire/pycat-napari}
}
```

## Support
For support, please check our [GitHub Issues](https://github.com/BanerjeeLab-repertoire/pycat-napari/issues) page. You can:

- Search existing issues to see if your question has already been answered
- Open a new issue to report bugs or request features
- Ask questions about usage and implementation


## Project Status

Current version: 1.0.0

For version history and updates, see [CHANGELOG.md](CHANGELOG.md).


## Acknowledgments

This project was developed by Christian Neureuter (https://github.com/cneureuter) in the Condensate Biophysics Lab (Banerjee Lab) at the State University of New York at Buffalo.

---



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


