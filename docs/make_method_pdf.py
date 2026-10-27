"""Build the calculation-method PDF from the program itself.

    python docs/make_method_pdf.py            # writes docs/Damper_Pressure_Drop_Calculation_Method.pdf

Every number, table and figure in the worked examples is computed by the
program from the SAMPLE-FSD-100 model, so the document always matches the
code. Rebuild it after changing the method or the sample data.

Needs reportlab and matplotlib (pip install reportlab matplotlib).
"""

from __future__ import annotations

import io
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import matplotlib  # noqa: E402
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import Rectangle  # noqa: E402
from matplotlib.ticker import PercentFormatter  # noqa: E402
import numpy as np  # noqa: E402

from reportlab.lib import colors  # noqa: E402
from reportlab.lib.enums import TA_CENTER  # noqa: E402
from reportlab.lib.pagesizes import letter  # noqa: E402
from reportlab.lib.styles import ParagraphStyle  # noqa: E402
from reportlab.lib.units import inch  # noqa: E402
from reportlab.pdfbase import pdfmetrics  # noqa: E402
from reportlab.pdfbase.ttfonts import TTFont  # noqa: E402
from reportlab.platypus import (  # noqa: E402
    Image, KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
)

from damper_dp import __version__, physics as ph  # noqa: E402
from damper_dp.model import (  # noqa: E402
    BLEND_RANGE, EXPONENT_TOLERANCE, PA_PER_IN_WG, DamperModel, calibrate_geometry,
)

OUT = ROOT / "docs" / "Damper_Pressure_Drop_Calculation_Method.pdf"
MODEL_DIR = ROOT / "data" / "models" / "SAMPLE-FSD-100"

# --------------------------------------------------------------------------- fonts & styles

FONT_DIRS = [Path("/usr/share/fonts/truetype/dejavu"), Path("C:/Windows/Fonts"),
             Path(matplotlib.get_data_path()) / "fonts" / "ttf"]


def _register_fonts():
    names = {"DejaVu": "DejaVuSans.ttf", "DejaVu-Bold": "DejaVuSans-Bold.ttf",
             "DejaVu-Oblique": "DejaVuSans-Oblique.ttf",
             "DejaVu-BoldOblique": "DejaVuSans-BoldOblique.ttf"}
    for name, file in names.items():
        path = next((d / file for d in FONT_DIRS if (d / file).exists()), None)
        if path is None:
            raise SystemExit(f"Font {file} not found (looked in {', '.join(map(str, FONT_DIRS))}).")
        pdfmetrics.registerFont(TTFont(name, str(path)))
    pdfmetrics.registerFontFamily("DejaVu", normal="DejaVu", bold="DejaVu-Bold",
                                  italic="DejaVu-Oblique", boldItalic="DejaVu-BoldOblique")


_register_fonts()

NAVY = colors.HexColor("#1f3864")
BLUE = colors.HexColor("#2f5597")
TINT = colors.HexColor("#f2f5fa")
GRID = colors.HexColor("#c8d0dc")
WARN_BG = colors.HexColor("#fff4e0")
WARN_BAR = colors.HexColor("#e0a030")
NOTE_BG = colors.HexColor("#eaf4ea")
NOTE_BAR = colors.HexColor("#4a9a4a")
GRAY = colors.HexColor("#6b7a90")

BODY = ParagraphStyle("body", fontName="DejaVu", fontSize=9.6, leading=13.6, spaceAfter=6)
SMALL = ParagraphStyle("small", parent=BODY, fontSize=8.6, leading=11.5, spaceAfter=0)
CELL = ParagraphStyle("cell", parent=BODY, fontSize=8.6, leading=11, spaceAfter=0)
CELL_HEAD = ParagraphStyle("cellh", parent=CELL, fontName="DejaVu-Bold", textColor=colors.white)
TITLE = ParagraphStyle("title", fontName="DejaVu-Bold", fontSize=22, leading=27,
                       textColor=NAVY, spaceAfter=6)
SUBTITLE = ParagraphStyle("sub", fontName="DejaVu", fontSize=11.5, leading=15,
                          textColor=GRAY, spaceAfter=14)
H1 = ParagraphStyle("h1", fontName="DejaVu-Bold", fontSize=15, leading=19, textColor=NAVY,
                    spaceBefore=14, spaceAfter=7, keepWithNext=1)
H2 = ParagraphStyle("h2", fontName="DejaVu-Bold", fontSize=11.5, leading=15, textColor=BLUE,
                    spaceBefore=9, spaceAfter=4, keepWithNext=1)
EQ = ParagraphStyle("eq", fontName="DejaVu-Oblique", fontSize=10.5, leading=17,
                    alignment=TA_CENTER)
CAPTION = ParagraphStyle("cap", fontName="DejaVu-Oblique", fontSize=8.6, leading=11.5,
                         textColor=GRAY, alignment=TA_CENTER, spaceBefore=3, spaceAfter=10)

CONTENT_W = letter[0] - 2 * 0.9 * inch


def p(text, style=BODY):
    return Paragraph(text, style)


def sub(base, s):
    return f"{base}<sub>{s}</sub>"


def eq_box(*lines):
    t = Table([[p(line, EQ)] for line in lines], colWidths=[CONTENT_W])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), TINT),
        ("LINEBEFORE", (0, 0), (0, -1), 3, BLUE),
        ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ("TOPPADDING", (0, 0), (-1, 0), 7), ("BOTTOMPADDING", (0, -1), (-1, -1), 7),
    ]))
    return KeepTogether([t, Spacer(1, 7)])


def callout(text, bg=WARN_BG, bar=WARN_BAR):
    t = Table([[p(text, ParagraphStyle("co", parent=BODY, spaceAfter=0))]], colWidths=[CONTENT_W])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), bg), ("LINEBEFORE", (0, 0), (0, -1), 3, bar),
        ("TOPPADDING", (0, 0), (-1, -1), 7), ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
    ]))
    return KeepTogether([t, Spacer(1, 8)])


def table(rows, widths, bold_last=False):
    """rows[0] is the header. widths are fractions of the content width."""
    data = [[p(str(c), CELL_HEAD) for c in rows[0]]]
    data += [[p(str(c), CELL) for c in r] for r in rows[1:]]
    t = Table(data, colWidths=[w * CONTENT_W for w in widths], repeatRows=1)
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), NAVY),
        ("GRID", (0, 0), (-1, -1), 0.5, GRID),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]
    for i in range(2, len(data), 2):
        style.append(("BACKGROUND", (0, i), (-1, i), TINT))
    t.setStyle(TableStyle(style))
    return KeepTogether([t, Spacer(1, 10)])


def figure(fig, width_in, caption):
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=200)
    plt.close(fig)
    buf.seek(0)
    w, h = fig.get_size_inches()
    img = Image(buf, width=width_in * inch, height=width_in * h / w * inch)
    return KeepTogether([img, p(caption, CAPTION)])


plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 8.5, "axes.titlesize": 9.5})

# --------------------------------------------------------------------------- figures


