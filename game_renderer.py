import math
import random

import constants as C
from constants import (
    BASE_W, BASE_H,
    MEMBER_COLORS, BG_COL,
    LANE_CONFIGS,
    UI_FONT, MONO_FONT, TITLE_FONT,
    dim, blend, lighten, additive_blend,
    lane_cx,
)
from ui_helpers import draw_star5, draw_ellipse_glow


# ══════════════════════════════════════════════════════════════════
#  TRACK  (3D perspective lane grid + hit bar)
# ══════════════════════════════════════════════════════════════════

def draw_track(cv, t: float, combo: int, num_lanes: int,
               w: int, h: int,
               _project, _HIT_DEPTH: float) -> None:
    """
    Draw the 3D perspective lane track onto the Canvas.

    The track is drawn as two overlapping systems of lines:

    1. HORIZONTAL DEPTH LINES  (28 rows, evenly spaced in depth space)
       Each row spans the full track width at its projected y.
       Two lines per row:
         Glow pass  — wider (up to 8 px), dimmed to 40% alpha for a
                      soft neon bloom effect.
         Core pass  — narrower (up to 3 px), full brightness for a
                      crisp visible grid.

       Row colour transitions through three depth zones:
         depth < 0.35: deep purple (#330044) → indigo (#7700FF)
         depth 0.35–0.70: navy (#0033CC) → cyan (#00E5FF)
         depth 0.70–1.0 : cyan (#00E5FF) → gold (#FFD700)
       These transitions create the stage-light "colour wash" effect
       that makes the track look like a lit performance floor.

       Brightness scales with the current combo:
         brightness = min(1.0, 0.55 + combo × 0.012)
       At combo 0: base level 0.55 (bright).
       At combo 37+: full brightness 1.0 — the stage glows brighter
       as the player builds their streak, creating a visual reward loop.

       Depth exponent: d = (i / (N-1)) ^ 1.4
       The 1.4 power biases row density toward the horizon (more rows
       where perspective convergence is strongest) for a more realistic
       perspective grid.

    2. VERTICAL LANE DIVIDERS  (num_lanes + 1 lines)
       Run from the vanishing point (depth=0) to the near edge (depth=1).
       Coloured with MEMBER_COLORS cycling per divider.
       Two passes: glow (15% brightness) + core (40% brightness).

    3. HIT BAR  (three-line glow stack at depth = HIT_DEPTH)
       The bar that the player must press keys at exactly.
       Three overlapping horizontal lines:
         Outer glow: 11 px wide, 30% alpha
         Mid glow:    5 px wide, 55% alpha
         Core:        2 px wide, 85% alpha (near-white)
       Brightness pulses at 4.2 Hz using sin(t × 4.2).

    Args:
        cv         — tkinter.Canvas.
        t          — Current game time in seconds (for hit-bar pulse).
        combo      — Current combo count (brightens the stage).
        num_lanes  — Active lane count (3, 4, or 5).
        w, h       — Canvas dimensions in pixels.
        _project   — Projection callable(lx, depth) → (px, py, scale).
        _HIT_DEPTH — Depth value of the hit bar.

    Called by:
        bgyo_game._draw_stage()  — before notes and particles.
    """
    brightness = min(1.0, 0.55 + combo * 0.012)
    grid_steps = 28

    # ── Horizontal depth lines ────────────────────────────────────
    for i in range(grid_steps):
        # Non-linear depth distribution: biased toward horizon
        d  = (i / (grid_steps - 1)) ** 1.4
        lp = _project(0, d)   # left edge at this depth
        rp = _project(1, d)   # right edge at this depth

        # Three-zone colour ramp from deep purple → cyan → gold
        if   d < 0.35:
            base = blend("#330044", "#7700FF", d / 0.35)
        elif d < 0.70:
            base = blend("#0033CC", "#00E5FF", (d - 0.35) / 0.35)
        else:
            base = blend("#00E5FF", "#FFD700", (d - 0.70) / 0.30)

        # Brightness increases toward the near edge (d → 1)
        la = brightness * (0.35 + d * 0.70)

        # Glow pass: wide, dim — creates a soft bloom halo around the core
        cv.create_line(lp[0], lp[1], rp[0], rp[1],
                       fill=dim(base, la * 0.40),
                       width=max(3, int(d * 8)))
        # Core pass: narrow, bright — the sharp visible grid line
        cv.create_line(lp[0], lp[1], rp[0], rp[1],
                       fill=dim(base, la),
                       width=max(1, int(d * 3)))

    # ── Vertical lane dividers ────────────────────────────────────
    for i in range(num_lanes + 1):
        lx  = i / num_lanes           # normalised x position [0, 1]
        fa  = _project(lx, 0.0)      # far (horizon) endpoint
        na  = _project(lx, 1.0)      # near (camera) endpoint
        col = MEMBER_COLORS[i % len(MEMBER_COLORS)]

        # Glow pass
        cv.create_line(fa[0], fa[1], na[0], na[1],
                       fill=dim(col, brightness * 0.15),
                       width=max(2, int(4 * lx)))
        # Core pass
        cv.create_line(fa[0], fa[1], na[0], na[1],
                       fill=dim(col, brightness * 0.40),
                       width=1)

    # ── Hit bar ───────────────────────────────────────────────────
    # Three overlapping lines create a glowing gold bar that pulses
    # with the game time, guiding the player's eye to the hit zone.
    pls   = 0.65 + 0.35 * math.sin(t * 4.2)   # pulse frequency: 4.2 rad/s
    bar_b = min(1.0, brightness + 0.15)         # slightly brighter than stage
    hl    = _project(0, _HIT_DEPTH)             # left end of hit bar
    hr    = _project(1, _HIT_DEPTH)             # right end of hit bar

    cv.create_line(hl[0], hl[1], hr[0], hr[1],
                   fill=dim("#FFD700", pls * bar_b * 0.30), width=11)
    cv.create_line(hl[0], hl[1], hr[0], hr[1],
                   fill=dim("#FFD700", pls * bar_b * 0.55), width=5)
    cv.create_line(hl[0], hl[1], hr[0], hr[1],
                   fill=dim("#FFFFFF", pls * bar_b * 0.85), width=2)


# ══════════════════════════════════════════════════════════════════
#  GAME BANNER  (optional wide stage photo)
# ══════════════════════════════════════════════════════════════════

