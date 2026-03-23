"""
╔══════════════════════════════════════════════════════════════════╗
║   BGYO: THE LIGHT STAGE — ACES OF P-POP  v18.0                 ║
║   constants.py  ·  Shared constants, colour helpers, projection ║
╚══════════════════════════════════════════════════════════════════╝

PURPOSE & ROLE IN THE ARCHITECTURE
────────────────────────────────────
This is the single source of truth for every value that needs to be
shared across the codebase.  No other module defines game-wide
tuning values or colour helpers — they all import from here.

Dependency graph (constants.py sits at the root):

    constants.py
        ↑ imported by ALL other modules:
        │  audio_engine.py   — reads SONGS_DIR, HIT_DEPTH, MIN_BEAT_GAP,
        │                       MASTER_VOLUME, effective_music_volume()
        │  game_objects.py   — reads MEMBER_COLORS, BG_COL, W, H,
        │                       dim/blend/additive_blend/spotlight_col
        │  file_helpers.py   — reads SONGS_DIR, PREVIEW_DIR, COVERS_DIR
        │  settings_state.py — reads/writes MASTER_VOLUME, MUSIC_VOLUME,
        │                       SFX_VOLUME, SFX_INTENSITY, SFX_ENABLED
        │  database.py       — (no constants imports)
        └  bgyo_game.py      — reads almost everything

MUTABLE GLOBALS (the "global state bus")
──────────────────────────────────────────
MASTER_VOLUME, MUSIC_VOLUME, SFX_VOLUME, SFX_INTENSITY, SFX_ENABLED
are intentionally mutable module-level names.  settings_state.apply()
writes them whenever the player changes a slider, and audio_engine /
game_objects read them on every frame — giving real-time, zero-restart
volume and effect updates without circular imports between those modules.
"""

import os, math


# ══════════════════════════════════════════════════════════════════
#  RESOLUTION  &  FRAME RATE
# ══════════════════════════════════════════════════════════════════

# Base (windowed) resolution.  All layout math is expressed as
# fractions of these values so the UI scales correctly when
# BGYOGame._apply_fullscreen() switches to the native screen size.
# W and H start equal to BASE_W/BASE_H but are overridden at runtime
# when fullscreen mode is active.
BASE_W, BASE_H = 1080, 700   # design-time canvas size in pixels
FPS            = 60           # target frames per second for the main loop
W, H           = BASE_W, BASE_H  # runtime canvas size (may differ from BASE_* in fullscreen)


# ══════════════════════════════════════════════════════════════════
#  DIRECTORY PATHS
# ══════════════════════════════════════════════════════════════════

# _HERE resolves to the directory that contains constants.py itself,
# so all paths remain correct regardless of where the game is launched.
_HERE       = os.path.dirname(os.path.abspath(__file__))

# MP3 audio assets — full-length songs used during gameplay.
SONGS_DIR   = os.path.join(_HERE, "songs")

# Short ~30-second chorus clips for song-select screen previews.
# Stored separately so full gameplay tracks aren't loaded during browsing.
PREVIEW_DIR = os.path.join(SONGS_DIR, "preview")

# General image assets (logo, member photos, banners).
IMG_DIR     = os.path.join(_HERE, "images")

# Per-song album artwork thumbnails (PNG/JPG/WEBP).
# Displayed in the song-select carousel and the gameplay HUD header.
COVERS_DIR  = os.path.join(IMG_DIR, "covers")


# ══════════════════════════════════════════════════════════════════
#  BGYO MEMBER DATA
# ══════════════════════════════════════════════════════════════════

