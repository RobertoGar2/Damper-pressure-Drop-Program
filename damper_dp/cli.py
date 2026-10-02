"""Command-line interface.

Examples
--------
    python -m damper_dp.cli --list-models
    python -m damper_dp.cli --model SAMPLE-FSD-100 --list
    python -m damper_dp.cli --model SAMPLE-FSD-100 --width 24 --height 24 --cfm 4000
    python -m damper_dp.cli --compare --width 24 --height 24 --cfm 4000
    python -m damper_dp.cli --model SAMPLE-FSD-100 --free-area-only --width 30 --height 18
    python -m damper_dp.cli --model SAMPLE-FSD-100 --calibrate   (add --save to keep it)
    python -m damper_dp.cli --model SAMPLE-FSD-100 --to-excel    (move data into one workbook)

--model can be left out when there is only one damper model.
"""

from __future__ import annotations

import argparse
import math
import sys

from . import APP_NAME, __version__
from . import physics as ph
from .model import (
    DamperModel, DataError, ModelError, ModelLibrary, calibrate_geometry, comparison_stats,
    convert_to_workbook,
)
from .paths import default_models_dir


def print_models(lib: ModelLibrary) -> None:
    print(f"Models folder: {lib.models_dir}\n")
    for name, m in lib.models.items():
        desc = f" - {m.description}" if m.description else ""
        print(f"  {name}{desc}")
        print(f"      {len(m.fits)} tested sizes, {m.n_points} test points, "
              f"{len(m.free_area_table)} measured free areas; data in {m.storage}")
    for name, err in lib.errors.items():
        first_line = err.splitlines()[0]
        print(f"  {name}  [NOT LOADED: {first_line}]")


def print_compare(lib: ModelLibrary, width: float, height: float, **conditions) -> None:
    results = lib.compare(width, height, **conditions)
    results.sort(key=lambda r: (r[1] is None,
                                r[1].pressure_drop_in_wg if r[1] is not None else 0))
    first = next((e for _, e, _ in results if e is not None), None)
    if first is not None:
        print(f'Comparison  {width:g}" x {height:g}" at {first.airflow_cfm:,.0f} cfm '
              f"({first.face_velocity_fpm:,.0f} fpm face), air density "
              f"{first.air_density:.4f} lb/ft3\n")
    print(f"{'Model':<22} {'dP in.wg':>9} {'Free ft2':>9} {'Free %':>7} {'C face':>7}  Basis")
    for name, est, err in results:
        if est is None:
            print(f"{name:<22} {'-':>9}  ERROR: {err}")
            continue
        print(f"{name:<22} {est.pressure_drop_in_wg:>9.3f} {est.free_area_ft2:>9.3f} "
              f"{est.free_ratio:>7.0%} {est.c_face:>7.3f}  {est.source}")
        for w in est.warnings:
            print(f"{'':<22}   WARNING: {w}")


def print_fits(model: DamperModel) -> None:
    print(f"Model:      {model.name}" + (f" - {model.description}" if model.description else ""))
    print(f"Data:       {model.data_source}   ({model.storage})")
    print(f"Geometry:   {model.geometry_source}")
    print(f"Free areas: {model.free_area_table_source} ({len(model.free_area_table)} sizes)")
    print(f"Size model: {model.size_model.describe()}")
    print(f"Size rules: {model.size_rules.describe()}")
    if model.blade_table.is_empty:
        print(f"Blades:     blade pitch {model.geometry.blade_pitch_in:g} in (no blade table)\n")
    else:
        rows = ", ".join(f"{r.label}: {r.blades}" for r in model.blade_table.rules)
        print(f"Blades:     {rows}  (section height: blades)\n")
    print(f"{'Size':>10} {'Pts':>4} {'Face ft2':>9} {'Free ft2':>9} {'Free %':>7} "
          f"{'C free':>8} {'C face':>8} {'R2':>7}  Free area from")
    for f in model.fits:
        r2 = "n/a" if math.isnan(f.r2) else f"{f.r2:.3f}"
        print(f"{f.label:>10} {f.n_points:>4} {f.face_area_ft2:>9.2f} {f.free_area_ft2:>9.2f} "
              f"{f.free_ratio:>7.0%} {f.c_free:>8.3f} {f.c_face:>8.3f} {r2:>7}  "
              f"{f.free_area_source}")


