import tkinter as tk
import math, random

import constants as C
from constants import (
    BG_COL, MEMBER_COLORS,
    BTN_COLORS, btn_fg,
    UI_FONT, MONO_FONT, TITLE_FONT,
    dim, blend, lighten, hex_to_rgb,
)


# ══════════════════════════════════════════════════════════════════
#  PIXEL BUTTON  (Canvas-drawn, animated press state)
# ══════════════════════════════════════════════════════════════════

def make_pixel_btn(parent, label: str, col: str, cmd,
                   root,
                   width: int = 160,
                   height: int = None) -> tk.Canvas:
    """
    Create a Canvas-drawn "pixel-style" button with a 3-layer
    highlight/shadow appearance and an animated press-down state.

    This is the primary button style used throughout every screen
    in the game.  It mimics the look of a backlit hardware arcade
    button: a flat coloured face with a bright highlight strip at
    the top and a dark shadow strip at the bottom.

    ── VISUAL ANATOMY ──────────────────────────────────────────────

        ┌──────────────────────────────┐  ← bright strip (lighten × 0.55)
        │                              │
        │         LABEL TEXT           │  ← col face (BG_COL text)
        │                              │
        └──────────────────────────────┘  ← dark strip (dim × 0.40)

    Press state: the entire drawing shifts (ox=2, oy=2) to simulate
    a physical key-down depression.  Released/unpressed: (ox=0, oy=0).

    ── INTERACTION MODEL ───────────────────────────────────────────
    Three event bindings handle the full interaction cycle:
      <ButtonPress-1>   → set pressed=True, redraw shifted
      <ButtonRelease-1> → set pressed=False, redraw unshifted,
                          play click sound, schedule cmd via after(40)
      <Leave>           → if still pressed (drag-off), reset to unshifted

    cmd is called via root.after(40) (40 ms delay) so the press
    animation is briefly visible before the screen transitions away.

    ── AUDIO ───────────────────────────────────────────────────────
    A click sound is played on ButtonRelease via play_click(root).
    This keeps the click synthesis self-contained in ui_helpers and
    removes the dependency on BGYOGame._play_click() for every screen.

    Args:
        parent  — tkinter parent widget (Frame, Canvas, etc.).
        label   — Button label text (e.g. "▶  LOGIN").
        col     — Button face hex colour (e.g. BTN_COLORS[0] = "#FFD700").
        cmd     — Zero-argument callable to invoke on button release.
        root    — tk.Tk root window; needed for after() scheduling.
        width   — Button width in pixels (default 160).
        height  — Button height in pixels (default C.BTN_H = 46).

    Returns:
        A configured tk.Canvas widget.  The caller packs/places it.
    """
    bw = width
    bh = height if height is not None else C.BTN_H

    # Top highlight strip height and bottom shadow strip height (pixels).
    # 4 px gives the button visible 3D depth without looking chunky.
    HL = 4
    SH = 4

    # Create the Canvas with a transparent background matching the parent
    cv_btn = tk.Canvas(
        parent, width=bw, height=bh,
        bg=parent.cget("bg"),   # inherit parent background for transparency
        highlightthickness=0,   # no default border around the canvas
        cursor="hand2"          # pointer cursor signals interactivity
    )

    # Mutable press state stored in a list so the nested closures can
    # write to it without the Python 2-era 'nonlocal' restriction.
    pressed = [False]

    def _draw(p: bool = False):
        """Redraw the button face.  p=True draws the shifted press state."""
        cv_btn.delete("all")
        # Press offset: shift everything 2 px right+down when pressed
        ox, oy = (2, 2) if p else (0, 0)
        bright = lighten(col, 0.55)   # top highlight: col blended 55% toward white
        dark   = dim(col, 0.40)       # bottom shadow: col dimmed to 40% brightness

        # Main face rectangle
        cv_btn.create_rectangle(ox, oy, bw + ox, bh + oy,
                                fill=col, outline="")
        # Top highlight strip (HL px tall)
        cv_btn.create_rectangle(ox, oy, bw + ox, oy + HL,
                                fill=bright, outline="")
        # Bottom shadow strip (SH px tall)
        cv_btn.create_rectangle(ox, bh + oy - SH, bw + ox, bh + oy,
                                fill=dark, outline="")
        # Centred label — always dark text (#04000C) for maximum contrast
        # on the bright button colours used in BTN_COLORS.
        cv_btn.create_text(bw // 2 + ox, bh // 2 + oy,
                           text=label, fill="#04000C",
                           font=(UI_FONT, 13, "bold"), anchor="center")

    def _press(e):
        pressed[0] = True
        _draw(True)

    def _release(e):
        pressed[0] = False
        _draw(False)
        play_click(root)           # synthesise click sound on release
        root.after(40, cmd)        # brief delay so press animation is visible

    def _leave(e):
        # If the mouse is dragged off the button while held, reset
        # the visual state without invoking the command.
        if pressed[0]:
            pressed[0] = False
            _draw(False)

    cv_btn.bind("<ButtonPress-1>",   _press)
    cv_btn.bind("<ButtonRelease-1>", _release)
    cv_btn.bind("<Leave>",           _leave)
    _draw()   # initial unpressed draw

    return cv_btn


