"""
╔══════════════════════════════════════════════════════════════════╗
║   BGYO: THE LIGHT STAGE — ACES OF P-POP  v18.1                 ║
║   file_helpers.py  ·  MP3 / cover / preview path utilities      ║
╚══════════════════════════════════════════════════════════════════╝

PURPOSE & ROLE IN THE ARCHITECTURE
────────────────────────────────────
This module is the single gateway for resolving asset file paths on
disk.  No other module calls os.listdir() on the songs or images
directories — all path lookups are centralised here so the matching
logic is maintained in one place.

    constants.py  ←  file_helpers.py  ←  bgyo_game.py
                                       ←  audio_engine.py  (SONGS_DIR, PREVIEW_DIR only)

Consumers and what they use:
  bgyo_game.py   — find_mp3(), find_preview(), find_cover(),
                   get_song_info(), get_all_playable_songs(),
                   ensure_dirs() on startup.
  audio_engine   — imports SONGS_DIR / PREVIEW_DIR from constants
                   directly for BGM rotation; does NOT use this module.

THE MATCHING PROBLEM
─────────────────────
Song titles in DEFAULT_SONGS (database.py) are clean, human-readable
names like "The Light" or "Best Time".  The actual MP3 files on disk
often have messy YouTube-download filenames like:

    "BGYO - The Light (Lyrics).mp3"
    "best_time_hes_into_her_season_2_ost.mp3"
    "bgyo___all_these_ladies_official_lyric_video.mp3"

Three complementary strategies handle this gap:

  1. Multi-pass substring matching  (find_mp3, find_cover)
       Pass 1 — exact stem match
       Pass 2 — song name is a substring of filename
       Pass 3 — strip common "bgyo - " prefix, then compare
       Pass 4 — alias map lookup (_MP3_ALIASES, _COVER_ALIASES)

  2. Fuzzy keyword matching  (find_preview — v18.1)
       Extract keywords from the song name, then check that ALL
       keywords appear anywhere in the filename (any order).
       "Best Time" → keywords ["best", "time"] → matches
       "best_time_hes_into_her.mp3"

  3. Explicit alias maps  (_MP3_ALIASES, _COVER_ALIASES)
       Hard-coded {stem → title} dicts for files whose names are too
       far from the song title for the automatic passes to match.

v18.1 CHANGES
──────────────
  find_preview() — ENHANCED fuzzy keyword matching:
    - _extract_keywords() splits the song name into individual words,
      strips common English stop-words, and returns a lowercase list.
    - _fuzzy_match() returns True only when ALL extracted keywords
      appear in the filename (any order, any position).
    - This replaces the previous simple substring check which failed
      for titles like "Best Time" against filenames that embed the
      song name in a longer string like "best_time_hes_into_her.mp3".
"""

import os, re
from constants import SONGS_DIR, PREVIEW_DIR, COVERS_DIR


# ══════════════════════════════════════════════════════════════════
#  FILENAME ALIAS MAPS
# ══════════════════════════════════════════════════════════════════
#
# These dicts map the exact lowercase filename stem (no extension) of
# an uploaded/downloaded file to the canonical song title used in
# DEFAULT_SONGS.  They are consulted as a last resort in Pass 4 of
# find_mp3() and find_cover() after all automatic matching strategies
# have failed.
#
# When to add an entry:
#   • The filename has no meaningful overlap with the song title
#     (e.g. "bgyo___all_these_ladies_official_lyric_video" vs
#      "All These Ladies") after prefix-stripping.
#   • The fuzzy/substring passes produce a false match on a different
#     song (e.g. a file named "bgyo_the_light_official" also matching
#     "The Baddest" because neither keyword appears in the other).
#
# Key  : the full filename stem in lowercase, exactly as os.listdir()
#         would return it (minus the ".mp3" / ".png" extension).
# Value: the canonical song title string from DEFAULT_SONGS.

