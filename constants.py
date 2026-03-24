import os, math, sys


# ══════════════════════════════════════════════════════════════════
#  PLATFORM DETECTION
# ══════════════════════════════════════════════════════════════════

# Used throughout UI code to apply Windows-specific overrides without
# altering any game-logic behaviour.
IS_WINDOWS = sys.platform.startswith("win")
IS_MAC     = sys.platform == "darwin"


# ══════════════════════════════════════════════════════════════════
#  RESOLUTION  &  FRAME RATE
# ══════════════════════════════════════════════════════════════════

BASE_W, BASE_H = 1080, 700
FPS            = 60
W, H           = BASE_W, BASE_H


# ══════════════════════════════════════════════════════════════════
#  DPI SCALING FACTOR
# ══════════════════════════════════════════════════════════════════

# Windows applies system DPI scaling that shrinks apparent widget sizes.
# Applying this multiplier to every font point size and fixed pixel
# dimension keeps the macOS-designed layout intact on High-DPI displays.
# On macOS (Retina), tkinter already handles scaling internally — no
# adjustment needed.  On non-HiDPI Windows (96 DPI, scale 100%) the
# factor is 1.0 so nothing changes.
if IS_WINDOWS:
    # Most modern Windows laptops default to 125 % (1.25×) or 150 % (1.5×).
    # We compensate in the opposite direction: if the OS doubles logical
    # pixels we must halve our declared sizes so the rendered result
    # matches the macOS baseline.  The factor is capped at 1.0 so we
    # never enlarge text beyond the Mac design.
    try:
        import ctypes
        _PROCESS_DPI_UNAWARE         = 0
        _PROCESS_SYSTEM_DPI_AWARE    = 1
        _PROCESS_PER_MONITOR_DPI_AWARE = 2
        # Tell Windows this process manages DPI itself; prevents automatic
        # bitmap-scaling that blurs the canvas and misplaces widgets.
        ctypes.windll.shcore.SetProcessDpiAwareness(_PROCESS_PER_MONITOR_DPI_AWARE)
        # Read the actual system DPI and derive the compensation factor.
        _dpi = ctypes.windll.user32.GetDpiForSystem()
        DPI_SCALE = 96.0 / max(_dpi, 96)   # 96 DPI = 100 % baseline
    except Exception:
        DPI_SCALE = 1.0
else:
    DPI_SCALE = 1.0


def _fs(pt: int) -> int:
    """
    Return a DPI-adjusted font size in points.
    Multiplies `pt` by DPI_SCALE and rounds to the nearest integer.
    On macOS DPI_SCALE == 1.0, so this is a no-op.
    On Windows with 125 % scaling, a 14 pt font becomes ~11 pt,
    matching the visual size the macOS Retina display produces.
    """
    return max(1, round(pt * DPI_SCALE))


# ══════════════════════════════════════════════════════════════════
#  DIRECTORY PATHS
# ══════════════════════════════════════════════════════════════════

_HERE       = os.path.dirname(os.path.abspath(__file__))
SONGS_DIR   = os.path.join(_HERE, "songs")
PREVIEW_DIR = os.path.join(SONGS_DIR, "preview")
IMG_DIR     = os.path.join(_HERE, "images")
COVERS_DIR  = os.path.join(IMG_DIR, "covers")


# ══════════════════════════════════════════════════════════════════
#  BGYO MEMBER DATA
# ══════════════════════════════════════════════════════════════════

MEMBER_NAMES  = ["GELO", "AKIRA", "JL", "MIKKI", "NATE"]
MEMBER_ROLES  = [
    "Leader · Center · Lead Dancer",
    "Lead Vocalist · Visual",
    "Main Vocalist · Songwriter",
    "Main Dancer · Sub Vocalist",
    "Lead Dancer · Sub Vocalist",
]
MEMBER_COLORS = ["#FFD700", "#FF3385", "#00E5FF", "#00FF99", "#FF8800"]
BG_COL        = "#04000C"


# ══════════════════════════════════════════════════════════════════
#  AVATAR PALETTE
# ══════════════════════════════════════════════════════════════════

AVATAR_COLORS = [
    "#FFD700", "#FF3385", "#00E5FF", "#00FF99", "#FF8800",
    "#CC44FF", "#FF4444", "#44FFDD", "#FFFFFF", "#888888",
]


# ══════════════════════════════════════════════════════════════════
#  LANE CONFIGURATIONS
# ══════════════════════════════════════════════════════════════════

LANE_CONFIGS = {
    3: {"keys": list("fjl"),   "labels": ["F", "J", "L"]},
    4: {"keys": list("fjkl"),  "labels": ["F", "J", "K", "L"]},
    5: {"keys": list("dfjkl"), "labels": ["D", "F", "J", "K", "L"]},
}


# ══════════════════════════════════════════════════════════════════
#  DIFFICULTY PRESETS
# ══════════════════════════════════════════════════════════════════