# ══════════════════════════════════════════════════════════════════
#  STANDARD TK BUTTON  (legacy / simple use cases)
# ══════════════════════════════════════════════════════════════════

def make_btn(parent, text: str, bg: str, fg: str, cmd,
             root,
             width: int = None) -> tk.Button:
    """
    Create a standard tk.Button styled with the game's colour scheme.

    Used as a simpler alternative to make_pixel_btn() in contexts where
    the overhead of a Canvas is undesirable (e.g. inside tk.Text or
    very narrow layout areas).

    The command is wrapped to play a click sound 40 ms before executing,
    matching the interaction feel of make_pixel_btn().

    Args:
        parent — Parent widget.
        text   — Button label.
        bg     — Background colour (hex string).
        fg     — Foreground / text colour (hex string).
        cmd    — Command to invoke on click.
        root   — tk.Tk root window for after() scheduling.
        width  — Optional width in character units (tk.Button default).

    Returns:
        A configured tk.Button widget.  The caller packs/places it.
    """
    # Lighten bg slightly for the activebackground hover state
    try:
        ab = lighten(bg, 0.25)
    except Exception:
        ab = bg   # graceful fallback if bg is not a valid hex string

    kw = {"width": width} if width else {}

    def _cmd_with_sfx():
        play_click(root)
        root.after(40, cmd)

    return tk.Button(
        parent, text=text, bg=bg, fg=fg,
        activebackground=ab, activeforeground=fg,
        font=(UI_FONT, C.BTN_FONT_SIZE, "bold"),
        relief="flat", padx=18, pady=9,
        cursor="hand2", command=_cmd_with_sfx,
        **kw,
    )


# ══════════════════════════════════════════════════════════════════
#  THEMED BUTTON ROW
# ══════════════════════════════════════════════════════════════════

def make_themed_btn_row(parent, items: list, root,
                        start_idx: int = 0) -> list:
    """
    Pack a horizontal row of colorful-rectangle buttons that cycle
    through BTN_COLORS in order.

    Each button in `items` is drawn with the next colour in the
    BTN_COLORS palette (wrapping cyclically), and the foreground
    colour is selected automatically via btn_fg() to ensure contrast.

    Args:
        parent     — Parent Frame into which buttons are packed.
        items      — List of (label: str, callback: callable) tuples.
                     One make_btn() is created per item.
        root       — tk.Tk root for click-sound scheduling.
        start_idx  — BTN_COLORS index to use for the first button
                     (default 0 = gold).  Allows multi-row screens to
                     continue the palette from where the previous row
                     left off.

    Returns:
        List of tk.Button widgets in left-to-right order.

    Example (settings screen):
        btns = make_themed_btn_row(frame, [
            ("SAVE & CLOSE", on_save),
            ("CANCEL",       on_cancel),
        ], root, start_idx=0)
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
    Add a section-divider row to `parent`: a short label on the left
    with a 1-px horizontal rule extending to the right edge.

    Used in the settings screen to visually group related controls
    (e.g. "AUDIO", "GAMEPLAY", "ACCOUNT").

    Layout:
        [ AUDIO ─────────────────────────────── ]

    The Frame packs fill="x" so the rule always reaches the right
    edge regardless of the parent's width.

    Args:
        parent — Parent Frame to pack the divider row into.
        txt    — Label string (e.g. "AUDIO", "GAMEPLAY").
    """
    f = tk.Frame(parent, bg="#0a0018")
    f.pack(fill="x", pady=(6, 0))

    # Left-aligned label in grey (#888888) — subdued so it doesn't
    # compete with the interactive controls below it.
    tk.Label(f, text=txt, bg="#0a0018", fg="#888888",
             font=(UI_FONT, 9, "bold")).pack(side="left")

    # The rule is a 1-px-tall Frame that expands to fill remaining width.
    # padx=(10, 0) leaves a small gap between the label and the rule.
    tk.Frame(f, bg="#333333", height=1).pack(
        side="left", fill="x", expand=True, padx=(10, 0), pady=5)