def fig_geometry(model, w, h):
    bd = model.design.breakdown(w, h)
    g = model.geometry
    fig, ax = plt.subplots(figsize=(5.2, 3.6), layout="constrained")
    fw, fh = g.frame_obstruction_width_in, g.frame_obstruction_height_in
    ax.add_patch(Rectangle((0, 0), w, h, fc="#b0b0b0", ec="k", lw=1.2))
    ax.add_patch(Rectangle((fw, fh), w - 2 * fw, h - 2 * fh, fc="#dceeff", ec="k", lw=0.6))
    oh = h - 2 * fh
    for i in range(bd.n_blades):
        y = fh + oh * (i + 0.5) / bd.n_blades
        ax.add_patch(Rectangle((fw, y - g.blade_thickness_in / 2), w - 2 * fw,
                               g.blade_thickness_in, fc="#1f4e99", ec="none"))
    ax.annotate("", (0, -2), (w, -2), arrowprops=dict(arrowstyle="<->", lw=0.8))
    ax.text(w / 2, -2, f"W = {w:g} in", ha="center", va="center", backgroundcolor="white")
    ax.annotate("", (-2, 0), (-2, h), arrowprops=dict(arrowstyle="<->", lw=0.8))
    ax.text(-2, h / 2, f"H = {h:g} in", ha="center", va="center", rotation=90,
            backgroundcolor="white")
    ax.annotate("", (fw, h + 2), (w - fw, h + 2), arrowprops=dict(arrowstyle="<->", lw=0.8))
    ax.text(w / 2, h + 2, f"open width {w - 2 * fw:g} in", ha="center", va="center",
            backgroundcolor="white")
    ax.annotate(f"frame obstruction\nf = {fw:g} in each side", (fw * 0.5, h - fh * 0.5),
                (-4, h + 6), fontsize=7.5, arrowprops=dict(arrowstyle="-", lw=0.6))
    ax.annotate(f"blade: thickness t = {g.blade_thickness_in:g} in\n"
                f"({bd.n_blades} blades from {'blade table' if 'table' in bd.blade_source else 'pitch'})",
                (w * 0.6, fh + oh * 0.5 / bd.n_blades), (w + 3, 2), fontsize=7.5,
                arrowprops=dict(arrowstyle="-", lw=0.6))
    ax.set_xlim(-8, w + 22)
    ax.set_ylim(-5, h + 9)
    ax.set_aspect("equal")
    ax.axis("off")
    return fig, bd


def fig_fit(fit):
    v_free = fit.face_velocities_fpm * fit.face_area_ft2 / fit.free_area_ft2
    x = ph.velocity_pressure(v_free)
    fig, ax = plt.subplots(figsize=(5.0, 3.2), layout="constrained")
    xs = np.linspace(0, x.max() * 1.1, 50)
    ax.plot(xs, fit.c_free * xs, "-", color="#1f4e99", lw=2,
            label=f"Fitted line through zero: slope $C_{{free}}$ = {fit.c_free:.4f}")
    ax.plot(x, fit.pressure_drops_in_wg, "o", mfc="tab:red", mec="k", ms=6,
            label=f"Test points ({fit.label})")
    for xi, yi, v in zip(x, fit.pressure_drops_in_wg, fit.face_velocities_fpm):
        ax.annotate(f"{v:,.0f} fpm", (xi, yi), (8, -10), textcoords="offset points", fontsize=7,
                    color="0.35")
    ax.set_xlabel("Velocity pressure through the free area, VP$_{free}$ (in. wg)")
    ax.set_ylabel("Measured pressure drop ΔP (in. wg)")
    ax.set_xlim(0)
    ax.set_ylim(0)
    ax.grid(alpha=0.4)
    ax.legend(loc="upper left")
    return fig


def fig_size_model(model):
    fig, ax = plt.subplots(figsize=(5.4, 3.5), layout="constrained")
    sm = model.size_models
    lo, hi = sm["linear"].ratio_min, sm["linear"].ratio_max
    ax.axvspan(lo, hi, color="0.92", label="Tested range of R")
    xs = np.linspace(max(lo - 0.08, 0.2), min(hi + 0.06, 0.95), 120)
    for kind, style in (("linear", dict(color="#1f4e99", lw=2)),
                        ("curved", dict(color="#e07020", lw=2, ls="--"))):
        m = sm[kind]
        ax.plot(xs, [m.c_free(x) for x in xs], **style,
                label=f"{kind}: R² = {m.r2:.3f}")
    for f in model.fits:
        ax.plot(f.free_ratio, f.c_free, "o", mfc="tab:red", mec="k", ms=6)
        ax.annotate(f.label, (f.free_ratio, f.c_free), (6, 4), textcoords="offset points",
                    fontsize=7)
    ax.plot([], [], "o", mfc="tab:red", mec="k", label="$C_{free}$ of each tested size")
    ax.xaxis.set_major_formatter(PercentFormatter(1.0, decimals=0))
    ax.set_xlabel("Free-area ratio R = A$_{free}$ / A$_{face}$")
    ax.set_ylabel("Loss coefficient $C_{free}$")
    ax.grid(alpha=0.4)
    ax.legend(fontsize=7.5)
    return fig


def fig_blend(model_cal):
    sizes = np.round(np.arange(19.0, 30.001, 0.05), 2)   # whole hundredths: no blade-table gaps
    blended, line = [], []
    for s in sizes:
        e = model_cal.estimate(s, s, face_velocity_fpm=1000)
        blended.append(e.c_free)
        line.append(e.c_free_size_model)
    fig, ax = plt.subplots(figsize=(5.4, 3.0), layout="constrained")
    ax.plot(sizes, line, color="0.55", lw=1.4, ls="--", label="Size model alone")
    ax.plot(sizes, blended, color="#1f4e99", lw=2, label="Used: size model + blend")
    for f in model_cal.fits:
        if sizes[0] <= f.width_in <= sizes[-1] and f.width_in == f.height_in:
            ax.plot(f.width_in, f.c_free, "o", mfc="tab:red", mec="k", ms=6)
            ax.annotate(f"tested {f.label}", (f.width_in, f.c_free), (6, 4),
                        textcoords="offset points", fontsize=7)
            ax.axvspan(f.width_in / (1 + BLEND_RANGE), f.width_in * (1 + BLEND_RANGE),
                       color="#1f4e99", alpha=0.06)
    ax.set_xlabel("Square damper size W = H (in)")
    ax.set_ylabel("Loss coefficient $C_{free}$")
    ax.grid(alpha=0.4)
    ax.legend(fontsize=7.5)
    return fig


def fig_curve(model, w, h, q_op, t_hot, z_hot):
    std = model.estimate(w, h, airflow_cfm=q_op)
    hot = model.estimate(w, h, airflow_cfm=q_op, temperature_f=t_hot, altitude_ft=z_hot)
    fit = std.tested
    q_max = fit.v_face_max * std.face_area_ft2 * 1.04
    q = np.linspace(0, q_max, 200)
    fig, ax = plt.subplots(figsize=(5.0, 3.2), layout="constrained")
    ax.plot(q, std.curve(q), "-", color="#1f4e99", lw=2, label="Standard air (70 °F, sea level)")
    ax.plot(q, hot.curve(q), "--", color="#e07020", lw=2, label=f"{t_hot:g} °F at {z_hot:,.0f} ft")
    ax.plot(fit.face_velocities_fpm * std.face_area_ft2, fit.pressure_drops_in_wg, "o",
            mfc="tab:red", mec="k", ms=5, label="Test data")
    ax.plot([q_op], [std.pressure_drop_in_wg], "D", mfc="gold", mec="k", ms=8,
            label=f"Example: {q_op:,.0f} cfm")
    ax.set_xlim(0, q_max)
    ax.set_ylim(0)
    ax.set_xlabel("Airflow (cfm)")
    ax.set_ylabel("ΔP (in. wg)")
    top = ax.secondary_xaxis("top", functions=(lambda x: x / std.face_area_ft2,
                                               lambda v: v * std.face_area_ft2))
    top.set_xlabel("Face velocity (fpm)")
    ax.grid(alpha=0.4)
    ax.legend(fontsize=7.5)
    return fig


