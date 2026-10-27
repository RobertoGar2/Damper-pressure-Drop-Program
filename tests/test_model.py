"""Tests for the damper pressure-drop model. Run with:  python -m pytest"""

from pathlib import Path

import pytest

from damper_dp import physics as ph
from damper_dp.model import (
    DamperModel, DataError, DataPoint, FreeAreaEntry, ModelError, ModelLibrary,
    calibrate_geometry, create_model_folder, load_blade_table, load_free_area_table,
    load_geometry, load_points, load_size_rules, save_blade_table, save_geometry,
    save_size_rules, convert_to_workbook,
)
from damper_dp import workbook as xl

ROOT = Path(__file__).resolve().parent.parent
MODELS = ROOT / "data" / "models"


def make_points(sizes, c_of_ratio, geometry=None):
    """Exact (noise-free) test points generated from a known C_free(ratio)."""
    geometry = geometry or ph.DamperGeometry()
    points = []
    for w, h in sizes:
        ratio = ph.free_area_ft2(w, h, geometry) / ph.face_area_ft2(w, h)
        for v in (500, 1000, 1500, 2000, 2500):
            dp = c_of_ratio(ratio) * ph.velocity_pressure(v / ratio)
            points.append(DataPoint(w, h, v, dp))
    return points


# --- physics ----------------------------------------------------------------

def test_standard_air_density():
    assert ph.air_density(70, 0) == pytest.approx(0.075, rel=1e-4)


def test_density_drops_with_temperature_and_altitude():
    assert ph.air_density(120, 0) < ph.air_density(70, 0)
    assert ph.air_density(70, 5000) < ph.air_density(70, 0)


def test_velocity_pressure_at_4005_fpm_is_one_inch():
    assert ph.velocity_pressure(4005.0) == pytest.approx(1.0)


def test_free_area_from_geometry():
    g = ph.DamperGeometry()          # 1.5" frame each side, 0.5" blades at 6" pitch
    # 24x24: open 21 x 21 = 441 in2; 4 blades x 0.5 x 21 = 42 in2 blocked
    assert ph.blade_count(24, g) == 4
    assert ph.free_area_ft2(24, 24, g) == pytest.approx((441 - 42) / 144)


def test_free_area_breakdown_adds_up():
    g = ph.DamperGeometry(other_blockage_in2=5)
    bd = ph.free_area_breakdown(30, 18, g)
    total = bd.free_area_in2 + bd.frame_block_in2 + bd.blade_block_in2 + bd.other_block_in2
    assert total == pytest.approx(30 * 18)
    assert bd.n_blades == 3


def test_frame_too_large_raises():
    g = ph.DamperGeometry(frame_obstruction_width_in=5)
    with pytest.raises(ValueError):
        ph.free_area_ft2(10, 24, g)


# --- fitting ----------------------------------------------------------------

def test_fit_recovers_known_coefficient():
    model = DamperModel(make_points([(24, 24)], lambda r: 0.4))
    fit = model.fits[0]
    assert fit.c_free == pytest.approx(0.4)
    assert fit.r2 == pytest.approx(1.0)
    assert model.size_model.kind == "constant"


def test_size_model_recovers_linear_trend():
    points = make_points([(12, 12), (24, 24), (48, 48)], lambda r: 0.9 - 0.8 * r)
    sm = DamperModel(points).size_model
    assert sm.kind == "linear"
    assert sm.intercept == pytest.approx(0.9)
    assert sm.slope == pytest.approx(-0.8)


def test_inconsistent_free_area_in_data_raises():
    points = [DataPoint(24, 24, 1000, 0.05, 3.0), DataPoint(24, 24, 2000, 0.2, 3.1)]
    with pytest.raises(DataError):
        DamperModel(points)


# --- estimating -------------------------------------------------------------

def test_tested_size_reproduces_test_point():
    points = make_points([(24, 24)], lambda r: 0.4)
    model = DamperModel(points)
    target = points[2]
    est = model.estimate(24, 24, face_velocity_fpm=target.face_velocity_fpm)
    assert est.pressure_drop_in_wg == pytest.approx(target.pressure_drop_in_wg)
    assert est.tested is not None
    assert est.warnings == []


def test_untested_size_uses_size_model():
    c_of_ratio = lambda r: 0.9 - 0.8 * r  # noqa: E731
    model = DamperModel(make_points([(12, 12), (24, 24), (48, 48)], c_of_ratio))
    est = model.estimate(30, 30, airflow_cfm=5000)
    assert est.tested is None
    assert est.c_free == pytest.approx(c_of_ratio(est.free_ratio))


