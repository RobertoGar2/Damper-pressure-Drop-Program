"""Airflow physics used by the damper pressure-drop model (IP units).

Pressure drop is modeled as

    dP = C_free * VP_free

where VP_free is the velocity pressure based on the velocity through the
damper's free area:

    VP = (rho / 0.075) * (V / 4005)^2      [in. wg, V in fpm]
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, fields

STD_AIR_DENSITY = 0.075   # lb/ft^3, standard air (70 F, sea level)
VP_CONSTANT = 4005.0      # fpm; V = 4005 * sqrt(VP) for standard air
IN2_PER_FT2 = 144.0


@dataclass
class DamperGeometry:
    """Frame and blade dimensions used to estimate a damper's free area.

    Blades are assumed to be horizontal (running across the width) and
    fully open, so each blade blocks a strip `blade_thickness_in` tall
    across the open width. The number of blades is the open height divided
    by the blade pitch, rounded up.
    """

    frame_obstruction_width_in: float = 1.5    # each side, left and right
    frame_obstruction_height_in: float = 1.5   # each side, top and bottom
    blade_thickness_in: float = 0.5            # thickness facing the airflow
    blade_pitch_in: float = 6.0                # blade spacing
    other_blockage_in2: float = 0.0            # linkage, jackshaft, stops

    @classmethod
    def from_dict(cls, data: dict) -> "DamperGeometry":
        """Build from a dict. Keys starting with '_' are treated as notes."""
        known = {f.name for f in fields(cls)}
        values = {}
        for key, value in data.items():
            if key.startswith("_"):
                continue
            if key not in known:
                raise ValueError(
                    f"Unknown geometry setting '{key}'. "
                    f"Valid settings: {', '.join(sorted(known))}"
                )
            try:
                values[key] = float(value)
            except (TypeError, ValueError):
                raise ValueError(f"Geometry setting '{key}' must be a number.") from None
        return cls(**values)

    def validate(self) -> None:
        for f in fields(self):
            if getattr(self, f.name) < 0:
                raise ValueError(f"Geometry setting '{f.name}' cannot be negative.")
        if self.blade_pitch_in <= 0:
            raise ValueError("Geometry setting 'blade_pitch_in' must be greater than zero.")


def face_area_ft2(width_in: float, height_in: float) -> float:
    """Nominal face area (W x H) in ft^2."""
    return width_in * height_in / IN2_PER_FT2


def blade_count(height_in: float, geometry: DamperGeometry) -> int:
    """Number of blades from the blade pitch: open height / pitch, rounded up.

    `height_in` is the nominal section height; the frame obstruction is taken
    off top and bottom first, because blades only fill the clear opening.
    (The blade table, by contrast, is looked up by the nominal section height,
    the way blade schedules are usually written.)
    """
    open_h = height_in - 2 * geometry.frame_obstruction_height_in
    if open_h <= 0:
        return 0
    return max(1, math.ceil(open_h / geometry.blade_pitch_in - 1e-9))


# ---------------------------------------------------------------------------
# Blade table
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class BladeRule:
    """Sections with a height from min to max (inclusive) have this many blades."""

    min_height_in: float
    max_height_in: float
    blades: int

    @property
    def label(self) -> str:
        return f"{self.min_height_in:g}-{self.max_height_in:g} in"


@dataclass
class BladeTable:
    """Blade count by section height. Empty means: use the blade pitch."""

    rules: list[BladeRule] = field(default_factory=list)

    def __post_init__(self):
        self.rules = sorted(self.rules, key=lambda r: (r.min_height_in, r.max_height_in))

    @property
    def is_empty(self) -> bool:
        return not self.rules

    def validate(self) -> None:
        for r in self.rules:
            if r.min_height_in <= 0 or r.max_height_in < r.min_height_in:
                raise ValueError(
                    f"Blade-table row {r.label}: heights must be above zero and the "
                    f"max height must be at least the min height."
                )
            if r.blades < 1 or r.blades != int(r.blades):
                raise ValueError(f"Blade-table row {r.label}: blades must be a whole number, 1 or more.")
        for a, b in zip(self.rules, self.rules[1:]):
            if b.min_height_in < a.max_height_in - 1e-9:
                raise ValueError(f"Blade-table rows {a.label} and {b.label} overlap.")

    def lookup(self, height_in: float) -> int | None:
        """Blade count for a section height, or None if no row covers it.

        A height on a boundary shared by two rows uses the lower row.
        """
        for r in self.rules:
            if r.min_height_in - 1e-6 <= height_in <= r.max_height_in + 1e-6:
                return r.blades
        return None

    def gap_around(self, height_in: float) -> tuple[BladeRule, BladeRule] | None:
        """The two rows a height falls between, if it is in a gap."""
        for a, b in zip(self.rules, self.rules[1:]):
            if a.max_height_in < height_in < b.min_height_in:
                return a, b
        return None


def section_blades(section_height_in: float, geometry: DamperGeometry,
                   table: BladeTable | None = None) -> tuple[int, str]:
    """Blades in one section and where that number came from.

    Uses the blade table when it covers the height. Heights above or below
    the table fall back to the blade pitch. A height in a gap between rows
    is an error, because the table is probably missing a row.
    """
    if table is not None and not table.is_empty:
        n = table.lookup(section_height_in)
        if n is not None:
            return n, "blade table"
        gap = table.gap_around(section_height_in)
        if gap is not None:
            a, b = gap
            raise ValueError(
                f"Section height {section_height_in:g} in falls in a gap in the blade table, "
                f"between the {a.label} and {b.label} rows. Make the rows meet, for example "
                f"{a.min_height_in:g}-{b.min_height_in - 0.01:g} and "
                f"{b.min_height_in:g}-{b.max_height_in:g}."
            )
        return blade_count(section_height_in, geometry), "blade pitch (height outside blade table)"
    return blade_count(section_height_in, geometry), "blade pitch"


# ---------------------------------------------------------------------------
# Size rules and multi-section layout
# ---------------------------------------------------------------------------

# Shapes the size model (C_free vs. free-area ratio R) can take:
#   linear: C = a + b * R
#   curved: C = a + b * (1/R - 1)^2, the shape of the classic loss coefficient of
#           a grille or perforated plate, which rises steeply as R gets small.
SIZE_MODEL_KINDS = ("linear", "curved")


@dataclass
class SizeRules:
    """Size limits for a damper model, and when to split it into sections.

    Any limit set to None means no limit. With no maximum section size, the
    damper is always a single section.
    """

    min_width_in: float | None = None
    min_height_in: float | None = None
    max_width_in: float | None = None          # overall assembly
    max_height_in: float | None = None
    max_section_width_in: float | None = None  # largest single section
    max_section_height_in: float | None = None
    joint_blockage_in: float = 0.0             # extra width blocked along each joint
    size_model: str = "linear"                 # shape of C_free vs. free-area ratio

    @classmethod
    def from_dict(cls, data: dict) -> "SizeRules":
        known = {f.name for f in fields(cls)}
        values = {}
        for key, value in data.items():
            if key.startswith("_"):
                continue
            if key not in known:
                raise ValueError(
                    f"Unknown size rule '{key}'. Valid rules: {', '.join(sorted(known))}"
                )
            if key == "size_model":
                values[key] = str(value or "linear").strip().lower() or "linear"
                continue
            if value is None or value == "":
                values[key] = 0.0 if key == "joint_blockage_in" else None
                continue
            try:
                values[key] = float(value)
            except (TypeError, ValueError):
                raise ValueError(f"Size rule '{key}' must be a number or null.") from None
        return cls(**values)

    def to_dict(self) -> dict:
        return {f.name: getattr(self, f.name) for f in fields(self)}

    def validate(self) -> None:
        if self.size_model not in SIZE_MODEL_KINDS:
            raise ValueError(
                f"Size rule 'size_model' must be one of: {', '.join(SIZE_MODEL_KINDS)} "
                f"(got '{self.size_model}')."
            )
        for f in fields(self):
            v = getattr(self, f.name)
            if f.name == "size_model":
                continue
            if f.name == "joint_blockage_in":
                if v < 0:
                    raise ValueError("Joint blockage cannot be negative.")
            elif v is not None and v <= 0:
                raise ValueError(f"Size rule '{f.name}' must be greater than zero (or blank).")
        for lo, hi, what in ((self.min_width_in, self.max_width_in, "width"),
                             (self.min_height_in, self.max_height_in, "height")):
            if lo is not None and hi is not None and lo > hi:
                raise ValueError(f"Minimum {what} is larger than the maximum {what}.")

    @property
    def multi_section(self) -> bool:
        return self.max_section_width_in is not None or self.max_section_height_in is not None

    def check(self, width_in: float, height_in: float) -> None:
        """Raise ValueError if the size is outside the limits."""
        problems = []
        if self.min_width_in is not None and width_in < self.min_width_in - 1e-9:
            problems.append(f"{width_in:g} in wide is below the minimum width of {self.min_width_in:g} in")
        if self.max_width_in is not None and width_in > self.max_width_in + 1e-9:
            problems.append(f"{width_in:g} in wide is above the maximum width of {self.max_width_in:g} in")
        if self.min_height_in is not None and height_in < self.min_height_in - 1e-9:
            problems.append(f"{height_in:g} in high is below the minimum height of {self.min_height_in:g} in")
        if self.max_height_in is not None and height_in > self.max_height_in + 1e-9:
            problems.append(f"{height_in:g} in high is above the maximum height of {self.max_height_in:g} in")
        if problems:
            raise ValueError("Size outside this model's limits: " + "; ".join(problems) + ".")

    def layout(self, width_in: float, height_in: float) -> tuple[int, int]:
        """Number of sections across the width and up the height.

        The fewest equal sections that keep each one within the maximum
        section size.
        """
        def count(size, max_section):
            if max_section is None:
                return 1
            return max(1, math.ceil(size / max_section - 1e-9))
        return count(width_in, self.max_section_width_in), count(height_in, self.max_section_height_in)

    def joint_area_in2(self, width_in, height_in, sections_wide, sections_high) -> float:
        """Extra area blocked along the joints between sections."""
        j = self.joint_blockage_in
        if j <= 0:
            return 0.0
        vertical = (sections_wide - 1) * height_in
        horizontal = (sections_high - 1) * width_in
        crossings = (sections_wide - 1) * (sections_high - 1) * j    # counted twice above
        return j * (vertical + horizontal) - j * crossings

    def describe(self) -> str:
        def rng(lo, hi):
            lo_s = f"{lo:g}" if lo is not None else "any"
            hi_s = f"{hi:g}" if hi is not None else "any"
            return f"{lo_s} to {hi_s} in"
        text = f"width {rng(self.min_width_in, self.max_width_in)}, " \
               f"height {rng(self.min_height_in, self.max_height_in)}"
        if self.multi_section:
            sw = f"{self.max_section_width_in:g}" if self.max_section_width_in else "any"
            sh = f"{self.max_section_height_in:g}" if self.max_section_height_in else "any"
            text += f"; max section {sw} x {sh} in"
            if self.joint_blockage_in:
                text += f", {self.joint_blockage_in:g} in extra at joints"
        else:
            text += "; single section only"
        return text


# ---------------------------------------------------------------------------
# Free area
# ---------------------------------------------------------------------------

@dataclass
class FreeAreaBreakdown:
    """Where the nominal face area goes: frame, blades, other, joints, free area.

    open_width_in, open_height_in and n_blades are per section; the areas
    are totals for the whole damper.
    """

    width_in: float
    height_in: float
    open_width_in: float
    open_height_in: float
    n_blades: int
    frame_block_in2: float
    blade_block_in2: float
    other_block_in2: float
    free_area_in2: float
    sections_wide: int = 1
    sections_high: int = 1
    blade_source: str = "blade pitch"
    joint_block_in2: float = 0.0

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
    def total_blades(self) -> int:
        return self.n_blades * self.n_sections

    @property
    def face_area_in2(self) -> float:
        return self.width_in * self.height_in

    @property
    def face_area_ft2(self) -> float:
        return self.face_area_in2 / IN2_PER_FT2

    @property
    def free_area_ft2(self) -> float:
        return self.free_area_in2 / IN2_PER_FT2

    @property
    def free_ratio(self) -> float:
        return self.free_area_in2 / self.face_area_in2

    @property
    def section_free_ratio(self) -> float:
        """Free-area ratio of one section on its own (without joint blockage)."""
        return (self.free_area_in2 + self.joint_block_in2) / self.face_area_in2


def free_area_breakdown(width_in: float, height_in: float, geometry: DamperGeometry,
                        rules: SizeRules | None = None,
                        blade_table: BladeTable | None = None) -> FreeAreaBreakdown:
    """Estimate the free area from the geometry, blade table and section layout.

    The damper is split into equal sections (see SizeRules.layout). Each
    section has its own frame, blades and other blockage.
    """
    if width_in <= 0 or height_in <= 0:
        raise ValueError("Width and height must be greater than zero.")
    rules = rules or SizeRules()
    nw, nh = rules.layout(width_in, height_in)
    sw, sh = width_in / nw, height_in / nh
    what = "section" if nw * nh > 1 else "damper"

    open_w = sw - 2 * geometry.frame_obstruction_width_in
    open_h = sh - 2 * geometry.frame_obstruction_height_in
    if open_w <= 0 or open_h <= 0:
        raise ValueError(
            f"The frame obstruction leaves no opening for a {sw:g} x {sh:g} {what}."
        )
    n, source = section_blades(sh, geometry, blade_table)
    blade_block = n * geometry.blade_thickness_in * open_w
    section_free = open_w * open_h - blade_block - geometry.other_blockage_in2
    if section_free <= 0:
        raise ValueError(
            f"Blades and other blockage cover the whole opening of a {sw:g} x {sh:g} {what}."
        )
    sections = nw * nh
    joint = rules.joint_area_in2(width_in, height_in, nw, nh)
    free = sections * section_free - joint
    if free <= 0:
        raise ValueError(f"Joint blockage covers the whole opening of a "
                         f"{width_in:g} x {height_in:g} damper.")
    return FreeAreaBreakdown(
        width_in=width_in, height_in=height_in,
        open_width_in=open_w, open_height_in=open_h, n_blades=n,
        frame_block_in2=sections * (sw * sh - open_w * open_h),
        blade_block_in2=sections * blade_block,
        other_block_in2=sections * geometry.other_blockage_in2,
        free_area_in2=free,
        sections_wide=nw, sections_high=nh, blade_source=source, joint_block_in2=joint,
    )


def free_area_ft2(width_in: float, height_in: float, geometry: DamperGeometry) -> float:
    """Single-section free area (ft^2) from the frame and blade geometry alone."""
    return free_area_breakdown(width_in, height_in, geometry).free_area_ft2


@dataclass
class DamperDesign:
    """Everything that sets a damper model's free area: geometry, blade
    table and size rules."""

    geometry: DamperGeometry = field(default_factory=DamperGeometry)
    blade_table: BladeTable = field(default_factory=BladeTable)
    size_rules: SizeRules = field(default_factory=SizeRules)

    def validate(self) -> None:
        self.geometry.validate()
        self.blade_table.validate()
        self.size_rules.validate()

    def breakdown(self, width_in: float, height_in: float,
                  check_limits: bool = False) -> FreeAreaBreakdown:
        if check_limits:
            self.size_rules.check(width_in, height_in)
        return free_area_breakdown(width_in, height_in, self.geometry,
                                   self.size_rules, self.blade_table)

    def free_area_ft2(self, width_in: float, height_in: float) -> float:
        return self.breakdown(width_in, height_in).free_area_ft2

    def with_geometry(self, geometry: DamperGeometry) -> "DamperDesign":
        return DamperDesign(geometry, self.blade_table, self.size_rules)


def air_density(temperature_f: float = 70.0, altitude_ft: float = 0.0) -> float:
    """Dry-air density (lb/ft^3) at a temperature and altitude.

    Uses the standard-atmosphere pressure ratio and the ideal-gas law,
    referenced to 0.075 lb/ft^3 at 70 F and sea level.
    """
    if temperature_f <= -459.67:
        raise ValueError("Temperature must be above absolute zero (-459.67 F).")
    base = 1.0 - 6.8754e-6 * altitude_ft
    if base <= 0 or altitude_ft < -2000:
        raise ValueError("Altitude must be between -2,000 ft and about 145,000 ft.")
    pressure_ratio = base ** 5.2559
    return STD_AIR_DENSITY * pressure_ratio * (529.67 / (temperature_f + 459.67))


def velocity_pressure(velocity_fpm, density: float = STD_AIR_DENSITY):
    """Velocity pressure in in. wg. Works with numbers or numpy arrays."""
    return (density / STD_AIR_DENSITY) * (velocity_fpm / VP_CONSTANT) ** 2
