"""Tkinter desktop interface for the damper pressure-drop estimator."""

from __future__ import annotations

import argparse
import math
import os
import subprocess
import sys
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

import matplotlib

matplotlib.use("TkAgg")
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk  # noqa: E402
from matplotlib.figure import Figure  # noqa: E402
from matplotlib.patches import Rectangle  # noqa: E402
from matplotlib.ticker import PercentFormatter  # noqa: E402

from . import APP_NAME, __version__  # noqa: E402
from . import physics as ph  # noqa: E402
from .model import (  # noqa: E402
    DamperModel, DataError, Estimate, ModelError, ModelLibrary,
    calibrate_geometry, compare_free_areas, comparison_stats, convert_to_workbook,
    create_model_folder, BLEND_RANGE,
)
from .paths import (  # noqa: E402
    DATA_FILE, FREE_AREA_FILE, WORKBOOK_FILE, default_models_dir,
)

# Geometry settings shown in the Free area tab: (setting name, label)
GEOMETRY_FIELDS = [
    ("frame_obstruction_width_in", "Frame, each side left/right (in)"),
    ("frame_obstruction_height_in", "Frame, each side top/bottom (in)"),
    ("blade_thickness_in", "Blade thickness (in)"),
    ("blade_pitch_in", "Blade pitch (in)\n(when not in blade table)"),
    ("other_blockage_in2", "Other blockage per section (in\u00b2)"),
]

# Size rules shown in the Sizes & blades tab: (rule name, label)
RULE_FIELDS = [
    ("min_width_in", "Minimum width (in)"),
    ("min_height_in", "Minimum height (in)"),
    ("max_width_in", "Maximum overall width (in)"),
    ("max_height_in", "Maximum overall height (in)"),
    ("max_section_width_in", "Maximum section width (in)"),
    ("max_section_height_in", "Maximum section height (in)"),
    ("joint_blockage_in", "Extra blockage at each\nsection joint (in)"),
]


