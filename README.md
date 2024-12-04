# PyCAT-Napari

PyCAT (Python Condensate Analysis Toolbox) is an open-source application built on napari for analyzing biomolecular condensates in biological images. It provides a comprehensive suite of tools for fluorescence image analysis, particularly focused on condensate detection, measurement, and characterization.


### Documentation is in progress, please return soon for the completed docs. 


## Features

- **Interactive GUI**: Built on napari viewer for intuitive image visualization and analysis
- **Comprehensive Analysis Tools**:
  - Condensate detection and segmentation
  - Fluorescence intensity analysis
  - Feature extraction and measurement
  - Multi-channel analysis capabilities
  - Batch processing support
- **Flexible Data Handling**:
  - Support for common microscopy file formats
  - Data export in standard formats
  - Integration with popular scientific Python libraries
- **Extensible Architecture**:
  - Python API for programmatic access
  - Integration with scientific workflows
  - Custom analysis pipeline creation

## Installation

PyCAT-Napari requires Python 3.9 or later. Installation is straightforward using pip:

```bash
pip install pycat-napari
```

For platform-specific installations:

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
```bash
run-pycat
```

### Basic Python Usage
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

Detailed documentation is available at [documentation link]. This includes:
- Tutorial guides
- API reference
- Example workflows
- Advanced usage scenarios

## Contributing

We welcome contributions! Please see [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

## License

PyCAT-Napari is licensed under the BSD 3-Clause License. See [LICENSE](LICENSE) for details.

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

## Third-Party Libraries

PyCAT-Napari builds upon several open-source libraries. See [THIRD_PARTY_LICENSES.txt](THIRD_PARTY_LICENSES.txt) for details.

## Project Status

Current version: 1.0.0

For version history and updates, see [CHANGELOG.md](CHANGELOG.md).

## Support

- GitHub Issues: Report bugs and request features
- Discussion Forum: [Link to forum/discussions]
- Email: [Your lab's contact email]

## Contributing

We welcome and ecourage community contributions! Please see [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines on how to get started.

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