# ══════════════════════════════════════════════════════════════════
#  FANCY TITLE  (neon "BGYO" + subtitle on Canvas)
# ══════════════════════════════════════════════════════════════════

def draw_fancy_title(parent, bg: str = BG_COL):
    """
    Render the animated neon "BGYO" game title and subtitle onto a
    Canvas widget packed into `parent`.

    Used on the login and register screens as the unified header that
    sits above the form fields inside the single container frame.

    ── RENDERING TECHNIQUE ─────────────────────────────────────────
    Both the title and subtitle use a multi-layer shadow stack drawn
    back-to-front on the same Canvas.  Each layer is offset by 1-2 px
    and uses a different colour, creating the illusion of coloured
    neon light bleeding around sharp dark type:

    "BGYO" layers (back → front):
        1. Dark blue  (#0000AA) at (244, 40)  — deep shadow
        2. Dark red   (#550033) at (242, 42)  — warm glow undertone
        3. Dark gold  (#553300) at (241, 41)  — amber reflection
        4. Gold       (#FFD700) at (240, 40)  — primary face colour
        5. Cyan       (#00E5FF) at (240, 40)  — neon highlight with
           stipple="gray25" — only 25% of pixels drawn, creating a
           semi-transparent overlay without true alpha support in tkinter.

    "READY FOR THE BEAT, ACE?" layers (back → front):
        1. Near-black (#110022) at (242, 93)  — deep drop shadow
        2. Pink       (#FF3385) at (240, 91)  — neon glow
        3. Pale gold  (#FFE066) at (240, 90)  — warm face colour

    Args:
        parent — Parent widget to pack the title Canvas into.
        bg     — Canvas background colour (should match parent bg
                 so the Canvas blends seamlessly; defaults to BG_COL).

    Returns:
        Nothing.  The Canvas is packed directly into parent.
    """
    title_cv = tk.Canvas(parent, width=480, height=110, bg=bg,
                         highlightthickness=0)
    title_cv.pack(pady=(12, 10))

    # ── "BGYO" — five-layer neon shadow stack ─────────────────────
    # Drawn back-to-front so each layer composites on top of the last.
    title_cv.create_text(244, 40, text="BGYO",
                         fill="#0000AA",
                         font=(TITLE_FONT, 58, "bold"), anchor="center")
    title_cv.create_text(242, 42, text="BGYO",
                         fill="#550033",
                         font=(TITLE_FONT, 58, "bold"), anchor="center")
    title_cv.create_text(241, 41, text="BGYO",
                         fill="#553300",
                         font=(TITLE_FONT, 58, "bold"), anchor="center")
    title_cv.create_text(240, 40, text="BGYO",
                         fill="#FFD700",
                         font=(TITLE_FONT, 58, "bold"), anchor="center")
    # Cyan overlay drawn with stipple="gray25" — every other pixel is
    # transparent, producing a 25%-density hatching effect that simulates
    # a translucent neon sheen without true alpha compositing.
    title_cv.create_text(240, 40, text="BGYO",
                         fill="#00E5FF",
                         font=(TITLE_FONT, 58, "bold"),
                         anchor="center", stipple="gray25")

    # ── "READY FOR THE BEAT, ACE?" — three-layer subtitle ─────────
    sub_font = (TITLE_FONT, 18, "bold")
    title_cv.create_text(242, 93, text="READY FOR THE BEAT, ACE?",
                         fill="#110022", font=sub_font, anchor="center")
    title_cv.create_text(240, 91, text="READY FOR THE BEAT, ACE?",
                         fill="#FF3385", font=sub_font, anchor="center")
    title_cv.create_text(240, 90, text="READY FOR THE BEAT, ACE?",
                         fill="#FFE066", font=sub_font, anchor="center")


