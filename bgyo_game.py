import tkinter as tk
import math, random, time, os, threading

# ── Optional libraries ────────────────────────────────────────────
try:
    from PIL import Image, ImageTk, ImageDraw
    PIL_OK = True
    print("✓ PIL (Pillow) ready")
except ImportError:
    PIL_OK = False
    print("✗ Pillow not installed — images disabled. pip install pillow")

# ── Core project modules ─────────────────────────────────────────
import constants as C
from constants import (
    W, H, BASE_W, BASE_H, FPS,
    MEMBER_NAMES, MEMBER_ROLES, MEMBER_COLORS, BG_COL,
    AVATAR_COLORS, LANE_CONFIGS, DIFFICULTY, TRIVIA,
    BTN_COLORS, btn_fg,
    UI_FONT, MONO_FONT, TITLE_FONT,
    dim, blend, lighten, hex_to_rgb, rgb_to_hex,
    additive_blend, spotlight_col,
    project, VPX, VPY, SW, SL, NEARY, HIT_DEPTH,
    lane_cx,
)
import database as db
from database import AccountError
import settings_state as ss
from settings_state import cfg, session
from audio_engine import audio
from file_helpers import (
    find_mp3, find_preview, find_cover,
    get_all_playable_songs, get_song_info, ensure_dirs,
)
from game_objects import Note, Particle, Spark, Flash, SideEffect, Spotlight

# ── v18.2 refactored modules ─────────────────────────────────────
# game_logic   — pure gameplay mechanics (no tkinter/pygame)
# game_renderer — all Canvas draw calls for the gameplay screen
# ui_helpers   — reusable UI primitives
from game_logic import (
    make_game_state,    # factory for the gs session dict
    update_game,        # per-frame note/physics/scoring update
    hit_lane,           # key-press hit detection and scoring
    calc_accuracy,      # (perfect+good)/total × 100
    calc_grade,         # letter grade + colour from accuracy
    grade_message,      # flavour text for each grade
)
from game_renderer import (
    draw_track,           # 3D perspective grid + hit bar
    draw_game_banner,     # optional stage photo behind notes
    draw_notes,           # note circles with glow/glint/sparkles
    draw_lane_targets,    # hit-zone markers at the hit bar
    draw_particles,       # hit-burst particle dots
    draw_sparks,          # PERFECT spark flares
    draw_flashes,         # floating feedback text
    draw_side_effects,    # screen-edge PERFECT/COMBO/MISS labels
    draw_combo_panel,     # animated cheer + orbiting stars
    draw_combo_burst,     # "★ Nx COMBO!" below track
    draw_countdown,       # 3…2…1…GO! pre-game display
    draw_pause_overlay,   # SPACE-key pause panel
    draw_loading_bar,     # beat-analysis progress indicator
    draw_hud,             # score / combo / song title / counters
)
from ui_helpers import (
    make_pixel_btn,           # Canvas-drawn animated pixel button
    make_btn,                 # standard tk.Button wrapper
    make_themed_btn_row,      # palette-cycling button row
    make_section_label,       # horizontal rule + label divider
    draw_fancy_title,         # neon "BGYO" + subtitle canvas widget
    update_neon_border,       # animated cycling glow border
    draw_star5,               # 5-pointed star polygon
    draw_ellipse_glow,        # concentric soft radial glow
    draw_transition_overlay,  # full-screen white fade overlay
    play_click,               # synthesised click sound
)

# ── Projection globals (updated on fullscreen toggle) ─────────────
# These shadow constants.py values so _apply_fullscreen() can replace
# the projection closure without touching constants.py directly.
_project   = project
_HIT_DEPTH = HIT_DEPTH
_W, _H     = W, H


def _clamp(v):
    return max(0, min(255, int(v)))