_MP3_ALIASES: dict[str, str] = {
    # YouTube-downloaded MP3s whose names don't match the song title
    "be_us_-_bgyo_x_moophs__lyrics_":                       "Be Us",
    "best_time_-_bgyo__lyrics___hes_into_her_season_2_ost": "Best Time",
    "bgyo___all_these_ladies_official_lyric_video":         "All These Ladies",
    "bgyo_-_andito_lang__lyrics_":                          "Andito Lang",
    "bgyo__divine__official_lyric_video_":                  "Divine",
    "bgyo_-_fly_away__lyrics_":                             "Fly Away",
    "bgyo__fresh_official_lyric_video":                     "Fresh",
    "bgyo_-_rocketman__lyrics_":                            "Rocketman",
    "bgyo_-_sabay__lyrics_":                                "Sabay",
}

_COVER_ALIASES: dict[str, str] = {
    # Cover art files whose stems are abbreviations or alternate names
    "shuffle":             "SHUFFLE",
    "all_these_ladies":    "All These Ladies",
    "be_us":               "Be Us",
    "best_time":           "Best Time",
    "dance_with_me":       "Dance With Me",
    "fly_away":            "Fly Away",
    "fresh":               "Fresh",
    "kabataang_pinoy":     "Kabataang Pinoy",
    "kulay":               "Kulay",
    "kundiman":            "Kundiman",
    "live_vivid":          "Live Vivid",
    "magnet":              "Magnet",
    "mahal_na_kita":       "Mahal Na Kita",
    "patuloy_lang":        "Patuloy Lang Ang Lipad",
    "rocketman":           "Rocketman",
    "runnin":              "Runnin'",
    "sabay":               "Sabay",
    "the_baddest":         "The Baddest",
    "the_light":           "The Light",
    "tumitigil_ang_mundo": "Tumitigil Ang Mundo",
    "up":                  "Up!",
    "while_we_are_young":  "While We Are Young",
}


# ══════════════════════════════════════════════════════════════════
#  INTERNAL HELPERS
# ══════════════════════════════════════════════════════════════════

def _strip_prefix(stem: str) -> str:
    """
    Remove common "BGYO - " style prefixes from a filename stem.

    Many downloaded BGYO files begin with "bgyo - ", "bgyo_", or
    similar variants.  Stripping this prefix before comparison gives
    Pass 3 in find_mp3() / find_cover() a cleaner string to match
    against the song title.

    The prefix list is checked in order; the first matching prefix is
    removed and the remainder is stripped of leading/trailing whitespace.
    If no prefix matches, the original stem is returned unchanged.

    Examples:
        _strip_prefix("bgyo - the light")  →  "the light"
        _strip_prefix("bgyo_rocketman")    →  "rocketman"
        _strip_prefix("the baddest")       →  "the baddest"  (unchanged)
    """
    for prefix in ("bgyo - ", "bgyo- ", "bgyo -", "bgyo_", "bgyo"):
        if stem.startswith(prefix):
            return stem[len(prefix):].strip()
    return stem


def _resolve_mp3_alias(stem: str) -> str:
    """
    Look up a filename stem in _MP3_ALIASES and return the canonical
    song title, or the original stem if no alias entry exists.

    stem is lowercased before lookup (the alias map keys are all
    lowercase), so the caller does not need to normalise it first.

    Used by find_mp3() in Pass 4 as the last-resort matching step.
    """
    return _MP3_ALIASES.get(stem.lower(), stem)


def _resolve_cover_alias(stem: str) -> str:
    """
    Look up a filename stem in _COVER_ALIASES and return the canonical
    song title, or the original stem if no alias entry exists.

    Used by find_cover() in Pass 4 as the last-resort matching step.
    """
    return _COVER_ALIASES.get(stem.lower(), stem)


