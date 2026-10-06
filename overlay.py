"""
overlay.py  ─  Task 3: External Graphical Overlay
──────────────────────────────────────────────────
Creates a fully transparent, always-on-top, click-through overlay window
using pygame + win32gui. Draws ESP boxes over tracked entities without
stealing focus from the target process.

Window flags used:
  WS_EX_LAYERED   → supports per-pixel / colorkey transparency
  WS_EX_TOPMOST   → always above other windows
  WS_EX_TOOLWINDOW→ hidden from taskbar / Alt-Tab
  WS_EX_TRANSPARENT (click-through)  →  mouse clicks pass through to CS2

Requirements:
    pip install pygame pywin32

Architecture:
    ┌─────────────┐     reads memory      ┌──────────────┐
    │  CS2 process│ ◄──────────────────── │ memory_reader│
    └─────────────┘                       └──────┬───────┘
           │                                     │ PlayerSnapshot list
           │ renders to screen                   ▼
           │                              ┌──────────────┐
           ▼                              │ world_to_     │
    ┌─────────────┐   overlay draws over  │ screen.py     │
    │ CS2 window  │ ◄──────────────────── └──────┬───────┘
    └─────────────┘                              │ Vector2 (pixels)
                                                 ▼
                                          ┌──────────────┐
                                          │ overlay.py   │
                                          │ (pygame SDL)  │
                                          └──────────────┘
"""

from __future__ import annotations

import os
import sys
import math
import time
import ctypes
import ctypes.wintypes

# Prevent SDL2 from minimizing overlay window when switching focus to CS2
os.environ["SDL_VIDEO_MINIMIZE_ON_FOCUS_LOSS"] = "0"

if sys.platform == "win32":
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # Per-monitor DPI aware
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass

    try:
        _u32 = ctypes.windll.user32
        _hdesk = _u32.OpenDesktopW("Default", 0, False, 0x01FF)
        if _hdesk:
            _u32.SetThreadDesktop(_hdesk)
    except Exception:
        pass

import pygame

# ── Windows API imports ───────────────────────────────────────────────────────
try:
    import win32gui
    import win32con
    import win32api
except ImportError:
    raise SystemExit(
        "pywin32 required:  pip install pywin32\n"
        "This module only works on Windows."
    )

# ── Project imports ────────────────────────────────────────────────────────────
from world_to_screen import Vector2


# ══════════════════════════════════════════════════════════════════════════════
#  Win32 extended-style constants
# ══════════════════════════════════════════════════════════════════════════════

GWL_EXSTYLE       = -20

# These flags together create the transparent, click-through, topmost window
WS_EX_LAYERED     = 0x00080000   # enables transparency / colorkey
WS_EX_TRANSPARENT = 0x00000020   # mouse events fall through to window below
WS_EX_TOPMOST     = 0x00000008   # always renders above all other windows
WS_EX_TOOLWINDOW  = 0x00000080   # hidden from Alt-Tab and taskbar
WS_EX_NOACTIVATE  = 0x08000000   # prevents window from taking focus or being deactivated

# SetLayeredWindowAttributes — colorkey mode
LWA_COLORKEY      = 0x00000001   # treat one colour as fully transparent

# The "magic" colour we use as the transparency mask.
# Anything drawn in this colour becomes invisible (passes mouse through too).
TRANSPARENT_COLOR = (0, 0, 0)    # pure black  → transparent pixel


# ══════════════════════════════════════════════════════════════════════════════
#  Visual design constants
# ══════════════════════════════════════════════════════════════════════════════

COLOR_BOX_ALLY    = (0, 0, 0)    # green  — same team
COLOR_BOX_ENEMY   = (220, 0,  0)   # red    — enemy
COLOR_BOX_T       = (235, 165,  30)   # gold/orange — Terrorist (T)
COLOR_BOX_CT      = (50,  160, 255)   # cyan/blue   — Counter-Terrorist (CT)
COLOR_HEALTH_BG   = (50,  50,  50)   # dark grey health bar background
COLOR_HEALTH_FG   = (0,  210,  80)   # bright green health fill
COLOR_TEXT        = (255, 255, 255)  # white labels
COLOR_SKELETON    = (255, 200,  0)   # yellow bone lines

BOX_WIDTH         = 50   # pixels wide  (ESP bounding box)
BOX_HEIGHT        = 90   # pixels tall
BOX_THICKNESS     = 2    # line width in pixels
HEALTH_BAR_W      = 6    # health bar width in pixels
FONT_SIZE         = 13