def test_cfm_and_fpm_inputs_agree():
    model = DamperModel(make_points([(24, 24)], lambda r: 0.4))
    by_cfm = model.estimate(24, 24, airflow_cfm=4000)
    by_fpm = model.estimate(24, 24, face_velocity_fpm=1000)   # 4 ft2 x 1000 fpm
    assert by_cfm.pressure_drop_in_wg == pytest.approx(by_fpm.pressure_drop_in_wg)


def test_lower_density_gives_lower_pressure_drop():
    model = DamperModel(make_points([(24, 24)], lambda r: 0.4))
    std = model.estimate(24, 24, airflow_cfm=4000)
    hot = model.estimate(24, 24, airflow_cfm=4000, temperature_f=150)
    assert hot.pressure_drop_in_wg < std.pressure_drop_in_wg


def test_extrapolation_warnings():
    model = DamperModel(make_points([(12, 12), (24, 24), (36, 36)], lambda r: 0.9 - 0.8 * r))
    est = model.estimate(60, 60, face_velocity_fpm=4000)
    assert len(est.warnings) >= 2


def test_must_give_exactly_one_flow_input():
    model = DamperModel(make_points([(24, 24)], lambda r: 0.4))
    with pytest.raises(ModelError):
        model.estimate(24, 24)
    with pytest.raises(ModelError):
        model.estimate(24, 24, airflow_cfm=4000, face_velocity_fpm=1000)


# --- files ------------------------------------------------------------------

def test_missing_column_is_reported(tmp_path):
    f = tmp_path / "bad.csv"
    f.write_text("width_in,height_in,face_velocity_fpm\n24,24,1000\n")
    with pytest.raises(DataError, match="pressure_drop_in_wg"):
        load_points(f)


def test_bad_number_reports_line(tmp_path):
    f = tmp_path / "bad.csv"
    f.write_text("# note\nwidth_in,height_in,face_velocity_fpm,pressure_drop_in_wg\n"
                 "24,24,1000,abc\n")
    with pytest.raises(DataError, match="line 3"):
        load_points(f)


def test_all_model_folders_load():
    """Every model folder must load. Catches data errors before a release."""
    lib = ModelLibrary.load(MODELS)
    problems = "\n".join(f"{name}: {err}" for name, err in lib.errors.items())
    assert not lib.errors, f"Model folders with data errors:\n{problems}"
    assert lib.models, "No damper models found in data/models"


# --- free area ----------------------------------------------------------------

TRUE_GEOMETRY = ph.DamperGeometry(1.625, 1.625, 0.6, 6.0, 5.0)
TABLE_SIZES = [(8, 8), (12, 12), (16, 16), (24, 24), (30, 30), (36, 24), (48, 48)]


def make_table(geometry=TRUE_GEOMETRY, sizes=TABLE_SIZES):
    return [FreeAreaEntry(w, h, ph.free_area_ft2(w, h, geometry)) for w, h in sizes]


def test_calibration_recovers_known_geometry():
    result = calibrate_geometry(make_table(), blade_pitch_in=6.0)
    g = result.geometry
    assert g.frame_obstruction_width_in == pytest.approx(1.625)
    assert g.frame_obstruction_height_in == pytest.approx(1.625)
    assert g.blade_thickness_in == pytest.approx(0.6, abs=1e-6)
    assert g.other_blockage_in2 == pytest.approx(5.0, abs=1e-4)
    assert result.max_error_pct < 1e-6


def test_calibration_with_separate_frame_values():
    true = ph.DamperGeometry(2.0, 1.5, 0.5, 6.0, 0.0)
    result = calibrate_geometry(make_table(true), 6.0, equal_frame=False)
    assert result.geometry.frame_obstruction_width_in == pytest.approx(2.0)
    assert result.geometry.frame_obstruction_height_in == pytest.approx(1.5)


def test_calibration_needs_enough_sizes():
    with pytest.raises(ModelError):
        calibrate_geometry(make_table(sizes=[(12, 12), (24, 24)]), 6.0)


def test_measured_free_area_takes_priority_over_geometry():
    table = [FreeAreaEntry(24, 24, 2.5)]
    model = DamperModel(make_points([(24, 24)], lambda r: 0.4), free_area_table=table)
    assert model.fits[0].free_area_ft2 == pytest.approx(2.5)
    assert "measured" in model.fits[0].free_area_source
    area, source = model.free_area_for(30, 30)          # not in the table
    assert source == "geometry estimate"
    assert area == pytest.approx(ph.free_area_ft2(30, 30, model.geometry))