def _extract_keywords(text: str) -> list:
    """
    Extract a list of meaningful lowercase keywords from a song title.

    Used by _fuzzy_match() (v18.1) to decompose a song name into
    individual searchable tokens before checking a filename.

    Process:
      1. Lowercase the input.
      2. Replace all non-alphanumeric characters (punctuation, hyphens,
         apostrophes) with spaces using re.sub(r'[^\\w\\s]', ' ', text).
      3. Split on whitespace to get individual words.
      4. Filter out:
           • Single-character words (likely noise after punctuation removal)
           • English stop-words (the, a, an, and, or, …) that are too
             common to be meaningful discriminators in filenames.

    The stop-word list is deliberately small — only the most common
    function words.  Content words like "with", "me", "my" are kept
    because they *do* appear meaningfully in song titles like
    "Dance With Me".

    Examples:
        _extract_keywords("The Light")     →  ["light"]
        _extract_keywords("Best Time")     →  ["best", "time"]
        _extract_keywords("He's Into Her") →  ["hes", "into", "her"]
        _extract_keywords("Be Us")         →  ["us"]   ("be" is 2 chars, kept)
        _extract_keywords("All These Ladies") →  ["these", "ladies"]

    Note: "The" is a stop-word, so "The Light" yields only ["light"].
    This is intentional — "light" is the discriminating keyword; "the"
    would match almost every sentence.
    """
    # Remove punctuation (apostrophes in "He's", hyphens, etc.) → spaces
    text = re.sub(r'[^\w\s]', ' ', text.lower())
    words = text.split()

    # Stop-words that add noise without discriminating between songs
    stopwords = {
        'the', 'a', 'an', 'and', 'or', 'but', 'in', 'on', 'at',
        'to', 'for', 'of', 'with', 'by', 'from', 'as', 'is',
        'was', 'are', 'were'
    }

    # Keep words longer than 1 character that aren't stop-words
    keywords = [w for w in words if len(w) > 1 and w not in stopwords]
    return keywords


def _fuzzy_match(song_name: str, filename: str) -> bool:
    """
    Return True if ALL keywords extracted from song_name appear in
    filename (case-insensitive, any order, any position).

    This is the v18.1 matching strategy used by find_preview().  It is
    more flexible than a plain substring check because:

      • "The Light" (keywords: ["light"]) matches "bgyo_the_light_preview.mp3"
        even though the song title is not a verbatim substring of the filename.

      • "Best Time" (keywords: ["best", "time"]) matches
        "best_time_hes_into_her.mp3" even though the filename contains
        additional words between and after the keywords.

      • False positives are avoided by requiring ALL keywords to match,
        not just one.  "The Baddest" (keywords: ["baddest"]) will NOT
        match a "the_light_preview.mp3" file.

    Process:
      1. Call _extract_keywords(song_name) to get the keyword list.
      2. Lowercase filename.
      3. Return True iff every keyword is a substring of the filename.

    Args:
        song_name — Canonical song title (e.g. "Best Time").
        filename  — Filename string to test against (e.g. "best_time_preview.mp3").

    Examples:
        _fuzzy_match("The Light",  "bgyo_the_light_preview.mp3")  →  True
        _fuzzy_match("Best Time",  "best_time_hes_into_her.mp3")  →  True
        _fuzzy_match("The Light",  "the_baddest_official.mp3")    →  False
        _fuzzy_match("Dance With Me", "dance_with_me_bgyo.mp3")   →  True
    """
    keywords = _extract_keywords(song_name)
    filename_lower = filename.lower()
    # ALL keywords must appear somewhere in the filename
    return all(kw in filename_lower for kw in keywords)


# ══════════════════════════════════════════════════════════════════
#  MP3 LOOKUP
# ══════════════════════════════════════════════════════════════════