def print_geometry(g: ph.DamperGeometry) -> None:
    print(f"  Frame left/right   {g.frame_obstruction_width_in:.4f} in")
    print(f"  Frame top/bottom   {g.frame_obstruction_height_in:.4f} in")
    print(f"  Blade thickness    {g.blade_thickness_in:.4f} in")
    print(f"  Blade pitch        {g.blade_pitch_in:.4f} in")
    print(f"  Other blockage     {g.other_blockage_in2:.2f} in2")


def print_comparison(rows) -> None:
    print(f"{'Size':>10} {'Measured':>10} {'Estimate':>10} {'Error':>8}")
    for r in rows:
        est = "   none" if math.isnan(r.estimated_ft2) else f"{r.estimated_ft2:>10.3f}"
        err = "" if math.isnan(r.estimated_ft2) else f"{r.error_pct:>+7.1f}%"
        print(f"{r.label:>10} {r.measured_ft2:>10.3f} {est} {err:>8}")
    rms, worst = comparison_stats(rows)
    if not math.isnan(rms):
        print(f"RMS error {rms:.3f} ft2, largest error {worst:.1f}%")


def free_area_report(model: DamperModel, width: float, height: float) -> None:
    try:
        model.size_rules.check(width, height)
    except ValueError as e:
        print(f"  NOTE: {e}")
    bd = model.design.breakdown(width, height)
    face = bd.face_area_in2
    print(f'{model.name}  {width:g}" x {height:g}" - free area from geometry')
    print(f"  Free area          {bd.free_area_ft2:.3f} ft2 ({bd.free_area_in2:,.1f} in2), "
          f"{bd.free_ratio:.1%} of face")
    print(f"  Face area          {bd.face_area_ft2:.3f} ft2")
    if bd.n_sections > 1:
        print(f"  Sections           {bd.sections_wide} wide x {bd.sections_high} high, "
              f"each {bd.section_width_in:.4g} x {bd.section_height_in:.4g} in")
    print(f"  Clear opening      {bd.open_width_in:.4g} x {bd.open_height_in:.4g} in "
          f"{'per section' if bd.n_sections > 1 else ''}")
    print(f"  Blades             {bd.n_blades}{' per section' if bd.n_sections > 1 else ''} "
          f"({bd.blade_source})")
    print(f"  Blocked by frame   {bd.frame_block_in2:,.1f} in2 ({bd.frame_block_in2 / face:.1%})")
    print(f"  Blocked by blades  {bd.blade_block_in2:,.1f} in2 ({bd.blade_block_in2 / face:.1%})")
    print(f"  Other blockage     {bd.other_block_in2:,.1f} in2 ({bd.other_block_in2 / face:.1%})")
    if bd.joint_block_in2:
        print(f"  Section joints     {bd.joint_block_in2:,.1f} in2 ({bd.joint_block_in2 / face:.1%})")
    entry = model.measured_free_area(width, height)
    if entry is not None:
        err = (bd.free_area_ft2 - entry.free_area_ft2) / entry.free_area_ft2 * 100
        print(f"  Measured (table)   {entry.free_area_ft2:.3f} ft2 "
              f"(geometry estimate differs by {err:+.1f}%; estimates use the measured value)")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="damper_dp.cli",
        description=f"{APP_NAME} v{__version__}: estimate fire/smoke damper pressure drop.",
    )
    parser.add_argument("--models-dir", default=str(default_models_dir()),
                        help="folder holding one sub-folder per damper model")
    parser.add_argument("--model", help="damper model (folder name) to use")
    parser.add_argument("--list-models", action="store_true", help="list the damper models")
    parser.add_argument("--compare", action="store_true",
                        help="estimate every damper model for the same size and airflow")
    parser.add_argument("--list", action="store_true",
                        help="show the fitted sizes of a model and exit")
    parser.add_argument("--free-area-only", action="store_true",
                        help="show the free-area breakdown for --width x --height")
    parser.add_argument("--calibrate", action="store_true",
                        help="fit the geometry settings to the measured free-area table")
    parser.add_argument("--separate-frame", action="store_true",
                        help="with --calibrate: fit left/right and top/bottom frame separately")
    parser.add_argument("--save", action="store_true",
                        help="with --calibrate: save the fitted geometry to the model")
    parser.add_argument("--to-excel", action="store_true",
                        help="move the model's data files into one Excel workbook")
    parser.add_argument("--width", type=float, help="damper width, in")
    parser.add_argument("--height", type=float, help="damper height, in")
    flow = parser.add_mutually_exclusive_group()
    flow.add_argument("--cfm", type=float, help="airflow, cfm")
    flow.add_argument("--fpm", type=float, help="face velocity, fpm")
    parser.add_argument("--temp", type=float, default=70.0, help="air temperature, F (default 70)")
    parser.add_argument("--altitude", type=float, default=0.0, help="altitude, ft (default 0)")
    parser.add_argument("--free-area", type=float, help="free area override, ft2")
    args = parser.parse_args(argv)

    try:
        lib = ModelLibrary.load(args.models_dir)
    except DataError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    if args.list_models:
        print_models(lib)
        return 0

    if args.compare:
        if args.width is None or args.height is None or (args.cfm is None and args.fpm is None):
            parser.error("--compare needs --width, --height and either --cfm or --fpm")
        print_compare(lib, args.width, args.height, airflow_cfm=args.cfm,
                      face_velocity_fpm=args.fpm, temperature_f=args.temp,
                      altitude_ft=args.altitude)
        return 0

    # Everything below works on a single model.
    if args.model is None:
        if len(lib.models) == 1:
            args.model = lib.names[0]
        else:
            names = ", ".join(lib.names) or "none"
            parser.error(f"choose a damper model with --model (available: {names}), "
                         f"or use --compare")
    if args.model in lib.errors:
        print(f"Error: model '{args.model}' could not be loaded:\n{lib.errors[args.model]}",
              file=sys.stderr)
        return 1
    try:
        model = lib.get(args.model)
    except ModelError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    if args.list:
        print_fits(model)
        if model.free_area_table:
            print("\nMeasured free areas vs. current geometry estimate:")
            print_comparison(model.free_area_comparison())
        return 0

    if args.calibrate:
        try:
            result = calibrate_geometry(model.free_area_table, model.geometry.blade_pitch_in,
                                        equal_frame=not args.separate_frame,
                                        blade_table=model.blade_table,
                                        size_rules=model.size_rules)
        except ModelError as e:
            print(f"Error: {e}", file=sys.stderr)
            return 1
        print("Fitted geometry:")
        print_geometry(result.geometry)
        print()
        print_comparison(result.rows)
        if args.save:
            try:
                model.save_geometry(result.geometry)
            except (DataError, ModelError, OSError) as e:
                print(f"Error saving: {e}", file=sys.stderr)
                return 1
            print(f"\nSaved to {model.name}'s {model.storage}")
        else:
            print("\nRun again with --save to save these values to the model.")
        return 0

    if args.to_excel:
        try:
            path = convert_to_workbook(model)
        except (DataError, ModelError, OSError) as e:
            print(f"Error: {e}", file=sys.stderr)
            return 1
        print(f"Wrote {path}\nThe old CSV/JSON files were moved to {path.parent / '_old_files'}")
        return 0

    if args.free_area_only:
        if args.width is None or args.height is None:
            parser.error("--free-area-only needs --width and --height")
        try:
            free_area_report(model, args.width, args.height)
        except ValueError as e:
            print(f"Error: {e}", file=sys.stderr)
            return 1
        return 0

    if args.width is None or args.height is None or (args.cfm is None and args.fpm is None):
        parser.error("give --width, --height and either --cfm or --fpm "
                     "(or use --list, --list-models or --compare)")

    try:
        est = model.estimate(args.width, args.height, airflow_cfm=args.cfm,
                             face_velocity_fpm=args.fpm, temperature_f=args.temp,
                             altitude_ft=args.altitude, free_area_override_ft2=args.free_area)
    except ModelError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    print(f'{model.name}  {est.width_in:g}" x {est.height_in:g}"')
    print(f"  Pressure drop      {est.pressure_drop_in_wg:.3f} in. wg  ({est.pressure_drop_pa:,.0f} Pa)")
    print(f"  Airflow            {est.airflow_cfm:,.0f} cfm")
    print(f"  Face velocity      {est.face_velocity_fpm:,.0f} fpm")
    print(f"  Free-area velocity {est.free_velocity_fpm:,.0f} fpm")
    print(f"  Free area          {est.free_area_ft2:.3f} ft2 ({est.free_ratio:.0%} of face), "
          f"{est.free_area_source}")
    if est.n_sections > 1:
        print(f"  Sections           {est.sections_wide} wide x {est.sections_high} high, "
              f"each {est.section_width_in:.4g} x {est.section_height_in:.4g} in")
    print(f"  Blades             {est.blades_per_section}"
          f"{' per section' if est.n_sections > 1 else ''} ({est.blade_source})")
    print(f"  Air density        {est.air_density:.4f} lb/ft3")
    print(f"  C (free / face)    {est.c_free:.4f} / {est.c_face:.4f}")
    print(f"  Basis              {est.source}")
    for w in est.warnings:
        print(f"  WARNING: {w}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
