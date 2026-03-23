"""
╔══════════════════════════════════════════════════════════════════╗
║   BGYO: THE LIGHT STAGE — ACES OF P-POP  v17.0                 ║
║   settings_state.py  ·  Runtime cfg + active session           ║
╚══════════════════════════════════════════════════════════════════╝

PURPOSE & ROLE IN THE ARCHITECTURE
────────────────────────────────────
This module owns two module-level singletons that are imported
directly by bgyo_game.py:

    cfg     — a _Settings instance holding all runtime preferences
    session — a _Session instance tracking the logged-in player

Together they act as a lightweight application-state layer between
the UI (bgyo_game.py) and the persistence layer (database.py /
constants.py), so neither of those modules needs to know about each
other directly.

Dependency chain:

    constants.py  ←─┐
    database.py   ←─┤  settings_state.py  ←  bgyo_game.py
    audio_engine  ←─┘  (lazy import inside apply())

Circular-import avoidance:
    audio_engine.py imports constants.py at module level.
    settings_state.py would create a circle if it also imported
    audio_engine at module level:
        settings_state → audio_engine → constants → (ok)
        audio_engine   → settings_state  ← CIRCULAR

    Solution: audio_engine is imported INSIDE apply() using a
    try/except so the import only happens at call time (after all
    modules are fully loaded), never at module-load time.

HOW THE SETTINGS PIPELINE WORKS
─────────────────────────────────
When the player moves a volume slider in the settings screen:

  1. bgyo_game calls cfg.set_music_volume(new_val)
         ↓
  2. _Settings.set_music_volume() clamps the value and stores it
     in self.music_volume, then calls self.apply()
         ↓
  3. apply() writes the new value to C.MUSIC_VOLUME (the module-level
     constant that game_objects and bgyo_game read each frame)
         ↓
  4. apply() lazily imports audio_engine and calls
     audio.set_volume(C.effective_music_volume()) to push the
     change to the pygame mixer immediately — no restart needed

When the player clicks "Save & Close":
  5. bgyo_game calls cfg.save_to_db(session.account_id)
         ↓
  6. save_to_db() calls database.save_settings() to persist all
     current values — a no-op if account_id is None (guest)

v17 CHANGES vs v16
───────────────────
  • _Settings now tracks master_volume, music_volume, sfx_intensity
  • apply() propagates all five audio values to constants globals
    AND to audio_engine.audio in real-time (no game restart needed)
  • load_from_db / save_to_db round-trip all five audio columns
  • save_to_db is a no-op for guest (id is None) — nothing breaks
    when a guest changes a slider
  • _Session.logout() resets cfg back to guest defaults via cfg.reset()
"""

import database as db
import constants as C


# ══════════════════════════════════════════════════════════════════
#  SETTINGS
# ══════════════════════════════════════════════════════════════════

