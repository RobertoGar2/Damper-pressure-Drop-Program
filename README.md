# Damper Pressure Drop Estimator

A desktop tool that estimates the pressure drop of fire/smoke dampers for any size and airflow, based on your own test data.

It handles multiple damper models (product lines), each with its own test data, free areas and geometry. You give it test data (damper size, face velocity, measured pressure drop) for the sizes you have tested. It fits a loss coefficient for each size, learns how that coefficient changes with size, and then estimates pressure drop for any damper size, airflow, air temperature, and altitude.

> **Note:** The two models in `data/models` (`SAMPLE-FSD-100` and `SAMPLE-FSD-200`) contain made-up sample data so the program runs out of the box. Replace them with your real damper models before relying on any results.

## Features

- **Multiple damper models**, each in its own folder, selected from a drop-down
- **Compare models** side by side for the same size and airflow, with overlaid curves
- **Blade table**: set the actual number of blades by section height, instead of relying on the blade pitch
- **Multi-section dampers**: size limits and a maximum single-section size per model; larger dampers are split into equal sections automatically
- **Per-model rules**: every damper model has its own blade table, size limits and geometry, editable in the program or in Excel
- **Excel workbooks**: a model can keep all of its data in one `model_data.xlsx`, and changes made in the program are written back to it
- Desktop app (Tkinter) with an input panel, results, and a pressure drop vs. airflow plot
- Loss coefficients based on the damper's actual **free area**, not just the nominal size
- **Free-area estimator** with a drawing of the damper face and a breakdown of what blocks the opening (frame, blades, other)
- Measured free-area table, used directly for the sizes it lists
- **Geometry calibration**: fits frame, blade and blockage dimensions to your measured free areas, so every other size gets a consistent estimate
- Estimates for untested sizes using a size model fitted across all tested sizes, as a straight line or a physically shaped curve (chosen per model, both shown for comparison)
- Smooth results next to tested sizes: untested sizes blend towards the nearest tested size's own coefficient, so the estimate doesn't jump
- Fit diagnostics for every tested size: the fitted exponent n of ΔP = kVⁿ and the residual of every test point
- Air density correction for temperature and altitude
- Warnings when an estimate is an extrapolation beyond the test data
- Command-line interface for quick lookups or scripting
- Builds to a standalone Windows `.exe` with PyInstaller

## Quick start

Requires Python 3.10 or newer.

```bash
git clone https://github.com/RobertoGar2/Damper-pressure-Drop-Program.git
cd Damper-pressure-Drop-Program
pip install -r requirements.txt
python main.py
```

Command line:

```bash
python -m damper_dp.cli --list-models                                    # list damper models
python -m damper_dp.cli --compare --width 24 --height 24 --cfm 4000       # compare all models
python -m damper_dp.cli --model SAMPLE-FSD-100 --width 24 --height 24 --cfm 4000
python -m damper_dp.cli --model SAMPLE-FSD-100 --width 30 --height 20 --fpm 2000 --temp 120 --altitude 5000
python -m damper_dp.cli --model SAMPLE-FSD-100 --list                    # fitted sizes
python -m damper_dp.cli --model SAMPLE-FSD-100 --free-area-only --width 30 --height 18
python -m damper_dp.cli --model SAMPLE-FSD-100 --calibrate --save        # fit and save geometry
python -m damper_dp.cli --model SAMPLE-FSD-100 --to-excel                # move data into one workbook
```

`--model` can be left out when there is only one damper model.

## Damper models

Each damper model has its own folder under `data/models`. The folder name is the model name shown in the program. Everything about a model (test data, free areas, geometry, blade table and size rules) belongs to that model only, so every model can have different blade rules and size limits.

