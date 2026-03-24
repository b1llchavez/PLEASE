import math
import random

from constants import (
    MEMBER_COLORS, BG_COL,
    dim, blend, additive_blend, spotlight_col,
    hex_to_rgb,
    W, H,
)


def _clamp(v: float) -> int:
    """Clamp a float to the integer range [0, 255] for RGB channel encoding."""
    return max(0, min(255, int(v)))


# ══════════════════════════════════════════════════════════════════
#  NOTE
# ══════════════════════════════════════════════════════════════════

class Note:
    """
    A single rhythm-game note travelling down one lane of the 3D track.

    Notes are created by bgyo_game._update_game() and projected onto
    the canvas each frame using constants.project(lane_cx, depth).

    Attributes:
        lane   (int)   — zero-based lane index [0, num_lanes).
                         Determines horizontal position via lane_cx().
        depth  (float) — current normalised depth [0.0 to 1.0+].
                         0 = far horizon (spawn point).
                         HIT_DEPTH ~0.84 = the hit bar position.
                         Notes beyond 1.0 are considered missed and
                         marked alive=False by bgyo_game._update_game().
        beat_t (float) — the original beat timestamp (seconds) from
                         audio_engine.analyse_beats().  Used by
                         bgyo_game._check_hit() to compute timing
                         accuracy: |audio.position() - beat_t| is
                         compared against the PERFECT/hit window widths
                         from constants.DIFFICULTY.
        alive  (bool)  — False when the note has been hit or missed.
                         bgyo_game filters out dead notes each frame
                         to avoid re-processing them.

    Per-frame update (done in bgyo_game._update_game()):
        note.depth += speed * dt
    where speed is DIFFICULTY[cfg.difficulty]["speed"] and dt is the
    frame delta-time in seconds.
    """

    def __init__(self, lane: int, beat_t: float = 0.0, depth: float = 0.0):
        self.lane   = lane
        self.depth  = depth    # starts at 0 (horizon), advances toward 1 (camera)
        self.beat_t = beat_t   # target audio timestamp for timing accuracy scoring
        self.alive  = True


# ══════════════════════════════════════════════════════════════════
#  PARTICLE
# ══════════════════════════════════════════════════════════════════