DIFFICULTY = {
    "Easy":   {"speed": 0.30, "ival_min": 0.90, "ival_max": 1.60, "hit": 0.16, "perf": 0.08},
    "Normal": {"speed": 0.42, "ival_min": 0.55, "ival_max": 1.00, "hit": 0.13, "perf": 0.060},
    "Hard":   {"speed": 0.56, "ival_min": 0.35, "ival_max": 0.65, "hit": 0.10, "perf": 0.045},
    "ACE":    {"speed": 0.72, "ival_min": 0.22, "ival_max": 0.42, "hit": 0.08, "perf": 0.032},
}


# ══════════════════════════════════════════════════════════════════
#  BEAT-CHART THINNING
# ══════════════════════════════════════════════════════════════════

MIN_BEAT_GAP = 0.16


# ══════════════════════════════════════════════════════════════════
#  GLOBAL RUNTIME AUDIO / VISUAL SETTINGS
# ══════════════════════════════════════════════════════════════════

MASTER_VOLUME  = 1.00
MUSIC_VOLUME   = 0.85
SFX_VOLUME     = 0.85
SFX_INTENSITY  = 1.00
SFX_ENABLED    = True


def effective_music_volume() -> float:
    return max(0.0, min(1.0, MASTER_VOLUME * MUSIC_VOLUME))


def effective_sfx_volume() -> float:
    return max(0.0, min(1.0, MASTER_VOLUME * SFX_VOLUME))


# ══════════════════════════════════════════════════════════════════
#  FONT CONSTANTS  (cross-platform)
# ══════════════════════════════════════════════════════════════════

# On Windows, "Segoe UI" and "Consolas" are built-in and render
# nearly identically to their macOS counterparts at the same point
# size when DPI_SCALE compensation is applied via _fs().
# "Arial Black" ships on both platforms and is the safest heavy-weight
# option for the title font without an external font file.
UI_FONT    = "Segoe UI"     # macOS falls back cleanly to its Helvetica Neue
MONO_FONT  = "Consolas"     # macOS falls back to Courier New — same metrics
TITLE_FONT = "Arial Black"  # Available on Windows and macOS; no fallback needed


# ══════════════════════════════════════════════════════════════════
#  BUTTON / UI THEME
# ══════════════════════════════════════════════════════════════════

BTN_COLORS = [
    "#FFD700",
    "#FF3385",
    "#00E5FF",
    "#00FF99",
    "#FF8800",
    "#CC44FF",
]

BTN_TEXT_DARK  = "#04000C"
BTN_TEXT_LIGHT = "#FFFFFF"
_DARK_TEXT_INDICES = {0, 2, 3}


def btn_fg(palette_index: int) -> str:
    return BTN_TEXT_DARK if palette_index in _DARK_TEXT_INDICES else BTN_TEXT_LIGHT


# ── Button geometry — hardcoded to macOS design dimensions ──────────
# Windows tk.Button defaults add extra internal padding and a raised
# border that makes buttons taller than intended.  Every button in
# this project is Canvas-drawn (make_pixel_btn) or explicitly styled
# (make_btn with relief="flat"), so these constants drive consistent
# geometry on both platforms without relying on OS defaults.
BTN_H         = 46    # Fixed height in pixels — not affected by DPI (Canvas-drawn)
BTN_RADIUS    = 10    # Corner radius (pixels)
BTN_FONT_SIZE = _fs(14)   # DPI-compensated so Windows renders the same visual size

ARROW_FG       = "#FFD700"
ARROW_HOVER_FG = "#FFFFFF"
ARROW_BG       = "#1A0A30"
ARROW_BORDER   = "#FF3385"


# ══════════════════════════════════════════════════════════════════
#  TRIVIA QUESTION BANK
# ══════════════════════════════════════════════════════════════════