# The five BGYO members in stage order.
# Each index i is shared across MEMBER_NAMES, MEMBER_ROLES, and
# MEMBER_COLORS so game_objects.py and bgyo_game.py can always do:
#     name  = MEMBER_NAMES[i]
#     role  = MEMBER_ROLES[i]
#     color = MEMBER_COLORS[i]
MEMBER_NAMES  = ["GELO", "AKIRA", "JL", "MIKKI", "NATE"]
MEMBER_ROLES  = [
    "Leader · Center · Lead Dancer",   # Gelo
    "Lead Vocalist · Visual",           # Akira
    "Main Vocalist · Songwriter",       # JL
    "Main Dancer · Sub Vocalist",       # Mikki
    "Lead Dancer · Sub Vocalist",       # Nate
]

# Each member's signature colour — used for note lane tints, spotlight
# beam colours, HUD label colours, and the animated LED ticker on the
# home screen.
MEMBER_COLORS = ["#FFD700", "#FF3385", "#00E5FF", "#00FF99", "#FF8800"]

# The deep-space almost-black background.  Every additive colour blend
# starts from this base, which is why the spotlights look like light
# being *added* rather than paint being applied.
BG_COL        = "#04000C"


# ══════════════════════════════════════════════════════════════════
#  AVATAR PALETTE
# ══════════════════════════════════════════════════════════════════

# Colours available when a player creates an account.
# Shown as a colour-picker grid in the profile/account screen.
# The first five mirror MEMBER_COLORS so fans can pick their bias's colour.
AVATAR_COLORS = [
    "#FFD700",   # Gelo gold
    "#FF3385",   # Akira pink
    "#00E5FF",   # JL cyan
    "#00FF99",   # Mikki green
    "#FF8800",   # Nate orange
    "#CC44FF",   # purple
    "#FF4444",   # red
    "#44FFDD",   # teal
    "#FFFFFF",   # white
    "#888888",   # grey (guest default)
]


# ══════════════════════════════════════════════════════════════════
#  LANE CONFIGURATIONS
# ══════════════════════════════════════════════════════════════════

# Maps the selected number of lanes → keyboard keys + display labels.
# bgyo_game.py reads LANE_CONFIGS[cfg.num_lanes] at the start of each
# session to set up key bindings and lane rendering.
#
#   keys   : lowercase single-character strings, left→right order.
#            Matched against event.keysym.lower() in _on_key_down.
#   labels : uppercase strings shown inside lane markers at the hit bar.
#
# Home-row key groupings keep both hands near the centre for comfort:
#   3-lane: F  J  L   (left index, right index, right ring)
#   4-lane: F  J  K  L
#   5-lane: D  F  J  K  L  (adds left middle finger)
LANE_CONFIGS = {
    3: {"keys": list("fjl"),   "labels": ["F", "J", "L"]},
    4: {"keys": list("fjkl"),  "labels": ["F", "J", "K", "L"]},
    5: {"keys": list("dfjkl"), "labels": ["D", "F", "J", "K", "L"]},
}


# ══════════════════════════════════════════════════════════════════
#  DIFFICULTY PRESETS
# ══════════════════════════════════════════════════════════════════

# Each preset controls four independent axes of challenge:
#
#   speed    (float, depth-units/sec)
#            How fast notes travel from the spawn horizon to the
#            hit bar.  Higher = less reaction time.
#            Range in use: 0.30 (Easy) → 0.72 (ACE).
#
#   ival_min (float, seconds)
#            Minimum allowed gap between consecutive notes in the
#            beat-chart.  In audio_engine.build_beat_chart() only
#            40% of this value is enforced as a hard skip so the
#            chart stays song-accurate; the full value is used by
#            the random-mode fallback in bgyo_game._update_game().
#            Lower = denser note patterns.
#
#   ival_max (float, seconds)
#            Maximum gap between notes in random-mode fallback.
#            Keeps patterns from feeling too empty between beats.
#
#   hit      (float, depth-units)
#            Half-width of the "good hit" timing window around the
#            ideal HIT_DEPTH.  A note at depth d is hittable while
#            |d - HIT_DEPTH| ≤ hit.  Larger = more forgiving.
#
#   perf     (float, depth-units)
#            Half-width of the inner "perfect hit" sub-window.
#            Must be ≤ hit.  A hit inside this window counts as
#            PERFECT and awards bonus score / combo multiplier.
#
# v18 change: speeds raised and intervals tightened so each difficulty
# feels meaningfully different; Normal now challenges mid-skill players.
DIFFICULTY = {
    "Easy":   {"speed": 0.30, "ival_min": 0.90, "ival_max": 1.60, "hit": 0.16, "perf": 0.08},
    "Normal": {"speed": 0.42, "ival_min": 0.55, "ival_max": 1.00, "hit": 0.13, "perf": 0.060},
    "Hard":   {"speed": 0.56, "ival_min": 0.35, "ival_max": 0.65, "hit": 0.10, "perf": 0.045},
    "ACE":    {"speed": 0.72, "ival_min": 0.22, "ival_max": 0.42, "hit": 0.08, "perf": 0.032},
}