class Particle:
    """
    A single coloured square/star sprite used in hit-feedback bursts,
    SideEffect explosions, and confetti showers.

    ── PHYSICS MODEL ───────────────────────────────────────────────
    Each particle is launched at a random angle and speed, then
    subject to gravity (vy += 0.18 per frame) and slight air
    resistance (vx *= 0.99 per frame).  This produces the arc
    trajectory of a real confetti burst.

    ── DIRECTIONAL MODES ───────────────────────────────────────────
    The `side` parameter biases the launch angle for side-panel effects:

      side="left"   -> angles in [-0.4pi, 0.4pi] offset by -pi
                       (fires leftward, away from the stage)
      side="right"  -> angles in [-0.4pi, 0.4pi]
                       (fires rightward)
      side=None     -> full 360 degree random angle (general burst)

    ── BURST MODE ──────────────────────────────────────────────────
    burst=True  increases speed (4-10 vs 1.5-4.5) and gives a
    stronger upward bias (vy -= 3.0 vs -1.0) for a more explosive
    PERFECT-hit effect.  Particle size is also larger (5-12 vs 2-5).

    ── STAR MODE ───────────────────────────────────────────────────
    star=True marks this particle to be drawn as a 4-pointed star
    polygon by bgyo_game's render loop rather than a simple square.
    Used for PERFECT and COMBO side effects.

    Attributes:
        x, y    (float) — current screen position in pixels.
        vx, vy  (float) — velocity in pixels per frame.
        life    (float) — remaining life in [0.0, 1.0].  Starts at 1.0
                          and decreases by `decay` each frame.
        decay   (float) — life reduction per frame, randomised in
                          [0.018, 0.040] (~25-55 frame lifetime at 60 FPS).
        col     (str)   — '#RRGGBB' hex colour string.
        size    (int)   — half-width of the drawn square/star in pixels.
        star    (bool)  — draw as 4-pointed star if True, square if False.

    The step() method advances physics and returns True while alive,
    False once life <= 0 — allowing bgyo_game to filter with a list
    comprehension: self.particles = [p for p in self.particles if p.step()]
    """

    def __init__(self, x: float, y: float, col: str,
                 star: bool = False, burst: bool = False, side=None):
        # ── Launch angle selection ────────────────────────────────
        if side == "left":
            # Fire leftward: angles centred on -pi (pointing left),
            # spread +-0.4pi so particles fan out rather than flying in a line.
            ang = random.uniform(-math.pi * 0.4, math.pi * 0.4) - math.pi
        elif side == "right":
            # Fire rightward: angles centred on 0 (pointing right),
            # same +-0.4pi spread.
            ang = random.uniform(-math.pi * 0.4, math.pi * 0.4)
        else:
            # Omnidirectional burst — full 360 degrees for general hit sparks.
            ang = random.uniform(0, 2 * math.pi)

        # ── Speed and upward bias ─────────────────────────────────
        # burst=True: faster, higher launch for PERFECT explosions.
        # burst=False: gentle float for ambient confetti.
        spd     = random.uniform(4, 10) if burst else random.uniform(1.5, 4.5)
        self.x  = x
        self.y  = y
        self.vx = math.cos(ang) * spd
        self.vy = math.sin(ang) * spd - (3.0 if burst else 1.0)  # upward bias

        # ── Lifetime ──────────────────────────────────────────────
        self.life  = 1.0
        self.decay = random.uniform(0.018, 0.040)  # ~25-55 frames at 60 FPS

        # ── Visual properties ─────────────────────────────────────
        self.col  = col
        self.size = random.randint(5, 12) if burst else random.randint(2, 5)
        self.star = star   # True -> drawn as 4-pointed star by bgyo_game renderer

    def step(self) -> bool:
        """
        Advance physics by one frame and return True while alive.

        Physics per frame:
            x  += vx             (horizontal drift)
            y  += vy             (vertical drift, includes upward bias from init)
            vy += 0.18           (gravity — accelerates downward)
            vx *= 0.99           (very slight air resistance — slows horizontal)
            life -= decay        (fade-out counter)

        Returns False when life <= 0 so bgyo_game can remove dead
        particles in a single list-comprehension filter pass.
        """
        self.x    += self.vx
        self.y    += self.vy
        self.vy   += 0.18    # gravity pulls downward each frame
        self.vx   *= 0.99    # slight horizontal drag
        self.life -= self.decay
        return self.life > 0


# ══════════════════════════════════════════════════════════════════
#  SPARK
# ══════════════════════════════════════════════════════════════════

class Spark:
    """
    A fast-decaying point-light sprite for note-hit impact flashes.

    Sparks are visually similar to Particles but decay ~2.5x faster
    (decay range 0.045-0.095 vs 0.018-0.040) and have a stronger
    upward bias (vy -= 2.5 vs 1.0).  They appear and disappear in
    ~10-22 frames, giving a sharp, punchy hit-flash.

    Attributes: identical to Particle minus the `star` and `burst` flags.

    Physics per frame (in step()):
        x  += vx
        y  += vy
        vy += 0.25   (stronger gravity than Particle's 0.18 — sparks arc down faster)
        vx *= 0.96   (more air resistance — sparks decelerate horizontally faster)
        life -= decay

    Used by bgyo_game._on_hit() — spawned at the note's screen position
    on every successful keypress.
    """

    def __init__(self, x: float, y: float, col: str):
        ang      = random.uniform(0, 2 * math.pi)   # full 360 degree burst
        spd      = random.uniform(3, 9)              # faster than Particle
        self.x   = x
        self.y   = y
        self.vx  = math.cos(ang) * spd
        self.vy  = math.sin(ang) * spd - 2.5        # stronger upward bias than Particle
        self.life  = 1.0
        self.decay = random.uniform(0.045, 0.095)    # fast decay -> short lifespan
        self.col   = col
        self.size  = random.randint(2, 4)            # small sharp dots

    def step(self) -> bool:
        """
        Advance physics by one frame and return True while alive.

        Gravity (0.25) and air resistance (0.96) are stronger than
        Particle's values, making sparks arc and decelerate faster.
        """
        self.x    += self.vx
        self.y    += self.vy
        self.vy   += 0.25    # stronger gravity than Particle
        self.vx   *= 0.96    # more horizontal drag than Particle
        self.life -= self.decay
        return self.life > 0