def draw_game_banner(cv, img_refs: dict, w: int, h: int) -> None:
    """
    Draw the bgyo_during_game banner photo centred above the track.

    The image is stored in img_refs["game_banner"] (a PhotoImage) and
    thumbnailed to 86% width × 18% height of the canvas during loading.
    It is drawn at 14.5% down from the top so it sits in the upper
    portion of the screen, framing the stage area below it.

    This is a purely decorative element — it does not affect gameplay.
    If the image file is absent, this function is a no-op.

    Args:
        cv       — tkinter.Canvas.
        img_refs — BGYOGame.img_refs dict.
        w, h     — Canvas dimensions.
    """
    if "game_banner" in img_refs:
        cv.create_image(w // 2, int(h * 0.145),
                        image=img_refs["game_banner"], anchor="center")


# ══════════════════════════════════════════════════════════════════
#  NOTES
# ══════════════════════════════════════════════════════════════════

def draw_notes(cv, notes: list, t: float, num_lanes: int,
               _project) -> None:
    """
    Draw all live notes onto the Canvas.

    Each note is rendered as a layered circle at its projected screen
    position with size scaled by the projection's `scale` factor
    (larger/closer at higher depth values).

    ── VISUAL LAYERS PER NOTE (back to front) ───────────────────────

    1. SOFT GLOW  (2 concentric ovals)
       Radius: r × 1.5 down to r × 1.25  (step: r × 0.25)
       Alpha:  0.14 down to 0.09
       Creates a diffuse colour halo behind the note that bleeds
       into the dark track, making notes visible at low contrast.
       Reduced from 4 layers to 2 in v17 to prevent lag on dense ACE
       beat sections.

    2. MAIN BODY  (solid filled oval)
       Radius: r = scale × 27 pixels
       Fill:   MEMBER_COLORS[lane % 5]
       Outline: white (#FFFFFF), width 3 px
       The white outline keeps notes readable against any track colour.

    3. GLINT  (small white oval, offset up-left)
       Radius: approximately r × 0.25 wide, r × 0.13 tall
       Position: shifted -r×0.13 upward from the note centre
       Simulates a top-left light reflection on a glossy sphere,
       giving the note a 3D "button" appearance.

    4. APPROACH SPARKLES  (3 orbiting dots, only near the hit bar)
       Only drawn when note.depth > 0.65 (within 35% of the hit bar).
       Three small ovals orbit the note at r × 1.35 distance, rotating
       at 4.0 rad/s to draw the player's eye as the note approaches.
       The 3 dots are evenly spaced (2π/3 apart) and share the note's
       colour.

    Args:
        cv        — tkinter.Canvas.
        notes     — List of live Note objects from gs["notes"].
        t         — Current game time in seconds (for sparkle rotation).
        num_lanes — Active lane count for lane_cx() normalisation.
        _project  — Projection callable(lx, depth) → (px, py, scale).
    """
    for n in notes:
        px, py, sc = _project(lane_cx(n.lane, num_lanes), n.depth)
        r   = sc * 27   # base radius scales with depth (closer = larger)
        col = MEMBER_COLORS[n.lane % len(MEMBER_COLORS)]

        # Layer 1 — soft glow halos (2 passes)
        for gi in range(2):
            gr = r * (1.5 - gi * 0.25)    # shrinking glow radius
            a  = max(0.0, 0.14 - gi * 0.05)  # dimming glow alpha
            cv.create_oval(px - gr, py - gr, px + gr, py + gr,
                           fill=dim(col, a), outline="")

        # Layer 2 — main note body: solid fill + white outline
        cv.create_oval(px - r, py - r, px + r, py + r,
                       fill=col, outline="#FFFFFF", width=3)

        # Layer 3 — top-left glint (mimics a specular highlight on a sphere)
        # Offset: x − r×0.13 (left), y − r×0.14 (up)
        cv.create_oval(px - r * 0.38, py - r * 0.38 - r * 0.14,
                       px + r * 0.12, py + r * 0.12 - r * 0.14,
                       fill="#ffffff", outline="")

        # Layer 4 — approach sparkles (only within 35% of the hit bar)
        if n.depth > 0.65:
            for si in range(3):
                # Evenly spaced at 2π/3 apart, rotating at 4.0 rad/s
                ang = t * 4.0 + si * (2 * math.pi / 3)
                sx_ = px + math.cos(ang) * r * 1.35
                sy_ = py + math.sin(ang) * r * 1.35
                cv.create_oval(sx_ - 2, sy_ - 2, sx_ + 2, sy_ + 2,
                               fill=col, outline="")


# ══════════════════════════════════════════════════════════════════
#  LANE TARGETS  (hit-zone markers at the hit bar)
# ══════════════════════════════════════════════════════════════════