# ══════════════════════════════════════════════════════════════════
#  BEAT-CHART THINNING
# ══════════════════════════════════════════════════════════════════

# During librosa analysis in audio_engine.analyse_beats(), any two
# beat events closer than MIN_BEAT_GAP seconds are deduplicated so
# only the earlier one is kept.  This prevents impossible note clusters
# while still capturing the rhythmic density of P-Pop tracks
# (typical BPM range 120-150 → ~0.40-0.50 s between beats).
#
# 0.16 s ≈ 375 BPM — far faster than any P-Pop song, so this floor
# only removes genuine librosa double-detections, not real beats.
# The additional 30 ms dedup pass inside audio_engine catches the
# nearest-neighbour collisions that occur before thinning.
MIN_BEAT_GAP = 0.16   # seconds


# ══════════════════════════════════════════════════════════════════
#  GLOBAL RUNTIME AUDIO / VISUAL SETTINGS  (the "state bus")
# ══════════════════════════════════════════════════════════════════
#
# These are intentionally mutable module-level floats/bools.
# They act as a lightweight publish-subscribe bus:
#
#   WRITER : settings_state._Settings.apply()
#            Called whenever a player moves a slider or toggles SFX.
#            Writes new values here AND calls audio_engine.audio.set_volume().
#
#   READERS: audio_engine  — effective_music_volume() / effective_sfx_volume()
#            game_objects   — SFX_INTENSITY gates particle counts
#            bgyo_game      — SFX_ENABLED gates click/hit sound calls
#
# This pattern avoids circular imports:
#   settings_state → audio_engine  (OK — one direction only)
#   audio_engine   → constants     (OK — reads values, never writes)
#   game_objects   → constants     (OK — reads values, never writes)

MASTER_VOLUME  = 1.00   # Overall multiplier applied on top of MUSIC_VOLUME and SFX_VOLUME.
                         # Equivalent to a hardware master fader.
MUSIC_VOLUME   = 0.85   # BGM and gameplay track channel volume (0-1).
SFX_VOLUME     = 0.85   # Sound-effects / preview clip channel volume (0-1).
SFX_INTENSITY  = 1.00   # Scales visual feedback density: particle count, flash opacity,
                         # confetti bursts.  0.0 = minimal; 1.0 = full concert intensity.
SFX_ENABLED    = True   # Master on/off toggle for ALL sound effects.
                         # False suppresses click sounds and hit particles globally.


def effective_music_volume() -> float:
    """
    Return the final music channel volume after applying the master multiplier.

    Formula:   clamp(MASTER_VOLUME × MUSIC_VOLUME, 0.0, 1.0)

    Called by:
      • audio_engine.AudioPlayer.play()          — sets pygame music volume on game start
      • audio_engine.AudioPlayer.set_master_volume() — re-syncs after master slider change
      • settings_state._Settings.apply()         — propagates slider changes immediately
    """
    return max(0.0, min(1.0, MASTER_VOLUME * MUSIC_VOLUME))


