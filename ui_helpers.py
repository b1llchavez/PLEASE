import tkinter as tk
import math, random

import constants as C
from constants import (
    BG_COL, MEMBER_COLORS,
    BTN_COLORS, btn_fg,
    UI_FONT, MONO_FONT, TITLE_FONT,
    dim, blend, lighten, hex_to_rgb,
    _fs, IS_WINDOWS,
)


# ══════════════════════════════════════════════════════════════════
#  PIXEL BUTTON  (Canvas-drawn, animated press state)
# ══════════════════════════════════════════════════════════════════

def make_pixel_btn(parent, label: str, col: str, cmd,
                   root,
                   width: int = 160,
                   height: int = None) -> tk.Canvas:
    """
    Canvas-drawn button with 3-layer highlight/shadow and press animation.
    All dimensions are hardcoded — never derived from OS widget metrics —
    so the macOS visual layout is preserved exactly on Windows.
    """
    bw = width
    # Retains macOS button height; ignores OS default padding on Windows
    bh = height if height is not None else C.BTN_H

    HL = 4   # Top highlight strip height (pixels) — fixed, not OS-dependent
    SH = 4   # Bottom shadow strip height (pixels) — fixed

    # bg inherits parent colour so the Canvas blends into any background
    cv_btn = tk.Canvas(
        parent, width=bw, height=bh,
        bg=parent.cget("bg"),
        highlightthickness=0,   # Removes Windows default focus rectangle
        cursor="hand2",
    )

    pressed = [False]

    def _draw(p: bool = False):
        cv_btn.delete("all")
        ox, oy = (2, 2) if p else (0, 0)
        bright = lighten(col, 0.55)
        dark   = dim(col, 0.40)
        cv_btn.create_rectangle(ox, oy, bw + ox, bh + oy, fill=col, outline="")
        cv_btn.create_rectangle(ox, oy, bw + ox, oy + HL, fill=bright, outline="")
        cv_btn.create_rectangle(ox, bh + oy - SH, bw + ox, bh + oy, fill=dark, outline="")
        # DPI-adjusted font keeps label text the same visual size on Windows
        cv_btn.create_text(bw // 2 + ox, bh // 2 + oy,
                           text=label, fill="#04000C",
                           font=(UI_FONT, _fs(13), "bold"), anchor="center")

    def _press(e):
        pressed[0] = True
        _draw(True)

    def _release(e):
        pressed[0] = False
        _draw(False)
        play_click(root)
        root.after(40, cmd)

    def _leave(e):
        if pressed[0]:
            pressed[0] = False
            _draw(False)

    cv_btn.bind("<ButtonPress-1>",   _press)
    cv_btn.bind("<ButtonRelease-1>", _release)
    cv_btn.bind("<Leave>",           _leave)
    _draw()

    return cv_btn


# ══════════════════════════════════════════════════════════════════
#  STANDARD TK BUTTON  (legacy / simple use cases)
# ══════════════════════════════════════════════════════════════════

def make_btn(parent, text: str, bg: str, fg: str, cmd,
             root,
             width: int = None) -> tk.Button:
    """
    tk.Button styled to match the game's macOS appearance on Windows.
    relief="flat" and explicit padx/pady override OS-default button chrome.
    """
    try:
        ab = lighten(bg, 0.25)
    except Exception:
        ab = bg

    kw = {"width": width} if width else {}

    def _cmd_with_sfx():
        play_click(root)
        root.after(40, cmd)

    return tk.Button(
        parent, text=text, bg=bg, fg=fg,
        activebackground=ab, activeforeground=fg,
        # DPI-compensated font size matches macOS visual weight
        font=(UI_FONT, _fs(C.BTN_FONT_SIZE), "bold"),
        # Hardcoded relief and padding override Windows button defaults
        relief="flat",
        padx=18, pady=9,
        # Removes Windows focus rectangle that adds unwanted visual border
        highlightthickness=0,
        bd=0,
        cursor="hand2",
        command=_cmd_with_sfx,
        **kw,
    )


# ══════════════════════════════════════════════════════════════════
#  THEMED BUTTON ROW
# ══════════════════════════════════════════════════════════════════

