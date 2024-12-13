# PyCAT-Napari
![License](https://img.shields.io/github/license/BanerjeeLab-repertoire/pycat-napari)
![PyPI](https://img.shields.io/pypi/v/pycat-napari)
![Python Version](https://img.shields.io/pypi/pyversions/pycat-napari)
![Downloads](https://img.shields.io/pypi/dm/pycat-napari)
![GitHub Stars](https://img.shields.io/github/stars/BanerjeeLab-repertoire/pycat-napari)
![GitHub Forks](https://img.shields.io/github/forks/BanerjeeLab-repertoire/pycat-napari)

![PyCAT Logo](src/pycat/icons/pycat_logo_512.png)

PyCAT (Python Condensate Analysis Toolbox) is an open-source application built on [napari](https://napari.org/) for analyzing biomolecular condensates in biological images. It provides a comprehensive suite of tools for fluorescence image analysis, particularly focused on condensate detection, measurement, and characterization. PyCAT aims to provide a low/no-code solution, accessible to researchers with varying levels of programming experience.

## Table of Contents

- [Features](#features)
- [Quick Start](#quick-start)
- [System Requirements](#system-requirements)
- [Installation](#installation)
- [Getting Started](#getting-started)
- [Examples](#examples)
- [Documentation](#documentation)
- [Contributing](#contributing)
- [License](#license)
- [Support](#support)

## Features

PyCAT-Napari provides comprehensive tools for biological image analysis:

| Feature Category | Capabilities                                       | Requirements        |
|------------------|----------------------------------------------------|---------------------|
| Image Processing | - Condensate detection and segmentation<br>- Fluorescence intensity analysis<br>- Multi-channel analysis | Basic               |
| Data Analysis    | - Feature extraction<br>- Measurement tools<br>- Statistical analysis | Basic               |
| Data Export      | - Dataframe CSV export<br>- Excel compatibility<br>- Figure generation | Basic               |
| Advanced Tools   | - Additional bio-image analysis tools<br>- Extended functionality | `[devbio-napari]` extra |

*`[devbio-napari] extra` refers to optional dependencies installed with `pip install "pycat-napari[devbio-napari]"` for enhanced functionality.*

## System Requirements

### Compatibility Matrix

| Platform      | Python | Status    | Notes                    |
|--------------|---------|-----------|--------------------------|
| Windows 10/11 | 3.9     | Tested    | Logo display issue      |
| Mac M1/ARM   | 3.9     | Tested    | Requires specific torch |
| Mac Intel    | 3.9     | Untested* | Should work            |
| Linux        | 3.9     | Untested* | Should work            |

*While untested, these platforms should work with standard installation.

### Minimum Requirements
- **Python Version**: 3.9.x (Required)
  > ⚠️ **Important**: PyCAT-Napari is currently only compatible with Python 3.9. Other versions are not supported in this release. Future releases may aim to expand to more versions. 
- RAM: 8GB (16GB recommended)
- Disk Space: ~2GB (including dependencies)
- GPU: Not required (CPU-only processing)

## Getting Started

PyCAT requires Python 3.9 and a package/environment manager. We recommend using Mambaforge for package and environment management, but we include instructions for alternative methods.  Before installing PyCAT-Napari, follow this quick assessment to determine your setup needs:

### Initial Setup Check

#### 1. Do you have Python installed?

Check Python Installation

Run this command in your terminal (mac)/command prompt (anaconda prompt)/powershell(windows):
```bash
python --version
```

If you get a version number: ✅ You have Python installed

If you get an error: ❌ See [Python Installation Guide](#python-installation)

#### 2. Do you have Conda or Mamba installed?

Check Your Environment Manager

```bash
conda --version
# or
mamba --version
```

If you get a version number: ✅ Proceed to installation

If you get an error: ❌ See [Package Manager Installation](#package-manager-installation)

#### 3. Are you familiar with Python environments?

If yes: ✅ Proceed to [Installation](#installation)

If no: ❌ Read our quick environment guide below

**Python Package and Environments Info**

Think of environments like separate containers for different projects - they help avoid conflicts and keep things organized. Don't worry, they're simpler than they sound!

Python environments help you:
- Keep projects separate
- Avoid version conflicts
- Ensure reproducibility

Package Manager Choice
> 💡 **Why Mambaforge?**  
> Mambaforge is a lightweight distribution of Conda, offering faster package installation and fewer pre-installed packages.  
> **Key Advantages:**  
> - Quicker dependency resolution  
> - Minimal initial install (no unnecessary extras)  
> - Fully compatible with conda commands (just use `mamba` in place of `conda`)

Already have Anaconda? That's fine! You can skip the Mambaforge installation and use your existing setup.

Basic environment commands:
```bash
# Create new environment with Python 3.9
conda create -n pycat-env python=3.9

# Activate the environment
conda activate pycat-env

# Verify you're in the right environment
python --version  # Should show Python 3.9.x
```

## Installation

### Basic Installation

Create and activate a new environment:
```bash
# Create environment
conda create -n pycat-env python=3.9

# Activate environment
conda activate pycat-env
```

#### Windows
```bash
pip install pycat-napari
```
Note: On Windows, due to some platform-specific rendering quirks, the application logo may not display correctly. This is purely cosmetic and does not affect functionality.

#### Mac M1/ARM
```bash
pip install "pycat-napari[arm-mac]"
```

### Optional Features

You can install PyCAt with additional tools, features, and packages. For example, dev, test, and doc tools. The devbio-napari package adds numerous additional image analysis tools. Learn more at [devbio-napari documentation](https://github.com/haesleinhuepf/devbio-napari).

```bash
# Development tools
pip install "pycat-napari[dev]"

# Additional bio-image analysis tools (recommended)
pip install "pycat-napari[devbio-napari]"
```
> 💡 **Tip**: You can designate multiple optional dependencies by separating them with a comma
   ```bash
   # Install dev tools on an ARM Mac
   pip install "pycat-napari[arm-mac, dev]"
   ```


### Alternative Installation Methods

If you encounter issues with the standard installation, use our tested environment files located in the config/ folder. We provide complete environment files that match our development package setup (no dev tools installed though, please install those separately if youre trying to install a dev version for a fork or pull request) to provide you with the same environment we developed and ran in. 

```bash
# Windows
mamba env create -f pycat-devbio-napari-env-x86-windows.yml

# Mac M1/ARM
mamba env create -f pycat-devbio-napari-env-arm-mac.yml
```

### Verifying Installation & Optional Testing 

After installation, verify PyCAT-Napari is working correctly:

#### 1. Basic Checks
```bash
# Activate your environment
conda activate pycat-env

# Verify Python version
python --version  # Should show 3.9.x

# Test basic import
python -c "import pycat; print('PyCAT import successful!')"

# Launch GUI (basic smoke test)
run-pycat  # Should open the GUI window
```

If you encounter any failures, check:

1. Python version (must be 3.9.x)
2. Environment activation
3. Complete installation of dependencies
4. Troubleshooting Guide
5. Check the issues 

Still having problems installing or running the program? Open a github issue. If you need urgent help, reach out to us and we will try to get back to you as soon as possible. 

## 🛠️ Usage

PyCAT-Napari offers two ways to analyze your data: through a user-friendly GUI or programmatically via Python code. PyCAT was developed as a low/no-code solution to image analysis so usage of the GUI is recommedned. API usage has not been thoroughly tested however many core functions are modular and should work via API. 

### GUI Application

![PyCAT main interface with key areas labeled](./assets/screenshots/Viewer_and_menu_highlights.png)

Launch PyCAT's graphical interface with:
```bash
run-pycat
```

A blank Napari viewer with added menu items on the right will open up for you. In the image above the added menus have been expanded and color coded.
- 🟦 **Analysis Methods** - provides pre-made pipelines offering tools and outputs depending on the given method that you choose.
- 🟩 **Toolbox** - is a menu full of all of the individual functions and tools in PyCAT, for novel algorithm experimentation and analysis workflow customization.
- 🟥 **Open/Save File(s)** - handles image and data input/output for PyCAT, using AICSImageIO to read various microscope and metadata formats, and storing the information in PyCAT’s internal data structure.
   - Note: you must use this and not the integrated Napari IO under the typical file open file save or the integrated drag and drop feature since they are not integrated with PyCATs internal data structure. 

PyCAT-Napari integrates seamlessly with the Napari interface, providing users with a powerful and intuitive environment for image analysis. Napari's interface is designed to be user-friendly, resembling popular pixel or raster photo editors like MSPaint or Photoshop. So, if you've ever used a photo editor, the tools should be simple enough to acclimate to. 

- 🟨 **Layer Tools** - where users can easily add or remove various layers such as images, shapes, and labels from the viewer. This feature allows for quick management of the visual elements, including the ability to hide or show layers using the eye icon.
- 🟪 **Shape and Label Tools** - which include node tools for manipulating shape layers, as well as paint brush, eraser, and bucket tools for label layers. Users can also apply colormaps to images and change opacity to view overlapping images. 






#### Basic GUI Workflow 

Once you have the application open, choose your analysis method from the menu. This populates the dock with a pre made analysis pipeline, even if you're doing your own algorithmic exploration, it is recomended to use general analysis for more robust integration with the internal PyCAT data strcutre. 

PyCAT excels at in-cellulo nuclear condensate analysis. An example pair of images is included in the folder assets/example analysis images/. The following is a basic example of a `Condensate Analysis` with this data. For a more comprehensive walkthrough of this example, please see our [API Documentation](https://pycat-napari.readthedocs.io/en/latest/). 

**Load Data**

   Open/Save File(s)
   - Click `Open/Save File(s) > Open 2D Image(s)`
      - Note: you must use this and not the integrated Napari IO under the typical file open file save or the integrated drag and drop feature since they are not integrated with PyCATs internal data structure. 
      - Supported formats: TIFF, CZI, PNG, JPG
   - Multiple files can be loaded simultaneously, multi-channel images or multiple selected files will be added the the viewer as separate layers
   - Assign names to each channel in a dialog box for easier layer tracking
   - In addition to the images, 2 shapes layers will be added for measuring object sizes

![PyCAT main interface with image loaded](./assets/screenshots/opened_image_in_viewer.png)

**View, Process, and analyze images** 

1. **Draw Measurement Lines**
   - Draw lines across characteristic objects on the shapes layers:
     - **Cell Diameter**: For cell or nuclei diameters (or if in-vitro ~size of background features)
     - **Object Diameter**: For condensate or subcellular object diameters
   - Click "Measure Lines" to calculate diameters in both pixels and microns (if metadata is available).

![PyCAT object measurement layers](./assets/screenshots/measuring_lines.png)

   **Upscale Images (Optional)**
   - If you are upscaling your images, you can multi-select the layers in the viewer and then click `Run Upscaling` button
      - Upscaling can be useful for segmentation and pre-processing algorithms, however, it can also introduce noise artifcats, and should be considered appropriately and applied consistently. 

2. **Preprocess Images**
- Preprocessing operates on the active image layer (blue highlighted layer in layers panel on the left side)
   - In the example, we do this on the GFP image
   - Preprocessing steps include:
     - White top-hat filtering
     - Laplacian of Gaussian enhancement
     - Wavelet-based noise reduction
     - Gaussian smoothing
     - Contrast-limited adaptive histogram equalization (CLAHE)

4. **Background Removal**
- Background removal operates on the active image layer (blue highlighted layer in layers panel on the left side)
- In the example, we do this on the GFP image
   - Background removal consists of:
     - Rolling ball background removal
     - Gaussian background subtraction and division
     - Gabor filtering

![PyCAT preprocessed and bacground removed image](./assets/screenshots/preprocessed_images.png)

5. **Primary Mask Generation**
   - Use Cellpose or Random Forest for cell/nuclei segmentation:
     - Select the primary object image (DAPI, Hoechst, etc.) for segmentation in the respective dropdown.
     - Click `Run Cellpose`
     - See full walkthrough for example of RF classifier

6. **Cell/Nuclei Analysis**
   - Measure various properties of the primary object mask
      - In the example, we do this on the GFP image (we always measure off of the unaltered image or in the case of this example the unaltered, upscaled, image)
   - **Optional Mask to exclude**
      - Create a blank labels layer to mark structures to omit (e.g., nucleoli, cytoplasm, etc.).
   - Select the primary mask, objects to omit (optionally), and the image to measure on.
   - Click "Run Cell Analyzer."

![PyCAT cell segmentation and analysis](./assets/screenshots/cell_analyzer.png)

7. **Condensate Segmentation**
   - Choose the most processed image for segmentation and the unaltered image (in this examplke, the upscaled gfp image) to measure from, in the respective dropdowns
   - Click "Run Condensate Segmentation."
   - Two masks are generated:
     - **Total Puncta Mask**: Over-segmented, unfiltered result.
     - **Total Refined Puncta Mask**: Object-filtered for balanced accuracy.

8. **Condensate Analysis**
   - Choose a mask and make any final manual tweaks.
   - Select the measurement image (in this examplke, the upscaled gfp image) and click "Run Condensate Analyzer."
   - Outputs include:
     - **Cell Data Frame**: Individual cell/nuclei/primary mask metrics
     - **Puncta Data Frame**: Metrics for individual condensates or subcellular objects
   - Visualization layers:
     - Labeled puncta mask for each cell (where the objects share the same label as their parent cell)
     - Side-by-side image with raw and segmentation overlay for domstrative purposes

![PyCAT condensate segmentation](./assets/screenshots/condensate_segmentation.png)


**Exporting Data**

   Save Results and Clear the Viewer
   - To export analyzed images, data, and masks, navigate to `Open/Save File(s) > Save and Clear`
   - Select from all active layers, and internal dataframes to export:
     - Images as .tiff
     - Masks as .png
     - Data Frames (cell and/or puncta) as .csv
   - Choose between "Clear Only Saved" or "Clear All" to reset PyCAT for the next analysis.

![PyCAT condensate segmentation](./assets/screenshots/save_and_clear_popup.png)



### Programmatic API

For automated analysis or integration into existing workflows:

```python
# Launch the GUI programmatically
from pycat import run_pycat_func
run_pycat_func()

# or 

# Use processing tools programmatically
import numpy as np
from pycat.toolbox.image_processing_tools import apply_rescale_intensity

# Load your image (using your preferred method)
image = np.array([...])  # Your image data

# Process the image
# Rescale intensity to full range of image's data type
processed_image = apply_rescale_intensity(image)

# Or specify custom intensity range
processed_image = apply_rescale_intensity(
    image,
    out_min=0,      # Minimum intensity value
    out_max=65535   # Maximum intensity value (e.g., for 16-bit image)
)
```

> 💡 **Tip**: PyCAT is designed primarily as a low/no-code solution for image analysis, making the GUI the recommended interface for most users. While the API offers modular access to core functions, it hasn't been extensively tested, so users should proceed with caution when integrating it into programmatic workflows. Running the PyCAT GUI should not be done in jupyter notebooks as there are PyQT and UI related issues that can cause downstream bugs. 


## 📘 Documentation

> 🚧 Full documentation is in progress. Please check back soon for complete docs!

For detailed API documentation, see our - [API Documentation](https://pycat-napari.readthedocs.io/en/latest/).

Current documentation includes:

### Reference Materials


## 📓 Notebooks

These notebooks included are exeamples of how to read, combine, and compare data output by PyCAT. They are for coding and methodology examples and are not as structured, documented, or tested as the main PyCAt application, but we thought they would be more useful than not.

### Analysis Examples
- **pycat_plotting.ipynb**
  - Loading and combining of output dataframes from multiple subfolders
  - Generate scatter plots for multiple datasets
  - Estimate saturation concentrations (C-sat) by boud, constrained, fitting of a generalized ReLU function parameterized by the x_0 intercept
  - Add interactive data cursors identifying plot points to files and cells
  - Create plots with customizable parameters

### Data Processing
- **int_truncated_dfs.ipynb**
  - Filter datasets by intensity ranges
  - Process cell and puncta dataframes
  - Combine CSV files from multiple directories
  - Generate truncated datasets based on custom parameters
  - Export filtered results for further analysis

## 💻 Development

### Setting Up Development Environment

1. Clone the repository:
```bash
git clone https://github.com/BanerjeeLab-repertoire/pycat-napari.git
cd pycat-napari
```

2. Create development environment:
```bash
# Windows
conda env create -f pycat-devbio-napari-env-x86-windows.yml

# Mac M1/ARM
conda env create -f pycat-devbio-napari-env-arm-mac.yml
```

3. Install development dependencies:
```bash
pip install -e ".[dev]"
```

### Running Tests
```bash
# Install test dependencies
pip install -e ".[test]"

# Run tests with coverage
pytest
```

## 🤝 Contributing

We welcome contributions! See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

Key areas for contribution:
- Bug fixes and feature improvements
- Documentation and examples
- Test coverage expansion
- Platform compatibility testing

## 📄 License

PyCAT-Napari is licensed under the BSD 3-Clause License. See [LICENSE](LICENSE) for details.

### Third-Party Libraries
See [THIRD_PARTY_LICENSES.txt](THIRD_PARTY_LICENSES.txt) for details about dependencies.

## 📚 Citation

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

## 🆘 Support & Troubleshooting

### Common Issues

1. **Installation Problems**
   - Verify Python 3.9 installation
   - Check platform-specific requirements
   - Use provided environment files

2. **GUI Issues**
   - Update graphics drivers
   - Check PyQt5 installation
   - Verify napari compatibility

3. **Analysis Errors**
   - Confirm input file format
   - Check memory availability
   - Verify parameter ranges

### Getting Help

- Search [existing issues](https://github.com/BanerjeeLab-repertoire/pycat-napari/issues)
- Open a [new issue](https://github.com/BanerjeeLab-repertoire/pycat-napari/issues/new)
- Join our [discussion forum](link-to-forum)

## 🔄 Project Status & Roadmap

Current Version: 1.0.0

### Recent Updates
See [CHANGELOG.md](CHANGELOG.md) for detailed version history.

### Roadmap
- Extended file format support (including migration to BioIO) and integration with native napari IO
- GPU acceleration/parallelization, and multi-threading, e.g. performance optimizations
- 3D, Z-stack, time series support
- Expanded analysis methods and more individual tools
- ML classifiers and segmentation models trained on annotated data output by PyCAT

## 🙏 Acknowledgments

This project was developed by Christian Neureuter in the Condensate Biophysics Lab (Banerjee Lab) at SUNY Buffalo.

### Key Dependencies
- [napari](https://napari.org/) - Image visualization
- [scikit-image](https://scikit-image.org/) - Image processing
- [numpy](https://numpy.org/) - Numerical computing
- [pandas](https://pandas.pydata.org/) - Data analysis

### Special Thanks
- Banerjee Lab members for testing and feedback
- napari community for viewer framework
- Open source community for supporting libraries

For additional details, troubleshooting, and advanced features, see our [full documentation](https://pycat-napari.readthedocs.io/en/latest/).
