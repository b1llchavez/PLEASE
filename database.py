"""
╔══════════════════════════════════════════════════════════════════╗
║   BGYO: THE LIGHT STAGE — ACES OF P-POP  v17.0                 ║
║   database.py  ·  SQLite persistence layer                      ║
╚══════════════════════════════════════════════════════════════════╝

PURPOSE & ROLE IN THE ARCHITECTURE
────────────────────────────────────
This module is the single gateway to all persistent data.  Nothing
else in the codebase touches SQLite directly — every read and write
goes through the functions defined here.

Consumers and what they use:
  settings_state.py  — load_settings(), save_settings()
                        Called on login and whenever a slider is moved.
  bgyo_game.py       — login(), create_account(), save_score(),
                        load_top_scores_by_difficulty(),
                        save_trivia_score(), load_trivia_scores(),
                        GUEST sentinel, DEFAULT_SONGS list,
                        AccountError exception class.

This module imports NO other project module — it sits at the very
bottom of the dependency chain alongside constants.py.

DATABASE FILE
──────────────
A single SQLite file  bgyo_data.db  is stored next to this source
file (resolved via __file__ so it works regardless of launch directory).
A separate  bgyo_users.db  is not used by this module; it may be a
legacy artifact from an earlier version.

TABLES
───────
  accounts          — player profiles (credentials, avatar colour)
  account_settings  — per-player audio/gameplay preferences
  scores            — rhythm-game results (leaderboard data)
  trivia_scores     — Aces Trivia session results
  songs             — read-only default song library

SCHEMA VERSIONING
──────────────────
New columns are added with ALTER TABLE … ADD COLUMN inside init_db().
SQLite silently raises OperationalError if the column already exists,
so each ALTER is wrapped in a try/except — this gives safe, zero-
downtime upgrades for players with existing save files.

THREADING
──────────
check_same_thread=False is set on every connection so the beat-analysis
worker thread (bgyo_game._worker_thread) can call database functions
without triggering a "created in a different thread" error.  Each
function opens its own connection, performs its work, and closes it,
which is safe for SQLite's WAL (write-ahead log) mode.

SECURITY
─────────
Passwords are never stored in plain text.  _hash() computes a SHA-256
hex digest before any INSERT or comparison.  This is sufficient for a
local single-player game; a production service would add salting.
All SQL queries use parameterised placeholders (?) to prevent
injection, even though the inputs come from the player's own machine.
"""

import sqlite3, hashlib, os, time, re


# ── Database file location ────────────────────────────────────────
# Resolved relative to this source file so the path is correct
# whether the game is launched from its own directory, a shortcut,
# or a packaged executable.
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bgyo_data.db")

# Minimum acceptable password length (enforced by validate_password).
_PW_MIN_LEN = 6


# ══════════════════════════════════════════════════════════════════
#  DEFAULT SONG LIBRARY
# ══════════════════════════════════════════════════════════════════

# Canonical list of BGYO song titles.  This list is:
#   1. Seeded into the `songs` table on first run by init_db().
#   2. Copied into cfg.songs by settings_state._Settings.__init__()
#      and reset() so the game always has a full song list regardless
#      of whether a user is logged in.
#   3. Used by bgyo_game._show_song_select() to populate the carousel.
#   4. Used by file_helpers.get_all_playable_songs() to filter down
#      to songs that actually have an MP3 file on disk.
DEFAULT_SONGS = [
    "Gigil", "To Be Yours", "Bulalakaw", "All These Ladies",
    "Dance With Me", "Headlines", "Divine", "Trash",
    "Kabataang Pinoy", "Patintero", "Andito Lang",
    "Be Us", "Live Vivid", "Magnet", "Mahal Na Kita", "Up!", "Best Time",
    "Patuloy Lang Ang Lipad", "Tumitigil Ang Mundo",
    "The Light", "The Baddest", "He's Into Her", "Rocketman",
    "Kundiman", "Sabay", "Fly Away", "Fresh", "When I'm With You",
    "Kulay", "Runnin'", "While We Are Young",
]


# ══════════════════════════════════════════════════════════════════
#  TYPED ERROR CLASS
# ══════════════════════════════════════════════════════════════════

