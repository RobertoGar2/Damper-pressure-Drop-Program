"""Read and write a damper model's Excel workbook (model_data.xlsx).

A model folder can hold all of its data in one workbook instead of separate
CSV/JSON files. Sheets (names are not case-sensitive; all but the first are
optional):

    Test data    width_in | height_in | face_velocity_fpm | pressure_drop_in_wg | free_area_ft2
    Free area    width_in | height_in | free_area_ft2
    Blade table  min_height_in | max_height_in | blades
    Size rules   setting | value | notes      (one rule per row)
    Geometry     setting | value | notes      (one setting per row, plus "description")

Table sheets: the header row can be anywhere in the first 30 rows, so notes
can go above it. Rows whose first cell starts with '#' and blank rows are
ignored, and extra columns (e.g. your own notes) are allowed.

Settings sheets: the setting name goes in column A and its value in column
B; anything in other columns is ignored. A blank value means "no limit".

When the program saves changes, it rewrites only the sheet it changed and
keeps the other sheets, your notes columns and formatting.
"""

from __future__ import annotations

from pathlib import Path

from .errors import DataError

SHEET_TEST = "Test data"
SHEET_FREE = "Free area"
SHEET_BLADES = "Blade table"
SHEET_RULES = "Size rules"
SHEET_GEOMETRY = "Geometry"

HEADER_SEARCH_ROWS = 30
_HEADER_FILL = "1F3B66"
_NOTE_COLOR = "4A5568"


def _openpyxl():
    try:
        import openpyxl
    except ImportError:
        raise DataError(
            "Excel workbooks need the openpyxl package. Install it with: pip install openpyxl"
        ) from None
    return openpyxl


def _cell_text(value) -> str:
    """Cell value as text: whole numbers without '.0', blanks as ''."""
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _find_sheet(wb, name):
    for ws in wb.worksheets:
        if ws.title.strip().lower() == name.lower():
            return ws
    return None


class WorkbookReader:
    """Reads one workbook. Uses the saved (calculated) values of formula cells."""

    def __init__(self, path):
        opx = _openpyxl()
        self.path = Path(path)
        try:
            self._values = opx.load_workbook(self.path, data_only=True)
            self._formulas = opx.load_workbook(self.path, data_only=False)
        except PermissionError:
            raise DataError(f"Could not open {self.path.name}: it is locked by another "
                            f"program. Close it and try again.") from None
        except Exception as e:  # corrupt file, not an xlsx, ...
            raise DataError(f"Could not open {self.path.name}: {e}") from None

    def has_sheet(self, name) -> bool:
        return _find_sheet(self._values, name) is not None

    def _text(self, ws_v, ws_f, r, c, loc) -> str:
        value = ws_v.cell(r, c).value
        if value is None:
            formula = ws_f.cell(r, c).value
            if isinstance(formula, str) and formula.startswith("="):
                raise DataError(
                    f"{loc}: cell {ws_v.cell(r, c).coordinate} has a formula with no saved "
                    f"result. Open {self.path.name} in Excel, save it, then reload."
                )
        return _cell_text(value)

    def _sheets(self, name):
        ws_v = _find_sheet(self._values, name)
        if ws_v is None:
            raise DataError(f"{self.path.name} has no '{name}' sheet.")
        return ws_v, self._formulas[ws_v.title]

    def read_table(self, name, required_columns):
        """Rows of a table sheet, in the same shape as the CSV reader."""
        ws_v, ws_f = self._sheets(name)
        where = f"{self.path.name} [{ws_v.title}]"
        max_col = ws_v.max_column

        header_row, header = None, None
        for r in range(1, min(ws_v.max_row, HEADER_SEARCH_ROWS) + 1):
            texts = [_cell_text(ws_v.cell(r, c).value).lower() for c in range(1, max_col + 1)]
            if all(req in texts for req in required_columns):
                header_row, header = r, texts
                break
        if header_row is None:
            raise DataError(
                f"{where}: no header row with the columns {', '.join(required_columns)} "
                f"in the first {HEADER_SEARCH_ROWS} rows."
            )

        col = {}
        for i, name_ in enumerate(header):
            if name_ and name_ not in col:
                col[name_] = i
        rows = []
        for r in range(header_row + 1, ws_v.max_row + 1):
            loc = f"{where}, row {r}"
            values = [self._text(ws_v, ws_f, r, c, loc) for c in range(1, max_col + 1)]
            if not any(values) or values[0].startswith("#"):
                continue
            rows.append((r, loc, values))
        return col, rows

    def read_settings(self, name):
        """(key, value text, location) for each setting row (column A, column B)."""
        ws_v, ws_f = self._sheets(name)
        where = f"{self.path.name} [{ws_v.title}]"
        pairs = []
        for r in range(1, ws_v.max_row + 1):
            loc = f"{where}, row {r}"
            key = _cell_text(ws_v.cell(r, 1).value)
            if not key or key.startswith("#"):
                continue
            value = self._text(ws_v, ws_f, r, 2, loc)
            if value.lower() == "value" and key.lower() in ("setting", "rule", "name", "key"):
                continue          # header row
            pairs.append((key, value, loc))
        return pairs


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------