def draw_lane_targets(cv, t: float, num_lanes: int, lane_lit: list,
                      _project, _HIT_DEPTH: float) -> None:
    """
    Draw the circular hit-zone markers at the hit bar for each lane.

    There is one target per lane.  Each target has two states:

    INACTIVE  (lane key is not currently held down):
        • Outer ring: r × 1.38, dim outline (pulsing at 22% brightness)
        • Inner circle: dark fill (#0a001f) with 55%-bright outline
        • Key label: dim text at 60% brightness
        The pulsing (0.7 + 0.3 × sin(t × 5 + i)) reminds the player
        where to press without being distracting.

    ACTIVE  (lane key IS being held down — lane_lit[i] == True):
        • Three concentric glow rings at r×2.8, r×2.0, r×1.55
          with increasing alpha (8%, 14%, 22%) — "halo expanding" feel
        • Bright outer ring at r×1.38 with 70% outline
        • Filled inner circle at 65% fill + white outline
        • Key label in bright white
        The active state gives immediate visual confirmation that the
        correct key is being pressed.

    Args:
        cv         — tkinter.Canvas.
        t          — Current game time in seconds.
        num_lanes  — Active lane count.
        lane_lit   — List of bool, True if the lane's key is held down.
        _project   — Projection callable.
        _HIT_DEPTH — Hit bar depth value.
    """
    lane_keys = LANE_CONFIGS[num_lanes]["keys"]

    for i in range(num_lanes):
        px, py, sc = _project(lane_cx(i, num_lanes), _HIT_DEPTH)
        r      = sc * 28
        col    = MEMBER_COLORS[i % len(MEMBER_COLORS)]
        active = lane_lit[i] if i < len(lane_lit) else False
        # Slow pulse for inactive state — breathing rhythm at 5 rad/s
        pls    = 0.7 + 0.3 * math.sin(t * 5 + i)

        key_label = lane_keys[i].upper() if i < len(lane_keys) else "?"

        if active:
            # ── ACTIVE: key is held ───────────────────────────────
            # Three expanding glow rings signal the key is pressed
            for ri_, ra in [(r * 2.8, 0.08), (r * 2.0, 0.14), (r * 1.55, 0.22)]:
                cv.create_oval(px - ri_, py - ri_, px + ri_, py + ri_,
                               outline=dim(col, ra * pls), width=2, fill="")
            # Bright outer ring
            cv.create_oval(px - r * 1.38, py - r * 1.38,
                           px + r * 1.38, py + r * 1.38,
                           outline=dim(col, 0.70), width=3, fill="")
            # Filled inner circle — partially transparent col fill + white border
            cv.create_oval(px - r, py - r, px + r, py + r,
                           fill=dim(col, 0.65), outline="#FFFFFF", width=3)
            # Bright white key label
            cv.create_text(px, py, text=key_label,
                           fill="#FFFFFF",
                           font=(MONO_FONT, max(10, int(sc * 18)), "bold"))
        else:
            # ── INACTIVE: subtle pulsing ring ─────────────────────
            # Single dim outer ring — visible but not distracting
            cv.create_oval(px - r * 1.38, py - r * 1.38,
                           px + r * 1.38, py + r * 1.38,
                           outline=dim(col, pls * 0.22), width=2, fill="")
            # Dark inner circle with moderate outline
            cv.create_oval(px - r, py - r, px + r, py + r,
                           fill="#0a001f", outline=dim(col, 0.55), width=2)
            # Dim key label
            cv.create_text(px, py, text=key_label,
                           fill=dim(col, 0.60),
                           font=(MONO_FONT, max(10, int(sc * 16)), "bold"))


# ══════════════════════════════════════════════════════════════════
#  PARTICLES  &  SPARKS
# ══════════════════════════════════════════════════════════════════

def draw_particles(cv, particles: list) -> None:
    """
    Draw all live Particle objects onto the Canvas.

    Each particle is rendered as either:
      • A filled oval (star=False): radius capped at 8 px, scaled by
        remaining life (shrinks as the particle fades).
      • A 5-pointed star polygon (star=True): drawn via draw_star5()
        at r × 1.5 size, for the celebratory PERFECT/combo effects.

    Alpha is scaled to 80% of remaining life so particles fade out
    smoothly during the last few frames of their lifetime.

    Size formula:
        r = max(1, min(8, int(p.size × p.life × 0.85)))
    The 0.85 factor gives a slight size-shrink before full fade,
    making the disappearance feel natural rather than abrupt.

    Args:
        cv        — tkinter.Canvas.
        particles — BGYOGame.particles list.
    """
    for p in particles:
        r   = max(1, min(8, int(p.size * p.life * 0.85)))
        col = dim(p.col, p.life * 0.80)   # 80% life → colour opacity
        if p.star:
            draw_star5(cv, p.x, p.y, r * 1.5, col)
        else:
            cv.create_oval(p.x - r, p.y - r, p.x + r, p.y + r,
                           fill=col, outline="")


def draw_sparks(cv, sparks: list) -> None:
    """
    Draw all live Spark objects onto the Canvas.

    Sparks are small (max 4 px), fast-decaying bright dots used for
    the PERFECT hit electric-flash effect.  They are drawn as plain
    filled ovals (no star shape) at 70% life-based alpha.

    Size formula:
        r = max(1, min(4, int(s.size × s.life)))
    No 0.85 factor — sparks keep their full size until they fade to
    give a sharp flare rather than a softening shrink.

    Args:
        cv     — tkinter.Canvas.
        sparks — BGYOGame.sparks list.
    """
    for s in sparks:
        r   = max(1, min(4, int(s.size * s.life)))
        col = dim(s.col, s.life * 0.70)
        cv.create_oval(s.x - r, s.y - r, s.x + r, s.y + r,
                       fill=col, outline="")


# ══════════════════════════════════════════════════════════════════
#  FLASH LABELS  (floating feedback text)
# ══════════════════════════════════════════════════════════════════

def draw_flashes(cv, flashes: list) -> None:
    """
    Draw all live Flash objects as floating text labels.

    Each Flash is drawn with a 1-px drop shadow (black at 60% alpha)
    followed by the main text (at 92% life-based alpha).  This two-
    pass technique ensures readability against any background colour —
    the dark shadow creates contrast whether the text is over a bright
    spotlight or a dark section of the track.

    Flashes with alpha < 0.05 are skipped entirely to avoid drawing
    near-invisible items.

    Alpha source:
        If the Flash has an effective_alpha() method it is used
        (reserved for a future easing curve enhancement); otherwise
        alpha = clamp(f.life, 0, 1) is used directly.

    Args:
        cv      — tkinter.Canvas.
        flashes — BGYOGame.flashes list.
    """
    for f in flashes:
        # Support optional effective_alpha() method for eased fade
        a = (f.effective_alpha()
             if hasattr(f, "effective_alpha")
             else max(0.0, min(1.0, f.life)))
        if a < 0.05:
            continue   # fully transparent — skip

        weight = "bold" if f.bold else "normal"
        font   = (TITLE_FONT, f.size, weight)

        # Drop shadow — 1 px right and 1 px down from the text origin
        cv.create_text(f.x + 1, f.y + 1,
                       text=f.text,
                       fill=dim("#000000", a * 0.60),
                       font=font)
        # Main coloured text
        cv.create_text(f.x, f.y,
                       text=f.text,
                       fill=dim(f.col, a * 0.92),
                       font=font)


# ══════════════════════════════════════════════════════════════════
#  SIDE EFFECT LABELS  (screen-edge PERFECT / COMBO / MISS text)
# ══════════════════════════════════════════════════════════════════