class AccountError(Exception):
    """
    Raised by create_account(), validate_username(), validate_password(),
    change_password(), and change_username() when an operation cannot
    proceed due to invalid or conflicting input.

    Attributes:
        code    (str) : Machine-readable error identifier.  The UI in
                        bgyo_game._show_register() and _show_login()
                        switches on this to apply the correct styling
                        or take a specific recovery action.
        message (str) : Human-readable description shown directly to
                        the player in the form's error label.

    Known codes:
        "USERNAME_EMPTY"      — username field was blank
        "USERNAME_TOO_SHORT"  — fewer than 3 characters after strip
        "USERNAME_TOO_LONG"   — more than 24 characters
        "USERNAME_INVALID"    — contains characters outside [A-Za-z0-9_.-]
        "USERNAME_TAKEN"      — already exists in the accounts table
        "PASSWORD_EMPTY"      — password field was blank
        "PASSWORD_WHITESPACE" — leading/trailing whitespace detected
        "PASSWORD_TOO_SHORT"  — fewer than _PW_MIN_LEN (6) characters

    Usage in bgyo_game.py:
        try:
            acct = db.create_account(uname, pw)
        except AccountError as ae:
            msg_var.set(f"✗  {ae.message}")
    """
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code    = code
        self.message = message


# ══════════════════════════════════════════════════════════════════
#  INTERNAL HELPERS
# ══════════════════════════════════════════════════════════════════

def _conn() -> sqlite3.Connection:
    """
    Open and return a new SQLite connection to DB_PATH.

    Configuration:
      row_factory = sqlite3.Row
          Makes fetchone()/fetchall() return dict-like Row objects
          so callers can access columns by name (row["username"])
          rather than position (row[1]).

      PRAGMA foreign_keys = ON
          Enforces ON DELETE SET NULL / ON DELETE CASCADE constraints
          declared in the schema.  Without this pragma SQLite silently
          ignores foreign-key actions.

      PRAGMA journal_mode = WAL
          Write-Ahead Log mode allows concurrent reads during writes.
          Important here because the beat-analysis background thread
          may read scores while the main thread writes settings.

      check_same_thread = False
          Required so the background beat-analysis thread can safely
          call database functions that were originally opened on the
          main thread.  Each call opens its own connection, so there
          is no shared mutable state between threads.
    """
    c = sqlite3.connect(DB_PATH, check_same_thread=False)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON")
    c.execute("PRAGMA journal_mode = WAL")
    return c


def _now() -> str:
    """Return the current local time as a 'YYYY-MM-DD HH:MM:SS' string.
    Stored in created_at, last_login, and played_at columns."""
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _hash(pw: str) -> str:
    """
    Return the SHA-256 hex digest of a UTF-8 encoded password string.

    Used in:
      • create_account() — hash before INSERT
      • login()          — hash the input then compare to stored hash
      • change_password() — hash old_pw to verify, hash new_pw to store

    SHA-256 produces a 64-character lowercase hex string.
    Passwords are never stored or logged in plain text anywhere in
    this module.
    """
    return hashlib.sha256(pw.encode("utf-8")).hexdigest()


# ══════════════════════════════════════════════════════════════════
#  INPUT VALIDATION HELPERS
# ══════════════════════════════════════════════════════════════════
# These are called by the UI (bgyo_game.py) *before* the DB write so
# the player gets immediate, specific feedback without a round-trip.
# They are also called inside create_account() as a second safety net.

def validate_username(username: str) -> str:
    """
    Validate and clean a username string.

    Rules (in order of check):
      1. Not empty / whitespace-only after strip.
      2. At least 3 characters after strip.
      3. No more than 24 characters after strip.
      4. Contains only [A-Za-z0-9_\\-.] (alphanumeric + underscore,
         hyphen, dot) — enforced by regex ^[\\w\\-\\.]+$.
      5. Not already taken (case-insensitive DB lookup).

    Returns:
        The stripped, valid username string.

    Raises:
        AccountError with an appropriate .code on any violation.

    Called by:
        create_account()   — as a validation gate before INSERT
        change_username()  — same gate before UPDATE
        bgyo_game.py       — directly in _show_register() for live feedback
    """
    u = username.strip()
    if not u:
        raise AccountError("USERNAME_EMPTY", "Username cannot be empty.")
    if len(u) < 3:
        raise AccountError("USERNAME_TOO_SHORT",
                           f"Username too short ({len(u)}/3 chars).")
    if len(u) > 24:
        raise AccountError("USERNAME_TOO_LONG",
                           f"Username too long ({len(u)}/24 chars).")
    if not re.match(r'^[\w\-\.]+$', u):
        raise AccountError(
            "USERNAME_INVALID",
            "Only letters, numbers, _, -, or . allowed."
        )
    # Uniqueness check — hits the DB (fast indexed lookup)
    if username_exists(u):
        raise AccountError("USERNAME_TAKEN", f'"{u}" is taken. Try another.')
    return u