def effective_sfx_volume() -> float:
    """
    Return the final SFX / preview channel volume after the master multiplier.

    Formula:   clamp(MASTER_VOLUME × SFX_VOLUME, 0.0, 1.0)

    Called by:
      • audio_engine.AudioPlayer.play_preview()  — sets Sound object volume
      • audio_engine.AudioPlayer.set_sfx_volume() — live SFX slider updates
    """
    return max(0.0, min(1.0, MASTER_VOLUME * SFX_VOLUME))


# ══════════════════════════════════════════════════════════════════
#  FONT CONSTANTS
# ══════════════════════════════════════════════════════════════════

# All UI text goes through one of these three font families so the
# look stays consistent even if a particular font is absent
# (tkinter silently falls back to its default sans-serif).
UI_FONT    = "Segoe UI"     # Body text, labels, button labels, HUD info.
                             # Fallback: Arial — both are clean, legible sans-serifs.
MONO_FONT  = "Consolas"     # Score values, combo counter, accuracy numbers.
                             # Monospaced spacing prevents digits jumping on every frame.
TITLE_FONT = "Arial Black"  # Game title and member name display only.
                             # Heavy weight for maximum stage-presence at large sizes.


# ══════════════════════════════════════════════════════════════════
#  BUTTON / UI THEME
# ══════════════════════════════════════════════════════════════════

# Ordered palette of button face colours, cycled in menu order.
# Index 0 = first button on a screen, index 1 = second, etc.
# Every screen that creates buttons iterates BTN_COLORS[i % len(BTN_COLORS)]
# so the colour order is predictable and visually balanced.
BTN_COLORS = [
    "#FFD700",   # 0 gold   — primary / confirm actions
    "#FF3385",   # 1 pink   — secondary / navigate-back actions
    "#00E5FF",   # 2 cyan   — register / alternate positive actions
    "#00FF99",   # 3 green  — play / start
    "#FF8800",   # 4 orange — guest / alternate
    "#CC44FF",   # 5 purple — exit / destructive actions
]

# Text colours chosen for readability against each button background.
# BG_COL-derived dark text works on bright buttons (gold, cyan, green).
# White works on saturated dark buttons (pink, orange, purple).
BTN_TEXT_DARK  = "#04000C"   # Near-black — same as BG_COL for visual harmony.
BTN_TEXT_LIGHT = "#FFFFFF"   # Pure white — maximum contrast on dark/saturated bg.

# Indices of BTN_COLORS entries that require dark text.
# Determined by perceptual luminance: gold (#FFD700 ≈ 77% L), cyan (#00E5FF ≈ 78% L),
# and green (#00FF99 ≈ 74% L) are bright enough that dark text is easier to read.
# Pink/orange/purple are below the threshold, so white text is used instead.
_DARK_TEXT_INDICES = {0, 2, 3}   # gold, cyan, green → use BTN_TEXT_DARK


def btn_fg(palette_index: int) -> str:
    """
    Return the correct foreground (text) colour for a button at the
    given position in the BTN_COLORS palette.

    Usage in bgyo_game.py:
        fill = btn_fg(i)   # i is the button's index in the menu list
        canvas.create_text(..., fill=fill)

    Internally checks whether palette_index is in the set of
    high-luminance colours that need dark text.
    """
    return BTN_TEXT_DARK if palette_index in _DARK_TEXT_INDICES else BTN_TEXT_LIGHT


# Standard button geometry — shared by all Canvas-drawn pixel buttons
# so every screen has identically-sized interactive targets.
BTN_H         = 46    # Button height in pixels (uniform across all screens).
BTN_RADIUS    = 10    # Corner radius for rounded-rectangle buttons (pixels).
                       # Note: tkinter Canvas doesn't support rounded rects natively;
                       # bgyo_game uses create_polygon with arc segments instead.
BTN_FONT_SIZE = 14    # Button label font size in points.