def find_mp3(song_name: str) -> str | None:
    """
    Locate the full-length MP3 for song_name inside SONGS_DIR.

    Returns the absolute path to the first matching file, or None if
    no match is found after all four passes.

    ── SEARCH PASSES (in order) ────────────────────────────────────

    Pass 1 — Exact stem match
        Compares the lowercase filename stem (no extension) against
        the lowercased song name.
        Matches: "The Light.mp3" for song_name="The Light"

    Pass 2 — Substring match
        Checks whether the song name is a substring of the lowercase
        filename (song name embedded anywhere in the filename).
        Matches: "BGYO - The Light (Lyrics).mp3" for "The Light"

    Pass 3 — Prefix-stripped match
        Strips common "bgyo - " prefixes from the filename stem via
        _strip_prefix(), then tests exact, contains, and contained-by
        relationships.
        Matches: "bgyo_rocketman_official.mp3" after stripping "bgyo_"
                 → "rocketman_official" which contains "rocketman"

    Pass 4 — Alias map
        Consults _MP3_ALIASES with the raw filename stem.  Used for
        files whose names have no meaningful keyword overlap with the
        song title even after prefix-stripping.
        Matches: "bgyo___all_these_ladies_official_lyric_video.mp3"
                 → alias maps to "All These Ladies"

    Only SONGS_DIR is searched (not PREVIEW_DIR), because preview clips
    are separate shorter files managed by find_preview().

    Called by:
        bgyo_game._show_song_select()    — checks playability of each song
        bgyo_game._start_game()          — resolves the path before audio.play()
        file_helpers.get_all_playable_songs() — filters DEFAULT_SONGS list
    """
    if not os.path.isdir(SONGS_DIR):
        return None

    nl    = song_name.lower().strip()
    # Collect all .mp3 files in SONGS_DIR (not subdirectories)
    cands = [f for f in os.listdir(SONGS_DIR) if f.lower().endswith(".mp3")]

    # Pass 1 — Exact stem
    for f in cands:
        if os.path.splitext(f)[0].lower() == nl:
            return os.path.join(SONGS_DIR, f)

    # Pass 2 — Song name is a substring of the filename
    for f in cands:
        if nl in f.lower():
            return os.path.join(SONGS_DIR, f)

    # Pass 3 — Strip "bgyo - " prefix, then exact / contains / contained-by
    for f in cands:
        stem = _strip_prefix(os.path.splitext(f)[0].lower())
        if stem == nl or stem in nl or nl in stem:
            return os.path.join(SONGS_DIR, f)

    # Pass 4 — Explicit alias map (last resort for non-matching filenames)
    for f in cands:
        raw_stem  = os.path.splitext(f)[0]
        canonical = _resolve_mp3_alias(raw_stem).lower()
        if canonical == nl:
            return os.path.join(SONGS_DIR, f)

    return None


# ══════════════════════════════════════════════════════════════════
#  PREVIEW LOOKUP
# ══════════════════════════════════════════════════════════════════

def find_preview(song_name: str) -> str | None:
    """
    Locate a short preview clip for song_name.

    Searches PREVIEW_DIR first, then falls back to SONGS_DIR (some
    setups store short clips alongside full tracks rather than in the
    preview subfolder).

    Returns the absolute path to the first match, or None.

    ── SEARCH STRATEGY (v18.1) ─────────────────────────────────────

    For each search directory (PREVIEW_DIR, then SONGS_DIR):

      Pass 1 — Exact stem match
          Fastest check — the preview file is named exactly after the
          song title (e.g. "The Light.mp3").

      Pass 2 — Fuzzy keyword match  (NEW in v18.1)
          Calls _fuzzy_match(song_name, filename).  Returns the first
          file where ALL keywords from the song title appear in the
          filename in any order.
          This handles cases like:
            "Best Time" → ["best", "time"] matches
            "best_time_hes_into_her_season_2_ost.mp3"

      Pass 3 — Substring match  (PREVIEW_DIR only)
          Simple case-insensitive substring check.  Only applied in
          PREVIEW_DIR (not SONGS_DIR) to avoid accidentally matching
          a full-length track with a song title that is a substring of
          a different song's filename (e.g. "Light" matching
          "the_light_show_extended.mp3").

    The SONGS_DIR fallback only uses passes 1 and 2 (no substring),
    for the same false-match avoidance reason.

    Called by:
        bgyo_game._show_song_select() — triggered ~700 ms after the
        user highlights a song in the carousel, via a delayed after()
        callback, to auto-play a short preview.
    """
    nl = song_name.lower().strip()

    for search_dir in (PREVIEW_DIR, SONGS_DIR):
        if not os.path.isdir(search_dir):
            continue
        cands = [f for f in os.listdir(search_dir) if f.lower().endswith(".mp3")]

        # Pass 1 — Exact stem match
        for f in cands:
            if os.path.splitext(f)[0].lower() == nl:
                return os.path.join(search_dir, f)

        # Pass 2 — Fuzzy keyword match (v18.1 enhancement)
        # All keywords from the song name must appear in the filename.
        for f in cands:
            if _fuzzy_match(song_name, f):
                return os.path.join(search_dir, f)

        # Pass 3 — Substring match (PREVIEW_DIR only, to avoid false positives
        # from the longer/more-varied filenames that live in SONGS_DIR)
        if search_dir == PREVIEW_DIR:
            for f in cands:
                if nl in f.lower():
                    return os.path.join(search_dir, f)

    return None