def draw_side_effects(cv, side_effects: list, t: float,
                      w: int, h: int) -> None:
    """
    Draw SideEffect particles and label text on the screen edges.

    SideEffects appear at x≈65 px (left) or x≈W-65 px (right) and
    display stacked label text + burst particles for PERFECT, COMBO,
    and MISS events.

    ── PARTICLE RENDERING ───────────────────────────────────────────
    Drawn for ALL SideEffects on a side before any labels.  Star
    particles use draw_star5() at r×1.6; plain particles use ovals.
    Alpha is 70% of remaining life.

    ── LABEL STACK ──────────────────────────────────────────────────
    Up to MAX_VISIBLE=3 labels are shown per side simultaneously,
    sorted newest-first by remaining life (highest life = most recent,
    shown at the top).  Labels with life ≤ 0.15 are excluded (nearly
    faded, too dim to read).

    Labels are vertically centred on the screen midline:
        total_h = (n_visible - 1) × LABEL_SLOT_H
        base_y  = h//2 - total_h//2

    Each label is drawn with a three-pass technique:
      1. Outer neon glow: font size 22, alpha × 0.30
      2. Drop shadow:     font size 20, offset (+2,+2), black at 80%
      3. Main vivid text: font size 20, alpha × 1.0
    The colour cycles through a 6-colour palette at 2.5 Hz per slot,
    with each slot offset by +1.3 rad to prevent all labels from
    sharing the same colour simultaneously.

    Args:
        cv           — tkinter.Canvas.
        side_effects — BGYOGame.side_effects list.
        t            — Current game time in seconds.
        w, h         — Canvas dimensions.

    Called by:
        bgyo_game._draw_game() after draw_combo_panel().
    """
    left_ses  = [se for se in side_effects if se.side == "left"]
    right_ses = [se for se in side_effects if se.side == "right"]

    # Scale x positions relative to canvas width
    LABEL_SLOT_H = 52   # vertical spacing between stacked labels (pixels)
    MAX_VISIBLE  = 3    # cap: at most 3 labels visible per side at once
    _cycle_cols  = ["#FFD700", "#FF3385", "#00FF99", "#00E5FF", "#FF8800", "#CC44FF"]

    for ses_list, side in ((left_ses, "left"), (right_ses, "right")):
        # Label x-position: inset 65 px from the edge, scaled to canvas width
        lx = int(65 * w / BASE_W) if side == "left" else w - int(65 * w / BASE_W)

        # ── Particle pass — draw all particles before labels ─────
        for se in ses_list:
            for p in se.particles:
                r   = max(1, int(p.size * p.life * 0.85))
                col = dim(p.col, p.life * 0.7)
                if p.star:
                    draw_star5(cv, p.x, p.y, r * 1.6, col)
                else:
                    cv.create_oval(p.x - r, p.y - r, p.x + r, p.y + r,
                                   fill=col, outline="")

        # ── Label pass — newest first, max 3 visible ─────────────
        visible = [se for se in ses_list if se.label and se.life > 0.15]
        visible = sorted(visible, key=lambda se: se.life, reverse=True)[:MAX_VISIBLE]

        n_vis   = len(visible)
        total_h = (n_vis - 1) * LABEL_SLOT_H
        # Centre the label stack on the screen's vertical midline
        base_y  = h // 2 - total_h // 2

        for slot_i, se in enumerate(visible):
            la     = min(0.90, se.life * 1.4)   # alpha: clamps at 0.90 max
            slot_y = base_y + slot_i * LABEL_SLOT_H

            # Per-slot colour cycling — each slot shimmers independently
            cp      = (t * 2.5 + slot_i * 1.3) % 1.0
            ci0     = int(cp * len(_cycle_cols)) % len(_cycle_cols)
            ci1     = (ci0 + 1) % len(_cycle_cols)
            cf      = (cp * len(_cycle_cols)) - ci0
            cyc_col = blend(_cycle_cols[ci0], _cycle_cols[ci1], cf)

            # Outer neon glow (slightly larger font for bloom effect)
            cv.create_text(lx, slot_y,
                           text=se.label,
                           fill=dim(cyc_col, la * 0.30),
                           font=(TITLE_FONT, 22, "bold"), anchor="center")
            # Drop shadow
            cv.create_text(lx + 2, slot_y + 2,
                           text=se.label,
                           fill=dim("#000000", la * 0.80),
                           font=(TITLE_FONT, 20, "bold"), anchor="center")
            # Main vivid text
            cv.create_text(lx, slot_y,
                           text=se.label,
                           fill=dim(cyc_col, la),
                           font=(TITLE_FONT, 20, "bold"), anchor="center")


# ══════════════════════════════════════════════════════════════════
#  COMBO SIDE PANEL  (animated cheer + orbiting stars)
# ══════════════════════════════════════════════════════════════════