def validate_password(password: str) -> str:
    """
    Validate a password string against the game's policy.

    Rules (in order of check):
      1. Not empty (None or empty string).
      2. No leading or trailing whitespace (prevents invisible-character
         passwords that are impossible for the player to reproduce).
      3. At least _PW_MIN_LEN (6) characters.

    Returns:
        The original password string, unchanged, on success.

    Raises:
        AccountError with an appropriate .code on any violation.

    Called by:
        create_account()   — validate before hashing + INSERT
        change_password()  — validate the new password before UPDATE
        bgyo_game.py       — directly in _show_register() for live feedback
    """
    if not password:
        raise AccountError("PASSWORD_EMPTY", "Password cannot be empty.")
    if password != password.strip() or not password.strip():
        raise AccountError("PASSWORD_WHITESPACE",
                           "No leading/trailing spaces allowed.")
    if len(password) < _PW_MIN_LEN:
        raise AccountError(
            "PASSWORD_TOO_SHORT",
            f"Password too short ({len(password)}/{_PW_MIN_LEN} chars)."
        )
    return password


# ══════════════════════════════════════════════════════════════════
#  DATABASE INITIALISATION
# ══════════════════════════════════════════════════════════════════

def init_db():
    """
    Create all tables and seed default data on first run.
    Safe to call repeatedly — all CREATE TABLE statements use
    IF NOT EXISTS so re-running on an existing database is a no-op.

    Also performs safe ALTER TABLE migrations for new columns added
    in later versions (each guarded by try/except OperationalError).

    Called automatically at the bottom of this module on import, so
    any module that does `import database` is guaranteed to have a
    fully initialised database before it makes any queries.

    TABLE OVERVIEW
    ───────────────
    accounts
        One row per registered player.  Stores hashed password,
        chosen avatar colour, and login timestamps.
        `username` has a NOCASE UNIQUE constraint so "Alice" and
        "alice" cannot coexist.

    scores
        One row per completed rhythm-game session.
        account_id is a nullable FK (ON DELETE SET NULL) so scores
        survive account deletion, staying on the leaderboard under
        the player_name string.

    trivia_scores
        One row per completed Aces Trivia session.
        Same nullable account_id pattern as `scores`.

    songs
        Read-only default song catalogue.  Seeded from DEFAULT_SONGS
        on init.  No insert/delete API is exposed — the song-upload
        feature was intentionally removed in v17.

    account_settings
        One row per account (account_id is the PK and FK).
        ON DELETE CASCADE removes settings automatically if an account
        is deleted.  Stores all five audio slider values plus
        difficulty, num_lanes, and fullscreen preference.
    """
    c   = _conn()
    cur = c.cursor()

    # ── accounts table ────────────────────────────────────────────
    # Stores one row per registered player.
    # avatar_col defaults to gold (#FFD700, Gelo's colour) as a
    # friendly default for new accounts.
    cur.execute("""CREATE TABLE IF NOT EXISTS accounts(
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        username   TEXT NOT NULL UNIQUE COLLATE NOCASE,
        pass_hash  TEXT NOT NULL,
        avatar_col TEXT NOT NULL DEFAULT '#FFD700',
        created_at TEXT NOT NULL,
        last_login TEXT
    )""")

    # ── scores table (rhythm game results) ───────────────────────
    # account_id is nullable so guest scores (account_id = NULL) are
    # stored and appear on the leaderboard.
    # ON DELETE SET NULL means the row stays after account deletion.
    cur.execute("""CREATE TABLE IF NOT EXISTS scores(
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        account_id  INTEGER REFERENCES accounts(id) ON DELETE SET NULL,
        player_name TEXT NOT NULL,
        score       INTEGER NOT NULL DEFAULT 0,
        accuracy    INTEGER NOT NULL DEFAULT 0,
        max_combo   INTEGER NOT NULL DEFAULT 0,
        grade       TEXT NOT NULL DEFAULT 'D',
        difficulty  TEXT NOT NULL DEFAULT 'Normal',
        song_name   TEXT NOT NULL DEFAULT 'Unknown',
        mode        TEXT NOT NULL DEFAULT 'Single',
        played_at   TEXT NOT NULL
    )""")

    # ── trivia_scores table ───────────────────────────────────────
    # score = number of correct answers; total = questions asked.
    # Sorted by score DESC, then played_at DESC on the leaderboard
    # so ties show the most recent attempt first.
    cur.execute("""CREATE TABLE IF NOT EXISTS trivia_scores(
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        account_id  INTEGER REFERENCES accounts(id) ON DELETE SET NULL,
        player_name TEXT NOT NULL,
        score       INTEGER NOT NULL,
        total       INTEGER NOT NULL,
        played_at   TEXT NOT NULL
    )""")

    # ── songs table ───────────────────────────────────────────────
    # Read-only catalogue seeded from DEFAULT_SONGS below.
    # is_default=1 for all seeded rows; reserved for a potential
    # future "featured" vs "community" distinction.
    cur.execute("""CREATE TABLE IF NOT EXISTS songs(
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        title      TEXT NOT NULL UNIQUE COLLATE NOCASE,
        is_default INTEGER NOT NULL DEFAULT 1,
        added_at   TEXT NOT NULL
    )""")

    # ── account_settings table ────────────────────────────────────
    # account_id is both the PRIMARY KEY and a FK to accounts.
    # ON DELETE CASCADE: if an account is deleted, its settings row
    # is automatically removed — no orphan rows.
    #
    # Five audio columns (added incrementally across versions):
    #   master_volume — overall multiplier (maps to C.MASTER_VOLUME)
    #   volume        — legacy alias for music_volume (kept for old DBs)
    #   music_volume  — BGM / gameplay track level (C.MUSIC_VOLUME)
    #   sfx_volume    — sound effects channel (C.SFX_VOLUME)
    #   sfx_intensity — visual effect density (C.SFX_INTENSITY)
    cur.execute("""CREATE TABLE IF NOT EXISTS account_settings(
        account_id     INTEGER PRIMARY KEY REFERENCES accounts(id) ON DELETE CASCADE,
        master_volume  REAL    NOT NULL DEFAULT 1.00,
        volume         REAL    NOT NULL DEFAULT 0.85,
        music_volume   REAL    NOT NULL DEFAULT 0.85,
        sfx_volume     REAL    NOT NULL DEFAULT 0.85,
        sfx_intensity  REAL    NOT NULL DEFAULT 1.00,
        sfx_enabled    INTEGER NOT NULL DEFAULT 1,
        difficulty     TEXT    NOT NULL DEFAULT 'Normal',
        num_lanes      INTEGER NOT NULL DEFAULT 5,
        fullscreen     INTEGER NOT NULL DEFAULT 0
    )""")

    # ── Safe schema migration: add new columns to existing DBs ────
    # Players who installed an earlier version already have an
    # account_settings table without the v17 columns.  ALTER TABLE
    # adds the missing columns; OperationalError is raised (and
    # silently swallowed) when the column already exists.
    for col_def in [
        "master_volume  REAL NOT NULL DEFAULT 1.00",
        "music_volume   REAL NOT NULL DEFAULT 0.85",
        "sfx_intensity  REAL NOT NULL DEFAULT 1.00",
    ]:
        try:
            cur.execute(f"ALTER TABLE account_settings ADD COLUMN {col_def}")
        except sqlite3.OperationalError:
            pass   # column already present — safe to ignore

    # ── Seed default songs ────────────────────────────────────────
    # INSERT OR IGNORE skips duplicates so re-running init_db() on an
    # existing database never creates duplicate song rows.
    for title in DEFAULT_SONGS:
        cur.execute(
            "INSERT OR IGNORE INTO songs(title, is_default, added_at) VALUES(?, 1, ?)",
            (title, _now())
        )

    c.commit()
    c.close()