# ══════════════════════════════════════════════════════════════════
#  FLASH
# ══════════════════════════════════════════════════════════════════

class Flash:
    """
    Floating score-feedback text that rises and fades after a hit.

    Typical messages: "+100 PERFECT", "+50 GOOD", "MISS".
    Spawned by bgyo_game._on_hit() at the screen position of the
    hit note, directly above the hit bar.

    ── ANIMATION ───────────────────────────────────────────────────
    Each frame (via step(dt)):
        life -= dt x 1.5    (fades in ~0.67 seconds at 60 FPS)
        y    -= dt x 42     (rises upward at 42 pixels/second)

    bgyo_game draws the text with opacity proportional to `life` by
    using dim(col, life) so the text fades from full colour to black.

    Attributes:
        text  (str)   — display string shown on canvas.
        x, y  (float) — current screen coordinates (y decreases each frame).
        col   (str)   — '#RRGGBB' hex colour (e.g. "#FFD700" for PERFECT).
        size  (int)   — font size in points.
        bold  (bool)  — whether to render in bold weight.
        life  (float) — remaining life [0.0, 1.0]; used for opacity.
    """

    def __init__(self, text: str, x: float, y: float,
                 col: str, size: int = 18, bold: bool = True):
        self.text = text
        self.x    = x
        self.y    = float(y)
        self.col  = col
        self.size = size
        self.bold = bold
        self.life = 1.0       # starts fully opaque

    def step(self, dt: float) -> bool:
        """
        Advance animation by `dt` seconds and return True while visible.

        dt is the frame delta-time in seconds (typically ~0.0167 at 60 FPS).

        The life decay rate (x1.5) means a Flash lives for ~0.67 seconds,
        which is long enough to read but short enough to clear the screen
        before the next hit.
        """
        self.life -= dt * 1.5   # fade rate: fully transparent in ~0.67 s
        self.y    -= dt * 42    # rise rate: moves 42 pixels upward per second
        return self.life > 0


# ══════════════════════════════════════════════════════════════════
#  SIDE EFFECT
# ══════════════════════════════════════════════════════════════════

