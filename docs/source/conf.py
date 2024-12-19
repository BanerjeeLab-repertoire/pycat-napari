# Configuration file for the Sphinx documentation builder.
# For the full list of built-in configuration values, see the documentation:
# https://www.sphinx-doc.org/en/master/usage/configuration.html

import os
import sys
from datetime import datetime

# Add the project root directory to Python path
sys.path.insert(0, os.path.abspath('../../src'))

# -- Project information -----------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#project-information

project = 'PyCAT-Napari'
copyright = f'{datetime.now().year}, Christian Neureuter'
author = 'Christian Neureuter'
release = '1.0.0'

# Get version from package
with open("../../src/pycat/__init__.py") as f:
    for line in f:
        if line.startswith('__version__'):
            release = line.strip().split('=')[1].strip(' \'"')
            break
version = release

# -- General configuration ---------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#general-configuration

# Extensions configuration
extensions = [
    'sphinx.ext.autodoc',
    'sphinx.ext.napoleon',
    'sphinx.ext.viewcode',
    'sphinx.ext.githubpages',
    'sphinx.ext.autosummary',
    'sphinx.ext.intersphinx',
    'sphinx_autodoc_typehints',
    'sphinx_copybutton',  # Added for code block copy button
    'numpydoc',          # Added for better NumPy-style docstring support
    'myst_parser',        # Added for markdown support
]

# -- Options for HTML output -------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#options-for-html-output


# Theme configuration
html_theme = 'pydata_sphinx_theme'
html_logo = "_static/pycat_logo_512.png"
html_favicon = "_static/pycat_logo_512.png"
html_static_path = ['_static']
# html_theme_options = {
#     "show_nav_level": 2,
#     "show_toc_level": 2,
#     "navigation_with_keys": True,
#     "icon_links": [
#         {
#             "name": "PyPI",
#             "url": "https://pypi.org/project/pycat-napari",
#             "icon": "fab fa-python",
#         },
#     ],
# }
html_theme_options = {
    "logo": {
        "alt_text": "PyCAT-Napari logo",
        "text": "PyCAT-Napari",
        "link": "https://pycat-napari.readthedocs.io",
    },
    "icon_links": [
        {
            "name": "GitHub",
            "url": "https://github.com/BanerjeeLab-repertoire/pycat-napari",
            "icon": "fa-brands fa-github",
        },
        {
            "name": "PyPI",
            "url": "https://pypi.org/project/pycat-napari",
            "icon": "fa-solid fa-box",
        },
    ],
    "show_nav_level": 2,
    "show_toc_level": 3,
    "navigation_with_keys": True,
    "navbar_align": "left",
    #"navbar_end": ["version-switcher", "navbar-icon-links"],
    "show_prev_next": False,

    # Items at start of navbar (left side)
    "navbar_start": ["navbar-logo"],

    # "navbar_center": [
    #     {"name": "Installation", "url": "installation"},
    #     {"name": "Usage", "url": "usage/index"},
    #     {"name": "API", "url": "api/index"},
    #     {"name": "Development", "url": "development/index"},
    # ],
    
    # Items in center of navbar
    "navbar_center": ["navbar-nav"], 
    
    # Items at end of navbar (right side)
    "navbar_end": ["navbar-icon-links", "version-switcher"],
    
}

# -- Extension configurations -----------------------------------------------

# Autosummary settings
autosummary_generate = True
autosummary_imported_members = True
add_module_names = False  # Remove module names from object titles

autodoc_default_options = {
    'members': True,
    'member-order': 'groupwise',
    'special-members': '__init__',
    'undoc-members': True,
    'exclude-members': '__weakref__',
    'show-inheritance': True,
}

# Napoleon settings
# napoleon_google_docstring = True
# napoleon_numpy_docstring = True
# napoleon_include_init_with_doc = False
# napoleon_include_private_with_doc = False
# napoleon_include_special_with_doc = False
# napoleon_use_admonition_for_examples = True
# napoleon_use_admonition_for_notes = True
# napoleon_use_ivar = True
# napoleon_use_param = True
# napoleon_use_rtype = True

napoleon_google_docstring = True
napoleon_numpy_docstring = True
napoleon_include_init_with_doc = False
napoleon_include_private_with_doc = False
napoleon_include_special_with_doc = False
napoleon_use_admonition_for_examples = True
napoleon_use_admonition_for_notes = True
napoleon_use_param = True
napoleon_use_rtype = True

# Numpydoc settings
numpydoc_show_class_members = False
numpydoc_class_members_toctree = False

# Intersphinx configuration
intersphinx_mapping = {
    'python': ('https://docs.python.org/3.9', None),
    'numpy': ('https://numpy.org/doc/stable/', None),
    'scipy': ('https://docs.scipy.org/doc/scipy/', None),
    'pandas': ('https://pandas.pydata.org/docs/', None),
    'napari': ('https://napari.org/stable/', None),
    'scikit-image': ('https://scikit-image.org/docs/stable/', None),
}


# General configuration
templates_path = ['_templates']
exclude_patterns = []
source_suffix = {
    '.rst': 'restructuredtext',
    '.md': 'markdown',
}

# Add custom CSS for dark mode
# def setup(app):
#     app.add_css_file("dark_mode.css")


# General configuration
templates_path = ['_templates']
exclude_patterns = []
source_suffix = {
    '.rst': 'restructuredtext',
    '.md': 'markdown',
}

# Hide "Built with Sphinx" footer
#html_show_sphinx = False