def make_themed_btn_row(parent, items: list, root,
                        start_idx: int = 0) -> list:
    """
    Horizontal row of palette-cycling buttons.
    Delegates to make_btn() which already applies cross-platform overrides.
    """
    result = []
    for i, (label, cb) in enumerate(items):
        ci  = (start_idx + i) % len(BTN_COLORS)
        bg  = BTN_COLORS[ci]
        fg  = btn_fg(ci)
        btn = make_btn(parent, label, bg, fg, cb, root)
        btn.pack(side="left", padx=8)
        result.append(btn)
    return result


# ══════════════════════════════════════════════════════════════════
#  SECTION LABEL  (horizontal rule + label)
# ══════════════════════════════════════════════════════════════════

def make_section_label(parent, txt: str):
    """
    Section-divider row: label on left, 1-px rule to the right.
    Background explicitly set to prevent Windows grey window colour
    bleeding through the frame.
    """
    f = tk.Frame(parent, bg="#0a0018")
    f.pack(fill="x", pady=(6, 0))

    tk.Label(f, text=txt, bg="#0a0018", fg="#888888",
             # DPI-adjusted size keeps this readable at all Windows DPI settings
             font=(UI_FONT, _fs(9), "bold")).pack(side="left")

    # Explicit height=1 overrides the Windows minimum frame height of ~4 px
    tk.Frame(f, bg="#333333", height=1).pack(
        side="left", fill="x", expand=True, padx=(10, 0), pady=5)


# ══════════════════════════════════════════════════════════════════
#  ANIMATED TITLE COLOUR HELPERS
# ══════════════════════════════════════════════════════════════════

# Full palette used by the BGYO title colour cycle and all animated borders
_GLOW_COLORS  = ["#FFD700", "#FF3385", "#00E5FF", "#FF8800", "#CC44FF", "#00FF99"]
_TITLE_COLORS = ["#00E5FF", "#FFD700", "#FF3385", "#00FF99", "#FF8800", "#CC44FF"]


def get_title_cycle_color(t: float) -> str:
    """
    Derive the current animated colour for the top BGYO title layer.
    Cycles through _TITLE_COLORS at a gentle pace (full loop ~8 s).
    Pass self.t from the main loop so all animated elements stay in sync.
    """
    cols  = _TITLE_COLORS
    phase = (t * 0.38) % 1.0
    i0    = int(phase * len(cols)) % len(cols)
    i1    = (i0 + 1) % len(cols)
    frac  = (phase * len(cols)) - i0
    return blend(cols[i0], cols[i1], frac)


def _current_glow_color(t: float, speed: float = 0.35) -> str:
    """Return a smoothly blended border-glow colour for the given time."""
    cols  = _GLOW_COLORS
    phase = (t * speed) % 1.0
    n     = len(cols)
    i0    = int(phase * n) % n
    i1    = (i0 + 1) % n
    frac  = (phase * n) - i0
    return blend(cols[i0], cols[i1], frac)


# ══════════════════════════════════════════════════════════════════
#  FANCY TITLE  (neon "BGYO" + subtitle on Canvas)
# ══════════════════════════════════════════════════════════════════

def draw_fancy_title(parent, bg: str = BG_COL,
                     title_color: str = "#00E5FF") -> tk.Canvas:
    """
    Fixed-size Canvas title widget.  Width=480, height=110 are absolute
    pixel values — not derived from font metrics — so the layout is
    identical on Windows and macOS regardless of system font scaling.

    bg should be set to the parent widget's background colour so the Canvas
    is visually invisible — callers pass parent.cget("bg") to avoid a
    black box appearing behind the title text.

    Windows stipple fix: the old stipple="gray25" overlay composited against
    the OS window colour (white) producing a white grid.  All layers now use
    fully opaque blended colours — no stipple anywhere.

    title_color: animated top-layer colour from get_title_cycle_color(t).
    """
    title_cv = tk.Canvas(parent, width=480, height=110, bg=bg,
                         highlightthickness=0)
    title_cv.pack(pady=(12, 10))

    # ── "BGYO" — multi-layer neon shadow stack (tagged "title_shadow") ──
    title_cv.create_text(244, 40, text="BGYO", fill="#0000AA",
                         font=(TITLE_FONT, _fs(58), "bold"), anchor="center",
                         tags="title_shadow")
    title_cv.create_text(242, 42, text="BGYO", fill="#550033",
                         font=(TITLE_FONT, _fs(58), "bold"), anchor="center",
                         tags="title_shadow")
    title_cv.create_text(241, 41, text="BGYO", fill="#553300",
                         font=(TITLE_FONT, _fs(58), "bold"), anchor="center",
                         tags="title_shadow")
    # Gold base layer — always under the animated colour layer
    title_cv.create_text(240, 40, text="BGYO", fill="#FFD700",
                         font=(TITLE_FONT, _fs(58), "bold"), anchor="center",
                         tags="title_shadow")
    # Animated colour layer — tagged "title_anim" so callers can redraw it
    _top_col = blend("#FFD700", title_color, 0.60)
    title_cv.create_text(240, 40, text="BGYO", fill=_top_col,
                         font=(TITLE_FONT, _fs(58), "bold"), anchor="center",
                         tags="title_anim")

    # ── Subtitle ─────────────────────────────────────────────────────
    sub_font = (TITLE_FONT, _fs(18), "bold")
    title_cv.create_text(242, 93, text="READY FOR THE BEAT, ACE?",
                         fill="#110022", font=sub_font, anchor="center")
    title_cv.create_text(240, 91, text="READY FOR THE BEAT, ACE?",
                         fill="#FF3385", font=sub_font, anchor="center")
    title_cv.create_text(240, 90, text="READY FOR THE BEAT, ACE?",
                         fill="#FFE066", font=sub_font, anchor="center")

    return title_cv


