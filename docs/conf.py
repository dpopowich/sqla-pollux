# Configuration file for the Sphinx documentation builder

# -- General configuration ------------------------------------------------
# If extensions (or modules to document with autodoc) are in another directory,
you can add these directories to sys.path here.  If the directory is relative to
the documentation root, use os.path.abspath to make it absolute, like shown here.

import os
import sys
sys.path.insert(0, os.path.abspath('../..'))

# -- Project information -----------------------------------------------------
project = 'sqla-pollux'
author = 'Your Name'
copyright = '2023, Your Name'

# -- General configuration ---------------------------------------------------
extensions = []

# Add any paths that contain templates here, relative to this directory.
templates_path = ['_templates']

# The suffix of source filenames.
suffix = '.rst'

# The master toctree document.
master_doc = 'index'

# -- Options for HTML output -------------------------------------------------
html_theme = 'alabaster'