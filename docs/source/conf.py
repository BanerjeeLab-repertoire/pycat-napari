# Configuration file for the Sphinx documentation builder.
# For the full list of built-in configuration values, see the documentation:
# https://www.sphinx-doc.org/en/master/usage/configuration.html

import os
import sys
#from datetime import datetime

# Add the project root directory to Python path
sys.path.insert(0, os.path.abspath('../../src'))

# -- Project information -----------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#project-information

project = 'PyCAT-Napari'
copyright = '2024, Christian Neureuter'
author = 'Christian Neureuter'
release = '1.0.0'

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
    'myst_parser',  # For markdown support
]

# -- Options for HTML output -------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#options-for-html-output

# html_theme = "sphinx_rtd_theme"
# html_static_path = ['_static']

# html_theme_options = {
#     "style_nav_header_background": "black",
#     'navigation_depth': 4,
#     'titles_only': False,
#     'logo_only': False,
# }

# Theme configuration
html_theme = 'pydata_sphinx_theme'
html_theme_options = {
    "show_nav_level": 2,
    "show_toc_level": 2,
    "navigation_with_keys": True,
    "icon_links": [
        {
            "name": "PyPI",
            "url": "https://pypi.org/project/pycat-napari",
            "icon": "fab fa-python",
        },
    ],
}

# Autosummary settings
autosummary_generate = True
add_module_names = False  # Remove module names from object titles

# Napoleon settings
napoleon_google_docstring = True
napoleon_numpy_docstring = True
napoleon_include_init_with_doc = False
napoleon_include_private_with_doc = False
napoleon_include_special_with_doc = False
napoleon_use_admonition_for_examples = True
napoleon_use_admonition_for_notes = True
napoleon_use_ivar = True
napoleon_use_param = True
napoleon_use_rtype = True

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

# AutoDoc settings
autodoc_default_options = {
    'members': True,
    'member-order': 'bysource',
    'special-members': '__init__',
    'undoc-members': True,
    'exclude-members': '__weakref__,__init__',
    'show-inheritance': True,
}

# Intersphinx configuration
intersphinx_mapping = {
    'python': ('https://docs.python.org/3.9', None),
    'numpy': ('https://numpy.org/doc/stable/', None),
    'scipy': ('https://docs.scipy.org/doc/scipy/', None),
    'pandas': ('https://pandas.pydata.org/docs/', None),
    'napari': ('https://napari.org/stable/', None),
}

# General configuration
templates_path = ['_templates']
exclude_patterns = []
source_suffix = {
    '.rst': 'restructuredtext',
    '.md': 'markdown',
}

# Hide "Built with Sphinx" footer
#html_show_sphinx = False