# ═══════════════════════════════════════════════════════════════
#  MAIN APPLICATION
# ═══════════════════════════════════════════════════════════════
class BGYOGame:
    # ── Construction ─────────────────────────────────────────────────
    def __init__(self):
        global _project, _HIT_DEPTH, _W, _H

        ensure_dirs()

        self.root = tk.Tk()
        self.root.title("The Light Stage: Aces of P-Pop  v18.5")
        self.root.resizable(False, False)
        self.root.configure(bg=BG_COL)

        self.cv = tk.Canvas(self.root, width=W, height=H,
                            bg=BG_COL, highlightthickness=0)
        self.cv.pack()

        # ── Time / animation ──────────────────────────────────────────
        self.t      = 0.0
        self.last_t = time.time()
        self.screen = "login"
        self._title_canvas = None  # For animated title in login/register screens

        # ── Scene objects ─────────────────────────────────────────────
        self.stars = [
            {"nx": random.random(), "ny": random.random() * 0.42,
             "r":  random.uniform(0.4, 2.6), "ph": random.uniform(0, 6.28)}
            for _ in range(200)
        ]
        self.spotlights   = [Spotlight(i) for i in range(8)]
        self.particles    = []
        self.sparks       = []
        self.flashes      = []
        self.side_effects = []
        self.ticker_x     = float(W)

        # ── Transition ────────────────────────────────────────────────
        self._tr_alpha = 0.0
        self._tr_dir   = 0
        self._tr_speed = 2.8
        self._tr_cb    = None

        # ── Input ─────────────────────────────────────────────────────
        self.keys_held = set()
        self.lane_lit  = []

        # ── Game state ────────────────────────────────────────────────
        self.gs           = {}
        self._beat_cache  = {}
        self._worker_thread = None
        self._game_volume   = cfg.music_volume

        # ── Trivia state ──────────────────────────────────────────────
        self._tv_questions    = []
        self._tv_idx          = 0
        self._tv_score        = 0
        self._tv_answered     = False
        self._tv_pending      = None   # index chosen but not yet confirmed
        self._tv_next_time    = None
        self._tv_timer_start  = 0.0
        self._tv_time_limit   = 15.0
        self._tv_waiting      = False  # ENHANCED v18.1: transition state
        self._tv_time_taken   = 0.0   # cumulative seconds used across all answers

        # ── Carousel / song select ────────────────────────────────────
        self._carousel_idx    = 0
        self._carousel_songs  = []
        self._carousel_anim   = 0.0
        self._carousel_target = 0
        self._preview_timer   = None
        self._preview_song    = None

        # ── In-game overlay ───────────────────────────────────────────
        self._overlay_open  = False
        self._overlay_frame = None

        # ── Canvas button hit-testing ─────────────────────────────────
        self._nav_pressed    = None
        self._badge_frame    = None   # home screen profile badge (animated glow border)
        self._tc_btn_rects   = {}     # trivia confirm screen canvas button hit-rects
        self._tc_btn_press   = None   # currently depressed trivia confirm button key

        # ── Image cache ───────────────────────────────────────────────
        self.img_refs = {}
        self._load_images()

        # ── Event bindings ────────────────────────────────────────────
        self.root.bind("<KeyPress>",   self._on_key_down)
        self.root.bind("<KeyRelease>", self._on_key_up)
        self.cv.bind("<ButtonPress-1>",   self._on_canvas_press)
        self.cv.bind("<ButtonRelease-1>", self._on_canvas_release)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        # ── Alive flag — set False when window is closing ────────────
        self._alive = True

        # ── Start ─────────────────────────────────────────────────────
        self._apply_fullscreen()
        self._show_login()
        self._loop()
        self.root.mainloop()

    # ── Fullscreen ───────────────────────────────────────────────────
    def _apply_fullscreen(self):
        global _W, _H, _project, _HIT_DEPTH
        if cfg.fullscreen:
            self.root.attributes("-fullscreen", True)
            self.root.update_idletasks()
            _W = self.root.winfo_screenwidth()
            _H = self.root.winfo_screenheight()
        else:
            self.root.attributes("-fullscreen", False)
            _W, _H = BASE_W, BASE_H
        self.cv.config(width=_W, height=_H)
        self.root.geometry(f"{_W}x{_H}")
        from constants import _make_proj
        _project, *_, _HIT_DEPTH = _make_proj(_W, _H)
        self.stars = [
            {"nx": random.random(), "ny": random.random() * 0.42,
             "r":  random.uniform(0.4, 2.6), "ph": random.uniform(0, 6.28)}
            for _ in range(200)
        ]
        # Reload images so member photo and logo scale to new resolution
        self.img_refs.pop("members", None)
        self.img_refs.pop("members_size", None)
        self.img_refs.pop("logo", None)
        self._load_images()

    def _W(self):  return _W
    def _H(self):  return _H
    def sx(self, x): return x * _W / BASE_W
    def sy(self, y): return y * _H / BASE_H

    # ── Image loading ─────────────────────────────────────────────────
    def _load_images(self):
        if not PIL_OK:
            return
        img_dir = C.IMG_DIR
        os.makedirs(img_dir, exist_ok=True)
        try:
            p = os.path.join(img_dir, "bgyo_logo.png")
            if os.path.exists(p):
                im = Image.open(p).convert("RGBA")
                im.thumbnail((int(min(_W, _H) * 0.20),) * 2, Image.Resampling.LANCZOS)
                self.img_refs["logo"] = ImageTk.PhotoImage(im)

            p = os.path.join(img_dir, "ranking_icon.png")
            if os.path.exists(p):
                im = Image.open(p).convert("RGBA")
                im.thumbnail((48, 48), Image.Resampling.LANCZOS)
                self.img_refs["ranking_icon"] = ImageTk.PhotoImage(im)

            p = os.path.join(img_dir, "bgyo_members_grouped.png")
            if os.path.exists(p):
                im  = Image.open(p).convert("RGBA")
                # Moderate sizing: 50% wide × 40% tall — appropriate scale
                tw  = int(_W * 0.50); th = int(_H * 0.40)
                ow, oh = im.size
                sc  = min(tw / ow, th / oh)
                im  = im.resize((int(ow * sc), int(oh * sc)), Image.Resampling.LANCZOS)
                # No fade effect — clean, solid photo display
                iw, ih = im.size
                self.img_refs["members"] = ImageTk.PhotoImage(im)
                # Store dimensions for layout math
                self.img_refs["members_size"] = (iw, ih)

            for fname in ("bgyo_during_game.png", "bgyo_during_game.jpg"):
                p = os.path.join(img_dir, fname)
                if os.path.exists(p):
                    im = Image.open(p).convert("RGBA")
                    # Fit within 86% width × 14% height — leaves room below the HUD
                    # title box (which occupies ~88 px from top) so the banner
                    # is drawn beneath it instead of hidden behind it.
                    im.thumbnail((int(_W * 0.86), int(_H * 0.14)), Image.Resampling.LANCZOS)
                    self.img_refs["game_banner"]      = ImageTk.PhotoImage(im)
                    self.img_refs["game_banner_size"] = im.size   # store (w, h) for positioning
                    break
        except Exception as e:
            print(f"✗ Image load error: {e}")

    def _load_cover_image(self, song_name: str, size: int = 220):
        """Load & cache a cover image (or generate placeholder). Returns PhotoImage or None."""
        key = f"cover_{song_name}_{size}"
        if key in self.img_refs:
            return self.img_refs[key]
        cover_path = find_cover(song_name)
        if cover_path and PIL_OK:
            try:
                im = Image.open(cover_path).convert("RGBA")
                im = im.resize((size, size), Image.Resampling.LANCZOS)
                self.img_refs[key] = ImageTk.PhotoImage(im)
                return self.img_refs[key]
            except Exception:
                pass
        ph = self._make_placeholder_cover(song_name, size)
        self.img_refs[key] = ph
        return ph

    def _make_placeholder_cover(self, song_name: str, size: int = 220):
        if not PIL_OK:
            return None
        colors  = ["#FFD700", "#FF3385", "#00E5FF", "#00FF99", "#FF8800"]
        mc      = colors[abs(hash(song_name)) % len(colors)]
        r, g, b = hex_to_rgb(mc)
        im = Image.new("RGBA", (size, size), (4, 0, 12, 255))
        d  = ImageDraw.Draw(im)
        for i in range(size):
            t2 = i / size
            d.line([(0, i), (size, i)],
                   fill=(int(4 + r * 0.25 * t2), int(g * 0.25 * t2), int(12 + b * 0.25 * t2), 255))
        for ri_f, af in [(0.75, 40), (0.55, 60), (0.38, 90), (0.22, 130)]:
            ri = int(size * ri_f / 2)
            cx, cy = size // 2, size // 2
            d.ellipse([cx - ri, cy - ri, cx + ri, cy + ri], outline=(r, g, b, af), width=2)
        ns = size // 4; cx2, cy2 = size // 2, size // 2
        d.ellipse([cx2 - ns // 2, cy2 - ns // 4, cx2 + ns // 2, cy2 + ns // 4],
                  fill=(r, g, b, 200))
        d.rectangle([cx2 + ns // 2 - 4, cy2 - ns, cx2 + ns // 2, cy2], fill=(r, g, b, 200))
        short = song_name[:12] + ("…" if len(song_name) > 12 else "")
        ty    = size - 32
        d.rectangle([0, ty - 2, size, size], fill=(4, 0, 12, 220))
        d.text((size // 2, ty + 8), short, fill=(r, g, b, 255), anchor="mm")
        return ImageTk.PhotoImage(im)

    # ── Lifecycle ─────────────────────────────────────────────────────
    def _on_close(self):
        self._alive = False          # signal the loop to stop scheduling
        try:
            audio.stop()
            audio.stop_preview()
        except Exception:
            pass
        try:
            self.root.destroy()
        except Exception:
            pass

    def _clear_widgets(self):
        for w in self.root.winfo_children():
            if w is not self.cv:
                w.destroy()

    # ── UI helpers ────────────────────────────────────────────────────
    def _btn(self, parent, text: str, bg: str, fg: str, cmd, width=None):
        """Standard tk.Button — delegates to ui_helpers.make_btn()."""
        return make_btn(parent, text, bg, fg, cmd, self.root, width=width)
    def _pixel_btn(self, parent, label, col, cmd, width=160, height=None):
        """
        Canvas-drawn pixel-style button — delegates to ui_helpers.make_pixel_btn().
        Injects self.root for after() scheduling and play_click() for audio feedback.
        """
        return make_pixel_btn(parent, label, col, cmd,
                              self.root, width=width, height=height)

    def _themed_btn_row(self, parent, items, start_idx=0):
        """Palette-cycling button row — delegates to ui_helpers.make_themed_btn_row()."""
        return make_themed_btn_row(parent, items, self.root, start_idx)

    def _section_label(self, parent, txt: str):
        """Section-divider label with horizontal rule — delegates to ui_helpers."""
        make_section_label(parent, txt)
    def _play_click(self):
        """Synthesised 880 Hz click sound — delegates to ui_helpers.play_click()."""
        play_click(self.root)

    # ── Fade transition ───────────────────────────────────────────────
    def _fade_to(self, callback, speed=2.8):
        """
        Begin a fade-to-white transition.  callback fires at peak opacity.
        Ignored if a transition is already running (_tr_dir != 0).
        """
        if self._tr_dir != 0:
            return
        self._tr_alpha = 0.0; self._tr_dir = 1
        self._tr_speed = speed; self._tr_cb = callback

    def _draw_transition(self, dt):
        """
        Advance fade state machine and draw the white overlay via
        ui_helpers.draw_transition_overlay().
        +1 = fading in, callback fires at 1.0, then -1 = fading out → 0 = idle.
        """
        if self._tr_dir == 0:
            return
        self._tr_alpha += self._tr_dir * self._tr_speed * dt
        if self._tr_dir == 1 and self._tr_alpha >= 1.0:
            self._tr_alpha = 1.0; self._tr_dir = -1
            cb = self._tr_cb; self._tr_cb = None
            if cb: cb()
        elif self._tr_dir == -1 and self._tr_alpha <= 0.0:
            self._tr_alpha = 0.0; self._tr_dir = 0
        if not self._alive:
            return
        draw_transition_overlay(self.cv,
                                max(0.0, min(1.0, self._tr_alpha)),
                                _W, _H)

    # ═══════════════════════════════════════════════════════════════
    #  AUTH SCREENS — Login / Register
    # ═══════════════════════════════════════════════════════════════
    def _draw_fancy_title(self, parent, bg=BG_COL):
        """
        Draw the neon 'BGYO' title and subtitle inside parent.
        Delegates to ui_helpers.draw_fancy_title().
        """
        draw_fancy_title(parent, bg)

    def _show_login(self):
        self._clear_widgets(); self.screen = "login"
        cfg.fullscreen = False
        self._apply_fullscreen()
        # Transparent outer frame shows starry background
        outer = tk.Frame(self.root, bg=BG_COL)
        outer.place(relx=0.5, rely=0.5, anchor="center")

        # ── SINGLE UNIFIED CONTAINER: Title + Login Form ────────────
        container = tk.Frame(outer, bg="#0a0018")
        container.pack(ipadx=min(30, int(_W*0.025)), ipady=16)
        
        # Store reference for border animation
        self._login_container = container
        
        # Draw title inside the container
        self._draw_fancy_title(container)

        # Draw login form inside the same container
        tk.Label(container, text="▶  PLAYER LOGIN", bg="#0a0018", fg="#FFD700",
                 font=(UI_FONT, 16, "bold")).pack(pady=(0, 16))

        # ── Centered input fields with uniform sizing ────
        INPUT_WIDTH = 22
        
        # Username field (centered)
        uf = tk.Frame(container, bg="#0a0018"); uf.pack(pady=8)
        tk.Label(uf, text="USERNAME", bg="#0a0018", fg="#888888",
                 font=(UI_FONT, 9, "bold")).pack(pady=(0, 4))
        user_var = tk.StringVar()
        user_e   = tk.Entry(uf, textvariable=user_var, bg="#0c0022", fg="#FFFFFF",
                            insertbackground="#FFD700", font=(UI_FONT, 13, "bold"),
                            relief="flat", width=INPUT_WIDTH,
                            highlightthickness=2,
                            highlightbackground="#334466",
                            highlightcolor="#FFD700")
        user_e.pack()
        user_e.focus_set()

        # Password field (centered, same size as username)
        pf = tk.Frame(container, bg="#0a0018"); pf.pack(pady=8)
        tk.Label(pf, text="PASSWORD", bg="#0a0018", fg="#888888",
                 font=(UI_FONT, 9, "bold")).pack(pady=(0, 4))
        pass_var = tk.StringVar()
        pass_e   = tk.Entry(pf, textvariable=pass_var, show="●", bg="#0c0022", fg="#FFFFFF",
                            insertbackground="#FFD700", font=(UI_FONT, 13, "bold"),
                            relief="flat", width=INPUT_WIDTH,
                            highlightthickness=2,
                            highlightbackground="#334466",
                            highlightcolor="#FFD700")
        pass_e.pack()

        # Message label
        msg_var = tk.StringVar()
        msg_lbl = tk.Label(container, textvariable=msg_var, bg="#0a0018", fg="#FF3385",
                           font=(UI_FONT, 10, "bold"), wraplength=380)
        msg_lbl.pack(pady=(10, 4))

        def do_login():
            acct = db.login(user_var.get(), pass_var.get())
            if acct:
                session.login(acct)
                cfg.load_from_db(session.account_id)
                cfg.fullscreen = True
                self._fade_to(self._show_title)
            else:
                uname = user_var.get().strip()
                if not uname:
                    msg_var.set("✗  Enter your username.")
                elif not db.username_exists(uname):
                    msg_var.set("✗  Username not found. Register to create an account.")
                else:
                    msg_var.set("✗  Wrong password. Try again.")
                msg_lbl.config(fg="#FF3385")

        # ── Centered buttons with uniform sizing ────
        BTN_W = 155; BTN_H_PX = C.BTN_H; GAP = 16
        HL = 4; SH = 4

        def _make_pixel_btn(parent, label, col, cmd, width=None):
            bw = width if width else BTN_W
            cv_btn = tk.Canvas(parent, width=bw, height=BTN_H_PX,
                               bg="#0a0018", highlightthickness=0, cursor="hand2")
            pressed = [False]

            def _draw(p=False):
                cv_btn.delete("all")
                ox, oy = (2, 2) if p else (0, 0)
                bright = lighten(col, 0.55)
                dark   = dim(col, 0.40)
                x1, y1, x2, y2 = 0, 0, bw, BTN_H_PX
                cv_btn.create_rectangle(x1+ox, y1+oy, x2+ox, y2+oy, fill=col, outline="")
                cv_btn.create_rectangle(x1+ox, y1+oy, x2+ox, y1+oy+HL, fill=bright, outline="")
                cv_btn.create_rectangle(x1+ox, y2+oy-SH, x2+ox, y2+oy, fill=dark, outline="")
                cv_btn.create_text(bw//2+ox, BTN_H_PX//2+oy, text=label,
                                   fill="#04000C", font=(UI_FONT, 13, "bold"), anchor="center")

            def _press(e):
                pressed[0] = True; _draw(True)
            def _release(e):
                pressed[0] = False; _draw(False)
                self._play_click()
                self.root.after(40, cmd)
            def _leave(e):
                if pressed[0]: pressed[0] = False; _draw(False)

            cv_btn.bind("<ButtonPress-1>",   _press)
            cv_btn.bind("<ButtonRelease-1>", _release)
            cv_btn.bind("<Leave>",           _leave)
            _draw()
            return cv_btn

        # Button row (centered) — LOGIN and REGISTER side-by-side
        bf = tk.Frame(container, bg="#0a0018"); bf.pack(pady=(8, 6))
        _make_pixel_btn(bf, "▶  LOGIN",    BTN_COLORS[0], do_login, width=BTN_W).pack(side="left", padx=(4, GAP//2))
        _make_pixel_btn(bf, "★  REGISTER", BTN_COLORS[2],
                        lambda: self._fade_to(self._show_register), width=BTN_W).pack(side="left", padx=(GAP//2, 4))

        # Guest play button — full width below
        guest_w = BTN_W * 2 + GAP
        gf = tk.Frame(container, bg="#0a0018"); gf.pack(pady=(0, 4))
        _make_pixel_btn(gf, "◉  PLAY AS GUEST", BTN_COLORS[4],
                        self._confirm_guest_play,
                        width=guest_w).pack()

        # Exit game button — matches homescreen EXIT button style
        ef = tk.Frame(container, bg="#0a0018"); ef.pack(pady=(2, 12))
        _make_pixel_btn(ef, "✕  EXIT GAME", BTN_COLORS[5],
                        self._on_close, width=guest_w).pack()

        user_e.bind("<Return>", lambda e: pass_e.focus_set())
        pass_e.bind("<Return>", lambda e: do_login())

    def _show_register(self):
        self._clear_widgets(); self.screen = "register"
        cfg.fullscreen = False
        self._apply_fullscreen()
        # Transparent outer frame shows starry background
        outer = tk.Frame(self.root, bg=BG_COL)
        outer.place(relx=0.5, rely=0.5, anchor="center")

        # ── SINGLE UNIFIED CONTAINER: Title + Register Form ────────
        # This will be wrapped with animated glowing border
        container = tk.Frame(outer, bg="#0a0018")
        container.pack(ipadx=30, ipady=18)
        
        # Store reference for border animation
        self._login_container = container
        
        # Draw title inside the container
        self._draw_fancy_title(container)

        # Draw register form inside the same container
        tk.Label(container, text="★  CREATE ACCOUNT", bg="#0a0018", fg="#00E5FF",
                 font=(UI_FONT, 16, "bold")).pack(pady=(0, 16))

        # ── Centered input fields with uniform sizing ────
        INPUT_WIDTH = 22
        fields    = [("USERNAME", "uv", ""), ("PASSWORD", "pv", "●"), ("CONFIRM", "cv", "●")]
        entry_map = {}
        for lbl_text, var_name, show_ch in fields:
            rf = tk.Frame(container, bg="#0a0018"); rf.pack(pady=8)
            tk.Label(rf, text=lbl_text, bg="#0a0018", fg="#888888",
                     font=(UI_FONT, 9, "bold")).pack(pady=(0, 4))
            v = tk.StringVar(); setattr(container, var_name, v)
            e = tk.Entry(rf, textvariable=v, show=show_ch,
                         bg="#0c0022", fg="#FFFFFF",
                         insertbackground="#00E5FF", font=(UI_FONT, 13, "bold"),
                         relief="flat", width=INPUT_WIDTH,
                         highlightthickness=2,
                         highlightbackground="#334466",
                         highlightcolor="#00E5FF")
            e.pack()
            entry_map[var_name] = e
            if lbl_text == "USERNAME":
                e.focus_set()

        tk.Label(container,
                 text="Username: 3-24 chars  •  Password: 6+ chars",
                 bg="#0a0018", fg="#445566", font=(UI_FONT, 8)).pack(pady=(6, 0))

        msg_var = tk.StringVar()
        msg_lbl = tk.Label(container, textvariable=msg_var, bg="#0a0018",
                           font=(UI_FONT, 10, "bold"), wraplength=380)
        msg_lbl.pack(pady=(10, 4))

        def do_register():
            uname = container.uv.get().strip()
            pw    = container.pv.get()
            pw2   = container.cv.get()
            if not uname:
                msg_var.set("✗  Enter a username."); msg_lbl.config(fg="#FF3385"); return
            if not pw:
                msg_var.set("✗  Enter a password."); msg_lbl.config(fg="#FF3385"); return
            if pw != pw2:
                msg_var.set("✗  Passwords don't match."); msg_lbl.config(fg="#FF3385"); return
            try:
                acct = db.create_account(uname, pw)
                session.login(acct)
                cfg.load_from_db(session.account_id)
                cfg.fullscreen = True
                msg_var.set("✓  Account created! Logging in…"); msg_lbl.config(fg="#00FF99")
                self.root.after(900, lambda: self._fade_to(self._show_title))
            except AccountError as ae:
                msg_var.set(f"✗  {ae.message}"); msg_lbl.config(fg="#FF3385")

        # ── Centered buttons with uniform sizing ────
        BTN_W = 155
        
        # Helper function to create pixel buttons (same as login)
        def _make_pixel_btn(parent, label, col, cmd, width=None):
            bw = width if width else BTN_W
            cv_btn = tk.Canvas(parent, width=bw, height=C.BTN_H,
                               bg="#0a0018", highlightthickness=0, cursor="hand2")
            pressed = [False]

            def _draw(p=False):
                cv_btn.delete("all")
                ox, oy = (2, 2) if p else (0, 0)
                bright = lighten(col, 0.55)
                dark   = dim(col, 0.40)
                x1, y1, x2, y2 = 0, 0, bw, C.BTN_H
                cv_btn.create_rectangle(x1+ox, y1+oy, x2+ox, y2+oy, fill=col, outline="")
                cv_btn.create_rectangle(x1+ox, y1+oy, x2+ox, y1+oy+4, fill=bright, outline="")
                cv_btn.create_rectangle(x1+ox, y2+oy-4, x2+ox, y2+oy, fill=dark, outline="")
                cv_btn.create_text(bw//2+ox, C.BTN_H//2+oy, text=label,
                                   fill="#04000C", font=(UI_FONT, 13, "bold"), anchor="center")

            def _press(e):
                pressed[0] = True; _draw(True)
            def _release(e):
                pressed[0] = False; _draw(False)
                self._play_click()
                self.root.after(40, cmd)
            def _leave(e):
                if pressed[0]: pressed[0] = False; _draw(False)

            cv_btn.bind("<ButtonPress-1>",   _press)
            cv_btn.bind("<ButtonRelease-1>", _release)
            cv_btn.bind("<Leave>",           _leave)
            _draw()
            return cv_btn

        # Button row (centered) — CREATE and BACK side-by-side with equal sizes
        bf = tk.Frame(container, bg="#0a0018"); bf.pack(pady=(8, 4))
        _make_pixel_btn(bf, "★  CREATE", BTN_COLORS[2], do_register, width=BTN_W).pack(side="left", padx=4)
        _make_pixel_btn(bf, "◄  BACK",   BTN_COLORS[1],
                        lambda: self._fade_to(self._show_login), width=BTN_W).pack(side="left", padx=4)

        # Exit game button — matches homescreen EXIT button style
        ef = tk.Frame(container, bg="#0a0018"); ef.pack(pady=(2, 12))
        _make_pixel_btn(ef, "✕  EXIT GAME", BTN_COLORS[5],
                        self._on_close, width=BTN_W * 2 + 8).pack()

        for e in entry_map.values():
            e.bind("<Return>", lambda ev: do_register())

    def _update_login_container_border(self):
        """Animate neon glow border on login/register form — delegates to ui_helpers."""
        if not hasattr(self, '_login_container') or not self._login_container:
            return
        try:
            update_neon_border(self._login_container, self.t)
        except Exception:
            pass

    def _confirm_guest_play(self):
        """Show a modal warning that guest scores are not saved, then proceed."""
        CARD = "#0a0018"
        modal = tk.Toplevel(self.root)
        modal.title("")
        modal.configure(bg=CARD)
        modal.resizable(False, False)
        modal.grab_set()   # block interaction with main window
        modal.transient(self.root)

        # Centre over root
        modal.update_idletasks()
        mw, mh = 460, 260
        rx = self.root.winfo_rootx() + (_W - mw) // 2
        ry = self.root.winfo_rooty() + (_H - mh) // 2
        modal.geometry(f"{mw}x{mh}+{rx}+{ry}")
        modal.attributes("-topmost", True)

        # Border frame
        border = tk.Frame(modal, bg="#FF8800", padx=2, pady=2)
        border.pack(fill="both", expand=True)
        inner = tk.Frame(border, bg=CARD)
        inner.pack(fill="both", expand=True)

        tk.Label(inner, text="◉  PLAY AS GUEST", bg=CARD, fg="#FF8800",
                 font=(UI_FONT, 16, "bold")).pack(pady=(20, 6))

        tk.Label(inner,
                 text="Your game scores will NOT be saved\nto the leaderboard as a Guest.\n\nRegister a free account to keep your scores!",
                 bg=CARD, fg="#CCCCCC", font=(UI_FONT, 11), justify="center").pack(pady=(0, 18))

        btn_f = tk.Frame(inner, bg=CARD); btn_f.pack(pady=(0, 18))

        def _proceed():
            modal.destroy()
            session.logout()
            cfg.fullscreen = True
            self._fade_to(self._show_title)

        def _go_register():
            modal.destroy()
            self._fade_to(self._show_register)

        self._pixel_btn(btn_f, "▶  CONTINUE AS GUEST", BTN_COLORS[4],
                        _proceed, width=200).pack(side="left", padx=8)
        self._pixel_btn(btn_f, "★  REGISTER", BTN_COLORS[2],
                        _go_register, width=120).pack(side="left", padx=8)

    def _logout(self):
        audio.stop_bgm()
        session.logout()
        self._fade_to(self._show_login)

    # ═══════════════════════════════════════════════════════════════
    #  TITLE / HOME SCREEN
    # ═══════════════════════════════════════════════════════════════
    def _show_title(self):
        self._clear_widgets(); self.screen = "title"
        self.particles.clear(); self.flashes.clear()
        self.side_effects.clear(); self.sparks.clear()
        audio.stop_preview()
        audio.play_menu_bgm()   # guaranteed-different track each visit
        self._nav_pressed = None
        self._badge_frame = None   # reset badge reference for new screen
        self._apply_fullscreen()  # Apply fullscreen setting

        # ── Logged-in user badge (top-left) ───────────────────────────
        if not session.is_guest:
            badge = tk.Frame(self.root, bg="#0a0018",
                             highlightbackground="#FFD700", highlightthickness=2)
            badge.place(x=12, y=12)
            # Store reference so the loop can animate the border
            self._badge_frame = badge
            tk.Label(badge, text=f"★  {session.username.upper()}", bg="#0a0018", fg="#FFD700",
                     font=(UI_FONT, 10, "bold")).pack(side="left", padx=(10, 4), pady=6)
        # Colorful pixel-style LOGOUT button
            _lo_col = BTN_COLORS[1]  # pink
            _lo_w, _lo_h = 90, 30
            lo_cv = tk.Canvas(badge, width=_lo_w, height=_lo_h,
                              bg="#0a0018", highlightthickness=0, cursor="hand2")
            lo_cv.pack(side="left", padx=(0, 4), pady=6)
            def _draw_lo(pressed=False):
                lo_cv.delete("all")
                ox, oy = (1, 1) if pressed else (0, 0)
                lo_cv.create_rectangle(ox, oy, _lo_w+ox, _lo_h+oy,
                                       fill=_lo_col, outline="")
                lo_cv.create_rectangle(ox, oy, _lo_w+ox, oy+3,
                                       fill=lighten(_lo_col, 0.55), outline="")
                lo_cv.create_rectangle(ox, _lo_h+oy-3, _lo_w+ox, _lo_h+oy,
                                       fill=dim(_lo_col, 0.40), outline="")
                lo_cv.create_text(_lo_w//2+ox, _lo_h//2+oy, text="LOGOUT",
                                  fill="#000000", font=(UI_FONT, 9, "bold"),
                                  anchor="center")
            _lo_pressed = [False]
            def _lo_press(e):  _lo_pressed[0]=True;  _draw_lo(True)
            def _lo_release(e):
                _lo_pressed[0]=False; _draw_lo(False)
                self._play_click(); self.root.after(40, self._logout)
            def _lo_leave(e):
                if _lo_pressed[0]: _lo_pressed[0]=False; _draw_lo(False)
            lo_cv.bind("<ButtonPress-1>",   _lo_press)
            lo_cv.bind("<ButtonRelease-1>", _lo_release)
            lo_cv.bind("<Leave>",           _lo_leave)
            _draw_lo()

            # MY PROFILE button
            _pr_col = BTN_COLORS[2]  # cyan
            _pr_w, _pr_h = 110, 30
            pr_cv = tk.Canvas(badge, width=_pr_w, height=_pr_h,
                              bg="#0a0018", highlightthickness=0, cursor="hand2")
            pr_cv.pack(side="left", padx=(0, 6), pady=6)
            def _draw_pr(pressed=False):
                pr_cv.delete("all")
                ox, oy = (1, 1) if pressed else (0, 0)
                pr_cv.create_rectangle(ox, oy, _pr_w+ox, _pr_h+oy,
                                       fill=_pr_col, outline="")
                pr_cv.create_rectangle(ox, oy, _pr_w+ox, oy+3,
                                       fill=lighten(_pr_col, 0.55), outline="")
                pr_cv.create_rectangle(ox, _pr_h+oy-3, _pr_w+ox, _pr_h+oy,
                                       fill=dim(_pr_col, 0.40), outline="")
                pr_cv.create_text(_pr_w//2+ox, _pr_h//2+oy, text="MY PROFILE",
                                  fill="#000000", font=(UI_FONT, 9, "bold"),
                                  anchor="center")
            _pr_pressed = [False]
            def _pr_press(e):  _pr_pressed[0]=True;  _draw_pr(True)
            def _pr_release(e):
                _pr_pressed[0]=False; _draw_pr(False)
                self._play_click(); self.root.after(40, self._show_edit_profile)
            def _pr_leave(e):
                if _pr_pressed[0]: _pr_pressed[0]=False; _draw_pr(False)
            pr_cv.bind("<ButtonPress-1>",   _pr_press)
            pr_cv.bind("<ButtonRelease-1>", _pr_release)
            pr_cv.bind("<Leave>",           _pr_leave)
            _draw_pr()

        else:
            # ── Guest badge (top-left) — shows Login / Register button ──
            guest_badge = tk.Frame(self.root, bg="#0a0018",
                                   highlightbackground="#FF8800", highlightthickness=2)
            guest_badge.place(x=12, y=12)
            tk.Label(guest_badge, text="👤  GUEST", bg="#0a0018", fg="#FF8800",
                     font=(UI_FONT, 10, "bold")).pack(side="left", padx=(10, 4), pady=6)

            _gi_col = BTN_COLORS[0]   # gold
            _gi_w, _gi_h = 160, 30
            gi_cv = tk.Canvas(guest_badge, width=_gi_w, height=_gi_h,
                              bg="#0a0018", highlightthickness=0, cursor="hand2")
            gi_cv.pack(side="left", padx=(0, 6), pady=6)

            def _draw_gi(pressed=False):
                gi_cv.delete("all")
                ox, oy = (1, 1) if pressed else (0, 0)
                gi_cv.create_rectangle(ox, oy, _gi_w + ox, _gi_h + oy,
                                       fill=_gi_col, outline="")
                gi_cv.create_rectangle(ox, oy, _gi_w + ox, oy + 3,
                                       fill=lighten(_gi_col, 0.55), outline="")
                gi_cv.create_rectangle(ox, _gi_h + oy - 3, _gi_w + ox, _gi_h + oy,
                                       fill=dim(_gi_col, 0.40), outline="")
                gi_cv.create_text(_gi_w // 2 + ox, _gi_h // 2 + oy,
                                  text="LOGIN / REGISTER",
                                  fill="#000000", font=(UI_FONT, 9, "bold"),
                                  anchor="center")

            _gi_pressed = [False]

            def _gi_press(e):
                _gi_pressed[0] = True; _draw_gi(True)

            def _gi_release(e):
                _gi_pressed[0] = False; _draw_gi(False)
                self._play_click(); self.root.after(40, self._show_login)

            def _gi_leave(e):
                if _gi_pressed[0]: _gi_pressed[0] = False; _draw_gi(False)

            gi_cv.bind("<ButtonPress-1>",   _gi_press)
            gi_cv.bind("<ButtonRelease-1>", _gi_release)
            gi_cv.bind("<Leave>",           _gi_leave)
            _draw_gi()

    # ── Nav button layout ────────────────────────────────────────────
    def _nav_btns_layout(self):
        btns = [
            ("▶  RHYTHM STAGE",  BTN_COLORS[0], self._show_pre_game),
            ("★  ACES TRIVIA",   BTN_COLORS[2], self._show_trivia_confirmation),
            ("⚙  SETTINGS",      BTN_COLORS[4], self._show_settings),
            ("◆  RANKINGS",      BTN_COLORS[1], self._show_rankings),
            ("✕  EXIT",          BTN_COLORS[5], self._on_close),
        ]
        bw, bh  = 195, C.BTN_H + 4
        gap     = 12
        total_w = len(btns) * bw + (len(btns) - 1) * gap
        start_x = (_W - total_w) // 2
        # Ticker is 26 px tall; leave a clean intentional 12 px gap between
        # the button row bottom and the ticker top — no cramped overlap.
        TICKER_H        = 26
        GAP_ABOVE_TICKER = 12
        by1 = _H - TICKER_H - GAP_ABOVE_TICKER - bh
        by2 = by1 + bh
        result  = []
        for i, (lbl, col, cb) in enumerate(btns):
            bx1 = start_x + i * (bw + gap)
            result.append((lbl, col, cb, bx1, by1, bx1 + bw, by2))
        return result

    def _draw_nav_buttons(self, cv):
        if self.screen != "title":
            return
        HL = 4; SH = 4
        for i, (lbl, col, cb, x1, y1, x2, y2) in enumerate(self._nav_btns_layout()):
            pressed = (self._nav_pressed == i)
            ox, oy  = (2, 2) if pressed else (0, 0)
            bright  = lighten(col, 0.55)
            dark    = dim(col, 0.40)
            # Main face
            cv.create_rectangle(x1 + ox, y1 + oy, x2 + ox, y2 + oy, fill=col, outline="")
            # Highlight top
            cv.create_rectangle(x1 + ox, y1 + oy, x2 + ox, y1 + oy + HL, fill=bright, outline="")
            # Shadow bottom
            cv.create_rectangle(x1 + ox, y2 + oy - SH, x2 + ox, y2 + oy, fill=dark, outline="")
            # Label
            tx = (x1 + x2) // 2 + ox; ty = (y1 + y2) // 2 + oy
            cv.create_text(tx, ty, text=lbl, fill="#04000C",
                           font=(UI_FONT, 13, "bold"), anchor="center")

    def _on_canvas_press(self, ev):
        if self.screen == "title":
            for i, (lbl, col, cb, bx1, by1, bx2, by2) in enumerate(self._nav_btns_layout()):
                if bx1 <= ev.x <= bx2 and by1 <= ev.y <= by2:
                    self._nav_pressed = i; return
        if self.screen == "trivia_confirm":
            for key, (bx1, by1, bx2, by2) in getattr(self, "_tc_btn_rects", {}).items():
                if bx1 <= ev.x <= bx2 and by1 <= ev.y <= by2:
                    self._tc_btn_press = key; return
            self._tc_btn_press = None
        self._nav_pressed = None

    def _on_canvas_release(self, ev):
        if self.screen == "title":
            pi = self._nav_pressed; self._nav_pressed = None
            for i, (lbl, col, cb, bx1, by1, bx2, by2) in enumerate(self._nav_btns_layout()):
                if i == pi and bx1 <= ev.x <= bx2 and by1 <= ev.y <= by2:
                    self._play_click()
                    self._fade_to(cb); return
        if self.screen == "trivia_confirm":
            pk = getattr(self, "_tc_btn_press", None)
            self._tc_btn_press = None
            rects = getattr(self, "_tc_btn_rects", {})
            if pk and pk in rects:
                bx1, by1, bx2, by2 = rects[pk]
                if bx1 <= ev.x <= bx2 and by1 <= ev.y <= by2:
                    self._play_click()
                    if pk == "start":
                        self._fade_to(self._start_trivia)
                    elif pk == "back":
                        self._fade_to(self._show_title)
            return
        self._nav_pressed = None

    # ═══════════════════════════════════════════════════════════════
    #  PRE-GAME  (Carousel Song Select)
    # ═══════════════════════════════════════════════════════════════
    def _show_pre_game(self):
        self._clear_widgets(); self.screen = "pre_game"
        audio.stop_preview()
        # ENHANCED v18.1 FINAL: Stop home screen BGM when entering song selection
        audio.stop_bgm()

        playable              = get_all_playable_songs(cfg.songs)
        self._carousel_songs  = ["SHUFFLE"] + [n for n, _ in playable]
        self._carousel_idx    = 0
        self._carousel_anim   = 0.0
        self._carousel_target = 0
        self._preview_song    = None

        # Preload covers in background
        def _preload():
            for name in self._carousel_songs[1:]:
                self._load_cover_image(name, 220)
        threading.Thread(target=_preload, daemon=True).start()

        outer = tk.Frame(self.root, bg=BG_COL,
                         highlightbackground="#00E5FF", highlightthickness=2)  # Stars visible through
        outer.place(relx=0.5, rely=0.5, anchor="center", width=min(900, int(_W*0.90)), height=min(620, int(_H*0.88)))

        tk.Label(outer, text="♪  SELECT YOUR SONG", bg="#0a0018", fg="#00E5FF",
                 font=(UI_FONT, 20, "bold")).pack(pady=(14, 10))

        main_f = tk.Frame(outer, bg="#0a0018")
        main_f.pack(fill="both", expand=True, padx=16, pady=2)

        # ── Left: Carousel ────────────────────────────────────────────
        left_f = tk.Frame(main_f, bg="#0a0018"); left_f.pack(side="left", fill="both", expand=True)
        self._carousel_canvas = tk.Canvas(left_f, width=530, height=320,
                                          bg="#0a0018", highlightthickness=0)
        self._carousel_canvas.pack(pady=(0, 4))

        self._carousel_title_var = tk.StringVar(value="")
        tk.Label(left_f, textvariable=self._carousel_title_var,
                 bg="#0a0018", fg="#FFD700", font=(UI_FONT, 13, "bold"),
                 wraplength=500).pack()

        # Nav arrows — colorful pixel-style buttons
        nav_f = tk.Frame(left_f, bg="#0a0018"); nav_f.pack(pady=(10, 0))
        nav_cfg = [("◀◀  PREV", BTN_COLORS[1], -1), ("NEXT  ▶▶", BTN_COLORS[2], 1)]
        for nav_lbl, nav_col, nav_dir in nav_cfg:
            self._pixel_btn(nav_f, nav_lbl, nav_col,
                            lambda d=nav_dir: (self._play_click(), self._carousel_move(d)),
                            width=150).pack(side="left", padx=10)

        # ── Right: Difficulty & Lanes ─────────────────────────────────
        right_f = tk.Frame(main_f, bg="#0a0018"); right_f.pack(side="right", fill="y", padx=(12, 0))

        tk.Label(right_f, text="DIFFICULTY", bg="#0a0018", fg="#FFD700",
                 font=(UI_FONT, 13, "bold")).pack(anchor="w", pady=(0, 6))
        self._diff_var   = tk.StringVar(value=cfg.difficulty)
        diff_cvs = {}   # val -> canvas widget

        DIFF_PALETTE = {
            "Easy":   (BTN_COLORS[3], "#000000"),   # green, dark text
            "Normal": (BTN_COLORS[2], "#000000"),   # cyan, dark text
            "Hard":   (BTN_COLORS[0], "#000000"),   # gold, dark text
            "ACE":    (BTN_COLORS[1], "#000000"),   # pink, dark text
        }
        HL_D = 4; SH_D = 4; DBW = 170; DBH = 40

        def _draw_diff_btn(cv_d, col, label, selected, pressed=False):
            cv_d.delete("all")
            ox, oy = (1, 1) if pressed else (0, 0)
            if selected:
                bg_c   = col
                text_c = "#000000"
                bright = lighten(col, 0.55)
                dark   = dim(col, 0.40)
            else:
                bg_c   = dim(col, 0.18)
                text_c = dim(col, 0.65)
                bright = dim(col, 0.30)
                dark   = dim(col, 0.12)
            cv_d.create_rectangle(ox, oy, DBW+ox, DBH+oy, fill=bg_c, outline="")
            cv_d.create_rectangle(ox, oy, DBW+ox, oy+HL_D, fill=bright, outline="")
            cv_d.create_rectangle(ox, DBH+oy-SH_D, DBW+ox, DBH+oy, fill=dark, outline="")
            border = col if selected else dim(col, 0.35)
            cv_d.create_rectangle(ox, oy, DBW+ox-1, DBH+oy-1, fill="", outline=border, width=1)
            cv_d.create_text(DBW//2+ox, DBH//2+oy, text=label,
                             fill=text_c, font=(UI_FONT, 13, "bold"), anchor="center")

        def _update_diff(selected):
            for val, cv_d in diff_cvs.items():
                col = DIFF_PALETTE[val][0]
                _draw_diff_btn(cv_d, col, val.upper(), selected=(val == selected))

        for d, (col, _) in DIFF_PALETTE.items():
            cv_d = tk.Canvas(right_f, width=DBW, height=DBH,
                             bg="#0a0018", highlightthickness=0, cursor="hand2")
            cv_d.pack(fill="x", pady=4)
            _d_pressed = [False]
            def _make_diff_handlers(val_, cv_, col_):
                def _press(e):
                    _d_pressed[0] = True
                    _draw_diff_btn(cv_, col_, val_.upper(),
                                   selected=(val_ == self._diff_var.get()), pressed=True)
                def _release(e):
                    _d_pressed[0] = False
                    self._play_click()
                    setattr(cfg, "difficulty", val_)
                    self._diff_var.set(val_)
                    _update_diff(val_)
                def _leave(e):
                    if _d_pressed[0]:
                        _d_pressed[0] = False
                        _draw_diff_btn(cv_, col_, val_.upper(),
                                       selected=(val_ == self._diff_var.get()))
                cv_.bind("<ButtonPress-1>",   _press)
                cv_.bind("<ButtonRelease-1>", _release)
                cv_.bind("<Leave>",           _leave)
            _make_diff_handlers(d, cv_d, col)
            diff_cvs[d] = cv_d
        _update_diff(cfg.difficulty)

        tk.Label(right_f, text="NUMBER OF LANES", bg="#0a0018", fg="#FFD700",
                 font=(UI_FONT, 11, "bold")).pack(anchor="w", pady=(16, 6))
        self._lanes_var = tk.IntVar(value=cfg.num_lanes)
        lf2 = tk.Frame(right_f, bg="#0a0018"); lf2.pack(fill="x")
        lane_cvs = {}
        LANE_PALETTE = {3: BTN_COLORS[4], 4: BTN_COLORS[0], 5: BTN_COLORS[2]}
        LBW = 170; LBH = 36; HL_L = 3; SH_L = 3

        def _draw_lane_btn(cv_l, col, label, selected, pressed=False):
            cv_l.delete("all")
            ox, oy = (1, 1) if pressed else (0, 0)
            if selected:
                bg_c = col; text_c = "#000000"
                bright = lighten(col, 0.55); dark = dim(col, 0.40)
            else:
                bg_c = dim(col, 0.18); text_c = dim(col, 0.65)
                bright = dim(col, 0.30); dark = dim(col, 0.12)
            cv_l.create_rectangle(ox, oy, LBW+ox, LBH+oy, fill=bg_c, outline="")
            cv_l.create_rectangle(ox, oy, LBW+ox, oy+HL_L, fill=bright, outline="")
            cv_l.create_rectangle(ox, LBH+oy-SH_L, LBW+ox, LBH+oy, fill=dark, outline="")
            border = col if selected else dim(col, 0.35)
            cv_l.create_rectangle(ox, oy, LBW+ox-1, LBH+oy-1, fill="", outline=border, width=1)
            cv_l.create_text(LBW//2+ox, LBH//2+oy, text=label,
                             fill=text_c, font=(UI_FONT, 12, "bold"), anchor="center")

        def _update_lanes(selected):
            for val, cv_l in lane_cvs.items():
                col = LANE_PALETTE[val]
                _draw_lane_btn(cv_l, col, f"{val} LANES", selected=(val == selected))

        for n in (3, 4, 5):
            col   = LANE_PALETTE[n]
            cv_l  = tk.Canvas(lf2, width=LBW, height=LBH,
                              bg="#0a0018", highlightthickness=0, cursor="hand2")
            cv_l.pack(fill="x", pady=3)
            _lp = [False]
            def _make_lane_handlers(val_, cv_, col_):
                def _press(e):
                    _lp[0] = True
                    _draw_lane_btn(cv_, col_, f"{val_} LANES",
                                   selected=(val_ == self._lanes_var.get()), pressed=True)
                def _release(e):
                    _lp[0] = False
                    self._play_click()
                    setattr(cfg, "num_lanes", val_)
                    self._lanes_var.set(val_)
                    _update_lanes(val_)
                def _leave(e):
                    if _lp[0]:
                        _lp[0] = False
                        _draw_lane_btn(cv_, col_, f"{val_} LANES",
                                       selected=(val_ == self._lanes_var.get()))
                cv_.bind("<ButtonPress-1>",   _press)
                cv_.bind("<ButtonRelease-1>", _release)
                cv_.bind("<Leave>",           _leave)
            _make_lane_handlers(n, cv_l, col)
            lane_cvs[n] = cv_l
        _update_lanes(cfg.num_lanes)

        # Bottom buttons
        bf2 = tk.Frame(outer, bg="#0a0018"); bf2.pack(pady=(6, 14), fill="x")
        inner_bf2 = tk.Frame(bf2, bg="#0a0018"); inner_bf2.pack(anchor="center", padx=(0, 120))
        self._pixel_btn(inner_bf2, "▶  START PERFORMANCE", BTN_COLORS[0],
                        self._confirm_pre_game, width=210).pack(side="left", padx=10)
        self._pixel_btn(inner_bf2, "✖  CANCEL", BTN_COLORS[1],
                        lambda: (self._cancel_preview_timer(), audio.stop_preview(), self._fade_to(self._show_title)),
                        width=140).pack(side="left", padx=10)
        self._carousel_render()
        self._carousel_update_info()
        self._schedule_preview()

    def _carousel_move(self, direction):
        n = len(self._carousel_songs)
        if n == 0: return
        self._carousel_idx = (self._carousel_idx + direction) % n
        self._carousel_render()
        self._carousel_update_info()
        self._schedule_preview()

    def _carousel_update_info(self):
        idx   = self._carousel_idx
        songs = self._carousel_songs
        if not songs: return
        name  = songs[idx]
        if name == "SHUFFLE":
            self._carousel_title_var.set(f"♾  SHUFFLE — {len(songs)-1} songs")
        else:
            self._carousel_title_var.set(name.upper())

    def _schedule_preview(self):
        """Play preview immediately on navigation; loops while on the same song."""
        # Cancel any pending loop check first
        self._cancel_preview_timer()
        audio.stop_preview()
        self._preview_song = None
        self._play_preview_current()

    def _cancel_preview_timer(self):
        """Cancel the looping preview timer safely."""
        if self._preview_timer:
            try:
                self.root.after_cancel(self._preview_timer)
            except Exception:
                pass
            self._preview_timer = None

    def _play_preview_current(self):
        if self.screen != "pre_game":
            return
        idx   = self._carousel_idx
        songs = self._carousel_songs
        if not songs or idx >= len(songs):
            return
        name = songs[idx]
        if name == "SHUFFLE":
            return
        prev_path = find_preview(name)
        if prev_path:
            audio.play_preview(prev_path)
            self._preview_song = name
            # Schedule loop-check after 500 ms
            self._preview_timer = self.root.after(500, self._loop_preview_check)

    def _loop_preview_check(self):
        """Re-start preview clip if it has finished, only while still on pre_game screen."""
        self._preview_timer = None
        # Hard guard: do nothing if we left the song-select screen
        if self.screen != "pre_game":
            return
        idx   = self._carousel_idx
        songs = self._carousel_songs
        if not songs or idx >= len(songs):
            return
        name = songs[idx]
        # Guard: only loop for the song that was originally playing
        if name != self._preview_song:
            return
        try:
            import pygame
            ch = getattr(audio, "_preview_channel", None)
            if ch is None or not ch.get_busy():
                prev_path = find_preview(name)
                if prev_path:
                    audio.play_preview(prev_path)
        except Exception:
            pass
        # Re-schedule only if still on pre_game
        if self.screen == "pre_game":
            self._preview_timer = self.root.after(500, self._loop_preview_check)

    def _carousel_render(self):
        if not hasattr(self, "_carousel_canvas"): return
        try:
            cv = self._carousel_canvas; cv.delete("all")
        except tk.TclError:
            return
        songs = self._carousel_songs
        if not songs: return
        cw, ch   = 530, 320
        cx       = cw // 2; cy = ch // 2
        COVER_W  = 200; COVER_H = 200; SPACING = 230

        for offset in [-2, -1, 1, 2, 0]:
            ri        = (self._carousel_idx + offset) % len(songs)
            sname     = songs[ri]
            x_center  = cx + offset * SPACING
            scale     = 1.0 - abs(offset) * 0.18
            w         = int(COVER_W * scale); h = int(COVER_H * scale)
            y_center  = cy + abs(offset) * 18
            if x_center + w // 2 < 0 or x_center - w // 2 > cw: continue
            alpha_frac = 1.0 - abs(offset) * 0.35
            card_col   = blend("#0c0022", "#1a0040", 0.5 if offset == 0 else 0.2)
            border_col = "#00E5FF" if offset == 0 else dim("#334455", alpha_frac)
            bw_        = 3 if offset == 0 else 1
            cv.create_rectangle(x_center - w // 2 - 4, y_center - h // 2 - 4,
                                 x_center + w // 2 + 4, y_center + h // 2 + 4,
                                 fill=card_col, outline=border_col, width=bw_)
            cover = self._load_cover_image(sname, max(60, w))
            if cover:
                try: cv.create_image(x_center, y_center, image=cover, anchor="center")
                except Exception: pass
            else:
                mc = MEMBER_COLORS[abs(hash(sname)) % len(MEMBER_COLORS)]
                cv.create_rectangle(x_center - w // 2, y_center - h // 2,
                                     x_center + w // 2, y_center + h // 2,
                                     fill=dim(mc, 0.25 * alpha_frac), outline="")
                cv.create_text(x_center, y_center,
                               text=(sname[:14] + "…" if len(sname) > 14 else sname),
                               fill=dim(mc, alpha_frac),
                               font=(UI_FONT, max(8, int(10 * scale)), "bold"), width=w - 10)
            if offset == 0:
                pls = 0.55 + 0.25 * math.sin(self.t * 3.0)
                mc2 = MEMBER_COLORS[self._carousel_idx % len(MEMBER_COLORS)]
                for ri2 in range(3):
                    cv.create_rectangle(x_center - w // 2 - 4 - ri2 * 2, y_center - h // 2 - 4 - ri2 * 2,
                                         x_center + w // 2 + 4 + ri2 * 2, y_center + h // 2 + 4 + ri2 * 2,
                                         fill="", outline=dim(mc2, pls * (0.5 - ri2 * 0.12)), width=1)

        n = len(songs); max_dots = min(n, 15); dot_spacing = 14
        dot_y   = ch - 14; start_x = cx - (max_dots - 1) * dot_spacing // 2
        for di in range(max_dots):
            dx        = start_x + di * dot_spacing
            is_center = (di == max_dots // 2)
            col       = "#FFD700" if is_center else "#334455"
            r_dot     = 4 if is_center else 2
            cv.create_oval(dx - r_dot, dot_y - r_dot, dx + r_dot, dot_y + r_dot,
                           fill=col, outline="")

    def _confirm_pre_game(self):
        self._cancel_preview_timer()
        audio.stop_preview()
        self._preview_song = None
        idx  = self._carousel_idx
        if idx < 0 or idx >= len(self._carousel_songs): return
        name = self._carousel_songs[idx]
        cfg.selected_mode = "shuffle" if name == "SHUFFLE" else "single"
        cfg.selected_song = name
        cfg.num_lanes     = self._lanes_var.get()
        self._fade_to(self._start_game)

    # ═══════════════════════════════════════════════════════════════
    #  SETTINGS
    # ═══════════════════════════════════════════════════════════════
    def _show_settings(self):
        self._clear_widgets(); self.screen = "settings"
        # BGM keeps playing so the user hears volume changes live.

        # Snapshot originals so CANCEL can truly restore them
        _orig_vol = cfg.master_volume
        _orig_fs  = cfg.fullscreen

        # Responsive panel: 82% wide, capped at 700 px, auto-height (with min height for buttons)
        panel_w = min(700, int(_W * 0.82))
        panel_h = min(520, int(_H * 0.75))  # Ensure minimum height for save/close buttons
        outer = tk.Frame(self.root, bg="#0a0018",
                         highlightbackground="#FFD700", highlightthickness=2)
        outer.place(relx=0.5, rely=0.5, anchor="center", width=panel_w, height=panel_h)

        tk.Label(outer, text="⚙   S E T T I N G S", bg="#0a0018", fg="#FFD700",
                 font=(UI_FONT, 20, "bold")).pack(pady=(20, 4))
        tk.Frame(outer, bg="#FFD700", height=1).pack(fill="x", padx=24, pady=(0, 14))

        body = tk.Frame(outer, bg="#0a0018"); body.pack(fill="both", expand=True, padx=32, pady=(0, 8))

        # ── Single Volume slider ─────────────────────────────────────
        self._section_label(body, "VOLUME")

        vol_row = tk.Frame(body, bg="#0a0018"); vol_row.pack(fill="x", pady=10)
        tk.Label(vol_row, text="♪  Volume", bg="#0a0018", fg="#FFD700",
                 font=(UI_FONT, 11, "bold"), width=14, anchor="w").pack(side="left")

        vol_var = tk.DoubleVar(value=cfg.master_volume)
        pct_lbl = tk.Label(vol_row, text=f"{int(cfg.master_volume * 100)}%",
                           bg="#0a0018", fg="#FFFFFF",
                           font=(UI_FONT, 11, "bold"), width=5)
        pct_lbl.pack(side="right")

        slider_len = max(200, panel_w - 220)

        def _on_vol(v):
            val = float(v)
            pct_lbl.config(text=f"{int(val * 100)}%")
            cfg.master_volume = val
            cfg.music_volume  = val
            cfg.sfx_volume    = val
            cfg.sfx_intensity = val  # Also sync particle/visual intensity with master volume
            cfg.apply()  # Updates constants + music volume via set_volume()
            try:
                # Ensure all audio channels respond to master volume changes in real-time
                audio.set_master_volume(val)  # Re-apply effective music volume (master × music)
                audio.set_sfx_volume(val)     # Apply to SFX/preview channel
            except Exception:
                pass

        tk.Scale(vol_row, variable=vol_var, from_=0.0, to=1.0, resolution=0.01,
                 orient="horizontal", length=slider_len,
                 bg="#0a0018", fg="#FFD700",
                 troughcolor="#220044", activebackground="#FFD700",
                 highlightthickness=0, sliderlength=22, width=14,
                 command=_on_vol).pack(side="left", padx=(8, 0))

        # ── Display section ──────────────────────────────────────────
        self._section_label(body, "DISPLAY")
        fs_row = tk.Frame(body, bg="#0a0018"); fs_row.pack(fill="x", pady=(10, 0))
        fs_var = tk.BooleanVar(value=cfg.fullscreen)

        _fs_w, _fs_h = min(240, panel_w - 40), 38
        fs_cv = tk.Canvas(fs_row, width=_fs_w, height=_fs_h,
                          bg="#0a0018", highlightthickness=0, cursor="hand2")
        fs_cv.pack(side="left")

        def _draw_fs_btn(on=False, pressed=False):
            fs_cv.delete("all")
            col = BTN_COLORS[2] if on else dim(BTN_COLORS[2], 0.25)
            ox, oy = (1, 1) if pressed else (0, 0)
            fs_cv.create_rectangle(ox, oy, _fs_w+ox, _fs_h+oy, fill=col, outline="")
            fs_cv.create_rectangle(ox, oy, _fs_w+ox, oy+3, fill=lighten(col, 0.55), outline="")
            fs_cv.create_rectangle(ox, _fs_h+oy-3, _fs_w+ox, _fs_h+oy, fill=dim(col, 0.40), outline="")
            lbl = "⛶  FULLSCREEN  ON" if on else "⛶  FULLSCREEN  OFF"
            fg  = "#000000" if on else dim(BTN_COLORS[2], 0.80)
            fs_cv.create_text(_fs_w//2+ox, _fs_h//2+oy, text=lbl,
                              fill=fg, font=(UI_FONT, 11, "bold"), anchor="center")

        _draw_fs_btn(cfg.fullscreen)
        _fs_pressed = [False]

        def _fs_press(e):
            _fs_pressed[0] = True
            _draw_fs_btn(fs_var.get(), True)

        def _fs_release(e):
            _fs_pressed[0] = False
            new_val = not fs_var.get()
            fs_var.set(new_val)
            cfg.fullscreen = new_val
            self._apply_fullscreen()
            _draw_fs_btn(new_val)

        def _fs_leave(e):
            if _fs_pressed[0]:
                _fs_pressed[0] = False
                _draw_fs_btn(fs_var.get())

        fs_cv.bind("<ButtonPress-1>",   _fs_press)
        fs_cv.bind("<ButtonRelease-1>", _fs_release)
        fs_cv.bind("<Leave>",           _fs_leave)

        # ── Save & Cancel ────────────────────────────────────────────
        BTN_W_S = 180; BTN_H_S = C.BTN_H; GAP_S = 12; HL = 4; SH = 4

        def _make_pixel_btn_s(parent, label, col, cmd, width=None):
            bw = width if width else BTN_W_S
            cv_btn = tk.Canvas(parent, width=bw, height=BTN_H_S,
                               bg="#0a0018", highlightthickness=0, cursor="hand2")
            pressed = [False]
            def _draw(p=False):
                cv_btn.delete("all")
                ox, oy = (2, 2) if p else (0, 0)
                bright = lighten(col, 0.55); dark = dim(col, 0.40)
                cv_btn.create_rectangle(ox, oy, bw+ox, BTN_H_S+oy, fill=col, outline="")
                cv_btn.create_rectangle(ox, oy, bw+ox, oy+HL, fill=bright, outline="")
                cv_btn.create_rectangle(ox, BTN_H_S+oy-SH, bw+ox, BTN_H_S+oy, fill=dark, outline="")
                cv_btn.create_text(bw//2+ox, BTN_H_S//2+oy, text=label,
                                   fill="#04000C", font=(UI_FONT, 13, "bold"), anchor="center")
            def _press(e):  pressed[0] = True;  _draw(True)
            def _release(e):
                pressed[0] = False; _draw(False)
                self._play_click(); self.root.after(40, cmd)
            def _leave(e):
                if pressed[0]: pressed[0] = False; _draw(False)
            cv_btn.bind("<ButtonPress-1>",   _press)
            cv_btn.bind("<ButtonRelease-1>", _release)
            cv_btn.bind("<Leave>",           _leave)
            _draw()
            return cv_btn

        def _do_save():
            # Sync all audio channels to master volume level
            cfg.music_volume = cfg.master_volume
            cfg.sfx_volume   = cfg.master_volume
            cfg.sfx_intensity = cfg.master_volume     # Also sync SFX intensity for particles
            cfg.apply()  # Update constants + music volume via audio.set_volume()
            
            # Re-apply effective music volume explicitly to ensure all channels are synced
            try:
                audio.set_master_volume(cfg.master_volume)  # Ensures music volume respects MASTER_VOLUME × MUSIC_VOLUME
                audio.set_sfx_volume(cfg.master_volume)     # Ensure SFX preview volume is synced
            except Exception:
                pass
            
            # Persist settings to database for logged-in users
            if not session.is_guest:
                try:
                    cfg.save_to_db(session.account_id)
                except Exception:
                    pass
            
            # Clean transition: destroy window first, then fade to title screen
            outer.destroy()
            self._fade_to(self._show_title)

        def _do_cancel():
            # Restore every audio channel to the original snapshot value
            cfg.master_volume = _orig_vol
            cfg.music_volume  = _orig_vol
            cfg.sfx_volume    = _orig_vol
            cfg.fullscreen    = _orig_fs
            cfg.apply()  # Update constants + music volume
            
            # Ensure all audio channels are properly restored
            try:
                audio.set_master_volume(_orig_vol)   # Restore effective music volume
                audio.set_sfx_volume(_orig_vol)      # Restore SFX preview volume
            except Exception:
                pass
            
            # Restore fullscreen setting if it was changed
            if cfg.fullscreen != _orig_fs:
                self._apply_fullscreen()
            
            # Clean transition: destroy window first, then fade to title screen
            outer.destroy()
            self._fade_to(self._show_title)

        bf = tk.Frame(body, bg="#0a0018"); bf.pack(pady=(20, 18), fill="x")
        _make_pixel_btn_s(bf, "✔  SAVE & CLOSE", BTN_COLORS[0],
                          _do_save, width=BTN_W_S + 20).pack(side="left", padx=(0, GAP_S))
        _make_pixel_btn_s(bf, "✖  CANCEL", BTN_COLORS[1],
                          _do_cancel, width=130).pack(side="left")

    # NOTE: Settings are now saved via the _do_save() function inside _show_settings()
    # This ensures proper window lifecycle management and smooth transitions.

    # ═══════════════════════════════════════════════════════════════
    #  RANKINGS / LEADERBOARD
    # ═══════════════════════════════════════════════════════════════
    def _show_rankings(self):
        self._clear_widgets(); self.screen = "rankings"
        # BG_COL background lets the star/spotlight canvas show through the frame gaps
        outer = tk.Frame(self.root, bg=BG_COL,
                         highlightbackground="#FFD700", highlightthickness=2)
        outer.place(relx=0.5, rely=0.5, anchor="center", width=min(820, int(_W*0.88)), height=min(600, int(_H*0.88)))

        hdr = tk.Frame(outer, bg=BG_COL); hdr.pack(pady=(20, 4))
        if "ranking_icon" in self.img_refs:
            tk.Label(hdr, image=self.img_refs["ranking_icon"], bg=BG_COL).pack(side="left", padx=(0, 10))
        tk.Label(hdr, text="THE LIGHT STAGE  —  TOP ACES", bg=BG_COL, fg="#FFD700",
                 font=(UI_FONT, 20, "bold")).pack(side="left")

        # Difficulty filter tabs — colorful pixel-style
        tab_f   = tk.Frame(outer, bg=BG_COL); tab_f.pack(pady=(4, 0))
        tab_var = tk.StringVar(value="All")
        tab_cvs = {}
        TAB_COLORS = {
            "All":    BTN_COLORS[0],   # gold
            "Easy":   BTN_COLORS[3],   # green
            "Normal": BTN_COLORS[2],   # cyan
            "Hard":   BTN_COLORS[5],   # purple
            "ACE":    BTN_COLORS[1],   # pink
        }
        TBW = 110; TBH = 36; HL_T = 3; SH_T = 3

        def _draw_tab(cv_t, col, label, selected, pressed=False):
            cv_t.delete("all")
            ox, oy = (1, 1) if pressed else (0, 0)
            if selected:
                bg_c = col; text_c = "#000000"
                bright = lighten(col, 0.55); dark = dim(col, 0.40)
            else:
                bg_c = dim(col, 0.18); text_c = dim(col, 0.72)
                bright = dim(col, 0.30); dark = dim(col, 0.12)
            cv_t.create_rectangle(ox, oy, TBW+ox, TBH+oy, fill=bg_c, outline="")
            cv_t.create_rectangle(ox, oy, TBW+ox, oy+HL_T, fill=bright, outline="")
            cv_t.create_rectangle(ox, TBH+oy-SH_T, TBW+ox, TBH+oy, fill=dark, outline="")
            border = col if selected else dim(col, 0.35)
            cv_t.create_rectangle(ox, oy, TBW+ox-1, TBH+oy-1, fill="", outline=border, width=1)
            cv_t.create_text(TBW//2+ox, TBH//2+oy, text=label,
                             fill=text_c, font=(UI_FONT, 11, "bold"), anchor="center")

        def _update_tabs(selected):
            for d, cv_t in tab_cvs.items():
                _draw_tab(cv_t, TAB_COLORS[d], d, selected=(d == selected))

        def _load_tab(diff):
            tab_var.set(diff)
            _update_tabs(diff)
            _populate_rows(diff)

        for diff_name in ["All", "Easy", "Normal", "Hard", "ACE"]:
            col  = TAB_COLORS[diff_name]
            cv_t = tk.Canvas(tab_f, width=TBW, height=TBH,
                             bg=BG_COL, highlightthickness=0, cursor="hand2")
            cv_t.pack(side="left", padx=3)
            _tp = [False]
            def _make_tab_handlers(val_, cv_, col_):
                def _press(e):
                    _tp[0] = True
                    _draw_tab(cv_, col_, val_, selected=(val_ == tab_var.get()), pressed=True)
                def _release(e):
                    _tp[0] = False
                    self._play_click()
                    _load_tab(val_)
                def _leave(e):
                    if _tp[0]:
                        _tp[0] = False
                        _draw_tab(cv_, col_, val_, selected=(val_ == tab_var.get()))
                cv_.bind("<ButtonPress-1>",   _press)
                cv_.bind("<ButtonRelease-1>", _release)
                cv_.bind("<Leave>",           _leave)
            _make_tab_handlers(diff_name, cv_t, col)
            tab_cvs[diff_name] = cv_t
        _update_tabs("All")

        col_defs = [("#", 4), ("PLAYER", 18), ("SCORE", 12), ("GRADE", 7),
                    ("ACC", 6), ("COMBO", 8), ("SONG", 16), ("DATE", 10)]
        hdr_f = tk.Frame(outer, bg="#1a0040"); hdr_f.pack(fill="x", padx=20, pady=(6, 0))
        for col_txt, col_w in col_defs:
            tk.Label(hdr_f, text=col_txt, bg="#1a0040", fg="#FFD700",
                     font=(UI_FONT, 10, "bold"), width=col_w, anchor="center").pack(side="left", padx=2, pady=4)

        table_f = tk.Frame(outer, bg="#04000C"); table_f.pack(fill="both", expand=True, padx=20, pady=2)
        scroll  = tk.Scrollbar(table_f); scroll.pack(side="right", fill="y")
        rank_cv = tk.Canvas(table_f, bg="#04000C", highlightthickness=0, yscrollcommand=scroll.set)
        rank_cv.pack(fill="both", expand=True)
        scroll.config(command=rank_cv.yview)
        rows_f  = tk.Frame(rank_cv, bg="#04000C")
        rank_cv.create_window((0, 0), window=rows_f, anchor="nw")

        rank_colors = {"S": "#FFD700", "A": "#00FF99", "B": "#00E5FF", "C": "#FF8800", "D": "#FF3385"}

        # Current logged-in username for row highlighting — None for guests
        _logged_user = session.username if not session.is_guest else None

        def _populate_rows(diff):
            for w in rows_f.winfo_children():
                w.destroy()
            if diff == "All":
                scores = db.load_scores(50)
            else:
                scores = db.load_top_scores_by_difficulty(diff, 50)
            for i, s in enumerate(scores, 1):
                rk       = s.get("grade", "?")
                fg_c     = rank_colors.get(rk, "#CCCCCC")
                # Highlight the logged-in player's row with a gold-tinted background
                is_me    = (_logged_user and
                            s.get("player_name", "").lower() == _logged_user.lower())
                row_bg   = ("#1a1200" if is_me else
                            "#0a0020" if i % 2 == 0 else "#060014")
                row      = tk.Frame(rows_f, bg=row_bg,
                                    highlightbackground="#FFD700" if is_me else row_bg,
                                    highlightthickness=1 if is_me else 0)
                row.pack(fill="x", pady=1)
                # "★" star prefix on the player-name cell for logged-in user
                name_txt = (f"★ {s.get('player_name', '?')}" if is_me
                            else s.get("player_name", "?"))
                name_col = "#FFD700" if is_me else fg_c
                cells  = [
                    (str(i),                        4,  "#FFD700" if is_me else "#888888"),
                    (name_txt[:18],                 18, name_col),
                    (f"{s.get('score', 0):,}",      12, "#FFFFFF"),
                    (rk,                             7,  fg_c),
                    (f"{s.get('accuracy', 0)}%",     6,  "#AAAAAA"),
                    (f"x{s.get('max_combo', 0)}",    8,  "#AAAAAA"),
                    (s.get("song_name", "")[:14],   16, "#777777"),
                    (s.get("played_at", "")[:10],   10, "#666666"),
                ]
                for txt, w_, fc in cells:
                    tk.Label(row, text=txt, bg=row_bg, fg=fc,
                             font=(UI_FONT, 10, "bold" if is_me else "normal"),
                             width=w_, anchor="center").pack(side="left", padx=2, pady=3)
            if not scores:
                tk.Label(rows_f, text="  No scores yet — play a game to be first!",
                         bg="#04000C", fg="#666666", font=(UI_FONT, 11)).pack(pady=20)
            rows_f.update_idletasks()
            rank_cv.config(scrollregion=rank_cv.bbox("all"))

        _populate_rows("All")

        # ── Logged-in player's personal best summary (below the table) ─
        if not session.is_guest:
            pb_scores = db.load_scores_for_account(session.account_id, 1)
            pb_f = tk.Frame(outer, bg="#0a0018",
                            highlightbackground="#FFD700", highlightthickness=1)
            pb_f.pack(fill="x", padx=20, pady=(4, 0))
            tk.Label(pb_f, text=f"★  YOUR BEST — {session.username.upper()}",
                     bg="#0a0018", fg="#FFD700",
                     font=(UI_FONT, 10, "bold")).pack(side="left", padx=(12, 20), pady=6)
            if pb_scores:
                pb = pb_scores[0]
                rk2 = pb.get("grade", "?")
                for lbl2, val2, vc2 in [
                    ("SCORE",    f"{pb.get('score', 0):,}", "#FFFFFF"),
                    ("GRADE",    rk2,                       rank_colors.get(rk2, "#888")),
                    ("ACC",      f"{pb.get('accuracy', 0)}%", "#00E5FF"),
                    ("COMBO",    f"x{pb.get('max_combo', 0)}", "#FF8800"),
                    ("SONG",     pb.get("song_name", "")[:16], "#777777"),
                ]:
                    tk.Label(pb_f, text=f"{lbl2}: {val2}", bg="#0a0018", fg=vc2,
                             font=(UI_FONT, 9, "bold")).pack(side="left", padx=10, pady=6)
            else:
                tk.Label(pb_f, text="No scores saved yet — play a game!",
                         bg="#0a0018", fg="#666666",
                         font=(UI_FONT, 9)).pack(side="left", padx=10, pady=6)

        # Bottom button row — MAIN STAGE and ACES TRIVIA RANKINGS side-by-side
        bf = tk.Frame(outer, bg=BG_COL); bf.pack(pady=(8, 16))
        self._pixel_btn(bf, "⌂  MAIN STAGE", BTN_COLORS[2],
                        lambda: self._fade_to(self._show_title), width=180).pack(side="left", padx=10)
        # Trivia rankings button — same pixel-style as all other buttons
        self._pixel_btn(bf, "✦  ACES TRIVIA RANKINGS", BTN_COLORS[0],
                        lambda: self._fade_to(self._show_trivia_rankings), width=230).pack(side="left", padx=10)
        # NOTE: "Clear All Scores" button intentionally NOT present (spec requirement)

    # ── Aces Trivia Leaderboard ───────────────────────────────────────
    def _show_trivia_rankings(self):
        """
        Dedicated leaderboard for the Aces Trivia game.
        Shows top scores (correct / total, accuracy %) for all players.
        Logged-in player's rows are gold-highlighted with a ★ prefix,
        and a personal-best summary bar is shown below the table.
        Mirrors the visual style of _show_rankings() exactly.
        """
        self._clear_widgets(); self.screen = "trivia_rankings"

        # BG_COL background lets the star/spotlight canvas show through the frame gaps
        outer = tk.Frame(self.root, bg=BG_COL,
                         highlightbackground="#FFD700", highlightthickness=2)
        outer.place(relx=0.5, rely=0.5, anchor="center",
                    width=min(820, int(_W * 0.88)), height=min(600, int(_H * 0.88)))

        # ── Header ───────────────────────────────────────────────────
        hdr = tk.Frame(outer, bg=BG_COL); hdr.pack(pady=(20, 4))
        if "ranking_icon" in self.img_refs:
            tk.Label(hdr, image=self.img_refs["ranking_icon"],
                     bg=BG_COL).pack(side="left", padx=(0, 10))
        tk.Label(hdr, text="✦  ACES TRIVIA  —  TOP SCHOLARS", bg=BG_COL, fg="#FFD700",
                 font=(UI_FONT, 20, "bold")).pack(side="left")

        # Subtitle — ranking criteria: accuracy first, then speed, then recency
        tk.Label(outer,
                 text="Most correct answers first  ·  equal scores ranked by fastest total time  ·  then most recent",
                 bg=BG_COL, fg="#556677", font=(UI_FONT, 9)).pack()

        # ── Column header row ────────────────────────────────────────
        TV_COL_DEFS = [
            ("#",         4),
            ("PLAYER",   16),
            ("CORRECT",   9),
            ("TOTAL",     7),
            ("ACC %",     7),
            ("TIME (s)",  9),
            ("GRADE",     7),
            ("DATE",     11),
        ]
        hdr_f = tk.Frame(outer, bg="#1a0040"); hdr_f.pack(fill="x", padx=20, pady=(8, 0))
        for col_txt, col_w in TV_COL_DEFS:
            tk.Label(hdr_f, text=col_txt, bg="#1a0040", fg="#FFD700",
                     font=(UI_FONT, 10, "bold"), width=col_w,
                     anchor="center").pack(side="left", padx=2, pady=4)

        # ── Scrollable table ─────────────────────────────────────────
        table_f = tk.Frame(outer, bg="#04000C")
        table_f.pack(fill="both", expand=True, padx=20, pady=2)
        tv_scroll = tk.Scrollbar(table_f); tv_scroll.pack(side="right", fill="y")
        tv_cv     = tk.Canvas(table_f, bg="#04000C", highlightthickness=0,
                              yscrollcommand=tv_scroll.set)
        tv_cv.pack(fill="both", expand=True)
        tv_scroll.config(command=tv_cv.yview)
        tv_rows_f = tk.Frame(tv_cv, bg="#04000C")
        tv_cv.create_window((0, 0), window=tv_rows_f, anchor="nw")

        # Grade colours reused from the rhythm-game leaderboard for consistency
        tv_rank_colors = {
            "S": "#FFD700", "A": "#00FF99",
            "B": "#00E5FF", "C": "#FF8800", "D": "#FF3385",
        }

        # Grade thresholds that mirror _tv_end() — applied per-row so the
        # leaderboard is self-consistent even without a stored grade column.
        def _trivia_grade(score, total):
            """Derive a letter grade from correct / total the same way _tv_end() does."""
            pct = int(score / max(total, 1) * 100)
            if score == total:   return "S"
            if pct >= 80:        return "A"
            if pct >= 60:        return "B"
            if pct >= 40:        return "C"
            return "D"

        # Current logged-in username for row highlighting — None for guests
        _logged_user = session.username if not session.is_guest else None

        # Populate table with top 50 trivia scores from database
        tv_scores = db.load_trivia_scores(50)
        for i, s in enumerate(tv_scores, 1):
            score_val = s.get("score", 0)
            total_val = s.get("total", 12)
            pct_val   = int(score_val / max(total_val, 1) * 100)
            grade_val = _trivia_grade(score_val, total_val)
            grade_col = tv_rank_colors.get(grade_val, "#CCCCCC")

            # Highlight the logged-in player's row — same treatment as rhythm leaderboard
            is_me   = (_logged_user and
                       s.get("player_name", "").lower() == _logged_user.lower())
            row_bg  = ("#1a1200" if is_me else
                       "#0a0020" if i % 2 == 0 else "#060014")
            row     = tk.Frame(tv_rows_f, bg=row_bg,
                                highlightbackground="#FFD700" if is_me else row_bg,
                                highlightthickness=1 if is_me else 0)
            row.pack(fill="x", pady=1)

            # Star prefix on the player-name cell for the logged-in user
            name_txt = (f"★ {s.get('player_name', '?')}" if is_me
                        else s.get("player_name", "?"))
            name_col = "#FFD700" if is_me else grade_col

            # time_taken stored as float seconds; format to 1 decimal, cap display
            time_val  = s.get("time_taken", 0.0)
            time_disp = f"{time_val:.1f}s" if time_val > 0 else "—"

            tv_cells = [
                (str(i),               4,  "#FFD700" if is_me else "#888888"),
                (name_txt[:16],       16,  name_col),
                (str(score_val),       9,  "#00FF99"),
                (str(total_val),       7,  "#AAAAAA"),
                (f"{pct_val}%",        7,  "#00E5FF"),
                (time_disp,            9,  "#FF8800"),
                (grade_val,            7,  grade_col),
                (s.get("played_at", "")[:10], 11, "#666666"),
            ]
            for txt, w_, fc in tv_cells:
                tk.Label(row, text=txt, bg=row_bg, fg=fc,
                         font=(UI_FONT, 10, "bold" if is_me else "normal"),
                         width=w_, anchor="center").pack(side="left", padx=2, pady=3)

        if not tv_scores:
            tk.Label(tv_rows_f,
                     text="  No trivia scores yet — be the first ACE Scholar!",
                     bg="#04000C", fg="#666666", font=(UI_FONT, 11)).pack(pady=20)

        tv_rows_f.update_idletasks()
        tv_cv.config(scrollregion=tv_cv.bbox("all"))

        # ── Logged-in player's personal trivia best (below the table) ─
        if not session.is_guest:
            pb_tv = db.load_trivia_scores_for_account(session.account_id, 1)
            pb_f2 = tk.Frame(outer, bg="#0a0018",
                             highlightbackground="#FFD700", highlightthickness=1)
            pb_f2.pack(fill="x", padx=20, pady=(4, 0))
            tk.Label(pb_f2, text=f"✦  YOUR BEST TRIVIA — {session.username.upper()}",
                     bg="#0a0018", fg="#FFD700",
                     font=(UI_FONT, 10, "bold")).pack(side="left", padx=(12, 20), pady=6)
            if pb_tv:
                pb2       = pb_tv[0]
                pb2_score = pb2.get("score", 0)
                pb2_total = pb2.get("total", 12)
                pb2_pct   = int(pb2_score / max(pb2_total, 1) * 100)
                pb2_grade = _trivia_grade(pb2_score, pb2_total)
                pb2_time  = pb2.get("time_taken", 0.0)
                pb2_tdisp = f"{pb2_time:.1f}s" if pb2_time > 0 else "—"
                for lbl3, val3, vc3 in [
                    ("CORRECT", str(pb2_score),          "#00FF99"),
                    ("TOTAL",   str(pb2_total),           "#AAAAAA"),
                    ("ACC",     f"{pb2_pct}%",            "#00E5FF"),
                    ("TIME",    pb2_tdisp,                "#FF8800"),
                    ("GRADE",   pb2_grade,                tv_rank_colors.get(pb2_grade, "#888")),
                    ("DATE",    pb2.get("played_at", "")[:10], "#666666"),
                ]:
                    tk.Label(pb_f2, text=f"{lbl3}: {val3}", bg="#0a0018", fg=vc3,
                             font=(UI_FONT, 9, "bold")).pack(side="left", padx=10, pady=6)
            else:
                tk.Label(pb_f2, text="No trivia scores saved yet — play Aces Trivia!",
                         bg="#0a0018", fg="#666666",
                         font=(UI_FONT, 9)).pack(side="left", padx=10, pady=6)

        # ── Bottom navigation buttons ────────────────────────────────
        bf2 = tk.Frame(outer, bg=BG_COL); bf2.pack(pady=(8, 16))
        # Back to rhythm leaderboard — same pixel-style button
        self._pixel_btn(bf2, "◆  RHYTHM RANKINGS", BTN_COLORS[1],
                        lambda: self._fade_to(self._show_rankings), width=210).pack(side="left", padx=10)
        # Back to main stage
        self._pixel_btn(bf2, "⌂  MAIN STAGE", BTN_COLORS[2],
                        lambda: self._fade_to(self._show_title), width=180).pack(side="left", padx=10)

    # ═══════════════════════════════════════════════════════════════
    #  TRIVIA
    # ═══════════════════════════════════════════════════════════════
    def _show_trivia_confirmation(self):
        """
        Intro/confirmation screen before the trivia game begins.
        Entirely canvas-drawn — zero tk widget overlay — so the live
        star/spotlight animation shows through every pixel of the screen.

        _draw_trivia_confirm_canvas() renders the full UI on self.cv each
        frame: constellation lines, animated title, frosted card panel,
        stat badges, instruction text, and pixel buttons.
        Hit-testing for the two buttons lives in _on_canvas_press/release.
        """
        self._clear_widgets()
        audio.play_menu_bgm()

        # Button hit-rect storage — recalculated each frame in _draw_trivia_confirm_canvas
        self._tc_btn_rects = {}    # "start" | "back"  →  (x1, y1, x2, y2)
        self._tc_btn_press = None  # currently depressed button key

        self.screen = "trivia_confirm"

    # ── Trivia confirm — fully canvas-drawn, runs every frame ────────────
    def _draw_trivia_confirm_canvas(self, cv):
        """
        Per-frame canvas render for the trivia confirmation screen.
        No tk widgets — everything is drawn directly on self.cv so the
        star/spotlight background is always visible.

        Layers (bottom → top):
          1. Colorful smoke / stage fog rising from the screen bottom
          2. Extra-bright star pass
          3. Colorful per-member-color rings encircling the title
          4. Clean rainbow title text (shadow + crisp cycling color)
          5. Twinkling star accents below the title
          6. Frosted card panel with cycling border
          7. Stat badges, instruction lines, pixel buttons inside card
        """
        cx = _W // 2
        t  = self.t

       

        # ── 2. Extra-bright star pass ────────────────────────────────────
        for s in self.stars:
            x  = s["nx"] * _W
            y  = s["ny"] * _H
            ph = s["ph"]
            a  = 0.75 + 0.25 * abs(math.sin(t * 0.7 + ph))
            r  = s["r"] * (1.2 + 0.5 * a)
            cv.create_oval(x - r, y - r, x + r, y + r,
                           fill=dim("#FFFFFF", a), outline="")

        # ── Shared colour cycle ──────────────────────────────────────────
        phase = (t * 0.50) % 1.0
        ci0   = int(phase * len(MEMBER_COLORS)) % len(MEMBER_COLORS)
        ci1   = (ci0 + 1) % len(MEMBER_COLORS)
        cfrac = (phase * len(MEMBER_COLORS)) - ci0
        col   = blend(MEMBER_COLORS[ci0], MEMBER_COLORS[ci1], cfrac)
        pulse = 0.88 + 0.12 * math.sin(t * 2.4)

        # ── Card geometry ────────────────────────────────────────────────
        CARD_W  = min(580, int(_W * 0.70))
        CARD_H  = min(340, int(_H * 0.54))
        card_x1 = cx - CARD_W // 2
        card_y1 = (_H - CARD_H) // 2 + int(_H * 0.05)
        card_x2 = card_x1 + CARD_W
        card_y2 = card_y1 + CARD_H

        # Title floats above the card
        TITLE_Y = card_y1 - int(_H * 0.09)

        # ── 3. Colorful rings around the title ───────────────────────────
        # Each ring uses its own MEMBER_COLOR so they form a rainbow halo.
        # Rings breathe at slightly different rates for a living feel.
        N_RINGS = len(MEMBER_COLORS)   # one ring per member
        for ri in range(N_RINGS):
            rc      = MEMBER_COLORS[ri]
            # Spread the rings from tight to wide; inner rings are brighter
            r_frac  = 0.12 + ri * 0.032
            hr      = int(_W * r_frac) + int(5 * math.sin(t * 1.2 + ri * 0.7))
            # Alternating breath speeds keep rings from moving in lockstep
            ra      = (0.28 - ri * 0.030) * (0.65 + 0.35 * math.sin(t * 1.5 + ri * 1.3))
            ra      = max(0.04, ra)
            cv.create_oval(cx - hr,      TITLE_Y - hr // 4,
                           cx + hr,      TITLE_Y + hr // 4,
                           outline=dim(rc, ra), fill="", width=2)

        # ── 4. Clean rainbow title ────────────────────────────────────────
        title_sz  = max(44, int(_H * 0.078))
        TITLE_TXT = "✦  BGYO  ACES  TRIVIA  ✦"

        # Shadow — single small offset, dark blue tint
        cv.create_text(cx + 2, TITLE_Y + 2, text=TITLE_TXT,
                       fill=dim("#000033", 0.90),
                       font=(UI_FONT, title_sz, "bold"), anchor="center")
        # Crisp main text — smooth cycling color at full brightness
        cv.create_text(cx, TITLE_Y, text=TITLE_TXT,
                       fill=dim(col, pulse),
                       font=(UI_FONT, title_sz, "bold"), anchor="center")

        # ── 5. Twinkling accent stars below the title ────────────────────
        for si in range(5):
            sp_x = cx + (si - 2) * int(CARD_W * 0.22)
            sp_y = TITLE_Y + int(_H * 0.038) + int(5 * math.sin(t * 1.8 + si * 1.2))
            sp_c = MEMBER_COLORS[si % len(MEMBER_COLORS)]
            sp_a = 0.60 + 0.40 * abs(math.sin(t * 2.2 + si))
            sp_r = 2.5 + 1.5 * abs(math.sin(t * 3.1 + si))
            cv.create_oval(sp_x - sp_r, sp_y - sp_r,
                           sp_x + sp_r, sp_y + sp_r,
                           fill=dim(sp_c, sp_a), outline="")

        # ── 6. Card panel ─────────────────────────────────────────────────
        cv.create_rectangle(card_x1 - 6, card_y1 - 6,
                            card_x2 + 6, card_y2 + 6,
                            fill="", outline=dim(col, pulse * 0.18), width=3)
        cv.create_rectangle(card_x1, card_y1, card_x2, card_y2,
                            fill="#0D0028", outline="")
        cv.create_rectangle(card_x1, card_y1, card_x2, card_y2,
                            fill="", outline=dim(col, pulse * 0.80), width=2)
        cv.create_rectangle(card_x1 + 2, card_y1 + 2, card_x2 - 2, card_y1 + 3,
                            fill=dim(col, pulse * 0.50), outline="")

        # ── 7. Content layout inside card ────────────────────────────────
        INNER_PAD  = 20
        inner_top  = card_y1 + INNER_PAD
        inner_bot  = card_y2 - INNER_PAD

        BADGE_H    = 52
        BTN_H2     = 42
        badges_top = inner_top
        btns_bot   = inner_bot
        btns_top   = btns_bot - BTN_H2
        lines_bot  = btns_top - 10
        lines_top  = badges_top + BADGE_H + 12

        # Stat badges
        BADGE_DEFS = [
            ("QUESTIONS",  "12",    "#FFD700"),
            ("TIME LIMIT", "15 s",  "#00E5FF"),
            ("TOPIC",      "BGYO",  "#FF3385"),
        ]
        BADGE_W  = 116; BADGE_GAP = 14
        total_bw = len(BADGE_DEFS) * BADGE_W + (len(BADGE_DEFS) - 1) * BADGE_GAP
        bx0      = cx - total_bw // 2

        for bi, (blbl, bval, bcol) in enumerate(BADGE_DEFS):
            bx1 = bx0 + bi * (BADGE_W + BADGE_GAP)
            bx2 = bx1 + BADGE_W
            by1 = badges_top; by2 = badges_top + BADGE_H
            bcx = (bx1 + bx2) // 2
            cv.create_rectangle(bx1, by1, bx2, by2, fill="#0a001e", outline="")
            cv.create_rectangle(bx1, by1, bx2, by2, fill="", outline=bcol, width=2)
            cv.create_text(bcx, by1 + 14, text=blbl,
                           fill="#8888AA", font=(UI_FONT, 8, "bold"), anchor="center")
            cv.create_text(bcx, by2 - 14, text=bval,
                           fill=bcol, font=(MONO_FONT, 16, "bold"), anchor="center")

        # Instruction lines
        LINES = [
            ("Test your BGYO knowledge across Members, Songs, History & more!",
             "#CCCCCC", 11, "normal"),
            ("Select your answer, then press  ✔ CONFIRM  to lock it in.",
             "#00E5FF", 11, "normal"),
            ("Answer correctly and quickly — faster time breaks leaderboard ties!",
             "#FF3385", 10, "bold"),
        ]
        n_lines    = len(LINES)
        lines_mid  = (lines_top + lines_bot) // 2
        line_gap   = min(22, (lines_bot - lines_top) // n_lines)
        line_start = lines_mid - (n_lines - 1) * line_gap // 2

        for li, (ltxt, lcol, lsz, lweight) in enumerate(LINES):
            cv.create_text(cx, line_start + li * line_gap,
                           text=ltxt, fill=lcol,
                           font=(UI_FONT, lsz, lweight),
                           anchor="center", width=CARD_W - 48)

        # Pixel-style buttons
        BTN_W2 = 188; BTN_GAP2 = 16; HL = 4; SH = 4
        total_btn_w = BTN_W2 * 2 + BTN_GAP2
        sx1 = cx - total_btn_w // 2
        sx2 = sx1 + BTN_W2
        bk1 = sx2 + BTN_GAP2
        bk2 = bk1 + BTN_W2
        btn_y1 = btns_top
        btn_y2 = btns_top + BTN_H2

        self._tc_btn_rects = {
            "start": (sx1, btn_y1, sx2, btn_y2),
            "back":  (bk1, btn_y1, bk2, btn_y2),
        }

        for key, bx1, bx2, bcol, blabel in [
            ("start", sx1, sx2, BTN_COLORS[3], "▶  START GAME"),
            ("back",  bk1, bk2, BTN_COLORS[1], "◄  MAIN STAGE"),
        ]:
            pressed = (getattr(self, "_tc_btn_press", None) == key)
            ox, oy  = (2, 2) if pressed else (0, 0)
            bright  = lighten(bcol, 0.55)
            dark    = dim(bcol, 0.40)
            cv.create_rectangle(bx1+ox, btn_y1+oy, bx2+ox, btn_y2+oy,
                                fill=bcol, outline="")
            cv.create_rectangle(bx1+ox, btn_y1+oy, bx2+ox, btn_y1+oy+HL,
                                fill=bright, outline="")
            cv.create_rectangle(bx1+ox, btn_y2+oy-SH, bx2+ox, btn_y2+oy,
                                fill=dark, outline="")
            cv.create_text((bx1+bx2)//2+ox, (btn_y1+btn_y2)//2+oy,
                           text=blabel, fill="#04000C",
                           font=(UI_FONT, 13, "bold"), anchor="center")

    def _start_trivia(self):
        """Start the actual trivia game (called after confirmation)."""
        self._clear_widgets()
        # Don't play BGM again - already playing from confirmation screen
        qs = list(TRIVIA); random.shuffle(qs)
        self._tv_questions   = qs[:12]
        self._tv_idx         = 0
        self._tv_score       = 0
        self._tv_answered    = False
        self._tv_pending     = None
        self._tv_next_time   = None
        self._tv_timer_start = time.time()
        self._tv_time_limit  = 15.0
        self._tv_time_taken  = 0.0   # reset cumulative answer-time for new session
        self.screen = "trivia"
        self._build_trivia_ui()
        self._render_trivia()

    def _build_trivia_ui(self):
        self._clear_widgets()
        BG = "#04000C"; CARD_BG = "#080018"; PX = 5
        T_COLORS = ["#FFD700", "#FF3385", "#00E5FF", "#FF8800"]
        T_LABELS = ["A", "B", "C", "D"]

        outer = tk.Frame(self.root, bg=BG,
                         highlightbackground="#FFD700", highlightthickness=2)
        outer.place(x=0, y=0, width=_W, height=_H)

        def px_rect(cv, x1, y1, x2, y2, bg_c, border_c):
            dark  = dim(border_c, 0.28); light = lighten(border_c, 0.55)
            cv.create_rectangle(x1+PX, y1+PX, x2+PX, y2+PX, fill=dark,  outline="")
            cv.create_rectangle(x1, y1, x2, y2, fill=bg_c, outline="")
            cv.create_rectangle(x1, y1, x2, y1+PX,  fill=light, outline="")
            cv.create_rectangle(x1, y1, x1+PX, y2,  fill=light, outline="")
            cv.create_rectangle(x1, y2-PX, x2, y2,  fill=dark,  outline="")
            cv.create_rectangle(x2-PX, y1, x2, y2,  fill=dark,  outline="")

        HDR_H  = 58
        hdr_cv = tk.Canvas(outer, width=_W-24, height=HDR_H, bg=BG, highlightthickness=0)
        hdr_cv.place(x=12, y=12)
        px_rect(hdr_cv, 0, 0, _W-24, HDR_H, CARD_BG, "#FFD700")
        # ENHANCED v18.1: Centered title
        hdr_cv.create_text((_W-24)//2, HDR_H//2, text="✦  BGYO  ACES  TRIVIA  ✦",
                            fill="#FFD700", font=(UI_FONT, 17, "bold"), anchor="center")

        self._tv_qnum_var  = tk.StringVar(value="Q 1 / 12")
        self._tv_score_var = tk.StringVar(value="SCORE  0 / 0")
        # Center-aligned score and question counter in the header
        tk.Label(outer, textvariable=self._tv_score_var, bg=CARD_BG, fg="#00E5FF",
                 font=(UI_FONT, 12, "bold")).place(relx=0.75, y=12+HDR_H//2, anchor="center")
        tk.Label(outer, textvariable=self._tv_qnum_var, bg=CARD_BG, fg="#00FF99",
                 font=(UI_FONT, 12, "bold")).place(relx=0.25, y=12+HDR_H//2, anchor="center")

        self._tv_prog      = tk.Canvas(outer, width=_W-24, height=12, bg="#111122", highlightthickness=0)
        self._tv_prog.place(x=12, y=12+HDR_H+8)
        self._tv_timer_bar = tk.Canvas(outer, width=_W-24, height=8, bg="#050010", highlightthickness=0)
        self._tv_timer_bar.place(x=12, y=12+HDR_H+24)

        strip_y = 12+HDR_H+38
        seg_w   = (_W-24)//len(MEMBER_COLORS)
        for i, mc in enumerate(MEMBER_COLORS):
            tk.Frame(outer, bg=mc).place(x=12+i*seg_w, y=strip_y, width=seg_w, height=6)

        QC_Y = strip_y+14; QC_H = 108
        QC_BG = "#D8D8E8"; QC_HL = "#F0F0F8"
        qc_cv = tk.Canvas(outer, width=_W-24, height=QC_H, bg=BG, highlightthickness=0)
        qc_cv.place(x=12, y=QC_Y)
        px_rect(qc_cv, 0, 0, _W-24, QC_H, QC_BG, "#332266")
        qc_cv.create_rectangle(PX, PX, _W-24-PX, PX*3, fill=QC_HL, outline="")

        self._tv_cat_var = tk.StringVar(value="❓  GENERAL")
        tk.Label(outer, textvariable=self._tv_cat_var, bg="#1A0040", fg="#FF8800",
                 font=(UI_FONT, 9, "bold"), padx=8, pady=2).place(relx=0.5, y=QC_Y+14, anchor="center")
        self._tv_q_var = tk.StringVar()
        # Dark text on light card background for readability, centered
        tk.Label(outer, textvariable=self._tv_q_var, bg=QC_BG, fg="#111111",
                 font=(UI_FONT, 13, "bold"),
                 wraplength=_W-80, justify="center").place(relx=0.5, y=QC_Y+QC_H//2+14, anchor="center")

        BTN_Y = QC_Y+QC_H+18; BTN_H = 78; GAP = 12; BTN_W = (_W-24-GAP)//2
        self._tv_btn_frame = tk.Frame(outer, bg=BG)
        self._tv_btn_frame.place(x=12, y=BTN_Y, width=_W-24, height=(BTN_H+GAP)*2)
        self._tv_btns = []

        for i in range(4):
            r, c  = divmod(i, 2)
            bx    = c * (BTN_W+GAP); by = r * (BTN_H+GAP)
            bc    = T_COLORS[i]
            dark_bc  = dim(bc, 0.28); light_bc = lighten(bc, 0.55)
            tint_bg  = "#D8D8E8"; inner_hl = "#F0F0F8"; BADGE_W = 52

            cell = tk.Frame(self._tv_btn_frame, bg=BG)
            cell.place(x=bx, y=by, width=BTN_W, height=BTN_H)
            tk.Frame(cell, bg=dim(bc, 0.22)).place(x=PX, y=PX, width=BTN_W, height=BTN_H)
            body = tk.Frame(cell, bg=tint_bg)
            body.place(x=0, y=0, width=BTN_W, height=BTN_H)
            tk.Frame(body, bg=light_bc).place(x=0, y=0, width=BTN_W, height=PX)
            tk.Frame(body, bg=light_bc).place(x=0, y=0, width=PX, height=BTN_H)
            tk.Frame(body, bg=dark_bc).place(x=0, y=BTN_H-PX, width=BTN_W, height=PX)
            tk.Frame(body, bg=dark_bc).place(x=BTN_W-PX, y=0, width=PX, height=BTN_H)

            badge = tk.Frame(body, bg=bc)
            badge.place(x=PX, y=PX, width=BADGE_W, height=BTN_H-PX*2)
            tk.Frame(badge, bg=light_bc).place(x=0, y=0, width=BADGE_W, height=PX)
            tk.Frame(badge, bg=light_bc).place(x=0, y=0, width=PX, height=BTN_H-PX*2)
            tk.Frame(badge, bg=dark_bc).place(x=0, y=BTN_H-PX*3, width=BADGE_W, height=PX)
            tk.Frame(badge, bg=dark_bc).place(x=BADGE_W-PX, y=0, width=PX, height=BTN_H-PX*2)
            # Dark label text for readability on colored badge
            tk.Label(badge, text=T_LABELS[i], bg=bc, fg="#000000",
                     font=(UI_FONT, 20, "bold")).place(relx=0.5, rely=0.5, anchor="center")

            inner_x = BADGE_W+PX*2; inner_w = BTN_W-BADGE_W-PX*3
            tk.Frame(body, bg=inner_hl).place(x=inner_x, y=PX, width=inner_w, height=PX*2)
            # Answer selection button — dark text on light background
            btn = tk.Button(body, text="", wraplength=inner_w-16,
                            bg=tint_bg, fg="#111111",
                            activebackground=lighten(bc, 0.70), activeforeground="#000000",
                            disabledforeground="#888899",
                            font=(UI_FONT, 11, "bold"), relief="flat", anchor="w",
                            padx=10, cursor="hand2", highlightthickness=0,
                            command=lambda idx=i: self._tv_select(idx))
            btn.place(x=inner_x, y=PX*3, width=inner_w, height=BTN_H-PX*4)
            self._tv_btns.append((btn, badge, body, bc, light_bc, dark_bc, tint_bg))

        # Feedback label — centered
        self._tv_fb_var = tk.StringVar()
        self._tv_fb_lbl = tk.Label(outer, textvariable=self._tv_fb_var, bg=BG, fg="#00FF99",
                                   font=(UI_FONT, 13, "bold"), justify="center")
        self._tv_fb_lbl.place(relx=0.5, y=_H-90, anchor="center")

        # Waiting transition text — centered
        self._tv_wait_var = tk.StringVar()
        self._tv_wait_lbl = tk.Label(outer, textvariable=self._tv_wait_var, bg=BG, fg="#FFD700",
                                     font=(UI_FONT, 14, "bold"), justify="center")
        self._tv_wait_lbl.place(relx=0.5, y=_H//2, anchor="center")

        # ── CONFIRM ANSWER button (hidden until a choice is selected) ──
        _ca_col = BTN_COLORS[3]  # green
        _ca_w, _ca_h = 300, 48; HL_CA = 4; SH_CA = 4
        self._tv_confirm_cv = tk.Canvas(outer, width=_ca_w, height=_ca_h,
                                        bg=BG, highlightthickness=0, cursor="hand2")

        def _draw_confirm(pressed=False):
            self._tv_confirm_cv.delete("all")
            ox, oy = (2, 2) if pressed else (0, 0)
            bright = lighten(_ca_col, 0.55); dark = dim(_ca_col, 0.40)
            self._tv_confirm_cv.create_rectangle(ox, oy, _ca_w+ox, _ca_h+oy,
                                                  fill=_ca_col, outline="")
            self._tv_confirm_cv.create_rectangle(ox, oy, _ca_w+ox, oy+HL_CA,
                                                  fill=bright, outline="")
            self._tv_confirm_cv.create_rectangle(ox, _ca_h+oy-SH_CA, _ca_w+ox, _ca_h+oy,
                                                  fill=dark, outline="")
            self._tv_confirm_cv.create_text(_ca_w//2+ox, _ca_h//2+oy,
                                             text="✔  CONFIRM ANSWER",
                                             fill="#000000",
                                             font=(UI_FONT, 13, "bold"),
                                             anchor="center")
        _ca_pressed = [False]
        def _ca_press(e):  _ca_pressed[0]=True;  _draw_confirm(True)
        def _ca_release(e):
            _ca_pressed[0]=False; _draw_confirm(False)
            self._play_click(); self.root.after(40, self._tv_confirm_answer)
        def _ca_leave(e):
            if _ca_pressed[0]: _ca_pressed[0]=False; _draw_confirm(False)
        self._tv_confirm_cv.bind("<ButtonPress-1>",   _ca_press)
        self._tv_confirm_cv.bind("<ButtonRelease-1>", _ca_release)
        self._tv_confirm_cv.bind("<Leave>",           _ca_leave)
        _draw_confirm()

        # Store as _tv_confirm_btn alias for compatibility with show/hide calls
        self._tv_confirm_btn = self._tv_confirm_cv

        # Placed but hidden initially
        self._tv_confirm_cv.place(relx=0.5, y=_H-55, anchor="center")
        self._tv_confirm_cv.place_forget()

        # Back button — now shows confirmation dialog before leaving
        back_col    = "#FFD700"
        back_bright = lighten(back_col, 0.55); back_dark = dim(back_col, 0.40)
        back_w, back_h = 280, 42; HL = 4; SH = 4
        back_cv = tk.Canvas(outer, width=back_w, height=back_h, bg=BG, highlightthickness=0)
        back_cv.place(x=_W//2-back_w//2, y=_H-52)
        back_cv.create_rectangle(0, 0, back_w, back_h, fill=back_col, outline="")
        back_cv.create_rectangle(0, 0, back_w, HL, fill=back_bright, outline="")
        back_cv.create_rectangle(0, back_h-SH, back_w, back_h, fill=back_dark, outline="")
        back_cv.create_text(back_w//2, back_h//2, text="◀  BACK TO MAIN STAGE",
                            fill="#0a0a1a", font=(UI_FONT, 13, "bold"))

        def _confirm_back():
            """Show a confirmation modal before leaving trivia mid-game."""
            self._play_click()
            # Build a small modal on top of the trivia screen
            conf = tk.Toplevel(self.root)
            conf.title("Leave Trivia?")
            conf.configure(bg="#04000C")
            conf.resizable(False, False)
            conf.grab_set()
            cw, ch = 420, 200
            cx_pos = self.root.winfo_rootx() + (_W - cw) // 2
            cy_pos = self.root.winfo_rooty() + (_H - ch) // 2
            conf.geometry(f"{cw}x{ch}+{cx_pos}+{cy_pos}")
            tk.Label(conf, text="⚠  LEAVE TRIVIA?", bg="#04000C", fg="#FFD700",
                     font=(UI_FONT, 16, "bold")).pack(pady=(24, 6))
            tk.Label(conf, text="Your current progress will be lost.",
                     bg="#04000C", fg="#00E5FF", font=(UI_FONT, 11),
                     justify="center").pack(pady=(0, 18))
            btn_row = tk.Frame(conf, bg="#04000C"); btn_row.pack()
            def _yes():
                conf.destroy()
                self._fade_to(self._show_title)
            def _no():
                conf.destroy()
            self._pixel_btn(btn_row, "✔  YES, LEAVE", BTN_COLORS[1], _yes, width=160).pack(side="left", padx=10)
            self._pixel_btn(btn_row, "✖  NO, STAY",  BTN_COLORS[3], _no,  width=160).pack(side="left", padx=10)

        back_cv.bind("<Button-1>", lambda e: _confirm_back())
        back_cv.config(cursor="hand2")
        self._tv_back_cv = back_cv

    def _render_trivia(self):
        if self._tv_idx >= len(self._tv_questions):
            self._tv_end(); return
        q = self._tv_questions[self._tv_idx]
        self._tv_answered    = False
        self._tv_pending     = None
        self._tv_next_time   = None
        self._tv_timer_start = time.time()

        # ENHANCED v18.1: Clear waiting text
        self._tv_waiting = False
        self._tv_wait_var.set("")

        prog = self._tv_idx / len(self._tv_questions)
        pw   = _W-24
        self._tv_prog.delete("all")
        self._tv_prog.create_rectangle(0, 0, pw, 12, fill="#1a1a33", outline="")
        seg = max(1, int(pw*prog)//len(MEMBER_COLORS))
        for i, mc in enumerate(MEMBER_COLORS):
            sx = i * seg; ex = sx + seg if i < len(MEMBER_COLORS)-1 else int(pw*prog)
            if sx < int(pw*prog):
                self._tv_prog.create_rectangle(sx, 0, min(ex, int(pw*prog)), 12, fill=mc, outline="")

        self._tv_qnum_var.set(f"Q {self._tv_idx+1} / {len(self._tv_questions)}")
        self._tv_score_var.set(f"SCORE  {self._tv_score} / {self._tv_idx}")
        cats = {"members":"👤  MEMBERS","songs":"🎵  SONGS","history":"📖  HISTORY","general":"❓  GENERAL"}
        self._tv_cat_var.set(cats.get(q.get("cat","general"), "❓  GENERAL"))
        self._tv_q_var.set(q["q"])

        for i, (btn, badge, body, bc, light_bc, dark_bc, tint_bg) in enumerate(self._tv_btns):
            btn.config(text=f"  {q['opts'][i]}", bg=tint_bg, fg="#111111", state="normal",
                       relief="flat", highlightthickness=0)
            badge.config(bg=bc)
            body.config(bg=tint_bg)
            for w in badge.winfo_children():
                if isinstance(w, tk.Label): w.config(bg=bc, fg="#000000")

        self._tv_fb_var.set("")
        # Hide confirm button until a choice is made
        try: self._tv_confirm_btn.place_forget()
        except Exception: pass
        # Show back button
        try: self._tv_back_cv.place(x=_W//2-140, y=_H-52)
        except Exception: pass
        self._update_timer_bar()

    def _update_timer_bar(self):
        if not hasattr(self, "_tv_timer_bar") or self.screen != "trivia": return
        if self._tv_answered: return
        elapsed = time.time() - self._tv_timer_start
        frac    = max(0.0, 1.0 - elapsed / self._tv_time_limit)
        pw      = _W-24; bar_w = int(pw * frac)
        self._tv_timer_bar.delete("all")
        self._tv_timer_bar.create_rectangle(0, 0, pw, 8, fill="#111111", outline="")
        if bar_w > 0:
            col = "#00FF99" if frac > 0.5 else "#FF8800" if frac > 0.25 else "#FF3385"
            self._tv_timer_bar.create_rectangle(0, 0, bar_w, 8, fill=col, outline="")
        if frac <= 0 and not self._tv_answered:
            self._tv_answer(-1)

    def _tv_select(self, chosen: int):
        """Highlight the selected option and show the Confirm button — don't lock in yet."""
        if self._tv_answered: return
        self._tv_pending = chosen
        q = self._tv_questions[self._tv_idx]
        # Reset all buttons to normal appearance first
        for i, (btn, badge, body, bc, light_bc, dark_bc, tint_bg) in enumerate(self._tv_btns):
            btn.config(bg=tint_bg, fg="#111111", relief="flat", highlightthickness=0)
            body.config(bg=tint_bg)
        # Highlight chosen button with a border
        sel_btn, sel_badge, sel_body, sel_bc, *_ = self._tv_btns[chosen]
        sel_body.config(bg=lighten(sel_bc, 0.80))
        sel_btn.config(bg=lighten(sel_bc, 0.80), highlightbackground=sel_bc,
                       highlightthickness=3)
        self._tv_fb_var.set(f"Selected: {chr(65+chosen)}  —  Press Confirm to lock in!")
        self._tv_fb_lbl.config(fg="#FF8800")
        # Show Confirm button, hide Back
        try: self._tv_back_cv.place_forget()
        except Exception: pass
        try: self._tv_confirm_btn.place(relx=0.5, y=_H-55, anchor="center")
        except Exception: pass

    def _tv_confirm_answer(self):
        """Lock in the pending answer."""
        if self._tv_answered or self._tv_pending is None: return
        self._tv_answer(self._tv_pending)

    def _tv_answer(self, chosen: int):
        if self._tv_answered: return
        self._tv_answered = True
        q       = self._tv_questions[self._tv_idx]
        correct = q["a"]
        # Hide confirm, show back
        try: self._tv_confirm_btn.place_forget()
        except Exception: pass
        try: self._tv_back_cv.place(x=_W//2-140, y=_H-52)
        except Exception: pass
        # Disable all buttons
        for btn, *_ in self._tv_btns:
            btn["state"] = "disabled"
        # Reveal correct answer in green
        c_btn, c_badge, c_body, c_bc, *_ = self._tv_btns[correct]
        c_btn.config(bg="#003322", fg="#00FF99"); c_body.config(bg="#003322")
        c_badge.config(bg="#00FF99")
        for w in c_badge.winfo_children():
            if isinstance(w, tk.Label): w.config(bg="#00FF99", fg="#003322")
        # Feedback
        # Accumulate the seconds used on this question — capped at the time
        # limit so a timeout doesn't penalise the player more than the max.
        elapsed_this_q = min(time.time() - self._tv_timer_start, self._tv_time_limit)
        self._tv_time_taken += elapsed_this_q

        if chosen == correct:
            self._tv_score += 1
            self._tv_fb_var.set("✦   CORRECT!   You ARE an ACE!")
            self._tv_fb_lbl.config(fg="#00FF99")
        elif chosen == -1:
            self._tv_fb_var.set("⏱   TIME'S UP!   The answer was highlighted!")
            self._tv_fb_lbl.config(fg="#FF8800")
        else:
            w_btn, w_badge, w_body, *_ = self._tv_btns[chosen]
            w_btn.config(bg="#2a0015", fg="#FF3385"); w_body.config(bg="#2a0015")
            w_badge.config(bg="#FF3385")
            for ww in w_badge.winfo_children():
                if isinstance(ww, tk.Label): ww.config(bg="#FF3385", fg="#ffffff")
            self._tv_fb_var.set("✗   Not quite!   Keep stanning BGYO!")
            self._tv_fb_lbl.config(fg="#FF3385")
        self._tv_score_var.set(f"SCORE  {self._tv_score} / {self._tv_idx+1}")
        
        # ENHANCED v18.1: Show waiting text
        self._tv_waiting = True
        self._tv_wait_var.set("⏳ Please wait for the next question...")
        
        self._tv_next_time = time.time() + 2.2

    def _tv_end(self):
        """Show results and prompt for name before saving."""
        self._clear_widgets()
        n   = len(self._tv_questions); s = self._tv_score; pct = int(s / n * 100)
        if   s == n:    grade, gc, msg = "S", "#FFD700", "★  PERFECT ACE!  You know BGYO inside out!"
        elif pct >= 80: grade, gc, msg = "A", "#00FF99", "✦  Amazing!  You're a true ACE!"
        elif pct >= 60: grade, gc, msg = "B", "#00E5FF", "◎  Good job!  Keep stanning!"
        elif pct >= 40: grade, gc, msg = "C", "#FF8800", "◌  Not bad!  Level up your BGYO knowledge!"
        else:           grade, gc, msg = "D", "#FF3385", "✗  Keep stanning BGYO and try again!"

        outer = tk.Frame(self.root, bg="#04000C",
                         highlightbackground=gc, highlightthickness=3)
        outer.place(x=0, y=0, width=_W, height=_H)

        tk.Label(outer, text=grade, bg="#04000C", fg=gc,
                 font=(TITLE_FONT, 96, "bold")).pack(pady=(50, 0))
        tk.Label(outer, text=f"{s}  /  {n}  CORRECT   ·   {pct}%",
                 bg="#04000C", fg="#FFFFFF", font=(TITLE_FONT, 18, "bold")).pack(pady=(4, 8))
        tk.Label(outer, text=msg, bg="#04000C", fg=gc,
                 font=(UI_FONT, 14, "bold")).pack(pady=(0, 20))

        # Total time taken this session — shown on the results screen so
        # players can see their speed before deciding to save the score.
        total_time_str = f"{self._tv_time_taken:.1f}s"

        strip = tk.Frame(outer, bg="#04000C"); strip.pack()
        for lbl, val, vc in [
            ("CORRECT",  str(s),           "#00FF99"),
            ("WRONG",    str(n-s),         "#FF3385"),
            ("ACCURACY", f"{pct}%",        "#00E5FF"),
            ("TIME",     total_time_str,   "#FF8800"),   # cumulative answer time
        ]:
            cell = tk.Frame(strip, bg="#0a0020", highlightbackground=vc, highlightthickness=2)
            cell.pack(side="left", padx=10, ipadx=18, ipady=10)
            tk.Label(cell, text=lbl, bg="#0a0020", fg="#888888", font=(UI_FONT, 9, "bold")).pack()
            tk.Label(cell, text=val, bg="#0a0020", fg=vc,       font=(MONO_FONT, 20, "bold")).pack()

        # ── Score save section — logged-in users only; guests cannot save ─
        nf = tk.Frame(outer, bg="#04000C"); nf.pack(pady=(22, 8))

        if session.is_guest:
            # Guest accounts: inform that saving is not available
            tk.Label(nf, text="GUEST ACCOUNT — SCORE NOT SAVED",
                     bg="#04000C", fg="#FF8800",
                     font=(UI_FONT, 13, "bold")).pack(pady=(0, 4))
            tk.Label(nf,
                     text="Log in or register a free account to save scores and appear on the leaderboard!",
                     bg="#04000C", fg="#888888",
                     font=(UI_FONT, 11), justify="center", wraplength=440).pack(pady=(0, 4))
        else:
            # Logged-in users: name is locked to their account username —
            # they cannot change it to prevent impersonation on the leaderboard.
            tk.Label(nf, text="SAVE YOUR TRIVIA SCORE:", bg="#04000C", fg="#FFD700",
                     font=(UI_FONT, 12, "bold")).pack(pady=(0, 6))
            name_row = tk.Frame(nf, bg="#04000C"); name_row.pack()
            name_var = tk.StringVar(value=session.username)
            # Read-only entry — name is locked to the account username
            name_e = tk.Entry(name_row, textvariable=name_var, bg="#1a0038", fg="#00E5FF",
                               insertbackground="#FFD700", font=(UI_FONT, 13, "bold"),
                               relief="flat", width=20,
                               highlightthickness=2,
                               highlightbackground="#334466",
                               highlightcolor="#FFD700",
                               state="readonly")
            name_e.pack(side="left")
            tk.Label(name_row, text="  ★ LOGGED IN", bg="#04000C", fg="#00E5FF",
                     font=(UI_FONT, 9, "bold")).pack(side="left")

            name_msg = tk.Label(nf, text="", bg="#04000C", fg="#FF3385",
                                font=(UI_FONT, 10, "bold"))
            name_msg.pack()

            def _save_trivia():
                # Always use the logged-in account username — name is not editable.
                # Pass _tv_time_taken so the leaderboard can rank equal scores by speed.
                name = session.username
                db.save_trivia_score(name, s, n,
                                     account_id=session.account_id,
                                     time_taken=self._tv_time_taken)
                name_msg.config(text="✓  Score saved!", fg="#00FF99")
                save_btn.config(state="disabled")

            save_btn = self._btn(name_row, "  SAVE  ", BTN_COLORS[0], btn_fg(0), _save_trivia)
            save_btn.pack(side="left", padx=8)

        bf = tk.Frame(outer, bg="#04000C"); bf.pack(pady=20)
        self._pixel_btn(bf, "↺  PLAY AGAIN", BTN_COLORS[0],
                        lambda: self._fade_to(self._start_trivia), width=170).pack(side="left", padx=12)
        self._pixel_btn(bf, "◄  MAIN STAGE", BTN_COLORS[1],
                        lambda: self._fade_to(self._show_title), width=170).pack(side="left", padx=12)

    # ═══════════════════════════════════════════════════════════════
    #  PROFILE SCREEN
    # ═══════════════════════════════════════════════════════════════
    def _show_profile(self):
        if session.is_guest:
            return
        self._clear_widgets(); self.screen = "profile"
        outer = tk.Frame(self.root, bg=BG_COL,
                         highlightbackground="#FFD700", highlightthickness=2)  # Stars visible through
        outer.place(relx=0.5, rely=0.5, anchor="center", width=760, height=560)

        tk.Label(outer, text="★  PLAYER PROFILE", bg="#0a0018", fg="#FFD700",
                 font=(UI_FONT, 20, "bold")).pack(pady=(22, 6))

        # Avatar colour row
        av_f = tk.Frame(outer, bg="#0a0018"); av_f.pack(pady=(0, 10))
        av_sel = tk.StringVar(value=session.avatar_col)
        tk.Label(av_f, text="AVATAR COLOUR:", bg="#0a0018", fg="#888888",
                 font=(UI_FONT, 10, "bold")).pack(side="left", padx=(0, 8))
        av_disp = tk.Label(av_f, text="   ", bg=session.avatar_col, width=4, height=1,
                           relief="flat")
        av_disp.pack(side="left", padx=(0, 8))

        def _pick_color(col):
            av_sel.set(col); av_disp.config(bg=col)
            db.update_avatar(session.account_id, col)

        for col in AVATAR_COLORS:
            tk.Button(av_f, text=" ", bg=col, width=2, height=1, relief="flat",
                      cursor="hand2",
                      command=lambda c=col: _pick_color(c)).pack(side="left", padx=2)

        # Best scores
        self._section_label(outer, f"  BEST SCORES — {session.username.upper()}")
        scores_f = tk.Frame(outer, bg="#0a0018"); scores_f.pack(fill="x", padx=20, pady=4)
        sc_scroll = tk.Scrollbar(scores_f); sc_scroll.pack(side="right", fill="y")
        sc_cv     = tk.Canvas(scores_f, bg="#0a0018", height=120, highlightthickness=0,
                              yscrollcommand=sc_scroll.set)
        sc_cv.pack(fill="both", expand=True)
        sc_scroll.config(command=sc_cv.yview)
        sc_rows = tk.Frame(sc_cv, bg="#0a0018")
        sc_cv.create_window((0, 0), window=sc_rows, anchor="nw")
        rank_colors = {"S":"#FFD700","A":"#00FF99","B":"#00E5FF","C":"#FF8800","D":"#FF3385"}
        for i, s in enumerate(db.load_scores_for_account(session.account_id, 10), 1):
            rk = s.get("grade","?")
            row = tk.Frame(sc_rows, bg="#06001A"); row.pack(fill="x", pady=1)
            for txt, w_, fc in [
                (str(i),                    4,  "#888888"),
                (s.get("song_name","")[:16], 18, "#FFD700"),
                (f"{s.get('score',0):,}",    12, "#FFFFFF"),
                (rk,                         6,  rank_colors.get(rk,"#888")),
                (s.get("difficulty",""),     10, "#00E5FF"),
            ]:
                tk.Label(row, text=txt, bg="#06001A", fg=fc, font=(UI_FONT, 9),
                         width=w_, anchor="center").pack(side="left", padx=2, pady=2)
        sc_rows.update_idletasks()
        sc_cv.config(scrollregion=sc_cv.bbox("all"))

        # Change password
        self._section_label(outer, "  CHANGE PASSWORD")
        pw_f  = tk.Frame(outer, bg="#0a0018"); pw_f.pack(pady=(6, 0))
        old_var = tk.StringVar(); new_var = tk.StringVar(); new2_var = tk.StringVar()
        for lbl_t, var, show_ch in [("Old PW", old_var, "●"),
                                    ("New PW", new_var, "●"),
                                    ("Confirm", new2_var, "●")]:
            rf = tk.Frame(pw_f, bg="#0a0018"); rf.pack(side="left", padx=8)
            tk.Label(rf, text=lbl_t, bg="#0a0018", fg="#888888",
                     font=(UI_FONT, 8, "bold")).pack()
            tk.Entry(rf, textvariable=var, show=show_ch, bg="#0c0022", fg="#FFFFFF",
                     insertbackground="#FFD700", font=(UI_FONT, 11),
                     relief="flat", bd=0, width=14,
                     highlightthickness=2,
                     highlightbackground="#334466",
                     highlightcolor="#FFD700").pack()

        pw_msg = tk.Label(outer, text="", bg="#0a0018", fg="#FF3385",
                          font=(UI_FONT, 10, "bold"))
        pw_msg.pack(pady=(6, 0))

        def _change_pw():
            new = new_var.get(); new2 = new2_var.get()
            if new != new2:
                pw_msg.config(text="✗  New passwords do not match.", fg="#FF3385"); return
            try:
                ok = db.change_password(session.account_id, old_var.get(), new)
                if ok:
                    pw_msg.config(text="✓  Password changed!", fg="#00FF99")
                else:
                    pw_msg.config(text="✗  Old password is incorrect.", fg="#FF3385")
            except AccountError as ae:
                pw_msg.config(text=f"✗  {ae.message}", fg="#FF3385")

        bf = tk.Frame(outer, bg="#0a0018"); bf.pack(pady=(16, 0))
        self._pixel_btn(bf, "🔑  CHANGE PASSWORD", BTN_COLORS[4], _change_pw, width=210).pack(side="left", padx=8)
        self._pixel_btn(bf, "◄  BACK",             BTN_COLORS[1],
                        lambda: self._fade_to(self._show_title), width=130).pack(side="left", padx=8)

    # ═══════════════════════════════════════════════════════════════
    #  EDIT PROFILE (My Profile popup — username, password changes)
    # ═══════════════════════════════════════════════════════════════
    def _show_edit_profile(self):
        """Open a Toplevel window for changing username and password.
        Reuses the registration-style UI.  Current password is always
        required before any change is accepted."""
        if session.is_guest:
            return

        win = tk.Toplevel(self.root)
        win.title("My Profile")
        win.configure(bg="#0a0018")
        win.resizable(False, False)
        win.grab_set()   # modal

        # Centre the window over the main window
        win.update_idletasks()
        pw_w, pw_h = 520, 500
        mx = self.root.winfo_rootx() + (_W - pw_w) // 2
        my = self.root.winfo_rooty() + (_H - pw_h) // 2
        win.geometry(f"{pw_w}x{pw_h}+{mx}+{my}")

        BG   = "#0a0018"
        CARD = "#0a0018"

        # Header
        hdr = tk.Frame(win, bg=CARD, highlightbackground="#00E5FF", highlightthickness=2)
        hdr.pack(fill="x", padx=0, pady=0)
        tk.Label(hdr, text="★  MY PROFILE", bg=CARD, fg="#00E5FF",
                 font=(UI_FONT, 18, "bold")).pack(pady=(16, 4))
        tk.Label(hdr, text=f"Logged in as: {session.username.upper()}", bg=CARD, fg="#888888",
                 font=(UI_FONT, 10)).pack(pady=(0, 14))

        body = tk.Frame(win, bg="#0a0018"); body.pack(fill="both", expand=True, padx=30, pady=20)

        msg_var = tk.StringVar()
        msg_lbl = tk.Label(body, textvariable=msg_var, bg="#0a0018", font=(UI_FONT, 10, "bold"),
                           wraplength=440)
        # placed at bottom — packed after sections so it appears last

        # ── Current password (required for any change) ──────────────
        self._section_label(body, "VERIFY IDENTITY")
        cur_pw_var = tk.StringVar()
        cur_row = tk.Frame(body, bg=BG); cur_row.pack(fill="x", pady=(4, 8))
        tk.Label(cur_row, text="Current Password:", bg=BG, fg="#888888",
                 font=(UI_FONT, 10, "bold"), width=18, anchor="w").pack(side="left")
        cur_pw_e = tk.Entry(cur_row, textvariable=cur_pw_var, show="●",
                            bg="#0c0022", fg="#FFFFFF", insertbackground="#FFD700",
                            font=(UI_FONT, 12, "bold"), relief="flat", width=20,
                            highlightthickness=2,
                            highlightbackground="#334466",
                            highlightcolor="#FFD700")
        cur_pw_e.pack(side="left", padx=(8, 0))
        cur_pw_e.focus_set()

        # ── Change username ─────────────────────────────────────────
        self._section_label(body, "CHANGE USERNAME")
        new_uname_var = tk.StringVar()
        un_row = tk.Frame(body, bg=BG); un_row.pack(fill="x", pady=(4, 0))
        tk.Label(un_row, text="New Username:", bg=BG, fg="#888888",
                 font=(UI_FONT, 10, "bold"), width=18, anchor="w").pack(side="left")
        tk.Entry(un_row, textvariable=new_uname_var, bg="#0c0022", fg="#FFFFFF",
                 insertbackground="#00E5FF", font=(UI_FONT, 12, "bold"),
                 relief="flat", width=20,
                 highlightthickness=2,
                 highlightbackground="#334466",
                 highlightcolor="#00E5FF").pack(side="left", padx=(8, 0))
        tk.Label(body, text="3-24 chars  •  Leave blank to skip",
                 bg=BG, fg="#445566", font=(UI_FONT, 8)).pack(anchor="w", pady=(2, 8))

        # ── Change password ─────────────────────────────────────────
        self._section_label(body, "CHANGE PASSWORD")
        new_pw_var  = tk.StringVar()
        new_pw2_var = tk.StringVar()
        for lbl_t, var in [("New Password:", new_pw_var), ("Confirm New:", new_pw2_var)]:
            prow = tk.Frame(body, bg=BG); prow.pack(fill="x", pady=3)
            tk.Label(prow, text=lbl_t, bg=BG, fg="#888888",
                     font=(UI_FONT, 10, "bold"), width=18, anchor="w").pack(side="left")
            tk.Entry(prow, textvariable=var, show="●", bg="#0c0022", fg="#FFFFFF",
                     insertbackground="#FF3385", font=(UI_FONT, 12, "bold"),
                     relief="flat", width=20,
                     highlightthickness=2,
                     highlightbackground="#334466",
                     highlightcolor="#FF3385").pack(side="left", padx=(8, 0))
        tk.Label(body, text="Min 6 chars  •  Leave blank to skip",
                 bg=BG, fg="#445566", font=(UI_FONT, 8)).pack(anchor="w", pady=(2, 0))

        msg_lbl.pack(pady=(12, 0))

        def _do_save():
            cur_pw  = cur_pw_var.get()
            new_un  = new_uname_var.get().strip()
            new_pw  = new_pw_var.get()
            new_pw2 = new_pw2_var.get()

            # Verify current password first
            acct = db.login(session.username, cur_pw)
            if not acct:
                msg_var.set("✗  Wrong current password."); msg_lbl.config(fg="#FF3385"); return

            any_change = False

            # Username change
            if new_un:
                if new_un == session.username:
                    msg_var.set("✗  New username same as current."); msg_lbl.config(fg="#FF3385"); return
                try:
                    db.change_username(session.account_id, new_un)
                    session._acct["username"] = new_un
                    any_change = True
                except AccountError as ae:
                    msg_var.set(f"✗  {ae.message}"); msg_lbl.config(fg="#FF3385"); return
                except Exception as ex:
                    msg_var.set(f"✗  {ex}"); msg_lbl.config(fg="#FF3385"); return

            # Password change
            if new_pw or new_pw2:
                if new_pw != new_pw2:
                    msg_var.set("✗  Passwords don't match."); msg_lbl.config(fg="#FF3385"); return
                try:
                    ok = db.change_password(session.account_id, cur_pw, new_pw)
                    if not ok:
                        msg_var.set("✗  Password change failed."); msg_lbl.config(fg="#FF3385"); return
                    any_change = True
                except AccountError as ae:
                    msg_var.set(f"✗  {ae.message}"); msg_lbl.config(fg="#FF3385"); return

            if any_change:
                msg_var.set("✓  Profile updated!"); msg_lbl.config(fg="#00FF99")
                # Refresh the home screen badge after a short delay
                win.after(1200, lambda: (win.destroy(), self._fade_to(self._show_title)))
            else:
                msg_var.set("No changes were made."); msg_lbl.config(fg="#888888")

        btn_row = tk.Frame(body, bg=BG); btn_row.pack(pady=(14, 0))
        self._pixel_btn(btn_row, "✔  SAVE CHANGES", BTN_COLORS[0], _do_save, width=180).pack(side="left", padx=8)
        self._pixel_btn(btn_row, "✖  CLOSE",        BTN_COLORS[1], win.destroy, width=120).pack(side="left", padx=8)

    # ═══════════════════════════════════════════════════════════════
    #  GAME LOGIC
    # ═══════════════════════════════════════════════════════════════
    def _start_game(self):
        """
        Initialise and begin a new gameplay session.

        Builds the song list, creates the gs dict via
        game_logic.make_game_state(), then starts beat analysis +
        audio in a background thread via _load_and_play_current().
        """
        self._clear_widgets()
        self.particles.clear(); self.flashes.clear()
        self.side_effects.clear(); self.sparks.clear()
        self._overlay_open  = False
        self._overlay_frame = None
        audio.fadeout_for_game()

        num_lanes = cfg.num_lanes
        self.lane_lit  = [False] * num_lanes
        self.keys_held = set()
        playable = get_all_playable_songs(cfg.songs)

        # Build ordered song list and path map
        if cfg.selected_mode == "shuffle":
            if playable:
                random.shuffle(playable)
                song_names = [n for n, _ in playable]
                song_paths = {n: p for n, p in playable}
            else:
                sl = list(cfg.songs); random.shuffle(sl)
                song_names = sl; song_paths = {}
            is_endless = True
        else:
            song_names = [cfg.selected_song]
            path       = next((p for n, p in playable if n == cfg.selected_song), None)
            song_paths = {cfg.selected_song: path} if path else {}
            is_endless = False

        # Create the game state dict via the factory in game_logic
        self.gs = make_game_state(
            song_names, song_paths, is_endless,
            num_lanes, cfg.difficulty
        )
        self._game_volume = cfg.music_volume
        self.screen       = "game"
        self._build_ingame_overlay_btn()
        self._load_and_play_current()

    def _build_ingame_overlay_btn(self):
        # Colorful pixel-style MENU button — no circle/glow box behind it
        _mc = BTN_COLORS[2]  # cyan
        _mw, _mh = 100, 32
        self._overlay_btn = tk.Canvas(
            self.root, width=_mw, height=_mh,
            bg=BG_COL, highlightthickness=0, cursor="hand2",
        )
        self._overlay_btn.place(relx=1.0, rely=0.0, anchor="ne", x=-8, y=82)
        self._overlay_btn_col   = _mc
        self._overlay_btn_label = "☰  MENU"

        def _draw_menu_btn(col, label, pressed=False):
            self._overlay_btn.delete("all")
            ox, oy = (1, 1) if pressed else (0, 0)
            bright = lighten(col, 0.55); dark = dim(col, 0.40)
            self._overlay_btn.create_rectangle(ox, oy, _mw+ox, _mh+oy, fill=col, outline="")
            self._overlay_btn.create_rectangle(ox, oy, _mw+ox, oy+3, fill=bright, outline="")
            self._overlay_btn.create_rectangle(ox, _mh+oy-3, _mw+ox, _mh+oy, fill=dark, outline="")
            self._overlay_btn.create_text(_mw//2+ox, _mh//2+oy, text=label,
                                          fill="#000000",
                                          font=(UI_FONT, 9, "bold"), anchor="center")

        _mb_pressed = [False]
        def _mb_press(e):  _mb_pressed[0]=True;  _draw_menu_btn(self._overlay_btn_col, self._overlay_btn_label, True)
        def _mb_release(e):
            _mb_pressed[0]=False; _draw_menu_btn(self._overlay_btn_col, self._overlay_btn_label, False)
            self._play_click(); self.root.after(40, self._toggle_ingame_overlay)
        def _mb_leave(e):
            if _mb_pressed[0]: _mb_pressed[0]=False; _draw_menu_btn(self._overlay_btn_col, self._overlay_btn_label, False)

        self._overlay_btn.bind("<ButtonPress-1>",   _mb_press)
        self._overlay_btn.bind("<ButtonRelease-1>", _mb_release)
        self._overlay_btn.bind("<Leave>",           _mb_leave)
        self._draw_menu_btn_fn = _draw_menu_btn
        _draw_menu_btn(_mc, "☰  MENU")

    def _toggle_ingame_overlay(self):
        if self._overlay_open:
            self._close_ingame_overlay()
        else:
            self._open_ingame_overlay()

    def _open_ingame_overlay(self):
        if self._overlay_open: return
        self._overlay_open = True
        gs = self.gs
        # Pause the game logic and audio when menu opens, but mark it as
        # a "menu pause" so the PAUSED canvas text is suppressed.
        # The PAUSED canvas text only shows when SPACE is pressed directly.
        if not gs.get("paused") and not gs.get("ended"):
            gs["paused"]      = True
            gs["menu_paused"] = True   # flag: paused BY menu, not by spacebar
            if gs.get("countdown", 0) <= 0:
                audio.pause()
        else:
            gs["menu_paused"] = False

        overlay = tk.Frame(self.root, bg="#0a0018",
                           highlightbackground="#FFD700", highlightthickness=2)
        overlay.place(relx=0.5, rely=0.5, anchor="center", width=420, height=480)
        self._overlay_frame = overlay

        tk.Label(overlay, text="⏸  GAME  MENU", bg="#0a0018", fg="#FFD700",
                 font=(TITLE_FONT, 18, "bold")).pack(pady=(22, 4))
        tk.Frame(overlay, bg="#FFD700", height=1).pack(fill="x", padx=30, pady=(0, 14))

        # ── Single Volume slider — controls all audio ───────────────
        vol_body = tk.Frame(overlay, bg="#0a0018"); vol_body.pack(fill="x", padx=28, pady=(0, 4))

        vol_row = tk.Frame(vol_body, bg="#0a0018"); vol_row.pack(fill="x", pady=5)
        tk.Label(vol_row, text="♪  Volume", bg="#0a0018", fg="#FFD700",
                 font=(UI_FONT, 10, "bold"), width=12, anchor="w").pack(side="left")
        self._ingame_vol_var = tk.DoubleVar(value=cfg.master_volume)
        ig_pct = tk.Label(vol_row, text=f"{int(cfg.master_volume * 100)}%",
                          bg="#0a0018", fg="#FFFFFF",
                          font=(UI_FONT, 10, "bold"), width=5)
        ig_pct.pack(side="right")

        def _on_all_vol(v):
            val = float(v)
            ig_pct.config(text=f"{int(val * 100)}%")
            self._game_volume = val
            cfg.master_volume = val
            cfg.music_volume  = val
            cfg.sfx_volume    = val
            cfg.apply()
            try:
                audio.set_sfx_volume(val)
            except Exception:
                pass

        tk.Scale(vol_row, variable=self._ingame_vol_var, from_=0.0, to=1.0,
                 resolution=0.01, orient="horizontal", length=230,
                 bg="#0a0018", fg="#FFD700", troughcolor="#220044",
                 activebackground="#FFD700",
                 highlightthickness=0, sliderlength=18, width=12,
                 command=_on_all_vol).pack(side="left", padx=(8, 0))

        btn_f = tk.Frame(overlay, bg="#0a0018"); btn_f.pack(pady=(10, 0))

        def _resume():
            self._close_ingame_overlay()
            # _close_ingame_overlay already handles menu_paused → unpause
            # If user also pressed SPACE while menu was open (double-paused),
            # those remain paused until SPACE is pressed again.

        def _main_menu():
            self._close_ingame_overlay()
            self._end_game()
            self._fade_to(self._show_title)

        overlay_btns = [
            ("▶  RESUME",      BTN_COLORS[3], _resume),
            ("↺  SONG SELECT", BTN_COLORS[0],
             lambda: (self._close_ingame_overlay(), self._end_game(),
                      self._fade_to(self._show_pre_game))),
            ("⌂  MAIN MENU",   BTN_COLORS[1], _main_menu),
        ]
        for ob_lbl, ob_col, ob_cmd in overlay_btns:
            self._pixel_btn(btn_f, ob_lbl, ob_col, ob_cmd, width=240).pack(pady=6)

        # Fullscreen toggle button inside the in-game menu
        fs_row = tk.Frame(overlay, bg="#0a0018"); fs_row.pack(pady=(4, 0))
        _fs2_w, _fs2_h = 240, 36

        fs2_cv = tk.Canvas(fs_row, width=_fs2_w, height=_fs2_h,
                           bg="#0a0018", highlightthickness=0, cursor="hand2")
        fs2_cv.pack()

        def _draw_fs2_btn(on=False, pressed=False):
            fs2_cv.delete("all")
            col  = BTN_COLORS[2] if on else dim(BTN_COLORS[2], 0.28)
            ox, oy = (1, 1) if pressed else (0, 0)
            fs2_cv.create_rectangle(ox, oy, _fs2_w+ox, _fs2_h+oy, fill=col, outline="")
            fs2_cv.create_rectangle(ox, oy, _fs2_w+ox, oy+3, fill=lighten(col, 0.55), outline="")
            fs2_cv.create_rectangle(ox, _fs2_h+oy-3, _fs2_w+ox, _fs2_h+oy, fill=dim(col, 0.40), outline="")
            lbl = "⛶  FULLSCREEN  ON" if on else "⛶  FULLSCREEN  OFF"
            fg  = "#000000" if on else dim(BTN_COLORS[2], 0.85)
            fs2_cv.create_text(_fs2_w//2+ox, _fs2_h//2+oy, text=lbl,
                               fill=fg, font=(UI_FONT, 11, "bold"), anchor="center")

        _draw_fs2_btn(cfg.fullscreen)
        _fs2_pressed = [False]

        def _fs2_press(e):   _fs2_pressed[0] = True;  _draw_fs2_btn(cfg.fullscreen, True)
        def _fs2_release(e):
            _fs2_pressed[0] = False
            new_val = not cfg.fullscreen
            cfg.fullscreen = new_val
            self._apply_fullscreen()
            _draw_fs2_btn(new_val)
        def _fs2_leave(e):
            if _fs2_pressed[0]: _fs2_pressed[0] = False; _draw_fs2_btn(cfg.fullscreen)

        fs2_cv.bind("<ButtonPress-1>",   _fs2_press)
        fs2_cv.bind("<ButtonRelease-1>", _fs2_release)
        fs2_cv.bind("<Leave>",           _fs2_leave)

        if hasattr(self, "_overlay_btn"):
            self._overlay_btn_col   = BTN_COLORS[1]   # pink = "close"
            self._overlay_btn_label = "✕  CLOSE"
            try:
                self._draw_menu_btn_fn(BTN_COLORS[1], "✕  CLOSE")
            except Exception:
                pass

    def _close_ingame_overlay(self):
        self._overlay_open = False
        if self._overlay_frame:
            try: self._overlay_frame.destroy()
            except Exception: pass
            self._overlay_frame = None
        # If the game was paused by the menu (not by SPACE), auto-resume it
        if hasattr(self, "gs") and self.gs:
            gs = self.gs
            if gs.get("menu_paused") and gs.get("paused") and not gs.get("ended"):
                gs["paused"]      = False
                gs["menu_paused"] = False
                if gs.get("countdown", 0) <= 0:
                    audio.unpause()
            elif "menu_paused" in gs:
                gs["menu_paused"] = False
        if hasattr(self, "_overlay_btn"):
            self._overlay_btn_col   = BTN_COLORS[2]   # cyan = "menu"
            self._overlay_btn_label = "☰  MENU"
            try:
                self._draw_menu_btn_fn(BTN_COLORS[2], "☰  MENU")
            except Exception:
                pass

    def _load_and_play_current(self):
        gs        = self.gs
        if gs.get("ended"): return
        song_name = gs["song_names"][gs["song_idx"] % len(gs["song_names"])]
        mp3_path  = gs["song_paths"].get(song_name)

        def _worker():
            if not mp3_path:
                gs["beat_mode"] = False; gs["loading"] = False; return
            if mp3_path in self._beat_cache:
                beats = self._beat_cache[mp3_path]
            else:
                gs["loading"] = True
                from audio_engine import analyse_beats, build_beat_chart
                beats = analyse_beats(mp3_path)
                self._beat_cache[mp3_path] = beats
                gs["loading"] = False
            if gs.get("ended"): return
            if beats:
                diff  = DIFFICULTY[cfg.difficulty]
                speed = diff["speed"]
                from audio_engine import build_beat_chart
                gs["beat_chart"]   = build_beat_chart(beats, speed, gs["num_lanes"], diff)
                gs["chart_cursor"] = 0
                gs["beat_mode"]    = True
                gs["song_duration"]= beats[-1] + 4.0
            else:
                gs["beat_chart"] = []; gs["chart_cursor"] = 0
                gs["beat_mode"]  = False
            gs["current_mp3"] = mp3_path
            gs["countdown"]   = 3.99
            gs["advancing"]   = False

        self._worker_thread = threading.Thread(target=_worker, daemon=True)
        self._worker_thread.start()

    def _advance_song(self):
        gs = self.gs
        if gs.get("ended") or gs.get("advancing"): return
        gs["advancing"]       = True
        audio.stop()
        gs["song_idx"]        = (gs["song_idx"] + 1) % max(1, len(gs["song_names"]))
        gs["beat_chart"]      = []; gs["chart_cursor"] = 0
        gs["beat_mode"]       = False; gs["loading"]   = False
        gs["song_wall_start"] = None; gs["song_duration"] = None
        gs["notes"]           = []
        self._load_and_play_current()

    def _end_game(self):
        if self.gs.get("ended"): return
        self.gs["ended"] = True
        audio.stop()
        self._close_ingame_overlay()
        self._fade_to(self._show_name_entry)

    # ── Name entry / Game-over ────────────────────────────────────────
    def _show_name_entry(self):
        self._clear_widgets(); self.screen = "name_entry"
        gs    = self.gs
        total = gs["perfect"] + gs["good"] + gs["miss"]
        acc   = int(((gs["perfect"] + gs["good"]) / max(total, 1)) * 100)

        if   acc >= 95 and gs["perfect"] > 15: rank, rc = "S", "#FFD700"
        elif acc >= 85:                         rank, rc = "A", "#00FF99"
        elif acc >= 70:                         rank, rc = "B", "#00E5FF"
        elif acc >= 50:                         rank, rc = "C", "#FF8800"
        else:                                   rank, rc = "D", "#FF3385"

        outer = tk.Frame(self.root, bg="#04000C",
                         highlightbackground=rc, highlightthickness=2)
        outer.place(relx=0.5, rely=0.5, anchor="center", width=min(580, int(_W*0.82)), height=min(440, int(_H*0.82)))

        tk.Label(outer, text=rank, bg="#04000C", fg=rc,
                 font=(TITLE_FONT, 80, "bold")).pack(pady=(16, 0))

        sf = tk.Frame(outer, bg="#04000C"); sf.pack(pady=(0, 16))
        for lbl, val in [("SCORE", f"{gs['score']:,}"),
                         ("ACC",   f"{acc}%"),
                         ("MAX COMBO", str(gs["max_combo"]))]:
            cf = tk.Frame(sf, bg="#0a0020", highlightbackground=rc, highlightthickness=1)
            cf.pack(side="left", padx=8, ipadx=16, ipady=8)
            tk.Label(cf, text=lbl, bg="#0a0020", fg="#888888",
                     font=(UI_FONT, 8, "bold")).pack()
            tk.Label(cf, text=val, bg="#0a0020", fg=rc,
                     font=(MONO_FONT, 18, "bold")).pack()

        if session.is_guest:
            # Guests cannot save scores — show info and go straight to gameover
            tk.Label(outer, text="PERFORMANCE COMPLETE",
                     bg="#04000C", fg="#FFFFFF", font=(UI_FONT, 13, "bold")).pack(pady=(6, 4))
            tk.Label(outer, text="Log in to save scores to the leaderboard.",
                     bg="#04000C", fg="#888888", font=(UI_FONT, 11)).pack(pady=(0, 8))
            tk.Label(outer, text="Playing as Guest — scores are not saved.",
                     bg="#04000C", fg="#FF8800", font=(UI_FONT, 10, "bold")).pack(pady=(0, 16))
            bf = tk.Frame(outer, bg="#04000C"); bf.pack(pady=(4, 0))
            self._pixel_btn(bf, "▶  CONTINUE", BTN_COLORS[0],
                            lambda: self._fade_to(self._show_gameover), width=200).pack(side="left", padx=8)
            self._pixel_btn(bf, "★  LOGIN / REGISTER", BTN_COLORS[2],
                            lambda: self._fade_to(self._show_login), width=200).pack(side="left", padx=8)
            return

        # ── Logged-in user: show read-only name + optional save ──────
        tk.Label(outer, text="SAVE YOUR SCORE TO THE LEADERBOARD",
                 bg="#04000C", fg="#FFFFFF", font=(UI_FONT, 13, "bold")).pack(pady=(6, 16))

        ef = tk.Frame(outer, bg="#04000C"); ef.pack(pady=(0, 8))
        tk.Label(ef, text="NAME:", bg="#04000C", fg="#FFD700",
                 font=(UI_FONT, 12, "bold")).pack(side="left", padx=(0, 8))
        name_var = tk.StringVar(value=session.username)
        # Read-only entry — logged-in users cannot change their name
        name_e = tk.Entry(ef, textvariable=name_var, bg="#1a0038", fg="#00E5FF",
                          insertbackground="#FFD700", font=(UI_FONT, 14, "bold"),
                          relief="flat", width=18,
                          highlightthickness=2,
                          highlightbackground="#334466",
                          highlightcolor="#FFD700",
                          state="readonly")
        name_e.pack(side="left")
        tk.Label(ef, text=f"  ★ LOGGED IN", bg="#04000C", fg="#00E5FF",
                 font=(UI_FONT, 9, "bold")).pack(side="left")

        msg_lbl = tk.Label(outer, text="", bg="#04000C", fg="#FF3385",
                           font=(UI_FONT, 10, "bold"))
        msg_lbl.pack()

        def submit():
            name = session.username   # always use account username
            db.save_score(
                player_name = name,
                score       = gs["score"],
                accuracy    = acc,
                max_combo   = gs["max_combo"],
                grade       = rank,
                difficulty  = cfg.difficulty,
                song_name   = gs["song_names"][gs["song_idx"] % max(1, len(gs["song_names"]))],
                account_id  = session.account_id,
            )
            msg_lbl.config(text="✓  Score saved!", fg="#00FF99")
            self.root.after(700, lambda: self._fade_to(self._show_gameover))

        bf = tk.Frame(outer, bg="#04000C"); bf.pack(pady=(8, 0))
        self._pixel_btn(bf, "✔  SAVE SCORE", BTN_COLORS[0], submit, width=160).pack(side="left", padx=8)
        self._pixel_btn(bf, "✖  SKIP",       BTN_COLORS[4],
                        lambda: self._fade_to(self._show_gameover), width=120).pack(side="left", padx=8)

    def _show_gameover(self):
        self._clear_widgets(); self.screen = "gameover"
        gs    = self.gs
        total = gs["perfect"] + gs["good"] + gs["miss"]
        acc   = int(((gs["perfect"] + gs["good"]) / max(total, 1)) * 100)

        if   acc >= 95 and gs["perfect"] > 15: rank,rc,msg = "S","#FFD700","LEGENDARY ACE Performance!"
        elif acc >= 85:                         rank,rc,msg = "A","#00FF99","Amazing! You're a true ACE!"
        elif acc >= 70:                         rank,rc,msg = "B","#00E5FF","Great job — keep shining!"
        elif acc >= 50:                         rank,rc,msg = "C","#FF8800","Keep practicing, ACE!"
        else:                                   rank,rc,msg = "D","#FF3385","The light keeps burning for you!"

        outer = tk.Frame(self.root, bg="#04000C",
                         highlightbackground=rc, highlightthickness=3)
        outer.place(relx=0.5, rely=0.5, anchor="center")
        tk.Label(outer, text=rank,    bg="#04000C", fg=rc,       font=(TITLE_FONT, 100, "bold")).pack()
        tk.Label(outer, text="PERFORMANCE COMPLETE",
                 bg="#04000C", fg="#FF3385", font=(TITLE_FONT, 24, "bold")).pack()
        tk.Label(outer, text=msg, bg="#04000C", fg="#aaaaaa",    font=(UI_FONT, 13)).pack(pady=(4, 22))

        grid = tk.Frame(outer, bg="#04000C"); grid.pack(pady=(0, 24))
        for i, (lbl, val) in enumerate([
            ("FINAL SCORE", f"{gs['score']:,}"), ("MAX COMBO", str(gs["max_combo"])),
            ("PERFECT",     str(gs["perfect"])), ("ACCURACY",  f"{acc}%"),
        ]):
            r, c = divmod(i, 2)
            cell = tk.Frame(grid, bg="#0a0020", highlightbackground=rc, highlightthickness=1)
            cell.grid(row=r, column=c, padx=10, pady=8, ipadx=22, ipady=10)
            tk.Label(cell, text=lbl, bg="#0a0020", fg="#888888", font=(UI_FONT, 9, "bold")).pack()
            tk.Label(cell, text=val, bg="#0a0020", fg=rc,        font=(MONO_FONT, 26, "bold")).pack()

        bf = tk.Frame(outer, bg="#04000C"); bf.pack(pady=(0, 16))
        self._pixel_btn(bf, "▶  PLAY AGAIN", BTN_COLORS[0],
                        lambda: self._fade_to(self._show_pre_game), width=160).pack(side="left", padx=8)
        self._pixel_btn(bf, "★  RANKINGS",   BTN_COLORS[1],
                        lambda: self._fade_to(self._show_rankings), width=160).pack(side="left", padx=8)
        self._pixel_btn(bf, "⌂  MAIN STAGE", BTN_COLORS[2],
                        lambda: self._fade_to(self._show_title), width=160).pack(side="left", padx=8)

    # ═══════════════════════════════════════════════════════════════
    #  INPUT
    # ═══════════════════════════════════════════════════════════════
    def _on_key_down(self, ev):
        k = ev.keysym.lower()
        if k == "escape":
            if self.screen in ("login", "register"): return
            if self.screen == "title" and not session.is_guest:
                self._logout(); return
            if self.screen in ("game", "gameover", "settings", "trivia",
                               "pre_game", "rankings", "trivia_rankings",
                               "name_entry", "profile"):
                if self.screen == "game": self._end_game()
                else: self._fade_to(self._show_title)
            return
        if self.screen == "game":
            if k == "space":
                if not self._overlay_open: self._toggle_pause()
                return
            if k in ("equal", "plus"):
                self._game_volume = min(1.0, self._game_volume + 0.05)
                cfg.set_music_volume(self._game_volume); return
            if k == "minus":
                self._game_volume = max(0.0, self._game_volume - 0.05)
                cfg.set_music_volume(self._game_volume); return
            if self.gs.get("countdown", 0) > 0 or self.gs.get("paused"): return
        lane_keys = LANE_CONFIGS[cfg.num_lanes]["keys"]
        if k in lane_keys and k not in self.keys_held:
            self.keys_held.add(k)
            idx = lane_keys.index(k)
            if idx < len(self.lane_lit): self.lane_lit[idx] = True
            if self.screen == "game": self._hit_lane(idx)

    def _on_key_up(self, ev):
        k = ev.keysym.lower(); self.keys_held.discard(k)
        lane_keys = LANE_CONFIGS[cfg.num_lanes]["keys"]
        if k in lane_keys:
            try:
                idx = lane_keys.index(k)
                if idx < len(self.lane_lit): self.lane_lit[idx] = False
            except ValueError: pass

    def _toggle_pause(self):
        if self.screen != "game" or self.gs.get("ended"): return
        gs = self.gs
        if gs.get("paused"):
            gs["paused"] = False
            if gs["countdown"] <= 0: audio.unpause()
        else:
            gs["paused"] = True
            if gs["countdown"] <= 0: audio.pause()

    def _hit_lane(self, idx):
        """
        Handle a key press in lane idx.
        Delegates all hit detection and scoring to game_logic.hit_lane()
        which applies the PERFECT/GOOD/MISS rules, updates gs, and spawns
        the appropriate visual feedback objects.
        """
        hit_lane(
            idx, self.gs,
            self.particles, self.sparks, self.flashes, self.side_effects,
            _W, _H, _project, _HIT_DEPTH
        )

    # ═══════════════════════════════════════════════════════════════
    #  MAIN LOOP & DRAWING
    # ═══════════════════════════════════════════════════════════════
    def _loop(self):
        if not self._alive:
            return                   # window is closing — stop the loop
        now    = time.time()
        dt     = min(now - self.last_t, 0.05)
        self.last_t = now; self.t += dt

        if self.screen == "trivia" and self._tv_next_time and now >= self._tv_next_time:
            self._tv_next_time = None; self._tv_idx += 1; self._render_trivia()
        if self.screen == "trivia":
            self._update_timer_bar()

        # Update animated border for login/register screens
        if self.screen in ("login", "register"):
            self._update_login_container_border()

        try:
            self._draw(dt)
        except Exception:
            pass                     # canvas may be mid-destruction — swallow silently

        if self._alive:
            self.root.after(int(1000 / FPS), self._loop)

    def _draw(self, dt):
        """
        Clear the canvas and render the current frame.

        Draw order: background → track geometry → game objects or title
        graphics → carousel → transition overlay (always last).
        _draw_stage() is kept for backward compatibility with title-screen
        callers; it internally calls draw_track() from game_renderer.
        """
        cv = self.cv; cv.delete("all")
        self._draw_bg(cv)
        if self.screen in ("title", "game", "login", "register"):
            self._draw_stage(cv)
        if self.screen == "game":
            self._update_game(dt)
            self._draw_game(cv)
        if self.screen == "title":
            self._draw_title_graphics(cv)
            self._draw_nav_buttons(cv)
            self._draw_led_ticker(cv, dt)
            # Animate the logged-in user badge border glow
            if hasattr(self, '_badge_frame') and self._badge_frame:
                try:
                    glow_colors = ["#FFD700", "#FF3385", "#00E5FF", "#FF8800", "#CC44FF"]
                    gp = (self.t * 0.5) % 1.0
                    gi0 = int(gp * len(glow_colors)) % len(glow_colors)
                    gi1 = (gi0 + 1) % len(glow_colors)
                    gf2 = (gp * len(glow_colors)) - gi0
                    gc  = blend(glow_colors[gi0], glow_colors[gi1], gf2)
                    self._badge_frame.config(highlightbackground=gc, highlightthickness=2)
                except Exception:
                    pass
        if self.screen == "pre_game":
            self._carousel_render()
        if self.screen == "trivia_confirm":
            self._draw_trivia_confirm_canvas(cv)
        # Transition overlay always drawn last — covers everything during fades
        self._draw_transition(dt)

    # ── Background ────────────────────────────────────────────────────
    def _draw_bg(self, cv):
        segs = 20
        for i in range(segs):
            t2 = i / segs
            r2 = _clamp(6  + t2 * 10)
            b2 = _clamp(18 + t2 * 50)
            y0 = int(_H * i / segs); y1 = int(_H * (i + 1) / segs)
            cv.create_rectangle(0, y0, _W, y1, fill=f"#{r2:02x}00{b2:02x}", outline="")
        
        # Concert lights — always visible on title/game screens
        for sl in self.spotlights:
            sl.draw(cv, self.t, _W, _H)
        # Twinkling stars — always visible
        for s in self.stars:
            x  = s["nx"] * _W
            y  = s["ny"] * _H
            ph = s["ph"]
            a  = 0.55 + 0.45 * abs(math.sin(self.t * 0.7 + ph))
            r  = s["r"] * (0.9 + 0.4 * a)
            cv.create_oval(x - r, y - r, x + r, y + r, 
                          fill=dim("#FFFFFF", a), outline="")

    # ── Title graphics ────────────────────────────────────────────────
    def _draw_title_graphics(self, cv):
        cx  = _W // 2; cy = int(self.sy(BASE_H * 0.15))

        # ── Stage floor behind photo (title screen only) ──────────────
        # Draw a perspective stage grid at the bottom so the photo sits
        # naturally on a stage. This renders BEFORE the photo so it appears
        # beneath the members image.
        if "members" in self.img_refs:
            stage_bottom = _H
            stage_top    = int(_H * 0.52)
            vp_x         = _W // 2
            vp_y         = stage_top
            stage_w      = _W * 0.90
            sl_           = (_W - stage_w) / 2
            grid_steps   = 12
            pls_stage    = 0.55 + 0.25 * math.sin(self.t * 1.6)
            for i in range(grid_steps + 1):
                d   = i / grid_steps
                persp = 0.18 + d * 0.82
                y_line = vp_y + (stage_bottom - vp_y) * d
                left_x = vp_x + (sl_ - vp_x) * persp
                right_x = vp_x + (sl_ + stage_w - vp_x) * persp
                if   d < 0.4: base = blend("#220033", "#5500AA", d / 0.4)
                elif d < 0.75: base = blend("#002299", "#0099CC", (d-0.4)/0.35)
                else:          base = blend("#0099CC", "#FFD700", (d-0.75)/0.25)
                la = pls_stage * (0.12 + d * 0.50)
                cv.create_line(left_x, y_line, right_x, y_line,
                               fill=dim(base, la * 0.35), width=max(2, int(d * 6)))
                cv.create_line(left_x, y_line, right_x, y_line,
                               fill=dim(base, la), width=max(1, int(d * 2)))
            # Vertical lane lines
            for lane in range(6):
                lx_frac = lane / 5
                for d_pair in [(0.0, 1.0)]:
                    d0, d1 = d_pair
                    p0 = (vp_x + (sl_ + lx_frac * stage_w - vp_x) * (0.18 + d0 * 0.82),
                          vp_y + (stage_bottom - vp_y) * d0)
                    p1 = (vp_x + (sl_ + lx_frac * stage_w - vp_x) * (0.18 + d1 * 0.82),
                          vp_y + (stage_bottom - vp_y) * d1)
                    col = MEMBER_COLORS[lane % len(MEMBER_COLORS)]
                    cv.create_line(p0[0], p0[1], p1[0], p1[1],
                                   fill=dim(col, pls_stage * 0.18), width=1)
            # Floor glow — single-pixel-height line, purely additive, no visible shape
            glow_rx = int(_W * 0.38)
            glow_cy = int(_H * 0.958)
            cv.create_oval(cx - glow_rx, glow_cy - 2, cx + glow_rx, glow_cy + 2,
                           fill=additive_blend(BG_COL, "#FFD700", 0.06 * pls_stage), outline="")

        # Ambient rings around logo — animated colour cycle (yellow → blue → pink-gold)
        _ring_cycle = ["#FFD700", "#00E5FF", "#FF3385", "#FFB347", "#00FF99"]
        _n_cyc = len(_ring_cycle)
        for ring in range(6):
            r     = self.sy(155 + ring * 58) + math.sin(self.t * 1.1 + ring) * 10
            phase = (self.t * 0.38 + ring * 0.22) % 1.0   # 0..1 continuously
            ci0   = int(phase * _n_cyc) % _n_cyc
            ci1   = (ci0 + 1) % _n_cyc
            frac  = (phase * _n_cyc) - int(phase * _n_cyc)
            ring_col = blend(_ring_cycle[ci0], _ring_cycle[ci1], frac)
            pulse_a   = 0.22 + 0.14 * abs(math.sin(self.t * 1.6 + ring * 0.8))
            a = max(0.0, pulse_a - ring * 0.025)
            cv.create_oval(cx - r, cy - r, cx + r, cy + r,
                           outline=dim(ring_col, a), width=2)

        # Logo with ANIMATED COLOUR-CYCLING GLOW (yellow → blue → pink-gold)
        logo_y = cy
        _glow_colors = ["#FFD700", "#00E5FF", "#FF3385", "#FFB347"]
        _gc_phase = (self.t * 0.30) % 1.0
        _gc_i0    = int(_gc_phase * len(_glow_colors)) % len(_glow_colors)
        _gc_i1    = (_gc_i0 + 1) % len(_glow_colors)
        _gc_frac  = (_gc_phase * len(_glow_colors)) - int(_gc_phase * len(_glow_colors))
        _glow_col = blend(_glow_colors[_gc_i0], _glow_colors[_gc_i1], _gc_frac)
        if "logo" in self.img_refs:
            pulse = math.sin(self.t * 3.0) * 0.28 + 0.72
            self._draw_ellipse_glow(cv, cx, logo_y, self.sy(148), self.sy(148), _glow_col, 14, pulse)
            cv.create_image(cx, logo_y, image=self.img_refs["logo"], anchor="center")
        else:
            logo_sz   = max(72, int(_H * 0.195))
            glow_p    = 0.62 + 0.38 * math.sin(self.t * 2.4)
            self._draw_ellipse_glow(cv, cx, logo_y, int(_W * 0.22), int(_H * 0.09), _glow_col, 12, glow_p * 0.85)
            cv.create_text(cx + 5, logo_y + 5, text="BGYO", fill=dim("#00E5FF", 0.30),
                           font=(TITLE_FONT, logo_sz, "bold"))
            gold = blend("#FFD700", "#FFEE00", (glow_p - 0.6) / 0.40)
            cv.create_text(cx, logo_y, text="BGYO", fill=gold,
                           font=(TITLE_FONT, logo_sz, "bold"))

        # Title text with shadow for readability above the photo
        float_y  = math.sin(self.t * 2.5) * self.sy(7)
        title_y  = int(self.sy(BASE_H * 0.345) + float_y)
        title_sz = int(self.sy(50))
        TFONT    = (TITLE_FONT, title_sz, "bold")
        title    = "THE LIGHT STAGE"
        # Strong shadow for legibility over stage/photo
        for sx_off, sy_off in [(5,5),(4,4),(3,3)]:
            cv.create_text(_W // 2 + sx_off, title_y + sy_off, text=title,
                           fill=dim("#000000", 0.65), font=TFONT)
        cv.create_text(_W // 2, title_y, text=title, fill="#FFFFFF", font=TFONT)
        neon_a = 0.60 + 0.12 * math.sin(self.t * 2.2)
        cv.create_text(_W // 2, title_y, text=title, fill=dim("#00E5FF", neon_a), font=TFONT)

        pulse_txt = math.sin(self.t * 4.0) * 0.20 + 0.80
        sub_y     = int(self.sy(BASE_H * 0.435)) + math.sin(self.t * 3.0 + 1) * self.sy(3)

        sub_text  = "ACES  OF  P-POP"
        sub_sz    = int(self.sy(20))
        sub_font  = (TITLE_FONT, max(14, sub_sz), "bold")
        # Shadow layers for visibility over stage floor
        for sx_off, sy_off in [(3,3),(2,2)]:
            cv.create_text(_W // 2 + sx_off, sub_y + sy_off, text=sub_text,
                           fill=dim("#000000", 0.70), font=sub_font)
        cv.create_text(_W // 2, sub_y, text=sub_text,
                       fill=dim("#FF3385", min(1.0, pulse_txt * 0.95)), font=sub_font)
        cv.create_text(_W // 2, sub_y, text=sub_text,
                       fill=dim("#FFE066", pulse_txt), font=sub_font)

        # ── Group photo — bottom-anchored flush with button row ─────
        # No glow effects — plain photo with bottom-fade so it blends
        # naturally into the stage floor without looking pasted on.
        if "members" in self.img_refs:
            btn_top   = self._nav_btns_layout()[0][4]   # y1 of button row
            photo_img = self.img_refs["members"]
            iw, ih    = self.img_refs.get("members_size",
                                          (photo_img.width(), photo_img.height()))
            # Bottom of photo sits exactly at button row top
            photo_bottom = btn_top
            photo_top    = photo_bottom - ih
            photo_cx     = _W // 2
            photo_cy     = photo_top + ih // 2
            cv.create_image(photo_cx, photo_cy,
                            image=photo_img, anchor="center")

    # ── Stage / track ─────────────────────────────────────────────────
    def _draw_stage(self, cv):
        # ENHANCED v18.1 FINAL: MUCH BRIGHTER, glowing neon stage
        if self.screen not in ("game", "title"): return
        LANES      = self.gs.get("num_lanes", cfg.num_lanes) if self.screen == "game" else cfg.num_lanes
        combo      = self.gs.get("combo", 0) if self.screen == "game" else 0
        brightness = min(1.0, 0.55 + combo * 0.012)  # Increased base from 0.45 to 0.55

        grid_steps = 28
        for i in range(grid_steps):
            d   = (i / (grid_steps - 1)) ** 1.4
            lp  = _project(0, d); rp = _project(1, d)
            if   d < 0.35: base = blend("#330044", "#7700FF", d / 0.35)
            elif d < 0.70: base = blend("#0033CC", "#00E5FF", (d - 0.35) / 0.35)
            else:          base = blend("#00E5FF", "#FFD700", (d - 0.70) / 0.30)
            # ENHANCED: MUCH brighter stage glow - increased alpha significantly
            la = brightness * (0.35 + d * 0.70)  # Was 0.25 + d * 0.60, now even brighter!
            cv.create_line(lp[0], lp[1], rp[0], rp[1],
                           fill=dim(base, la * 0.40), width=max(3, int(d * 8)))  # Was 0.3, now 0.40
            cv.create_line(lp[0], lp[1], rp[0], rp[1],
                           fill=dim(base, la),       width=max(1, int(d * 3)))

        for i in range(LANES + 1):
            lx  = i / LANES
            fa  = _project(lx, 0.0); na = _project(lx, 1.0)
            col = MEMBER_COLORS[i % len(MEMBER_COLORS)]
            cv.create_line(fa[0], fa[1], na[0], na[1],
                           fill=dim(col, brightness * 0.15), width=max(2, int(4 * lx)))
            cv.create_line(fa[0], fa[1], na[0], na[1],
                           fill=dim(col, brightness * 0.40), width=1)

        pls = 0.65 + 0.35 * math.sin(self.t * 4.2)
        bar_b = min(1.0, brightness + 0.15)
        hl = _project(0, _HIT_DEPTH); hr = _project(1, _HIT_DEPTH)
        cv.create_line(hl[0], hl[1], hr[0], hr[1],
                       fill=dim("#FFD700", pls * bar_b * 0.30), width=11)
        cv.create_line(hl[0], hl[1], hr[0], hr[1],
                       fill=dim("#FFD700", pls * bar_b * 0.55), width=5)
        cv.create_line(hl[0], hl[1], hr[0], hr[1],
                       fill=dim("#FFFFFF", pls * bar_b * 0.85), width=2)

    # ── Game update — delegates to game_logic module ─────────────────
    def _update_game(self, dt):
        """
        Advance all gameplay systems by one frame.

        Calls game_logic.update_game() for pure mechanics (note spawning,
        depth advance, auto-miss, particle step/cull).  Then checks the
        gs["advancing"] flag that update_game() sets on song-end detection,
        and calls _advance_song() or _end_game() as appropriate.
        These UI-level callbacks cannot live in game_logic because they
        need access to tkinter's after() system.
        """
        update_game(
            self.gs, dt, audio,
            self.particles, self.sparks, self.flashes, self.side_effects,
            _W, _H, _project, _HIT_DEPTH
        )
        # Song-end flag set by update_game when audio.is_busy() → False
        if self.gs.get("advancing") and not self.gs.get("ended"):
            if self.gs["endless"]:
                self._advance_song()
            else:
                self._end_game()

    # ── Game drawing — delegates to game_renderer module ─────────────
    def _draw_game_banner_below_hud(self, cv):
        """
        Draw the during-game photo banner BELOW the HUD title box so it
        is always fully visible and never hidden behind the song-title panel.
        The HUD box spans 14 px top margin + 58 px height; add an 8 px gap.
        """
        ph = self.img_refs.get("game_banner")
        if not ph:
            return
        # Position the banner starting just below the HUD song-title box
        banner_top = 14 + 58 + 8
        bw, bh = self.img_refs.get("game_banner_size", (ph.width(), ph.height()))
        banner_cx = _W // 2
        banner_cy = banner_top + bh // 2
        try:
            cv.create_image(banner_cx, banner_cy, image=ph, anchor="center")
        except Exception:
            pass

    def _draw_game(self, cv):
        """
        Render the full gameplay scene onto the Canvas.
        Calls each game_renderer function in the correct draw order:
        side effects → combo panel → notes → lane targets →
        particles → sparks → flashes → countdown → combo burst →
        pause overlay → loading bar → HUD → banner below HUD.

        NOTE: draw_game_banner() from game_renderer is replaced by
        _draw_game_banner_below_hud() which places the photo beneath
        the HUD song-title box rather than hidden behind it.
        """
        gs    = self.gs
        LANES = gs.get("num_lanes", 5)

        # Banner is drawn AFTER the HUD further below — skip the original call

        # Screen-edge burst panels (PERFECT / COMBO / MISS)
        draw_side_effects(cv, self.side_effects, self.t, _W, _H)

        # Brief combo-cheer panel at milestones (combo ≥ 10)
        cf_val = gs.get("combo_flash", 0)
        if gs["combo"] >= 10 and cf_val > 0:
            panel_alpha = min(0.45, cf_val * 0.5)
            panel_col   = "#FF3385" if gs["combo"] >= 50 else "#FFD700"
            draw_combo_panel(cv, "left",  gs["combo"], panel_col, panel_alpha, self.t, _W, _H)
            draw_combo_panel(cv, "right", gs["combo"], panel_col, panel_alpha, self.t, _W, _H)

        # Notes — on top of all background effects
        draw_notes(cv, gs["notes"], self.t, LANES, _project)

        # Lane hit-zone targets at the hit bar
        draw_lane_targets(cv, self.t, LANES, self.lane_lit, _project, _HIT_DEPTH)

        # Hit burst particles and PERFECT spark flares
        draw_particles(cv, self.particles)
        draw_sparks(cv, self.sparks)

        # Floating feedback text ("PERFECT!", "+300", "MISS", etc.)
        draw_flashes(cv, self.flashes)

        # Pre-song countdown 3…2…1…GO!
        draw_countdown(cv, gs, self.t, _W, _H, self.sy)

        # "★ Nx COMBO!" burst text below the track
        draw_combo_burst(cv, gs, _W, _H, self.sy)

        # SPACE-key pause overlay (suppressed when in-game menu is open)
        draw_pause_overlay(cv, gs, self._game_volume, self._overlay_open, _W, _H, self.sx, self.sy)

        # Beat-analysis loading indicator
        draw_loading_bar(cv, gs, _W, _H, self.sx)

        # HUD — drawn before the banner so the song-title box is fully visible
        draw_hud(
            cv, gs, self.t, cfg, audio,
            self.img_refs, self._game_volume,
            _W, _H, self.sx, self.sy,
            _HIT_DEPTH,
            find_cover, PIL_OK, self._load_cover_image
        )

        # Banner photo — drawn AFTER the HUD so it sits below the song-title
        # box and is never obscured by it; _draw_game_banner_below_hud() places
        # it at y = HUD_top (14) + HUD_height (58) + gap (8) = 80 px from top.
        self._draw_game_banner_below_hud(cv)
    # ── HUD helpers ───────────────────────────────────────────────────
    def _draw_side_panel(self, cv, side, combo, col, alpha):
        if alpha <= 0: return

        x = int(self.sx(65)) if side == "left" else _W - int(self.sx(65))

        cheers = ["FIRE!", "ACE!", "PERFECT!", "WOW!", "BGYO!", "ACES!"]
        cheer  = cheers[combo % len(cheers)]
        # Sit just above the label stack which is centered on _H//2
        cheer_y = _H // 2 - 90

        # Colour cycling
        _cheer_cols = ["#FFD700", "#FF3385", "#00FF99", "#00E5FF", "#FF8800", "#CC44FF"]
        cp   = (self.t * 3.5) % 1.0
        ci0  = int(cp * len(_cheer_cols)) % len(_cheer_cols)
        ci1  = (ci0 + 1) % len(_cheer_cols)
        cf   = (cp * len(_cheer_cols)) - ci0
        cyc  = blend(_cheer_cols[ci0], _cheer_cols[ci1], cf)

        # Outer neon glow
        cv.create_text(x, cheer_y, text=cheer,
                       fill=dim(cyc, alpha * 0.28),
                       font=(TITLE_FONT, 30, "bold"), anchor="center")
        # Drop shadow
        cv.create_text(x + 2, cheer_y + 2, text=cheer,
                       fill=dim("#000000", alpha * 0.80),
                       font=(TITLE_FONT, 26, "bold"), anchor="center")
        # Main vivid text
        cv.create_text(x, cheer_y, text=cheer,
                       fill=dim(cyc, alpha * 0.95),
                       font=(TITLE_FONT, 26, "bold"), anchor="center")

        # Orbiting stars
        if alpha > 0.3:
            for star_i in range(6):
                angle     = (self.t * 2.2 + star_i * (2 * math.pi / 6)) % (2 * math.pi)
                star_dist = 50 + 10 * math.sin(self.t * 3 + star_i)
                star_x    = x        + math.cos(angle) * star_dist
                star_y    = cheer_y  + math.sin(angle) * star_dist * 0.45
                star_size = 7 + 4 * abs(math.sin(self.t * 4 + star_i))
                star_col  = _cheer_cols[(ci0 + star_i) % len(_cheer_cols)]
                self._draw_star5(cv, star_x, star_y, star_size,
                                 dim(star_col, alpha * (0.5 + 0.4 * abs(math.sin(self.t * 5 + star_i)))))

    def _draw_star5(self, cv, cx, cy, r, col):
        """5-pointed star polygon — delegates to ui_helpers.draw_star5()."""
        draw_star5(cv, cx, cy, r, col)

    def _draw_hud_panel_box(self, cv, x1, y1, x2, y2):
        # Translucent dark bg — blends with stage rather than blocking it
        cv.create_rectangle(x1-1, y1-1, x2+1, y2+1, fill="#02000a", outline=dim("#FFD700", 0.22), width=2)
        cv.create_rectangle(x1,   y1,   x2,   y2,   fill="#04001a", outline=dim("#FFD700", 0.70), width=1)
        for cx_, cy_ in [(x1, y1), (x2, y1), (x1, y2), (x2, y2)]:
            cv.create_oval(cx_-2, cy_-2, cx_+2, cy_+2, fill=dim("#FFD700", 0.80), outline="")

    def _draw_ellipse_glow(self, cv, cx, cy, rx, ry, hex_col, layers=6, max_alpha=0.5):
        """Concentric ellipse glow — delegates to ui_helpers.draw_ellipse_glow()."""
        draw_ellipse_glow(cv, cx, cy, rx, ry, hex_col, layers, max_alpha)

    def _hud_panel(self, cv):
        gs    = self.gs
        LANES = gs.get("num_lanes", 5)

        # Progress bar
        if gs.get("song_duration") and gs["song_duration"] > 0:
            prog = min(1.0, audio.position() / gs["song_duration"]) if gs.get("song_wall_start") else 0.0
        else:
            prog = 0.0
        # Semi-transparent progress track (no opaque black rectangle)
        cv.create_rectangle(0, 0, _W, 7, fill="#0a0020", outline="")
        pr = _clamp(255 * prog); pg = _clamp(215 * (1 - prog))
        cv.create_rectangle(0, 0, _W * prog, 7, fill=f"#{pr:02x}{pg:02x}00")

        # Score (left) — subtle glowing border box
        _sc_pulse = 0.45 + 0.25 * math.sin(self.t * 2.1)
        cv.create_rectangle(int(self.sx(11)), 13, int(self.sx(193)), 77,
                            fill="", outline=dim("#FFD700", _sc_pulse * 0.40), width=2)
        cv.create_rectangle(int(self.sx(12)), 14, int(self.sx(192)), 76,
                            fill="#030010", outline=dim("#FFD700", _sc_pulse * 0.70), width=1)
        cv.create_text(int(self.sx(22)), 22, text="SCORE", fill=dim("#FFD700", 0.75),
                       anchor="w", font=(UI_FONT, 9, "bold"))
        cv.create_text(int(self.sx(23)), 51, text=f"{gs['score']:,}", fill="#000000",
                       anchor="w", font=(MONO_FONT, 21, "bold"))
        cv.create_text(int(self.sx(22)), 50, text=f"{gs['score']:,}", fill="#FFFFFF",
                       anchor="w", font=(MONO_FONT, 21, "bold"))

        # Center HUD — song title LED scrolling effect + cover art
        names    = gs.get("song_names", []); idx = gs.get("song_idx", 0)
        cur_song = names[idx % max(1, len(names))] if names else ""
        song_lbl = cur_song.upper() if cur_song else "—"

        hud_cx   = _W // 2
        cover_size = 48
        box_w    = 400; box_h = 58
        hud_box_x1 = hud_cx - box_w // 2; hud_box_x2 = hud_cx + box_w // 2

        # LED-cycling border colour (matches title text cycling)
        _hb_colors = MEMBER_COLORS
        _hb_phase  = (self.t * 0.55) % 1.0
        _hb_i0     = int(_hb_phase * len(_hb_colors)) % len(_hb_colors)
        _hb_i1     = (_hb_i0 + 1) % len(_hb_colors)
        _hb_frac   = (_hb_phase * len(_hb_colors)) - int(_hb_phase * len(_hb_colors))
        hud_border = blend(_hb_colors[_hb_i0], _hb_colors[_hb_i1], _hb_frac)
        hud_pulse  = 0.55 + 0.30 * math.sin(self.t * 3.0)

        # Dark background with glowing border
        cv.create_rectangle(hud_box_x1 - 1, 13, hud_box_x2 + 1, 14 + box_h + 1,
                            fill="", outline=dim(hud_border, hud_pulse * 0.45), width=2)
        cv.create_rectangle(hud_box_x1, 14, hud_box_x2, 14 + box_h,
                            fill="#030010", outline=dim(hud_border, hud_pulse * 0.80), width=1)

        # Cover image — left side of box
        cover_x = hud_box_x1 + 8 + cover_size // 2
        cover_y = 14 + box_h // 2
        if cur_song:
            hud_key = f"hud_cover_{cur_song}"
            if hud_key not in self.img_refs:
                cp = find_cover(cur_song)
                if cp and PIL_OK:
                    try:
                        im = Image.open(cp).convert("RGBA")
                        im = im.resize((cover_size, cover_size), Image.Resampling.LANCZOS)
                        self.img_refs[hud_key] = ImageTk.PhotoImage(im)
                    except Exception:
                        self.img_refs[hud_key] = self._load_cover_image(cur_song, cover_size)
                else:
                    self.img_refs[hud_key] = self._load_cover_image(cur_song, cover_size)
            ph = self.img_refs.get(hud_key)
            if ph:
                try: cv.create_image(cover_x, cover_y, image=ph, anchor="center")
                except Exception: pass

        # LED scrolling song title — colour cycles through member colours
        text_area_x  = hud_box_x1 + cover_size + 18
        text_area_cx = (text_area_x + hud_box_x2) // 2
        text_area_w  = hud_box_x2 - text_area_x - 6

        # Cycle through member colours for the LED effect
        _led_colors = MEMBER_COLORS
        _lc_phase   = (self.t * 0.55) % 1.0
        _lc_i0      = int(_lc_phase * len(_led_colors)) % len(_led_colors)
        _lc_i1      = (_lc_i0 + 1) % len(_led_colors)
        _lc_frac    = (_lc_phase * len(_led_colors)) - int(_lc_phase * len(_led_colors))
        led_col     = blend(_led_colors[_lc_i0], _led_colors[_lc_i1], _lc_frac)

        # Shadow then coloured title — auto-fit font size
        title_font_sz = 13
        cv.create_text(text_area_cx + 1, 34 + 1, text=song_lbl,
                       fill=dim("#000000", 0.80),
                       font=(UI_FONT, title_font_sz, "bold"), anchor="center",
                       width=text_area_w)
        cv.create_text(text_area_cx, 34, text=song_lbl,
                       fill=led_col,
                       font=(UI_FONT, title_font_sz, "bold"), anchor="center",
                       width=text_area_w)

        # Status indicator — keep only ⟳ ANALYSING and ◌ NO AUDIO (remove BEAT SYNC / PLAYING)
        bm_col, bm_txt = (
            ("#FF8800", "⟳ ANALYSING") if gs.get("loading") else
            ("#00E5FF", "♪ PLAYING")    if gs.get("song_wall_start") else
            ("#555555", "◌ NO AUDIO")
        )
        cv.create_text(text_area_cx, 58, text=bm_txt, fill=bm_col,
                       font=(UI_FONT, 8, "bold"), anchor="center")

        # Combo (right) — subtle glowing border box
        _cb_pulse = 0.45 + 0.25 * math.sin(self.t * 2.1 + 1.0)
        cv.create_rectangle(_W - int(self.sx(193)), 13, _W - int(self.sx(11)), 77,
                            fill="", outline=dim("#00E5FF", _cb_pulse * 0.40), width=2)
        cv.create_rectangle(_W - int(self.sx(192)), 14, _W - int(self.sx(12)), 76,
                            fill="#030010", outline=dim("#00E5FF", _cb_pulse * 0.70), width=1)
        cv.create_text(_W - int(self.sx(22)), 22, text="COMBO", fill=dim("#FFD700", 0.75),
                       anchor="e", font=(UI_FONT, 9, "bold"))
        cv.create_text(_W - int(self.sx(21)), 51, text=str(gs["combo"]), fill="#000000",
                       anchor="e", font=(MONO_FONT, 21, "bold"))
        cv.create_text(_W - int(self.sx(22)), 50, text=str(gs["combo"]), fill="#FFFFFF",
                       anchor="e", font=(MONO_FONT, 21, "bold"))

        # Member / difficulty — positioned well below the stage hit bar
        m   = gs["member_idx"] % len(MEMBER_NAMES)
        col = MEMBER_COLORS[m]; pls = 0.70 + 0.30 * math.sin(self.t * 2.4)
        cv.create_text(_W // 2 + 2, _H - int(self.sy(52)) + 2, text=MEMBER_NAMES[m],
                       fill=dim("#000000", 0.85), font=(TITLE_FONT, 24, "bold"))
        cv.create_text(_W // 2, _H - int(self.sy(52)), text=MEMBER_NAMES[m],
                       fill=dim(col, pls), font=(TITLE_FONT, 24, "bold"))
        cv.create_text(_W // 2 + 1, _H - int(self.sy(28)) + 1, text=MEMBER_ROLES[m],
                       fill=dim("#000000", 0.75), font=(UI_FONT, 9))
        cv.create_text(_W // 2, _H - int(self.sy(28)), text=MEMBER_ROLES[m],
                       fill="#00E5FF",     font=(UI_FONT, 9))

        dc = {"Easy": "#00FF99", "Normal": "#00E5FF", "Hard": "#FFD700", "ACE": "#FF3385"}
        cv.create_text(_W-14, _H-50, text=cfg.difficulty, anchor="se",
                       fill=dc.get(cfg.difficulty, "#fff"), font=(UI_FONT, 10, "bold"))

        lbl_map = {5: "5 LANES  D·F·J·K·L", 4: "4 LANES  F·J·K·L", 3: "3 LANES  F·J·L"}
        cv.create_text(_W-14, _H-32, text=lbl_map.get(LANES, ""), anchor="se",
                       fill="#556677", font=(UI_FONT, 8, "bold"))

        cv.create_text(14, _H-50, text="SPACE  Pause", anchor="sw",
                       fill="#445566", font=(UI_FONT, 8, "bold"))
        cv.create_text(14, _H-32, text=f"♪ {int(self._game_volume*100)}%   +/- Vol", anchor="sw",
                       fill="#445566", font=(UI_FONT, 8, "bold"))

        # Perfect / Good / Miss counters — glowing border box, higher for visibility
        pgm_y     = int(self.sy(88))
        pgm_lbl_y = pgm_y
        pgm_val_y = pgm_y + 17
        _pgm_pulse = 0.40 + 0.20 * math.sin(self.t * 1.8 + 0.5)
        cv.create_rectangle(11, pgm_y - 5, int(self.sx(193)), pgm_val_y + 16,
                            fill="", outline=dim("#FF3385", _pgm_pulse * 0.40), width=2)
        cv.create_rectangle(12, pgm_y - 4, int(self.sx(192)), pgm_val_y + 15,
                            fill="#030010", outline=dim("#FF3385", _pgm_pulse * 0.65), width=1)
        for xi, lbl, val, fc in [
            (20,  "PERFECT", str(gs["perfect"]), "#FFD700"),
            (80,  "GOOD",    str(gs["good"]),    "#00E5FF"),
            (140, "MISS",    str(gs["miss"]),    "#FF3385"),
        ]:
            cv.create_text(xi, pgm_lbl_y, text=lbl, fill=dim(fc, 0.85), anchor="w", font=(UI_FONT, 8, "bold"))
            cv.create_text(xi+1, pgm_val_y+1, text=val, fill="#000000", anchor="w", font=(MONO_FONT, 13, "bold"))
            cv.create_text(xi,   pgm_val_y,   text=val, fill="#FFFFFF",  anchor="w", font=(MONO_FONT, 13, "bold"))

    # ── LED song ticker (title screen) ───────────────────────────────
    def _draw_led_ticker(self, cv, dt):
        """Moving LED-style song list displayed below the nav buttons on the home screen."""
        songs = cfg.songs if cfg.songs else db.DEFAULT_SONGS
        # Colour-cycle through MEMBER_COLORS for each song name
        parts = []
        for i, s in enumerate(songs):
            col = MEMBER_COLORS[i % len(MEMBER_COLORS)]
            parts.append((f"  ♪  {s.upper()}", col))

        # Approximate pixel width of the full ticker (mono ~7px per char at font size 9)
        CHAR_W  = 7.2
        full_text = "".join(p[0] for p in parts) + "  ♪  "
        tw       = len(full_text) * CHAR_W

        self.ticker_x -= dt * 60   # scroll speed (px/s)
        if self.ticker_x < -tw:
            self.ticker_x = 0.0

        ticker_h  = 26
        # Pin the ticker to the very bottom of the screen regardless of button position
        ticker_y2 = _H
        ticker_y1 = _H - ticker_h

        # Background bar
        cv.create_rectangle(0, ticker_y1, _W, ticker_y2,
                            fill="#06001A", outline="")
        # Top edge: a 2-px colour-cycling glow separator line for a clean visual break
        _tp = (self.t * 0.45) % 1.0
        _ti0 = int(_tp * len(MEMBER_COLORS)) % len(MEMBER_COLORS)
        _ti1 = (_ti0 + 1) % len(MEMBER_COLORS)
        _tf  = (_tp * len(MEMBER_COLORS)) - _ti0
        edge_col = blend(MEMBER_COLORS[_ti0], MEMBER_COLORS[_ti1], _tf)
        # Draw two lines: a dim wide one for glow, a bright thin one on top
        cv.create_line(0, ticker_y1,     _W, ticker_y1,     fill=dim(edge_col, 0.30), width=3)
        cv.create_line(0, ticker_y1,     _W, ticker_y1,     fill=dim(edge_col, 0.75), width=1)

        cy = (ticker_y1 + ticker_y2) // 2

        # Draw two copies to fill screen seamlessly
        for rep_offset in (0, 1):
            x = self.ticker_x + rep_offset * tw
            for seg_text, seg_col in parts:
                seg_w = len(seg_text) * CHAR_W
                cx_seg = x + seg_w / 2
                if cx_seg + seg_w < 0 or cx_seg - seg_w > _W:
                    x += seg_w
                    continue
                # Pulse brightness per segment
                pulse = 0.65 + 0.30 * abs(math.sin(self.t * 1.8 + abs(hash(seg_text)) * 0.01))
                # Shadow
                cv.create_text(x + 1, cy + 1, text=seg_text,
                               fill=dim("#000000", 0.7), anchor="w",
                               font=(UI_FONT, 9, "bold"))
                # Coloured text
                cv.create_text(x, cy, text=seg_text,
                               fill=dim(seg_col, pulse), anchor="w",
                               font=(UI_FONT, 9, "bold"))
                x += seg_w


# ═══════════════════════════════════════════════════════════════
#  ENTRY POINT
# ═══════════════════════════════════════════════════════════════
if __name__ == "__main__":
    BGYOGame()