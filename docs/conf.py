"""Sphinx configuration for conda-presto documentation."""

import os
import sys

sys.path.insert(0, os.path.abspath(".."))

project = "conda-presto"
html_title = "conda-presto"
copyright = "2026, conda community"
author = "conda community"

extensions = [
    "myst_parser",
    "sphinx_design",
    "sphinx_copybutton",
    "sphinxcontrib.mermaid",
]

myst_enable_extensions = [
    "colon_fence",
    "deflist",
    "fieldlist",
    "tasklist",
]

html_theme = "conda_sphinx_theme"
html_static_path = ["_static"]
html_css_files = ["css/custom.css"]

exclude_patterns = ["_build"]

suppress_warnings = ["myst.header"]