# ══════════════════════════════════════════════════════════════════
#  ACCOUNT MANAGEMENT
# ══════════════════════════════════════════════════════════════════

def username_exists(username: str) -> bool:
    """
    Return True if `username` already exists in the accounts table
    (case-insensitive comparison via COLLATE NOCASE).

    Called by validate_username() before every INSERT or UPDATE so
    the player sees a "taken" error before any DB write attempt.
    Also serves as a lightweight pre-check in the UI's live validation.
    """
    c   = _conn()
    row = c.execute(
        "SELECT id FROM accounts WHERE username=? COLLATE NOCASE",
        (username.strip(),)
    ).fetchone()
    c.close()
    return row is not None


def create_account(username: str, password: str,
                   avatar_col: str = "#FFD700") -> dict:
    """
    Create a new player account after validating both inputs.

    Process:
      1. validate_username() — checks length, chars, and uniqueness.
      2. validate_password() — checks length and whitespace policy.
      3. INSERT into accounts with a hashed password.
      4. INSERT a default row into account_settings (so load_settings()
         always finds a row for newly created accounts).
      5. Return the full account row as a dict.

    The IntegrityError catch on step 3 handles the rare race condition
    where another thread inserted the same username between the
    validate_username() check and the INSERT — treated as "USERNAME_TAKEN".

    Returns:
        dict with keys: id, username, pass_hash, avatar_col,
                        created_at, last_login.

    Raises:
        AccountError — on any validation failure or uniqueness conflict.

    Called by:
        bgyo_game._show_register() → do_register() on form submission.
    """
    # Validate first — raises AccountError with a specific code on failure
    clean_user = validate_username(username)
    validate_password(password)

    c = _conn()
    try:
        c.execute(
            "INSERT INTO accounts(username, pass_hash, avatar_col, created_at)"
            " VALUES(?, ?, ?, ?)",
            (clean_user, _hash(password), avatar_col, _now())
        )
        c.commit()
        row = c.execute(
            "SELECT * FROM accounts WHERE username=? COLLATE NOCASE",
            (clean_user,)
        ).fetchone()
        # Seed a default settings row so load_settings() always finds one
        c.execute(
            "INSERT OR IGNORE INTO account_settings(account_id) VALUES(?)",
            (row["id"],)
        )
        c.commit()
        return dict(row)
    except sqlite3.IntegrityError:
        # Race condition: username was taken between the check and the write
        raise AccountError("USERNAME_TAKEN",
                           f'"{clean_user}" is taken. Try another.')
    finally:
        c.close()


