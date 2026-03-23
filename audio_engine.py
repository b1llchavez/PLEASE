import os, json, random, time
import constants as C
from constants import SONGS_DIR, PREVIEW_DIR, HIT_DEPTH


# ══════════════════════════════════════════════════════════════════
#  OPTIONAL LIBRARY SETUP
# ══════════════════════════════════════════════════════════════════

# pygame — runtime audio mixer.
# Initialised here at import time with settings tuned for low latency:
#   frequency=44100  — standard CD-quality sample rate
#   size=-16         — signed 16-bit samples (negative = signed)
#   channels=2       — stereo output
#   buffer=512       — small buffer for minimal input→sound latency
# If pygame is missing or the mixer fails to init, PYGAME_OK=False
# and every AudioPlayer method becomes a silent no-op.
try:
    import pygame
    pygame.mixer.init(frequency=44100, size=-16, channels=2, buffer=512)
    PYGAME_OK = True
    print("✓ pygame ready")
except Exception as e:
    PYGAME_OK = False
    print(f"✗ pygame unavailable: {e}")

# librosa — offline audio analysis for beat/onset detection.
# Only needed when generating a .beats cache file for a new song.
# If librosa is absent, analyse_beats() returns [] and the game falls
# back to the random-note spawner in bgyo_game._update_game().
try:
    import librosa
    LIBROSA_OK = True
    print("✓ librosa ready")
except ImportError:
    LIBROSA_OK = False
    print("✗ librosa not installed — beat sync disabled")


# ══════════════════════════════════════════════════════════════════
#  BEAT DETECTION PIPELINE
# ══════════════════════════════════════════════════════════════════

