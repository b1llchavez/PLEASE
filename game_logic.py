import math
import random
import time

import constants as C
from constants import (
    MEMBER_COLORS,
    DIFFICULTY,
    HIT_DEPTH,
    W, H,
    lane_cx,
)
from game_objects import Note, Particle, Spark, Flash, SideEffect


# ══════════════════════════════════════════════════════════════════
#  GAME STATE FACTORY
# ══════════════════════════════════════════════════════════════════

def make_game_state(song_names: list, song_paths: dict,
                    is_endless: bool, num_lanes: int,
                    difficulty_name: str) -> dict:
    """
    Create and return a fresh game state dict for a new session.

    All counters start at zero; timing fields start at their pre-game
    values; the notes list is empty (notes are added by update_game()).

    Args:
        song_names      — Ordered list of song title strings to play.
                          In endless/shuffle mode this is the full
                          shuffled playlist; in single-song mode it
                          is a one-element list.
        song_paths      — Dict mapping song_name → absolute MP3 path.
                          May be empty if no files are on disk; the
                          game falls back to random-note mode.
        is_endless      — True = advance to next song after each ends;
                          False = call _end_game() after the song.
        num_lanes       — Active lane count (3, 4, or 5).
        difficulty_name — Key into constants.DIFFICULTY (e.g. "Normal").

    Returns:
        A dict conforming to the game state schema described in the
        module docstring.

    Called by:
        bgyo_game.BGYOGame._start_game()
    """
    d = DIFFICULTY[difficulty_name]
    return {
        # ── Scoring ───────────────────────────────────────────────
        "score":     0,      # Cumulative score for this session
        "combo":     0,      # Current unbroken hit streak
        "max_combo": 0,      # Highest combo reached this session
        "perfect":   0,      # Count of PERFECT hits
        "good":      0,      # Count of GOOD hits
        "miss":      0,      # Count of missed notes (key or scroll)

        # ── Active notes ──────────────────────────────────────────
        "notes":      [],    # List of live Note objects

        # ── Session timing ────────────────────────────────────────
        "elapsed":    0.0,   # Total unpaused seconds elapsed this session

        # ── Song playback ─────────────────────────────────────────
        "endless":         is_endless,
        "song_names":      song_names,
        "song_paths":      song_paths,
        "song_idx":        0,         # Index into song_names for the current song
        "current_mp3":     None,      # Absolute path of the currently playing MP3
        "song_wall_start": None,      # wall time when gameplay audio started
        "song_duration":   None,      # duration of the current track in seconds

        # ── Countdown (pre-song) ─────────────────────────────────
        # A 3-second countdown displays before audio starts so the
        # player has time to get ready.  countdown decrements each
        # frame; when it reaches 0, audio.play() is called.
        "countdown":    0.0,   # Seconds remaining in pre-song countdown
        "countdown_go": 0.0,   # Short "GO!" flash timer after countdown ends

        # ── Note timing (random fallback mode) ────────────────────
        # Used when no beat chart is available (librosa unavailable or
        # analysis failed).  note_timer counts down to the next spawn.
        "note_timer":  1.5,              # Seconds until next random note spawn
        "ival_min":    d["ival_min"],    # Minimum gap between spawns
        "ival_max":    d["ival_max"],    # Maximum gap between spawns

        # ── Difficulty tuning ─────────────────────────────────────
        "speed":    d["speed"],    # Note travel speed (depth-units/second)
        "hit_win":  d["hit"],      # GOOD hit window half-width (depth-units)
        "perf_win": d["perf"],     # PERFECT hit window half-width (depth-units)

        # ── Beat chart (beat-sync mode) ───────────────────────────
        "beat_chart":   [],    # List of (spawn_t, lane, beat_t) from build_beat_chart()
        "chart_cursor": 0,     # Index of the next unspawned chart entry
        "beat_mode":    False, # True when a beat chart is loaded and active
        "loading":      False, # True while beat analysis is running in background

        # ── Visual feedback ───────────────────────────────────────
        "combo_flash":   0.0,  # Countdown timer for combo burst text (seconds)
        "member_idx":    0,    # Index of the currently displayed member (0–4)
        "member_timer":  0.0,  # Time since last member rotation (rotates every 8 s)

        # ── State flags ───────────────────────────────────────────
        "paused":      False,  # True when gameplay is paused (SPACE or menu)
        "menu_paused": False,  # True when paused specifically by the in-game menu
                               # (suppresses the on-canvas PAUSED text)
        "ended":       False,  # True once _end_game() has been called
        "advancing":   False,  # True while transitioning to the next song
                               # (prevents double-advance on rapid song ends)

        # ── Layout ────────────────────────────────────────────────
        "num_lanes": num_lanes,   # Passed through so renderers don't need cfg
    }


