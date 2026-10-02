"""Locate the data folders, both when run from source and as a built .exe.

Layout:
    data/models/<model name>/damper_data.csv      pressure-drop test data
    data/models/<model name>/free_area_data.csv   measured free areas (optional)
    data/models/<model name>/geometry.json        frame and blade dimensions
    data/models/<model name>/blade_table.csv      blades by section height (optional)
    data/models/<model name>/size_rules.json      size limits and sections (optional)

or, instead of those files, one Excel workbook:
    data/models/<model name>/model_data.xlsx      all of the above as sheets
"""

from __future__ import annotations

import sys
from pathlib import Path

DATA_FILE = "damper_data.csv"
FREE_AREA_FILE = "free_area_data.csv"
GEOMETRY_FILE = "geometry.json"


def app_dir() -> Path:
    """Folder containing the program.

    When built with PyInstaller, this is the folder holding the .exe, so the
    data folder can sit next to it and be edited without rebuilding.
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def default_models_dir() -> Path:
    return app_dir() / "data" / "models"

BLADE_TABLE_FILE = "blade_table.csv"
SIZE_RULES_FILE = "size_rules.json"
WORKBOOK_FILE = "model_data.xlsx"   # if present, the model is read from this workbook only