class SideEffect:
    """
    A short-lived event that fires a particle burst in one of the
    side panels (left or right of the track) and displays a label.

    SideEffects are used for PERFECT, COMBO milestone, and MISS events.
    Each instance manages its own Particle list and a label string.

    ── KINDS ────────────────────────────────────────────────────────
      "perfect" — 30 large burst-star particles in member colours,
                  label "PERFECT!" in green (#00FF99).
      "combo"   — 20 medium star particles in gold/white/pink,
                  label "COMBO!" in gold (#FFD700).
      "miss"    — 12 small plain particles in pink (#FF3385),
                  label "MISS" in pink.

    ── PARTICLE POSITIONING ─────────────────────────────────────────
    Particles are spawned vertically scattered across the middle 44%
    of the screen height (28%-72% for perfect/combo, 35%-65% for miss),
    at x=60 (left panel) or x=W-60 (right panel).  The `side` parameter
    on each Particle constructor biases the launch angle to fire away
    from the track centre.

    ── LIFETIME ─────────────────────────────────────────────────────
    self.life starts at 1.0 and decreases by 0.025 per step() call
    (~40 frames / ~0.67 seconds at 60 FPS).  step() also advances
    each child Particle and removes dead ones.  The SideEffect is
    considered expired when life <= 0 (even if some Particles remain
    alive — their visual contribution is negligible at that point).

    Attributes:
        side       (str)  — "left" or "right".
        kind       (str)  — "perfect", "combo", or "miss".
        life       (float)— remaining lifetime [0.0, 1.0].
        label      (str)  — text to display in the side panel (or None).
        label_col  (str)  — '#RRGGBB' colour for the label text.
        particles  (list) — list of active Particle children.

    Called by bgyo_game._on_hit() for PERFECT/GOOD hits and by
    bgyo_game._on_miss() for MISS events.
    """

    def __init__(self, side: str, kind: str, w: int = None, h: int = None):
        # Use supplied dimensions or fall back to module-level W/H.
        # bgyo_game passes _W, _H (which may differ from W/H in fullscreen).
        _w = w or W
        _h = h or H

        self.side = side
        self.kind = kind
        self.life = 1.0
        self.particles = []

        # ── Label setup ───────────────────────────────────────────
        # label and label_col are read by bgyo_game's side-panel
        # renderer to display the event name alongside the particles.
        if kind == "perfect":
            self.label     = "PERFECT!"
            self.label_col = "#00FF99"   # green — positive reinforcement
        elif kind == "combo":
            self.label     = "COMBO!"
            self.label_col = "#FFD700"   # gold — celebratory
        elif kind == "miss":
            self.label     = "MISS"
            self.label_col = "#FF3385"   # pink — negative feedback
        else:
            self.label     = None
            self.label_col = "#FFFFFF"

        # ── Spawn position ────────────────────────────────────────
        # Horizontally: 60 px from the relevant screen edge, inside
        # the side panel area that sits outside the track boundaries.
        x = 60 if side == "left" else _w - 60

        # ── Particle burst by kind ────────────────────────────────
        if kind == "perfect":
            # 30 large coloured burst-stars — most spectacular effect.
            # Spread across the middle 44% of screen height.
            cols = ["#FFD700", "#FF3385", "#00FF99", "#00E5FF", "#FF8800"]
            for _ in range(30):
                self.particles.append(
                    Particle(
                        x,
                        random.randint(int(_h * 0.28), int(_h * 0.72)),
                        random.choice(cols),
                        star=True,    # draw as 4-pointed star
                        burst=True,   # fast launch, large size
                        side=side,    # fire away from track centre
                    )
                )

        elif kind == "combo":
            # 20 medium gold/white/pink stars — celebratory but less
            # intense than PERFECT, appropriate for combo milestones.
            cols = ["#FFD700", "#FFFFFF", "#FF3385"]
            for _ in range(20):
                self.particles.append(
                    Particle(
                        x,
                        random.randint(int(_h * 0.35), int(_h * 0.65)),
                        random.choice(cols),
                        star=True,    # star shape for visual clarity
                        side=side,
                    )
                )

        elif kind == "miss":
            # 12 small plain pink circles — subtle negative feedback,
            # enough to register without being distracting.
            for _ in range(12):
                self.particles.append(
                    Particle(
                        x,
                        random.randint(int(_h * 0.35), int(_h * 0.65)),
                        "#FF3385",    # Akira's pink — matches MISS label colour
                        side=side,
                    )
                )

    def step(self) -> bool:
        """
        Advance the SideEffect by one frame.

        Decrements self.life by 0.025 (~40-frame lifetime at 60 FPS).
        Calls step() on each child Particle and removes those that
        have expired (life <= 0).

        Returns True while self.life > 0 so bgyo_game can filter:
            self.side_effects = [e for e in self.side_effects if e.step()]
        """
        self.life      -= 0.025
        # Advance particles; keep only those still alive
        self.particles  = [p for p in self.particles if p.step()]
        return self.life > 0