def analyse_beats(mp3_path: str) -> list:
    """
    Analyse an MP3 file and return a thinned list of beat/onset
    timestamps (in seconds) suitable for chart generation.

    The result is cached to  <mp3_path>.beats  as a JSON array of
    floats.  On subsequent calls the cached file is returned
    immediately, skipping the expensive librosa analysis (~5-30 s
    depending on track length and CPU).  The cache is invalidated by
    deleting the .beats file.

    Returns:
        list[float]  — sorted timestamps in seconds, or [] if librosa
                       is unavailable or analysis fails.

    ── PIPELINE STAGES ─────────────────────────────────────────────

    Stage 1 — Beat tracking  (librosa.beat.beat_track)
    ────────────────────────────────────────────────────
    Estimates the global tempo and snaps the detected beats to a
    regular tempo grid.  tightness=100 uses a *looser* snap than the
    default (300), so beats in P-Pop tracks with slight swing or rubato
    are still detected rather than being skipped because they fall
    slightly off the grid.

    Produces: beat_times  — list of float timestamps.

    Stage 2 — Onset detection  (librosa.onset.onset_detect)
    ─────────────────────────────────────────────────────────
    Detects individual note/drum transients by looking for sudden
    increases in the onset strength envelope.  Parameters tuned for
    P-Pop (120-150 BPM) content:

      hop_length = 512 frames   (~23 ms per frame at sr=22050)
      delta      = 0.07         Lower threshold → catches softer hits
                                 (e.g. soft synth stabs, vocal chops).
      wait       = max(1, int(MIN_BEAT_GAP × sr / hop))
                                Minimum frames between onsets, computed
                                from MIN_BEAT_GAP so it stays consistent
                                if the constant is tuned.
      pre_max/post_max = 2      Peak-picking window (frames before/after)
      pre_avg/post_avg = 3,4    Averaging window for adaptive threshold

    Produces: onset_times  — list of float timestamps.

    Stage 3 — Merge & deduplicate
    ──────────────────────────────
    beat_times and onset_times are merged and sorted.  Then a 30 ms
    dedup window collapses any two events within 30 ms of each other
    into just the earlier one.  This prevents double-notes that occur
    when beat_track and onset_detect both fire on the same drum hit
    at slightly different frame boundaries.

    Stage 4 — MIN_BEAT_GAP thinning
    ─────────────────────────────────
    After dedup, any event closer than MIN_BEAT_GAP seconds to the
    previous kept event is discarded.  MIN_BEAT_GAP (0.16 s ≈ 375 BPM)
    is far above any real P-Pop tempo, so this only removes artefacts
    — not real musical events.

    The thinned list is written to the .beats JSON cache and returned.

    Called by:
        bgyo_game._start_analysis_worker() — runs in a background thread
        so the UI stays responsive during the analysis delay.
    """
    if not LIBROSA_OK:
        return []

    # ── Stage 0: Cache check ──────────────────────────────────────
    # The cache file lives alongside the MP3 (e.g. "the_light.mp3.beats").
    # A valid cache is a non-empty JSON list of numbers.
    cache = mp3_path + ".beats"
    if os.path.exists(cache):
        try:
            with open(cache, "r") as f:
                data = json.load(f)
            if isinstance(data, list) and data:
                return data   # fast path — skip analysis entirely
        except Exception:
            pass   # corrupt or empty cache — fall through to re-analyse

    # ── Stage 1 & 2: Beat tracking + onset detection ─────────────
    try:
        gap = C.MIN_BEAT_GAP   # read live so tuning changes apply without clearing cache
        print(f"  Analysing: {os.path.basename(mp3_path)}  (gap={gap}s) …")

        # Load audio as mono at 22 050 Hz (librosa's default SR).
        # Mono reduces memory and CPU; 22 050 Hz is sufficient for
        # rhythm analysis (well above the Nyquist limit for drum hits).
        y, sr = librosa.load(mp3_path, sr=22050, mono=True)

        # ── Stage 1: Beat tracking ────────────────────────────────
        # tightness=100 → looser snap to tempo grid (default is 300).
        # Higher tightness enforces stricter alignment to the estimated
        # BPM; lower tightness lets beats land where the audio actually
        # peaks even if that's slightly off the grid.
        tempo, beat_frames = librosa.beat.beat_track(
            y=y, sr=sr, units="frames", tightness=100
        )
        beat_times = librosa.frames_to_time(beat_frames, sr=sr).tolist()

        # ── Stage 2: Onset detection ──────────────────────────────
        hop     = 512   # hop_length in samples (~23 ms per frame at 22 050 Hz)

        # Minimum frames between valid onsets, derived from MIN_BEAT_GAP
        # so it stays consistent if the constant is tuned.
        wait_fr = max(1, int(gap * sr / hop))

        onset_env    = librosa.onset.onset_strength(y=y, sr=sr, hop_length=hop)
        onset_frames = librosa.onset.onset_detect(
            onset_envelope=onset_env, sr=sr, units="frames",
            hop_length=hop,
            pre_max=2, post_max=2,   # peak-picking window: 2 frames before/after
            pre_avg=3, post_avg=4,   # adaptive-threshold averaging window
            delta=0.07,              # lower threshold → catches softer transients
            wait=wait_fr             # minimum gap between detected onsets
        )
        onset_times = librosa.frames_to_time(
            onset_frames, sr=sr, hop_length=hop
        ).tolist()

        # ── Stage 3: Merge and deduplicate ────────────────────────
        # Combine both timestamp lists and sort chronologically.
        raw = sorted(beat_times + onset_times)

        # Collapse any two events within 30 ms of each other into the
        # earlier one.  This removes duplicate detections where both
        # beat_track and onset_detect fire on the same drum hit at
        # slightly different frame indices.
        DEDUP_WINDOW = 0.030   # 30 milliseconds
        deduped = []
        for t in raw:
            if deduped and t - deduped[-1] < DEDUP_WINDOW:
                continue   # too close to the previous event — discard
            deduped.append(round(t, 3))

        # ── Stage 4: MIN_BEAT_GAP thinning ───────────────────────
        # Enforce a hard minimum spacing so no two notes are closer
        # than MIN_BEAT_GAP seconds in the final chart.
        # At 0.16 s this only removes artefacts, not real beats.
        merged = []
        for t in deduped:
            if not merged or t - merged[-1] >= gap:
                merged.append(t)

        # ── Write cache ───────────────────────────────────────────
        with open(cache, "w") as f:
            json.dump(merged, f)

        # Log summary — helpful for tuning MIN_BEAT_GAP and delta
        bpm = float(tempo) if not hasattr(tempo, "__len__") else float(tempo[0])
        print(f"  ✓ {os.path.basename(mp3_path)}: {bpm:.1f} BPM, "
              f"{len(merged)} events ({len(raw)} raw → {len(deduped)} deduped)")
        return merged

    except Exception as e:
        print(f"  ✗ Beat analysis failed ({type(e).__name__}): {e}")
        return []