class App(tk.Tk):
    def __init__(self, models_dir=None, model_name=None):
        super().__init__()
        self.title(f"{APP_NAME}  v{__version__}")
        self.geometry("1200x760")
        self.minsize(1000, 640)

        self.models_dir = Path(models_dir) if models_dir else default_models_dir()
        self.library: ModelLibrary | None = None
        self.model: DamperModel | None = None
        self._tree_fits = {}

        self._build_menu()
        self._build_ui()
        self.load_library(select=model_name)

    # ------------------------------------------------------------------ UI

    def _build_menu(self):
        menubar = tk.Menu(self)

        file_menu = tk.Menu(menubar, tearoff=False)
        file_menu.add_command(label="New damper model...", command=self.new_model)
        file_menu.add_command(label="Open model data", command=self.open_model_data)
        file_menu.add_command(label="Open model folder", command=self.open_model_folder)
        file_menu.add_command(label="Convert model to Excel workbook...",
                              command=self.convert_model_to_excel)
        file_menu.add_command(label="Choose models folder...", command=self.choose_models_dir)
        file_menu.add_command(label="Reload", command=self.reload, accelerator="F5")
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self.destroy)
        menubar.add_cascade(label="File", menu=file_menu)

        help_menu = tk.Menu(menubar, tearoff=False)
        help_menu.add_command(label="Data file format", command=self.show_data_help)
        help_menu.add_command(label="About", command=self.show_about)
        menubar.add_cascade(label="Help", menu=help_menu)

        self.config(menu=menubar)
        self.bind("<F5>", lambda e: self.reload())

    def _build_ui(self):
        # Status bar is packed first so it always keeps its space at the bottom.
        self.status_var = tk.StringVar()
        ttk.Label(self, textvariable=self.status_var, anchor="w", relief="sunken",
                  padding=(6, 2)).pack(side="bottom", fill="x", padx=8, pady=(0, 6))

        # Damper model selector
        bar = ttk.Frame(self, padding=(8, 8, 8, 0))
        bar.pack(side="top", fill="x")
        ttk.Label(bar, text="Damper model:", font=("Segoe UI", 10, "bold")).pack(side="left")
        self.model_var = tk.StringVar()
        self.model_combo = ttk.Combobox(bar, textvariable=self.model_var, state="readonly",
                                        width=28)
        self.model_combo.pack(side="left", padx=(6, 6))
        self.model_combo.bind("<<ComboboxSelected>>",
                              lambda e: self.select_model(self.model_var.get()))
        ttk.Button(bar, text="New model...", command=self.new_model).pack(side="left")
        ttk.Button(bar, text="Open data", command=self.open_model_data).pack(
            side="left", padx=(4, 0))
        self.model_desc_var = tk.StringVar()
        ttk.Label(bar, textvariable=self.model_desc_var, foreground="gray").pack(
            side="left", padx=(12, 0))

        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill="both", expand=True, padx=8, pady=8)

        est_tab = ttk.Frame(self.notebook)
        free_tab = ttk.Frame(self.notebook)
        rules_tab = ttk.Frame(self.notebook)
        data_tab = ttk.Frame(self.notebook)
        compare_tab = ttk.Frame(self.notebook)
        self.notebook.add(est_tab, text="  Estimate  ")
        self.notebook.add(free_tab, text="  Free area  ")
        self.notebook.add(rules_tab, text="  Sizes & blades  ")
        self.notebook.add(data_tab, text="  Test data & model  ")
        self.notebook.add(compare_tab, text="  Compare models  ")

        self._build_estimate_tab(est_tab)
        self._build_free_area_tab(free_tab)
        self._build_rules_tab(rules_tab)
        self._build_data_tab(data_tab)
        self._build_compare_tab(compare_tab)

    def _build_estimate_tab(self, parent):
        left = ttk.Frame(parent, padding=8)
        left.pack(side="left", fill="y")
        right = ttk.Frame(parent, padding=(0, 8, 8, 8))
        right.pack(side="left", fill="both", expand=True)

        # --- inputs
        box = ttk.LabelFrame(left, text="Damper & conditions", padding=10)
        box.pack(fill="x")
        box.columnconfigure(1, weight=1)

        self.width_var = tk.StringVar(value="24")
        self.height_var = tk.StringVar(value="24")
        self.flow_mode = tk.StringVar(value="cfm")
        self.flow_value_var = tk.StringVar(value="4000")
        self.temp_var = tk.StringVar(value="70")
        self.alt_var = tk.StringVar(value="0")
        self.free_area_var = tk.StringVar(value="")

        row = 0
        row = self._entry_row(box, row, "Width (in)", self.width_var)
        row = self._entry_row(box, row, "Height (in)", self.height_var)

        ttk.Label(box, text="Flow input").grid(row=row, column=0, sticky="nw", pady=3)
        radios = ttk.Frame(box)
        radios.grid(row=row, column=1, sticky="w", pady=3)
        ttk.Radiobutton(radios, text="Airflow (cfm)", variable=self.flow_mode,
                        value="cfm").pack(anchor="w")
        ttk.Radiobutton(radios, text="Face velocity (fpm)", variable=self.flow_mode,
                        value="fpm").pack(anchor="w")
        row += 1

        self.flow_label = ttk.Label(box, text="Airflow (cfm)")
        self.flow_label.grid(row=row, column=0, sticky="w", pady=3)
        ttk.Entry(box, textvariable=self.flow_value_var, width=14).grid(
            row=row, column=1, sticky="ew", pady=3)
        row += 1
        self.flow_mode.trace_add("write", self._on_flow_mode_change)

        row = self._entry_row(box, row, "Air temperature (\u00b0F)", self.temp_var)
        row = self._entry_row(box, row, "Altitude (ft)", self.alt_var)
        row = self._entry_row(box, row, "Free area override\n(ft\u00b2, optional)", self.free_area_var)

        ttk.Button(box, text="Calculate", command=self.calculate).grid(
            row=row, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        self.bind("<Return>", lambda e: self.calculate())

        # --- results
        res = ttk.LabelFrame(left, text="Result", padding=6)
        res.pack(fill="both", expand=True, pady=(8, 0))
        self.result_text = tk.Text(res, width=46, height=20, wrap="word",
                                   font=("Consolas", 10), relief="flat",
                                   background=self.cget("background"))
        self.result_text.pack(fill="both", expand=True)
        self.result_text.tag_configure("big", font=("Consolas", 16, "bold"))
        self.result_text.tag_configure("warnhead", foreground="#b35c00",
                                       font=("Consolas", 10, "bold"))
        self.result_text.tag_configure("warn", foreground="#b35c00")
        self.result_text.config(state="disabled")

        # --- plot
        self.est_fig = Figure(figsize=(7, 5), dpi=100, layout="constrained")
        self.est_canvas = FigureCanvasTkAgg(self.est_fig, master=right)
        toolbar = NavigationToolbar2Tk(self.est_canvas, right, pack_toolbar=False)
        toolbar.update()
        toolbar.pack(side="bottom", fill="x")
        self.est_canvas.get_tk_widget().pack(fill="both", expand=True)

    def _build_data_tab(self, parent):
        top = ttk.Frame(parent, padding=8)
        top.pack(fill="x")
        self.data_info_var = tk.StringVar()
        ttk.Label(top, textvariable=self.data_info_var, justify="left").pack(anchor="w")
        ttk.Label(top, text="Double-click a size to estimate it.",
                  foreground="gray").pack(anchor="w", pady=(4, 0))

        pane = ttk.PanedWindow(parent, orient="horizontal")
        pane.pack(fill="both", expand=True, padx=8, pady=(0, 8))

        table = ttk.Frame(pane)
        plot = ttk.Frame(pane)
        pane.add(table, weight=1)
        pane.add(plot, weight=1)

        columns = [
            ("size", "Size (in)", 80), ("points", "Points", 50),
            ("face", "Face ft\u00b2", 65), ("free", "Free ft\u00b2", 65),
            ("freesrc", "Free area from", 150),
            ("ratio", "Free %", 55), ("cfree", "C free", 65),
            ("cface", "C face", 65), ("r2", "R\u00b2", 55), ("vmax", "Max fpm", 65),
            ("n", "n (V\u207f)", 60), ("resid", "Max resid.", 70),
        ]
        self.tree = ttk.Treeview(table, columns=[c[0] for c in columns],
                                 show="headings", height=14)
        for key, text, width in columns:
            self.tree.heading(key, text=text)
            self.tree.column(key, width=width, anchor="center")
        scroll = ttk.Scrollbar(table, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="left", fill="y")
        self.tree.bind("<Double-1>", self._on_tree_double_click)

        self.data_fig = Figure(figsize=(5, 4), dpi=100, layout="constrained")
        self.data_canvas = FigureCanvasTkAgg(self.data_fig, master=plot)
        self.data_canvas.get_tk_widget().pack(fill="both", expand=True)

    @staticmethod
    def _entry_row(parent, row, label, var):
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=3, padx=(0, 8))
        ttk.Entry(parent, textvariable=var, width=14).grid(row=row, column=1, sticky="ew", pady=3)
        return row + 1

    def _on_flow_mode_change(self, *_):
        self.flow_label.config(
            text="Airflow (cfm)" if self.flow_mode.get() == "cfm" else "Face velocity (fpm)")

    # ----------------------------------------------------------- free area

    def _build_free_area_tab(self, parent):
        left = ttk.Frame(parent, padding=8)
        left.pack(side="left", fill="y")
        right = ttk.PanedWindow(parent, orient="vertical")
        right.pack(side="left", fill="both", expand=True, padx=(0, 8), pady=8)

        # --- size
        size = ttk.LabelFrame(left, text="Damper size", padding=10)
        size.pack(fill="x")
        size.columnconfigure(1, weight=1)
        self.fa_width_var = tk.StringVar(value="24")
        self.fa_height_var = tk.StringVar(value="24")
        row = self._entry_row(size, 0, "Width (in)", self.fa_width_var)
        self._entry_row(size, row, "Height (in)", self.fa_height_var)

        # --- geometry
        geo = ttk.LabelFrame(left, text="Frame & blade geometry", padding=10)
        geo.pack(fill="x", pady=(8, 0))
        geo.columnconfigure(1, weight=1)
        self.geo_vars = {name: tk.StringVar() for name, _ in GEOMETRY_FIELDS}
        row = 0
        for name, label in GEOMETRY_FIELDS:
            row = self._entry_row(geo, row, label, self.geo_vars[name])
        buttons = ttk.Frame(geo)
        buttons.grid(row=row, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        buttons.columnconfigure((0, 1), weight=1)
        ttk.Button(buttons, text="Calculate", command=self.calc_free_area).grid(
            row=0, column=0, sticky="ew", padx=(0, 3))
        ttk.Button(buttons, text="Save geometry", command=self.save_geometry).grid(
            row=0, column=1, sticky="ew", padx=(3, 0))

        # --- calibration
        cal = ttk.LabelFrame(left, text="Calibrate from measured free areas", padding=10)
        cal.pack(fill="x", pady=(8, 0))
        self.equal_frame_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(cal, text="Same frame obstruction on all sides",
                        variable=self.equal_frame_var).pack(anchor="w")
        ttk.Button(cal, text="Calibrate geometry", command=self.calibrate).pack(
            fill="x", pady=(6, 4))
        ttk.Label(cal, wraplength=270, foreground="gray", justify="left", text=(
            "Fits the frame, blade thickness and other blockage to the "
            "free-area table. Blade counts come from the blade table (or the "
            "pitch as entered), and multi-section sizes follow the size rules.")).pack(anchor="w")

        # --- result
        res = ttk.LabelFrame(left, text="Free area", padding=6)
        res.pack(fill="both", expand=True, pady=(8, 0))
        self.fa_text = tk.Text(res, width=46, height=14, wrap="word",
                               font=("Consolas", 10), relief="flat",
                               background=self.cget("background"))
        self.fa_text.pack(fill="both", expand=True)
        self.fa_text.tag_configure("big", font=("Consolas", 16, "bold"))
        self.fa_text.tag_configure("note", foreground="#b35c00")
        self.fa_text.config(state="disabled")

        # --- drawing
        draw = ttk.Frame(right)
        right.add(draw, weight=3)
        self.fa_fig = Figure(figsize=(6, 4), dpi=100, layout="constrained")
        self.fa_canvas = FigureCanvasTkAgg(self.fa_fig, master=draw)
        self.fa_canvas.get_tk_widget().pack(fill="both", expand=True)

        # --- measured vs estimated table
        comp = ttk.Frame(right)
        right.add(comp, weight=2)
        self.fa_comp_var = tk.StringVar()
        ttk.Label(comp, textvariable=self.fa_comp_var).pack(anchor="w", pady=(4, 2))
        tree_frame = ttk.Frame(comp)
        tree_frame.pack(fill="both", expand=True)
        cols = [("size", "Size (in)", 90), ("measured", "Measured ft\u00b2", 110),
                ("estimated", "Geometry estimate ft\u00b2", 160), ("error", "Error %", 80)]
        self.fa_tree = ttk.Treeview(tree_frame, columns=[c[0] for c in cols],
                                    show="headings", height=6)
        for key, text, width in cols:
            self.fa_tree.heading(key, text=text)
            self.fa_tree.column(key, width=width, anchor="center")
        sb = ttk.Scrollbar(tree_frame, orient="vertical", command=self.fa_tree.yview)
        self.fa_tree.configure(yscrollcommand=sb.set)
        self.fa_tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="left", fill="y")
        self.fa_tree.bind("<Double-1>", self._on_fa_tree_double_click)

    def _load_geometry_fields(self, geometry: ph.DamperGeometry):
        for name, _ in GEOMETRY_FIELDS:
            self.geo_vars[name].set(f"{getattr(geometry, name):.6g}")

    @staticmethod
    def _same_geometry(a: ph.DamperGeometry, b: ph.DamperGeometry) -> bool:
        return all(math.isclose(getattr(a, n), getattr(b, n), abs_tol=1e-3)
                   for n, _ in GEOMETRY_FIELDS)

    def _geometry_from_fields(self) -> ph.DamperGeometry:
        values = {}
        for name, label in GEOMETRY_FIELDS:
            values[name] = self._read(self.geo_vars[name], label, positive=False)
        geometry = ph.DamperGeometry(**values)
        geometry.validate()
        return geometry

    def _current_design(self, geometry: ph.DamperGeometry) -> ph.DamperDesign:
        """The selected model's blade table and size rules with the given geometry."""
        if self.model is None:
            return ph.DamperDesign(geometry)
        return self.model.design.with_geometry(geometry)

    def calc_free_area(self):
        try:
            width = self._read(self.fa_width_var, "Width")
            height = self._read(self.fa_height_var, "Height")
            geometry = self._geometry_from_fields()
            design = self._current_design(geometry)
            bd = design.breakdown(width, height)
        except ValueError as e:
            messagebox.showerror("Input error", str(e))
            return
        limit_note = ""
        try:
            design.size_rules.check(width, height)
        except ValueError as e:
            limit_note = str(e)
        self._show_free_area(bd, geometry, limit_note)
        self._draw_face(bd, geometry)
        self._refresh_free_area_table(geometry)

    def _show_free_area(self, bd: ph.FreeAreaBreakdown, geometry: ph.DamperGeometry,
                        limit_note: str = ""):
        face = bd.face_area_in2
        multi = bd.n_sections > 1
        t = self.fa_text
        t.config(state="normal")
        t.delete("1.0", "end")
        t.insert("end", f"{bd.free_area_ft2:.3f} ft\u00b2\n", "big")
        t.insert("end", f"{bd.free_area_in2:,.1f} in\u00b2, {bd.free_ratio:.1%} of face\n\n")
        rows = [("Face area", f"{face:,.1f} in\u00b2  ({bd.face_area_ft2:.3f} ft\u00b2)")]
        if multi:
            rows.append(("Sections", f"{bd.sections_wide} wide x {bd.sections_high} high"))
            rows.append(("Section size", f"{bd.section_width_in:.4g} x {bd.section_height_in:.4g} in"))
        rows += [
            ("Clear opening", f"{bd.open_width_in:.4g} x {bd.open_height_in:.4g} in"
                              + (" each" if multi else "")),
            ("Blades", f"{bd.n_blades}" + (f" per section ({bd.total_blades} total)" if multi else "")),
            ("Blade count from", bd.blade_source),
        ]
        for key, val in rows:
            t.insert("end", f"{key:<17}{val}\n")

        t.insert("end", "\nBlocked by:\n")
        blocked = [("Frame / sleeve", bd.frame_block_in2), ("Blades", bd.blade_block_in2),
                   ("Other", bd.other_block_in2)]
        if multi:
            blocked.append(("Section joints", bd.joint_block_in2))
        for key, area in blocked:
            t.insert("end", f"{key:<17}{area:,.1f} in\u00b2  ({area / face:.1%})\n")

        if limit_note:
            t.insert("end", f"\n{limit_note}\n", "note")
        if self.model is not None:
            entry = self.model.measured_free_area(bd.width_in, bd.height_in)
            if entry is not None:
                err = (bd.free_area_ft2 - entry.free_area_ft2) / entry.free_area_ft2 * 100
                err = 0.0 if abs(err) < 0.05 else err
                t.insert("end", f"\nMeasured (table): {entry.free_area_ft2:.3f} ft\u00b2\n")
                t.insert("end", f"Geometry estimate differs by {err:+.1f}%.\n"
                                f"Pressure-drop estimates use the measured value.\n")
            if not self._same_geometry(geometry, self.model.geometry):
                t.insert("end", "\nGeometry edited but not saved. Click Save geometry "
                                "to use it for pressure-drop estimates.\n", "note")
        t.config(state="disabled")

    def _draw_face(self, bd: ph.FreeAreaBreakdown, geometry: ph.DamperGeometry):
        fig = self.fa_fig
        fig.clear()
        ax = fig.add_subplot(111)
        w, h = bd.width_in, bd.height_in
        sw, sh = bd.section_width_in, bd.section_height_in
        fw, fh = geometry.frame_obstruction_width_in, geometry.frame_obstruction_height_in
        tb = geometry.blade_thickness_in
        spacing = bd.open_height_in / bd.n_blades

        first = True
        for i in range(bd.sections_wide):
            for j in range(bd.sections_high):
                x0, y0 = i * sw, j * sh
                ax.add_patch(Rectangle((x0, y0), sw, sh, fc="#b8b8b8", ec="k", lw=1,
                                       label="Frame / sleeve" if first else None))
                ax.add_patch(Rectangle((x0 + fw, y0 + fh), bd.open_width_in, bd.open_height_in,
                                       fc="#dcefff", ec="k", lw=0.6,
                                       label="Free area" if first else None))
                for k in range(bd.n_blades):
                    y = y0 + fh + (k + 0.5) * spacing - tb / 2
                    ax.add_patch(Rectangle((x0 + fw, y), bd.open_width_in, tb, fc="#1f4e99",
                                           ec="none",
                                           label="Blades (edge-on)" if first and k == 0 else None))
                first = False
        ax.add_patch(Rectangle((0, 0), w, h, fill=False, ec="k", lw=2))

        pad = 0.08 * max(w, h)
        ax.set_xlim(-pad, w + pad)
        ax.set_ylim(-pad, h + pad)
        ax.set_aspect("equal")
        ax.set_xlabel("Width (in)")
        ax.set_ylabel("Height (in)")
        layout = (f",  {bd.sections_wide} x {bd.sections_high} sections"
                  if bd.n_sections > 1 else "")
        fig.suptitle(f'{w:g}" x {h:g}" damper face{layout}: free area '
                     f'{bd.free_area_ft2:.3f} ft\u00b2 ({bd.free_ratio:.0%})')
        ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.12), ncol=3, frameon=False)
        notes = []
        if bd.other_block_in2 > 0:
            notes.append(f"+ {bd.other_block_in2:.1f} in\u00b2 other blockage")
        if bd.joint_block_in2 > 0:
            notes.append(f"+ {bd.joint_block_in2:.1f} in\u00b2 at section joints")
        if notes:
            ax.text(0.99, 0.01, ", ".join(notes) + " (not drawn)", transform=ax.transAxes,
                    ha="right", va="bottom", fontsize=8, color="0.3")
        self.fa_canvas.draw_idle()

    def _refresh_free_area_table(self, geometry: ph.DamperGeometry):
        self.fa_tree.delete(*self.fa_tree.get_children())
        table = self.model.free_area_table if self.model is not None else []
        if not table:
            self.fa_comp_var.set(
                f"No measured free areas loaded. Add them to {FREE_AREA_FILE} "
                f"(width_in, height_in, free_area_ft2) to check and calibrate the geometry.")
            return
        rows = compare_free_areas(table, self._current_design(geometry))
        for i, r in enumerate(rows):
            est = "no opening" if math.isnan(r.estimated_ft2) else f"{r.estimated_ft2:.3f}"
            err = "" if math.isnan(r.estimated_ft2) else f"{r.error_pct + 0.0:+.1f}"
            self.fa_tree.insert("", "end", iid=str(i), values=(
                r.label, f"{r.measured_ft2:.3f}", est, err))
        rms, worst = comparison_stats(rows)
        if math.isnan(rms):
            self.fa_comp_var.set("Measured vs. geometry estimate: the geometry leaves "
                                 "no opening for some sizes.")
        else:
            self.fa_comp_var.set(
                f"Measured vs. geometry estimate:  RMS error {rms:.3f} ft\u00b2, "
                f"largest error {worst:.1f}%   (double-click a size to view it)")

    def _on_fa_tree_double_click(self, _event):
        iid = self.fa_tree.focus()
        if not iid or self.model is None:
            return
        entry = self.model.free_area_table[int(iid)]
        self.fa_width_var.set(f"{entry.width_in:g}")
        self.fa_height_var.set(f"{entry.height_in:g}")
        self.calc_free_area()

    def calibrate(self):
        if self.model is None or not self.model.free_area_table:
            messagebox.showinfo("No measured free areas", (
                f"Add measured free areas to {FREE_AREA_FILE} in the "
                f"{self.model.name if self.model else ''} model folder first.\n\n"
                "Columns: width_in, height_in, free_area_ft2 (one row per size, "
                "at least 3 sizes from small to large)."))
            return
        try:
            pitch = self._read(self.geo_vars["blade_pitch_in"], "Blade pitch")
        except ValueError as e:
            messagebox.showerror("Input error", str(e))
            return
        self.config(cursor="watch")
        self.update_idletasks()
        try:
            result = calibrate_geometry(self.model.free_area_table, pitch,
                                        equal_frame=self.equal_frame_var.get(),
                                        blade_table=self.model.blade_table,
                                        size_rules=self.model.size_rules)
        except ModelError as e:
            messagebox.showerror("Calibration failed", str(e))
            return
        finally:
            self.config(cursor="")
        self._load_geometry_fields(result.geometry)
        self.calc_free_area()
        g = result.geometry
        messagebox.showinfo("Calibration complete", (
            f"Fitted geometry:\n"
            f"  Frame left/right   {g.frame_obstruction_width_in:.3f} in\n"
            f"  Frame top/bottom   {g.frame_obstruction_height_in:.3f} in\n"
            f"  Blade thickness    {g.blade_thickness_in:.3f} in\n"
            f"  Other blockage     {g.other_blockage_in2:.1f} in\u00b2\n\n"
            f"RMS error {result.rms_error_ft2:.3f} ft\u00b2, largest error "
            f"{result.max_error_pct:.1f}%.\n\n"
            "Check the values look physically sensible, then click Save geometry "
            "to use them for pressure-drop estimates."))

    def save_geometry(self):
        if self.model is None or self.model.folder is None:
            messagebox.showwarning("No model", "Select a damper model first.")
            return
        try:
            geometry = self._geometry_from_fields()
        except ValueError as e:
            messagebox.showerror("Input error", str(e))
            return
        if not messagebox.askyesno("Save geometry", (
                f"Save these settings for {self.model.name} to its "
                f"{self.model.storage}\nin {self.model.folder}\n\n"
                "and refit the pressure-drop model with them?")):
            return
        name = self.model.name
        try:
            self.model.save_geometry(geometry)
        except (OSError, DataError, ModelError, ValueError) as e:
            messagebox.showerror("Could not save", str(e))
            return
        self.load_library(select=name)
        self.status_var.set(f"Saved {name} geometry and refitted the model.")

    # ---------------------------------------------------------------- data

    def load_library(self, select=None):
        """Load every model folder, then select one (keeping the current one if possible)."""
        try:
            self.library = ModelLibrary.load(self.models_dir)
        except DataError as e:
            self.library = None
            self.model_combo["values"] = []
            self._set_no_model("No models folder.")
            messagebox.showerror("Could not load damper models", str(e))
            return

        names = self.library.names
        self.model_combo["values"] = names
        self._refresh_compare_models()

        if self.library.errors:
            details = "\n\n".join(f"{n}:\n{e}" for n, e in self.library.errors.items())
            messagebox.showwarning(
                "Some damper models could not be loaded",
                f"These model folders were skipped:\n\n{details}\n\n"
                f"Fix the files and press F5 to reload.")

        if not names:
            self._set_no_model(f"No damper models in {self.models_dir}. "
                               f"Use New model... to create one.")
            return
        if select not in names:
            select = self.model.name if self.model is not None and self.model.name in names \
                else names[0]
        self.select_model(select)

    def select_model(self, name):
        if self.library is None or name not in self.library.models:
            return
        self.model = self.library.models[name]
        self.model_var.set(name)
        self.model_desc_var.set(self.model.description)
        self._refresh_data_tab()
        self._load_geometry_fields(self.model.geometry)
        self._refresh_free_area_table(self.model.geometry)
        self._load_rules_tab(self.model)
        self.status_var.set(
            f"{name}: {len(self.model.fits)} tested sizes, {self.model.n_points} test points, "
            f"{len(self.model.free_area_table)} measured free areas, data in "
            f"{self.model.storage}.   "
            f"{len(self.library.models)} model(s) loaded from {self.models_dir}")
        self.after(50, self.calculate)
        self.after(80, self.calc_free_area)

    def _set_no_model(self, message):
        self.model = None
        self.model_var.set("")
        self.model_desc_var.set("")
        self.status_var.set(message)
        self._refresh_data_tab()
        self._refresh_free_area_table(ph.DamperGeometry())
        self._load_rules_tab(None)

    def reload(self):
        self.load_library(select=self.model_var.get() or None)

    def choose_models_dir(self):
        path = filedialog.askdirectory(title="Choose the folder holding the damper models",
                                       initialdir=str(self.models_dir))
        if path:
            self.models_dir = Path(path)
            self.load_library()

    def new_model(self):
        name = simpledialog.askstring(
            "New damper model", "Name for the new damper model\n(also used as its folder name):",
            parent=self)
        if not name:
            return
        description = simpledialog.askstring(
            "New damper model", "Short description (optional):", parent=self) or ""
        use_excel = messagebox.askyesno("New damper model", (
            "Keep this model's data in one Excel workbook?\n\n"
            "Yes: one model_data.xlsx with sheets for test data, free areas, blade "
            "table, size rules and geometry.\n"
            "No: separate CSV and JSON files."))
        copy_from = None
        if self.model is not None and self.model.folder is not None:
            if messagebox.askyesno("New damper model", (
                    f"Start from the geometry, blade table and size rules of "
                    f"{self.model.name}?")):
                copy_from = self.model.folder
        try:
            folder = create_model_folder(self.models_dir, name, copy_from, description,
                                         use_excel=use_excel)
        except (ModelError, DataError, OSError) as e:
            messagebox.showerror("Could not create model", str(e))
            return
        if use_excel:
            where = (f"Add pressure-drop test data to the 'Test data' sheet of {WORKBOOK_FILE} "
                     f"and measured free areas to its 'Free area' sheet")
        else:
            where = (f"Add pressure-drop test data to {DATA_FILE} and measured free areas to "
                     f"{FREE_AREA_FILE}")
        messagebox.showinfo("Model created", (
            f"Created {folder}\n\n{where}, then save and press F5 to reload.\n\n"
            f"The model appears in the list once it has test data."))
        self._open_path(folder / WORKBOOK_FILE if use_excel else folder)
        self.load_library(select=folder.name)

    def open_model_data(self):
        """Open the model's workbook in Excel, or its folder for CSV/JSON models."""
        if self.model is not None and self.model.workbook_path is not None:
            self._open_path(self.model.workbook_path)
        else:
            self.open_model_folder()

    def convert_model_to_excel(self):
        if self.model is None:
            messagebox.showwarning("No model", "Select a damper model first.")
            return
        if self.model.workbook_path is not None:
            messagebox.showinfo("Convert to Excel",
                                f"{self.model.name} already keeps its data in {WORKBOOK_FILE}.")
            return
        if not messagebox.askyesno("Convert to Excel workbook", (
                f"Put all of {self.model.name}'s data into one Excel workbook, "
                f"{WORKBOOK_FILE}?\n\nThe current CSV and JSON files are moved into an "
                f"_old_files sub-folder as a backup.")):
            return
        name = self.model.name
        try:
            path = convert_to_workbook(self.model)
        except (ModelError, DataError, OSError) as e:
            messagebox.showerror("Could not convert", str(e))
            return
        self.load_library(select=name)
        self.status_var.set(f"{name} now keeps its data in {path}.")
        self._open_path(path)

    def open_model_folder(self):
        if self.model is not None and self.model.folder is not None:
            self._open_path(self.model.folder)
        else:
            self._open_path(self.models_dir)

    @staticmethod
    def _open_path(path: Path):
        """Open a folder in the system file browser."""
        try:
            if sys.platform.startswith("win"):
                os.startfile(path)  # noqa: S606
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(path)])
            else:
                subprocess.Popen(["xdg-open", str(path)])
        except OSError:
            pass

    def _refresh_data_tab(self):
        self.tree.delete(*self.tree.get_children())
        self._tree_fits.clear()
        self.data_fig.clear()

        if self.model is None:
            self.data_info_var.set("No data loaded.")
            self.data_canvas.draw_idle()
            return

        m = self.model
        self.data_info_var.set(
            f"Model:       {m.name}   (data in {m.storage})\n"
            f"Data file:   {m.data_source}\n"
            f"Geometry:    {m.geometry_source}\n"
            f"Free areas:  {m.free_area_table_source} ({len(m.free_area_table)} sizes)\n"
            f"Size model:  {m.size_model.describe()}  (in use)"
            + (f"\nAlternative: {m.alternate_size_model.describe()}"
               if m.alternate_size_model is not None else "")
            + f"\nUntested sizes within {BLEND_RANGE:.0%} of a tested size blend towards its "
              f"coefficient. n: exponent of dP = k V\u207f (should be near 2). "
              f"Max resid.: largest test point vs. fit difference."
        )

        for i, f in enumerate(m.fits):
            iid = str(i)
            self._tree_fits[iid] = f
            r2 = "n/a" if math.isnan(f.r2) else f"{f.r2:.3f}"
            self.tree.insert("", "end", iid=iid, values=(
                f.label, f.n_points, f"{f.face_area_ft2:.2f}", f"{f.free_area_ft2:.2f}",
                f.free_area_source,
                f"{f.free_ratio:.0%}", f"{f.c_free:.3f}", f"{f.c_face:.3f}", r2,
                f"{f.v_face_max:,.0f}",
                "n/a" if math.isnan(f.exponent) else
                f"{f.exponent:.2f}" + ("" if f.exponent_ok else " !"),
                "n/a" if math.isnan(f.max_residual_pct) else f"{f.max_residual_pct:+.1f}%",
            ))

        # Plot C_free against free-area ratio with the fitted size model.
        ax = self.data_fig.add_subplot(111)
        ratios = [f.free_ratio for f in m.fits]
        cs = [f.c_free for f in m.fits]
        ax.plot(ratios, cs, "o", mfc="tab:red", mec="k", ms=7, ls="none", label="Tested sizes")
        for f in m.fits:
            ax.annotate(f.label, (f.free_ratio, f.c_free), textcoords="offset points",
                        xytext=(6, 4), fontsize=8)
        sm = m.size_model
        span = max(sm.ratio_max - sm.ratio_min, 0.1)
        x0, x1 = max(sm.ratio_min - 0.1 * span, 0.05), min(sm.ratio_max + 0.1 * span, 1.0)
        xs = [x0 + (x1 - x0) * i / 100 for i in range(101)]
        ax.plot(xs, [sm.c_free(x) for x in xs], "b-", lw=2, label=f"{sm.label} (in use)")
        alt = m.alternate_size_model
        if alt is not None:
            ax.plot(xs, [alt.c_free(x) for x in xs], color="gray", ls="--", lw=1.5,
                    label=alt.label)
        ax.set_xlabel("Free-area ratio (free / face)")
        ax.set_ylabel("Loss coefficient C (free area)")
        ax.set_title("Loss coefficient vs. free-area ratio")
        ax.xaxis.set_major_formatter(PercentFormatter(1.0, decimals=0))
        ax.grid(True, alpha=0.4)
        ax.legend()
        self.data_canvas.draw_idle()

    def _on_tree_double_click(self, _event):
        fit = self._tree_fits.get(self.tree.focus())
        if fit is None:
            return
        self.width_var.set(f"{fit.width_in:g}")
        self.height_var.set(f"{fit.height_in:g}")
        self.free_area_var.set("")
        self.notebook.select(0)
        self.calculate()

    # ------------------------------------------------------------ estimate

    @staticmethod
    def _read(var, name, positive=True, optional=False):
        text = var.get().strip().replace(",", "")
        if not text:
            if optional:
                return None
            raise ValueError(f"{name} is required.")
        try:
            value = float(text)
        except ValueError:
            raise ValueError(f"{name} must be a number (got '{text}').") from None
        if positive and value <= 0:
            raise ValueError(f"{name} must be greater than zero.")
        return value

    def calculate(self):
        if self.model is None:
            messagebox.showwarning("No data", "Load a test-data CSV first (File > Open test data).")
            return
        cfm_mode = self.flow_mode.get() == "cfm"
        try:
            width = self._read(self.width_var, "Width")
            height = self._read(self.height_var, "Height")
            value = self._read(self.flow_value_var, "Airflow" if cfm_mode else "Face velocity")
            temp = self._read(self.temp_var, "Temperature", positive=False)
            alt = self._read(self.alt_var, "Altitude", positive=False)
            free_area = self._read(self.free_area_var, "Free area override", optional=True)
            flow = {"airflow_cfm": value} if cfm_mode else {"face_velocity_fpm": value}
            est = self.model.estimate(width, height, temperature_f=temp, altitude_ft=alt,
                                      free_area_override_ft2=free_area, **flow)
        except (ValueError, ModelError) as e:
            messagebox.showerror("Input error", str(e))
            return
        self._show_result(est)
        self._plot_estimate(est)

    def _show_result(self, est: Estimate):
        t = self.result_text
        t.config(state="normal")
        t.delete("1.0", "end")
        t.insert("end", f"\u0394P = {est.pressure_drop_in_wg:.3f} in. wg\n", "big")
        t.insert("end", f"     ({est.pressure_drop_pa:,.0f} Pa)\n\n")
        rows = [
            ("Damper model", self.model.name if self.model else ""),
            ("Damper size", f'{est.width_in:g}" x {est.height_in:g}"'),
            ("Airflow", f"{est.airflow_cfm:,.0f} cfm"),
            ("Face velocity", f"{est.face_velocity_fpm:,.0f} fpm"),
            ("Free-area velocity", f"{est.free_velocity_fpm:,.0f} fpm"),
            ("Face area", f"{est.face_area_ft2:.3f} ft\u00b2"),
            ("Free area", f"{est.free_area_ft2:.3f} ft\u00b2 ({est.free_ratio:.0%})"),
            ("Free area from", est.free_area_source),
        ]
        if est.n_sections > 1:
            rows += [
                ("Sections", f"{est.sections_wide} wide x {est.sections_high} high"),
                ("Section size", f"{est.section_width_in:.4g} x {est.section_height_in:.4g} in"),
            ]
        rows += [
            ("Blades", f"{est.blades_per_section}"
                       + (" per section" if est.n_sections > 1 else "")
                       + f" ({est.blade_source})"),
            ("Air density", f"{est.air_density:.4f} lb/ft\u00b3"),
            ("C (free area)", f"{est.c_free:.4f}"),
            ("C (face)", f"{est.c_face:.4f}"),
        ]
        if not math.isnan(est.c_free_size_model):
            rows.append(("C size model", f"{est.c_free_size_model:.4f} at R = {est.coeff_ratio:.1%}"))
        if est.nearest_tested is not None:
            n = est.nearest_tested
            rows.append(("Nearest tested", f"{n.label}: C {n.c_free:.4f}, "
                                           f"{est.blend_offset:+.4f} vs. size model, "
                                           f"applied at {est.blend_weight:.0%}"))
        for key, val in rows:
            t.insert("end", f"{key:<20}{val}\n")
        t.insert("end", f"\nBasis: {est.source}\n")
        if est.warnings:
            t.insert("end", "\nWarnings\n", "warnhead")
            for w in est.warnings:
                t.insert("end", f"\u2022 {w}\n", "warn")
        t.config(state="disabled")

    def _plot_estimate(self, est: Estimate):
        fig = self.est_fig
        fig.clear()
        ax = fig.add_subplot(111)

        q_max = est.airflow_cfm * 1.5
        if est.tested is not None:
            q_max = max(q_max, est.tested.v_face_max * est.face_area_ft2 * 1.1)
        q = [q_max * i / 300 for i in range(301)]
        ax.plot(q, est.curve(q), "b-", lw=2, label="Estimated curve")

        if est.tested is not None:
            q_test = est.tested.face_velocities_fpm * est.face_area_ft2
            ax.plot(q_test, est.tested.pressure_drops_in_wg, "o", mfc="tab:red", mec="k",
                    ms=7, ls="none", label="Test data (standard air)")

        ax.plot([est.airflow_cfm], [est.pressure_drop_in_wg], "D", mfc="orange", mec="k",
                ms=9, ls="none", zorder=5, label="Operating point")
        ax.annotate(f"{est.pressure_drop_in_wg:.3f} in. wg\n@ {est.airflow_cfm:,.0f} cfm",
                    xy=(est.airflow_cfm, est.pressure_drop_in_wg),
                    xytext=(12, -34), textcoords="offset points", fontsize=9,
                    bbox=dict(boxstyle="round", fc="white", ec="0.7"))

        ax.set_xlim(0, q_max)
        ax.set_ylim(bottom=0)
        ax.set_xlabel("Airflow (cfm)")
        ax.set_ylabel("Pressure drop (in. wg)")
        ax.grid(True, alpha=0.4)
        ax.legend(loc="upper left")

        fa = est.face_area_ft2
        top = ax.secondary_xaxis("top", functions=(lambda x: x / fa, lambda v: v * fa))
        top.set_xlabel("Face velocity (fpm)")

        air = ""
        if abs(est.air_density - 0.075) > 0.0005:
            air = f"   (air {est.temperature_f:g}\u00b0F, {est.altitude_ft:,.0f} ft)"
        name = self.model.name if self.model else "Damper"
        layout = (f"  ({est.sections_wide} x {est.sections_high} sections)"
                  if est.n_sections > 1 else "")
        fig.suptitle(f'{name}  {est.width_in:g}" x {est.height_in:g}"{layout}{air}')
        self.est_canvas.draw_idle()

    # ------------------------------------------------------- sizes & blades

    def _build_rules_tab(self, parent):
        left = ttk.Frame(parent, padding=8)
        left.pack(side="left", fill="y")
        right = ttk.Frame(parent, padding=(0, 8, 8, 8))
        right.pack(side="left", fill="both", expand=True)

        # --- size rules
        box = ttk.LabelFrame(left, text="Size rules", padding=10)
        box.pack(fill="x")
        box.columnconfigure(1, weight=1)
        self.rule_vars = {name: tk.StringVar() for name, _ in RULE_FIELDS}
        row = 0
        for name, label in RULE_FIELDS:
            row = self._entry_row(box, row, label, self.rule_vars[name])
        self.size_model_var = tk.StringVar(value="linear")
        ttk.Label(box, text="Size model shape").grid(row=row, column=0, sticky="w", pady=2)
        ttk.Combobox(box, textvariable=self.size_model_var, values=list(ph.SIZE_MODEL_KINDS),
                     state="readonly", width=10).grid(row=row, column=1, sticky="ew", pady=2)
        row += 1
        ttk.Label(box, wraplength=300, foreground="gray", justify="left", text=(
            "linear: C = a + b\u00b7R. curved: C = a + b\u00b7(1/R \u2212 1)\u00b2, which "
            "rises steeply for small free-area ratios and extrapolates more safely. "
            "Compare both on the Test data & model tab.")).grid(
            row=row, column=0, columnspan=2, sticky="w", pady=(0, 4))
        row += 1
        ttk.Label(box, wraplength=300, foreground="gray", justify="left", text=(
            "Leave a limit blank for no limit. Leave both section limits blank "
            "for single-section dampers only. A damper larger than the maximum "
            "section size is split into equal sections, each with its own frame "
            "and blades.")).grid(row=row, column=0, columnspan=2, sticky="w", pady=(6, 0))

        # --- check a size
        chk = ttk.LabelFrame(left, text="Check a size", padding=10)
        chk.pack(fill="x", pady=(8, 0))
        chk.columnconfigure(1, weight=1)
        self.chk_width_var = tk.StringVar(value="96")
        self.chk_height_var = tk.StringVar(value="72")
        row = self._entry_row(chk, 0, "Width (in)", self.chk_width_var)
        row = self._entry_row(chk, row, "Height (in)", self.chk_height_var)
        ttk.Button(chk, text="Check", command=self.check_size).grid(
            row=row, column=0, columnspan=2, sticky="ew", pady=(6, 4))
        self.chk_result_var = tk.StringVar()
        ttk.Label(chk, textvariable=self.chk_result_var, wraplength=300,
                  justify="left").grid(row=row + 1, column=0, columnspan=2, sticky="w")

        # --- save / revert
        btns = ttk.Frame(left)
        btns.pack(fill="x", pady=(8, 0))
        btns.columnconfigure((0, 1), weight=1)
        ttk.Button(btns, text="Save rules & blade table", command=self.save_rules).grid(
            row=0, column=0, sticky="ew", padx=(0, 3))
        ttk.Button(btns, text="Revert", command=lambda: self._load_rules_tab(self.model)).grid(
            row=0, column=1, sticky="ew", padx=(3, 0))

        # --- blade table
        bt = ttk.LabelFrame(right, text="Blade table: blades per section by section height",
                            padding=10)
        bt.pack(fill="both", expand=True)
        tree_frame = ttk.Frame(bt)
        tree_frame.pack(fill="both", expand=True)
        cols = [("min", "From height (in)", 130), ("max", "To height (in)", 130),
                ("blades", "Blades", 90)]
        self.blade_tree = ttk.Treeview(tree_frame, columns=[c[0] for c in cols],
                                       show="headings", height=12, selectmode="browse")
        for key, text, width in cols:
            self.blade_tree.heading(key, text=text)
            self.blade_tree.column(key, width=width, anchor="center")
        sb = ttk.Scrollbar(tree_frame, orient="vertical", command=self.blade_tree.yview)
        self.blade_tree.configure(yscrollcommand=sb.set)
        self.blade_tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="left", fill="y")
        self.blade_tree.bind("<<TreeviewSelect>>", self._on_blade_row_select)

        edit = ttk.Frame(bt)
        edit.pack(fill="x", pady=(8, 0))
        self.br_min_var = tk.StringVar()
        self.br_max_var = tk.StringVar()
        self.br_blades_var = tk.StringVar()
        for label, var in (("From", self.br_min_var), ("To", self.br_max_var),
                           ("Blades", self.br_blades_var)):
            ttk.Label(edit, text=label).pack(side="left", padx=(0, 4))
            ttk.Entry(edit, textvariable=var, width=9).pack(side="left", padx=(0, 10))
        ttk.Button(edit, text="Add", command=self.add_blade_row).pack(side="left")
        ttk.Button(edit, text="Update", command=self.update_blade_row).pack(side="left", padx=4)
        ttk.Button(edit, text="Delete", command=self.delete_blade_row).pack(side="left")

        ttk.Label(bt, wraplength=600, foreground="gray", justify="left", text=(
            "Heights are the height of one section, and each range includes both ends. "
            "A height on a boundary shared by two rows uses the lower row. Leave no gaps "
            "between rows (for example 10-14.99 and 15-20), because a height in a gap is "
            "reported as an error. Sections above or below the table use the blade pitch "
            "from the Free area tab. An empty table means the blade pitch is always used."
        )).pack(anchor="w", pady=(8, 0))

        self._blade_rows: list[ph.BladeRule] = []

    def _load_rules_tab(self, model):
        rules = model.size_rules if model is not None else ph.SizeRules()
        for name, _ in RULE_FIELDS:
            value = getattr(rules, name)
            if name == "joint_blockage_in":
                self.rule_vars[name].set(f"{value:g}")
            else:
                self.rule_vars[name].set("" if value is None else f"{value:g}")
        self.size_model_var.set(rules.size_model)
        self._blade_rows = list(model.blade_table.rules) if model is not None else []
        self._refresh_blade_tree()
        self.chk_result_var.set("")

    def _refresh_blade_tree(self):
        self.blade_tree.delete(*self.blade_tree.get_children())
        self._blade_rows.sort(key=lambda r: (r.min_height_in, r.max_height_in))
        for i, r in enumerate(self._blade_rows):
            self.blade_tree.insert("", "end", iid=str(i), values=(
                f"{r.min_height_in:g}", f"{r.max_height_in:g}", r.blades))

    def _on_blade_row_select(self, _event=None):
        sel = self.blade_tree.selection()
        if not sel:
            return
        r = self._blade_rows[int(sel[0])]
        self.br_min_var.set(f"{r.min_height_in:g}")
        self.br_max_var.set(f"{r.max_height_in:g}")
        self.br_blades_var.set(str(r.blades))

    def _blade_row_from_fields(self) -> ph.BladeRule:
        lo = self._read(self.br_min_var, "From height")
        hi = self._read(self.br_max_var, "To height")
        n = self._read(self.br_blades_var, "Blades")
        if n != int(n):
            raise ValueError("Blades must be a whole number.")
        if hi < lo:
            raise ValueError("'To height' must be at least 'From height'.")
        return ph.BladeRule(lo, hi, int(n))

    def _set_blade_rows(self, rows):
        """Replace the rows if they are valid (no overlaps), else show why not."""
        table = ph.BladeTable(rows)
        try:
            table.validate()
        except ValueError as e:
            messagebox.showerror("Blade table", str(e))
            return False
        self._blade_rows = table.rules
        self._refresh_blade_tree()
        return True

    def add_blade_row(self):
        try:
            row = self._blade_row_from_fields()
        except ValueError as e:
            messagebox.showerror("Input error", str(e))
            return
        if self._set_blade_rows(self._blade_rows + [row]):
            for v in (self.br_min_var, self.br_max_var, self.br_blades_var):
                v.set("")

    def update_blade_row(self):
        sel = self.blade_tree.selection()
        if not sel:
            messagebox.showinfo("Blade table", "Select a row to update.")
            return
        try:
            row = self._blade_row_from_fields()
        except ValueError as e:
            messagebox.showerror("Input error", str(e))
            return
        rows = list(self._blade_rows)
        rows[int(sel[0])] = row
        self._set_blade_rows(rows)

    def delete_blade_row(self):
        sel = self.blade_tree.selection()
        if not sel:
            messagebox.showinfo("Blade table", "Select a row to delete.")
            return
        rows = list(self._blade_rows)
        del rows[int(sel[0])]
        self._set_blade_rows(rows)

    def _rules_from_fields(self) -> ph.SizeRules:
        values = {}
        for name, label in RULE_FIELDS:
            label = label.replace("\n", " ")
            if name == "joint_blockage_in":
                v = self._read(self.rule_vars[name], label, positive=False, optional=True)
                values[name] = 0.0 if v is None else v
            else:
                values[name] = self._read(self.rule_vars[name], label, optional=True)
        rules = ph.SizeRules(**values, size_model=self.size_model_var.get() or "linear")
        rules.validate()
        return rules

    def _design_from_rules_tab(self) -> ph.DamperDesign:
        geometry = self.model.geometry if self.model is not None else ph.DamperGeometry()
        table = ph.BladeTable(list(self._blade_rows))
        design = ph.DamperDesign(geometry, table, self._rules_from_fields())
        design.validate()
        return design

    def check_size(self):
        try:
            w = self._read(self.chk_width_var, "Width")
            h = self._read(self.chk_height_var, "Height")
            design = self._design_from_rules_tab()
        except ValueError as e:
            messagebox.showerror("Input error", str(e))
            return
        lines = []
        try:
            design.size_rules.check(w, h)
            lines.append("Within this model's size limits.")
        except ValueError as e:
            lines.append(f"\u26a0 {e}")
        try:
            bd = design.breakdown(w, h)
        except ValueError as e:
            self.chk_result_var.set("\n".join(lines + [f"\u26a0 {e}"]))
            return
        if bd.n_sections > 1:
            lines.append(f"{bd.sections_wide} wide x {bd.sections_high} high = "
                         f"{bd.n_sections} sections of {bd.section_width_in:.4g} x "
                         f"{bd.section_height_in:.4g} in")
        else:
            lines.append("Single section")
        lines.append(f"{bd.n_blades} blades per section ({bd.blade_source})")
        lines.append(f"Free area {bd.free_area_ft2:.3f} ft\u00b2 ({bd.free_ratio:.0%}) with the "
                     f"saved geometry")
        self.chk_result_var.set("\n".join(lines))

    def save_rules(self):
        if self.model is None or self.model.folder is None:
            messagebox.showwarning("No model", "Select a damper model first.")
            return
        try:
            design = self._design_from_rules_tab()
        except ValueError as e:
            messagebox.showerror("Input error", str(e))
            return
        if not messagebox.askyesno("Save rules & blade table", (
                f"Save the size rules and blade table for {self.model.name} to its "
                f"{self.model.storage}\nin {self.model.folder}\n\n"
                "and refit the pressure-drop model?")):
            return
        try:
            self.model.save_rules(design.size_rules, design.blade_table)
        except (OSError, DataError, ModelError, ValueError) as e:
            messagebox.showerror("Could not save", str(e))
            return
        name = self.model.name
        self.load_library(select=name)
        self.status_var.set(f"Saved {name} size rules and blade table and refitted the model.")

    # ------------------------------------------------------------- compare

    def _build_compare_tab(self, parent):
        left = ttk.Frame(parent, padding=8)
        left.pack(side="left", fill="y")
        right = ttk.PanedWindow(parent, orient="vertical")
        right.pack(side="left", fill="both", expand=True, padx=(0, 8), pady=8)

        box = ttk.LabelFrame(left, text="Size & conditions", padding=10)
        box.pack(fill="x")
        box.columnconfigure(1, weight=1)
        self.cmp_width_var = tk.StringVar(value="24")
        self.cmp_height_var = tk.StringVar(value="24")
        self.cmp_flow_mode = tk.StringVar(value="cfm")
        self.cmp_value_var = tk.StringVar(value="4000")
        self.cmp_temp_var = tk.StringVar(value="70")
        self.cmp_alt_var = tk.StringVar(value="0")

        row = self._entry_row(box, 0, "Width (in)", self.cmp_width_var)
        row = self._entry_row(box, row, "Height (in)", self.cmp_height_var)
        ttk.Label(box, text="Flow input").grid(row=row, column=0, sticky="nw", pady=3)
        radios = ttk.Frame(box)
        radios.grid(row=row, column=1, sticky="w", pady=3)
        ttk.Radiobutton(radios, text="Airflow (cfm)", variable=self.cmp_flow_mode,
                        value="cfm").pack(anchor="w")
        ttk.Radiobutton(radios, text="Face velocity (fpm)", variable=self.cmp_flow_mode,
                        value="fpm").pack(anchor="w")
        row = self._entry_row(box, row + 1, "Flow value", self.cmp_value_var)
        row = self._entry_row(box, row, "Air temperature (\u00b0F)", self.cmp_temp_var)
        self._entry_row(box, row, "Altitude (ft)", self.cmp_alt_var)

        models = ttk.LabelFrame(left, text="Models to compare", padding=10)
        models.pack(fill="both", expand=True, pady=(8, 0))
        self.cmp_list = tk.Listbox(models, selectmode="multiple", exportselection=False,
                                   height=8, activestyle="none")
        self.cmp_list.pack(fill="both", expand=True)
        sel = ttk.Frame(models)
        sel.pack(fill="x", pady=(6, 0))
        ttk.Button(sel, text="All", width=8,
                   command=lambda: self.cmp_list.select_set(0, "end")).pack(side="left")
        ttk.Button(sel, text="None", width=8,
                   command=lambda: self.cmp_list.select_clear(0, "end")).pack(side="left", padx=4)

        ttk.Button(left, text="Compare", command=self.compare).pack(fill="x", pady=(8, 0))
        ttk.Label(left, wraplength=260, foreground="gray", justify="left", text=(
            "Double-click a row to open that model in the Estimate tab.")).pack(
            anchor="w", pady=(6, 0))

        table = ttk.Frame(right)
        right.add(table, weight=2)
        cols = [("model", "Damper model", 150), ("dp", "\u0394P in. wg", 80),
                ("pa", "\u0394P Pa", 65), ("free", "Free ft\u00b2", 70),
                ("ratio", "Free %", 55), ("vfree", "Free-area fpm", 95),
                ("cface", "C face", 65), ("basis", "Basis", 220), ("notes", "Notes", 300)]
        self.cmp_tree = ttk.Treeview(table, columns=[c[0] for c in cols], show="headings",
                                     height=6)
        for key, text, width in cols:
            self.cmp_tree.heading(key, text=text)
            self.cmp_tree.column(key, width=width, anchor="w" if key in ("model", "basis",
                                                                           "notes") else "center")
        xs = ttk.Scrollbar(table, orient="horizontal", command=self.cmp_tree.xview)
        self.cmp_tree.configure(xscrollcommand=xs.set)
        self.cmp_tree.pack(fill="both", expand=True)
        xs.pack(fill="x")
        self.cmp_tree.bind("<Double-1>", self._on_cmp_double_click)

        plot = ttk.Frame(right)
        right.add(plot, weight=3)
        self.cmp_fig = Figure(figsize=(7, 4), dpi=100, layout="constrained")
        self.cmp_canvas = FigureCanvasTkAgg(self.cmp_fig, master=plot)
        self.cmp_canvas.get_tk_widget().pack(fill="both", expand=True)

    def _refresh_compare_models(self):
        self.cmp_list.delete(0, "end")
        if self.library is None:
            return
        for name in self.library.names:
            self.cmp_list.insert("end", name)
        self.cmp_list.select_set(0, "end")

    def compare(self):
        if self.library is None or not self.library.models:
            messagebox.showwarning("No models", "No damper models are loaded.")
            return
        names = [self.cmp_list.get(i) for i in self.cmp_list.curselection()]
        if not names:
            messagebox.showwarning("No models selected", "Select at least one model to compare.")
            return
        cfm_mode = self.cmp_flow_mode.get() == "cfm"
        try:
            width = self._read(self.cmp_width_var, "Width")
            height = self._read(self.cmp_height_var, "Height")
            value = self._read(self.cmp_value_var, "Airflow" if cfm_mode else "Face velocity")
            temp = self._read(self.cmp_temp_var, "Temperature", positive=False)
            alt = self._read(self.cmp_alt_var, "Altitude", positive=False)
        except ValueError as e:
            messagebox.showerror("Input error", str(e))
            return
        flow = {"airflow_cfm": value} if cfm_mode else {"face_velocity_fpm": value}
        results = self.library.compare(width, height, names=names, temperature_f=temp,
                                       altitude_ft=alt, **flow)

        # Lowest pressure drop first; models that failed go last.
        results.sort(key=lambda r: (r[1] is None,
                                    r[1].pressure_drop_in_wg if r[1] is not None else 0))

        self.cmp_tree.delete(*self.cmp_tree.get_children())
        for name, est, err in results:
            if est is None:
                self.cmp_tree.insert("", "end", iid=name, values=(
                    name, "\u2014", "", "", "", "", "", "Could not estimate", err))
                continue
            self.cmp_tree.insert("", "end", iid=name, values=(
                name, f"{est.pressure_drop_in_wg:.3f}", f"{est.pressure_drop_pa:,.0f}",
                f"{est.free_area_ft2:.3f}", f"{est.free_ratio:.0%}",
                f"{est.free_velocity_fpm:,.0f}", f"{est.c_face:.3f}",
                est.source, "; ".join(est.warnings)))
        self._plot_compare([(n, e) for n, e, _ in results if e is not None])

    def _plot_compare(self, results):
        fig = self.cmp_fig
        fig.clear()
        if not results:
            self.cmp_canvas.draw_idle()
            return
        ax = fig.add_subplot(111)
        first = results[0][1]
        q_op = first.airflow_cfm
        q_max = q_op * 1.5
        q = [q_max * i / 300 for i in range(301)]
        for name, est in results:
            line, = ax.plot(q, est.curve(q), lw=2, label=f"{name}  ({est.pressure_drop_in_wg:.3f})")
            ax.plot([est.airflow_cfm], [est.pressure_drop_in_wg], "o",
                    color=line.get_color(), mec="k", ms=7, zorder=5)
        ax.axvline(q_op, color="0.5", ls="--", lw=1)
        ax.set_xlim(0, q_max)
        ax.set_ylim(bottom=0)
        ax.set_xlabel("Airflow (cfm)")
        ax.set_ylabel("Pressure drop (in. wg)")
        ax.grid(True, alpha=0.4)
        ax.legend(loc="upper left", title="Model  (\u0394P at operating point)", fontsize=9)
        fa = first.face_area_ft2
        top = ax.secondary_xaxis("top", functions=(lambda x: x / fa, lambda v: v * fa))
        top.set_xlabel("Face velocity (fpm)")
        air = ""
        if abs(first.air_density - 0.075) > 0.0005:
            air = f"   (air {first.temperature_f:g}\u00b0F, {first.altitude_ft:,.0f} ft)"
        fig.suptitle(f'Model comparison  {first.width_in:g}" x {first.height_in:g}" '
                     f'at {q_op:,.0f} cfm{air}')
        self.cmp_canvas.draw_idle()

    def _on_cmp_double_click(self, _event):
        name = self.cmp_tree.focus()
        if not name or self.library is None or name not in self.library.models:
            return
        # Carry the comparison inputs over to the Estimate tab.
        self.width_var.set(self.cmp_width_var.get())
        self.height_var.set(self.cmp_height_var.get())
        self.flow_mode.set(self.cmp_flow_mode.get())
        self.flow_value_var.set(self.cmp_value_var.get())
        self.temp_var.set(self.cmp_temp_var.get())
        self.alt_var.set(self.cmp_alt_var.get())
        self.free_area_var.set("")
        self.notebook.select(0)
        self.select_model(name)

    # ---------------------------------------------------------------- help

    def show_data_help(self):
        messagebox.showinfo("Data file format", (
            "Test data is damper_data.csv, with one row per test point:\n\n"
            "width_in, height_in, face_velocity_fpm, pressure_drop_in_wg\n\n"
            "Optional column: free_area_ft2 (overrides the geometry estimate "
            "for that size).\n\n"
            "Lines starting with # are notes and are ignored.\n\n"
            "Measured free areas go in free_area_data.csv with columns "
            "width_in, height_in, free_area_ft2.\n\n"
            "Frame and blade dimensions for the free-area estimate are in "
            "geometry.json (editable in the Free area tab).\n\n"
            "Blade counts by section height go in blade_table.csv, and size limits "
            "and multi-section rules in size_rules.json (both editable in the "
            "Sizes & blades tab).\n\n"
            "Each damper model has its own folder holding these files, "
            "under data/models. Use New model... to create one.\n\n"
            "Instead of separate files, a model can keep everything in one Excel "
            "workbook, model_data.xlsx, with the sheets Test data, Free area, Blade "
            "table, Size rules and Geometry (File > Convert model to Excel workbook). "
            "Changes saved in the program are written back to the workbook; close "
            "it in Excel before saving.\n\n"
            "Press F5 to reload after editing any of these files."
        ))

    def show_about(self):
        messagebox.showinfo("About", (
            f"{APP_NAME}\nVersion {__version__}\n\n"
            "Estimates fire/smoke damper pressure drop from test data using a "
            "free-area loss-coefficient model.\n\n"
            "Estimates are only as good as the test data behind them. Check "
            "results against manufacturer data before relying on them."
        ))


def main(argv=None):
    parser = argparse.ArgumentParser(description=APP_NAME)
    parser.add_argument("--models-dir", help="folder holding one sub-folder per damper model")
    parser.add_argument("--model", help="damper model to select at start")
    args = parser.parse_args(argv)
    App(args.models_dir, args.model).mainloop()


if __name__ == "__main__":
    main()