def login(username: str, password: str):
    """
    Authenticate a player and return their account dict on success.

    Process:
      1. Fetch the account row by username (case-insensitive).
      2. Hash the supplied password and compare to stored hash.
      3. On match, UPDATE last_login timestamp and return the row as dict.
      4. On mismatch or missing user, return None (caller shows generic
         "invalid credentials" message — never reveal which field is wrong).

    Returns:
        dict  — full account row on success.
        None  — on wrong username or password.

    Called by:
        bgyo_game._show_login() → do_login() on form submission.
    """
    c = _conn()
    try:
        row = c.execute(
            "SELECT * FROM accounts WHERE username=? COLLATE NOCASE",
            (username.strip(),)
        ).fetchone()
        if row and row["pass_hash"] == _hash(password):
            # Record login timestamp for display in the profile screen
            c.execute("UPDATE accounts SET last_login=? WHERE id=?",
                      (_now(), row["id"]))
            c.commit()
            return dict(row)
        return None   # wrong username or wrong password — same response for both
    finally:
        c.close()


def update_avatar(account_id: int, avatar_col: str):
    """
    Persist a new avatar colour for account_id.
    avatar_col is a '#RRGGBB' hex string chosen from AVATAR_COLORS.

    Called by bgyo_game when the player picks a colour in the
    account/profile settings screen.
    """
    c = _conn()
    c.execute("UPDATE accounts SET avatar_col=? WHERE id=?",
              (avatar_col, account_id))
    c.commit()
    c.close()