def _load_for_write(path):
    opx = _openpyxl()
    path = Path(path)
    if path.exists():
        try:
            return opx.load_workbook(path)
        except Exception as e:
            raise DataError(f"Could not open {path.name}: {e}") from None
    wb = opx.Workbook()
    wb.remove(wb.active)
    return wb


def _save(wb, path):
    try:
        wb.save(path)
    except PermissionError:
        raise DataError(
            f"Could not save {Path(path).name}: it is open in Excel or another program. "
            f"Close it and try again."
        ) from None


def _style_header(ws, row, ncols):
    from openpyxl.styles import Font, PatternFill
    for c in range(1, ncols + 1):
        cell = ws.cell(row, c)
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor=_HEADER_FILL)


def _write_notes(ws, notes, start_row=1):
    from openpyxl.styles import Font
    for i, note in enumerate(notes):
        cell = ws.cell(start_row + i, 1, f"# {note}")
        cell.font = Font(italic=True, color=_NOTE_COLOR)
    return start_row + len(notes)


def _new_sheet(wb, name):
    ws = wb.create_sheet(name)
    order = [SHEET_TEST, SHEET_FREE, SHEET_BLADES, SHEET_RULES, SHEET_GEOMETRY]
    if name in order:   # keep the standard sheet order when adding a sheet
        later = [s for s in order[order.index(name) + 1:] if _find_sheet(wb, s) is not None]
        if later:
            target = wb.worksheets.index(_find_sheet(wb, later[0]))
            wb.move_sheet(ws, offset=target - wb.worksheets.index(ws))
    return ws


def write_table(path, name, columns, rows, notes=(), widths=None):
    """Replace the data rows of a table sheet, keeping notes above the header
    and any extra columns in the header. Creates the sheet (and file) if needed."""
    wb = _load_for_write(path)
    ws = _find_sheet(wb, name)
    header_row = None
    if ws is not None:
        for r in range(1, min(ws.max_row, HEADER_SEARCH_ROWS) + 1):
            texts = [_cell_text(ws.cell(r, c).value).lower()
                     for c in range(1, ws.max_column + 1)]
            if all(col in texts for col in columns):
                header_row = r
                positions = {t: i + 1 for i, t in reversed(list(enumerate(texts))) if t}
                break
    if ws is None or header_row is None:
        if ws is None:
            ws = _new_sheet(wb, name)
            start = _write_notes(ws, notes)
        else:
            start = ws.max_row + 2 if ws.max_row > 1 else 1
        header_row = start
        for i, col in enumerate(columns, start=1):
            ws.cell(header_row, i, col)
        _style_header(ws, header_row, len(columns))
        positions = {col: i for i, col in enumerate(columns, start=1)}
        ws.freeze_panes = ws.cell(header_row + 1, 1)
        for i, w in enumerate(widths or [], start=1):
            ws.column_dimensions[ws.cell(1, i).column_letter].width = w

    if ws.max_row > header_row:
        ws.delete_rows(header_row + 1, ws.max_row - header_row)
    for r, row in enumerate(rows, start=header_row + 1):
        for col, value in zip(columns, row):
            ws.cell(r, positions[col]).value = value
    _save(wb, path)


def write_settings(path, name, items, notes=()):
    """Update a settings sheet. items: [(key, value, note)]. Existing rows keep
    their position, notes and formatting; only column B changes. Missing keys
    are added at the end. Creates the sheet (and file) if needed."""
    wb = _load_for_write(path)
    ws = _find_sheet(wb, name)
    if ws is None:
        ws = _new_sheet(wb, name)
        row = _write_notes(ws, notes)
        for i, h in enumerate(("setting", "value", "notes"), start=1):
            ws.cell(row, i, h)
        _style_header(ws, row, 3)
        ws.column_dimensions["A"].width = 30
        ws.column_dimensions["B"].width = 14
        ws.column_dimensions["C"].width = 80
    existing = {}
    for r in range(1, ws.max_row + 1):
        key = _cell_text(ws.cell(r, 1).value).lower()
        if key and not key.startswith("#"):
            existing.setdefault(key, r)
    for key, value, note in items:
        r = existing.get(key.lower())
        if r is None:
            r = ws.max_row + 1
            ws.cell(r, 1, key)
            if note:
                ws.cell(r, 3, note)
        ws.cell(r, 2).value = value     # assign directly: cell(r, c, None) would not clear it
    _save(wb, path)