# Navigation arrow colours — used for left/right carousel and back arrows.
# Must contrast clearly against both BG_COL and any semi-transparent overlays.
ARROW_FG       = "#FFD700"   # Default arrow colour (gold = high visibility).
ARROW_HOVER_FG = "#FFFFFF"   # Arrow colour on mouse hover (white = maximum contrast).
ARROW_BG       = "#1A0A30"   # Subtle dark-purple pill background behind each arrow.
ARROW_BORDER   = "#FF3385"   # Arrow button border (pink = matches Akira's accent).


# ══════════════════════════════════════════════════════════════════
#  TRIVIA QUESTION BANK
# ══════════════════════════════════════════════════════════════════
#
# Each entry is a dict with three keys:
#   "q"    : str   — The question text displayed to the player.
#   "opts" : list  — Four answer option strings (always exactly 4).
#   "a"    : int   — Zero-based index of the correct option in "opts".
#
# Used exclusively by bgyo_game._show_trivia() and related helpers.
# The trivia engine shuffles this list before each session so
# questions appear in a random order every game.
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
#
# All colour math works in the "#RRGGBB" hex-string format that
# tkinter's Canvas widget expects.  Intermediate calculations use
# plain integers (0-255) for speed; _clamp() guards against overflow
# before the final hex encoding.
#
# Used by: game_objects.py (Spotlight), bgyo_game.py (HUD, notes,
# particles), and any module that needs animated colour transitions.

def _clamp(v: float) -> int:
    """Clamp a float to the integer range [0, 255] for RGB channel use."""
    return max(0, min(255, int(v)))


def hex_to_rgb(h: str) -> tuple:
    """
    Parse a '#RRGGBB' hex string into an (R, G, B) integer tuple.

    Example:
        hex_to_rgb("#FFD700")  →  (255, 215, 0)

    The leading '#' is stripped before slicing so both '#RRGGBB'
    and 'RRGGBB' forms are accepted.
    """
    h = h.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def rgb_to_hex(r: float, g: float, b: float) -> str:
    """
    Convert floating-point RGB channel values to a '#RRGGBB' string.
    Each channel is clamped to [0, 255] before formatting so callers
    can pass raw multiplication results without pre-clamping.

    Example:
        rgb_to_hex(255, 215, 0)   →  '#ffd700'
        rgb_to_hex(300, -5, 128)  →  '#ff0080'   (auto-clamped)
    """
    return f"#{_clamp(r):02x}{_clamp(g):02x}{_clamp(b):02x}"


def dim(col: str, alpha: float) -> str:
    """
    Darken a hex colour by a linear alpha factor (simulated transparency
    against the game's black background).

    Formula:  output = col × clamp(alpha, 0, 1)

    This is equivalent to compositing the colour over pure black at
    the given opacity, which is exactly what the dark BG_COL context
    needs.  Used everywhere for glow decay, pulse animations, and
    shadow text layers.

    Examples:
        dim("#FFD700", 0.5)  →  '#7f6b00'   (half-bright gold)
        dim("#FF3385", 0.0)  →  '#000000'   (fully transparent = black)
        dim("#FFFFFF", 1.0)  →  '#ffffff'   (unchanged)
    """
    alpha = max(0.0, min(1.0, float(alpha)))
    r, g, b = hex_to_rgb(col)
    return rgb_to_hex(r * alpha, g * alpha, b * alpha)


def blend(c1: str, c2: str, t: float) -> str:
    """
    Linear interpolation (lerp) between two hex colours.

    Formula:  output = c1 + (c2 - c1) × clamp(t, 0, 1)

    At t=0.0 the result equals c1; at t=1.0 it equals c2.
    Used for smooth colour transitions in spotlights, LED ticker,
    and note lane tint animations.

    Example:
        blend("#000000", "#FFD700", 0.5)  →  '#7f6b00'  (mid-gold)
    """
    t = max(0.0, min(1.0, float(t)))
    r1, g1, b1 = hex_to_rgb(c1)
    r2, g2, b2 = hex_to_rgb(c2)
    return rgb_to_hex(
        r1 + (r2 - r1) * t,
        g1 + (g2 - g1) * t,
        b1 + (b2 - b1) * t,
    )