# ══════════════════════════════════════════════════════════════════
#  NEON BORDER ANIMATION  (called every frame)
# ══════════════════════════════════════════════════════════════════

def update_neon_border(container: tk.Frame, t: float):
    """
    Animate the highlightbackground colour of a tk.Frame to create a
    smoothly cycling neon glow border effect.

    Called every frame from the main game loop (bgyo_game._loop()) when
    the login or register screen is active.  The border colour lerps
    continuously through a four-colour palette — gold → pink → cyan →
    orange — completing one full cycle every ~2.9 seconds
    (1 / 0.35 Hz ≈ 2.86 s).

    ── HOW IT WORKS ────────────────────────────────────────────────
    1. A phase value in [0, 1) is computed from the game time:
           phase = (t × 0.35) % 1.0
    2. The phase is mapped to a palette index pair (idx0, idx1) and a
       fractional blend factor:
           idx0  = int(phase × len(palette)) % len(palette)
           idx1  = (idx0 + 1) % len(palette)
           frac  = fractional part of (phase × len(palette))
    3. The border colour is the linear interpolation between palette[idx0]
       and palette[idx1] at the given frac using constants.blend().
    4. container.config(highlightbackground=colour, highlightthickness=2)
       applies the new colour via tkinter's built-in highlight border.

    Note: highlightthickness must be ≥ 1 for highlightbackground to
    be visible.  It is set to 2 here for a moderately thick glow ring.

    Args:
        container — The tk.Frame whose border will be animated.
                    Must have been created with highlightthickness≥1.
        t         — Current game time in seconds (from bgyo_game.t).

    Called by:
        bgyo_game._loop() when self.screen in ("login", "register")
        and self._login_container is not None.
    """
    if not container.winfo_exists():
        return   # widget may have been destroyed during a screen transition

    glow_colors = ["#FFD700", "#FF3385", "#00E5FF", "#FF8800"]
    phase   = (t * 0.35) % 1.0                        # cycles once every ~2.86 s
    idx0    = int(phase * len(glow_colors)) % len(glow_colors)
    idx1    = (idx0 + 1) % len(glow_colors)
    frac    = (phase * len(glow_colors)) - idx0        # fractional blend position
    glow_col = blend(glow_colors[idx0], glow_colors[idx1], frac)

    container.config(highlightbackground=glow_col, highlightthickness=2)


# ══════════════════════════════════════════════════════════════════
#  STARFIELD BACKGROUND
# ══════════════════════════════════════════════════════════════════

def draw_stars(cv, stars: list, t: float, w: int, h: int):
    """
    Render the animated parallax starfield background onto a Canvas.

    The starfield is a list of 200 star dicts, each with fields:
        nx   (float) — normalised x position [0, 1]
        ny   (float) — normalised y position [0, 0.42]
                       (upper 42% of screen — stars live in the "sky")
        r    (float) — base radius in pixels [0.4, 2.6]
        ph   (float) — per-star phase offset [0, 2π]

    Each star's brightness pulses independently using:
        brightness = 0.35 + 0.55 × |sin(t × 0.9 + star["ph"])|

    The pulsing rate (0.9 rad/s) is slow enough to feel like a gentle
    twinkle rather than a strobe.  The phase offset ensures no two
    stars pulse in synchrony.

    Stars are drawn as filled ovals.  Radius is computed per-frame:
        draw_r = max(0.5, r × brightness × 0.85)
    The 0.85 factor keeps large stars from becoming too dominant.

    Args:
        cv    — tkinter.Canvas to draw onto.
        stars — List of star dicts as described above.  Created by
                bgyo_game.__init__() and _apply_fullscreen().
        t     — Current game time in seconds.
        w     — Canvas width in pixels (used to scale nx → pixel x).
        h     — Canvas height in pixels (used to scale ny → pixel y).

    Called by:
        bgyo_game._loop() on every frame, before drawing spotlights,
        notes, and UI overlays — stars are always the furthest back.
    """
    for star in stars:
        # Per-star brightness pulse — slow sine wave with individual phase
        brightness = 0.35 + 0.55 * abs(math.sin(t * 0.9 + star["ph"]))
        # Convert normalised position to screen pixels
        sx = star["nx"] * w
        sy = star["ny"] * h
        # Scale radius by brightness (dimmer = smaller apparent star)
        r  = max(0.5, star["r"] * brightness * 0.85)
        # Colour: pure white dimmed by brightness — no colour tint on stars
        col = dim("#FFFFFF", brightness)
        cv.create_oval(sx - r, sy - r, sx + r, sy + r, fill=col, outline="")