def test_estimate_reports_free_area_source():
    table = [FreeAreaEntry(24, 24, 2.5)]
    model = DamperModel(make_points([(24, 24)], lambda r: 0.4), free_area_table=table)
    assert "measured" in model.estimate(24, 24, airflow_cfm=4000).free_area_source
    est = model.estimate(24, 24, airflow_cfm=4000, free_area_override_ft2=2.4)
    assert est.free_area_source == "entered override"


def test_free_area_table_rejects_duplicate_sizes(tmp_path):
    f = tmp_path / "fa.csv"
    f.write_text("width_in,height_in,free_area_ft2\n24,24,2.6\n24,24,2.7\n")
    with pytest.raises(DataError, match="more than once"):
        load_free_area_table(f)


def test_missing_free_area_table_is_optional(tmp_path):
    assert load_free_area_table(tmp_path / "missing.csv") == []


def test_save_geometry_round_trip_keeps_notes(tmp_path):
    f = tmp_path / "geometry.json"
    f.write_text('{"_notes": "keep me", "blade_pitch_in": 6}')
    save_geometry(TRUE_GEOMETRY, f)
    loaded, _ = load_geometry(f)
    assert loaded == TRUE_GEOMETRY
    assert "keep me" in f.read_text()


def test_sample_free_area_tables_calibrate_well():
    """Only checks the SAMPLE models, whose data is known to fit the geometry."""
    lib = ModelLibrary.load(MODELS)
    for name, model in lib.models.items():
        if not name.startswith("SAMPLE-"):
            continue
        result = calibrate_geometry(model.free_area_table, model.geometry.blade_pitch_in,
                                    blade_table=model.blade_table,
                                    size_rules=model.size_rules)
        assert result.max_error_pct < 5, name


# --- multiple damper models ---------------------------------------------------

def write_model(folder, c=0.4, sizes=((12, 12), (24, 24), (36, 36)), description=""):
    folder.mkdir(parents=True)
    rows = ["width_in,height_in,face_velocity_fpm,pressure_drop_in_wg"]
    for p in make_points(list(sizes), lambda r: c):
        rows.append(f"{p.width_in},{p.height_in},{p.face_velocity_fpm},{p.pressure_drop_in_wg}")
    (folder / "damper_data.csv").write_text("\n".join(rows) + "\n")
    if description:
        (folder / "geometry.json").write_text(f'{{"_description": "{description}"}}')


def test_library_loads_each_folder_as_a_model(tmp_path):
    write_model(tmp_path / "Model A", c=0.4, description="first")
    write_model(tmp_path / "Model B", c=0.2)
    lib = ModelLibrary.load(tmp_path)
    assert lib.names == ["Model A", "Model B"]
    assert lib.get("Model A").description == "first"
    assert lib.get("Model B").fits[0].c_free == pytest.approx(0.2)


def test_bad_model_folder_does_not_block_others(tmp_path):
    write_model(tmp_path / "Good")
    (tmp_path / "Bad").mkdir()
    (tmp_path / "Bad" / "damper_data.csv").write_text("width_in,height_in\n24,24\n")
    (tmp_path / "_ignored").mkdir()
    lib = ModelLibrary.load(tmp_path)
    assert lib.names == ["Good"]
    assert "Bad" in lib.errors
    assert "_ignored" not in lib.errors


def test_compare_models(tmp_path):
    write_model(tmp_path / "Low", c=0.2)
    write_model(tmp_path / "High", c=0.4)
    results = dict((n, e) for n, e, _ in
                   ModelLibrary.load(tmp_path).compare(24, 24, airflow_cfm=4000))
    assert results["High"].pressure_drop_in_wg == pytest.approx(
        2 * results["Low"].pressure_drop_in_wg)


def test_unknown_model_name_raises(tmp_path):
    write_model(tmp_path / "A")
    with pytest.raises(ModelError, match="Available: A"):
        ModelLibrary.load(tmp_path).get("B")