def fig_sections(model, w, h):
    bd = model.design.breakdown(w, h)
    g = model.geometry
    fig, ax = plt.subplots(figsize=(5.2, 4.0), layout="constrained")
    sw, sh = bd.section_width_in, bd.section_height_in
    fw, fh = g.frame_obstruction_width_in, g.frame_obstruction_height_in
    for i in range(bd.sections_wide):
        for j in range(bd.sections_high):
            x0, y0 = i * sw, j * sh
            ax.add_patch(Rectangle((x0, y0), sw, sh, fc="#b0b0b0", ec="k", lw=0.6))
            ax.add_patch(Rectangle((x0 + fw, y0 + fh), sw - 2 * fw, sh - 2 * fh,
                                   fc="#dceeff", ec="k", lw=0.4))
            oh = sh - 2 * fh
            for k in range(bd.n_blades):
                y = y0 + fh + oh * (k + 0.5) / bd.n_blades
                ax.add_patch(Rectangle((x0 + fw, y - g.blade_thickness_in / 2), sw - 2 * fw,
                                       g.blade_thickness_in, fc="#1f4e99", ec="none"))
    for i in range(1, bd.sections_wide):
        ax.plot([i * sw] * 2, [0, h], color="#d03030", lw=2)
    for j in range(1, bd.sections_high):
        ax.plot([0, w], [j * sh] * 2, color="#d03030", lw=2)
    ax.add_patch(Rectangle((0, 0), w, h, fc="none", ec="k", lw=1.5))
    ax.annotate("", (0, -4), (w, -4), arrowprops=dict(arrowstyle="<->", lw=0.8))
    ax.text(w / 2, -4, f"{w:g} in = {bd.sections_wide} sections of {sw:g} in", ha="center",
            va="center", backgroundcolor="white")
    ax.annotate("", (-4, 0), (-4, h), arrowprops=dict(arrowstyle="<->", lw=0.8))
    ax.text(-4, h / 2, f"{h:g} in = {bd.sections_high} sections of {sh:g} in", ha="center",
            va="center", rotation=90, backgroundcolor="white")
    ax.plot([], [], color="#d03030", lw=2, label="Section joints")
    ax.add_patch(Rectangle((0, 0), 0, 0, fc="#b0b0b0", ec="k", label="Frame / sleeve"))
    ax.add_patch(Rectangle((0, 0), 0, 0, fc="#dceeff", ec="k", label="Free area"))
    ax.add_patch(Rectangle((0, 0), 0, 0, fc="#1f4e99", label="Blades (edge-on)"))
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.02), ncol=4, fontsize=7, frameon=False)
    ax.set_xlim(-9, w + 3)
    ax.set_ylim(-8, h + 3)
    ax.set_aspect("equal")
    ax.axis("off")
    return fig, bd


# --------------------------------------------------------------------------- document


def fmt(x, d=4):
    return f"{x:,.{d}f}"