def draw_combo_panel(cv, side: str, combo: int, col: str,
                     alpha: float, t: float,
                     w: int, h: int) -> None:
    """
    Draw the brief animated combo-cheer panel on one screen edge.

    Appears during combo streaks ≥ 10, briefly flashing a cheer word
    ("FIRE!", "ACE!", "BGYO!", etc.) with orbiting star decorations.

    ── CHEER WORD SELECTION ─────────────────────────────────────────
    A fixed list of 6 cheer strings cycles by combo index:
        combo % 6 → ["FIRE!", "ACE!", "PERFECT!", "WOW!", "BGYO!", "ACES!"]
    This means each milestone triggers a different word, creating
    variety without random flickering.

    ── COLOUR CYCLING ───────────────────────────────────────────────
    The cheer colour cycles through a 6-colour palette at 3.5 Hz
    (t × 3.5), giving a rapid colour-shift effect on the text.
    The same phase drives the orbiting star colours.

    ── THREE-PASS TEXT ──────────────────────────────────────────────
    1. Outer neon glow: font 30, alpha × 0.28 — wide bloom halo
    2. Drop shadow: font 26, offset (+2,+2), black at 80%
    3. Main vivid text: font 26, alpha × 0.95

    ── ORBITING STARS ───────────────────────────────────────────────
    6 stars orbit the cheer text in an ellipse (radius 50 px horizontally,
    22.5 px vertically — squashed for a floor-plane perspective feel).
    Only drawn when alpha > 0.3.

    Args:
        cv    — tkinter.Canvas.
        side  — "left" or "right".
        combo — Current combo count (selects cheer word and colour).
        col   — Primary colour for the panel (usually "#FFD700" or "#FF3385").
        alpha — Panel opacity [0, 1].
        t     — Current game time in seconds.
        w, h  — Canvas dimensions.
    """
    if alpha <= 0:
        return

    x = int(65 * w / BASE_W) if side == "left" else w - int(65 * w / BASE_W)

    cheers = ["FIRE!", "ACE!", "PERFECT!", "WOW!", "BGYO!", "ACES!"]
    cheer  = cheers[combo % len(cheers)]
    # Position the cheer above the label stack (centred at h//2 - 90)
    cheer_y = h // 2 - 90

    # Colour cycling at 3.5 Hz
    _cheer_cols = ["#FFD700", "#FF3385", "#00FF99", "#00E5FF", "#FF8800", "#CC44FF"]
    cp   = (t * 3.5) % 1.0
    ci0  = int(cp * len(_cheer_cols)) % len(_cheer_cols)
    ci1  = (ci0 + 1) % len(_cheer_cols)
    cf   = (cp * len(_cheer_cols)) - ci0
    cyc  = blend(_cheer_cols[ci0], _cheer_cols[ci1], cf)

    # Three-pass text rendering
    cv.create_text(x, cheer_y, text=cheer,
                   fill=dim(cyc, alpha * 0.28),
                   font=(TITLE_FONT, 30, "bold"), anchor="center")
    cv.create_text(x + 2, cheer_y + 2, text=cheer,
                   fill=dim("#000000", alpha * 0.80),
                   font=(TITLE_FONT, 26, "bold"), anchor="center")
    cv.create_text(x, cheer_y, text=cheer,
                   fill=dim(cyc, alpha * 0.95),
                   font=(TITLE_FONT, 26, "bold"), anchor="center")

    # Orbiting stars — only when alpha is strong enough to be visible
    if alpha > 0.3:
        for star_i in range(6):
            # Each star orbits at the same angular velocity but starts at
            # a different phase (star_i × 2π/6 = 60° apart)
            angle     = (t * 2.2 + star_i * (2 * math.pi / 6)) % (2 * math.pi)
            # Orbit radius breathes slightly using a secondary oscillation
            star_dist = 50 + 10 * math.sin(t * 3 + star_i)
            star_x    = x       + math.cos(angle) * star_dist
            # Vertical radius is 45% of horizontal for perspective squash
            star_y    = cheer_y + math.sin(angle) * star_dist * 0.45
            # Size pulses between 7 and 11 px
            star_size = 7 + 4 * abs(math.sin(t * 4 + star_i))
            star_col  = _cheer_cols[(ci0 + star_i) % len(_cheer_cols)]
            draw_star5(cv, star_x, star_y, star_size,
                       dim(star_col,
                           alpha * (0.5 + 0.4 * abs(math.sin(t * 5 + star_i)))))


# ══════════════════════════════════════════════════════════════════
#  COUNTDOWN  (3…2…1…GO!)
# ══════════════════════════════════════════════════════════════════

def draw_countdown(cv, gs: dict, t: float, w: int, h: int,
                   sy_fn) -> None:
    """
    Draw the pre-song 3…2…1 countdown and the "GO!" flash.

    ── COUNTDOWN (3→2→1) ────────────────────────────────────────────
    While gs["countdown"] > 0:
        num   = ceil(countdown)  → the displayed integer (3, 2, 1)
        alpha = countdown - floor(countdown)  → fractional second position

    Alpha drives the colour intensity:
        fill = dim("#FFD700", 0.3 + alpha × 0.7)
    At the start of each second (alpha≈1.0): bright gold.
    At the end (alpha→0.0): dim gold — creates a natural "tick" pulse.

    Two-pass shadow + main text for readability.

    ── GO! FLASH ────────────────────────────────────────────────────
    After countdown ends, gs["countdown_go"] decays from 0.8 → 0.
    Text size is slightly larger than the countdown numbers.
    Alpha = min(1.0, countdown_go × 1.5) — ramps to full in 0.67 s.
    Colour: green (#00FF99) — positive "start!" signal.

    Args:
        cv    — tkinter.Canvas.
        gs    — Game state dict (reads "countdown" and "countdown_go").
        t     — Current game time (unused here, kept for API consistency).
        w, h  — Canvas dimensions.
        sy_fn — Vertical scaling function sy(y) = y × h / BASE_H.
    """
    cd = gs.get("countdown", 0)

    if cd > 0:
        # Countdown numbers: ceil to display whole seconds
        num   = math.ceil(cd)
        alpha = cd - math.floor(cd)   # fractional part: how far through this second
        fsz   = int(sy_fn(120))

        # Drop shadow
        cv.create_text(w // 2 + 3, h // 2 + 3,
                       text=str(num), fill="#000000",
                       font=(TITLE_FONT, fsz, "bold"))
        # Gold main text — brighter at start of second, dimmer at end
        cv.create_text(w // 2, h // 2,
                       text=str(num),
                       fill=dim("#FFD700", 0.3 + alpha * 0.7),
                       font=(TITLE_FONT, fsz, "bold"))

    elif gs.get("countdown_go", 0) > 0:
        cg  = gs["countdown_go"]
        fsz = int(sy_fn(140))

        # "GO!" is slightly larger and green — excitement peak
        cv.create_text(w // 2 + 3, h // 2 + 3,
                       text="GO!", fill="#000000",
                       font=(TITLE_FONT, fsz, "bold"))
        cv.create_text(w // 2, h // 2,
                       text="GO!",
                       fill=dim("#00FF99", min(1.0, cg * 1.5)),
                       font=(TITLE_FONT, fsz, "bold"))


# ══════════════════════════════════════════════════════════════════
#  COMBO BURST TEXT  (★ Nx COMBO! below the track)
# ══════════════════════════════════════════════════════════════════