def lighten(col: str, t: float) -> str:
    """
    Blend a colour toward white by factor t.
    Convenience wrapper around blend(col, "#ffffff", t).

    At t=0 the colour is unchanged; at t=1 it becomes pure white.
    Used for button highlight strips and text glow effects.

    Example:
        lighten("#FF3385", 0.55)  →  brighter pink (used for button tops)
    """
    return blend(col, "#ffffff", t)


def additive_blend(bg_col: str, light_col: str, intensity: float) -> str:
    """
    Simulate additive (screen) blending of a light colour onto a
    background — the visual model used for real stage lighting.

    Formula:  output_channel = bg_channel + light_channel × intensity

    Each channel is clamped after addition so over-bright areas clip
    to white rather than wrapping around.  This correctly models
    how light physically works: adding light only ever makes things
    brighter, never darker.

    Used by spotlight_col() and directly in game_objects.Spotlight.draw()
    for beam cores and floor-glow rays.

    Example (a dim gold light on the near-black background):
        additive_blend("#04000C", "#FFD700", 0.3)  →  subtle warm glow
    """
    br, bg_, bb = hex_to_rgb(bg_col)
    lr, lg, lb  = hex_to_rgb(light_col)
    return rgb_to_hex(
        _clamp(br + lr * intensity),
        _clamp(bg_ + lg * intensity),
        _clamp(bb + lb * intensity),
    )


def spotlight_col(beam_col: str, brightness: float) -> str:
    """
    Compute the rendered fill colour for a spotlight polygon layer.

    Equivalent to: additive_blend(BG_COL, beam_col, brightness)

    By always blending against BG_COL (not pure black) the spotlight
    layers pick up the subtle purple tint of the stage atmosphere,
    making the lighting feel warmer and more embedded in the scene.

    Called many times per frame by game_objects.Spotlight.draw() for
    the four overlapping beam-cone layers of each spotlight.
    """
    return additive_blend(BG_COL, beam_col, brightness)


# ══════════════════════════════════════════════════════════════════
#  3D PERSPECTIVE PROJECTION
# ══════════════════════════════════════════════════════════════════
#
# The rhythm track is rendered as a pseudo-3D perspective lane using
# a simple linear depth model.  Notes start at depth=0 (far horizon,
# near the vanishing point) and travel to depth=1 (close to camera,
# at the bottom of the screen).  The hit bar sits at HIT_DEPTH.
#
# COORDINATE SYSTEM
# ──────────────────
#   lx    : normalised horizontal lane position in [0, 1]
#            (0 = left edge of track, 1 = right edge)
#   depth : normalised depth in [0, 1]
#            (0 = vanishing point / far, 1 = closest / camera)
#
# PROJECTION GEOMETRY
# ────────────────────
# The track converges to a vanishing point at (VPX, VPY):
#   VPX = w × 0.50  → horizontally centred
#   VPY = h × 0.33  → one-third down from the top
#
# The track's near edge spans from x=SL to x=SL+SW at y=NEARY:
#   SW    = w × 0.74   (near width = 74% of screen width)
#   SL    = (w - SW)/2 (left edge of near track)
#   NEARY = h × 0.86   (near/bottom edge y-coordinate)
#
# HIT BAR POSITION
# ─────────────────
# The hit bar is rendered at y = h × 0.78.  HIT_DEPTH is the depth
# value at which a note's projected y-coordinate equals that screen-y:
#
#   hit_depth = (h×0.78 − VPY) / (NEARY − VPY)
#
# audio_engine.build_beat_chart() uses HIT_DEPTH in the formula:
#   travel_time = HIT_DEPTH / speed
#   spawn_t = beat_t − travel_time
# …so every note is spawned early enough to arrive at HIT_DEPTH
# exactly on its musical beat, at every difficulty speed.
#
# PROJECT FUNCTION
# ─────────────────
# project(lx, depth) → (px, py, scale)
#
#   persp = 0.22 + depth × 0.78
#       Linear perspective multiplier.  At depth=0 (horizon) persp=0.22,
#       making the track very narrow.  At depth=1 (near) persp=1.00.
#
#   px = VPX + (SL + lx×SW − VPX) × persp
#       Horizontal screen position: lerp from vanishing-point x toward
#       the full-width near position, scaled by perspective.
#
#   py = VPY + (NEARY − VPY) × depth
#       Vertical screen position: linear depth maps 0→VPY, 1→NEARY.
#       (Straight linear depth is sufficient for the shallow-angle
#       perspective this game uses.)
#
#   scale = 0.10 + depth × 0.90
#       Note size multiplier.  Far notes are drawn at 10% of their
#       base size; near notes reach 100%.  This reinforces the
#       perception of depth without true perspective scaling.
#
# _make_proj() is called once at module load (for BASE_W × BASE_H)
# and again by BGYOGame._apply_fullscreen() whenever the resolution
# changes, producing a new closure with updated geometry constants.