TRIVIA = [
    {"q": "What does BGYO stand for?",
     "opts": ["Be Great, Young Ones",
              "Becoming the Change, Going Further, You & I, Originally Filipino",
              "Boys Growing, Young and Outstanding",
              "Bright, Gifted, Young Ones"],
     "a": 1},

    {"q": "Who is the leader and center of BGYO?",
     "opts": ["Akira Morishita", "Mikki Escueta", "Gelo Rivera", "Nate Porcalla"],
     "a": 2},

    {"q": "What was BGYO's debut single (January 29, 2021)?",
     "opts": ["The Baddest", "He's Into Her", "The Light", "Rocketman"],
     "a": 2},

    {"q": "BGYO was the first Filipino act to top which Billboard chart?",
     "opts": ["Hot 100", "Global 200", "Next Big Sound", "Pop Airplay"],
     "a": 2},

    {"q": "What is the name of BGYO's official fanbase?",
     "opts": ["Stars", "BGYOers", "Lights", "Aces"],
     "a": 3},

    {"q": "Which talent academy launched BGYO?",
     "opts": ["Star Hunt Academy", "ABS-CBN Academy",
              "Star Magic School", "Pop Academy PH"],
     "a": 0},

    {"q": "BGYO is known as the _____ of P-Pop.",
     "opts": ["Kings", "Aces", "Stars", "Legends"],
     "a": 1},

    {"q": "What is the name of BGYO's sister group?",
     "opts": ["KAIA", "BINI", "VXON", "ALAMAT"],
     "a": 1},

    {"q": "How many members does BGYO have?",
     "opts": ["4", "6", "5", "7"],
     "a": 2},

    {"q": "Which BGYO member co-wrote 'The Light'?",
     "opts": ["Gelo", "Akira", "JL", "Mikki"],
     "a": 2},

    {"q": "Which 2025 BGYO song is a fun anthem about being admired by women?",
     "opts": ["Headlines", "Divine", "All These Ladies", "Dance With Me"],
     "a": 2},

    {"q": "'Kulay' was the official theme for which event?",
     "opts": ["BGYO Debut Concert", "Miss Universe Philippines 2021",
              "He's Into Her Season 2", "Darna TV Series"],
     "a": 1},

    {"q": "Which BGYO song was included in The Lunar Codex time capsule?",
     "opts": ["The Light", "Kundiman", "The Baddest", "He's Into Her"],
     "a": 2},

    {"q": "'Patuloy Lang Ang Lipad' was the OST for which Philippine drama?",
     "opts": ["He's Into Her", "Darna", "FPJ's Batang Quiapo", "Voltes V: Legacy"],
     "a": 1},

    {"q": "What colour is most associated with BGYO's brand?",
     "opts": ["Blue", "Gold / Yellow", "Red", "Green"],
     "a": 1},

    {"q": "Which BGYO member made their acting debut in He's Into Her?",
     "opts": ["Akira only", "JL only", "Nate only", "All five members"],
     "a": 3},

    {"q": "BGYO's 2025 self-titled EP was released on which date?",
     "opts": ["January 29, 2025", "March 13, 2025",
              "June 1, 2025",     "December 25, 2024"],
     "a": 1},

    {"q": "Which song marked BGYO's first collaboration with a K-pop act?",
     "opts": ["Magnet", "Be Us", "The Baddest", "Live Vivid"],
     "a": 0},
]


# ══════════════════════════════════════════════════════════════════
#  COLOUR HELPERS
# ══════════════════════════════════════════════════════════════════

def _clamp(v: float) -> int:
    return max(0, min(255, int(v)))


def hex_to_rgb(h: str) -> tuple:
    h = h.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def rgb_to_hex(r: float, g: float, b: float) -> str:
    return f"#{_clamp(r):02x}{_clamp(g):02x}{_clamp(b):02x}"


def dim(col: str, alpha: float) -> str:
    alpha = max(0.0, min(1.0, float(alpha)))
    r, g, b = hex_to_rgb(col)
    return rgb_to_hex(r * alpha, g * alpha, b * alpha)


def blend(c1: str, c2: str, t: float) -> str:
    t = max(0.0, min(1.0, float(t)))
    r1, g1, b1 = hex_to_rgb(c1)
    r2, g2, b2 = hex_to_rgb(c2)
    return rgb_to_hex(
        r1 + (r2 - r1) * t,
        g1 + (g2 - g1) * t,
        b1 + (b2 - b1) * t,
    )


def lighten(col: str, t: float) -> str:
    return blend(col, "#ffffff", t)


def additive_blend(bg_col: str, light_col: str, intensity: float) -> str:
    br, bg_, bb = hex_to_rgb(bg_col)
    lr, lg, lb  = hex_to_rgb(light_col)
    return rgb_to_hex(
        _clamp(br + lr * intensity),
        _clamp(bg_ + lg * intensity),
        _clamp(bb + lb * intensity),
    )


def spotlight_col(beam_col: str, brightness: float) -> str:
    return additive_blend(BG_COL, beam_col, brightness)


# ══════════════════════════════════════════════════════════════════
#  3D PERSPECTIVE PROJECTION
# ══════════════════════════════════════════════════════════════════

def _make_proj(w: int, h: int):
    vpx   = w * 0.50
    vpy   = h * 0.33
    sw    = w * 0.74
    sl    = (w - sw) / 2
    neary = h * 0.86
    hit_depth = (h * 0.78 - vpy) / (neary - vpy)

    def project(lx: float, depth: float) -> tuple:
        persp = 0.22 + depth * 0.78
        px    = vpx + (sl + lx * sw - vpx) * persp
        py    = vpy + (neary - vpy) * depth
        sc    = 0.10 + depth * 0.90
        return px, py, sc

    return project, vpx, vpy, sw, sl, neary, hit_depth


project, VPX, VPY, SW, SL, NEARY, HIT_DEPTH = _make_proj(W, H)


def lane_cx(lane: int, total_lanes: int) -> float:
    return (lane + 0.5) / total_lanes