def draw_combo_burst(cv, gs: dict, w: int, h: int, sy_fn) -> None:
    """
    Draw the "★ Nx COMBO!" text below the track for active streaks.

    Only drawn when combo ≥ 10 and combo_flash > 0.

    ── SCALING ──────────────────────────────────────────────────────
    Font size scales with combo magnitude:
        scale = min(1.6, 1.0 + combo / 80)
        size  = int(22 × scale)
    At combo 10: 22 pt (baseline).
    At combo 50: ~34 pt (scaled up).
    At combo 80+: 35 pt (capped at scale 1.6).

    ── COLOUR ───────────────────────────────────────────────────────
    combo < 50: gold (#FFD700) — warm praise
    combo ≥ 50: pink (#FF3385) — intense celebration

    ── ALPHA ────────────────────────────────────────────────────────
    a = min(0.85, combo_flash × 0.9)
    combo_flash decays from 2.2 → 0 over ~2.2 seconds.
    The 0.9 multiplier means the text is at 85% opacity for ~1 s
    then fades out, giving a clear impression without lingering.

    ── POSITION ─────────────────────────────────────────────────────
    Centred at x=w//2, y = h//2 + 95 scaled — places the burst text
    just below the hit bar, out of the way of active notes.

    Args:
        cv    — tkinter.Canvas.
        gs    — Game state dict.
        w, h  — Canvas dimensions.
        sy_fn — Vertical scaling function.
    """
    if not (gs.get("combo_flash", 0) > 0 and gs.get("combo", 0) >= 10):
        return

    combo = gs["combo"]
    scale = min(1.6, 1.0 + combo / 80)
    sz    = int(22 * scale)
    col   = "#FF3385" if combo >= 50 else "#FFD700"
    a     = min(0.85, gs["combo_flash"] * 0.9)
    burst_y = h // 2 + int(sy_fn(95))

    # Drop shadow
    cv.create_text(w // 2 + 2, burst_y + 2,
                   text=f"★  {combo}x  COMBO!",
                   fill=dim("#000000", a * 0.7),
                   font=(TITLE_FONT, sz, "bold"))
    # Main text
    cv.create_text(w // 2, burst_y,
                   text=f"★  {combo}x  COMBO!",
                   fill=dim(col, a),
                   font=(TITLE_FONT, sz, "bold"))


# ══════════════════════════════════════════════════════════════════
#  PAUSE OVERLAY
# ══════════════════════════════════════════════════════════════════

def draw_pause_overlay(cv, gs: dict, game_volume: float,
                       overlay_open: bool,
                       w: int, h: int,
                       sx_fn, sy_fn) -> None:
    """
    Draw the PAUSED overlay panel when the game is paused via SPACE.

    Only drawn when:
      • gs["paused"] is True
      • gs["menu_paused"] is False   (not paused BY the in-game menu)
      • overlay_open is False        (in-game menu is not open)

    This ensures the PAUSED text only appears for the SPACE-key pause,
    never when the menu overlay is showing (the menu is its own UI).

    Layout:
        Dark translucent rectangle centred on screen (±260 px wide × ±100 px tall)
        with a gold (#FFD700) border.
        "P A U S E D" in large gold type.
        Key hints and current volume below.

    Args:
        cv           — tkinter.Canvas.
        gs           — Game state dict.
        game_volume  — Current music volume [0,1] for display.
        overlay_open — True if the in-game menu overlay is open.
        w, h         — Canvas dimensions.
        sx_fn        — Horizontal scaling function.
        sy_fn        — Vertical scaling function.
    """
    if not (gs.get("paused") and not gs.get("menu_paused") and not overlay_open):
        return

    # Translucent dark panel
    cv.create_rectangle(
        w // 2 - int(sx_fn(260)), h // 2 - int(sy_fn(100)),
        w // 2 + int(sx_fn(260)), h // 2 + int(sy_fn(100)),
        fill="#08001A", outline=dim("#FFD700", 0.7), width=2
    )
    # "P A U S E D" with letter spacing
    cv.create_text(w // 2, h // 2 - 20,
                   text="P A U S E D", fill="#FFD700",
                   font=(TITLE_FONT, int(sy_fn(48)), "bold"))
    # Key hint row
    cv.create_text(w // 2, h // 2 + int(sy_fn(36)),
                   text="SPACE = Resume   |   ESC = Quit   |   +/- = Volume",
                   fill="#00E5FF", font=(UI_FONT, 12, "bold"))
    # Current volume level
    cv.create_text(w // 2, h // 2 + int(sy_fn(64)),
                   text=f"♪ Music: {int(game_volume * 100)}%",
                   fill="#FFD700", font=(UI_FONT, 11, "bold"))


# ══════════════════════════════════════════════════════════════════
#  LOADING INDICATOR  (beat-analysis in progress)
# ══════════════════════════════════════════════════════════════════

def draw_loading_bar(cv, gs: dict, w: int, h: int, sx_fn) -> None:
    """
    Draw the "⟳ Syncing beats to music…" indicator while beat analysis
    is running in the background thread.

    Only drawn when gs["loading"] is True.  The indicator disappears
    automatically once the analysis worker completes and sets
    gs["loading"] = False and gs["beat_mode"] = True.

    Positioned at h//2 + 55–88 (just below screen centre) so it does
    not obstruct notes during the pre-analysis random-spawn phase.

    Args:
        cv    — tkinter.Canvas.
        gs    — Game state dict.
        w, h  — Canvas dimensions.
        sx_fn — Horizontal scaling function.
    """
    if not gs.get("loading"):
        return

    bx = int(sx_fn(280))
    cv.create_rectangle(
        w // 2 - bx, h // 2 + 55,
        w // 2 + bx, h // 2 + 88,
        fill="#000000", outline="#FFD700", width=1
    )
    cv.create_text(w // 2, h // 2 + 71,
                   text="⟳  Syncing beats to music …",
                   fill="#FFD700", font=(UI_FONT, 10, "bold"))


# ══════════════════════════════════════════════════════════════════
#  HUD  (score, combo, song title, progress bar)
# ══════════════════════════════════════════════════════════════════