def test_create_model_folder(tmp_path):
    src = tmp_path / "Existing"
    write_model(src)
    save_geometry(TRUE_GEOMETRY, src / "geometry.json")
    folder = create_model_folder(tmp_path, "New Model", copy_geometry_from=src,
                                 description="copied")
    geometry, _ = load_geometry(folder / "geometry.json")
    assert geometry == TRUE_GEOMETRY
    assert (folder / "damper_data.csv").exists()
    assert (folder / "free_area_data.csv").exists()
    # No test data yet, so it is reported as an error rather than loaded.
    lib = ModelLibrary.load(tmp_path)
    assert "New Model" in lib.errors and "Existing" in lib.models


def test_create_model_folder_rejects_bad_or_duplicate_names(tmp_path):
    with pytest.raises(ModelError):
        create_model_folder(tmp_path, "bad/name")
    create_model_folder(tmp_path, "OK")
    with pytest.raises(ModelError, match="already exists"):
        create_model_folder(tmp_path, "OK")


def test_missing_models_folder_raises(tmp_path):
    with pytest.raises(DataError):
        ModelLibrary.load(tmp_path / "nope")


# --- blade table ---------------------------------------------------------------

def blade_table(*rows):
    return ph.BladeTable([ph.BladeRule(*r) for r in rows])


def test_blade_table_sets_blade_count():
    table = blade_table((10, 14, 1), (15, 20, 2))
    g = ph.DamperGeometry()
    assert ph.section_blades(12, g, table) == (1, "blade table")
    assert ph.section_blades(15, g, table) == (2, "blade table")
    assert ph.section_blades(20, g, table) == (2, "blade table")


def test_blade_table_gap_is_an_error():
    table = blade_table((10, 14, 1), (15, 20, 2))
    with pytest.raises(ValueError, match="gap"):
        ph.section_blades(14.5, ph.DamperGeometry(), table)


def test_height_outside_blade_table_uses_pitch():
    table = blade_table((10, 14, 1), (15, 20, 2))
    g = ph.DamperGeometry()          # 1.5" frame, 6" pitch
    n, source = ph.section_blades(30, g, table)
    assert n == ph.blade_count(30, g)
    assert "pitch" in source


def test_shared_boundary_uses_lower_row():
    table = blade_table((10, 15, 1), (15, 20, 2))
    table.validate()
    assert table.lookup(15) == 1


def test_overlapping_blade_rows_rejected():
    with pytest.raises(ValueError, match="overlap"):
        blade_table((10, 16, 1), (15, 20, 2)).validate()


def test_blade_table_changes_free_area():
    g = ph.DamperGeometry()
    design = ph.DamperDesign(g, blade_table((6, 30, 1)))
    one_blade = design.breakdown(24, 24)
    assert one_blade.n_blades == 1
    assert one_blade.free_area_in2 > ph.free_area_breakdown(24, 24, g).free_area_in2


def test_blade_table_file_round_trip(tmp_path):
    f = tmp_path / "blade_table.csv"
    f.write_text("# my note\nmin_height_in,max_height_in,blades\n15,20,2\n10,14,1\n")
    table = load_blade_table(f)
    assert [r.blades for r in table.rules] == [1, 2]          # sorted by height
    save_blade_table(table, f)
    assert "# my note" in f.read_text()
    assert load_blade_table(f) == table


def test_empty_blade_table_file(tmp_path):
    f = tmp_path / "blade_table.csv"
    f.write_text("# no rows yet\nmin_height_in,max_height_in,blades\n")
    assert load_blade_table(f).is_empty


# --- size rules and multiple sections -----------------------------------------------

def test_layout_splits_into_equal_sections():
    rules = ph.SizeRules(max_section_width_in=48, max_section_height_in=36)
    assert rules.layout(48, 36) == (1, 1)
    assert rules.layout(96, 72) == (2, 2)
    assert rules.layout(100, 40) == (3, 2)


def test_multi_section_free_area_is_sum_of_sections():
    g = ph.DamperGeometry()
    rules = ph.SizeRules(max_section_width_in=48)
    bd = ph.DamperDesign(g, size_rules=rules).breakdown(96, 24)
    single = ph.free_area_breakdown(48, 24, g)
    assert (bd.sections_wide, bd.sections_high) == (2, 1)
    assert bd.free_area_in2 == pytest.approx(2 * single.free_area_in2)


def test_joint_blockage():
    rules = ph.SizeRules(max_section_width_in=48, max_section_height_in=36,
                         joint_blockage_in=0.5)
    # 2 x 2 sections of 96 x 72: one vertical joint 72 long, one horizontal 96 long,
    # minus the 0.5 x 0.5 crossing counted twice.
    assert rules.joint_area_in2(96, 72, 2, 2) == pytest.approx(0.5 * (72 + 96) - 0.25)
    plain = ph.DamperDesign(size_rules=ph.SizeRules(max_section_width_in=48,
                                                    max_section_height_in=36))
    joint = ph.DamperDesign(size_rules=rules)
    diff = plain.breakdown(96, 72).free_area_in2 - joint.breakdown(96, 72).free_area_in2
    assert diff == pytest.approx(0.5 * (72 + 96) - 0.25)