# ══════════════════════════════════════════════════════════════════
#  COVER ART LOOKUP
# ══════════════════════════════════════════════════════════════════

def find_cover(song_name: str) -> str | None:
    """
    Locate cover art for song_name inside COVERS_DIR.

    Accepts PNG, JPG, JPEG, WEBP, and AVIF formats.
    Returns the absolute path to the first matching file, or None.

    ── SEARCH PASSES (in order) ────────────────────────────────────

    Pass 1 — Exact stem match
        Filename stem equals song name exactly (case-insensitive).
        Matches: "The Light.png" for song_name="The Light"

    Pass 2 — Substring match
        Song name appears anywhere in the filename stem.
        Matches: "bgyo - the light cover.jpg" for "The Light"

    Pass 3 — Prefix-stripped match
        Strips "bgyo - " prefix from filename stem via _strip_prefix(),
        then tests exact, contains, and contained-by relationships.
        Matches: "bgyo_the_baddest.png" → strip → "the_baddest" → matches

    Pass 4 — Cover alias map
        Consults _COVER_ALIASES with the raw filename stem.
        Used for cover files stored under shortened names:
        "patuloy_lang.png" → alias → "Patuloy Lang Ang Lipad"

    Called by:
        bgyo_game._load_cover_image()   — loads + caches cover for HUD display
        bgyo_game._show_song_select()   — thumbnail in the song carousel
        file_helpers.list_all_covers()  — bulk pre-cache on song-select open
    """
    if not os.path.isdir(COVERS_DIR):
        return None

    nl   = song_name.lower().strip()
    exts = (".png", ".jpg", ".jpeg", ".webp", ".avif")
    cands = [
        f for f in os.listdir(COVERS_DIR)
        if any(f.lower().endswith(e) for e in exts)
    ]

    # Pass 1 — Exact stem
    for f in cands:
        if os.path.splitext(f)[0].lower() == nl:
            return os.path.join(COVERS_DIR, f)

    # Pass 2 — Substring
    for f in cands:
        if nl in f.lower():
            return os.path.join(COVERS_DIR, f)

    # Pass 3 — Prefix-stripped comparison
    for f in cands:
        stem = _strip_prefix(os.path.splitext(f)[0].lower())
        if stem == nl or stem in nl or nl in stem:
            return os.path.join(COVERS_DIR, f)

    # Pass 4 — Explicit cover alias map
    for f in cands:
        raw_stem  = os.path.splitext(f)[0]
        canonical = _resolve_cover_alias(raw_stem).lower()
        if canonical == nl:
            return os.path.join(COVERS_DIR, f)

    return None


# ══════════════════════════════════════════════════════════════════
#  CONVENIENCE BUNDLE
# ══════════════════════════════════════════════════════════════════