def change_password(account_id: int, old_pw: str, new_pw: str) -> bool:
    """
    Change the password for account_id after verifying the old password.

    Process:
      1. validate_password(new_pw) — enforces the 6-char / no-whitespace policy.
      2. Fetch the stored hash for account_id.
      3. Compare hash(old_pw) to the stored hash.
      4. On match, UPDATE pass_hash to hash(new_pw).

    Returns:
        True  — password changed successfully.
        False — old password did not match (or account not found).

    Raises:
        AccountError — if new_pw fails the password policy.

    Called by: bgyo_game settings screen (change-password flow).
    """
    validate_password(new_pw)   # policy check on the new password
    c = _conn()
    try:
        row = c.execute(
            "SELECT pass_hash FROM accounts WHERE id=?",
            (account_id,)
        ).fetchone()
        if not row or row["pass_hash"] != _hash(old_pw):
            return False   # old password wrong — do not update
        c.execute("UPDATE accounts SET pass_hash=? WHERE id=?",
                  (_hash(new_pw), account_id))
        c.commit()
        return True
    finally:
        c.close()


def change_username(account_id: int, new_username: str) -> str:
    """
    Change the username for account_id.

    Process:
      1. validate_username(new_username) — checks length, chars, uniqueness.
      2. UPDATE the accounts row.
      3. IntegrityError on step 2 means a concurrent insert took the name —
         re-raised as AccountError("USERNAME_TAKEN", ...).

    Returns:
        The cleaned (stripped) new username string on success.

    Raises:
        AccountError — on any validation failure or uniqueness conflict.

    Called by: bgyo_game settings screen (change-username flow).
    """
    clean = validate_username(new_username)   # raises AccountError if invalid/taken
    c = _conn()
    try:
        c.execute("UPDATE accounts SET username=? WHERE id=?",
                  (clean, account_id))
        c.commit()
        return clean
    except sqlite3.IntegrityError:
        raise AccountError("USERNAME_TAKEN",
                           f'Username "{clean}" is already taken.')
    finally:
        c.close()


def get_account(account_id: int):
    """
    Fetch and return a single account row by primary key.

    Returns:
        dict  — account row if found.
        None  — if no row with account_id exists.

    Called by: bgyo_game when refreshing session data after a profile change.
    """
    c = _conn()
    try:
        row = c.execute("SELECT * FROM accounts WHERE id=?",
                        (account_id,)).fetchone()
        return dict(row) if row else None
    finally:
        c.close()


# ══════════════════════════════════════════════════════════════════
#  SETTINGS  (per-account audio/gameplay preferences)
# ══════════════════════════════════════════════════════════════════

# Default values returned by load_settings() when no row exists for
# account_id, or when a column is missing from an older database.
# Mirrors the defaults in settings_state._Settings.__init__().
_SETTINGS_DEFAULTS = {
    "master_volume": 1.00,
    "volume":        0.85,   # legacy alias for music_volume
    "music_volume":  0.85,
    "sfx_volume":    0.85,
    "sfx_intensity": 1.00,
    "sfx_enabled":   True,
    "difficulty":    "Normal",
    "num_lanes":     5,
    "fullscreen":    False,
}


def load_settings(account_id: int) -> dict:
    """
    Load the persisted settings for account_id and return them as a dict.

    If no settings row exists (brand-new account before save_settings()
    has been called), returns a copy of _SETTINGS_DEFAULTS.

    Also fills in defaults for any column that is NULL or missing in
    the database row — this handles old databases that pre-date the
    master_volume and music_volume columns added in v17.

    Boolean columns (sfx_enabled, fullscreen) are stored as SQLite
    integers (0/1) and explicitly coerced to Python bool here so
    callers never need to do int→bool conversion.

    Called by:
        settings_state._Settings.load_from_db(account_id)
        — which is itself called on login and after account creation.
    """
    c = _conn()
    try:
        row = c.execute(
            "SELECT * FROM account_settings WHERE account_id=?",
            (account_id,)
        ).fetchone()
        if not row:
            return dict(_SETTINGS_DEFAULTS)   # first login, no saved settings yet
        d = dict(row)
        # Coerce SQLite INTEGER columns to Python bool
        d["sfx_enabled"] = bool(d.get("sfx_enabled", 1))
        d["fullscreen"]  = bool(d.get("fullscreen",  0))
        # Fill missing columns with defaults (old-DB safety net)
        for key, default in _SETTINGS_DEFAULTS.items():
            if key not in d or d[key] is None:
                d[key] = default
        return d
    finally:
        c.close()