def build_beat_chart(beat_times: list, speed: float, num_lanes: int,
                     difficulty: dict = None) -> list:
    """
    Convert a list of beat timestamps into a sorted list of note spawn
    events for the rhythm engine.

    Each element in the returned chart is a 3-tuple:
        (spawn_t, lane, beat_t)

        spawn_t  (float) — wall-clock game time (seconds) at which the
                           note should be created.  Computed so the note
                           arrives at the hit bar exactly at beat_t.
        lane     (int)   — zero-based lane index [0, num_lanes).
        beat_t   (float) — the original beat timestamp from analyse_beats().
                           Used by bgyo_game to calculate per-note timing
                           accuracy for PERFECT/GOOD scoring.

    ── BEAT ACCURACY FORMULA ───────────────────────────────────────

        travel_time = HIT_DEPTH / speed
        spawn_t     = beat_t - travel_time

    HIT_DEPTH is the normalised depth value at which the hit bar sits
    (computed in constants._make_proj() as the depth where y = 78% of
    screen height).  A note spawned at spawn_t will travel the full
    HIT_DEPTH at the given speed and arrive at the hit bar at exactly
    beat_t — true beat-synchronised gameplay at any difficulty speed.

    ── DENSITY CONTROL ─────────────────────────────────────────────

    The difficulty dict's ival_min is applied at 40% of its full value
    as a hard skip threshold.  Using the full ival_min here would make
    charts too sparse at Normal/Hard — it's the right threshold for
    the random-mode fallback in bgyo_game._update_game(), not for a
    beat-accurate chart.  At 40% the chart stays dense and song-accurate
    while still preventing impossible same-frame clusters.

    ── LANE ASSIGNMENT ─────────────────────────────────────────────

    To prevent runs of the same lane (which would feel like a single-
    finger song), the algorithm tracks the last two used lanes and
    excludes them from the candidate pool for the next note.  If all
    lanes are excluded (only possible with num_lanes ≤ 2), the full
    pool is restored to avoid deadlock.

    Args:
        beat_times  — sorted list of float timestamps from analyse_beats().
        speed       — note travel speed in depth-units/second (from
                      constants.DIFFICULTY[difficulty_name]["speed"]).
        num_lanes   — number of active lanes (3, 4, or 5).
        difficulty  — the full difficulty preset dict from constants.DIFFICULTY,
                      used to read ival_min.  Pass None to disable the
                      density threshold (all beats become notes).

    Returns:
        list of (spawn_t, lane, beat_t) tuples, sorted by spawn_t.
        Returns [] if beat_times is empty.

    Called by:
        bgyo_game._start_analysis_worker() — after analyse_beats() completes,
        in the same background thread, before handing the chart to _update_game().
    """
    if not beat_times:
        return []

    # travel_time: how many seconds a note needs to travel from spawn
    # (depth=0, horizon) to the hit bar (depth=HIT_DEPTH).
    # Using the actual difficulty speed here ensures the chart is
    # beat-accurate regardless of which difficulty the player chose.
    travel_time = HIT_DEPTH / max(speed, 0.001)   # guard against zero-speed

    # Apply only 40% of ival_min as the chart-builder threshold.
    # The full ival_min is used by bgyo_game's random-mode fallback.
    # See docstring for rationale.
    ival_min = 0.0
    if difficulty and "ival_min" in difficulty:
        ival_min = float(difficulty["ival_min"]) * 0.40

    prev_lanes   = []    # rolling list of the last two used lane indices
    chart        = []    # accumulates (spawn_t, lane, beat_t) tuples
    last_spawn_t = -999.0  # tracks the most recently added spawn time

    for beat_t in beat_times:
        # Compute the spawn time for this beat
        spawn_t = beat_t - travel_time

        # Skip if this note would be too close to the previous one.
        # This prevents physically impossible rapid-fire clusters that
        # the player cannot react to.
        if spawn_t - last_spawn_t < ival_min:
            continue

        # Lane selection: exclude the last two lanes for visual spread.
        # If the exclusion list covers all lanes (edge case with ≤2 lanes),
        # reset to the full pool so the chart doesn't deadlock.
        available = [l for l in range(num_lanes) if l not in prev_lanes[-2:]]
        if not available:
            available = list(range(num_lanes))
        lane = random.choice(available)

        # Record the lane to inform the next exclusion window
        prev_lanes.append(lane)

        chart.append((spawn_t, lane, beat_t))
        last_spawn_t = spawn_t

    # Sort by spawn time — beat_times should already be sorted, but
    # this guarantees correctness even if the input is unordered.
    chart.sort(key=lambda x: x[0])
    return chart