class _Settings:
    """
    Container for all runtime player preferences.

    Holds the canonical in-memory copy of every setting.  Two sources
    can populate it:
        • __init__() / reset() — sensible defaults for a guest session
        • load_from_db()       — values loaded from the database on login

    The single most important method is apply(), which pushes the
    current in-memory values to:
        1. constants.py module globals   (read by game_objects, bgyo_game)
        2. audio_engine.audio singleton  (pushes to pygame mixer)

    This two-step push is the mechanism by which all game subsystems
    stay in sync with the player's preferences without needing to
    import settings_state themselves (which would cause circular imports).

    Attribute groups:
        Audio       — master_volume, music_volume, sfx_volume,
                      sfx_intensity, sfx_enabled
        Gameplay    — difficulty, num_lanes, fullscreen
        Song state  — songs list, selected_mode, selected_song
    """

    def __init__(self):
        # ── Audio preferences ─────────────────────────────────────
        # These map 1-to-1 with the constants.py module globals that
        # apply() writes.  Defaults match the constants.py initial values
        # so the game feels consistent before any settings are loaded.

        self.master_volume  = 1.00   # Overall multiplier → C.MASTER_VOLUME
                                      # Applied on top of music_volume and sfx_volume.
                                      # Equivalent to a hardware master fader.

        self.music_volume   = 0.85   # BGM / gameplay track level → C.MUSIC_VOLUME
                                      # Passed through effective_music_volume() before
                                      # reaching the pygame mixer (MASTER × MUSIC).

        self.sfx_volume     = 0.85   # Sound effects / preview clip level → C.SFX_VOLUME
                                      # Applied to pygame.mixer.Sound objects via
                                      # audio_engine.AudioPlayer.set_sfx_volume().

        self.sfx_intensity  = 1.00   # Visual effect density → C.SFX_INTENSITY
                                      # Scales particle counts, flash opacity, and
                                      # confetti bursts.  0.0 = minimal; 1.0 = full.

        self.sfx_enabled    = True   # Master SFX on/off toggle → C.SFX_ENABLED
                                      # False suppresses all click sounds and hit
                                      # particle spawning in bgyo_game.

        # ── Gameplay preferences ──────────────────────────────────
        self.difficulty     = "Normal"   # Key into constants.DIFFICULTY dict.
                                          # Controls note speed, density, hit windows.

        self.num_lanes      = 5          # Active lane count: 3, 4, or 5.
                                          # Maps to constants.LANE_CONFIGS[num_lanes].

        self.fullscreen     = False      # Whether to expand the window to native res.
                                          # Applied by BGYOGame._apply_fullscreen().

        # ── Song state ────────────────────────────────────────────
        self.songs          = list(db.DEFAULT_SONGS)   # Working copy of the song list.
                                                         # Always reset to DEFAULT_SONGS
                                                         # on login/logout — no per-account
                                                         # custom song lists in v17+.

        self.selected_mode  = "shuffle"  # "shuffle" or a specific song title.
                                          # Read by bgyo_game._start_game() to decide
                                          # whether to pick a random song or the one
                                          # the player selected in the carousel.

        self.selected_song  = ""         # The song title chosen in the carousel.
                                          # Only meaningful when selected_mode != "shuffle".

    # ── Legacy volume alias ───────────────────────────────────────
    # Older code in bgyo_game.py and some callbacks reference cfg.volume
    # directly.  This property keeps those references working without
    # requiring a mass find-and-replace.

    @property
    def volume(self) -> float:
        """
        Legacy read alias: cfg.volume → cfg.music_volume.
        Maintained for backward compatibility with any code that
        predates the separate music_volume / sfx_volume split.
        """
        return self.music_volume

    @volume.setter
    def volume(self, v: float):
        """
        Legacy write alias: cfg.volume = x → cfg.music_volume = x.
        Setting via this alias does NOT call apply() automatically;
        callers must call cfg.apply() or use set_music_volume() instead.
        """
        self.music_volume = float(v)


    # ── Core apply method ─────────────────────────────────────────

    def apply(self):
        """
        Push all current settings values to the live subsystems.

        This is the central synchronisation point of the settings
        architecture.  It must be called after ANY value change that
        should take effect immediately (before the next DB save).

        Step 1 — Write to constants.py globals
            game_objects.py (Spotlight, SideEffect) reads C.SFX_INTENSITY
            and C.SFX_ENABLED every frame.
            bgyo_game.py reads all five values on every update tick.
            Writing to the module globals is the fastest way to make
            the change visible to every reader simultaneously.

        Step 2 — Push to the live audio player
            audio_engine is imported lazily (inside this method, not at
            module level) to avoid a circular import:
                settings_state → audio_engine → constants (fine)
                audio_engine   → settings_state           (CIRCULAR)
            The try/except silently skips the audio update if the
            audio engine hasn't been initialised yet (e.g. during the
            very first cfg.apply() call at the bottom of this module,
            which runs before bgyo_game has imported audio_engine).

        Called by:
            __init__() / reset()           — initial push of defaults
            set_master_volume()            — after master slider change
            set_music_volume()             — after music slider change
            set_sfx_volume()               — after SFX slider change
            load_from_db()                 — after loading from database
            bgyo_game settings screen      — directly after slider release
        """
        # 1. Synchronise constants.py module globals ─────────────
        # These are the values read every frame by game_objects and bgyo_game.
        C.MASTER_VOLUME = max(0.0, min(1.0, self.master_volume))
        C.MUSIC_VOLUME  = max(0.0, min(1.0, self.music_volume))
        C.SFX_VOLUME    = max(0.0, min(1.0, self.sfx_volume))
        C.SFX_INTENSITY = max(0.0, min(1.0, self.sfx_intensity))
        C.SFX_ENABLED   = bool(self.sfx_enabled)

        # 2. Push effective music volume to the pygame mixer ──────
        # Imported here (not at top of module) to avoid the circular
        # import described in the module docstring.
        # The try/except catches ImportError (pygame/librosa not installed)
        # AND any runtime error (mixer not yet initialised).
        try:
            from audio_engine import audio
            audio.set_volume(C.effective_music_volume())
        except Exception:
            pass   # audio not yet initialised — will sync on next apply()


    # ── Reset to defaults ─────────────────────────────────────────

    def reset(self):
        """
        Reset every setting to its guest default and apply immediately.

        Called by:
            _Session.logout()   — ensures a guest play session always
                                  starts from the standard defaults,
                                  not leftover values from the previous
                                  logged-in player.
            bgyo_game "Cancel"  — when the settings screen is dismissed
                                  without saving (restores pre-edit values
                                  from the last apply()).

        After resetting, apply() is called so the constants globals
        and the audio mixer are immediately synchronised with the
        defaults — no stale values linger between sessions.
        """
        self.master_volume  = 1.00
        self.music_volume   = 0.85
        self.sfx_volume     = 0.85
        self.sfx_intensity  = 1.00
        self.sfx_enabled    = True
        self.difficulty     = "Normal"
        self.num_lanes      = 5
        self.fullscreen     = False
        self.songs          = list(db.DEFAULT_SONGS)
        self.selected_mode  = "shuffle"
        self.selected_song  = ""
        self.apply()


    # ── Database round-trip ───────────────────────────────────────

    def load_from_db(self, account_id: int):
        """
        Load the persisted settings for account_id and apply them.

        Calls database.load_settings(account_id) which returns a dict.
        Each value is extracted with a safe .get() fallback so that:
          • Old database rows missing newer columns (e.g. master_volume
            added in v17) silently get the default value.
          • The "volume" key is the legacy alias for music_volume —
            checked as a fallback: s.get("music_volume", s.get("volume", 0.85))

        After loading, apply() is called to immediately synchronise
        all subsystems with the loaded values.

        Note: songs is always reset to DEFAULT_SONGS regardless of what
        the database might contain — the song-upload feature was removed
        in v17 and per-account song lists no longer exist.

        Called by:
            bgyo_game._show_login()    → do_login()    — on successful login
            bgyo_game._show_register() → do_register() — after account creation
        """
        s = db.load_settings(account_id)

        self.master_volume  = float(s.get("master_volume",  1.00))
        # Fallback chain: music_volume → legacy "volume" key → default 0.85
        self.music_volume   = float(s.get("music_volume",   s.get("volume", 0.85)))
        self.sfx_volume     = float(s.get("sfx_volume",     0.85))
        self.sfx_intensity  = float(s.get("sfx_intensity",  1.00))
        self.sfx_enabled    = bool (s.get("sfx_enabled",    True))
        self.difficulty     = str  (s.get("difficulty",     "Normal"))
        self.num_lanes      = int  (s.get("num_lanes",      5))
        self.fullscreen     = bool (s.get("fullscreen",     True))
        # Always use the canonical song list — no per-account lists in v17+
        self.songs          = list(db.DEFAULT_SONGS)

        self.apply()

    def save_to_db(self, account_id: int):
        """
        Persist all current settings to the database.

        No-op if account_id is None (guest session) — this is the
        intentional design for guests: they can freely adjust settings
        during play but none of their changes survive the session.
        The None check prevents a database.save_settings() call with
        an invalid account_id, which would raise a SQLite constraint error.

        Passes all five audio columns plus gameplay prefs to
        database.save_settings(), which performs an UPSERT (INSERT or
        UPDATE depending on whether a settings row already exists).

        Called by:
            bgyo_game._show_settings() → "Save & Close" button handler.
        """
        if account_id is None:
            return   # guest — settings are transient, never persisted
        db.save_settings(
            account_id,
            master_volume  = self.master_volume,
            volume         = self.music_volume,    # legacy column name in DB schema
            music_volume   = self.music_volume,
            sfx_volume     = self.sfx_volume,
            sfx_intensity  = self.sfx_intensity,
            sfx_enabled    = self.sfx_enabled,
            difficulty     = self.difficulty,
            num_lanes      = self.num_lanes,
            fullscreen     = self.fullscreen,
        )


    # ── Convenience setters ───────────────────────────────────────
    # Each setter clamps its input and calls apply() immediately so
    # the change is reflected in the game on the very next frame
    # without requiring the caller to remember to call apply().
    #
    # These are the preferred way for bgyo_game's slider callbacks to
    # update settings, as opposed to writing to self.music_volume
    # directly and forgetting apply().

    def set_master_volume(self, v: float):
        """
        Update master_volume and apply immediately.

        Master volume is a multiplier applied on top of both music_volume
        and sfx_volume in C.effective_music_volume() and
        C.effective_sfx_volume().  Changing it re-scales all audio output
        without altering the individual channel settings.

        Clamped to [0.0, 1.0].

        Called by:
            bgyo_game settings screen — Master Volume slider callback.
        """
        self.master_volume = max(0.0, min(1.0, float(v)))
        self.apply()

    def set_music_volume(self, v: float):
        """
        Update music_volume and apply immediately.

        Affects: BGM loop (menu) and the gameplay music track.
        The pygame mixer receives effective_music_volume()
        (= MASTER × MUSIC) via apply() → audio.set_volume().

        Clamped to [0.0, 1.0].

        Called by:
            bgyo_game settings screen — Music Volume slider callback.
            bgyo_game in-game +/- volume keys (fast volume nudge).
        """
        self.music_volume = max(0.0, min(1.0, float(v)))
        self.apply()

    def set_sfx_volume(self, v: float):
        """
        Update sfx_volume and apply immediately.

        Affects: preview clips played during song selection.
        The currently-playing preview Sound object is updated via
        audio.set_sfx_volume() inside apply().

        Clamped to [0.0, 1.0].

        Called by:
            bgyo_game settings screen — SFX Volume slider callback.
        """
        self.sfx_volume = max(0.0, min(1.0, float(v)))
        self.apply()

    def set_sfx_intensity(self, v: float):
        """
        Update sfx_intensity and write to C.SFX_INTENSITY immediately.

        Unlike the volume setters, this does NOT call the full apply()
        because sfx_intensity only affects the constants global
        (C.SFX_INTENSITY) — it has no effect on the pygame mixer.
        Writing directly to C.SFX_INTENSITY is faster and avoids the
        unnecessary lazy audio_engine import on every drag event.

        Clamped to [0.0, 1.0].

        Called by:
            bgyo_game settings screen — SFX Intensity slider callback.
        """
        self.sfx_intensity  = max(0.0, min(1.0, float(v)))
        C.SFX_INTENSITY     = self.sfx_intensity   # immediate constant update

    def set_sfx_enabled(self, enabled: bool):
        """
        Toggle the master SFX on/off flag and write to C.SFX_ENABLED.

        Like set_sfx_intensity(), this bypasses the full apply() because
        the change only affects C.SFX_ENABLED — no audio mixer update
        is needed.

        Called by:
            bgyo_game settings screen — SFX Enabled toggle button.
        """
        self.sfx_enabled    = bool(enabled)
        C.SFX_ENABLED       = self.sfx_enabled     # immediate constant update