def save_settings(account_id: int,
                  master_volume:  float = 1.00,
                  volume:         float = 0.85,
                  music_volume:   float = 0.85,
                  sfx_volume:     float = 0.85,
                  sfx_intensity:  float = 1.00,
                  sfx_enabled:    bool  = True,
                  difficulty:     str   = "Normal",
                  num_lanes:      int   = 5,
                  fullscreen:     bool  = False):
    """
    Persist (upsert) settings for account_id.

    Uses INSERT … ON CONFLICT DO UPDATE (SQLite UPSERT syntax) so
    the function is safe to call whether or not a settings row already
    exists for this account — no separate "create vs update" logic needed.

    All float values are clamped to [0.0, 1.0] before storage so
    the DB never contains out-of-range slider values even if the UI
    somehow passes them through.

    Called by:
        settings_state._Settings.save_to_db(account_id)
        — which is itself called when the player clicks "Save & Close"
          in the settings screen.
    """
    c = _conn()
    c.execute(
        """INSERT INTO account_settings
               (account_id, master_volume, volume, music_volume,
                sfx_volume, sfx_intensity, sfx_enabled,
                difficulty, num_lanes, fullscreen)
           VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(account_id) DO UPDATE SET
               master_volume  = excluded.master_volume,
               volume         = excluded.volume,
               music_volume   = excluded.music_volume,
               sfx_volume     = excluded.sfx_volume,
               sfx_intensity  = excluded.sfx_intensity,
               sfx_enabled    = excluded.sfx_enabled,
               difficulty     = excluded.difficulty,
               num_lanes      = excluded.num_lanes,
               fullscreen     = excluded.fullscreen""",
        (
            account_id,
            max(0.0, min(1.0, float(master_volume))),
            max(0.0, min(1.0, float(volume))),
            max(0.0, min(1.0, float(music_volume))),
            max(0.0, min(1.0, float(sfx_volume))),
            max(0.0, min(1.0, float(sfx_intensity))),
            int(bool(sfx_enabled)),    # store as 0/1 for SQLite INTEGER column
            difficulty,
            int(num_lanes),
            int(bool(fullscreen)),     # store as 0/1
        )
    )
    c.commit()
    c.close()


# ══════════════════════════════════════════════════════════════════
#  RHYTHM-GAME SCORES
# ══════════════════════════════════════════════════════════════════