# ══════════════════════════════════════════════════════════════════════════════
#  Overlay class
# ══════════════════════════════════════════════════════════════════════════════

class Overlay:
    """
    A transparent, always-on-top pygame window positioned over the game.

    How transparency works
    ──────────────────────
    1.  pygame creates a normal SDL window.
    2.  We grab the HWND (Win32 window handle) via win32gui.FindWindow.
    3.  We apply extended window styles via SetWindowLong:
            WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOPMOST | WS_EX_TOOLWINDOW
    4.  SetLayeredWindowAttributes with LWA_COLORKEY makes our TRANSPARENT_COLOR
        (pure black) completely invisible AND passes all mouse messages through.
    5.  Every frame we fill the surface with TRANSPARENT_COLOR, then draw only
        the ESP elements on top.  Only drawn pixels are visible to the user.
    6.  Because of WS_EX_TRANSPARENT, the underlying CS2 window receives all
        keyboard and mouse input — the overlay NEVER steals focus.
    """

    def __init__(self, screen_w: int = 1920, screen_h: int = 1080,
                 target_window_title: str = "Counter-Strike 2"):
        self.screen_w = screen_w
        self.screen_h = screen_h
        self.target_title = target_window_title
        self._hwnd: int = 0

        pygame.init()
        pygame.display.set_caption("Telemetry Overlay")

        # Create a borderless window the size of the screen.
        # NOFRAME removes the title bar.  SHOWN makes it visible immediately.
        self.screen = pygame.display.set_mode(
            (screen_w, screen_h),
            pygame.NOFRAME | pygame.SHOWN,
        )

        self.font       = pygame.font.SysFont("Arial", FONT_SIZE, bold=True)
        self.font_small = pygame.font.SysFont("Arial", FONT_SIZE - 2)

        self._apply_win32_styles()
        self._position_over_target()

    # ── Win32 setup ─────────────────────────────────────────────────────────

    def _apply_win32_styles(self) -> None:
        """
        Find the pygame SDL window and apply extended styles that make
        it transparent, non-activating, click-through, and permanently topmost.
        """
        hwnd = 0
        try:
            hwnd = pygame.display.get_wm_info().get("window", 0)
        except Exception:
            pass

        if not hwnd:
            pygame.display.set_caption("__overlay__")           # temporary unique title
            time.sleep(0.1)                                      # let Windows register it
            hwnd = win32gui.FindWindow(None, "__overlay__")
            pygame.display.set_caption("Telemetry Overlay")     # restore caption

        if hwnd == 0:
            raise RuntimeError("Could not find overlay HWND. Win32 setup failed.")
        self._hwnd = hwnd

        # Read current extended style bits
        ex_style = win32gui.GetWindowLong(hwnd, GWL_EXSTYLE)

        # Combine our required flags with OR
        new_ex_style = (ex_style
                        | WS_EX_LAYERED
                        | WS_EX_TRANSPARENT    # click-through: passes mouse events down
                        | WS_EX_TOPMOST        # always on top
                        | WS_EX_TOOLWINDOW     # hidden from taskbar / Alt-Tab
                        | WS_EX_NOACTIVATE)    # never steal or lose focus

        # Apply the extended style
        win32gui.SetWindowLong(hwnd, GWL_EXSTYLE, new_ex_style)

        # Tell Windows which colour to treat as transparent (colorkey mode).
        # LWA_COLORKEY: any pixel matching TRANSPARENT_COLOR is fully invisible
        # and lets mouse messages pass through at that pixel.
        win32gui.SetLayeredWindowAttributes(
            hwnd,
            win32api.RGB(*TRANSPARENT_COLOR),   # the "invisible" colour
            0,                                   # alpha (unused in colorkey mode)
            LWA_COLORKEY,
        )

        # Explicitly apply HWND_TOPMOST so DWM places the overlay above other windows
        win32gui.SetWindowPos(
            hwnd, win32con.HWND_TOPMOST,
            0, 0, self.screen_w, self.screen_h,
            win32con.SWP_SHOWWINDOW | win32con.SWP_NOACTIVATE | win32con.SWP_FRAMECHANGED,
        )
        print(f"[+] Overlay HWND: {hex(hwnd)}  |  WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_NOACTIVATE | HWND_TOPMOST applied")

    def _position_over_target(self) -> None:
        """
        Find the CS2 window and snap the overlay exactly on top of it.
        This handles multi-monitor setups where CS2 may not be at (0,0).
        """
        target_hwnd = win32gui.FindWindow("SDL_app", self.target_title)
        if target_hwnd == 0:
            target_hwnd = win32gui.FindWindow(None, self.target_title)

        if target_hwnd != 0:
            client_rect = win32gui.GetClientRect(target_hwnd)
            top_left = win32gui.ClientToScreen(target_hwnd, (0, 0))
            x, y = top_left[0], top_left[1]
            w    = client_rect[2] - client_rect[0]
            h    = client_rect[3] - client_rect[1]
            if w > 200 and h > 200:
                self.screen_w, self.screen_h = w, h
                win32gui.SetWindowPos(
                    self._hwnd, win32con.HWND_TOPMOST,
                    x, y, w, h,
                    win32con.SWP_SHOWWINDOW | win32con.SWP_NOACTIVATE,
                )
                print(f"[+] Overlay positioned over '{self.target_title}' at ({x},{y}) size {w}×{h}")
                return

        print(f"[!] Target window '{self.target_title}' not found — using full display ({self.screen_w}x{self.screen_h})")
        win32gui.SetWindowPos(
            self._hwnd, win32con.HWND_TOPMOST,
            0, 0, self.screen_w, self.screen_h,
            win32con.SWP_SHOWWINDOW | win32con.SWP_NOACTIVATE,
        )

    # ── Drawing helpers ─────────────────────────────────────────────────────

    def _draw_box(self, sx: int, sy: int, color: tuple, w: int = BOX_WIDTH, h: int = BOX_HEIGHT) -> None:
        """Draw a rectangle box centred at (sx, sy)."""
        rect = pygame.Rect(
            sx - w // 2,
            sy - h // 2,
            w,
            h,
        )
        pygame.draw.rect(self.screen, color, rect, BOX_THICKNESS)

    def _draw_corner_box(self, sx: int, sy: int, color: tuple,
                         w: int = BOX_WIDTH, h: int = BOX_HEIGHT) -> None:
        """
        Draw a corner-bracket style ESP box (looks cleaner than full rectangle).
        Only draws the corners — 1/4 of each edge.
        """
        x, y = sx - w // 2, sy - h // 2
        cw, ch = max(4, w // 4), max(4, h // 4)       # corner length

        lines = [
            # top-left
            ((x, y), (x + cw, y)),
            ((x, y), (x, y + ch)),
            # top-right
            ((x + w - cw, y), (x + w, y)),
            ((x + w, y), (x + w, y + ch)),
            # bottom-left
            ((x, y + h - ch), (x, y + h)),
            ((x, y + h), (x + cw, y + h)),
            # bottom-right
            ((x + w, y + h - ch), (x + w, y + h)),
            ((x + w - cw, y + h), (x + w, y + h)),
        ]
        for start, end in lines:
            pygame.draw.line(self.screen, color, start, end, BOX_THICKNESS)

    def _draw_stylized_skeleton(self, sx: int, sy: int, color: tuple,
                                w: int = BOX_WIDTH, h: int = BOX_HEIGHT, thickness: int = 1,
                                is_crouching: bool = False, aspect_angle_deg: float = 180.0) -> None:
        """
        Renders an anatomical stick-figure (omino stilizzato) inside the ESP box:
          - 1 circle for the head
          - 1 line for the torso (neck -> pelvis)
          - 2 lines per arm (upper arm -> forearm)
          - 2 lines per leg (thigh -> shin)
        Dynamically transforms according to standing vs crouching posture and the
        3D observation perspective (aspect_angle_deg: front, profile, back, or 3/4).
        """
        if h < 15 or w < 8:
            return

        top = sy - h // 2
        bottom = sy + h // 2
        line_w = max(1, thickness)

        rad = math.radians(aspect_angle_deg)
        sin_a = math.sin(rad)
        cos_a = math.cos(rad)
        w_scale = w * 0.5

        def proj(xf: float, yr: float, zd: float) -> tuple[int, int]:
            """Projects 3D normalized model coordinates (forward, right, down) to 2D screen."""
            px = int(sx + (xf * sin_a + yr * cos_a) * w_scale)
            py = int(top + zd * h)
            # Clamp to prevent SDL line rasterization wrapping/flickering
            px = max(-100, min(self.screen_w + 100, px))
            py = max(-100, min(self.screen_h + 100, py))
            return px, py

        if not is_crouching:
            # ── Standing posture ─────────────────────────────────────────────
            head_cx = int(sx + 0.04 * sin_a * w_scale)
            head_cy = top + max(2, int(h * 0.09))
            head_radius = max(2, min(min(w // 6, int(h * 0.05)), 40))

            # Torso: 1 single segment from neck to pelvis
            torso_top = proj(0.02, 0.0, 0.17)
            torso_bot = proj(-0.02, 0.0, 0.52)

            # Left Arm (2 segments): shoulder/neck -> elbow -> hand
            elbow_l = proj(0.12, -0.62, 0.35)
            hand_l  = proj(0.35, -0.22, 0.45)

            # Right Arm (2 segments): shoulder/neck -> elbow -> hand
            elbow_r = proj(0.08, 0.62, 0.37)
            hand_r  = proj(0.30, 0.18, 0.45)

            # Left Leg (2 segments): pelvis -> knee -> foot
            knee_l = proj(0.06, -0.42, 0.74)
            foot_l = proj(0.10, -0.38, 0.98)

            # Right Leg (2 segments): pelvis -> knee -> foot
            knee_r = proj(-0.06, 0.42, 0.74)
            foot_r = proj(-0.10, 0.38, 0.98)

        else:
            # ── Crouching posture (abbassato / squatting) ───────────────────
            # Lower center of gravity, forward torso tilt, flexed knees
            head_cx = int(sx + 0.22 * sin_a * w_scale)
            head_cy = top + max(2, int(h * 0.14))
            head_radius = max(2, min(min(w // 6, int(h * 0.05)), 40))

            # Torso: 1 single segment leaning forward in squat
            torso_top = proj(0.18, 0.0, 0.24)
            torso_bot = proj(-0.22, 0.0, 0.54)

            # Left Arm (2 segments): arms angled forward toward knees
            elbow_l = proj(0.30, -0.65, 0.44)
            hand_l  = proj(0.45, -0.22, 0.56)

            # Right Arm (2 segments):
            elbow_r = proj(0.25, 0.65, 0.46)
            hand_r  = proj(0.40, 0.18, 0.56)

            # Left Leg (2 segments): knee thrusts forward, foot under pelvis
            knee_l = proj(0.45, -0.55, 0.70)
            foot_l = proj(-0.08, -0.32, 0.98)

            # Right Leg (2 segments):
            knee_r = proj(0.40, 0.55, 0.72)
            foot_r = proj(-0.14, 0.32, 0.98)

        # 1. Head circle
        head_cx = max(-100, min(self.screen_w + 100, head_cx))
        head_cy = max(-100, min(self.screen_h + 100, head_cy))
        head_center = (head_cx, head_cy)
        pygame.draw.circle(self.screen, color, head_center, head_radius, line_w)

        # 2. Torso (esattamente 1 linea)
        pygame.draw.line(self.screen, color, torso_top, torso_bot, line_w)

        # 3. Braccia (esattamente 2 linee per braccio)
        pygame.draw.line(self.screen, color, torso_top, elbow_l, line_w)
        pygame.draw.line(self.screen, color, elbow_l, hand_l, line_w)

        pygame.draw.line(self.screen, color, torso_top, elbow_r, line_w)
        pygame.draw.line(self.screen, color, elbow_r, hand_r, line_w)

        # 4. Gambe (esattamente 2 linee per gamba)
        pygame.draw.line(self.screen, color, torso_bot, knee_l, line_w)
        pygame.draw.line(self.screen, color, knee_l, foot_l, line_w)

        pygame.draw.line(self.screen, color, torso_bot, knee_r, line_w)
        pygame.draw.line(self.screen, color, knee_r, foot_r, line_w)

        # Articulated joint markers (discreti ed eleganti)
        if 35 <= h <= int(self.screen_h * 1.2):
            joints = [torso_top, torso_bot, elbow_l, hand_l, elbow_r, hand_r, knee_l, foot_l, knee_r, foot_r]
            for jx, jy in joints:
                if 0 <= jx <= self.screen_w and 0 <= jy <= self.screen_h:
                    pygame.draw.circle(self.screen, (255, 255, 255), (jx, jy), max(1, line_w))

    def _draw_body_contour(self, sx: int, sy: int, color: tuple,
                           w: int = BOX_WIDTH, h: int = BOX_HEIGHT, thickness: int = 2,
                           is_crouching: bool = False, lean_angle_deg: float = 0.0) -> None:
        """
        Draws a continuous outline contour surrounding the entity's body.
        Follows crouching posture and tilts with movement inertia.
        """
        if h < 15 or w < 8:
            return

        top = sy - h // 2
        bottom = sy + h // 2

        if not is_crouching:
            head_r = max(3, int(h * 0.05))
            head_cy = top + head_r + 2
            neck_y = head_cy + head_r + max(1, int(h * 0.015))
            shoulder_y = top + int(h * 0.15)
            shoulder_w = int(w * 0.38)
            elbow_y = top + int(h * 0.36)
            elbow_w = int(w * 0.44)
            wrist_y = top + int(h * 0.50)
            wrist_w = int(w * 0.36)
            waist_y = top + int(h * 0.44)
            waist_w = int(w * 0.24)
            hip_y = top + int(h * 0.54)
            hip_w = int(w * 0.28)
            crotch_y = top + int(h * 0.56)
            crotch_w = int(w * 0.06)
            knee_y = top + int(h * 0.74)
            knee_w = int(w * 0.26)
            ankle_w = int(w * 0.26)
            foot_y = bottom - 2
            foot_w = int(w * 0.28)

            head_pts = []
            for deg in range(180, -1, -25):
                rad = math.radians(deg)
                hx = sx + int(head_r * math.cos(rad))
                hy = head_cy - int(head_r * math.sin(rad))
                head_pts.append((hx, hy))

            right_side = [
                (sx + int(head_r * 0.8), neck_y),
                (sx + int(shoulder_w * 0.5), shoulder_y),
                (sx + shoulder_w, shoulder_y + 4),
                (sx + elbow_w, elbow_y),
                (sx + wrist_w, wrist_y),
                (sx + wrist_w - 5, wrist_y + 6),
                (sx + waist_w, waist_y),
                (sx + hip_w, hip_y),
                (sx + knee_w, knee_y),
                (sx + ankle_w, bottom - max(3, int(h * 0.06))),
                (sx + foot_w, foot_y),
                (sx + int(foot_w * 0.4), foot_y),
                (sx + int(knee_w * 0.35), int((knee_y + crotch_y) / 2)),
                (sx + crotch_w, crotch_y),
                (sx, crotch_y - 4),
            ]

            left_side = [
                (sx - crotch_w, crotch_y),
                (sx - int(knee_w * 0.35), int((knee_y + crotch_y) / 2)),
                (sx - int(foot_w * 0.4), foot_y),
                (sx - foot_w, foot_y),
                (sx - ankle_w, bottom - max(3, int(h * 0.06))),
                (sx - knee_w, knee_y),
                (sx - hip_w, hip_y),
                (sx - waist_w, waist_y),
                (sx - wrist_w + 5, wrist_y + 6),
                (sx - wrist_w, wrist_y),
                (sx - elbow_w, elbow_y),
                (sx - shoulder_w, shoulder_y + 4),
                (sx - int(shoulder_w * 0.5), shoulder_y),
                (sx - int(head_r * 0.8), neck_y),
            ]

            pts = head_pts + right_side + left_side

        else:
            head_r = max(3, int(h * 0.05))
            head_cy = top + head_r + 2
            neck_y = head_cy + head_r + max(1, int(h * 0.015))
            shoulder_y = top + int(h * 0.18)
            shoulder_w = int(w * 0.38)
            elbow_y = top + int(h * 0.40)
            elbow_w = int(w * 0.46)
            knee_y = top + int(h * 0.68)
            knee_w = int(w * 0.46)
            foot_y = bottom - 2
            foot_w = int(w * 0.22)
            crotch_y = top + int(h * 0.52)

            head_pts = []
            for deg in range(180, -1, -30):
                rad = math.radians(deg)
                hx = sx + int(head_r * math.cos(rad))
                hy = head_cy - int(head_r * math.sin(rad))
                head_pts.append((hx, hy))

            pts = head_pts + [
                (sx + int(head_r * 0.8), neck_y),
                (sx + shoulder_w, shoulder_y),
                (sx + elbow_w, elbow_y),
                (sx + int(w * 0.30), top + int(h * 0.52)),
                (sx + knee_w, knee_y),
                (sx + foot_w, foot_y),
                (sx + int(foot_w * 0.3), foot_y),
                (sx, crotch_y),
                (sx - int(foot_w * 0.3), foot_y),
                (sx - foot_w, foot_y),
                (sx - knee_w, knee_y),
                (sx - int(w * 0.30), top + int(h * 0.52)),
                (sx - elbow_w, elbow_y),
                (sx - shoulder_w, shoulder_y),
                (sx - int(head_r * 0.8), neck_y),
            ]

        if abs(lean_angle_deg) > 0.1:
            rad = math.radians(lean_angle_deg)
            cos_a = math.cos(rad)
            sin_a = math.sin(rad)
            pivot_y = bottom
            transformed = []
            for px, py in pts:
                dx = px - sx
                dy = py - pivot_y
                rx = dx * cos_a - dy * sin_a
                ry = dx * sin_a + dy * cos_a
                transformed.append((int(sx + rx), int(pivot_y + ry)))
            pts = transformed

        if len(pts) >= 3:
            pygame.draw.lines(self.screen, color, True, pts, max(1, thickness))

    def _draw_health_bar(self, sx: int, sy: int, health: int, w: int = BOX_WIDTH, h: int = BOX_HEIGHT) -> None:
        """Draw a vertical health bar and health percentage text with an offset to the left of the ESP box."""
        hp = max(0, min(health, 100))
        bar_x = sx - w // 2 - HEALTH_BAR_W - 4
        bar_y = sy - h // 2

        # Background (dark)
        bg_rect = pygame.Rect(bar_x, bar_y, HEALTH_BAR_W, h)
        pygame.draw.rect(self.screen, COLOR_HEALTH_BG, bg_rect)

        # Filled portion (proportional to health / 100)
        fill_h   = int(h * hp / 100)
        fill_y   = bar_y + (h - fill_h)
        fill_rect = pygame.Rect(bar_x, fill_y, HEALTH_BAR_W, fill_h)

        # Colour shifts red→yellow→green based on health
        r = int(255 * (1.0 - hp / 100.0))
        g = int(255 * (hp / 100.0))
        pygame.draw.rect(self.screen, (r, g, 30), fill_rect)

        # Draw health percentage text offset to the left of the health bar
        hp_str = f"{hp}%"
        surf_shadow = self.font_small.render(hp_str, True, (0, 0, 0))
        surf_hp = self.font_small.render(hp_str, True, (r, g, 60))
        text_x = bar_x - surf_hp.get_width() - 3
        text_y = fill_y - surf_hp.get_height() // 2 if h > 25 else bar_y
        text_y = max(bar_y - 2, min(bar_y + h - surf_hp.get_height() + 2, text_y))
        self.screen.blit(surf_shadow, (text_x + 1, text_y + 1))
        self.screen.blit(surf_hp, (text_x, text_y))

    def _draw_label(self, sx: int, sy: int, text: str, h: int = BOX_HEIGHT) -> None:
        """Render a text label above the box."""
        surf = self.font.render(text, True, COLOR_TEXT)
        rect = surf.get_rect(
            center=(sx, sy - h // 2 - FONT_SIZE)
        )
        self.screen.blit(surf, rect)

    def _draw_distance(self, sx: int, sy: int, dist: float, h: int = BOX_HEIGHT) -> None:
        """Render distance label below the box."""
        surf = self.font_small.render(f"{dist:.0f}m", True, (180, 180, 180))
        rect = surf.get_rect(
            center=(sx, sy + h // 2 + FONT_SIZE)
        )
        self.screen.blit(surf, rect)

    def _draw_crosshair(self) -> None:
        """Minimal crosshair at screen centre."""
        cx, cy = self.screen_w // 2, self.screen_h // 2
        pygame.draw.line(self.screen, (255, 255, 255), (cx-10, cy), (cx+10, cy), 1)
        pygame.draw.line(self.screen, (255, 255, 255), (cx, cy-10), (cx, cy+10), 1)

    # ── Main render entry ───────────────────────────────────────────────────

    def render_frame(self, entities: list[dict], hud_lines: list[str] | None = None) -> None:
        """
        Render one overlay frame.
        """
        # Fill with the transparent colour — wipes last frame cleanly
        self.screen.fill(TRANSPARENT_COLOR)

        self._draw_crosshair()

        # Render top-left status HUD so overlay is visibly active
        if hud_lines:
            y = 20
            for line in hud_lines:
                shadow = self.font.render(line, True, (0, 0, 0))
                self.screen.blit(shadow, (21, y + 1))
                surf = self.font.render(line, True, (0, 255, 120))
                self.screen.blit(surf, (20, y))
                y += 20

        for ent in entities:
            sx, sy = int(ent["x"]), int(ent["y"])
            w = int(ent.get("w", BOX_WIDTH))
            h = int(ent.get("h", BOX_HEIGHT))

            # Cull off-screen entities
            if not (-100 <= sx <= self.screen_w + 100 and -100 <= sy <= self.screen_h + 100):
                continue

            color = ent.get("color", COLOR_BOX_ENEMY)
            render_mode = ent.get("render_mode", "skeleton")
            is_crouching = ent.get("is_crouching", False)
            lean_angle = ent.get("lean_angle", 0.0)
            aspect_angle = ent.get("aspect_angle", 180.0)
            skel_color = ent.get("skeleton_color", color)

            self._draw_corner_box(sx, sy, color, w, h)
            if render_mode in ("contour", "both"):
                self._draw_body_contour(sx, sy, skel_color, w, h, thickness=2, is_crouching=is_crouching, lean_angle_deg=lean_angle)
            if render_mode in ("skeleton", "both"):
                self._draw_stylized_skeleton(sx, sy, skel_color, w, h, is_crouching=is_crouching, aspect_angle_deg=aspect_angle)
            self._draw_health_bar(sx, sy, ent.get("health", 100), w, h)
            label = ent.get("name") or ent.get("team_name", "?")
            hp = ent.get("health", 100)
            self._draw_label(sx, sy, f"{label} [{hp}%]", h)
            if "distance" in ent:
                self._draw_distance(sx, sy, ent["distance"], h)

        pygame.display.flip()

        # Reassert HWND_TOPMOST so overlay stays on top when CS2 receives input
        if self._hwnd:
            if win32gui.IsIconic(self._hwnd):
                win32gui.ShowWindow(self._hwnd, win32con.SW_RESTORE)
            win32gui.SetWindowPos(
                self._hwnd, win32con.HWND_TOPMOST,
                0, 0, 0, 0,
                win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_NOACTIVATE | win32con.SWP_SHOWWINDOW
            )

    def process_events(self) -> bool:
        """
        Pump the pygame event queue.
        Returns False when the user closes the overlay (e.g. Alt-F4).
        WS_EX_TRANSPARENT means game keyboard/mouse events never reach here.
        """
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                return False
            if event.type == pygame.KEYDOWN and event.key == pygame.K_F12:
                return False   # F12 = hotkey to close overlay
        return True

    def close(self) -> None:
        pygame.quit()


# ══════════════════════════════════════════════════════════════════════════════
#  Full integration loop (CS2 + memory reader + projection + overlay)
# ══════════════════════════════════════════════════════════════════════════════

def run_overlay(screen_w: int = 1920, screen_h: int = 1080,
                target_fps: int = 60) -> None:
    """
    Main telemetry + overlay loop.

    Imports memory_reader and world_to_screen inline to avoid
    circular imports at module level.
    """
    from memory_reader  import CS2Telemetry
    from world_to_screen import ViewMatrixReader, world_to_screen, Vector3 as WV3
    import pymem
    import pymem.process

    # ── Attach to CS2 ────────────────────────────────────────────────────────
    telem  = CS2Telemetry()
    vm_reader = ViewMatrixReader(telem.pm, telem.client_base)

    # ── Get window size from engine ──────────────────────────────────────────
    try:
        from cs2_offsets import Engine2Dll
        import pymem.process as pmp
        engine2 = pymem.process.module_from_name(telem.pm.process_handle, "engine2.dll")
        if engine2:
            eng_base = engine2.lpBaseOfDll
            w = telem.pm.read_int(eng_base + Engine2Dll.dwWindowWidth)
            h = telem.pm.read_int(eng_base + Engine2Dll.dwWindowHeight)
            if 640 <= w <= 7680 and 360 <= h <= 4320:
                screen_w, screen_h = w, h
                print(f"[+] Engine window size: {w}×{h}")
    except Exception:
        pass  # fall back to defaults

    # ── Create overlay ───────────────────────────────────────────────────────
    ov = Overlay(screen_w, screen_h)

    clock     = pygame.time.Clock()
    frame_idx = 0

    print(f"[+] Overlay running at {target_fps} FPS  |  F12 to quit\n")

    while True:
        if not ov.process_events():
            break

        try:
            # Read ViewMatrix and player list
            view_matrix = vm_reader.read()
            players     = telem.get_players()
            local       = telem.get_local_player()

            entities = []
            for player in players:
                # Skip local player (don't draw ESP on self - camera is at local player eyes)
                if local:
                    dx = player.origin.x - local.origin.x
                    dy = player.origin.y - local.origin.y
                    dz = player.origin.z - local.origin.z
                    if (dx * dx + dy * dy + dz * dz) < 625.0 or (player.name and player.name == local.name):
                        continue

                # Only show alive, initialized players
                if not player.is_alive or player.health <= 0:
                    continue
                if player.origin.x == 0.0 and player.origin.y == 0.0 and player.origin.z == 0.0:
                    continue

                # Project world origin (feet) and head
                wp_feet = WV3(player.origin.x, player.origin.y, player.origin.z)
                sp_feet = world_to_screen(wp_feet, view_matrix, screen_w, screen_h)
                if sp_feet is None:
                    continue

                head_z = 58.0 if player.is_crouching else 75.0
                wp_head = WV3(player.origin.x, player.origin.y, player.origin.z + head_z)
                sp_head = world_to_screen(wp_head, view_matrix, screen_w, screen_h)
                if sp_head is None:
                    continue

                box_h = int(abs(sp_feet.y - sp_head.y))
                # Validate reasonable height bounds (prevent near-camera coordinate explosion / flickering streaks)
                if box_h < 8 or box_h > int(screen_h * 1.5):
                    continue

                box_w = max(6, int(box_h * 0.52))
                center_x = int((sp_feet.x + sp_head.x) / 2)
                center_y = int((sp_feet.y + sp_head.y) / 2)

                # Cull entities completely outside the screen viewport
                if (center_x + box_w // 2 < -50 or center_x - box_w // 2 > screen_w + 50 or
                    center_y + box_h // 2 < -50 or center_y - box_h // 2 > screen_h + 50):
                    continue

                # Compute distance and aspect angle from local observer to entity
                dist = 0.0
                aspect_angle = 180.0
                if local:
                    dx = player.origin.x - local.origin.x
                    dy = player.origin.y - local.origin.y
                    dz = player.origin.z - local.origin.z
                    dist = (dx**2 + dy**2 + dz**2) ** 0.5 * 0.01905  # UU → metres
                    los_yaw = math.degrees(math.atan2(dy, dx))
                    ent_yaw = player.eye_angles.yaw
                    diff = (los_yaw - ent_yaw) % 360.0
                    if diff > 180.0:
                        diff -= 360.0
                    aspect_angle = diff

                # Dynamic lean angle based on movement velocity
                lean_angle = 0.0
                vel = player.velocity
                speed = (vel.x**2 + vel.y**2)**0.5
                if speed > 15.0 and sp_feet is not None:
                    wp_vel = WV3(player.origin.x + vel.x * 0.12, player.origin.y + vel.y * 0.12, player.origin.z)
                    sp_vel = world_to_screen(wp_vel, view_matrix, screen_w, screen_h)
                    if sp_vel is not None:
                        screen_dx = sp_vel.x - sp_feet.x
                        lean_angle = max(-14.0, min(14.0, screen_dx * 0.35))

                # Determine color based on team (T vs CT) or relationship (Ally vs Enemy)
                if player.team == 2:
                    color = COLOR_BOX_T      # Terrorist
                elif player.team == 3:
                    color = COLOR_BOX_CT     # Counter-Terrorist
                elif local and player.team == local.team:
                    color = COLOR_BOX_ALLY   # Ally
                else:
                    color = COLOR_BOX_ENEMY  # Enemy / fallback

                entities.append({
                    "x":          center_x,
                    "y":          center_y,
                    "w":          box_w,
                    "h":          box_h,
                    "color":      color,
                    "health":     player.health,
                    "team_name":  player.team_name,
                    "name":       player.name,
                    "distance":   dist,
                    "is_crouching": player.is_crouching,
                    "lean_angle": lean_angle,
                    "aspect_angle": aspect_angle,
                    "render_mode": "skeleton",
                })

            hud_lines = [
                f"[TELEMETRY OVERLAY - ACTIVE]",
                f"FPS: {int(clock.get_fps())} | Target: CS2",
                f"ViewMatrix: {'LOCKED' if view_matrix else 'SEARCHING'}",
            ]
            if local:
                hud_lines.append(f"Local Player: HP={local.health} Pos=({local.origin.x:.1f}, {local.origin.y:.1f}, {local.origin.z:.1f})")
            if entities:
                hud_lines.append(f"Tracking: {len(entities)} players")

            ov.render_frame(entities, hud_lines)

        except Exception as e:
            # Keep the overlay alive even if a single read fails
            print(f"[!] Frame {frame_idx} error: {e}")

        clock.tick(target_fps)
        frame_idx += 1

    ov.close()


if __name__ == "__main__":
    run_overlay()