def test_blade_table_uses_section_height():
    rules = ph.SizeRules(max_section_height_in=24)
    design = ph.DamperDesign(ph.DamperGeometry(), blade_table((10, 14, 1), (15, 20, 2)), rules)
    bd = design.breakdown(24, 36)                  # two sections, 18 in high each
    assert bd.sections_high == 2
    assert bd.n_blades == 2


def test_size_limits():
    rules = ph.SizeRules(min_width_in=8, min_height_in=8, max_width_in=120, max_height_in=96)
    rules.check(24, 24)
    with pytest.raises(ValueError, match="minimum width"):
        rules.check(6, 24)
    with pytest.raises(ValueError, match="maximum height"):
        rules.check(24, 100)


def test_estimate_rejects_size_outside_limits():
    rules = ph.SizeRules(max_width_in=48)
    model = DamperModel(make_points([(24, 24)], lambda r: 0.4), size_rules=rules)
    with pytest.raises(ModelError, match="maximum width"):
        model.estimate(60, 24, airflow_cfm=5000)


def test_multi_section_uses_tested_section_size():
    """Without joint blockage, two 24 x 24 sections at the same face velocity
    have the same pressure drop as one tested 24 x 24 damper."""
    rules = ph.SizeRules(max_section_width_in=24, max_section_height_in=24)
    model = DamperModel(make_points([(24, 24)], lambda r: 0.4), size_rules=rules)
    single = model.estimate(24, 24, face_velocity_fpm=1500)
    double = model.estimate(48, 24, face_velocity_fpm=1500)
    assert double.n_sections == 2
    assert double.pressure_drop_in_wg == pytest.approx(single.pressure_drop_in_wg)
    assert "section size" in double.source


def test_size_rules_file_round_trip(tmp_path):
    f = tmp_path / "size_rules.json"
    f.write_text('{"_notes": "keep", "min_width_in": 8, "max_section_width_in": null}')
    rules = load_size_rules(f)
    assert rules.min_width_in == 8 and rules.max_section_width_in is None
    save_size_rules(rules, f)
    assert "keep" in f.read_text()
    assert load_size_rules(f) == rules


def test_missing_rule_files_mean_no_limits(tmp_path):
    assert load_size_rules(tmp_path / "none.json") == ph.SizeRules()
    assert load_blade_table(tmp_path / "none.csv").is_empty


def test_calibration_with_blade_table_and_sections():
    true = ph.DamperGeometry(1.25, 1.25, 0.5, 6.0, 4.0)
    table_rows = blade_table((6, 11.99, 1), (12, 17.99, 2), (18, 23.99, 3), (24, 36, 4))
    rules = ph.SizeRules(max_section_width_in=36, max_section_height_in=36,
                         joint_blockage_in=0.25)
    design = ph.DamperDesign(true, table_rows, rules)
    sizes = [(8, 8), (12, 12), (18, 18), (24, 24), (36, 36), (48, 24), (72, 48)]
    measured = [FreeAreaEntry(w, h, design.free_area_ft2(w, h)) for w, h in sizes]
    result = calibrate_geometry(measured, 6.0, blade_table=table_rows, size_rules=rules)
    assert result.geometry.frame_obstruction_width_in == pytest.approx(1.25)
    assert result.geometry.blade_thickness_in == pytest.approx(0.5, abs=1e-6)
    assert result.geometry.other_blockage_in2 == pytest.approx(4.0, abs=1e-4)
    assert result.max_error_pct < 1e-6


# --- Excel workbook storage -----------------------------------------------------------

openpyxl = pytest.importorskip("openpyxl")


def excel_model(tmp_path, name="XL", c=0.4):
    """A file-based model converted to a workbook. Returns (folder, workbook path)."""
    folder = tmp_path / name
    write_model(folder, c=c)
    save_size_rules(ph.SizeRules(min_width_in=8, max_section_width_in=36), folder / "size_rules.json")
    save_blade_table(blade_table((6, 20, 2), (20.01, 40, 4)), folder / "blade_table.csv")
    model = DamperModel.from_folder(folder)
    return folder, convert_to_workbook(model)