def _make_proj(w: int, h: int):
    """
    Build and return a projection closure for the given canvas size.

    Returns:
        project   — callable(lx, depth) → (px, py, scale)
        vpx, vpy  — vanishing point in screen pixels
        sw, sl    — near-edge track width and left offset in pixels
        neary     — y-coordinate of the near/bottom track edge
        hit_depth — depth value corresponding to the hit bar y-position
    """
    # Vanishing point — centre of screen horizontally, one-third down.
    vpx   = w * 0.50
    vpy   = h * 0.33

    # Near-edge track geometry.
    sw    = w * 0.74        # track width at the near (bottom) edge
    sl    = (w - sw) / 2   # x-coordinate of the left lane boundary at near edge

    # The near edge of the track (where depth=1 notes arrive).
    neary = h * 0.86

    # Depth at which the hit bar sits (y = h × 0.78).
    # Derived by inverting the py formula: depth = (py - vpy) / (neary - vpy)
    hit_depth = (h * 0.78 - vpy) / (neary - vpy)

    def project(lx: float, depth: float) -> tuple:
        """
        Map a note's (lane position, depth) to screen coordinates.

        Args:
            lx    : Normalised horizontal position [0, 1] across the track.
                    Compute for a given lane with lane_cx(lane, total_lanes).
            depth : Normalised depth [0, 1].  0 = far (horizon), 1 = near (camera).

        Returns:
            px    : Screen x-coordinate in pixels.
            py    : Screen y-coordinate in pixels.
            scale : Size multiplier for the note sprite at this depth.
        """
        persp = 0.22 + depth * 0.78     # perspective factor: narrow at horizon, wide near
        px    = vpx + (sl + lx * sw - vpx) * persp
        py    = vpy + (neary - vpy) * depth
        sc    = 0.10 + depth * 0.90     # size grows linearly from 10% (far) to 100% (near)
        return px, py, sc

    return project, vpx, vpy, sw, sl, neary, hit_depth


# Module-level projection constants — computed once for BASE resolution.
# bgyo_game._apply_fullscreen() calls _make_proj() again with the new
# screen dimensions and stores the result in module-level _project / _HIT_DEPTH.
project, VPX, VPY, SW, SL, NEARY, HIT_DEPTH = _make_proj(W, H)


def lane_cx(lane: int, total_lanes: int) -> float:
    """
    Return the normalised centre-x position [0, 1] for a given lane.

    Formula:  (lane + 0.5) / total_lanes

    This places each lane's centre at equal intervals across the track
    width.  Pass the result as the `lx` argument to project().

    Example (5-lane setup):
        lane_cx(0, 5)  →  0.10  (leftmost lane)
        lane_cx(2, 5)  →  0.50  (centre lane)
        lane_cx(4, 5)  →  0.90  (rightmost lane)
    """
    return (lane + 0.5) / total_lanes
