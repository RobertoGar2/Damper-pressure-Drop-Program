"""Load damper test data, fit loss coefficients, and estimate pressure drop.

How the model works
-------------------
1. Per-size fit: for every damper size in the test data, the face
   velocities are converted to free-area velocities, and a loss coefficient
   C_free is fitted by least squares through the origin:

       dP = C_free * (V_free / 4005)^2

2. Size model: C_free changes with size because the frame and blades block
   a larger share of small dampers. With 3 or more tested sizes, C_free is
   fitted as a straight line against the free-area ratio (free / face).
   With fewer sizes, the average C_free is used.

3. Estimate: a tested size uses its own fitted C_free. Any other size uses
   the size model. Pressure drop is then corrected for air density.

Damper models
-------------
Each damper model (product line) has its own folder under data/models with
its own test data, free-area table and geometry. ModelLibrary loads them all.

Free area
---------
The free area of a size comes from, in order of priority:
  1. a free_area_ft2 value in the test-data file,
  2. the model's measured free-area table (free_area_data.csv),
  3. the model's geometry estimate (geometry.json).
calibrate_geometry() fits the geometry settings to the measured table, so
sizes that are not in the table get a free area consistent with it.
"""

from __future__ import annotations

import csv
import json
import math
import re
import shutil
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np

from . import physics as ph
from .errors import DataError, ModelError
from .paths import (
    BLADE_TABLE_FILE, DATA_FILE, FREE_AREA_FILE, GEOMETRY_FILE, SIZE_RULES_FILE, WORKBOOK_FILE,
)
from . import workbook as xl

REQUIRED_COLUMNS = ("width_in", "height_in", "face_velocity_fpm", "pressure_drop_in_wg")
BLADE_TABLE_COLUMNS = ("min_height_in", "max_height_in", "blades")
OPTIONAL_COLUMNS = ("free_area_ft2",)
FREE_AREA_COLUMNS = ("width_in", "height_in", "free_area_ft2")
SIZE_TOLERANCE_IN = 0.01    # sizes within this are treated as the same size
PA_PER_IN_WG = 249.089


# ---------------------------------------------------------------------------
# Reading tables (CSV files and Excel sheets share the same parsers)
# ---------------------------------------------------------------------------
#
# A table is (col, rows): col maps lower-case column names to positions, and
# each row is (row_number, location_text, [cell text, ...]). location_text is
# used in error messages, e.g. "damper_data.csv, line 14" or
# "model_data.xlsx [Test data], row 14".

@dataclass
class DataPoint:
    """One row of test data."""

    width_in: float
    height_in: float
    face_velocity_fpm: float
    pressure_drop_in_wg: float
    free_area_ft2: float | None = None
    line_no: int = 0