# ══════════════════════════════════════════════════════════════════
#  SPOTLIGHT
# ══════════════════════════════════════════════════════════════════

class Spotlight:
    """
    A single concert stage spotlight that sweeps across the floor.

    Eight Spotlight instances are created by BGYOGame.__init__() and
    persist for the entire game session.  They are drawn on every
    screen that calls _draw_spotlights() — home screen and gameplay.

    ── VISUAL DESIGN ────────────────────────────────────────────────
    Each spotlight renders two independent elements:

    1. STAGE BEAM (cone from rig to floor)
       Four overlapping polygon layers, each slightly narrower and
       brighter toward the centre:
           Layer 1 (outermost, hw x 1.00): barely visible — soft halo
           Layer 2             (hw x 0.60): dim mid-scatter
           Layer 3             (hw x 0.28): brighter inner cone
           Layer 4 (core,      hw x 0.10): brightest centre — still soft

       The overlapping polygons produce a gradient falloff from the
       beam edge to the core without needing true transparency support
       from tkinter (which only supports solid fills or no fill).

    2. FLOOR GLOW (radial line burst at beam tip)
       28 outward rays + 16 shorter inner rays drawn as 1-2 px lines
       radiating from the beam tip in an elliptical footprint.

       Why lines instead of filled ovals?
       tkinter's create_oval always paints a solid fill region.  Stacking
       multiple semi-dark ovals at low brightness repaints the background
       with a slightly-different dark colour that reads as an opaque disc —
       the "dark circle" artefact.  Lines have no fill, so they only add
       light to the scene, exactly like real stage floor pools.

       Footprint geometry:
           pool_rx = 110-145 px (animated horizontal radius)
           pool_ry = pool_rx x 0.20 (squashed vertically for floor perspective)

    ── ANIMATION ────────────────────────────────────────────────────
    The beam sweeps left and right via a sine wave:
        swing_x(t) = base_x + sin(t x speed + phase) x amp

    where base_x, speed, amp, and phase are all randomised at
    construction so each of the 8 spotlights moves independently,
    producing an organic-looking overlapping sweep pattern.

    Brightness also oscillates sinusoidally:
        brightness = 0.75 + 0.40 x |sin(t x speed x 1.6 + phase)|
        clamped to [0.65, 1.0]

    This keeps the beams visible at all times (min 0.65) while still
    providing a lively pulsing effect.

    ── COLOUR ───────────────────────────────────────────────────────
    Each spotlight's colour is MEMBER_COLORS[index % 5], so the 8
    beams cycle through all five member colours with indices 5-7
    repeating gold, pink, and cyan.

    Attributes:
        index   (int)   — spotlight index [0, 7]; determines colour.
        phase   (float) — random initial phase for swing/brightness sine waves.
        speed   (float) — sweep speed in radians/second [0.18, 0.42].
        amp     (float) — swing amplitude as fraction of screen width [0.10, 0.24].
        col     (str)   — '#RRGGBB' beam colour from MEMBER_COLORS.
        base_x  (float) — normalised centre of the sweep path [0.10, 0.90].
                          Evenly distributed: 0.10 + (index/7) x 0.80
    """

    MIN_B = 0.008   # Absolute minimum brightness for any beam layer.
                    # Prevents fully-black polygons which would paint
                    # opaque black over the background instead of being
                    # visually transparent (tkinter has no true alpha).

    def __init__(self, index: int):
        self.index  = index
        self.phase  = random.uniform(0, math.pi * 2)       # random start phase
        self.speed  = random.uniform(0.18, 0.42)           # sweep speed (rad/s)
        self.amp    = random.uniform(0.10, 0.24)           # swing amplitude (normalised)
        self.col    = MEMBER_COLORS[index % len(MEMBER_COLORS)]

        # Evenly space base positions from 10% to 90% of screen width
        # so beams fan across the full stage rather than clustering.
        self.base_x = 0.10 + (index / 7) * 0.80


    def _swing_x(self, t: float) -> float:
        """
        Return the current normalised x-position of the beam tip [0, 1].

        Formula:  base_x + sin(t x speed + phase) x amp

        Sine oscillation makes the beam sweep left and right smoothly.
        Each spotlight's unique (speed, phase, amp) combination means
        no two beams are in sync, producing natural-looking overlapping
        movements.
        """
        return self.base_x + math.sin(t * self.speed + self.phase) * self.amp


    def draw(self, cv, t: float, w: int, h: int):
        """
        Render this spotlight onto canvas `cv` for time `t`.

        Args:
            cv  — tkinter Canvas widget (bgyo_game passes self.cv).
            t   — current animation time in seconds (bgyo_game.t).
            w   — current canvas width in pixels (_W).
            h   — current canvas height in pixels (_H).

        Drawing order:
            1. Four beam-cone polygon layers (far to near / dim to bright).
            2. Hairline core ray (1-px line from rig to tip).
            3. 28 outer floor-glow rays (full ellipse footprint).
            4. 16 inner floor-glow rays (brighter hotspot, 35% radius).
            5. Single 3-px white hotspot dot at beam tip.
        """
        # ── Geometry ──────────────────────────────────────────────
        # tip: where the beam hits the floor (screen coordinates).
        tip_x = w * self._swing_x(t)
        tip_y = h * 0.88       # floor level — 88% down from top (v18.2: raised from 95%)

        # ox, oy: the rig (origin of the beam) at the top of the screen.
        # Offset horizontally based on index so rig positions fan out.
        ox = w * (0.20 + self.index * 0.085)
        oy = 0                 # rig sits at the very top edge

        # ── Brightness animation ──────────────────────────────────
        # Oscillates between 0.65 and 1.0 — never dims too much (v18.2).
        brightness = 0.75 + 0.40 * abs(
            math.sin(t * self.speed * 1.6 + self.phase)
        )
        brightness = max(0.65, min(1.0, brightness))

        # Beam half-width at the floor, animated to pulse slightly.
        hw = 80 + 40 * abs(math.sin(t * self.speed + self.phase + 0.7))

        # ── 1. Stage beam cone ────────────────────────────────────
        # Four polygon layers, from outermost/faintest to innermost/brightest.
        # Each layer is a trapezoid: narrow at the rig (top), wide at the floor.
        # Overlapping them creates a soft gradient from edge to core without
        # true alpha compositing.
        #
        # hw_frac : fraction of `hw` used as the half-width of this layer.
        # b_frac  : brightness multiplier for this layer's fill colour.
        # Reduced from 4 layers to 3 for performance; visual quality maintained.
        layers = [
            (1.00, max(self.MIN_B, brightness * 0.06)),   # outermost — soft halo
            (0.35, max(self.MIN_B, brightness * 0.22)),   # mid glow
            (0.10, max(self.MIN_B, brightness * 0.48)),   # core — bright but still soft
        ]

        # The rig end of each polygon is narrowed by ORIGIN_SPREAD so
        # all layers converge to a thin point at the light fixture rather
        # than spreading unrealistically wide at the source.
        ORIGIN_SPREAD = 0.04
        for hw_frac, b_frac in layers:
            half   = hw * hw_frac
            o_half = half * ORIGIN_SPREAD   # narrow top width
            # Polygon vertices: top-left, bottom-left, bottom-right, top-right
            pts = [
                ox - o_half, oy,        # top-left (rig, left edge)
                tip_x - half,  tip_y,   # bottom-left (floor, left edge)
                tip_x + half,  tip_y,   # bottom-right (floor, right edge)
                ox + o_half,   oy,      # top-right (rig, right edge)
            ]
            cv.create_polygon(pts,
                              fill=spotlight_col(self.col, b_frac),
                              outline="")

        # ── 2. Hairline core ray ──────────────────────────────────
        # A single thin line from rig to tip gives the beam a crisp
        # optical centre without adding solid fill mass.
        core_intensity = brightness * 0.90
        cv.create_line(
            ox, oy, tip_x, tip_y,
            fill=additive_blend(BG_COL, "#ffffff", core_intensity * 0.15),
            width=max(1, int(hw * 0.03)),
        )

        # ── 3. Floor glow — outer rays ────────────────────────────
        # 28 rays spread in a full ellipse around the beam tip.
        # The ellipse is squashed vertically (ry = rx x 0.20) to
        # simulate the floor perspective foreshortening.
        pool_rx = 110 + 35 * abs(math.sin(t * self.speed + self.phase + 0.3))
        pool_ry = pool_rx * 0.20   # vertical squash for floor perspective

        # Parse beam colour once for the inner loop — avoids repeated
        # hex_to_rgb calls inside the tight 28-iteration loop.
        r0, g0, b0 = hex_to_rgb(self.col)

        # Reduced from 28 to 18 outer rays — still produces a full, smooth
        # glow pool on the floor while cutting Canvas line calls by ~36%.
        NUM_RAYS = 18
        for ri in range(NUM_RAYS):
            # Distribute rays evenly around the full ellipse
            ang = (ri / NUM_RAYS) * 2 * math.pi

            # End point on the squashed ellipse footprint
            ex = tip_x + math.cos(ang) * pool_rx
            ey = tip_y + math.sin(ang) * pool_ry

            # Rays facing sideways (cos ~= 1) are brighter; top/bottom
            # rays (cos ~= 0) are dimmer.  This creates the natural "wider
            # than tall" appearance of a real floor light pool.
            side_weight = abs(math.cos(ang))   # 1 at sides, 0 at top/bottom
            ray_b = brightness * (0.10 + 0.22 * side_weight)

            rr = _clamp(r0 * ray_b)
            gg = _clamp(g0 * ray_b)
            bb = _clamp(b0 * ray_b)
            cv.create_line(tip_x, tip_y, ex, ey,
                           fill=f"#{rr:02x}{gg:02x}{bb:02x}",
                           width=1)   # 1px — adds light without filling area

        # ── 4. Floor glow — inner rays (hotspot) ─────────────────
        # 10 shorter, brighter rays at 35% of the outer radius (reduced from 16
        # for performance; the hotspot centre still reads clearly with 10 rays).
        # Phase-offset by half a step so inner rays fall between outer rays.
        NUM_INNER = 10
        for ri in range(NUM_INNER):
            ang = (ri / NUM_INNER) * 2 * math.pi + (math.pi / NUM_INNER)
            ex  = tip_x + math.cos(ang) * pool_rx * 0.35
            ey  = tip_y + math.sin(ang) * pool_ry * 0.35
            inner_b = brightness * 0.32   # brighter than outer rays
            rr = _clamp(r0 * inner_b)
            gg = _clamp(g0 * inner_b)
            bb = _clamp(b0 * inner_b)
            cv.create_line(tip_x, tip_y, ex, ey,
                           fill=f"#{rr:02x}{gg:02x}{bb:02x}",
                           width=2)   # 2px for a slightly warmer hotspot

        # ── 5. Hotspot dot at beam tip ────────────────────────────
        # A tiny 3-px white horizontal line at the exact beam tip.
        # create_line with width=3 gives a rounded dot without the
        # fill-region problem of create_oval.
        dot_b = brightness * 0.55
        wr    = _clamp(int(255 * dot_b))
        cv.create_line(
            tip_x - 1, tip_y, tip_x + 1, tip_y,
            fill=f"#{wr:02x}{wr:02x}{wr:02x}",
            width=3,
        )