# ══════════════════════════════════════════════════════════════════
#  SCREEN TRANSITION OVERLAY
# ══════════════════════════════════════════════════════════════════

def draw_transition_overlay(cv, alpha: float, w: int, h: int):
    """
    Draw a full-screen white rectangle at the given alpha level to
    produce the fade-to-white / fade-from-white transition effect.

    The rectangle fills the entire canvas (0, 0, w, h) and is drawn
    LAST in the frame — on top of everything else — so it correctly
    fades out the entire scene.

    Alpha range:
        0.0 — fully transparent (invisible, no effect on the scene)
        1.0 — pure white (#FFFFFF) covering everything

    The colour is computed as a greyscale hex from the alpha:
        channel = clamp(alpha × 255, 0, 255)
        colour  = "#RRGGBB" where R=G=B=channel

    This gives a neutral white fade that doesn't tint the scene.

    Args:
        cv    — tkinter.Canvas to draw onto.
        alpha — Opacity of the overlay in [0.0, 1.0].
        w, h  — Canvas dimensions in pixels.

    Called by:
        bgyo_game._loop() → _draw_transition() every frame when
        self._tr_dir != 0 (a transition is in progress).

    Transition lifecycle (managed by bgyo_game._fade_to()):
        _tr_dir = +1  → alpha ramps from 0→1 (fade to white)
        callback fires at alpha=1 (screen switches)
        _tr_dir = -1  → alpha ramps from 1→0 (fade from white)
        _tr_dir = 0   → no overlay drawn (this function is a no-op)
    """
    if alpha <= 0.0:
        return   # fully transparent — skip drawing entirely
    a  = max(0.0, min(1.0, alpha))
    na = max(0, min(255, int(a * 255)))
    try:
        cv.create_rectangle(0, 0, w, h,
                            fill=f"#{na:02x}{na:02x}{na:02x}",
                            outline="")
    except Exception:
        pass   # canvas may be destroyed mid-transition


# ══════════════════════════════════════════════════════════════════
#  STAR5 POLYGON  (5-pointed star)
# ══════════════════════════════════════════════════════════════════

def draw_star5(cv, cx: float, cy: float, r: float, col: str):
    """
    Draw a filled 5-pointed star polygon centred at (cx, cy).

    Algorithm:
        A 5-pointed star has 10 vertices alternating between:
          outer radius r   (the five points)
          inner radius r × 0.42  (the five indentations)

        Vertices are placed at angles:
          i × π/5  radians (every 36°)
          starting at -π/2 (top point pointing straight up)

        For each vertex i:
          angle = i × (π / 5) − (π / 2)   — 36° apart, starting at top
          ri    = r       if i is even (outer point)
                  r × 0.42 if i is odd  (inner indent)
          x = cx + cos(angle) × ri
          y = cy + sin(angle) × ri

    The inner radius ratio 0.42 produces a well-proportioned star
    that matches the visual weight of the BGYO brand aesthetic —
    neither too spindly nor too thick.

    Args:
        cv  — tkinter.Canvas.
        cx  — Centre x in screen pixels.
        cy  — Centre y in screen pixels.
        r   — Outer (point) radius in pixels.
        col — Fill colour as '#RRGGBB'.

    Used by:
        bgyo_game._draw_game_screen() — star Particle rendering
        draw_side_panel()             — orbiting star decorations
        SideEffect particle rendering — star=True Particle mode
    """
    pts = []
    for i in range(10):
        ang = (i * math.pi / 5) - math.pi / 2   # 36° increments, starting at top
        ri  = r if i % 2 == 0 else r * 0.42     # alternate outer/inner radius
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
    """
    Draw a soft radial glow effect as a series of concentric filled
    ellipses blending from BG_COL (background) toward hex_col.

    ── HOW IT WORKS ────────────────────────────────────────────────
    `layers` ellipses are drawn from largest (outermost) to smallest
    (innermost), each slightly smaller and slightly more saturated
    than the previous.

    For each layer i from `layers` down to 1:
        cur_rx = rx × (i / layers)   — decreasing radius
        cur_ry = ry × (i / layers)
        factor = max_alpha × (1 − i / layers)
                             — outer layers are near-transparent,
                               inner layers approach max_alpha
        colour = blend(BG_COL, hex_col, factor)
                             — at factor=0: pure BG_COL (invisible)
                               at factor=max_alpha: close to hex_col

    The result is a bloom-like glow that fades naturally into the
    dark background — used for the vanishing-point glow at the far
    end of the rhythm track and other ambient light effects.

    Args:
        cv        — tkinter.Canvas.
        cx, cy    — Centre of the glow ellipse in screen pixels.
        rx, ry    — Outer semi-radii (horizontal, vertical) in pixels.
        hex_col   — Target glow colour as '#RRGGBB'.
        layers    — Number of concentric ellipses (default 6).
                    More layers = smoother gradient; fewer = faster.
        max_alpha — Maximum blend factor toward hex_col for the
                    innermost ellipse (default 0.5 = halfway to hex_col).

    Used by:
        bgyo_game._draw_track()  — vanishing-point horizon glow
        bgyo_game._draw_hud()    — subtle lane glow accents
    """
    for i in range(layers, 0, -1):
        # Scale radii: outermost layer = full rx/ry, innermost = rx/layers
        cur_rx = rx * (i / layers)
        cur_ry = ry * (i / layers)
        # Outer ellipses are barely visible; inner ones are brighter
        factor = max_alpha * (1.0 - (i / layers))
        col    = blend(BG_COL, hex_col, factor)
        cv.create_oval(cx - cur_rx, cy - cur_ry,
                       cx + cur_rx, cy + cur_ry,
                       fill=col, outline="")