def _read_csv(path, required_columns):
    """Read a CSV, skipping blank and '#' lines."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")

    with path.open(newline="", encoding="utf-8-sig") as f:
        numbered = [
            (n, line) for n, line in enumerate(f, start=1)
            if line.strip() and not line.lstrip().startswith("#")
        ]
    if not numbered:
        raise DataError(f"{path.name} contains no data.")

    reader = csv.reader(line for _, line in numbered)
    header = [h.strip().lower() for h in next(reader)]
    missing = [c for c in required_columns if c not in header]
    if missing:
        raise DataError(
            f"{path.name} is missing required column(s): {', '.join(missing)}.\n"
            f"Required columns: {', '.join(required_columns)}"
        )
    col = {name: i for i, name in enumerate(header)}
    rows = [
        (line_no, f"{path.name}, line {line_no}", values)
        for (line_no, _), values in zip(numbered[1:], reader)
        if any(v.strip() for v in values)
    ]
    return col, rows


def _number(loc, col, values, name, required=True, allow_zero=False):
    """Read one numeric cell with a helpful error message."""
    i = col.get(name)
    text = values[i].strip() if i is not None and i < len(values) else ""
    if not text:
        if required:
            raise DataError(f"{loc}: '{name}' is blank.")
        return None
    try:
        value = float(text)
    except ValueError:
        raise DataError(f"{loc}: '{name}' must be a number, got '{text}'.") from None
    if value < 0 or (value == 0 and not allow_zero):
        raise DataError(f"{loc}: '{name}' must be greater than zero.")
    return value


def parse_points(col, rows, source="test data") -> list[DataPoint]:
    points = []
    for line_no, loc, values in rows:
        def num(name, **kw):
            return _number(loc, col, values, name, **kw)

        points.append(DataPoint(
            width_in=num("width_in"),
            height_in=num("height_in"),
            face_velocity_fpm=num("face_velocity_fpm"),
            pressure_drop_in_wg=num("pressure_drop_in_wg", allow_zero=True),
            free_area_ft2=num("free_area_ft2", required=False),
            line_no=line_no,
        ))
    if not points:
        raise DataError(f"The {source} has a header but no data rows.")
    return points


def load_points(path) -> list[DataPoint]:
    """Read pressure-drop test data from a CSV file.

    Required columns: width_in, height_in, face_velocity_fpm,
    pressure_drop_in_wg. Optional: free_area_ft2. Blank lines and lines
    starting with '#' are ignored, so the file can hold notes.
    """
    return parse_points(*_read_csv(path, REQUIRED_COLUMNS), source=Path(path).name)


@dataclass
class FreeAreaEntry:
    """One measured free area from the free-area table."""

    width_in: float
    height_in: float
    free_area_ft2: float
    line_no: int = 0

    @property
    def label(self) -> str:
        return f"{self.width_in:g} x {self.height_in:g}"

    def matches(self, width_in: float, height_in: float) -> bool:
        return (abs(self.width_in - width_in) <= SIZE_TOLERANCE_IN
                and abs(self.height_in - height_in) <= SIZE_TOLERANCE_IN)


def parse_free_area_table(col, rows) -> list[FreeAreaEntry]:
    entries: list[FreeAreaEntry] = []
    for line_no, loc, values in rows:
        def num(n, **kw):
            return _number(loc, col, values, n, **kw)

        e = FreeAreaEntry(num("width_in"), num("height_in"), num("free_area_ft2"), line_no)
        if e.free_area_ft2 >= ph.face_area_ft2(e.width_in, e.height_in):
            raise DataError(
                f"{loc}: free area {e.free_area_ft2:g} ft\u00b2 must be "
                f"smaller than the {e.label} face area."
            )
        for other in entries:
            if other.matches(e.width_in, e.height_in):
                raise DataError(
                    f"{loc}: size {e.label} is listed more than once "
                    f"(also on line/row {other.line_no})."
                )
        entries.append(e)
    return entries


def load_free_area_table(path) -> list[FreeAreaEntry]:
    """Read measured free areas (width_in, height_in, free_area_ft2).

    Returns an empty list if the file does not exist, since the table is
    optional.
    """
    if path is None or not Path(path).exists():
        return []
    return parse_free_area_table(*_read_csv(path, FREE_AREA_COLUMNS))


def parse_blade_table(col, rows, source="blade table") -> ph.BladeTable:
    rules = []
    for _, loc, values in rows:
        def num(n):
            return _number(loc, col, values, n)
        blades = num("blades")
        if blades != int(blades):
            raise DataError(f"{loc}: 'blades' must be a whole number.")
        rules.append(ph.BladeRule(num("min_height_in"), num("max_height_in"), int(blades)))
    table = ph.BladeTable(rules)
    try:
        table.validate()
    except ValueError as e:
        raise DataError(f"{source}: {e}") from None
    return table


def load_blade_table(path) -> ph.BladeTable:
    """Read blades by section height. A missing or empty file gives an empty table."""
    if path is None or not Path(path).exists():
        return ph.BladeTable()
    text = Path(path).read_text(encoding="utf-8-sig")
    if not any(ln.strip() and not ln.lstrip().startswith("#") for ln in text.splitlines()):
        return ph.BladeTable()
    return parse_blade_table(*_read_csv(path, BLADE_TABLE_COLUMNS), source=Path(path).name)


_NO_LIMIT_WORDS = {"", "none", "no limit", "null", "-", "n/a"}


def settings_from_pairs(pairs, source) -> dict:
    """Turn (key, value text, location) pairs from a settings sheet into a dict.

    Blank values and words like "none" or "no limit" become None.
    """
    data = {}
    for key, text, loc in pairs:
        key = key.strip().lower()
        if key in data:
            raise DataError(f"{loc}: '{key}' is listed more than once.")
        t = (text or "").strip()
        if t.lower() in _NO_LIMIT_WORDS:
            data[key] = None
            continue
        if key == "description":
            data[key] = t
            continue
        try:
            data[key] = float(t)
        except ValueError:
            raise DataError(f"{loc}: '{key}' must be a number, got '{t}'.") from None
    return data


def geometry_from_settings(data: dict, source: str) -> tuple[ph.DamperGeometry, str]:
    """Geometry and description from a settings dict. Blank values keep the default."""
    description = data.get("description") or ""
    values = {k: v for k, v in data.items() if k != "description" and v is not None}
    try:
        geometry = ph.DamperGeometry.from_dict(values)
        geometry.validate()
    except ValueError as e:
        raise DataError(f"{source}: {e}") from None
    return geometry, description


def rules_from_settings(data: dict, source: str) -> ph.SizeRules:
    try:
        rules = ph.SizeRules.from_dict(data)
        rules.validate()
    except ValueError as e:
        raise DataError(f"{source}: {e}") from None
    return rules


def load_geometry(path) -> tuple[ph.DamperGeometry, str]:
    """Read geometry settings from JSON. Falls back to defaults if missing."""
    if path is None or not Path(path).exists():
        return ph.DamperGeometry(), "built-in defaults (no geometry.json found)"
    path = Path(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise DataError(f"{path.name} is not valid JSON: {e}") from None
    if not isinstance(data, dict):
        raise DataError(f"{path.name} must contain a JSON object {{ ... }}.")
    try:
        geometry = ph.DamperGeometry.from_dict(data)
        geometry.validate()
    except ValueError as e:
        raise DataError(f"{path.name}: {e}") from None
    return geometry, str(path)


def save_geometry(geometry: ph.DamperGeometry, path) -> None:
    """Write geometry settings to JSON, keeping any '_' note keys already there."""
    geometry.validate()
    path = Path(path)
    notes = {}
    if path.exists():
        try:
            old = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(old, dict):
                notes = {k: v for k, v in old.items() if k.startswith("_")}
        except json.JSONDecodeError:
            pass
    data = {**notes, **{k: round(v, 4) for k, v in asdict(geometry).items()}}
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def save_blade_table(table: ph.BladeTable, path) -> None:
    """Write the blade table, keeping any '#' note lines at the top of the file."""
    table.validate()
    path = Path(path)
    notes = []
    if path.exists():
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            if line.lstrip().startswith("#"):
                notes.append(line)
            elif line.strip():
                break
    if not notes:
        notes = ["# Blades per section by section height (in). Heights in a range include",
                 "# both ends. Sections outside the table use the blade pitch in geometry.json."]
    lines = notes + [",".join(BLADE_TABLE_COLUMNS)]
    lines += [f"{r.min_height_in:g},{r.max_height_in:g},{r.blades}" for r in table.rules]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def load_size_rules(path) -> ph.SizeRules:
    """Read size limits and section rules. Missing file: no limits, single section."""
    if path is None or not Path(path).exists():
        return ph.SizeRules()
    path = Path(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise DataError(f"{path.name} is not valid JSON: {e}") from None
    if not isinstance(data, dict):
        raise DataError(f"{path.name} must contain a JSON object {{ ... }}.")
    return rules_from_settings(data, path.name)


def save_size_rules(rules: ph.SizeRules, path) -> None:
    """Write size rules to JSON, keeping any '_' note keys already there."""
    rules.validate()
    path = Path(path)
    notes = {}
    if path.exists():
        try:
            old = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(old, dict):
                notes = {k: v for k, v in old.items() if k.startswith("_")}
        except json.JSONDecodeError:
            pass
    path.write_text(json.dumps({**notes, **rules.to_dict()}, indent=2) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Excel workbook sheets
# ---------------------------------------------------------------------------

RULE_NOTES = {
    "min_width_in": "Smallest damper width made (in). Blank = no limit.",
    "min_height_in": "Smallest damper height made (in). Blank = no limit.",
    "max_width_in": "Largest overall width made (in). Blank = no limit.",
    "max_height_in": "Largest overall height made (in). Blank = no limit.",
    "max_section_width_in": "Largest single-section width (in). Wider dampers are split into "
                            "equal sections. Blank = never split across the width.",
    "max_section_height_in": "Largest single-section height (in). Taller dampers are split into "
                             "equal sections. Blank = never split up the height.",
    "joint_blockage_in": "Extra width blocked along each joint between sections (mullion, "
                         "angles), beyond the section frames (in). 0 = none.",
}
GEOMETRY_NOTES = {
    "description": "Short description shown next to the model list (optional).",
    "frame_obstruction_width_in": "Frame/sleeve obstruction on EACH side, left and right (in).",
    "frame_obstruction_height_in": "Frame/sleeve obstruction on EACH side, top and bottom (in).",
    "blade_thickness_in": "Blade thickness facing the airflow when fully open (in).",
    "blade_pitch_in": "Blade spacing (in). Only used for section heights not in the Blade table.",
    "other_blockage_in2": "Other blockage per section: linkage, jackshaft, stops (in2).",
}


def write_test_sheet(path, points: list[DataPoint], model_name: str = "") -> None:
    rows = [[p.width_in, p.height_in, p.face_velocity_fpm, p.pressure_drop_in_wg,
             p.free_area_ft2] for p in points]
    xl.write_table(path, xl.SHEET_TEST, REQUIRED_COLUMNS + OPTIONAL_COLUMNS, rows, notes=[
        f"Pressure-drop test data{' for ' + model_name if model_name else ''}: one row per test point.",
        "Test data is assumed to be at standard air (0.075 lb/ft3). free_area_ft2 is optional.",
        "Rows starting with # are ignored. You can add your own columns to the right.",
    ], widths=[12, 12, 20, 22, 15])


def write_free_area_sheet(path, entries: list[FreeAreaEntry]) -> None:
    rows = [[e.width_in, e.height_in, e.free_area_ft2] for e in entries]
    xl.write_table(path, xl.SHEET_FREE, FREE_AREA_COLUMNS, rows, notes=[
        "Measured free areas (ft2), one row per size. Sizes listed here use this value directly.",
    ], widths=[12, 12, 15])


def write_blade_sheet(path, table: ph.BladeTable) -> None:
    table.validate()
    rows = [[r.min_height_in, r.max_height_in, r.blades] for r in table.rules]
    xl.write_table(path, xl.SHEET_BLADES, BLADE_TABLE_COLUMNS, rows, notes=[
        "Blades per section by SECTION height (in). Each range includes both ends.",
        "Leave no gaps between rows (for example 10-14.99 and 15-20).",
        "Sections outside the table use blade_pitch_in from the Geometry sheet. "
        "Leave the table empty to always use the pitch.",
    ], widths=[16, 16, 10])


def write_rules_sheet(path, rules: ph.SizeRules) -> None:
    rules.validate()
    items = [(k, v, RULE_NOTES.get(k, "")) for k, v in rules.to_dict().items()]
    xl.write_settings(path, xl.SHEET_RULES, items, notes=[
        "Size limits and multi-section rules. Leave a value blank for no limit.",
    ])


def write_geometry_sheet(path, geometry: ph.DamperGeometry, description: str | None = None) -> None:
    geometry.validate()
    items = []
    if description is not None:
        items.append(("description", description or None, GEOMETRY_NOTES["description"]))
    items += [(k, round(v, 4), GEOMETRY_NOTES.get(k, "")) for k, v in asdict(geometry).items()]
    xl.write_settings(path, xl.SHEET_GEOMETRY, items, notes=[
        "Frame and blade dimensions used to estimate free area for sizes not in the Free area sheet.",
    ])


def _sheet_empty(reader: "xl.WorkbookReader", name: str) -> bool:
    return not reader.has_sheet(name) or not any(
        True for _ in reader.read_settings(name))


def _read_workbook_design(reader: "xl.WorkbookReader"):
    """Geometry, description, blade table and size rules from a workbook.
    Missing or empty sheets give the defaults."""
    name = reader.path.name
    if _sheet_empty(reader, xl.SHEET_GEOMETRY):
        geometry, description = ph.DamperGeometry(), ""
    else:
        geometry, description = geometry_from_settings(
            settings_from_pairs(reader.read_settings(xl.SHEET_GEOMETRY), name),
            f"{name} [{xl.SHEET_GEOMETRY}]")
    if _sheet_empty(reader, xl.SHEET_RULES):
        rules = ph.SizeRules()
    else:
        rules = rules_from_settings(
            settings_from_pairs(reader.read_settings(xl.SHEET_RULES), name),
            f"{name} [{xl.SHEET_RULES}]")
    if _sheet_empty(reader, xl.SHEET_BLADES):
        blades = ph.BladeTable()
    else:
        blades = parse_blade_table(*reader.read_table(xl.SHEET_BLADES, BLADE_TABLE_COLUMNS),
                                   source=f"{name} [{xl.SHEET_BLADES}]")
    return geometry, description, blades, rules


def _design_from_folder(folder):
    """Geometry, blade table and size rules of a model folder, whichever way it stores them."""
    folder = Path(folder)
    if (folder / WORKBOOK_FILE).exists():
        geometry, _, blades, rules = _read_workbook_design(xl.WorkbookReader(folder / WORKBOOK_FILE))
        return geometry, blades, rules
    return (load_geometry(folder / GEOMETRY_FILE)[0], load_blade_table(folder / BLADE_TABLE_FILE),
            load_size_rules(folder / SIZE_RULES_FILE))


# ---------------------------------------------------------------------------
# Fit results
# ---------------------------------------------------------------------------

def fit_through_origin(x: np.ndarray, y: np.ndarray) -> tuple[float, float]:
    """Least-squares fit of y = c * x. Returns (c, R^2)."""
    c = float(np.dot(x, y) / np.dot(x, x))
    ss_res = float(np.sum((y - c * x) ** 2))
    ss_tot = float(np.sum((y - y.mean()) ** 2))
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else math.nan
    return c, r2


@dataclass
class SizeFit:
    """Fitted loss coefficient for one tested damper size."""

    width_in: float
    height_in: float
    face_area_ft2: float
    free_area_ft2: float
    free_area_source: str
    c_free: float
    r2: float
    face_velocities_fpm: np.ndarray
    pressure_drops_in_wg: np.ndarray

    @property
    def label(self) -> str:
        return f"{self.width_in:g} x {self.height_in:g}"

    @property
    def free_ratio(self) -> float:
        return self.free_area_ft2 / self.face_area_ft2

    @property
    def c_face(self) -> float:
        """Equivalent coefficient based on face velocity."""
        return self.c_free / self.free_ratio ** 2

    @property
    def n_points(self) -> int:
        return len(self.face_velocities_fpm)

    @property
    def v_face_max(self) -> float:
        return float(self.face_velocities_fpm.max())

    def matches(self, width_in: float, height_in: float) -> bool:
        return (abs(self.width_in - width_in) <= SIZE_TOLERANCE_IN
                and abs(self.height_in - height_in) <= SIZE_TOLERANCE_IN)


@dataclass
class SizeModel:
    """C_free as a function of free-area ratio, fitted across sizes."""

    kind: str            # "linear" or "constant"
    intercept: float
    slope: float
    r2: float
    ratio_min: float
    ratio_max: float
    face_area_min: float
    face_area_max: float

    def c_free(self, free_ratio: float) -> float:
        return self.intercept + self.slope * free_ratio

    def describe(self) -> str:
        if self.kind == "linear":
            sign = "+" if self.slope >= 0 else "-"
            return (
                f"C_free = {self.intercept:.4f} {sign} {abs(self.slope):.4f} x (free-area ratio)"
                f"   R\u00b2 = {self.r2:.3f}, fitted to ratios "
                f"{self.ratio_min:.0%} to {self.ratio_max:.0%}"
            )
        return (
            f"C_free = {self.intercept:.4f} (average of tested sizes). "
            f"Add 3+ sizes with different free-area ratios for a size-dependent model."
        )


@dataclass
class Estimate:
    """Result of a pressure-drop estimate."""

    width_in: float
    height_in: float
    airflow_cfm: float
    face_velocity_fpm: float
    free_velocity_fpm: float
    face_area_ft2: float
    free_area_ft2: float
    temperature_f: float
    altitude_ft: float
    air_density: float
    velocity_pressure_in_wg: float
    c_free: float
    pressure_drop_in_wg: float
    source: str
    free_area_source: str = ""
    warnings: list[str] = field(default_factory=list)
    tested: SizeFit | None = None
    sections_wide: int = 1
    sections_high: int = 1
    blades_per_section: int = 0
    blade_source: str = ""

    @property
    def n_sections(self) -> int:
        return self.sections_wide * self.sections_high

    @property
    def section_width_in(self) -> float:
        return self.width_in / self.sections_wide

    @property
    def section_height_in(self) -> float:
        return self.height_in / self.sections_high

    @property
    def free_ratio(self) -> float:
        return self.free_area_ft2 / self.face_area_ft2

    @property
    def c_face(self) -> float:
        return self.c_free / self.free_ratio ** 2

    @property
    def pressure_drop_pa(self) -> float:
        return self.pressure_drop_in_wg * PA_PER_IN_WG

    def curve(self, airflow_cfm) -> np.ndarray:
        """Pressure drop (in. wg) at other airflows for the same damper and air."""
        q = np.asarray(airflow_cfm, dtype=float)
        return self.c_free * ph.velocity_pressure(q / self.free_area_ft2, self.air_density)


# ---------------------------------------------------------------------------
# Free-area calibration
# ---------------------------------------------------------------------------

@dataclass
class FreeAreaComparison:
    """Measured free area vs. the geometry estimate for one size."""

    width_in: float
    height_in: float
    measured_ft2: float
    estimated_ft2: float    # nan if the geometry gives no opening

    @property
    def label(self) -> str:
        return f"{self.width_in:g} x {self.height_in:g}"

    @property
    def error_pct(self) -> float:
        return (self.estimated_ft2 - self.measured_ft2) / self.measured_ft2 * 100


def compare_free_areas(table: list[FreeAreaEntry],
                       design) -> list[FreeAreaComparison]:
    """Compare each measured free area with the geometry estimate.

    `design` is a DamperDesign, or a DamperGeometry for a plain single-section
    damper with no blade table.
    """
    if isinstance(design, ph.DamperGeometry):
        design = ph.DamperDesign(design)
    rows = []
    for e in table:
        try:
            est = design.free_area_ft2(e.width_in, e.height_in)
        except ValueError:
            est = math.nan
        rows.append(FreeAreaComparison(e.width_in, e.height_in, e.free_area_ft2, est))
    return rows


def comparison_stats(rows: list[FreeAreaComparison]) -> tuple[float, float]:
    """(RMS error in ft^2, largest absolute error in %) over the rows."""
    errs = np.array([r.estimated_ft2 - r.measured_ft2 for r in rows])
    pcts = np.array([abs(r.error_pct) for r in rows])
    if len(rows) == 0 or np.isnan(errs).any():
        return math.nan, math.nan
    return float(np.sqrt(np.mean(errs ** 2))), float(pcts.max())


@dataclass
class CalibrationResult:
    geometry: ph.DamperGeometry
    rows: list[FreeAreaComparison]
    equal_frame: bool

    @property
    def rms_error_ft2(self) -> float:
        return comparison_stats(self.rows)[0]

    @property
    def max_error_pct(self) -> float:
        return comparison_stats(self.rows)[1]


def _nonnegative_fit(x: np.ndarray, z: np.ndarray,
                     k: np.ndarray | None = None) -> tuple[float, float, float]:
    """Fit z = t*x + o*k with t, o >= 0 by least squares. Returns (t, o, SSE).

    k defaults to 1 (a constant); for multi-section dampers it is the number
    of sections, because each section has its own "other" blockage.
    """
    k = np.ones_like(x) if k is None else k
    candidates = []
    a = np.column_stack([x, k])
    (t, o), *_ = np.linalg.lstsq(a, z, rcond=None)
    if t >= 0 and o >= 0:
        candidates.append((float(t), float(o)))
    if np.dot(x, x) > 0:
        candidates.append((max(0.0, float(np.dot(x, z) / np.dot(x, x))), 0.0))   # o = 0
    candidates.append((0.0, max(0.0, float(np.dot(k, z) / np.dot(k, k)))))         # t = 0
    best = None
    for t, o in candidates:
        sse = float(np.sum((z - t * x - o * k) ** 2))
        if best is None or sse < best[2]:
            best = (t, o, sse)
    return best


def calibrate_geometry(table: list[FreeAreaEntry], blade_pitch_in: float, *,
                       equal_frame: bool = True, max_frame_in: float = 6.0,
                       step_in: float = 1 / 32,
                       blade_table: ph.BladeTable | None = None,
                       size_rules: ph.SizeRules | None = None) -> CalibrationResult:
    """Fit the geometry settings to measured free areas.

    Fits the frame obstruction, blade thickness, and other blockage so the
    geometry estimate matches the measured free areas as closely as possible
    (least squares). The blade pitch is not fitted, because the blade count
    changes in steps; use the real pitch of your dampers. Sections covered
    by the blade table use its blade count instead of the pitch.

    Multi-section sizes are split by the size rules, exactly as for
    estimates; the other blockage is fitted per section.

    The frame obstruction is searched on a grid (default 1/32 in steps up to
    6 in). For each frame value, blade thickness and other blockage follow
    from a linear least-squares fit, constrained to be non-negative.
    """
    min_sizes = 3 if equal_frame else 4
    if len(table) < min_sizes:
        raise ModelError(
            f"Calibration needs measured free areas for at least {min_sizes} sizes "
            f"(found {len(table)}). More sizes, from small to large, give a better fit."
        )
    if blade_pitch_in <= 0:
        raise ModelError("Blade pitch must be greater than zero.")

    rules = size_rules or ph.SizeRules()
    blades = blade_table or ph.BladeTable()

    # Section layout and joint blockage do not depend on the fitted values.
    layout = [rules.layout(e.width_in, e.height_in) for e in table]
    sec = np.array([nw * nh for nw, nh in layout], dtype=float)
    sw = np.array([e.width_in / nw for e, (nw, _) in zip(table, layout)])
    sh = np.array([e.height_in / nh for e, (_, nh) in zip(table, layout)])
    joint = np.array([rules.joint_area_in2(e.width_in, e.height_in, nw, nh)
                      for e, (nw, nh) in zip(table, layout)])
    measured_in2 = np.array([e.free_area_ft2 for e in table]) * ph.IN2_PER_FT2

    # Blade counts from the table (None where the pitch rule applies).
    fixed_blades = []
    for e, h_sec in zip(table, sh):
        if blades.is_empty:
            fixed_blades.append(None)
            continue
        n = blades.lookup(h_sec)
        if n is None and blades.gap_around(h_sec) is not None:
            try:
                ph.section_blades(h_sec, ph.DamperGeometry(), blades)
            except ValueError as err:
                raise ModelError(f"Size {e.label}: {err}") from None
        fixed_blades.append(n)
    uses_pitch = np.array([n is None for n in fixed_blades])
    fixed = np.array([n if n is not None else 0 for n in fixed_blades], dtype=float)

    grid = np.arange(0.0, max_frame_in + 1e-9, step_in)
    pairs = ((f, f) for f in grid) if equal_frame else ((fw, fh) for fw in grid for fh in grid)

    best = None
    for fw, fh in pairs:
        open_w = sw - 2 * fw
        open_h = sh - 2 * fh
        if (open_w <= 0).any() or (open_h <= 0).any():
            continue
        n = np.where(uses_pitch, np.maximum(1, np.ceil(open_h / blade_pitch_in - 1e-9)), fixed)
        # Blocked by blades and other items = t * (sections * n * open_w) + other * sections
        x = sec * n * open_w
        z = sec * open_w * open_h - joint - measured_in2
        t, o, sse = _nonnegative_fit(x, z, sec)
        if best is None or sse < best[0] - 1e-9:
            best = (sse, fw, fh, t, o)

    if best is None:
        raise ModelError("No frame obstruction value leaves an opening for every size.")

    _, fw, fh, t, o = best
    geometry = ph.DamperGeometry(
        frame_obstruction_width_in=float(fw),
        frame_obstruction_height_in=float(fh),
        blade_thickness_in=t,
        blade_pitch_in=float(blade_pitch_in),
        other_blockage_in2=o,
    )
    design = ph.DamperDesign(geometry, blades, rules)
    return CalibrationResult(geometry, compare_free_areas(table, design), equal_frame)


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------

class DamperModel:
    """Fits the test data and produces pressure-drop estimates."""

    def __init__(self, points, geometry: ph.DamperGeometry | None = None,
                 geometry_source: str = "built-in defaults", data_source: str = "",
                 free_area_table: list[FreeAreaEntry] | None = None,
                 free_area_table_source: str = "", *, name: str = "Damper",
                 description: str = "", folder: Path | None = None,
                 blade_table: ph.BladeTable | None = None,
                 size_rules: ph.SizeRules | None = None,
                 workbook_path: Path | None = None):
        self.name = name
        self.workbook_path = Path(workbook_path) if workbook_path else None
        self.description = description
        self.folder = Path(folder) if folder else None
        self.points = list(points)
        if not self.points:
            raise DataError("No test data points.")
        self.geometry = geometry or ph.DamperGeometry()
        self.blade_table = blade_table or ph.BladeTable()
        self.size_rules = size_rules or ph.SizeRules()
        self.design = ph.DamperDesign(self.geometry, self.blade_table, self.size_rules)
        try:
            self.design.validate()
        except ValueError as e:
            raise DataError(str(e)) from None
        self.geometry_source = geometry_source
        self.data_source = data_source
        self.free_area_table = list(free_area_table or [])
        self.free_area_table_source = free_area_table_source
        self.fits = self._fit_sizes()
        self.size_model = self._fit_size_model()
        self.max_tested_face_velocity = max(p.face_velocity_fpm for p in self.points)

    @classmethod
    def from_files(cls, data_path, geometry_path=None, free_area_path=None, *,
                   name: str = "Damper", description: str = "", folder=None,
                   blade_table_path=None, size_rules_path=None) -> "DamperModel":
        points = load_points(data_path)
        geometry, geometry_source = load_geometry(geometry_path)
        table = load_free_area_table(free_area_path)
        table_source = str(free_area_path) if table else "none"
        return cls(points, geometry, geometry_source, str(data_path), table, table_source,
                   name=name, description=description, folder=folder,
                   blade_table=load_blade_table(blade_table_path),
                   size_rules=load_size_rules(size_rules_path))

    @classmethod
    def from_folder(cls, folder) -> "DamperModel":
        """Load a damper model from its folder (see paths.py for the layout).

        If the folder holds model_data.xlsx, everything is read from the
        workbook and the separate CSV/JSON files are ignored.
        """
        folder = Path(folder)
        if (folder / WORKBOOK_FILE).exists():
            return cls.from_workbook(folder)
        if not (folder / DATA_FILE).exists():
            raise DataError(f"No {WORKBOOK_FILE} or {DATA_FILE} in {folder}. "
                            f"Each model folder needs test data.")
        return cls.from_files(folder / DATA_FILE, folder / GEOMETRY_FILE,
                              folder / FREE_AREA_FILE, name=folder.name,
                              description=read_description(folder / GEOMETRY_FILE),
                              folder=folder,
                              blade_table_path=folder / BLADE_TABLE_FILE,
                              size_rules_path=folder / SIZE_RULES_FILE)

    @classmethod
    def from_workbook(cls, folder) -> "DamperModel":
        """Load a damper model from the folder's model_data.xlsx."""
        folder = Path(folder)
        path = folder / WORKBOOK_FILE
        reader = xl.WorkbookReader(path)
        if not reader.has_sheet(xl.SHEET_TEST):
            raise DataError(f"{path.name} has no '{xl.SHEET_TEST}' sheet. "
                            f"Each model needs pressure-drop test data.")
        points = parse_points(*reader.read_table(xl.SHEET_TEST, REQUIRED_COLUMNS),
                              source=f"'{xl.SHEET_TEST}' sheet of {path.name}")
        table = []
        if not _sheet_empty(reader, xl.SHEET_FREE):
            table = parse_free_area_table(*reader.read_table(xl.SHEET_FREE, FREE_AREA_COLUMNS))
        geometry, description, blades, rules = _read_workbook_design(reader)
        return cls(points, geometry, f"{path.name} [{xl.SHEET_GEOMETRY}]",
                   f"{path} [{xl.SHEET_TEST}]", table,
                   f"{path.name} [{xl.SHEET_FREE}]" if table else "none",
                   name=folder.name, description=description, folder=folder,
                   blade_table=blades, size_rules=rules, workbook_path=path)

    @property
    def storage(self) -> str:
        """Where this model's data lives, for display."""
        if self.workbook_path is not None:
            return f"Excel workbook ({self.workbook_path.name})"
        return "CSV/JSON files"

    def _require_folder(self):
        if self.folder is None:
            raise ModelError(f"{self.name} has no model folder to save to.")

    def save_geometry(self, geometry: ph.DamperGeometry) -> None:
        """Save geometry to this model's workbook or geometry.json."""
        self._require_folder()
        if self.workbook_path is not None:
            write_geometry_sheet(self.workbook_path, geometry)
        else:
            save_geometry(geometry, self.folder / GEOMETRY_FILE)

    def save_rules(self, rules: ph.SizeRules, blade_table: ph.BladeTable) -> None:
        """Save size rules and blade table to this model's workbook or files."""
        self._require_folder()
        if self.workbook_path is not None:
            write_rules_sheet(self.workbook_path, rules)
            write_blade_sheet(self.workbook_path, blade_table)
        else:
            save_size_rules(rules, self.folder / SIZE_RULES_FILE)
            save_blade_table(blade_table, self.folder / BLADE_TABLE_FILE)

    @property
    def geometry_path(self) -> Path | None:
        return self.folder / GEOMETRY_FILE if self.folder else None

    @property
    def free_area_path(self) -> Path | None:
        return self.folder / FREE_AREA_FILE if self.folder else None

    @property
    def blade_table_path(self) -> Path | None:
        return self.folder / BLADE_TABLE_FILE if self.folder else None

    @property
    def size_rules_path(self) -> Path | None:
        return self.folder / SIZE_RULES_FILE if self.folder else None

    # -- free area ----------------------------------------------------------

    def measured_free_area(self, width_in: float, height_in: float) -> FreeAreaEntry | None:
        for e in self.free_area_table:
            if e.matches(width_in, height_in):
                return e
        return None

    def free_area_for(self, width_in: float, height_in: float) -> tuple[float, str]:
        """Free area (ft^2) for a size, and where it came from.

        Uses the measured free-area table when the size is listed there,
        otherwise the geometry estimate.
        """
        entry = self.measured_free_area(width_in, height_in)
        if entry is not None:
            return entry.free_area_ft2, "measured (free-area table)"
        return self.design.free_area_ft2(width_in, height_in), "geometry estimate"

    def free_area_comparison(self) -> list[FreeAreaComparison]:
        return compare_free_areas(self.free_area_table, self.design)

    @property
    def n_points(self) -> int:
        return len(self.points)

    # -- fitting ------------------------------------------------------------

    def _fit_sizes(self) -> list[SizeFit]:
        groups: dict[tuple[float, float], list[DataPoint]] = {}
        for p in self.points:
            key = (round(p.width_in, 3), round(p.height_in, 3))
            groups.setdefault(key, []).append(p)

        fits = []
        for (w, h), pts in sorted(groups.items(), key=lambda kv: (kv[0][0] * kv[0][1], kv[0])):
            face = ph.face_area_ft2(w, h)

            given = [p.free_area_ft2 for p in pts if p.free_area_ft2 is not None]
            if given:
                if max(given) - min(given) > 1e-6:
                    raise DataError(
                        f"Size {w:g} x {h:g}: free_area_ft2 differs between rows. "
                        f"Use the same value on every row for a size."
                    )
                free, source = given[0], "test-data file"
            else:
                try:
                    free, source = self.free_area_for(w, h)
                except ValueError as e:
                    raise DataError(f"Size {w:g} x {h:g}: {e}") from None

            if free >= face:
                raise DataError(
                    f"Size {w:g} x {h:g}: free area ({free:.3f} ft\u00b2) must be smaller "
                    f"than the face area ({face:.3f} ft\u00b2)."
                )

            v = np.array([p.face_velocity_fpm for p in pts])
            dp = np.array([p.pressure_drop_in_wg for p in pts])
            order = np.argsort(v)
            v, dp = v[order], dp[order]

            # Test data is taken as standard air (AMCA reports are corrected to it).
            vp_free = ph.velocity_pressure(v * face / free)
            c_free, r2 = fit_through_origin(vp_free, dp)

            fits.append(SizeFit(w, h, face, free, source, c_free, r2, v, dp))
        return fits

    def _fit_size_model(self) -> SizeModel:
        ratios = np.array([f.free_ratio for f in self.fits])
        cs = np.array([f.c_free for f in self.fits])
        faces = np.array([f.face_area_ft2 for f in self.fits])
        lo, hi = float(ratios.min()), float(ratios.max())
        a_lo, a_hi = float(faces.min()), float(faces.max())

        if len(self.fits) >= 3 and hi - lo >= 0.02:
            slope, intercept = np.polyfit(ratios, cs, 1)
            pred = intercept + slope * ratios
            ss_tot = float(np.sum((cs - cs.mean()) ** 2))
            r2 = 1.0 - float(np.sum((cs - pred) ** 2)) / ss_tot if ss_tot > 0 else math.nan
            return SizeModel("linear", float(intercept), float(slope), r2, lo, hi, a_lo, a_hi)

        return SizeModel("constant", float(cs.mean()), 0.0, math.nan, lo, hi, a_lo, a_hi)

    # -- estimating ---------------------------------------------------------

    def find_size(self, width_in: float, height_in: float) -> SizeFit | None:
        for f in self.fits:
            if f.matches(width_in, height_in):
                return f
        return None

    def _coefficient(self, width_in, height_in, free_area_override):
        if width_in <= 0 or height_in <= 0:
            raise ModelError("Width and height must be greater than zero.")
        try:
            bd = self.design.breakdown(width_in, height_in, check_limits=True)
        except ValueError as e:
            raise ModelError(f"{self.name}: {e}") from None

        face = bd.face_area_ft2
        tested = self.find_size(width_in, height_in)
        warnings = []
        if "outside blade table" in bd.blade_source:
            warnings.append(
                f"Section height {bd.section_height_in:.4g} in is outside the blade table, "
                f"so the blade count ({bd.n_blades}) comes from the blade pitch."
            )

        if free_area_override is not None:
            if free_area_override <= 0:
                raise ModelError("Free area override must be greater than zero.")
            free, free_source = float(free_area_override), "entered override"
        elif tested:
            free, free_source = tested.free_area_ft2, tested.free_area_source
        else:
            try:
                free, free_source = self.free_area_for(width_in, height_in)
            except ValueError as e:
                raise ModelError(str(e)) from None

        if free >= face:
            raise ModelError(
                f"Free area ({free:.3f} ft\u00b2) must be smaller than the "
                f"face area ({face:.3f} ft\u00b2)."
            )
        ratio = free / face
        multi = bd.n_sections > 1
        layout_note = (f" ({bd.sections_wide} x {bd.sections_high} sections of "
                       f"{bd.section_width_in:.4g} x {bd.section_height_in:.4g} in)") if multi else ""

        # A multi-section damper whose section size was tested uses that fit:
        # each section sees the same face velocity as the tested damper.
        section_tested = None
        if multi and not tested and free_area_override is None:
            section_tested = self.find_size(bd.section_width_in, bd.section_height_in)

        if tested and free_area_override is None:
            c_free = tested.c_free
            r2 = "n/a" if math.isnan(tested.r2) else f"{tested.r2:.3f}"
            source = f"Test data for the {tested.label} size (R\u00b2 = {r2})"
            if tested.n_points < 3:
                warnings.append(
                    f"Only {tested.n_points} test point(s) for this size, "
                    f"so its fit is not well constrained."
                )
        elif section_tested is not None:
            c_free = section_tested.c_free
            source = f"Test data for the {section_tested.label} section size{layout_note}"
        else:
            sm = self.size_model
            # Look up C at the ratio of one section, so a large multi-section
            # damper is treated like its sections rather than a giant single one.
            c_ratio = ratio + bd.joint_block_in2 / bd.face_area_in2 if multi else ratio
            c_free = sm.c_free(c_ratio)
            source = ("Size model fitted across tested sizes" if sm.kind == "linear"
                      else "Average coefficient of tested sizes") + layout_note
            if c_ratio < sm.ratio_min - 0.005 or c_ratio > sm.ratio_max + 0.005:
                warnings.append(
                    f"Free-area ratio {c_ratio:.0%} is outside the tested range "
                    f"({sm.ratio_min:.0%} to {sm.ratio_max:.0%}), so this is an extrapolation."
                )
            section_face = face / bd.n_sections
            if section_face < sm.face_area_min * 0.95 or section_face > sm.face_area_max * 1.05:
                what = "Section face area" if multi else "Face area"
                warnings.append(
                    f"{what} {section_face:.2f} ft\u00b2 is outside the tested sizes "
                    f"({sm.face_area_min:.2f} to {sm.face_area_max:.2f} ft\u00b2)."
                )
            if c_free <= 0:
                raise ModelError(
                    f"The size model gives a non-positive loss coefficient ({c_free:.3f}) "
                    f"at a {c_ratio:.0%} free-area ratio, far outside the tested range. "
                    f"Add test data for a size closer to this one."
                )
        return face, free, free_source, c_free, source, warnings, tested, bd

    def estimate(self, width_in: float, height_in: float, *,
                 airflow_cfm: float | None = None,
                 face_velocity_fpm: float | None = None,
                 temperature_f: float = 70.0,
                 altitude_ft: float = 0.0,
                 free_area_override_ft2: float | None = None) -> Estimate:
        """Estimate pressure drop. Give either airflow_cfm or face_velocity_fpm."""
        if (airflow_cfm is None) == (face_velocity_fpm is None):
            raise ModelError("Give either an airflow (cfm) or a face velocity (fpm), not both.")

        face, free, free_source, c_free, source, warnings, tested, bd = self._coefficient(
            width_in, height_in, free_area_override_ft2)

        if airflow_cfm is not None:
            if airflow_cfm <= 0:
                raise ModelError("Airflow must be greater than zero.")
            q = float(airflow_cfm)
            v_face = q / face
        else:
            if face_velocity_fpm <= 0:
                raise ModelError("Face velocity must be greater than zero.")
            v_face = float(face_velocity_fpm)
            q = v_face * face
        v_free = q / free

        try:
            rho = ph.air_density(temperature_f, altitude_ft)
        except ValueError as e:
            raise ModelError(str(e)) from None

        vp = float(ph.velocity_pressure(v_free, rho))
        dp = c_free * vp

        v_max = tested.v_face_max if tested else self.max_tested_face_velocity
        if v_face > v_max * 1.05:
            warnings.append(
                f"Face velocity {v_face:,.0f} fpm is above the highest tested velocity "
                f"({v_max:,.0f} fpm), so this is an extrapolation."
            )

        return Estimate(
            width_in=float(width_in), height_in=float(height_in),
            airflow_cfm=q, face_velocity_fpm=v_face, free_velocity_fpm=v_free,
            face_area_ft2=face, free_area_ft2=free,
            temperature_f=float(temperature_f), altitude_ft=float(altitude_ft),
            air_density=rho, velocity_pressure_in_wg=vp,
            c_free=c_free, pressure_drop_in_wg=dp,
            source=source, free_area_source=free_source,
            warnings=warnings, tested=tested,
            sections_wide=bd.sections_wide, sections_high=bd.sections_high,
            blades_per_section=bd.n_blades, blade_source=bd.blade_source,
        )