def test_convert_to_workbook_gives_same_model(tmp_path):
    folder = tmp_path / "M"
    write_model(folder, c=0.4)
    save_size_rules(ph.SizeRules(max_section_width_in=36), folder / "size_rules.json")
    before = DamperModel.from_folder(folder).estimate(60, 24, airflow_cfm=6000)
    path = convert_to_workbook(DamperModel.from_folder(folder))
    after_model = DamperModel.from_folder(folder)
    assert after_model.workbook_path == path
    assert after_model.estimate(60, 24, airflow_cfm=6000).pressure_drop_in_wg == pytest.approx(
        before.pressure_drop_in_wg)
    assert not (folder / "damper_data.csv").exists()
    assert (folder / "_old_files" / "damper_data.csv").exists()


def test_rules_edited_in_excel_are_used(tmp_path):
    folder, path = excel_model(tmp_path)
    wb = openpyxl.load_workbook(path)
    ws = wb["Size rules"]
    for r in range(1, ws.max_row + 1):
        if ws.cell(r, 1).value == "max_section_width_in":
            ws.cell(r, 2).value = 20
        if ws.cell(r, 1).value == "min_width_in":
            ws.cell(r, 2).value = None                    # blank = no limit
    wb.save(path)
    model = DamperModel.from_folder(folder)
    assert model.size_rules.max_section_width_in == 20
    assert model.size_rules.min_width_in is None
    assert model.size_rules.layout(60, 24) == (3, 1)


def test_blade_table_edited_in_excel_is_used(tmp_path):
    folder, path = excel_model(tmp_path)
    wb = openpyxl.load_workbook(path)
    ws = wb["Blade table"]
    ws.append([40.01, 60, 6])
    wb.save(path)
    model = DamperModel.from_folder(folder)
    assert model.blade_table.lookup(50) == 6


def test_program_saves_keep_user_notes_and_columns(tmp_path):
    folder, path = excel_model(tmp_path)
    wb = openpyxl.load_workbook(path)
    wb["Test data"]["G6"] = "tested by lab A"          # user's own column
    wb.create_sheet("My notes")["A1"] = "keep me"
    wb.save(path)
    model = DamperModel.from_folder(folder)
    model.save_rules(ph.SizeRules(max_width_in=100), blade_table((6, 30, 3)))   # clears other rules
    model.save_geometry(TRUE_GEOMETRY)
    wb = openpyxl.load_workbook(path)
    assert wb["Test data"]["G6"].value == "tested by lab A"
    assert wb["My notes"]["A1"].value == "keep me"
    reloaded = DamperModel.from_folder(folder)
    assert reloaded.size_rules.max_width_in == 100
    assert reloaded.size_rules.max_section_width_in is None      # was 36, cleared by the save
    assert reloaded.size_rules.min_width_in is None
    assert [r.blades for r in reloaded.blade_table.rules] == [3]
    assert reloaded.geometry == TRUE_GEOMETRY


def test_workbook_notes_rows_and_extra_columns_are_ignored(tmp_path):
    folder = tmp_path / "Hand made"
    folder.mkdir()
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "test DATA"                                # sheet names are not case-sensitive
    ws.append(["My lab results, March"])                  # note above the header
    ws.append(["width_in", "height_in", "face_velocity_fpm", "pressure_drop_in_wg", "comment"])
    for p in make_points([(24, 24)], lambda r: 0.4):
        ws.append([p.width_in, p.height_in, p.face_velocity_fpm, p.pressure_drop_in_wg, "ok"])
    ws.append(["# 24 x 24 retest pending"])
    ws.append([])
    wb.save(folder / "model_data.xlsx")
    model = DamperModel.from_folder(folder)
    assert model.n_points == 5
    assert model.fits[0].c_free == pytest.approx(0.4)
    assert model.size_rules == ph.SizeRules() and model.blade_table.is_empty   # optional sheets


def test_formula_without_saved_value_is_reported(tmp_path):
    folder, path = excel_model(tmp_path)
    wb = openpyxl.load_workbook(path)
    ws = wb["Test data"]
    ws.cell(ws.max_row, 3).value = "=1000*2"             # never calculated by Excel
    wb.save(path)
    with pytest.raises(DataError, match="formula"):
        DamperModel.from_folder(folder)


