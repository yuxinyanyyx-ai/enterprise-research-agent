"""Layout and parsing constants aligned with Example.pptx."""

from __future__ import annotations

# Slide 3 milestone rows (column A)
MILESTONE_ORDER = [
    "Pre-IND",
    "CTA Sub",
    "1st SIV",
    "FPI",
    "LPI",
    "Key results",
    "NDA sub.",
    "NDA app.",
]

MILESTONE_ALIASES = {
    "Pre-IND": ["Pre-IND", "Pre IND"],
    "CTA Sub": ["CTA Sub", "CTA-s", "CTA s", "CTA-S"],
    "1st SIV": ["1st SIV", "First SIV"],
    "FPI": ["FPI"],
    "LPI": ["LPI"],
    "Key results": ["Key results", "Key Results"],
    "NDA sub.": ["NDA sub.", "NDA-s", "NDA s", "NDA-S"],
    "NDA app.": ["NDA app.", "NDA-a", "NDA a", "NDA-A"],
}

TIMELINE_LABEL_ALIASES = MILESTONE_ALIASES

COL_PAIR_MAX_DIST = 2_000_000
DEFAULT_TRACK_THRESHOLD = 2_700_000

# Slide 3 column indices (0-based): Global date/note, China date/note
GLOBAL_DATE_COL = 3
GLOBAL_NOTE_COL = 4
CHINA_DATE_COL = 7
CHINA_NOTE_COL = 8

# Slide 1 layout (EMU) — from Example.pptx measurements
SLIDE_WIDTH = 12_192_000
SLIDE_HEIGHT = 6_858_000

TITLE_LEFT, TITLE_TOP = 156_850, 147_417
TITLE_WIDTH, TITLE_HEIGHT = 11_233_151, 720_000

GRID_LEFT, GRID_TOP = 1_313_127, 815_105
GRID_WIDTH, GRID_HEIGHT = 10_658_940, 675_640
GRID_COLS = 20

UPPER_STUDY_LEFT, UPPER_STUDY_TOP = 59_378, 1_772_462
LOWER_STUDY_LEFT, LOWER_STUDY_TOP = 61_295, 3_335_466
STUDY_WIDTH, STUDY_HEIGHT = 825_189, 328_423

UPPER_TRACK_Y = 1_900_000
LOWER_TRACK_Y = 3_150_000
TRACK_LINE_LEFT = 1_200_000
TRACK_LINE_WIDTH = 10_700_000

# Vertical layout relative to track line (date above, label/note below — avoids line overlap)
DATE_ABOVE_LINE = 280_000
LABEL_BELOW_LINE = 220_000
NOTE_BELOW_LINE = 500_000

DATE_BOX_WIDTH = 910_000
DATE_BOX_HEIGHT = 164_000
LABEL_BOX_WIDTH = 850_000
LABEL_BOX_HEIGHT = 256_000
NOTE_BOX_HEIGHT = 220_000
MIN_CLUSTER_GAP = 400_000
MIN_TRACK_GAP = 120_000
MIN_SEGMENT_WIDTH = 200_000
SEGMENT_END_MARGIN = 80_000

STATUS_TABLE_LEFT, STATUS_TABLE_TOP = 302_019, 3_959_554
STATUS_TABLE_WIDTH, STATUS_TABLE_HEIGHT = 11_233_150, 1_371_600

RISK_TABLE_LEFT, RISK_TABLE_TOP = 336_592, 5_571_437
RISK_TABLE_WIDTH, RISK_TABLE_HEIGHT = 11_232_000, 1_554_480

FONT_NAME = "Calibri"
TITLE_FONT_SIZE = 14
BODY_FONT_SIZE = 10
DATE_FONT_SIZE = 10
NOTE_FONT_SIZE = 8

# OTL star markers on the track line (from Example.pptx)
UPPER_STAR_Y = 2_017_656
LOWER_STAR_Y = 3_298_340

# Milestone marker shapes (Office Timeline naming vs visual role)
SYMBOL_TRIANGLE = "triangle"  # OTLSHAPE_M small triangle — Pre-IND, CTA Sub
SYMBOL_DIAMOND = "diamond"  # Star: 4 Points — FPI/LPI/Key results/1st SIV
SYMBOL_STAR = "star"  # 五角星 — NDA sub.
SYMBOL_FLAG = "flag"  # Graphic picture — NDA app.

MILESTONE_SYMBOL_KIND: dict[str, str] = {
    "Pre-IND": SYMBOL_TRIANGLE,
    "CTA Sub": SYMBOL_TRIANGLE,
    "NDA sub.": SYMBOL_STAR,
    "NDA app.": SYMBOL_FLAG,
}

QUARTER_YEARS = ["2025", "", "", "", "2026", "", "", "", "2027", "", "", "", "2028", "", "", "", "2029", "", "2029", ""]
QUARTER_LABELS = [
    "Q1", "Q2", "Q3", "Q4",
    "Q1", "Q2", "Q3", "Q4",
    "Q1", "Q2", "Q3", "Q4",
    "Q1", "Q2", "Q3", "Q4",
    "H1", "H2", "H1", "H2",
]