def draw_hud(cv, gs: dict, t: float, cfg,
             audio, img_refs: dict,
             game_volume: float,
             w: int, h: int,
             sx_fn, sy_fn,
             _HIT_DEPTH: float,
             find_cover_fn, PIL_OK: bool,
             load_cover_fn) -> None:
    """
    Draw the full gameplay HUD onto the Canvas.

    The HUD comprises six visual regions drawn in order:

    1. PROGRESS BAR  (top edge, 7 px tall)
       Spans the full canvas width.  Fill colour transitions from gold
       (#FFD700 component) at 0% to gold-fading-to-dark at 100%.
       Progress = audio.position() / song_duration.

    2. SCORE BOX  (top-left, ~193 × 77 px)
       Dark background with animated gold glow border (pulses at 2.1 Hz).
       "SCORE" label + current score in large monospaced font.
       Two-pass shadow+white text for the number.

    3. SONG TITLE / COVER BOX  (top-centre, 400 × 58 px)
       Cover art thumbnail on the left (48×48 px).
       Scrolling LED-effect song title: colour cycles through member colours
       at 0.55 Hz with lerp between adjacent palette entries.
       Status indicator below the title: "⟳ ANALYSING", "♪ PLAYING", or
       "◌ NO AUDIO" depending on gs state.
       Glowing cycling-colour border.

    4. COMBO BOX  (top-right, ~193 × 77 px)
       Mirror of the score box.  Shows "COMBO" label + current count.

    5. MEMBER DISPLAY  (bottom-centre)
       Current member name in large coloured type with the member's
       role subtitle below.  Cycles every 8 seconds.
       Difficulty label + lane config label at bottom-right.
       Control hints (SPACE / +- volume) at bottom-left.

    6. PERFECT/GOOD/MISS COUNTERS  (top-left, below score box)
       Three columns in a glowing pink-border box.

    Args:
        cv           — tkinter.Canvas.
        gs           — Game state dict.
        t            — Current game time in seconds.
        cfg          — settings_state.cfg singleton (reads difficulty,
                       num_lanes, music_volume).
        audio        — AudioPlayer singleton (reads position()).
        img_refs     — BGYOGame.img_refs dict (cover art cache).
        game_volume  — Current in-game music volume [0,1].
        w, h         — Canvas dimensions.
        sx_fn        — Horizontal scaling: sx_fn(x) = int(x × w / BASE_W).
        sy_fn        — Vertical scaling:   sy_fn(y) = int(y × h / BASE_H).
        _HIT_DEPTH   — Hit bar depth (unused here, kept for completeness).
        find_cover_fn — file_helpers.find_cover callable.
        PIL_OK        — True if Pillow is available for image loading.
        load_cover_fn — BGYOGame._load_cover_image callable.
    """
    LANES = gs.get("num_lanes", 5)

    # ── 1. Progress bar ───────────────────────────────────────────
    if gs.get("song_duration") and gs["song_duration"] > 0 and gs.get("song_wall_start"):
        prog = min(1.0, audio.position() / gs["song_duration"])
    else:
        prog = 0.0

    # Semi-transparent dark track
    cv.create_rectangle(0, 0, w, 7, fill="#0a0020", outline="")
    # Progress fill: warm gold → dark as the song progresses
    pr = max(0, min(255, int(255 * prog)))
    pg = max(0, min(255, int(215 * (1 - prog))))
    cv.create_rectangle(0, 0, int(w * prog), 7,
                        fill=f"#{pr:02x}{pg:02x}00")

    # ── 2. Score box (top-left) ───────────────────────────────────
    _sc_pulse = 0.45 + 0.25 * math.sin(t * 2.1)
    cv.create_rectangle(sx_fn(11), 13, sx_fn(193), 77,
                        fill="", outline=dim("#FFD700", _sc_pulse * 0.40), width=2)
    cv.create_rectangle(sx_fn(12), 14, sx_fn(192), 76,
                        fill="#030010", outline=dim("#FFD700", _sc_pulse * 0.70), width=1)
    cv.create_text(sx_fn(22), 22,
                   text="SCORE", fill=dim("#FFD700", 0.75),
                   anchor="w", font=(UI_FONT, 9, "bold"))
    # Two-pass number: black shadow then white face
    cv.create_text(sx_fn(23), 51,
                   text=f"{gs['score']:,}", fill="#000000",
                   anchor="w", font=(MONO_FONT, 21, "bold"))
    cv.create_text(sx_fn(22), 50,
                   text=f"{gs['score']:,}", fill="#FFFFFF",
                   anchor="w", font=(MONO_FONT, 21, "bold"))

    # ── 3. Song title / cover box (top-centre) ────────────────────
    names    = gs.get("song_names", [])
    idx      = gs.get("song_idx", 0)
    cur_song = names[idx % max(1, len(names))] if names else ""
    song_lbl = cur_song.upper() if cur_song else "—"

    hud_cx      = w // 2
    cover_size  = 48
    box_w       = 400
    box_h       = 58
    hud_box_x1  = hud_cx - box_w // 2
    hud_box_x2  = hud_cx + box_w // 2

    # Cycling border colour matching the LED title text
    _hb_phase  = (t * 0.55) % 1.0
    _hb_i0     = int(_hb_phase * len(MEMBER_COLORS)) % len(MEMBER_COLORS)
    _hb_i1     = (_hb_i0 + 1) % len(MEMBER_COLORS)
    _hb_frac   = (_hb_phase * len(MEMBER_COLORS)) - _hb_i0
    hud_border = blend(MEMBER_COLORS[_hb_i0], MEMBER_COLORS[_hb_i1], _hb_frac)
    hud_pulse  = 0.55 + 0.30 * math.sin(t * 3.0)

    # Outer glow border + inner dark fill
    cv.create_rectangle(hud_box_x1 - 1, 13, hud_box_x2 + 1, 14 + box_h + 1,
                        fill="", outline=dim(hud_border, hud_pulse * 0.45), width=2)
    cv.create_rectangle(hud_box_x1, 14, hud_box_x2, 14 + box_h,
                        fill="#030010", outline=dim(hud_border, hud_pulse * 0.80), width=1)

    # Cover art — left side of the HUD box
    cover_x = hud_box_x1 + 8 + cover_size // 2
    cover_y  = 14 + box_h // 2
    if cur_song:
        hud_key = f"hud_cover_{cur_song}"
        if hud_key not in img_refs:
            # Load + cache the cover at HUD thumbnail size
            cp = find_cover_fn(cur_song)
            if cp and PIL_OK:
                try:
                    from PIL import Image, ImageTk
                    im = Image.open(cp).convert("RGBA")
                    im = im.resize((cover_size, cover_size), Image.Resampling.LANCZOS)
                    img_refs[hud_key] = ImageTk.PhotoImage(im)
                except Exception:
                    img_refs[hud_key] = load_cover_fn(cur_song, cover_size)
            else:
                img_refs[hud_key] = load_cover_fn(cur_song, cover_size)
        ph = img_refs.get(hud_key)
        if ph:
            try:
                cv.create_image(cover_x, cover_y, image=ph, anchor="center")
            except Exception:
                pass

    # LED song title — colour cycles through member colours
    text_area_x  = hud_box_x1 + cover_size + 18
    text_area_cx = (text_area_x + hud_box_x2) // 2
    text_area_w  = hud_box_x2 - text_area_x - 6

    _lc_phase = (t * 0.55) % 1.0
    _lc_i0    = int(_lc_phase * len(MEMBER_COLORS)) % len(MEMBER_COLORS)
    _lc_i1    = (_lc_i0 + 1) % len(MEMBER_COLORS)
    _lc_frac  = (_lc_phase * len(MEMBER_COLORS)) - _lc_i0
    led_col   = blend(MEMBER_COLORS[_lc_i0], MEMBER_COLORS[_lc_i1], _lc_frac)

    # Shadow then LED-coloured title
    cv.create_text(text_area_cx + 1, 34 + 1, text=song_lbl,
                   fill=dim("#000000", 0.80),
                   font=(UI_FONT, 13, "bold"), anchor="center",
                   width=text_area_w)
    cv.create_text(text_area_cx, 34, text=song_lbl,
                   fill=led_col,
                   font=(UI_FONT, 13, "bold"), anchor="center",
                   width=text_area_w)

    # Status indicator — analysing / playing / no audio
    bm_col, bm_txt = (
        ("#FF8800", "⟳ ANALYSING") if gs.get("loading") else
        ("#00E5FF", "♪ PLAYING")    if gs.get("song_wall_start") else
        ("#555555", "◌ NO AUDIO")
    )
    cv.create_text(text_area_cx, 58, text=bm_txt, fill=bm_col,
                   font=(UI_FONT, 8, "bold"), anchor="center")

    # ── 4. Combo box (top-right) ──────────────────────────────────
    _cb_pulse = 0.45 + 0.25 * math.sin(t * 2.1 + 1.0)
    cv.create_rectangle(w - sx_fn(193), 13, w - sx_fn(11), 77,
                        fill="", outline=dim("#00E5FF", _cb_pulse * 0.40), width=2)
    cv.create_rectangle(w - sx_fn(192), 14, w - sx_fn(12), 76,
                        fill="#030010", outline=dim("#00E5FF", _cb_pulse * 0.70), width=1)
    cv.create_text(w - sx_fn(22), 22, text="COMBO",
                   fill=dim("#FFD700", 0.75),
                   anchor="e", font=(UI_FONT, 9, "bold"))
    cv.create_text(w - sx_fn(21), 51, text=str(gs["combo"]),
                   fill="#000000", anchor="e",
                   font=(MONO_FONT, 21, "bold"))
    cv.create_text(w - sx_fn(22), 50, text=str(gs["combo"]),
                   fill="#FFFFFF", anchor="e",
                   font=(MONO_FONT, 21, "bold"))

    # ── 5. Member display (bottom-centre) ─────────────────────────
    m   = gs["member_idx"] % len(C.MEMBER_NAMES)
    col = MEMBER_COLORS[m]
    pls = 0.70 + 0.30 * math.sin(t * 2.4)

    # Member name — shadow + coloured
    cv.create_text(w // 2 + 2, h - sy_fn(52) + 2,
                   text=C.MEMBER_NAMES[m],
                   fill=dim("#000000", 0.85),
                   font=(TITLE_FONT, 24, "bold"))
    cv.create_text(w // 2, h - sy_fn(52),
                   text=C.MEMBER_NAMES[m],
                   fill=dim(col, pls),
                   font=(TITLE_FONT, 24, "bold"))

    # Role subtitle
    cv.create_text(w // 2 + 1, h - sy_fn(28) + 1,
                   text=C.MEMBER_ROLES[m],
                   fill=dim("#000000", 0.75),
                   font=(UI_FONT, 9))
    cv.create_text(w // 2, h - sy_fn(28),
                   text=C.MEMBER_ROLES[m],
                   fill="#00E5FF", font=(UI_FONT, 9))

    # Difficulty badge (bottom-right)
    dc = {"Easy": "#00FF99", "Normal": "#00E5FF", "Hard": "#FFD700", "ACE": "#FF3385"}
    cv.create_text(w - 14, h - 50,
                   text=cfg.difficulty, anchor="se",
                   fill=dc.get(cfg.difficulty, "#fff"),
                   font=(UI_FONT, 10, "bold"))

    # Lane config label (bottom-right, below difficulty)
    lbl_map = {5: "5 LANES  D·F·J·K·L",
               4: "4 LANES  F·J·K·L",
               3: "3 LANES  F·J·L"}
    cv.create_text(w - 14, h - 32,
                   text=lbl_map.get(LANES, ""), anchor="se",
                   fill="#556677", font=(UI_FONT, 8, "bold"))

    # Control hints (bottom-left)
    cv.create_text(14, h - 50,
                   text="SPACE  Pause", anchor="sw",
                   fill="#445566", font=(UI_FONT, 8, "bold"))
    cv.create_text(14, h - 32,
                   text=f"♪ {int(game_volume * 100)}%   +/- Vol", anchor="sw",
                   fill="#445566", font=(UI_FONT, 8, "bold"))

    # ── 6. Perfect/Good/Miss counters (top-left, below score box) ─
    pgm_y     = sy_fn(88)
    pgm_val_y = pgm_y + 17
    _pgm_pulse = 0.40 + 0.20 * math.sin(t * 1.8 + 0.5)

    # Pink-border counter box
    cv.create_rectangle(11, pgm_y - 5, sx_fn(193), pgm_val_y + 16,
                        fill="", outline=dim("#FF3385", _pgm_pulse * 0.40), width=2)
    cv.create_rectangle(12, pgm_y - 4, sx_fn(192), pgm_val_y + 15,
                        fill="#030010", outline=dim("#FF3385", _pgm_pulse * 0.65), width=1)

    # Three columns: PERFECT (gold) | GOOD (cyan) | MISS (pink)
    for xi, lbl, val, fc in [
        (20,  "PERFECT", str(gs["perfect"]), "#FFD700"),
        (80,  "GOOD",    str(gs["good"]),    "#00E5FF"),
        (140, "MISS",    str(gs["miss"]),    "#FF3385"),
    ]:
        cv.create_text(xi, pgm_y, text=lbl,
                       fill=dim(fc, 0.85), anchor="w",
                       font=(UI_FONT, 8, "bold"))
        cv.create_text(xi + 1, pgm_val_y + 1, text=val,
                       fill="#000000", anchor="w",
                       font=(MONO_FONT, 13, "bold"))
        cv.create_text(xi, pgm_val_y, text=val,
                       fill="#FFFFFF", anchor="w",
                       font=(MONO_FONT, 13, "bold"))