# ══════════════════════════════════════════════════════════════════
#  NEON BORDER ANIMATION  (called every frame)
# ══════════════════════════════════════════════════════════════════

def update_neon_border(container: tk.Frame, t: float, speed: float = 0.35):
    """
    Animates the highlightbackground of a Frame through a colour cycle.
    highlightthickness=2 is explicit — Windows does not apply this by
    default for frames, so it must be re-stated on every config() call.

    speed: colour-cycle rate in full cycles per second (default 0.35).
    """
    if not container.winfo_exists():
        return

    glow_col = _current_glow_color(t, speed)
    # highlightthickness must be re-stated; Windows resets it to 0 on some themes
    container.config(highlightbackground=glow_col, highlightthickness=2)


# ══════════════════════════════════════════════════════════════════
#  UNIVERSAL WINDOW CENTRING
# ══════════════════════════════════════════════════════════════════

def center_window(win: tk.Toplevel, width: int, height: int):
    """
    Position a Toplevel at the absolute centre of the user's display.
    Derives screen dimensions at call time — adapts to any resolution.
    Must be called after update_idletasks() so winfo_screenwidth/height
    return accurate values on both Windows and macOS.
    """
    win.update_idletasks()
    sw = win.winfo_screenwidth()
    sh = win.winfo_screenheight()
    # Calculates screen center for dynamic window placement
    x  = (sw - width)  // 2
    y  = (sh - height) // 2
    win.geometry(f"{width}x{height}+{x}+{y}")


# ══════════════════════════════════════════════════════════════════
#  STARFIELD BACKGROUND
# ══════════════════════════════════════════════════════════════════

def draw_stars(cv, stars: list, t: float, w: int, h: int):
    """Renders the animated parallax starfield — no platform-specific code needed."""
    for star in stars:
        brightness = 0.35 + 0.55 * abs(math.sin(t * 0.9 + star["ph"]))
        sx = star["nx"] * w
        sy = star["ny"] * h
        r  = max(0.5, star["r"] * brightness * 0.85)
        col = dim("#FFFFFF", brightness)
        cv.create_oval(sx - r, sy - r, sx + r, sy + r, fill=col, outline="")


# ══════════════════════════════════════════════════════════════════
#  SCREEN TRANSITION OVERLAY
# ══════════════════════════════════════════════════════════════════

def draw_transition_overlay(cv, alpha: float, w: int, h: int):
    """Full-screen white fade overlay — pure Canvas drawing, no OS dependency."""
    if alpha <= 0.0:
        return
    a  = max(0.0, min(1.0, alpha))
    na = max(0, min(255, int(a * 255)))
    try:
        cv.create_rectangle(0, 0, w, h,
                            fill=f"#{na:02x}{na:02x}{na:02x}",
                            outline="")
    except Exception:
        pass


# ══════════════════════════════════════════════════════════════════
#  STAR5 POLYGON  (5-pointed star)
# ══════════════════════════════════════════════════════════════════

def draw_star5(cv, cx: float, cy: float, r: float, col: str):
    """5-pointed star polygon — pure Canvas geometry, no OS dependency."""
    pts = []
    for i in range(10):
        ang = (i * math.pi / 5) - math.pi / 2
        ri  = r if i % 2 == 0 else r * 0.42
        pts += [cx + math.cos(ang) * ri,
                cy + math.sin(ang) * ri]
    cv.create_polygon(pts, fill=col, outline="")