# ══════════════════════════════════════════════════════════════════
#  CLICK SOUND SYNTHESIS
# ══════════════════════════════════════════════════════════════════

# Module-level cache for the synthesised click Sound object.
# Generated once on the first call to play_click(); reused thereafter.
_click_snd = None


def play_click(root):
    """
    Play a short synthesised click sound on a free pygame mixer channel.

    The sound is generated programmatically as a 55 ms, 880 Hz sine
    wave with a linear amplitude envelope (loud start → silence end).
    This avoids needing an external audio file for UI feedback.

    ── SIGNAL GENERATION ───────────────────────────────────────────
        sample_rate = 44100 Hz
        duration    = 0.055 s  →  n = 2425 samples
        frequency   = 880 Hz  (A5 — bright, crisp click tone)
        envelope    = 1 − (i / n)  (linear decay from 1.0 to 0.0)
        sample_i    = int(envelope × 14000 × sin(2π × 880 × i / rate))
        buffer      = stereo (L, R channels interleaved)

    Peak amplitude 14000 / 32767 ≈ 43% of the 16-bit range —
    loud enough to be audible without being harsh.  Volume is further
    scaled to 0.45 (45%) via Sound.set_volume().

    The Sound is cached in the module-level _click_snd variable so
    the PCM generation only runs once per application session.

    pygame.mixer.find_channel(True) finds a free channel (or steals
    the oldest if all are busy) so clicks never block other audio.

    Args:
        root — tk.Tk root window (unused; kept for API consistency
               with make_pixel_btn's cmd scheduling pattern).

    Called by:
        make_pixel_btn() → _release handler
        make_btn()       → _cmd_with_sfx wrapper
        bgyo_game directly for canvas nav buttons
    """
    global _click_snd
    try:
        import pygame
        import array

        if _click_snd is None:
            # Generate the PCM buffer on first call
            rate = 44100
            n    = int(rate * 0.055)   # 55 ms at 44.1 kHz
            buf  = array.array("h")    # signed 16-bit integers
            for i in range(n):
                envelope = 1.0 - (i / n)    # linear decay 1→0
                v = int(envelope * 14000 * math.sin(2 * math.pi * 880 * i / rate))
                buf.extend([v, v])           # L and R channels (stereo)
            _click_snd = pygame.mixer.Sound(buffer=bytes(buf))
            _click_snd.set_volume(0.45)

        # Play on a free channel — True = steal oldest if none free
        ch = pygame.mixer.find_channel(True)
        if ch:
            ch.play(_click_snd)

    except Exception:
        pass   # pygame unavailable or mixer not initialised — silent no-op