# ---------------------------------------------------------------------------
# Library of damper models
# ---------------------------------------------------------------------------

def read_description(geometry_path) -> str:
    """The optional "_description" text in a model's geometry.json."""
    try:
        data = json.loads(Path(geometry_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    if isinstance(data, dict):
        return str(data.get("_description", "")).strip()
    return ""


class ModelLibrary:
    """All damper models found in a models folder.

    A folder that fails to load does not stop the others: its error is kept
    in `errors` so the interface can report it.
    """

    def __init__(self, models_dir):
        self.models_dir = Path(models_dir)
        self.models: dict[str, DamperModel] = {}
        self.errors: dict[str, str] = {}

    @classmethod
    def load(cls, models_dir) -> "ModelLibrary":
        lib = cls(models_dir)
        if not lib.models_dir.is_dir():
            raise DataError(
                f"Models folder not found: {lib.models_dir}\n"
                f"Create it with one sub-folder per damper model."
            )
        for folder in sorted(p for p in lib.models_dir.iterdir() if p.is_dir()):
            if folder.name.startswith((".", "_")):
                continue
            try:
                lib.models[folder.name] = DamperModel.from_folder(folder)
            except (FileNotFoundError, DataError, ModelError, ValueError) as e:
                lib.errors[folder.name] = str(e)
        return lib

    @property
    def names(self) -> list[str]:
        return list(self.models)

    def get(self, name: str) -> DamperModel:
        try:
            return self.models[name]
        except KeyError:
            known = ", ".join(self.names) or "none"
            raise ModelError(f"Unknown damper model '{name}'. Available: {known}") from None

    def compare(self, width_in: float, height_in: float, names=None, **conditions):
        """Estimate the same size and conditions with several models.

        Returns [(name, Estimate or None, error message or "")].
        """
        results = []
        for name in names or self.names:
            try:
                results.append((name, self.get(name).estimate(width_in, height_in, **conditions), ""))
            except ModelError as e:
                results.append((name, None, str(e)))
        return results


_VALID_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._()+-]*$")


def create_model_folder(models_dir, name: str, copy_geometry_from=None,
                        description: str = "", use_excel: bool = False) -> Path:
    """Create a new model folder with empty data templates.

    use_excel=True stores everything in one workbook (model_data.xlsx);
    otherwise separate CSV/JSON files are used. If copy_geometry_from is a
    model folder, its geometry, blade table and size rules are copied.
    Returns the new folder.
    """
    name = name.strip()
    if not _VALID_NAME.match(name) or name.endswith((" ", ".")):
        raise ModelError(
            "Model names may use letters, numbers, spaces and . _ - ( ) +, "
            "and must start with a letter or number."
        )
    folder = Path(models_dir) / name
    if folder.exists():
        raise ModelError(f"A model named '{name}' already exists.")

    geometry, blades, rules = ph.DamperGeometry(), ph.BladeTable(), ph.SizeRules()
    if copy_geometry_from is not None:
        try:
            geometry, blades, rules = _design_from_folder(copy_geometry_from)
        except DataError as e:
            raise ModelError(f"Could not copy from {Path(copy_geometry_from).name}: {e}") from None

    folder.mkdir(parents=True)
    if use_excel:
        path = folder / WORKBOOK_FILE
        write_test_sheet(path, [], name)
        write_free_area_sheet(path, [])
        write_blade_sheet(path, blades)
        write_rules_sheet(path, rules)
        write_geometry_sheet(path, geometry, description)
        return folder

    save_geometry(geometry, folder / GEOMETRY_FILE)
    if description:
        data = json.loads((folder / GEOMETRY_FILE).read_text(encoding="utf-8"))
        data = {"_description": description, **data}
        (folder / GEOMETRY_FILE).write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    (folder / DATA_FILE).write_text(
        f"# Pressure-drop test data for {name}. One row per test point.\n"
        "# Test data is assumed to be at standard air (0.075 lb/ft3).\n"
        "width_in,height_in,face_velocity_fpm,pressure_drop_in_wg,free_area_ft2\n",
        encoding="utf-8")
    (folder / FREE_AREA_FILE).write_text(
        f"# Measured free areas for {name}. One row per size.\n"
        "width_in,height_in,free_area_ft2\n",
        encoding="utf-8")
    save_blade_table(blades, folder / BLADE_TABLE_FILE)
    save_size_rules(rules, folder / SIZE_RULES_FILE)
    return folder


OLD_FILES_FOLDER = "_old_files"


def convert_to_workbook(model: DamperModel) -> Path:
    """Move a file-based model into one Excel workbook.

    Writes model_data.xlsx with all of the model's data, then moves the old
    CSV/JSON files into an _old_files sub-folder (kept as a backup, ignored
    by the program). Returns the workbook path.
    """
    if model.folder is None:
        raise ModelError(f"{model.name} has no model folder.")
    if model.workbook_path is not None:
        raise ModelError(f"{model.name} already uses an Excel workbook.")
    path = model.folder / WORKBOOK_FILE
    write_test_sheet(path, model.points, model.name)
    write_free_area_sheet(path, model.free_area_table)
    write_blade_sheet(path, model.blade_table)
    write_rules_sheet(path, model.size_rules)
    write_geometry_sheet(path, model.geometry, model.description)

    backup = model.folder / OLD_FILES_FOLDER
    for fname in (DATA_FILE, FREE_AREA_FILE, GEOMETRY_FILE, BLADE_TABLE_FILE, SIZE_RULES_FILE):
        src = model.folder / fname
        if src.exists():
            backup.mkdir(exist_ok=True)
            shutil.move(str(src), str(backup / fname))
    return path