# ══════════════════════════════════════════════════════════════════
#  AUDIO PLAYER
# ══════════════════════════════════════════════════════════════════

class AudioPlayer:
    """
    Thin stateful wrapper around pygame's two audio channels.

    ── CHANNEL ARCHITECTURE ────────────────────────────────────────

    Music channel  (pygame.mixer.music)
        Streaming MP3 decoder — best for long tracks (menu BGM,
        gameplay music) because pygame streams from disk rather than
        loading the whole file into memory.
        Controlled by music.load(), music.play(), music.fadeout(),
        music.stop(), music.set_volume(), music.get_pos().

    Sound channel  (_preview_channel via pygame.mixer.Sound)
        In-memory audio buffer — used for short preview clips in the
        song-select screen.  A Sound object is created, its volume set,
        and it is played on a dedicated Channel handle so we can stop
        it independently.  Critically, stop_preview() only stops this
        channel — it NEVER calls pygame.mixer.stop() or
        pygame.mixer.music.stop(), so the menu BGM keeps playing
        uninterrupted while the user browses songs.

    ── BGM ROTATION LOGIC ──────────────────────────────────────────

    play_menu_bgm() guarantees a different track on every home-screen
    visit by:
      1. Building a shuffled list of all .mp3 files in SONGS_DIR.
      2. Excluding the last-played path from the pool (so the same
         song never repeats back-to-back, unless only one song exists).
      3. Playing the first entry from the shuffled pool.

    ── STATE MACHINE ───────────────────────────────────────────────

    _bgm_state tracks the music channel's lifecycle:
        "idle"        — no music playing (app start, after stop())
        "menu"        — menu BGM looping (-1 repeats)
        "fading_out"  — fadeout_for_game() called, BGM fading out
        "game"        — gameplay track playing (single play, no loop)

    bgyo_game.py reads bgm_state to decide whether to restart BGM
    when returning to the home screen after a game session.

    ── VOLUME CHAIN ────────────────────────────────────────────────

    settings_state.apply()  writes  C.MASTER_VOLUME, C.MUSIC_VOLUME
         ↓  then calls
    audio.set_volume(C.effective_music_volume())
         ↓  which calls
    pygame.mixer.music.set_volume(clamped_value)

    This one-direction push (settings → audio) avoids any need for
    audio_engine to import settings_state (which would be circular).
    """

    # Base volume for menu BGM — lower than gameplay to feel like
    # unobtrusive background music rather than a full playback session.
    MENU_VOL = 0.35   # applied as MENU_VOL × MASTER_VOLUME

    # Cross-fade duration when transitioning from menu BGM to gameplay.
    # 800 ms gives a smooth fade without feeling too slow.
    FADE_MS  = 800

    def __init__(self):
        # Wall-clock time (time.time()) at which the current gameplay
        # track started playing.  Used by position() as a fallback
        # when pygame.mixer.music.get_pos() returns -1.
        self._play_start_wall = None

        # Accumulated playback offset in seconds.  Reserved for future
        # use (e.g. mid-track seeks); currently always 0.0.
        self._audio_offset    = 0.0

        # Current lifecycle state of the music channel.
        # See class docstring for the full state machine.
        self._bgm_state       = "idle"

        # BGM rotation state — rebuilt and shuffled by _refresh_menu_paths()
        # before every home-screen visit.
        self._menu_paths      = []       # shuffled list of SONGS_DIR .mp3 paths
        self._menu_idx        = 0        # current position in _menu_paths
        self._last_menu_path  = None     # path played most recently (excluded next time)

        # Preview (Sound channel) state.
        self._preview_snd     = None     # pygame.mixer.Sound instance (or None)
        self._preview_channel = None     # Channel handle returned by snd.play() (or None)


    # ── BGM rotation helpers ──────────────────────────────────────

    def _refresh_menu_paths(self, exclude: str = None):
        """
        Rebuild and shuffle the list of candidate BGM tracks from SONGS_DIR.

        If `exclude` is provided (the path of the last-played track) and
        more than one MP3 exists, that path is removed from the pool so
        the next selection is guaranteed to be a different song.

        If SONGS_DIR doesn't exist or is empty, _menu_paths is set to []
        and play_menu_bgm() will silently do nothing.
        """
        if not os.path.isdir(SONGS_DIR):
            self._menu_paths = []
            return

        # Collect all .mp3 files directly under SONGS_DIR.
        # os.path.isfile() guard skips subdirectories (e.g. preview/).
        paths = [
            os.path.join(SONGS_DIR, f)
            for f in os.listdir(SONGS_DIR)
            if f.lower().endswith(".mp3")
            and os.path.isfile(os.path.join(SONGS_DIR, f))
        ]

        # Exclude the last-played track so the same song doesn't play twice.
        # Only applies when there are ≥ 2 candidates; with a single file we
        # have no choice but to repeat it.
        if exclude and len(paths) > 1:
            paths = [p for p in paths
                     if os.path.abspath(p) != os.path.abspath(exclude)]

        random.shuffle(paths)
        self._menu_paths = paths
        self._menu_idx   = 0


    def play_menu_bgm(self):
        """
        Start a random BGM track on the music channel for the home screen.

        Guarantees a different track from the previous visit by calling
        _refresh_menu_paths(exclude=self._last_menu_path).  Loops
        indefinitely (play(-1)) at MENU_VOL × MASTER_VOLUME.

        Resets _play_start_wall to None because position() is only
        meaningful during gameplay — menu BGM position is never queried.

        Called by:
            bgyo_game._show_title() every time the home screen is shown.
        """
        if not PYGAME_OK:
            return
        self._refresh_menu_paths(exclude=self._last_menu_path)
        if not self._menu_paths:
            return   # no MP3 files found — silently skip
        path = self._menu_paths[0]
        try:
            # Apply master multiplier to keep menu BGM scaled correctly
            # even after the player changes the master volume slider.
            vol = min(1.0, self.MENU_VOL * C.MASTER_VOLUME)
            pygame.mixer.music.load(path)
            pygame.mixer.music.set_volume(vol)
            pygame.mixer.music.play(-1)   # -1 = loop indefinitely
            self._bgm_state       = "menu"
            self._play_start_wall = None  # position() not used in menu state
            self._last_menu_path  = path  # record so next visit picks a different track
        except Exception as e:
            print(f"play_menu_bgm error ({type(e).__name__}): {e}")


    # ── Preview channel (Song-select screen) ─────────────────────

    def play_preview(self, path: str, volume: float = None):
        """
        Play a short preview clip on the Sound channel.

        Uses pygame.mixer.Sound (in-memory buffer) rather than
        pygame.mixer.music so the BGM / gameplay track on the music
        channel continues unaffected.

        Volume defaults to C.effective_sfx_volume() so the SFX Volume
        slider in settings applies to previews in real-time.

        Stops any previously playing preview before starting the new one
        (only one preview at a time; does not affect the music channel).

        Args:
            path    — absolute path to the preview MP3 / audio file.
            volume  — optional override in [0.0, 1.0]; defaults to
                      C.effective_sfx_volume().

        Called by:
            bgyo_game._show_song_select() — triggered ~700 ms after the
            player navigates to a new song in the carousel, giving a brief
            delay before the preview starts so rapid scrolling doesn't
            spam audio loads.
        """
        if not PYGAME_OK or not path or not os.path.exists(path):
            return
        if volume is None:
            volume = C.effective_sfx_volume()
        try:
            self._stop_preview_channel()   # stop current preview only
            snd = pygame.mixer.Sound(path)
            snd.set_volume(max(0.0, min(1.0, volume)))
            self._preview_snd     = snd
            self._preview_channel = snd.play()   # returns a Channel object
        except Exception as e:
            print(f"play_preview error ({type(e).__name__}): {e}")


    def _stop_preview_channel(self):
        """
        Internal helper — stop the Sound channel cleanly.

        Calls both channel.stop() and snd.stop() because:
          • channel.stop() halts the channel immediately but the Sound
            object may still hold a reference.
          • snd.stop() ensures the Sound buffer is released even if the
            channel reference is stale or has been recycled by pygame.

        Each call is individually try/except-guarded because either
        reference may already be None or in an invalid state (e.g. if
        pygame was shut down unexpectedly).

        IMPORTANT: This method never calls pygame.mixer.stop() or
        pygame.mixer.music.stop() — those would kill the BGM channel.
        """
        try:
            if self._preview_channel is not None:
                self._preview_channel.stop()
        except Exception:
            pass
        try:
            if self._preview_snd is not None:
                self._preview_snd.stop()
        except Exception:
            pass
        # Clear references so play_preview() creates a fresh Sound next time
        self._preview_snd     = None
        self._preview_channel = None


    def stop_preview(self):
        """
        Public API — stop the currently playing preview clip.

        Safe to call even if no preview is playing (handles None
        references gracefully via _stop_preview_channel()).
        Does NOT affect the music/BGM channel.

        Called by:
            bgyo_game._show_song_select() — when leaving the song-select
            screen, to prevent the preview from continuing into gameplay.
        """
        if not PYGAME_OK:
            return
        self._stop_preview_channel()


    # ── BGM lifecycle ─────────────────────────────────────────────

    def fadeout_for_game(self):
        """
        Begin a timed fade-out of the menu BGM before gameplay starts.

        Uses pygame.mixer.music.fadeout(FADE_MS) which lets the current
        track fade to silence over FADE_MS milliseconds (800 ms) and
        then stops automatically — no manual stop() call needed.

        Sets _bgm_state to "fading_out" so bgyo_game._start_game() can
        check whether the fade has completed before loading the gameplay
        track (or it can simply call play() which loads a new track,
        replacing whatever is in the music channel).

        Called by:
            bgyo_game._start_game() — immediately before loading the
            gameplay track, giving a smooth musical transition.
        """
        if not PYGAME_OK:
            return
        try:
            pygame.mixer.music.fadeout(self.FADE_MS)
            self._bgm_state = "fading_out"
        except Exception as e:
            print(f"fadeout_for_game error ({type(e).__name__}): {e}")


    def stop_bgm(self):
        """
        Hard-stop the music channel immediately (no fade).

        Resets _play_start_wall (position() returns 0.0 after this)
        and sets _bgm_state to "idle".

        Use fadeout_for_game() for a smooth transition; this method is
        for cases where an immediate stop is required (e.g. the player
        quits mid-game).
        """
        if not PYGAME_OK:
            return
        try:
            pygame.mixer.music.stop()
        except Exception:
            pass
        self._play_start_wall = None
        self._bgm_state       = "idle"


    def play(self, path: str, volume: float = None) -> bool:
        """
        Load and start playback of the gameplay music track.

        Loads the MP3 at `path` into the music channel, sets the volume
        to C.effective_music_volume() (or the supplied override), plays
        it once (no loop), and records the wall-clock start time for
        position() to use.

        This replaces whatever was previously in the music channel
        (menu BGM, previous gameplay track, etc.) without requiring
        an explicit stop() call first — pygame.mixer.music.load()
        implicitly stops the current track.

        Args:
            path    — absolute path to the gameplay MP3.
            volume  — optional override in [0.0, 1.0]; defaults to
                      C.effective_music_volume() (MASTER × MUSIC).

        Returns:
            True  — track loaded and playing successfully.
            False — path missing, pygame unavailable, or load error.

        Called by:
            bgyo_game._start_game() after the analysis worker has
            finished building the beat chart.
        """
        if not PYGAME_OK or not path or not os.path.exists(path):
            return False
        if volume is None:
            volume = C.effective_music_volume()
        try:
            pygame.mixer.music.load(path)
            pygame.mixer.music.set_volume(max(0.0, min(1.0, volume)))
            pygame.mixer.music.play()   # play once (no loop for gameplay tracks)
            self._play_start_wall = time.time()   # record for position() fallback
            self._audio_offset    = 0.0
            self._bgm_state       = "game"
            return True
        except Exception as e:
            print(f"audio.play error ({type(e).__name__}): {e}")
            return False


    def pause(self):
        """
        Pause the music channel (preserves playback position).
        Called by bgyo_game._pause_game() when SPACE is pressed.
        """
        if PYGAME_OK:
            try:
                pygame.mixer.music.pause()
            except Exception:
                pass


    def unpause(self):
        """
        Resume the music channel from where it was paused.
        Called by bgyo_game._resume_game() when SPACE is pressed again.
        """
        if PYGAME_OK:
            try:
                pygame.mixer.music.unpause()
            except Exception:
                pass


    def stop(self):
        """
        Stop gameplay music immediately and reset state to idle.

        Functionally identical to stop_bgm() — kept as a separate
        method so callers can express intent: stop() = "gameplay ended",
        stop_bgm() = "hard-stop the background music".

        Called by:
            bgyo_game._end_game() — when the song finishes naturally.
            bgyo_game._on_close() — when the window is closed mid-game.
        """
        if not PYGAME_OK:
            return
        try:
            pygame.mixer.music.stop()
        except Exception:
            pass
        self._play_start_wall = None
        self._bgm_state       = "idle"


    # ── Volume controls ───────────────────────────────────────────

    def set_volume(self, v: float):
        """
        Update the music channel volume.  Kept for back-compatibility
        with older callers; delegates to set_music_volume().

        Called by:
            settings_state._Settings.apply()
            — which passes C.effective_music_volume() (already
              accounts for MASTER × MUSIC).
        """
        self.set_music_volume(v)


    def set_music_volume(self, v: float):
        """
        Set the BGM / gameplay music channel volume directly (0–1).

        This is the final link in the volume chain:
            Slider change → settings_state.apply()
                         → C.MUSIC_VOLUME updated
                         → audio.set_volume(effective_music_volume())
                         → this method → pygame.mixer.music.set_volume()

        Clamped to [0.0, 1.0] before passing to pygame.
        """
        if not PYGAME_OK:
            return
        try:
            pygame.mixer.music.set_volume(max(0.0, min(1.0, float(v))))
        except Exception:
            pass


    def set_sfx_volume(self, v: float):
        """
        Update the volume of a currently playing preview clip.

        Only affects the active _preview_snd Sound object — if no
        preview is playing this is a no-op.  Future preview plays will
        use C.effective_sfx_volume() directly via play_preview(), so
        this method is only needed for live-update while a clip is playing.

        Called by settings_state._Settings.set_sfx_volume() when the
        SFX Volume slider is moved while a preview is active.
        """
        if not PYGAME_OK:
            return
        v = max(0.0, min(1.0, float(v)))
        try:
            if self._preview_snd is not None:
                self._preview_snd.set_volume(v)
        except Exception:
            pass


    def set_master_volume(self, v: float):
        """
        Re-synchronise the music channel after the master volume changes.

        constants.py globals are already updated by settings_state.apply()
        before this is called.  This method simply re-computes and applies
        the effective music volume (MASTER × MUSIC) to the pygame mixer.

        Called by settings_state._Settings.set_master_volume().
        """
        # C.MASTER_VOLUME has already been updated by settings_state.apply()
        # so effective_music_volume() returns the correct new value.
        self.set_music_volume(C.effective_music_volume())


    # ── State queries ─────────────────────────────────────────────

    def is_busy(self) -> bool:
        """
        Return True if the music channel is currently playing.

        Used by bgyo_game to detect when the gameplay track has ended
        naturally (the music stops) so the results screen can be shown.

        Returns False if pygame is unavailable.
        """
        if not PYGAME_OK:
            return False
        try:
            return bool(pygame.mixer.music.get_busy())
        except Exception:
            return False


    @property
    def bgm_state(self) -> str:
        """
        Current state of the music channel.
        One of: "idle" | "menu" | "fading_out" | "game".

        Read by bgyo_game._show_title() to decide whether to call
        play_menu_bgm() (if state is "idle") or let the existing
        music continue (if state is already "menu").
        """
        return self._bgm_state


    def position(self) -> float:
        """
        Return the current playback position of the gameplay track in seconds.

        Primary source: pygame.mixer.music.get_pos()
            Returns milliseconds since the track started, or -1 if no
            track is loaded.  Divided by 1000 and offset by _audio_offset
            (currently 0.0 — reserved for future seek support).

        Fallback: wall-clock difference from _play_start_wall.
            Used when pygame is unavailable or get_pos() returns -1.
            Less accurate (doesn't account for audio buffer latency)
            but sufficient as a fallback.

        Returns 0.0 if no gameplay track has been started yet
        (_play_start_wall is None).

        Called every frame by bgyo_game._update_game() to synchronise
        note spawning: a note is spawned when position() >= spawn_t.
        """
        if self._play_start_wall is None:
            return 0.0
        if PYGAME_OK:
            try:
                p = pygame.mixer.music.get_pos()
                if p != -1:
                    return (p / 1000.0) + self._audio_offset
            except Exception:
                pass
        # Wall-clock fallback
        return time.time() - self._play_start_wall


# ══════════════════════════════════════════════════════════════════
#  MODULE SINGLETON
# ══════════════════════════════════════════════════════════════════

# A single AudioPlayer instance shared across the entire application.
# Imported directly by bgyo_game.py:
#     from audio_engine import audio
# and by settings_state.apply() (imported lazily inside the method
# to avoid a circular import at module load time).
audio = AudioPlayer()