def save_score(player_name: str, score: int, accuracy: int, max_combo: int,
               grade: str, difficulty: str = "Normal", song_name: str = "Unknown",
               mode: str = "Single", account_id=None):
    """
    Persist the result of one completed rhythm-game session.

    player_name is truncated to 24 characters to match the leaderboard
    display width.  account_id is nullable — guest sessions pass None.
    No delete API exists; all scores are permanent on the leaderboard.

    Called by:
        bgyo_game._show_result() when the player submits their name
        after a game-over or song-complete event.
    """
    c = _conn()
    c.execute(
        """INSERT INTO scores
               (account_id, player_name, score, accuracy, max_combo,
                grade, difficulty, song_name, mode, played_at)
           VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (account_id, player_name.strip()[:24], int(score), int(accuracy),
         int(max_combo), grade, difficulty, song_name, mode, _now())
    )
    c.commit()
    c.close()


def load_scores(limit: int = 50) -> list:
    """
    Return the top `limit` rhythm-game scores across all difficulties,
    ordered by score DESC.

    Each element is a plain dict (column-name keys).
    Called by bgyo_game._show_leaderboard() for the "All" tab view.
    """
    c = _conn()
    try:
        rows = c.execute(
            "SELECT * FROM scores ORDER BY score DESC LIMIT ?",
            (limit,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        c.close()


def load_scores_for_account(account_id: int, limit: int = 20) -> list:
    """
    Return the top `limit` rhythm-game scores for a specific account,
    ordered by score DESC.

    Used by bgyo_game to show a player's personal best scores on
    their profile or result screen.
    """
    c = _conn()
    try:
        rows = c.execute(
            "SELECT * FROM scores WHERE account_id=? ORDER BY score DESC LIMIT ?",
            (account_id, limit)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        c.close()


def load_top_scores_by_difficulty(difficulty: str = "Normal",
                                  limit: int = 50) -> list:
    """
    Return the top `limit` rhythm-game scores filtered to a specific
    difficulty level, ordered by score DESC.

    Called by bgyo_game._show_leaderboard() for each difficulty-filter
    tab (Easy / Normal / Hard / ACE).  The `difficulty` value matches
    the keys in constants.DIFFICULTY.
    """
    c = _conn()
    try:
        rows = c.execute(
            "SELECT * FROM scores WHERE difficulty=? ORDER BY score DESC LIMIT ?",
            (difficulty, limit)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        c.close()


# ══════════════════════════════════════════════════════════════════
#  TRIVIA SCORES
# ══════════════════════════════════════════════════════════════════

def save_trivia_score(player_name: str, score: int, total: int,
                      account_id=None):
    """
    Persist the result of one completed Aces Trivia session.

    score / total : correct answers / total questions shown in that session.
    player_name is truncated to 24 chars and defaulted to "Anonymous" if
    the caller somehow provides a blank name (shouldn't happen — the UI
    validates before calling, but this is a safety net).

    No delete API exists; all trivia scores are permanent.

    Called by:
        bgyo_game._show_trivia() after the player enters their name
        in the post-game name-prompt dialog.
    """
    name = player_name.strip()[:24]
    if not name:
        name = "Anonymous"   # safety fallback — UI should prevent blank names
    c = _conn()
    c.execute(
        """INSERT INTO trivia_scores(account_id, player_name, score, total, played_at)
           VALUES(?, ?, ?, ?, ?)""",
        (account_id, name, int(score), int(total), _now())
    )
    c.commit()
    c.close()


def load_trivia_scores(limit: int = 30) -> list:
    """
    Return the top `limit` trivia scores across all players, ordered
    by score DESC then played_at DESC (most recent result wins ties).

    Called by bgyo_game._show_leaderboard() for the Trivia tab.
    """
    c = _conn()
    try:
        rows = c.execute(
            """SELECT * FROM trivia_scores
               ORDER BY score DESC, played_at DESC
               LIMIT ?""",
            (limit,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        c.close()


def load_trivia_scores_for_account(account_id: int, limit: int = 20) -> list:
    """
    Return the top `limit` trivia scores for a specific account,
    ordered by score DESC then played_at DESC.

    Used by bgyo_game to display a player's personal trivia history
    on the profile or result screen.
    """
    c = _conn()
    try:
        rows = c.execute(
            """SELECT * FROM trivia_scores
               WHERE account_id=?
               ORDER BY score DESC, played_at DESC
               LIMIT ?""",
            (account_id, limit)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        c.close()


# ══════════════════════════════════════════════════════════════════
#  SONGS  (read-only catalogue)
# ══════════════════════════════════════════════════════════════════

def get_song_titles() -> list:
    """
    Return all song titles from the `songs` table, ordered alphabetically.
    Includes both default and any non-default entries (none currently exist
    since the upload feature was removed in v17).
    """
    c = _conn()
    try:
        rows = c.execute("SELECT title FROM songs ORDER BY title").fetchall()
        return [r["title"] for r in rows]
    finally:
        c.close()


def get_default_song_titles() -> list:
    """
    Return only the default (is_default=1) song titles, ordered alphabetically.
    Used when the game needs to reset to the canonical song list.
    """
    c = _conn()
    try:
        rows = c.execute(
            "SELECT title FROM songs WHERE is_default=1 ORDER BY title"
        ).fetchall()
        return [r["title"] for r in rows]
    finally:
        c.close()


# ══════════════════════════════════════════════════════════════════
#  GUEST SENTINEL
# ══════════════════════════════════════════════════════════════════

# A minimal "account" dict that represents a non-logged-in player.
# Used by settings_state._Session.__init__() and _Session.logout()
# so the rest of the codebase can always treat session.account as a
# dict with the same keys as a real account row.
#
# Key design choices:
#   id = None       — settings_state.save_to_db() checks `if account_id is None`
#                     and skips the DB write, so guest preference changes are
#                     never persisted across sessions.
#   username = "GUEST" — displayed in the HUD and profile badge.
#   avatar_col = "#888888" — neutral grey, visually distinct from member colours.
GUEST = {"id": None, "username": "GUEST", "avatar_col": "#888888"}


# ══════════════════════════════════════════════════════════════════
#  AUTO-INITIALISE ON IMPORT
# ══════════════════════════════════════════════════════════════════

# Called once when any module does `import database`.
# Creates all tables and seeds DEFAULT_SONGS if the DB file is new.
# Safe to call on an existing database (all CREATE IF NOT EXISTS).
init_db()