A model keeps its data in **either** separate files **or** one Excel workbook (see [Excel workbooks](#excel-workbooks)):

```
data/models/
├── FSD-100/
│   ├── damper_data.csv       # pressure-drop test data (required)
│   ├── free_area_data.csv    # measured free areas (optional)
│   ├── geometry.json         # frame and blade dimensions (optional)
│   ├── blade_table.csv       # blades by section height (optional)
│   └── size_rules.json       # size limits and multi-section rules (optional)
└── FSD-200/
    └── model_data.xlsx       # the same data as sheets in one workbook
```

To add a model, click **New model...** in the program (or **File > New damper model...**). It creates the folder with empty data templates and can copy the geometry from the current model. Fill in the files and press **F5**. You can also copy an existing model folder and rename it.

A model appears in the drop-down once its `damper_data.csv` has test data. A folder with a problem in its files is skipped with a message, and the other models still load. Folders starting with `_` or `.` are ignored, which is handy for keeping old or work-in-progress models out of the list.

Add a short description, shown next to the drop-down, as `"_description"` in the model's `geometry.json`.

The **Compare models** tab estimates the same size and conditions with every selected model, lists them from lowest to highest pressure drop, and overlays their curves. Double-click a row to open that model in the Estimate tab.

## Excel workbooks

Instead of separate CSV/JSON files, a model can keep all of its data in one Excel workbook named `model_data.xlsx` in its folder. When that workbook exists, the program reads the model **only** from it. The sample model `SAMPLE-FSD-200` is set up this way.

| Sheet | Contents |
|---|---|
| **Test data** (required) | `width_in`, `height_in`, `face_velocity_fpm`, `pressure_drop_in_wg`, optional `free_area_ft2` |
| **Free area** | `width_in`, `height_in`, `free_area_ft2` |
| **Blade table** | `min_height_in`, `max_height_in`, `blades` |
| **Size rules** | one rule per row: name in column A, value in column B, notes in column C |
| **Geometry** | one setting per row in the same layout, plus an optional `description` row |

Rules for editing in Excel:

- Sheet names are not case-sensitive. Only **Test data** is required; missing sheets mean no measured free areas, no blade table, no size limits and default geometry.
- In table sheets, the header row can be anywhere in the first 30 rows, so you can put notes above it. Rows starting with `#` and blank rows are ignored, and you can add your own columns (comments, test dates) to the right.
- In the Size rules sheet, leave a value blank (or write `none`) for no limit.
- Avoid formulas in the data sheets. If a cell holds a formula Excel never calculated (for example after the program saved the file), the program asks you to open and save the workbook in Excel first.
- Save the workbook and press **F5** in the program to reload.

**Editing in the program:** the Free area and Sizes & blades tabs work the same for both storage types. When you save, the program writes the change into the model's own workbook, updating only the sheet that changed and keeping your notes, extra columns and other sheets. **Close the workbook in Excel first**, because Excel locks open files and the save will fail with a message.

**Switching an existing model to Excel:** select it and use **File > Convert model to Excel workbook...** (or `python -m damper_dp.cli --model NAME --to-excel`). The workbook gets all of the model's data, and the old files are moved to an `_old_files` sub-folder as a backup. **New model...** asks whether the new model should use a workbook. **Open data** opens the workbook in Excel, or the folder for file-based models.

## Adding your test data

Edit `damper_data.csv` in the model's folder. Each row is one test point:

| Column | Required | Description |
|---|---|---|
| `width_in` | yes | Nominal damper width, in |
| `height_in` | yes | Nominal damper height, in |
| `face_velocity_fpm` | yes | Face velocity based on nominal W x H, fpm |
| `pressure_drop_in_wg` | yes | Measured pressure drop, in. wg |
| `free_area_ft2` | no | Free area for this size, ft². Use the same value on every row of a size. Usually better kept in the free-area table (below). |

Lines starting with `#` are notes and are ignored. Press **F5** in the app to reload after editing.

Tips for good results:

- Include **3 or more sizes**, spread from small to large. With fewer sizes, the program uses one average coefficient for every size.
- Use **4 or more velocities per size**, covering the range you'll estimate.
- Keep all data from the **same test configuration** (for example, AMCA 500-D figure 5.3). Pressure drop differs noticeably between configurations.
- Test data is assumed to be at **standard air** (0.075 lb/ft³), which is how AMCA test reports present it.
- Check the **n** column in the Test data & model tab (or `--list`). It is the exponent of ΔP = kVⁿ fitted to each size and should be close to 2. A value outside 1.8 to 2.2 is flagged, because the velocity-squared fit then hides a problem with the data. The residuals show how far each test point is from the fit.

## Free area

The free area is the open area left after the frame, sleeve, blades and linkage block part of the nominal opening. The air speeds up through it, so it drives the pressure drop. For each size, the program takes the free area from the first of these that has it:

1. a `free_area_ft2` value in the model's `damper_data.csv`
2. the model's **measured free-area table**, `free_area_data.csv`
3. the **geometry estimate** from the model's `geometry.json`

### Measured free-area table

`free_area_data.csv` lists measured free areas, one row per size: `width_in, height_in, free_area_ft2`. The values can come from CAD, physical measurement, or the manufacturer's free-area table. It can include sizes you have no pressure-drop test data for.

### Geometry estimate

For sizes not in the table, the free area is calculated from the model's `geometry.json`:

| Setting | Description |
|---|---|
| `frame_obstruction_width_in` | Frame/sleeve obstruction on each side (left and right), in |
| `frame_obstruction_height_in` | Frame/sleeve obstruction on each side (top and bottom), in |
| `blade_thickness_in` | Blade thickness facing the airflow when fully open, in |
| `blade_pitch_in` | Blade spacing, in. Blade count = **open** height (section height minus the top and bottom frame) / pitch, rounded up. Only used for section heights not in the blade table, which is looked up by the **nominal** section height. |
| `other_blockage_in2` | Other blockage per section (linkage, jackshaft, blade stops), in² |

```
open width  = W - 2 x frame_width
open height = H - 2 x frame_height
free area   = open width x open height - blades x blade thickness x open width - other
```

Blades are assumed to be horizontal and fully open. The values in the repository are placeholders.

### Calibrating the geometry

With measured free areas for 3 or more sizes, the program can fit the geometry settings to them. In the **Free area** tab, click **Calibrate geometry**, check the fitted values make physical sense, then click **Save geometry**. From the command line, use `--model <name> --calibrate --save`. Each model is calibrated separately.

Blade counts come from the blade table where it covers the size, and multi-section sizes are split by the size rules, exactly as for estimates. The calibration fits the frame obstruction (one value for all sides, or left/right and top/bottom separately), the blade thickness and the other blockage by least squares. The blade pitch is not fitted, because the blade count changes in steps, so enter your real pitch first. The table under the drawing shows the measured vs. estimated free area for every size, so you can see how well the geometry matches.

## Blade table

By default the blade count comes from the blade pitch (open height / pitch, rounded up), which doesn't always match how dampers are really built. `blade_table.csv` sets the blade count directly by **section height**:

```
min_height_in,max_height_in,blades
10,14.99,1
15,20,2
```

Edit it in the **Sizes & blades** tab (select a row to change it, or use the boxes under the table to add one) or in any text editor or Excel.

- Each range includes both ends. A height on a boundary shared by two rows uses the lower row.
- Leave no gaps between rows. With rows `10-14` and `15-20`, a 14.5" section falls in the gap and is reported as an error; write `10-14.99` instead.
- Overlapping rows are rejected.
- Heights in the table are **nominal section heights**, the way blade schedules are usually written. The pitch rule used outside the table works on the open height instead, because blades only fill the clear opening.
- Sections taller or shorter than the whole table fall back to the blade pitch, with a warning on the estimate.
- An empty table (header only) means the blade pitch is always used.

## Size rules and multi-section dampers

`size_rules.json` sets each model's size limits and when it is built in sections:

```json
{
  "min_width_in": 8,
  "min_height_in": 8,
  "max_width_in": 144,
  "max_height_in": 96,
  "max_section_width_in": 48,
  "max_section_height_in": 48,
  "joint_blockage_in": 0.5,
  "size_model": "linear"
}
```

| Rule | Meaning |
|---|---|
| `min_width_in`, `min_height_in` | Smallest damper made. Smaller sizes are rejected. |
| `max_width_in`, `max_height_in` | Largest overall assembly made. Larger sizes are rejected. |
| `max_section_width_in`, `max_section_height_in` | Largest single section. A damper bigger than this is split into the fewest **equal** sections that fit, across the width and/or height. |
| `joint_blockage_in` | Extra width blocked along each joint between sections (mullion, connecting angles), beyond the sections' own frames. |
| `size_model` | Shape of the size model: `linear` (default) or `curved`. See [How it works](#how-it-works). Set it in the **Sizes & blades** tab, or blank in Excel for linear. |

Use `null` (or leave the box blank in the program) for no limit. With both section limits blank, the model is single-section only. Edit the rules in the **Sizes & blades** tab, where **Check a size** shows how a size will be split and how many blades each section gets before you save.

Each section has its own frame, blades (from the blade table, by section height) and other blockage. For example, with 48" maximum sections, a 96" x 72" damper becomes 2 x 2 sections of 48" x 36". The Free area tab draws the sections.

For pressure drop, the free area of the whole assembly, including joint blockage, sets the velocity. The loss coefficient comes from the size model at the free-area ratio of the whole assembly, joints included, because the joints block each section like extra frame. If the section size was tested, its own coefficient is used, adjusted by the size model for the joint blockage (with no joint blockage, it is exactly the tested section's coefficient).

## How it works

**1. Fit each tested size.** Face velocity is converted to the velocity through the free area, which is what actually causes the pressure drop:

```
Q      = V_face x A_face
V_free = Q / A_free
dP     = C_free x (rho / 0.075) x (V_free / 4005)^2
```

`C_free` is fitted by least squares through the origin for each size.

**2. Fit a size model.** Small dampers lose a larger share of their opening to the frame and blades, so their coefficient differs from large ones. With 3+ tested sizes, `C_free` is fitted against the free-area ratio `R = A_free / A_face`, in one of two shapes set by the model's `size_model` rule:

```
linear (default):  C_free = a + b x R
curved:            C_free = a + b x (1/R - 1)^2
```

The curved shape is the shape of the classic loss coefficient of a grille or perforated plate: it rises steeply as `R` gets small, so it extrapolates more safely to sizes smaller than anything tested. The straight line can fit well inside the tested range but under-predicts small dampers outside it. Both are fitted every time, and the **Test data & model** tab plots both, so you can see which suits your data before choosing.

**3. Estimate.** A tested size uses its own fitted coefficient. Any other size uses the size model, blended towards the nearest tested size when it is within 15% of it in both width and height:

```
C_free = C_model(R) + w x (C_tested - C_model(R_tested))
w      = 1 at the tested size, falling linearly to 0 at 15% away
```

This keeps the estimate continuous next to a tested size instead of jumping from that size's own coefficient to the size model. The results show the size-model value, the nearest tested size and the weight. The result is corrected for air density at the given temperature and altitude.

A free area typed into the Estimate tab uses the size model at that ratio, with no blending.

The equivalent face-velocity coefficient is `C_face = C_free / (A_free / A_face)^2`, for comparison with catalog values.

## Build a standalone program (Windows)

```bat
build_exe.bat
```

This creates `dist\DamperDP.exe` and copies the `data` folder next to it. To share the program, send the `.exe` together with the `data` folder. The data files stay editable, and new models can be added, without rebuilding.

### Automatic builds on GitHub

The workflow in `.github/workflows/build.yml` builds the program on GitHub's Windows servers, so you don't need to build it yourself:

- **Every push** to `main` runs the tests and builds the `.exe`. Download it from the **Actions** tab: open the latest run and look under **Artifacts**.
- **Pushing a version tag** also creates a **Release** with `DamperDP-windows.zip` attached:

  ```bash
  git tag v0.2.0
  git push origin v0.2.0
  ```

If the tests fail, no `.exe` is built.

## Run the tests

```bash
pip install -r requirements-dev.txt
python -m pytest
```

## Calculation method document

`docs/Damper_Pressure_Drop_Calculation_Method.pdf` explains the method step by step with worked examples. It is generated from the program itself, so its numbers always match the code. Rebuild it after changing the method or the sample data:

```bash
python docs/make_method_pdf.py
```

## Project structure

```
damper-pressure-drop/
├── main.py                 # starts the desktop app
├── damper_dp/
│   ├── physics.py          # free-area geometry, air density, velocity pressure
│   ├── model.py            # data loading, fitting, calibration, estimating, model library
│   ├── gui.py              # Tkinter desktop interface
│   ├── cli.py              # command-line interface
│   ├── workbook.py         # reading and writing model_data.xlsx
│   └── paths.py            # locates the data folder (source or .exe)
├── data/
│   └── models/             # one folder per damper model (sample data - replace)
│       ├── SAMPLE-FSD-100/   # stored as CSV/JSON files
│       └── SAMPLE-FSD-200/   # stored as one Excel workbook
├── docs/
│   ├── make_method_pdf.py  # builds the calculation-method PDF from the program
│   └── Damper_Pressure_Drop_Calculation_Method.pdf
├── tests/
│   └── test_model.py
├── .github/workflows/
│   └── build.yml           # builds the .exe on GitHub
├── build_exe.bat           # builds the Windows .exe locally
├── requirements.txt
└── requirements-dev.txt
```

## Limitations

- Estimates are only as good as the test data. Estimates outside the tested sizes or velocities are flagged as extrapolations and should be treated with caution. Below the lowest tested velocity there is also a warning: loss coefficients tend to rise at low velocity (low Reynolds number), so the real pressure drop may be a little higher than the V² estimate.
- A tested size's own estimate does not depend on its free area (the coefficient is fitted with the same free area, so the two cancel). Free area only matters for untested sizes. If an untested size next to a tested one gets a noticeably different pressure drop, compare their free areas first: calibrate the geometry so the geometry estimate agrees with the measured table.
- The air density correction covers temperature and altitude only, not humidity (usually under 1%).
- Models the damper fully open only (normal operation for fire/smoke dampers). It does not model partially closed blades.
- The geometry estimate assumes a simple rectangular frame and horizontal blades of constant thickness. Calibrating against measured free areas absorbs most of the difference for real dampers.
- Does not account for upstream or downstream fittings beyond the test configuration the data came from.
- When comparing models, make sure their test data comes from the same AMCA test configuration, or the comparison is not like-for-like. Published data for UL 555S fire/smoke dampers is often AMCA 500-D figure 5.3 (ducted both sides); keep each model's data to one configuration.
- Check results against manufacturer data before using them for design or submittals.

## License

MIT. See [LICENSE](LICENSE).