# ══════════════════════════════════════════════════════════════════
#  MAIN UPDATE  (called every frame while screen == "game")
# ══════════════════════════════════════════════════════════════════

def update_game(gs: dict, dt: float, audio,
                particles: list, sparks: list,
                flashes: list, side_effects: list,
                w: int, h: int,
                _project, _HIT_DEPTH: float) -> None:
    """
    Advance all gameplay systems by one frame (dt seconds).

    This is the heart of the game loop — called every frame from
    bgyo_game._update_game() while screen == "game" and the session
    is not paused or ended.

    Mutates gs, particles, sparks, flashes, and side_effects in place.
    Returns None; callers read the updated state from those same objects.

    ── SYSTEMS UPDATED EACH FRAME ───────────────────────────────────

    1. Session timers
       elapsed, member_timer (rotates displayed member every 8 s),
       countdown (pre-song), countdown_go ("GO!" flash after countdown).

    2. Note spawning
       Beat-chart mode: spawns notes whose spawn_t ≤ virtual_song_pos.
       Random fallback:  spawns notes on note_timer countdown.

    3. Note depth advance
       Beat-chart mode: depth recalculated from audio.position() each
           frame — keeps notes visually locked to the music.
       Random mode: depth += speed × dt (linear advance).

    4. Auto-miss detection
       Notes that scroll past depth 1.08 (8% past the near edge) are
       automatically marked as misses — combo resets, MISS flash + side
       effects are spawned, but no score penalty (key-press misses do
       deduct score via hit_lane()).

    5. Particle / effect step-and-cull
       All particle, spark, flash, and side_effect lists are advanced
       and dead objects removed in a single list comprehension pass.
       Side effects are hard-capped at 8 to prevent runaway counts on
       dense ACE-difficulty sessions.

    6. Song-end detection
       When audio.is_busy() returns False after the song has been
       playing for at least 3 s (guards against false positives at
       the very start), the advance_song or end_game callback is
       invoked depending on the `endless` flag.

    Args:
        gs            — Mutable game state dict (see make_game_state()).
        dt            — Elapsed seconds since the last frame (capped at
                        50 ms by the caller to handle pause/resume spikes).
        audio         — The audio_engine.AudioPlayer singleton.
        particles     — BGYOGame.particles list (mutated in place).
        sparks        — BGYOGame.sparks list (mutated in place).
        flashes       — BGYOGame.flashes list (mutated in place).
        side_effects  — BGYOGame.side_effects list (mutated in place).
        w, h          — Current canvas dimensions in pixels.
        _project      — The active projection function from constants.
        _HIT_DEPTH    — Current hit bar depth value from constants.

    Called by:
        bgyo_game.BGYOGame._update_game(dt)
    """
    if gs.get("ended") or gs.get("paused"):
        return   # nothing to update while paused or after end

    LANES = gs.get("num_lanes", 5)

    # ── 1. Session timers ─────────────────────────────────────────

    gs["elapsed"] += dt

    # Rotate the displayed BGYO member every 8 seconds.
    # member_idx cycles through 0–4 (or 0–num_lanes-1) to show each
    # member's name and role in the HUD.
    gs["member_timer"] += dt
    if gs["member_timer"] >= 8.0:
        gs["member_timer"] = 0.0
        gs["member_idx"]   = (gs["member_idx"] + 1) % LANES

    # ── 2 & 3. Countdown → audio start ───────────────────────────

    # virtual_song_pos is the effective audio position used for chart
    # lookup.  During the countdown it is negative (time before beat 0);
    # after countdown it mirrors audio.position().
    virtual_song_pos = 0.0

    if gs.get("countdown", 0) > 0:
        # Countdown is still ticking — audio has not started yet.
        gs["countdown"] -= dt
        # Negative position: e.g. countdown=1.5 → virtual_pos = -1.5
        virtual_song_pos = -gs["countdown"]

        if gs["countdown"] <= 0:
            # Countdown just expired — start the gameplay track.
            # audio.play() uses effective_music_volume() automatically.
            audio.play(gs["current_mp3"])
            gs["song_wall_start"] = time.time()
            gs["countdown_go"]    = 0.8   # "GO!" flash duration in seconds
            virtual_song_pos      = 0.0
    else:
        # Countdown finished — use live audio position.
        if gs.get("countdown_go", 0) > 0:
            gs["countdown_go"] -= dt   # decay the "GO!" flash

        virtual_song_pos = audio.position() if gs.get("song_wall_start") else 0.0

        # ── Song-end detection ────────────────────────────────────
        # audio.is_busy() returning False means the track ended.
        # The 3.0-second guard prevents a false positive if is_busy()
        # returns False during the brief gap between load and play().
        if (gs.get("song_wall_start")
                and not gs.get("advancing")
                and not gs.get("loading")
                and virtual_song_pos > 3.0
                and not audio.is_busy()):
            # Signal that we're advancing to prevent re-entry
            gs["advancing"] = True
            # The actual _advance_song() / _end_game() call is made by
            # bgyo_game because it needs access to UI callbacks.
            # game_logic sets gs["advancing"] as the trigger signal.

    # ── 4. Note spawning ──────────────────────────────────────────

    if gs["beat_mode"] and gs["beat_chart"]:
        # ── Beat-chart mode (librosa-synced) ──────────────────────
        # Walk through the chart and spawn any notes whose spawn_t
        # has been reached by the virtual song position.
        chart = gs["beat_chart"]
        cur   = gs["chart_cursor"]
        while cur < len(chart) and chart[cur][0] <= virtual_song_pos:
            _, lane, beat_t = chart[cur]
            # Place the note at the correct depth for its remaining
            # travel time so it arrives at HIT_DEPTH exactly at beat_t.
            #   time_left   = beat_t - current_pos
            #   start_depth = HIT_DEPTH - time_left × speed
            time_left   = beat_t - virtual_song_pos
            start_depth = _HIT_DEPTH - (time_left * gs["speed"])
            gs["notes"].append(Note(lane, beat_t, start_depth))
            cur += 1
        gs["chart_cursor"] = cur

    else:
        # ── Random fallback mode (no beat chart) ──────────────────
        # note_timer counts down; when it reaches 0 a random-lane
        # note is spawned and the timer resets to a random interval.
        if gs.get("countdown", 0) <= 0:
            gs["note_timer"] -= dt
            if gs["note_timer"] <= 0:
                gs["notes"].append(
                    Note(random.randint(0, LANES - 1), 0.0, 0.0)
                )
                gs["note_timer"] = random.uniform(
                    gs["ival_min"], gs["ival_max"]
                )

    # ── 5. Note depth advance + auto-miss ─────────────────────────

    speed_step = gs["speed"] * dt   # linear depth advance for random mode
    live_notes = []

    for n in gs["notes"]:
        if not n.alive:
            continue   # already hit or missed — skip

        if gs["beat_mode"]:
            # Recalculate depth from live audio position each frame.
            # This "rubber-bands" the note to the music: if a frame
            # takes longer than usual, the note jumps to where it
            # should be rather than falling behind.
            time_left = n.beat_t - virtual_song_pos
            n.depth   = _HIT_DEPTH - (time_left * gs["speed"])
        else:
            # Simple linear advance for random mode
            n.depth += speed_step

        if n.depth > 1.08:
            # Note scrolled past the near edge without being hit.
            # 1.08 gives an 8% buffer past the hit bar before marking
            # a miss, allowing for the note's visual radius at depth~1.
            n.alive = False
            gs["miss"]      += 1
            gs["combo"]      = 0
            gs["combo_flash"] = 0.0
            # Auto-miss shows a MISS flash at the centre of the screen
            # (not at the note's lane, since the note has scrolled off)
            flashes.append(Flash("MISS", w // 2, h * 0.38, "#FF3385", 14))
            side_effects.append(SideEffect("left",  "miss", w, h))
            side_effects.append(SideEffect("right", "miss", w, h))
        else:
            live_notes.append(n)

    gs["notes"] = live_notes

    # ── 6. Decay visual timers ────────────────────────────────────

    if gs["combo_flash"] > 0:
        gs["combo_flash"] -= dt   # decay the combo-burst display timer

    # ── 7. Step and cull all particle / effect lists ──────────────
    # Each step() call advances physics and returns False when dead.
    # The list comprehension simultaneously advances and filters.

    # Flashes are dt-based (see Flash.step docs); others are frame-based.
    particles[:]    = [p  for p  in particles    if p.step()]
    sparks[:]       = [s  for s  in sparks       if s.step()]
    flashes[:]      = [f  for f  in flashes      if f.step(dt)]
    side_effects[:] = [se for se in side_effects if se.step()]

    # Hard cap on side effects to prevent runaway particle counts during
    # ACE-difficulty dense beat sections where misses can pile up rapidly.
    if len(side_effects) > 8:
        # Keep only the 8 most recent (rightmost in the list)
        side_effects[:] = side_effects[-8:]


# ══════════════════════════════════════════════════════════════════
#  HIT DETECTION  (called on each key press in a lane)
# ══════════════════════════════════════════════════════════════════

def hit_lane(idx: int, gs: dict,
             particles: list, sparks: list,
             flashes: list, side_effects: list,
             w: int, h: int,
             _project, _HIT_DEPTH: float) -> None:
    """
    Process a key press in lane `idx` — find the best hittable note,
    score it, and spawn visual feedback.

    ── HIT DETECTION ALGORITHM ──────────────────────────────────────
    1. Scan all alive notes in `gs["notes"]` with lane == idx.
    2. For each candidate, compute the depth error:
           d = |note.depth - HIT_DEPTH|
    3. The "best" note is the one with the smallest d that is also
       within the hit window (d ≤ gs["hit_win"]).
    4. If no note qualifies → KEY MISS (score penalty, combo reset).
    5. If best note found:
       d ≤ gs["perf_win"] → PERFECT (+300 × combo_multiplier)
       else               → GOOD    (+100)

    ── SCORE FORMULA ────────────────────────────────────────────────
    PERFECT: pts = 300 × (1 + combo ÷ 10)
        At combo 0:   300 points
        At combo 10:  600 points
        At combo 50: 1800 points
        … reward sustained streaks significantly.

    GOOD: flat 100 points regardless of combo.

    KEY MISS: −50 points, floored at 0 (score can't go negative).

    ── VISUAL FEEDBACK ──────────────────────────────────────────────
    PERFECT:
        • Flash labels: "PERFECT!" + "+NNN" above the note
        • 5 star burst particles + 3 colour burst particles at note pos
        • 4 Sparks offset slightly from note pos + 2 white Sparks at pos
        • SideEffect "perfect" on both left and right edges

    GOOD:
        • Flash labels: "GOOD" + "+100" above the note
        • 3 burst particles + 2 Sparks at note pos
        (no SideEffect — GOOD is positive feedback but not a milestone)

    KEY MISS:
        • Flash label: "MISS" at the lane's hit bar position
        • 2 pink burst particles at the hit bar position
        • SideEffect "miss" on both edges

    Combo milestone side effects (at 10, 25, 50, 100, 200):
        • Flash label: "Nx COMBO!" at screen centre
        • SideEffect "combo" on both left and right edges

    Args:
        idx          — Zero-based lane index that was pressed.
        gs           — Mutable game state dict.
        particles    — BGYOGame.particles list (appended to).
        sparks       — BGYOGame.sparks list (appended to).
        flashes      — BGYOGame.flashes list (appended to).
        side_effects — BGYOGame.side_effects list (appended to).
        w, h         — Canvas dimensions in pixels.
        _project     — Active projection callable.
        _HIT_DEPTH   — Current hit bar depth value.

    Called by:
        bgyo_game.BGYOGame._hit_lane(idx)
    """
    LANES = gs.get("num_lanes", 5)

    # Project the hit bar position for this lane to screen coordinates.
    # px, py are used as the origin for all feedback effects.
    px, py, _ = _project(lane_cx(idx, LANES), _HIT_DEPTH)
    col = MEMBER_COLORS[idx % len(MEMBER_COLORS)]

    # ── Find the best hittable note in this lane ──────────────────
    best_note  = None
    best_error = 999.0   # depth error (smaller = better timing)

    for n in gs["notes"]:
        if n.lane == idx and n.alive:
            d = abs(n.depth - _HIT_DEPTH)
            if d < gs["hit_win"] and d < best_error:
                best_error = d
                best_note  = n

    if best_note:
        # ── A hittable note was found ─────────────────────────────
        best_note.alive = False   # consume the note

        if best_error < gs["perf_win"]:
            # ── PERFECT HIT ──────────────────────────────────────
            gs["perfect"] += 1
            # Score scales with combo: 300 base × (1 + combo÷10)
            # This makes maintaining a streak meaningfully rewarding.
            pts = 300 * (1 + gs["combo"] // 10)
            gs["score"] += pts

            # Flash labels: feedback text floats upward from the note
            flashes.append(Flash("PERFECT!", px, py - 110, col, 18))
            flashes.append(Flash(f"+{pts}",  px, py - 88,  "#FFFFFF", 13))

            # Particle burst — counts are tuned to be visually impactful
            # without lagging on dense ACE beats (reduced from original)
            for _ in range(5):
                particles.append(Particle(px, py, col, star=True, burst=True))
            for _ in range(3):
                ang_col = random.choice(
                    ["#FFD700", "#FF3385", "#00FF99", "#FFFFFF", "#00E5FF"]
                )
                particles.append(Particle(px, py, ang_col, star=False, burst=True))

            # Spark ring — small offsets create a scatter effect around
            # the note rather than all sparks originating from one point
            for _ in range(4):
                sx_ = px + random.uniform(-50, 50)
                sy_ = py + random.uniform(-15, 15)
                sparks.append(Spark(sx_, sy_, col))
            for _ in range(2):
                sparks.append(Spark(px, py, "#FFFFFF"))   # bright white centre sparks

            # Screen-edge explosions on every perfect hit
            side_effects.append(SideEffect("left",  "perfect", w, h))
            side_effects.append(SideEffect("right", "perfect", w, h))

        else:
            # ── GOOD HIT ─────────────────────────────────────────
            gs["good"]  += 1
            gs["score"] += 100   # flat reward — no combo multiplier for GOOD

            flashes.append(Flash("GOOD", px, py - 100, "#00E5FF", 14))
            flashes.append(Flash("+100", px, py - 80,  "#FFFFFF", 11))

            for _ in range(3):
                particles.append(Particle(px, py, col, star=False, burst=True))
            for _ in range(2):
                sparks.append(Spark(px, py, "#00E5FF"))

        # ── Combo bookkeeping (shared by PERFECT and GOOD) ────────
        gs["combo"] += 1
        if gs["combo"] > gs["max_combo"]:
            gs["max_combo"] = gs["combo"]

        # Trigger combo-burst display for combos ≥ 10
        if gs["combo"] >= 10:
            gs["combo_flash"] = 2.2   # seconds the burst text stays visible

        # Combo milestone announcements at these exact thresholds
        if gs["combo"] in (10, 25, 50, 100, 200):
            milestone_col = "#FF3385" if gs["combo"] >= 50 else "#FFD700"
            flashes.append(Flash(
                f"{gs['combo']}x COMBO!",
                w // 2, h * 0.45,
                milestone_col, 20, bold=True
            ))
            side_effects.append(SideEffect("left",  "combo", w, h))
            side_effects.append(SideEffect("right", "combo", w, h))

    else:
        # ── KEY MISS — no note in range ───────────────────────────
        # The player pressed a lane key but there was no hittable note
        # within hit_win depth of HIT_DEPTH in that lane.
        gs["miss"]       += 1
        gs["combo"]       = 0      # combo broken
        gs["combo_flash"] = 0.0    # clear any active combo display
        # Small score penalty to discourage button-mashing; floored at 0
        gs["score"]       = max(0, gs["score"] - 50)

        flashes.append(Flash("MISS", px, py - 95, "#FF3385", 14))
        for _ in range(2):
            particles.append(Particle(px, py, "#FF3385", burst=True))
        side_effects.append(SideEffect("left",  "miss", w, h))
        side_effects.append(SideEffect("right", "miss", w, h))


# ══════════════════════════════════════════════════════════════════
#  SCORING HELPERS
# ══════════════════════════════════════════════════════════════════

def calc_accuracy(gs: dict) -> int:
    """
    Compute the player's accuracy as an integer percentage [0, 100].

    Formula:
        accuracy = (perfect + good) / (perfect + good + miss) × 100

    The denominator is max'd with 1 to prevent division-by-zero when
    no notes have been registered yet.

    Args:
        gs — Game state dict.

    Returns:
        Integer accuracy percentage.

    Used by:
        bgyo_game._show_name_entry()  — result screen display
        bgyo_game._show_gameover()    — game-over screen display
        calc_grade()                  — for grade threshold check
    """
    total = gs["perfect"] + gs["good"] + gs["miss"]
    return int(((gs["perfect"] + gs["good"]) / max(total, 1)) * 100)


def calc_grade(gs: dict) -> tuple:
    """
    Compute the letter grade and associated display colour for a session.

    Grade thresholds (see module docstring for full table):
        S  — acc ≥ 95% AND perfect > 15  → gold   (#FFD700)
        A  — acc ≥ 85%                   → green  (#00FF99)
        B  — acc ≥ 70%                   → cyan   (#00E5FF)
        C  — acc ≥ 50%                   → orange (#FF8800)
        D  — acc < 50%                   → pink   (#FF3385)

    The S-rank additional requirement (perfect > 15) prevents a player
    from achieving S by only hitting 2 notes with 100% accuracy on a
    very sparse session.

    Args:
        gs — Game state dict.

    Returns:
        (grade: str, colour: str) — e.g. ("S", "#FFD700") or ("D", "#FF3385")

    Used by:
        bgyo_game._show_name_entry()  — result screen grade letter
        bgyo_game._show_gameover()    — game-over grade letter and message
    """
    acc = calc_accuracy(gs)

    if   acc >= 95 and gs["perfect"] > 15:
        return "S", "#FFD700"   # Legendary ACE performance
    elif acc >= 85:
        return "A", "#00FF99"   # Amazing
    elif acc >= 70:
        return "B", "#00E5FF"   # Great
    elif acc >= 50:
        return "C", "#FF8800"   # Keep practicing
    else:
        return "D", "#FF3385"   # The light keeps burning for you


def grade_message(grade: str) -> str:
    """
    Return the flavour text displayed on the game-over screen for a grade.

    Each message is themed around BGYO fandom / P-Pop culture for
    immersion.

    Args:
        grade — Single letter grade string ("S", "A", "B", "C", "D").

    Returns:
        A short motivational or celebratory message string.
    """
    messages = {
        "S": "LEGENDARY ACE Performance!",
        "A": "Amazing! You're a true ACE!",
        "B": "Great job — keep shining!",
        "C": "Keep practicing, ACE!",
        "D": "The light keeps burning for you!",
    }
    return messages.get(grade, "Keep going!")