# ══════════════════════════════════════════════════════════════════
#  SESSION
# ══════════════════════════════════════════════════════════════════

class _Session:
    """
    Tracks the currently logged-in player (or the GUEST sentinel).

    Wraps an account dict (the same dict returned by database.login()
    or database.create_account()) and exposes read-only properties for
    the fields that the rest of the game needs.

    At any time exactly one of these states is true:
        is_guest == True   — no one is logged in; _acct == db.GUEST
        is_guest == False  — a player is logged in; _acct has a real id

    The session is never None — it always holds either a real account
    dict or the GUEST sentinel, so callers never need to check for None.
    """

    def __init__(self):
        # Start in guest mode so the game is fully functional before
        # any login attempt.  db.GUEST is:
        #   {"id": None, "username": "GUEST", "avatar_col": "#888888"}
        self._acct = dict(db.GUEST)

    def login(self, acct: dict):
        """
        Store a logged-in player's account dict.

        acct should be the dict returned by database.login() or
        database.create_account() — it must contain at minimum the
        keys: id, username, avatar_col.

        A shallow copy (dict(acct)) is stored so the session holds its
        own reference and is not affected if the caller later modifies
        their copy of the dict.

        Called by:
            bgyo_game._show_login()    → do_login()    — on valid credentials
            bgyo_game._show_register() → do_register() — after account creation
        """
        self._acct = dict(acct)

    def logout(self):
        """
        Reset the session to guest state and restore default settings.

        Two things happen on logout:
          1. _acct is replaced with a fresh copy of db.GUEST so
             account_id becomes None and username becomes "GUEST".
          2. cfg.reset() is called to restore all audio/visual
             preferences to their defaults.  This prevents the next
             guest (or the next logged-in player) from inheriting
             the previous player's slider positions.

        Called by:
            bgyo_game "Log Out" button handler.
        """
        self._acct = dict(db.GUEST)
        cfg.reset()   # restore guest defaults — also calls cfg.apply()


    # ── Read-only properties ──────────────────────────────────────
    # These provide a clean, stable interface that hides the internal
    # dict structure.  Callers use session.username rather than
    # session._acct.get("username"), making the code more readable
    # and resilient to internal dict key changes.

    @property
    def is_guest(self) -> bool:
        """
        True if no player is logged in (account id is None).

        Used throughout bgyo_game.py to gate features:
          • Leaderboard submission — guests can submit but their score
            is not linked to an account.
          • Settings persistence  — cfg.save_to_db() is a no-op for guests.
          • Profile badge display — shows "GUEST" username and grey avatar.
        """
        return self._acct["id"] is None

    @property
    def account_id(self):
        """
        The integer primary key of the logged-in account, or None for guests.

        Passed to database functions as the account_id parameter.
        None signals "this is a guest" to every database function that
        accepts account_id — they either store NULL in the FK column
        (scores) or skip the write entirely (save_to_db).
        """
        return self._acct.get("id")

    @property
    def username(self) -> str:
        """
        Display name of the current player.
        Returns "GUEST" when not logged in.

        Shown in:
          • The profile/avatar badge on the home screen.
          • The HUD player name display during gameplay.
          • The name pre-filled in the score submission prompt.
        """
        return self._acct.get("username", "GUEST")

    @property
    def avatar_col(self) -> str:
        """
        The player's chosen avatar colour as a '#RRGGBB' hex string.
        Returns '#888888' (neutral grey) for guests.

        Used to tint the profile badge ring and avatar circle on the
        home screen.  Comes from AVATAR_COLORS (constants.py) chosen
        at account creation or in the settings screen.
        """
        return self._acct.get("avatar_col", "#888888")

    @property
    def account(self) -> dict:
        """
        The full internal account dict.

        Exposed for cases where bgyo_game needs to read a field not
        covered by the individual properties (e.g. created_at,
        last_login).  Returned by reference — callers should not
        mutate it; use session.login() to replace it atomically.
        """
        return self._acct


# ══════════════════════════════════════════════════════════════════
#  MODULE-LEVEL SINGLETONS
# ══════════════════════════════════════════════════════════════════

# Created once when this module is first imported.  Both objects are
# re-used for the entire lifetime of the application.
#
# Import pattern used by consumers:
#     from settings_state import cfg, session
#
# This gives a direct reference to the singleton objects, not copies,
# so any mutations (cfg.music_volume = …, session.login(…)) are
# immediately visible to every other importer.

cfg     = _Settings()   # Global runtime preferences
session = _Session()    # Currently logged-in player (or GUEST)

# Push the initial default values to constants.py globals and the
# audio engine immediately on import.  This guarantees that C.MASTER_VOLUME,
# C.MUSIC_VOLUME etc. reflect the _Settings defaults from the very first
# frame, before any login or slider interaction occurs.
#
# The audio_engine import inside apply() will silently fail here (the
# audio module may not be imported yet), but that is expected and safe —
# the pygame mixer will receive the correct volume on its first play() call.
cfg.apply()