# ══════════════════════════════════════════════════════════════════
#  ELLIPSE GLOW  (concentric soft radial glow)
# ══════════════════════════════════════════════════════════════════

def draw_ellipse_glow(cv, cx: float, cy: float,
                      rx: float, ry: float,
                      hex_col: str,
                      layers: int = 6,
                      max_alpha: float = 0.5):
    """Soft glow via concentric filled ellipses — pure Canvas, no OS dependency."""
    for i in range(layers, 0, -1):
        cur_rx = rx * (i / layers)
        cur_ry = ry * (i / layers)
        factor = max_alpha * (1.0 - (i / layers))
        col    = blend(BG_COL, hex_col, factor)
        cv.create_oval(cx - cur_rx, cy - cur_ry,
                       cx + cur_rx, cy + cur_ry,
                       fill=col, outline="")


# ══════════════════════════════════════════════════════════════════
#  CLICK SOUND SYNTHESIS
# ══════════════════════════════════════════════════════════════════

_click_snd = None


def play_click(root):
    """
    Synthesised 880 Hz click sound via pygame mixer.
    No platform-specific path — pygame abstracts audio across OS.
    """
    global _click_snd
    try:
        import pygame
        import array

        if _click_snd is None:
            rate = 44100
            n    = int(rate * 0.055)
            buf  = array.array("h")
            for i in range(n):
                envelope = 1.0 - (i / n)
                v = int(envelope * 14000 * math.sin(2 * math.pi * 880 * i / rate))
                buf.extend([v, v])
            _click_snd = pygame.mixer.Sound(buffer=bytes(buf))
            _click_snd.set_volume(0.45)

        ch = pygame.mixer.find_channel(True)
        if ch:
            ch.play(_click_snd)

    except Exception:
        pass


# ══════════════════════════════════════════════════════════════════
#  CROSS-PLATFORM ENTRY WIDGET FACTORY
# ══════════════════════════════════════════════════════════════════

def make_entry(parent, textvariable, bg="#0c0022", fg="#FFFFFF",
               insert_bg="#FFD700", width=22, show="",
               highlight_color="#FFD700") -> tk.Entry:
    """
    Creates a tk.Entry with all OS-default styling explicitly overridden.
    On Windows, entries render with a sunken 3D border and a white
    background by default; relief="flat" and explicit colours suppress this.
    highlightthickness=2 and explicit highlightbackground/highlightcolor
    replace the Windows focus rectangle with the game's neon style.
    """
    kw = {}
    if show:
        kw["show"] = show

    return tk.Entry(
        parent,
        textvariable=textvariable,
        bg=bg, fg=fg,
        insertbackground=insert_bg,
        font=(UI_FONT, _fs(13), "bold"),
        # Flat relief removes the sunken 3D border Windows applies by default
        relief="flat",
        bd=0,
        width=width,
        # Explicit highlight colours replace Windows blue focus outline
        highlightthickness=2,
        highlightbackground="#334466",
        highlightcolor=highlight_color,
        **kw,
    )


# ══════════════════════════════════════════════════════════════════
#  CROSS-PLATFORM SCALE (SLIDER) FACTORY
# ══════════════════════════════════════════════════════════════════

def make_scale(parent, variable, from_=0.0, to=1.0, resolution=0.01,
               length=200, command=None,
               trough_color="#220044",
               fg="#FFD700") -> tk.Scale:
    """
    Creates a tk.Scale with hardcoded dimensions and colours.
    On Windows, the default Scale uses a grey trough and white slider
    that look nothing like the macOS dark-themed version.  Every
    visual property is set explicitly so neither platform relies on
    OS theme defaults.
    sliderlength and width are pixel values — fixed to match macOS.
    """
    kw = {}
    if command:
        kw["command"] = command

    return tk.Scale(
        parent,
        variable=variable,
        from_=from_, to=to, resolution=resolution,
        orient="horizontal",
        length=length,
        bg="#0a0018",
        fg=fg,
        troughcolor=trough_color,
        activebackground=fg,
        # Explicit pixel dimensions prevent Windows from using system-DPI defaults
        sliderlength=22,
        width=14,
        # Removes Windows focus rectangle around the slider
        highlightthickness=0,
        bd=0,
        **kw,
    )