def get_song_info(song_name: str) -> dict:
    """
    Resolve all three asset paths for a song in a single call.

    Returns a dict with four keys:
        {
          'name'   : str,         — the original song_name argument
          'mp3'    : str | None,  — absolute path to full-length MP3
          'preview': str | None,  — absolute path to preview clip
          'cover'  : str | None,  — absolute path to cover art image
        }

    Any value that cannot be resolved is None — callers must handle
    the None case (e.g. display a placeholder cover, skip preview).

    Used by:
        bgyo_game._show_song_select()
            Called on every carousel navigation event to wire the song
            thumbnail, preview audio, and song info in a single lookup
            rather than three separate calls.

        bgyo_game._draw_hud()
            Called once per session start to cache the cover image path
            for the gameplay HUD header display.
    """
    return {
        "name"    : song_name,
        "mp3"     : find_mp3(song_name),
        "preview" : find_preview(song_name),
        "cover"   : find_cover(song_name),
    }


# ══════════════════════════════════════════════════════════════════
#  BATCH HELPERS
# ══════════════════════════════════════════════════════════════════

def get_all_playable_songs(song_list: list) -> list:
    """
    Filter a list of song title strings down to only those that have a
    matching MP3 file on disk, preserving the original order.

    Returns a list of (song_name, mp3_path) tuples so callers receive
    both the display title and the resolved path together.

    A song is "playable" if and only if find_mp3() returns a non-None
    path — the file must exist in SONGS_DIR.

    Used by:
        bgyo_game._show_song_select()
            Called when the song-select screen opens to build the
            carousel from only songs the player can actually play.
            Songs without an MP3 file are silently excluded.

    Args:
        song_list — List of canonical song title strings, typically
                    cfg.songs (which is a copy of database.DEFAULT_SONGS).

    Returns:
        List of (name: str, path: str) tuples in original order.
        Empty list if SONGS_DIR doesn't exist or no files match.
    """
    result = []
    for name in song_list:
        mp3 = find_mp3(name)
        if mp3:
            result.append((name, mp3))
    return result


def list_all_covers() -> dict:
    """
    Return a dict mapping lowercase song-name stems to their absolute
    cover art paths for every image file found in COVERS_DIR.

    Used by bgyo_game._show_song_select() to pre-cache cover thumbnails
    for the entire song list in a single pass when the screen opens,
    rather than calling find_cover() separately for every carousel item.

    Returns:
        { lowercase_stem: abs_path }  e.g. { "the light": "/…/the_light.png" }

    The key is the prefix-stripped filename stem (lowercased), which
    roughly corresponds to the lowercase song title.  Callers that need
    to look up by exact title should use find_cover() instead.

    Accepts PNG, JPG, JPEG, WEBP image formats.
    Returns {} if COVERS_DIR doesn't exist.
    """
    if not os.path.isdir(COVERS_DIR):
        return {}

    exts   = (".png", ".jpg", ".jpeg", ".webp")
    result = {}
    for f in os.listdir(COVERS_DIR):
        if any(f.lower().endswith(e) for e in exts):
            # Use the prefix-stripped stem as the key for a cleaner lookup
            stem = _strip_prefix(os.path.splitext(f)[0].lower())
            result[stem] = os.path.join(COVERS_DIR, f)
    return result


# ══════════════════════════════════════════════════════════════════
#  DIRECTORY BOOTSTRAP
# ══════════════════════════════════════════════════════════════════

def ensure_dirs():
    """
    Create the songs/, songs/preview/, and images/covers/ directories
    if they do not already exist.

    Called by BGYOGame.__init__() before any asset lookups so that
    os.listdir() calls in this module never raise FileNotFoundError
    on a fresh installation where the player hasn't added any files yet.

    os.makedirs(exist_ok=True) is a no-op if the directory already
    exists, so this is safe to call on every startup.
    """
    for d in (SONGS_DIR, PREVIEW_DIR, COVERS_DIR):
        os.makedirs(d, exist_ok=True)