def test_bad_value_in_settings_sheet_reports_row(tmp_path):
    folder, path = excel_model(tmp_path)
    wb = openpyxl.load_workbook(path)
    ws = wb["Size rules"]
    for r in range(1, ws.max_row + 1):
        if ws.cell(r, 1).value == "max_width_in":
            ws.cell(r, 2).value = "wide"
            bad_row = r
    wb.save(path)
    with pytest.raises(DataError, match=f"row {bad_row}"):
        DamperModel.from_folder(folder)


def test_new_excel_model_copies_design(tmp_path):
    src, _ = excel_model(tmp_path, "Source")
    folder = create_model_folder(tmp_path, "New", copy_geometry_from=src,
                                 description="copied", use_excel=True)
    assert (folder / "model_data.xlsx").exists()
    assert not (folder / "damper_data.csv").exists()
    reader = xl.WorkbookReader(folder / "model_data.xlsx")
    assert reader.has_sheet("Blade table") and reader.has_sheet("Size rules")
    wb = openpyxl.load_workbook(folder / "model_data.xlsx")
    ws = wb["Test data"]
    for p in make_points([(24, 24)], lambda r: 0.3):
        ws.append([p.width_in, p.height_in, p.face_velocity_fpm, p.pressure_drop_in_wg])
    wb.save(folder / "model_data.xlsx")
    model = DamperModel.from_folder(folder)
    assert model.description == "copied"
    assert model.size_rules.max_section_width_in == 36
    assert model.blade_table.lookup(30) == 4


def test_workbook_takes_priority_over_files(tmp_path):
    folder, path = excel_model(tmp_path)
    write_model(tmp_path / "tmp", c=0.9)                    # stray CSV file in the folder
    (tmp_path / "tmp" / "damper_data.csv").rename(folder / "damper_data.csv")
    model = DamperModel.from_folder(folder)
    assert model.workbook_path == path
    assert model.fits[0].c_free == pytest.approx(0.4)


# --- size-model shape, blending, joints in the coefficient, fit diagnostics ---

import math  # noqa: E402

from damper_dp.model import BLEND_RANGE, fit_exponent  # noqa: E402

SIZES_4 = [(12, 12), (18, 18), (24, 24), (48, 48)]


def test_curved_size_model_recovers_curved_trend():
    c_of_ratio = lambda r: 0.25 + 0.6 * (1 / r - 1) ** 2  # noqa: E731
    rules = ph.SizeRules(size_model="curved")
    model = DamperModel(make_points(SIZES_4, c_of_ratio), size_rules=rules)
    sm = model.size_model
    assert sm.kind == "curved"
    assert sm.intercept == pytest.approx(0.25, rel=1e-6)
    assert sm.slope == pytest.approx(0.6, rel=1e-6)
    assert model.alternate_size_model.kind == "linear"
    est = model.estimate(30, 30, airflow_cfm=5000)
    assert est.c_free == pytest.approx(c_of_ratio(est.free_ratio))


def test_linear_size_model_is_the_default():
    model = DamperModel(make_points(SIZES_4, lambda r: 0.9 - 0.8 * r))
    assert model.size_rules.size_model == "linear"
    assert model.size_model.kind == "linear"
    assert set(model.size_models) == {"linear", "curved"}


def test_bad_size_model_name_rejected():
    with pytest.raises(ValueError):
        ph.SizeRules(size_model="cubic").validate()


def test_size_model_rule_round_trips_through_json_and_excel(tmp_path):
    save_size_rules(ph.SizeRules(size_model="curved"), tmp_path / "size_rules.json")
    assert load_size_rules(tmp_path / "size_rules.json").size_model == "curved"
    folder, path = excel_model(tmp_path)
    model = DamperModel.from_folder(folder)
    model.save_rules(ph.SizeRules(size_model="curved"), model.blade_table)
    assert DamperModel.from_folder(folder).size_model.kind == "curved"


def test_blank_size_model_in_excel_means_linear(tmp_path):
    folder, path = excel_model(tmp_path)
    wb = openpyxl.load_workbook(path)
    ws = wb["Size rules"]
    for r in range(1, ws.max_row + 1):
        if ws.cell(r, 1).value == "size_model":
            ws.cell(r, 2).value = None
    wb.save(path)
    assert DamperModel.from_folder(folder).size_rules.size_model == "linear"


def offset_model():
    """Tested sizes on a straight line, except 24 x 24 sits 0.05 above it."""
    line = lambda r: 0.9 - 0.8 * r  # noqa: E731
    pts = make_points([(12, 12), (18, 18), (36, 36), (48, 48)], line)
    pts += make_points([(24, 24)], lambda r: line(r) + 0.05)
    return DamperModel(pts)