def build():
    model = DamperModel.from_folder(MODEL_DIR)
    lin = model.size_models["linear"]
    cur = model.size_models["curved"]
    rules = model.size_rules
    g = model.geometry
    story = []

    # ---------------- page 1
    story += [
        p("How the Damper Pressure Drop Estimator Calculates Pressure Drop", TITLE),
        p("Method description with worked examples, for fire/smoke dampers in IP units", SUBTITLE),
        p("This document explains, step by step, how the program turns a damper size and an "
          "airflow into a pressure drop: where every number comes from, which equations are used, "
          "and how test data, measured free areas, the blade table and the multi-section size rules "
          "feed into the result. Three worked examples at the end follow a tested size, a size next "
          "to a tested one, and a multi-section damper through every step."),
        callout("<b>Sample data.</b> The worked examples use the SAMPLE-FSD-100 model shipped with "
                "the program, whose data is made up for demonstration. The method is the same for "
                "real models; only the numbers change. Every number in this document is calculated "
                "by the program itself (docs/make_method_pdf.py)."),
        p("1. The idea in one equation", H1),
        p("A damper is an obstruction in the duct. The frame, sleeve, blades and linkage block part "
          "of the opening, so the air has to speed up to get through the <b>free area</b> that is "
          "left. Like any obstruction, the pressure it costs is proportional to the <b>velocity "
          "pressure</b> of that faster air. The program therefore uses:"),
        eq_box(f"ΔP = {sub('C','free')} × {sub('VP','free')}",
               f"{sub('VP','free')} = (ρ / 0.075) × ({sub('V','free')} / 4005)<super>2</super>"
               f"&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;{sub('V','free')} = Q / {sub('A','free')}"),
        p(f"ΔP is the pressure drop (in. wg), Q the airflow (cfm), {sub('A','free')} the free area "
          f"(ft²), ρ the air density (lb/ft³) and {sub('C','free')} a dimensionless <b>loss "
          f"coefficient</b>. Everything except {sub('C','free')} follows from the size, the "
          f"geometry and the air conditions. {sub('C','free')} is learned from your test data. "
          f"Because the pressure drop scales with velocity squared, doubling the airflow quadruples "
          f"the pressure drop."),
        p("The six steps", H2),
        table([["Step", "What happens", "Uses"],
               ["1", "Check the size against the model's limits and split it into sections if needed",
                "size_rules.json"],
               ["2", "Find the free area", "free_area_data.csv, geometry.json, blade_table.csv"],
               ["3", "Work out the face and free-area velocities", "airflow or face velocity"],
               ["4", "Find the air density and the velocity pressure", "temperature, altitude"],
               ["5", f"Choose the loss coefficient {sub('C','free')}",
                "damper_data.csv (test data), size_model rule"],
               ["6", f"Multiply: ΔP = {sub('C','free')} × {sub('VP','free')}", ""]],
              [0.08, 0.55, 0.37]),
        PageBreak(),
    ]

    # ---------------- step 1 & 2
    story += [
        p("2. Step 1: size check and section layout", H1),
        p("The size is first checked against the model's minimum and maximum overall width and "
          "height in <b>size_rules.json</b>. A size outside the limits is rejected with a message "
          "rather than estimated, because the model is not made in that size."),
        p("If the model has a maximum section width or height, a larger damper is split into the "
          "fewest <b>equal</b> sections that keep each one within the limit:"),
        eq_box(f"{sub('n','w')} = ⌈ W / {sub('W','max section')} ⌉&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;"
               f"{sub('n','h')} = ⌈ H / {sub('H','max section')} ⌉",
               f"section size&nbsp;&nbsp;{sub('W','s')} = W / {sub('n','w')},&nbsp;&nbsp;"
               f"{sub('H','s')} = H / {sub('n','h')}"),
        p(f"⌈ ⌉ means round up to the next whole number. With a 48 in maximum section width, a 96 in "
          f"wide damper becomes 2 sections of 48 in, and a 100 in wide damper becomes 3 sections of "
          f"33.33 in. Each section is treated as its own damper with its own frame, blades and other "
          f"blockage. With no section limits, every damper is a single section "
          f"({sub('n','w')} = {sub('n','h')} = 1)."),
        p("3. Step 2: free area", H1),
        p("The free area of the requested size is taken from the first of these sources that has it:"),
        table([["Priority", "Source", "When it applies"],
               ["1", "free_area_ft2 column in damper_data.csv",
                "The size has a free area entered with its test data"],
               ["2", "Measured free-area table, free_area_data.csv",
                "The size is listed in the table (within 0.01 in)"],
               ["3", "Geometry estimate", "Any other size"],
               ["(override)", "Free area typed into the Estimate tab",
                "Replaces all of the above for that estimate"]],
              [0.13, 0.42, 0.45]),
        p("Geometry estimate", H2),
        p("For each section, the frame obstruction <i>f</i> is removed from every side to give the "
          "clear opening, then the blades (each blocking a strip of thickness <i>t</i> across the "
          "clear width) and the other blockage <i>O</i> (linkage, jackshaft, stops) are subtracted:"),
        eq_box(f"{sub('w','open')} = {sub('W','s')} − 2{sub('f','w')}&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;"
               f"{sub('h','open')} = {sub('H','s')} − 2{sub('f','h')}",
               f"{sub('A','section')} = {sub('w','open')} × {sub('h','open')} − N × t × "
               f"{sub('w','open')} − O",
               f"{sub('A','free')} = {sub('n','w')}{sub('n','h')} × {sub('A','section')} − "
               f"{sub('A','joints')}&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;(in², divide by 144 for ft²)"),
    ]
    fig, bd24 = fig_geometry(model, 24, 24)
    a24 = bd24.free_area_in2
    story += [
        figure(fig, 4.6,
               f"Figure 1. Geometry of a single 24 x 24 section (SAMPLE-FSD-100 geometry). Free area "
               f"= {24 - 2 * g.frame_obstruction_width_in:g} × {24 - 2 * g.frame_obstruction_height_in:g}"
               f" − {bd24.n_blades} × {g.blade_thickness_in:g} × {24 - 2 * g.frame_obstruction_width_in:g}"
               f" − {g.other_blockage_in2:g} = {a24:,.0f} in² = {a24 / 144:.3f} ft²."),
        p("Number of blades <i>N</i>", H2),
        p(f"<i>N</i> is looked up in the <b>blade table</b> (blade_table.csv) by the <b>nominal "
          f"section height</b> {sub('H','s')}, the way blade schedules are usually written. Each row "
          f"covers a height range including both ends; a height on a boundary shared by two rows uses "
          f"the lower row. If the height falls in a gap between two rows, the program stops with an "
          f"error, because the table is probably missing a row. If the height is above or below the "
          f"whole table, or the table is empty, the blade pitch <i>p</i> is used instead (with a "
          f"warning on the estimate when a table exists). The pitch rule works on the <b>open "
          f"height</b>, because blades only fill the clear opening:"),
        eq_box(f"N = ⌈ {sub('h','open')} / p ⌉&nbsp;&nbsp;&nbsp;&nbsp;(only when the blade table "
               f"does not cover the section height)"),
        p("Joints between sections", H2),
        p(f"Each section already has its own frame, so back-to-back frames at a joint are counted "
          f"automatically. The optional <b>joint blockage</b> <i>j</i> adds extra width blocked "
          f"along every joint (a mullion or connecting angles). There are {sub('n','w')} − 1 "
          f"vertical joints of length H and {sub('n','h')} − 1 horizontal joints of length W; where "
          f"they cross, the overlap would be counted twice, so it is taken off once:"),
        eq_box(f"{sub('A','joints')} = j × [ ({sub('n','w')} − 1) × H + ({sub('n','h')} − 1) × W ] "
               f"− j<super>2</super> × ({sub('n','w')} − 1)({sub('n','h')} − 1)"),
        p("Calibrating the geometry", H2),
        p("When measured free areas are available, <b>Calibrate geometry</b> finds the frame "
          "obstruction, blade thickness and other blockage that make the geometry estimate match "
          "them best (least squares). The frame obstruction is searched in 1/32 in steps from 0 to "
          "6 in. For each frame value, the equations above are linear in <i>t</i> and <i>O</i>, so "
          "those two follow directly from a linear least-squares fit, constrained to be zero or "
          "positive. The blade counts come from the blade table (or the pitch, which is not fitted) "
          "and multi-section sizes are split by the size rules, exactly as for an estimate. Sizes "
          "listed in the measured table always use their measured value; the calibrated geometry "
          "only affects sizes that are not listed."),
        callout("<b>Calibrate before relying on untested sizes.</b> A tested size's own estimate "
                "does not depend on its free area: its coefficient was fitted with the same free "
                "area, so the two cancel. Free area only matters for sizes that were not tested, "
                "through the free-area ratio in the size model. If the geometry estimate disagrees "
                "with the measured table, a size just next to a tested one can get a noticeably "
                "different pressure drop, because it takes its free area from the geometry. The "
                f"SAMPLE-FSD-100 geometry is left uncalibrated (it over-estimates the 24 x 24 free "
                f"area by {(a24 / 144 / model.measured_free_area(24, 24).free_area_ft2 - 1):.1%}).",
                NOTE_BG, NOTE_BAR),
    ]

    # ---------------- step 3, 4
    story += [
        p("4. Step 3: velocities", H1),
        p("The airflow can be entered directly or as a face velocity; the other follows from the "
          "face area:"),
        eq_box(f"{sub('A','face')} = W × H / 144&nbsp;&nbsp;(ft²)&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;"
               f"Q = {sub('V','face')} × {sub('A','face')}",
               f"{sub('V','free')} = Q / {sub('A','free')} = {sub('V','face')} / R"
               f"&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;free-area ratio&nbsp;&nbsp;R = "
               f"{sub('A','free')} / {sub('A','face')}"),
        p("R is the fraction of the nominal opening that is actually open. A damper with R = 65% "
          "makes the air move 1 / 0.65 = 1.54 times faster than the face velocity."),
        p("5. Step 4: air density and velocity pressure", H1),
        p("Air density is found from the temperature T (°F) and altitude z (ft) using the "
          "standard-atmosphere pressure (ASHRAE) and the ideal-gas law, referenced to standard air "
          "(0.075 lb/ft³ at 70 °F and sea level). Humidity is not included; its effect is usually "
          "under 1%."),
        eq_box("ρ = 0.075 × (1 − 6.8754×10<super>−6</super> × z)<super>5.2559</super> × 529.67 / "
               "(T + 459.67)"),
        p("The velocity pressure is the pressure equivalent of the air's speed. For standard air, "
          "V = 4005 × √VP (fpm, in. wg), which gives:"),
        eq_box(f"{sub('VP','free')} = (ρ / 0.075) × ({sub('V','free')} / 4005)<super>2</super>"
               f"&nbsp;&nbsp;&nbsp;(in. wg)"),
        p("The factor ρ / 0.075 corrects for non-standard air: lighter air (hot, or at altitude) "
          "carries less velocity pressure at the same velocity, so it causes a smaller pressure drop. "
          "Test data is assumed to be at standard air, which is how AMCA 500-D test reports present it."),
    ]

    # ---------------- step 5
    fit24 = model.find_size(24, 24)
    story += [
        p(f"6. Step 5: the loss coefficient {sub('C','free')}", H1),
        p("5a. Fitting each tested size", H2),
        p(f"For every size in damper_data.csv, each test point's face velocity is converted to a "
          f"free-area velocity pressure {sub('x','i')} = {sub('VP','free,i')} (standard air), and "
          f"paired with its measured pressure drop {sub('y','i')}. The model says y = C × x, a "
          f"straight line through zero (no flow, no pressure drop). The best-fitting slope by least "
          f"squares is:"),
        eq_box(f"{sub('C','free')} = Σ {sub('x','i')}{sub('y','i')} / Σ {sub('x','i')}<super>2</super>"
               f"&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;R<super>2</super> = 1 − Σ({sub('y','i')} − "
               f"C{sub('x','i')})<super>2</super> / Σ({sub('y','i')} − ȳ)<super>2</super>"),
        p("R² (shown in the program for every size) measures how well the points follow the "
          "velocity-squared law: 1.000 is a perfect fit. Because the fit minimises absolute errors, "
          "it is steered mostly by the high-velocity points, where the pressure drops are largest. "
          "Two further checks show whether the lower velocities agree (5b)."),
        figure(fig_fit(fit24), 4.6,
               f"Figure 2. Fitting the 24 x 24 size of SAMPLE-FSD-100. Plotted against the free-area "
               f"velocity pressure, the test points fall on a straight line through zero; its slope "
               f"is {sub('C','free')} = {fit24.c_free:.4f} (R² = {fit24.r2:.3f})."),
        p("5b. Fit diagnostics: exponent and residuals", H2),
        p(f"For each tested size the program also fits ΔP = k × V<super>n</super> on log scales. For "
          f"turbulent flow through a damper the exponent <i>n</i> should be close to 2. A size whose "
          f"<i>n</i> is outside 2 ± {EXPONENT_TOLERANCE:g} is marked in the program, and an estimate "
          f"that uses it carries a warning, because the velocity-squared fit then hides a problem with "
          f"the data. The <b>residual</b> of each test point, (measured − fitted) / fitted, shows "
          f"where the fit misses; both are listed in the Test data &amp; model tab and by "
          f"<font name='DejaVu'>--list</font> on the command line."),
        table([["Size", "n", "Largest residual", "Size", "n", "Largest residual"]] +
              [[a.label, f"{a.exponent:.2f}", f"{a.max_residual_pct:+.1f}%",
                b.label if b else "", f"{b.exponent:.2f}" if b else "",
                f"{b.max_residual_pct:+.1f}%" if b else ""]
               for a, b in zip(model.fits[0::2], model.fits[1::2] + [None])],
              [0.18, 0.1, 0.22, 0.18, 0.1, 0.22]),
        p("5c. The size model", H2),
        p(f"{sub('C','free')} differs between sizes. Small dampers lose a larger share of their "
          f"opening to the frame and blades (lower R), and the flow through them contracts and "
          f"expands more sharply, so they have a higher coefficient. To estimate sizes that were not "
          f"tested, the program fits the coefficients of all tested sizes against their free-area "
          f"ratio, in one of two shapes chosen per model by the <b>size_model</b> rule "
          f"(size_rules.json, the Size rules sheet, or the Sizes &amp; blades tab):"),
        eq_box(f"linear (default):&nbsp;&nbsp;{sub('C','free')}(R) = a + b × R",
               f"curved:&nbsp;&nbsp;{sub('C','free')}(R) = a + b × (1/R − 1)<super>2</super>"),
        p("The curved shape is the shape of the classic loss coefficient of a grille or perforated "
          "plate (Idelchik): it rises steeply as R gets small and levels off as R approaches 1. A "
          "straight line can fit well inside the tested range but under-predicts small dampers below "
          "it, and keeps falling above it. Both shapes are fitted every time and both are plotted in "
          "the Test data &amp; model tab, so you can see which one follows your data before choosing. "
          "For SAMPLE-FSD-100, whose made-up data is nearly linear:"),
        table([["Shape", "Fitted size model", "R² across sizes"],
               ["linear (in use)", f"{sub('C','free')} = {lin.intercept:.4f} − {abs(lin.slope):.4f} × R",
                f"{lin.r2:.3f}"],
               ["curved", f"{sub('C','free')} = {cur.intercept:.4f} + {cur.slope:.4f} × "
                          f"(1/R − 1)<super>2</super>", f"{cur.r2:.3f}"]],
              [0.2, 0.6, 0.2]),
        p("This needs at least 3 tested sizes whose ratios differ by at least 2 percentage points. "
          "With fewer, the average coefficient of the tested sizes is used for every size instead."),
        figure(fig_size_model(model), 4.8,
               f"Figure 3. Both size-model shapes for SAMPLE-FSD-100. Each red point is one tested "
               f"size's fitted {sub('C','free')}. Outside the shaded tested range the two shapes "
               f"diverge; real data usually curves like the dashed line at small R."),
        p("5d. Blending next to a tested size", H2),
        p(f"A tested size uses its own fitted coefficient, which is usually a little above or below "
          f"the size model. Switching straight from one to the other would make the pressure drop jump "
          f"between a tested size and a size a fraction of an inch away. Instead, an untested size "
          f"within {BLEND_RANGE:.0%} of a tested size, in both width and height, keeps part of that "
          f"size's offset from the size model:"),
        eq_box(f"{sub('C','free')} = {sub('C','model')}(R) + w × [ {sub('C','tested')} − "
               f"{sub('C','model')}({sub('R','tested')}) ]",
               f"w = 1 − d / ln(1 + {BLEND_RANGE:g}),&nbsp;&nbsp;&nbsp;d = max( |ln(W / "
               f"{sub('W','tested')})|, |ln(H / {sub('H','tested')})| ),&nbsp;&nbsp;&nbsp;w ≥ 0"),
        p(f"<i>w</i> is 1 at the tested size and falls linearly to 0 at {BLEND_RANGE:.0%} away; "
          f"the nearest tested size (largest <i>w</i>) is used. Keeping the offset, rather than the "
          f"tested coefficient itself, means the size model still accounts for the difference in "
          f"free-area ratio. The Estimate tab shows the size-model value, the nearest tested size, "
          f"its offset and the weight. A free area typed in as an override uses the size model at "
          f"that ratio, with no blending."),
    ]
    cal = calibrate_geometry(model.free_area_table, g.blade_pitch_in,
                             blade_table=model.blade_table, size_rules=rules)
    model_cal = DamperModel(model.points, cal.geometry, free_area_table=model.free_area_table,
                            blade_table=model.blade_table, size_rules=rules)
    story += [
        figure(fig_blend(model_cal), 4.8,
               f"Figure 4. {sub('C','free')} for square sizes around the tested 24 x 24 "
               f"(SAMPLE-FSD-100 with its geometry calibrated to the measured free areas). Shaded: "
               f"the ±{BLEND_RANGE:.0%} blend zone. The curve passes smoothly through the tested "
               f"size; the steps at 22 and 28 in are the blade count changing."),
        p("5e. Which coefficient is used", H2),
        table([["Situation", "Coefficient used"],
               ["The exact size was tested (within 0.01 in)", f"That size's own fitted {sub('C','free')}"],
               ["Single-section size within 15% of a tested size",
                "Size model at the damper's R, plus the weighted offset of the nearest tested size (5d)"],
               ["Multi-section damper", f"Size model at the R of the whole assembly, joints included, "
                f"plus the weighted offset of the tested size nearest the <b>section</b> size. "
                f"If the section size was tested and there is no joint blockage, this is exactly "
                f"that section's {sub('C','free')}: every section sees the same face velocity, so it "
                f"behaves like the tested damper."],
               ["Any other size", "Size model at the damper's free-area ratio R"],
               ["Free area override entered", "Size model at the ratio given by the override"]],
              [0.4, 0.6]),
        p(f"For a multi-section damper the size model is read at the ratio of the whole assembly, "
          f"<b>including</b> the joint blockage. The joints block each section like extra frame, so "
          f"they reduce its effective free-area ratio and raise its coefficient as well as the "
          f"velocity. (Program versions before 0.5 read the size model at the section's ratio "
          f"without the joints, which gave a slightly lower, less conservative coefficient.)"),
        p(f"Catalogs often quote a coefficient based on face velocity instead. The two are related "
          f"by {sub('C','face')} = {sub('C','free')} / R<super>2</super>; the program shows both."),
        p("7. Step 6: pressure drop", H1),
        eq_box(f"ΔP = {sub('C','free')} × {sub('VP','free')}&nbsp;&nbsp;&nbsp;(in. wg)"
               f"&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;ΔP (Pa) = ΔP (in. wg) × {PA_PER_IN_WG}"),
        p("The curve drawn on the Estimate tab repeats this calculation over a range of airflows for "
          "the same damper and air conditions."),
    ]

    # ---------------- worked example 1
    q1, t_hot, z_hot = 4000, 120, 5000
    e1 = model.estimate(24, 24, airflow_cfm=q1)
    h1 = model.estimate(24, 24, airflow_cfm=q1, temperature_f=t_hot, altitude_ft=z_hot)
    entry = model.measured_free_area(24, 24)
    row_blade = next(r for r in model.blade_table.rules if r.min_height_in <= 24 <= r.max_height_in)
    story += [
        p("8. Worked example 1: a tested single-section size", H1),
        p(f"SAMPLE-FSD-100, 24 in × 24 in, {q1:,} cfm, standard air (70 °F, sea level)."),
        table([["Step", "Calculation", "Result"],
               ["1. Size and sections",
                f"Within limits ({rules.describe()}). 24 ≤ {rules.max_section_width_in:g}, so 1 section.",
                "1 section, 24 × 24"],
               ["2. Free area",
                f"24 × 24 is in the measured free-area table, so it takes priority. (The geometry "
                f"estimate would be {a24:,.0f} in² = {a24 / 144:.3f} ft², with {bd24.n_blades} blades "
                f"from the blade table row {row_blade.label}.)",
                f"{sub('A','free')} = {entry.free_area_ft2:.3f} ft²"],
               ["", f"{sub('A','face')} = 24 × 24 / 144 = {e1.face_area_ft2:.3f} ft²; R = "
                    f"{e1.free_area_ft2:.3f} / {e1.face_area_ft2:.3f}",
                f"R = {e1.free_ratio:.4f} ({e1.free_ratio:.1%})"],
               ["3. Velocities", f"{sub('V','face')} = {q1:,} / {e1.face_area_ft2:.3f}; "
                                 f"{sub('V','free')} = {q1:,} / {e1.free_area_ft2:.3f}",
                f"{e1.face_velocity_fpm:,.0f} fpm; {e1.free_velocity_fpm:,.1f} fpm"],
               ["4. Velocity pressure", f"ρ = 0.075 (standard air); VP = ({e1.free_velocity_fpm:,.1f}"
                                        f" / 4005)²",
                f"{sub('VP','free')} = {e1.velocity_pressure_in_wg:.5f} in. wg"],
               ["5. Coefficient", "24 × 24 was tested, so its own fit is used (table below)",
                f"{sub('C','free')} = {e1.c_free:.4f}"],
               ["6. Pressure drop", f"{e1.c_free:.4f} × {e1.velocity_pressure_in_wg:.5f}",
                f"<b>ΔP = {e1.pressure_drop_in_wg:.4f} in. wg</b> ({e1.pressure_drop_pa:.1f} Pa)"]],
              [0.18, 0.55, 0.27]),
    ]
    v_free = fit24.face_velocities_fpm * fit24.face_area_ft2 / fit24.free_area_ft2
    x = ph.velocity_pressure(v_free)
    y = fit24.pressure_drops_in_wg
    rows = [["V<sub>face</sub> (fpm)", "V<sub>free</sub> (fpm)", "x = VP<sub>free</sub> (in. wg)",
             "y = measured ΔP", "x·y", "x²", "C·x (fitted)", "Residual"]]
    for vf, vv, xi, yi, r in zip(fit24.face_velocities_fpm, v_free, x, y, fit24.residuals_pct):
        rows.append([f"{vf:,.0f}", f"{vv:,.1f}", f"{xi:.5f}", f"{yi:.3f}", f"{xi * yi:.6f}",
                     f"{xi * xi:.6f}", f"{fit24.c_free * xi:.4f}", f"{r:+.1f}%"])
    rows.append(["", "", "", "<b>Sums</b>", f"<b>{np.dot(x, y):.6f}</b>", f"<b>{np.dot(x, x):.6f}</b>",
                 "", ""])
    story += [
        KeepTogether([
            p(f"Where {sub('C','free')} = {fit24.c_free:.4f} comes from", H2),
            p(f"The test data for 24 × 24 (free area {fit24.free_area_ft2:.3f} ft², so "
              f"{sub('V','free')} = {sub('V','face')} × {fit24.face_area_ft2:.3f} / "
              f"{fit24.free_area_ft2:.3f}):"),
            table(rows, [0.11, 0.12, 0.15, 0.13, 0.13, 0.12, 0.12, 0.12])]),
        eq_box(f"{sub('C','free')} = Σxy / Σx<super>2</super> = {np.dot(x, y):.6f} / "
               f"{np.dot(x, x):.6f} = {fit24.c_free:.4f}"
               f"&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;n = {fit24.exponent:.2f}"),
        p("The same damper in hot air at altitude", H2),
        p(f"At {t_hot:g} °F and {z_hot:,} ft, ρ = 0.075 × (1 − 6.8754×10<super>−6</super> × "
          f"{z_hot})<super>5.2559</super> × 529.67 / {t_hot + 459.67:.2f} = {h1.air_density:.5f} lb/ft³. "
          f"Only the velocity pressure changes, by the factor ρ / 0.075 = {h1.air_density / 0.075:.4f}, "
          f"so ΔP = {e1.pressure_drop_in_wg:.4f} × {h1.air_density / 0.075:.4f} = "
          f"<b>{h1.pressure_drop_in_wg:.4f} in. wg</b>."),
        figure(fig_curve(model, 24, 24, q1, t_hot, z_hot), 4.6,
               "Figure 5. The resulting curve for the 24 x 24 damper, with its test data and the "
               "example operating point. Lighter air shifts the whole curve down."),
    ]

    # ---------------- worked example 2: near a tested size
    w2, q2 = 26, 4000
    e2 = model.estimate(w2, w2, airflow_cfm=q2)
    n2 = e2.nearest_tested
    d2 = math.log(w2 / n2.width_in)
    bd26 = model.design.breakdown(w2, w2)
    story += [
        PageBreak(),
        p("9. Worked example 2: a size next to a tested one", H1),
        p(f"SAMPLE-FSD-100, {w2} in × {w2} in, {q2:,} cfm, standard air. {w2} × {w2} was not tested "
          f"and is not in the measured free-area table, but it is within {BLEND_RANGE:.0%} of the "
          f"tested 24 × 24."),
        table([["Step", "Calculation", "Result"],
               ["1–2. Sections, free area",
                f"1 section. Geometry estimate: {bd26.n_blades} blades (blade table), "
                f"{bd26.free_area_in2:,.1f} in²; {sub('A','face')} = {e2.face_area_ft2:.3f} ft²",
                f"{sub('A','free')} = {e2.free_area_ft2:.3f} ft², R = {e2.free_ratio:.4f}"],
               ["3–4. Velocity pressure", f"{sub('V','free')} = {q2:,} / {e2.free_area_ft2:.3f}; "
                                          f"VP = ({e2.free_velocity_fpm:,.1f} / 4005)²",
                f"{sub('VP','free')} = {e2.velocity_pressure_in_wg:.5f} in. wg"],
               ["5a. Size model", f"{lin.intercept:.4f} − {abs(lin.slope):.4f} × {e2.free_ratio:.4f}",
                f"{sub('C','model')} = {e2.c_free_size_model:.4f}"],
               ["5b. Offset of 24 × 24", f"{n2.c_free:.4f} − ({lin.intercept:.4f} − "
                                         f"{abs(lin.slope):.4f} × {n2.free_ratio:.4f})",
                f"{e2.blend_offset:+.4f}"],
               ["5c. Weight", f"d = ln({w2} / 24) = {d2:.4f}; w = 1 − {d2:.4f} / ln({1 + BLEND_RANGE:g})",
                f"w = {e2.blend_weight:.3f}"],
               ["5d. Coefficient", f"{e2.c_free_size_model:.4f} + {e2.blend_weight:.3f} × "
                                   f"({e2.blend_offset:+.4f})",
                f"{sub('C','free')} = {e2.c_free:.4f}"],
               ["6. Pressure drop", f"{e2.c_free:.4f} × {e2.velocity_pressure_in_wg:.5f}",
                f"<b>ΔP = {e2.pressure_drop_in_wg:.4f} in. wg</b> ({e2.pressure_drop_pa:.1f} Pa)"]],
              [0.2, 0.53, 0.27]),
        callout(f"Here the 24 × 24 offset is small ({e2.blend_offset:+.4f}), so blending changes "
                f"{sub('C','free')} by only {abs(e2.c_free - e2.c_free_size_model):.4f}; with real, "
                f"scattered test data the offsets, and the effect of blending, are larger. Note that "
                f"26 × 26 takes its free area from the uncalibrated sample geometry (R = "
                f"{e2.free_ratio:.1%}), while 24 × 24 uses its measured {e1.free_ratio:.1%}. Once the "
                f"geometry is calibrated, the free-area ratios of neighbouring sizes line up as well "
                f"(Figure 4).", NOTE_BG, NOTE_BAR),
    ]

    # ---------------- worked example 3: multi-section
    w3, h3, q3 = 96, 72, 40000
    e3 = model.estimate(w3, h3, airflow_cfm=q3)
    fig, bd3 = fig_sections(model, w3, h3)
    row3 = next(r for r in model.blade_table.rules
                if r.min_height_in <= bd3.section_height_in <= r.max_height_in)
    sec_open_w = bd3.section_width_in - 2 * g.frame_obstruction_width_in
    sec_open_h = bd3.section_height_in - 2 * g.frame_obstruction_height_in
    a_sec = sec_open_w * sec_open_h - bd3.n_blades * g.blade_thickness_in * sec_open_w \
        - g.other_blockage_in2
    j = rules.joint_blockage_in
    near3, wt3 = model.nearest_tested(bd3.section_width_in, bd3.section_height_in)
    old_ratio = e3.free_ratio + bd3.joint_block_in2 / bd3.face_area_in2
    old_c = lin.c_free(old_ratio)
    story += [
        p("10. Worked example 3: an untested multi-section size", H1),
        p(f"SAMPLE-FSD-100, {w3} in × {h3} in, {q3:,} cfm, standard air. The model's size rules allow "
          f"up to {rules.max_width_in:g} × {rules.max_height_in:g} in overall, with sections of at "
          f"most {rules.max_section_width_in:g} × {rules.max_section_height_in:g} in and {j:g} in of "
          f"extra blockage at each joint."),
        figure(fig, 4.4,
               f"Figure 6. The {w3} x {h3} damper is split into {bd3.sections_wide} x "
               f"{bd3.sections_high} equal sections of {bd3.section_width_in:g} x "
               f"{bd3.section_height_in:g} in, each with its own frame and {bd3.n_blades} blades."),
        table([["Step", "Calculation", "Result"],
               ["1. Size and sections",
                f"{w3} ≤ {rules.max_width_in:g} and {h3} ≤ {rules.max_height_in:g}, within limits. "
                f"{sub('n','w')} = ⌈{w3} / {rules.max_section_width_in:g}⌉ = {bd3.sections_wide}, "
                f"{sub('n','h')} = ⌈{h3} / {rules.max_section_height_in:g}⌉ = {bd3.sections_high}",
                f"{bd3.n_sections} sections of {bd3.section_width_in:g} × {bd3.section_height_in:g}"],
               ["2. Blades", f"Section height {bd3.section_height_in:g} in is in the blade-table row "
                             f"{row3.label}", f"N = {bd3.n_blades} per section"],
               ["Free area per section",
                f"{sub('w','open')} = {bd3.section_width_in:g} − 2 × {g.frame_obstruction_width_in:g} "
                f"= {sec_open_w:g}; {sub('h','open')} = {bd3.section_height_in:g} − 2 × "
                f"{g.frame_obstruction_height_in:g} = {sec_open_h:g}; {sec_open_w:g} × {sec_open_h:g} "
                f"− {bd3.n_blades} × {g.blade_thickness_in:g} × {sec_open_w:g} − "
                f"{g.other_blockage_in2:g}", f"{sub('A','section')} = {a_sec:,.0f} in²"],
               ["Joints", f"{j:g} × [({bd3.sections_wide} − 1) × {h3} + ({bd3.sections_high} − 1) × "
                          f"{w3}] − {j:g}² × {bd3.sections_wide - 1} × {bd3.sections_high - 1}",
                f"{sub('A','joints')} = {bd3.joint_block_in2:,.2f} in²"],
               ["Total free area", f"{bd3.n_sections} × {a_sec:,.0f} − {bd3.joint_block_in2:,.2f} = "
                                   f"{bd3.free_area_in2:,.2f} in² (not in the measured table, so the "
                                   f"geometry estimate is used)",
                f"{sub('A','free')} = {e3.free_area_ft2:.3f} ft²"],
               ["", f"{sub('A','face')} = {w3} × {h3} / 144 = {e3.face_area_ft2:.3f} ft²",
                f"R = {e3.free_ratio:.4f} ({e3.free_ratio:.1%})"],
               ["3. Velocities", f"{sub('V','face')} = {q3:,} / {e3.face_area_ft2:g}; "
                                 f"{sub('V','free')} = {q3:,} / {e3.free_area_ft2:.3f}",
                f"{e3.face_velocity_fpm:,.1f} fpm; {e3.free_velocity_fpm:,.1f} fpm"],
               ["4. Velocity pressure", f"VP = ({e3.free_velocity_fpm:,.1f} / 4005)²",
                f"{sub('VP','free')} = {e3.velocity_pressure_in_wg:.5f} in. wg"],
               ["5. Coefficient",
                f"Neither {w3} × {h3} nor a {bd3.section_width_in:g} × {bd3.section_height_in:g} "
                f"section was tested" + (f", and no tested size is within {BLEND_RANGE:.0%} of the "
                f"section size, so there is no blend" if wt3 == 0 else
                f"; blended {wt3:.0%} towards {near3.label}") +
                f". Size model at the assembly ratio, joints included: {lin.intercept:.4f} − "
                f"{abs(lin.slope):.4f} × {e3.coeff_ratio:.4f}",
                f"{sub('C','free')} = {e3.c_free:.4f}"],
               ["6. Pressure drop", f"{e3.c_free:.4f} × {e3.velocity_pressure_in_wg:.5f}",
                f"<b>ΔP = {e3.pressure_drop_in_wg:.4f} in. wg</b> ({e3.pressure_drop_pa:.1f} Pa)"]],
              [0.2, 0.55, 0.25]),
        callout(f"<b>Change from version 0.4.</b> Earlier versions read the size model at the section "
                f"ratio without the joints, R = {e3.free_ratio:.4f} + {bd3.joint_block_in2:,.2f} / "
                f"{bd3.face_area_in2:,.0f} = {old_ratio:.4f}, giving {sub('C','free')} = {old_c:.4f} "
                f"and ΔP = {old_c * e3.velocity_pressure_in_wg:.4f} in. wg. Including the joints raises "
                f"the estimate by {(e3.c_free / old_c - 1):.1%}."
                + ("" if e3.warnings else f" The assembly ratio of {e3.coeff_ratio:.1%} is inside the "
                   f"tested range ({lin.ratio_min:.1%} to {lin.ratio_max:.1%}), so no extrapolation "
                   f"warning is shown.")),
    ]

    # ---------------- warnings, limitations, appendix
    story += [
        p("11. Warnings and errors", H1),
        p("An estimate is still given, with a warning, when:"),
        table([["Warning", "Meaning"],
               ["Free-area ratio outside the tested range",
                "The size model is extrapolated beyond the ratios it was fitted to."],
               ["Face area (or section face area) outside the tested sizes",
                "The size is smaller or larger than anything tested."],
               ["Face velocity above the highest tested velocity (by more than 5%)",
                "The velocity-squared law is extrapolated."],
               ["Face velocity below the lowest tested velocity (by more than 5%)",
                "Loss coefficients tend to rise at low velocity (low Reynolds number), so the real "
                "pressure drop may be somewhat higher than the estimate."],
               [f"Test data follows V<super>n</super> with n outside 2 ± {EXPONENT_TOLERANCE:g}",
                "The tested size used for the coefficient does not follow the velocity-squared law "
                "well; check its test points."],
               ["Fewer than 3 test points for this size", "The size's own fit is not well constrained."],
               ["Section height outside the blade table", "The blade count came from the blade pitch instead."]],
              [0.45, 0.55]),
        p("The two extrapolation warnings on ratio and face area are not shown when the coefficient "
          "comes entirely from a tested size (weight 1). No estimate is given, and the reason is "
          "shown, when the size is outside the model's limits, a section height falls in a gap in "
          "the blade table, the frame or blades leave no opening, the free area is not smaller than "
          "the face area, or the size model gives a coefficient of zero or less."),
        p("12. Assumptions and limitations", H1),
        p("• <b>Fully open blades.</b> The damper is modeled fully open, which is normal operation for "
          "fire/smoke dampers. Partly closed blades are not modeled."),
        p("• <b>Velocity-squared law.</b> Pressure drop is taken as proportional to velocity squared, "
          "which holds for the turbulent flow found in ducts. The fitted exponent n (5b) shows how "
          "well each tested size follows it."),
        p("• <b>Test configuration.</b> Pressure drop depends on how the damper was installed in the "
          "test (AMCA 500-D configuration). Estimates apply to the configuration the test data came "
          "from, and comparisons between models are only like-for-like when their data comes from the "
          "same configuration. Published data for UL 555S fire/smoke dampers is often figure 5.3 "
          "(ducted both sides). System effects from nearby elbows, transitions or other fittings are "
          "not included."),
        p("• <b>Standard-air test data.</b> Test pressure drops are assumed to be corrected to standard "
          "air. The density correction covers temperature and altitude, not humidity."),
        p("• <b>Simple geometry.</b> The geometry estimate assumes a rectangular frame, horizontal "
          "blades of constant projected thickness, equal sections, and a fixed other blockage per "
          "section. Calibrating to measured free areas absorbs most of the difference for real dampers."),
        p("• <b>Size model.</b> Between tested sizes, C<sub>free</sub> is assumed to follow the chosen "
          "shape (straight line or curve) in the free-area ratio. Estimates are most reliable inside "
          "the tested range of sizes and velocities; outside it, the curved shape is usually the safer "
          "of the two at small ratios."),
        PageBreak(),
        p("Appendix: symbols and units", H1),
        table([["Symbol", "Meaning", "Unit"],
               ["W, H", "Nominal damper width and height (overall)", "in"],
               [f"{sub('W','s')}, {sub('H','s')}", "Section width and height", "in"],
               [f"{sub('n','w')}, {sub('n','h')}", "Number of sections across the width and up the height", "–"],
               [f"{sub('f','w')}, {sub('f','h')}", "Frame obstruction on each side (left/right, top/bottom)", "in"],
               ["t", "Blade thickness facing the airflow", "in"],
               ["p", "Blade pitch (used only outside the blade table, on the open height)", "in"],
               ["N", "Blades per section", "–"],
               ["O", "Other blockage per section", "in²"],
               ["j", "Extra blockage width along each section joint", "in"],
               [f"{sub('A','face')}, {sub('A','free')}", "Face area and free area", "ft²"],
               ["R", f"Free-area ratio {sub('A','free')} / {sub('A','face')}", "–"],
               ["Q", "Airflow", "cfm"],
               [f"{sub('V','face')}, {sub('V','free')}", "Face velocity and velocity through the free area", "fpm"],
               ["ρ", "Air density (0.075 for standard air)", "lb/ft³"],
               ["VP", "Velocity pressure", "in. wg"],
               [f"{sub('C','free')}, {sub('C','face')}", "Loss coefficient based on free-area / face velocity", "–"],
               [f"{sub('C','model')}", "Size-model coefficient at a given R", "–"],
               ["a, b", "Fitted size-model constants", "–"],
               ["w", "Blend weight towards the nearest tested size (0 to 1)", "–"],
               ["n", "Fitted exponent of ΔP = k × V<super>n</super>", "–"],
               ["ΔP", "Pressure drop", "in. wg"]],
              [0.17, 0.68, 0.15]),
    ]
    return story


def on_page(canvas, doc):
    canvas.saveState()
    canvas.setFont("DejaVu", 7.5)
    canvas.setFillColor(GRAY)
    if doc.page > 1:
        canvas.drawString(0.9 * inch, letter[1] - 0.55 * inch,
                          "Damper Pressure Drop Estimator: calculation method")
    canvas.drawString(0.9 * inch, 0.5 * inch, f"Program version {__version__}")
    canvas.drawRightString(letter[0] - 0.9 * inch, 0.5 * inch, f"Page {doc.page}")
    canvas.restoreState()


def main(out=OUT):
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(str(out), pagesize=letter, leftMargin=0.9 * inch,
                            rightMargin=0.9 * inch, topMargin=0.8 * inch, bottomMargin=0.8 * inch,
                            title="How the Damper Pressure Drop Estimator Calculates Pressure Drop",
                            author="Damper Pressure Drop Estimator")
    doc.build(build(), onFirstPage=on_page, onLaterPages=on_page)
    print(f"Wrote {out}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else OUT)