def test_blending_is_continuous_at_a_tested_size():
    model = offset_model()
    at = model.estimate(24, 24, face_velocity_fpm=1000)
    near = model.estimate(24.05, 24.05, face_velocity_fpm=1000)
    assert at.tested is not None
    assert near.tested is None
    assert near.nearest_tested.label == "24 x 24"
    assert near.c_free == pytest.approx(at.c_free, abs=0.002)


def test_blend_fades_out_with_distance():
    model = offset_model()
    sm = model.size_model
    weights = []
    for w in (24.5, 25.5, 26.5):
        est = model.estimate(w, w, face_velocity_fpm=1000)
        weights.append(est.blend_weight)
        expected = sm.c_free(est.free_ratio) + est.blend_weight * est.blend_offset
        assert est.c_free == pytest.approx(expected)
    assert weights[0] > weights[1] > weights[2] > 0
    far = model.estimate(24 * (1 + BLEND_RANGE) * 1.01, 24 * (1 + BLEND_RANGE) * 1.01,
                         face_velocity_fpm=1000)
    assert far.blend_weight == 0
    assert far.c_free == pytest.approx(sm.c_free(far.free_ratio))


def test_free_area_override_does_not_blend():
    model = offset_model()
    est = model.estimate(24.5, 24.5, face_velocity_fpm=1000, free_area_override_ft2=2.8)
    assert est.blend_weight == 0
    assert est.c_free == pytest.approx(model.size_model.c_free(est.free_ratio))


def test_joint_blockage_raises_multi_section_coefficient():
    """The size model is read at the assembly's ratio, joints included."""
    line = lambda r: 0.9 - 0.8 * r  # noqa: E731
    pts = make_points([(12, 12), (18, 18), (30, 30), (40, 40)], line)
    no_joint = DamperModel(pts, size_rules=ph.SizeRules(max_section_width_in=48))
    joint = DamperModel(pts, size_rules=ph.SizeRules(max_section_width_in=48,
                                                     joint_blockage_in=1.0))
    a = no_joint.estimate(96, 36, face_velocity_fpm=1000)
    b = joint.estimate(96, 36, face_velocity_fpm=1000)
    assert b.free_ratio < a.free_ratio
    assert b.coeff_ratio == pytest.approx(b.free_ratio)
    assert b.c_free == pytest.approx(line(b.free_ratio))
    assert b.c_free > a.c_free


def test_tested_section_with_joints_is_adjusted_by_size_model():
    line = lambda r: 0.9 - 0.8 * r  # noqa: E731
    pts = make_points([(12, 12), (18, 18), (24, 24)], line)    # all single-section
    rules = ph.SizeRules(max_section_width_in=24, max_section_height_in=24, joint_blockage_in=1.0)
    model = DamperModel(pts, size_rules=rules)
    est = model.estimate(48, 24, face_velocity_fpm=1500)
    assert est.nearest_tested.label == "24 x 24"
    assert est.blend_weight == pytest.approx(1.0)
    assert est.c_free == pytest.approx(line(est.free_ratio))   # exact data on the line


def test_fit_exponent_and_residuals():
    model = DamperModel(make_points([(24, 24)], lambda r: 0.4))
    fit = model.fits[0]
    assert fit.exponent == pytest.approx(2.0)
    assert fit.exponent_ok
    assert all(abs(r) < 1e-9 for r in fit.residuals_pct)
    v = [500.0, 1000.0, 2000.0, 3000.0]
    assert fit_exponent(np_array(v), np_array([x ** 1.8 for x in v])) == pytest.approx(1.8)


def test_exponent_far_from_two_warns():
    pts = [DataPoint(24, 24, v, 1e-7 * v ** 1.6) for v in (500, 1000, 1500, 2000, 2500)]
    model = DamperModel(pts)
    assert not model.fits[0].exponent_ok
    est = model.estimate(24, 24, face_velocity_fpm=1000)
    assert any("V^1.60" in w for w in est.warnings)


def test_low_velocity_warning():
    model = DamperModel(make_points([(24, 24)], lambda r: 0.4))
    assert model.estimate(24, 24, face_velocity_fpm=500).warnings == []
    est = model.estimate(24, 24, face_velocity_fpm=300)
    assert any("below the lowest tested velocity" in w for w in est.warnings)


def np_array(x):
    import numpy as np
    return np.array(x, dtype=float)
