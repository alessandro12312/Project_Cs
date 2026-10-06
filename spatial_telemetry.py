"""
Spatial Telemetry Framework & External Overlay Prototype
Academic Research Codebase for Real-time 3D Telemetry and Spatial Projection
"""

import sys
import struct
import logging
from typing import Optional, Tuple, Dict, Any
import numpy as np

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

import os
# Prevent SDL2 from minimizing the overlay window when focus is switched to game
os.environ["SDL_VIDEO_MINIMIZE_ON_FOCUS_LOSS"] = "0"

# Windows-specific imports handled conditionally for cross-platform validation
IS_WINDOWS = sys.platform == "win32"
if IS_WINDOWS:
    import ctypes
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # Per-monitor DPI aware
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass

    import pymem
    import pymem.process
    import pygame
    import win32con
    import win32gui
    import win32api



import math
from dataclasses import dataclass

@dataclass
class EntityTelemetry:
    """Extracted real-time 3D telemetry data for a single entity."""
    position: Tuple[float, float, float]
    yaw: float = 0.0
    is_crouching: bool = False
    health: int = 100
    is_alive: bool = True

# ==============================================================================
# CONFIGURATION & OFFSETS SCHEME
# ==============================================================================
try:
    from cs2_offsets import (
        ClientDll,
        C_BaseEntity,
        CGameSceneNode,
        C_CSPlayerPawn,
        CCSPlayerController,
        ENTITY_ENTRY_SIZE,
        ENTITY_CHUNK_MASK,
    )
except ImportError:
    class ClientDll:
        dwEntityList = 0x2717828
        dwViewMatrix = 0x2567FA0
        dwLocalPlayerPawn = 0x2562808
    class C_BaseEntity:
        m_pGameSceneNode = 0x330
        m_fFlags = 0x3F4
        m_iHealth = 0x34C
        m_lifeState = 0x354
    class CGameSceneNode:
        m_vecAbsOrigin = 0xC8
    class C_CSPlayerPawn:
        m_angEyeAngles = 0x35F0
    class CCSPlayerController:
        m_hPlayerPawn = 0x92C
    ENTITY_ENTRY_SIZE = 112
    ENTITY_CHUNK_MASK = 0x1FF

DEFAULT_CONFIG = {
    "process_name": "cs2.exe",
    "module_name": "client.dll",
    "screen_width": 1920,
    "screen_height": 1080,
    "target_fps": 60,
    "chroma_key": (255, 0, 255),  # Magenta key for transparency
    "offsets": {
        "dwEntityList": ClientDll.dwEntityList,
        "dwViewMatrix": ClientDll.dwViewMatrix,
        "dwLocalPlayerPawn": getattr(ClientDll, "dwLocalPlayerPawn", 0x2562808),
        "m_pGameSceneNode": C_BaseEntity.m_pGameSceneNode,
        "m_vecAbsOrigin": CGameSceneNode.m_vecAbsOrigin,
        "m_hPlayerPawn": CCSPlayerController.m_hPlayerPawn,
        "m_fFlags": getattr(C_BaseEntity, "m_fFlags", 0x3F4),
        "m_angEyeAngles": getattr(C_CSPlayerPawn, "m_angEyeAngles", 0x35F0),
        "m_iHealth": getattr(C_BaseEntity, "m_iHealth", 0x34C),
        "m_lifeState": getattr(C_BaseEntity, "m_lifeState", 0x354),
        "dwEntityStride": ENTITY_ENTRY_SIZE,
        "index_mask": ENTITY_CHUNK_MASK,
    }
}

# ==============================================================================
# SPATIAL MATHEMATICS MODULE
# ==============================================================================
class SpatialProjection:
    """Handles 3D World space to 2D Screen space transformations."""

    @staticmethod
    def world_to_screen(
        world_pos: Tuple[float, float, float],
        view_matrix: np.ndarray,
        screen_width: int,
        screen_height: int
    ) -> Optional[Tuple[int, int]]:
        """
        Projects a 3D coordinate [X, Y, Z] into 2D Screen coordinates [Pixel X, Pixel Y].
        
        :param world_pos: (x, y, z) tuple in 3D world space
        :param view_matrix: 4x4 Transformation Matrix (row-major)
        :param screen_width: Display width in pixels
        :param screen_height: Display height in pixels
        :return: (screen_x, screen_y) tuple or None if point is behind camera
        """
        pos = np.array([world_pos[0], world_pos[1], world_pos[2], 1.0], dtype=np.float32)

        # Matrix multiplication for homogeneous clip coordinates
        clip_x = pos[0] * view_matrix[0, 0] + pos[1] * view_matrix[0, 1] + pos[2] * view_matrix[0, 2] + view_matrix[0, 3]
        clip_y = pos[0] * view_matrix[1, 0] + pos[1] * view_matrix[1, 1] + pos[2] * view_matrix[1, 2] + view_matrix[1, 3]
        clip_w = pos[0] * view_matrix[3, 0] + pos[1] * view_matrix[3, 1] + pos[2] * view_matrix[3, 2] + view_matrix[3, 3]

        # Perspective Check: Objects behind or on the near-clipping plane have W < 1.0
        if clip_w < 1.0:
            return None

        # Perspective Divide -> Normalized Device Coordinates (NDC) [-1.0, 1.0]
        ndc_x = clip_x / clip_w
        ndc_y = clip_y / clip_w

        # Reject coordinates excessively far from screen bounds to prevent SDL line overflow
        if abs(ndc_x) > 3.0 or abs(ndc_y) > 3.0:
            return None

        # Viewport Mapping to Screen Pixels
        screen_x = (screen_width / 2.0) * (1.0 + ndc_x)
        screen_y = (screen_height / 2.0) * (1.0 - ndc_y)

        return int(screen_x), int(screen_y)

# ==============================================================================
# MEMORY READER MODULE (WINDOWS ONLY)
# ==============================================================================
class MemoryReader:
    """Wrapper for Pymem operations to read memory arrays and structures safely."""

    def __init__(self, process_name: str, module_name: str):
        self.process_name = process_name
        self.module_name = module_name
        self.pm: Optional[Any] = None
        self.module_base: int = 0

    def attach(self) -> bool:
        """Attaches to target process and resolves module base address."""
        if not IS_WINDOWS:
            logging.error("Pymem memory attachment requires Windows OS environment.")
            return False
        try:
            self.pm = pymem.Pymem(self.process_name)
            module = pymem.process.module_from_name(self.pm.process_handle, self.module_name)
            self.module_base = module.lpBaseOfDll
            logging.info(f"Attached to {self.process_name}. Base [{self.module_name}]: {hex(self.module_base)}")
            return True
        except Exception as e:
            logging.error(f"Failed to attach to {self.process_name}: {e}")
            return False

    def read_view_matrix(self, offset: int) -> Optional[np.ndarray]:
        """Reads 64 bytes (16 single-precision floats) for the 4x4 ViewMatrix."""
        if not self.pm or not self.module_base:
            return None
        try:
            matrix_addr = self.module_base + offset
            raw_bytes = self.pm.read_bytes(matrix_addr, 64)
            floats = struct.unpack("16f", raw_bytes)
            return np.array(floats, dtype=np.float32).reshape((4, 4))
        except Exception as e:
            logging.debug(f"Error reading ViewMatrix: {e}")
            return None

    def read_local_player_origin(
        self,
        local_pawn_offset: int,
        scene_node_offset: int = 0x330,
        pos_offset: int = 0xC8
    ) -> Optional[Tuple[float, float, float]]:
        """Reads local player Vector3 position for relative perspective / line-of-sight angle."""
        if not self.pm or not self.module_base:
            return None
        try:
            local_pawn = self.pm.read_ulonglong(self.module_base + local_pawn_offset)
            if not local_pawn:
                return None
            scene_node = self.pm.read_ulonglong(local_pawn + scene_node_offset)
            if not scene_node:
                return None
            raw = self.pm.read_bytes(scene_node + pos_offset, 12)
            return struct.unpack("3f", raw)
        except Exception:
            return None

    def read_entity_telemetry(
        self,
        entity_list_offset: int,
        index: int,
        stride: int = 112,
        pos_offset: int = 0xC8,
        index_mask: int = 0x1FF,
        scene_node_offset: int = 0x330,
        pawn_handle_offset: int = 0x92C,
        flags_offset: int = 0x3F4,
        angles_offset: int = 0x35F0,
        health_offset: int = 0x34C,
        life_state_offset: int = 0x354,
    ) -> Optional[EntityTelemetry]:
        """
        Reads complete telemetry state of an entity:
          - 3D Position (m_vecAbsOrigin)
          - Yaw / Rotation (m_angEyeAngles)
          - Posture (m_fFlags bit 1 = FL_DUCKING, in piedi o abbassato)
          - Health & Life state (filtrando i morti / disconnessi)
        """
        if not self.pm or not self.module_base:
            return None
        try:
            entity_sys = self.pm.read_ulonglong(self.module_base + entity_list_offset)
            if not entity_sys:
                return None

            chunk_index = index >> 9
            slot = index & index_mask
            chunk_base = self.pm.read_ulonglong(entity_sys + 0x10 + (chunk_index * 8))
            if not chunk_base:
                return None

            ctrl_ptr = self.pm.read_ulonglong(chunk_base + (stride * slot))
            if not ctrl_ptr:
                return None

            # Read pawn handle from controller
            pawn_handle = self.pm.read_uint(ctrl_ptr + pawn_handle_offset)
            if not pawn_handle or pawn_handle == 0xFFFFFFFF:
                return None

            # Resolve pawn pointer
            pawn_idx = pawn_handle & 0x7FFF
            pawn_chunk_base = self.pm.read_ulonglong(entity_sys + 0x10 + 8 * (pawn_idx >> 9))
            if not pawn_chunk_base:
                return None

            pawn_ptr = self.pm.read_ulonglong(pawn_chunk_base + (stride * (pawn_idx & index_mask)))
            if not pawn_ptr:
                return None

            # Health and life-state check
            life_state = self.pm.read_bytes(pawn_ptr + life_state_offset, 1)[0]
            health = self.pm.read_int(pawn_ptr + health_offset)
            if life_state != 0 or health <= 0:
                return None

            # Flags: bit 1 = FL_DUCKING (crouching posture)
            flags = self.pm.read_uint(pawn_ptr + flags_offset)
            is_crouching = bool(flags & (1 << 1))

            # Eye angles: pitch, yaw, roll
            angles_bytes = self.pm.read_bytes(pawn_ptr + angles_offset, 12)
            _, yaw, _ = struct.unpack("3f", angles_bytes)

            # SceneNode -> Origin (Z origin at feet)
            scene_node = self.pm.read_ulonglong(pawn_ptr + scene_node_offset)
            if not scene_node:
                return None

            vector_bytes = self.pm.read_bytes(scene_node + pos_offset, 12)
            pos = struct.unpack("3f", vector_bytes)
            if pos == (0.0, 0.0, 0.0):
                return None

            return EntityTelemetry(
                position=pos,
                yaw=yaw,
                is_crouching=is_crouching,
                health=health,
                is_alive=True
            )
        except Exception as e:
            logging.debug(f"Error reading entity {index} telemetry: {e}")
            return None

    def read_entity_position(
        self,
        entity_list_offset: int,
        index: int,
        stride: int = 112,
        pos_offset: int = 0xC8,
        index_mask: int = 0x1FF,
        scene_node_offset: int = 0x330,
        pawn_handle_offset: int = 0x92C,
    ) -> Optional[Tuple[float, float, float]]:
        """Backward-compatible helper returning just position."""
        telem = self.read_entity_telemetry(
            entity_list_offset, index, stride, pos_offset, index_mask, scene_node_offset, pawn_handle_offset
        )
        return telem.position if telem else None

# ==============================================================================
# OVERLAY RENDERER MODULE
# ==============================================================================
class OverlayWindow:
    """Manages transparent win32 overlay rendering via Pygame."""

    def __init__(self, width: int, height: int, chroma_key: Tuple[int, int, int]):
        self.width = width
        self.height = height
        self.chroma_key = chroma_key
        self.screen: Optional[Any] = None
        self.clock: Optional[Any] = None
        self.hwnd: Optional[int] = None
        self.font: Optional[Any] = None

    def initialize(self) -> bool:
        """Initializes Pygame window and sets Win32 transparent styles."""
        if not IS_WINDOWS:
            logging.error("PyWin32 transparent overlay requires Windows OS environment.")
            return False
        try:
            pygame.init()
            self.screen = pygame.display.set_mode((self.width, self.height), pygame.NOFRAME)
            self.hwnd = pygame.display.get_wm_info()["window"]
            self.font = pygame.font.SysFont("Arial", 11, bold=True)

            # Configure Win32 Extended Styles
            extended_style = (
                win32gui.GetWindowLong(self.hwnd, win32con.GWL_EXSTYLE)
                | win32con.WS_EX_LAYERED
                | win32con.WS_EX_TRANSPARENT
                | win32con.WS_EX_TOPMOST
                | win32con.WS_EX_TOOLWINDOW
                | 0x08000000  # WS_EX_NOACTIVATE
            )
            win32gui.SetWindowLong(self.hwnd, win32con.GWL_EXSTYLE, extended_style)

            # Set Chroma-key color transparency
            key_color = win32api.RGB(*self.chroma_key)
            win32gui.SetLayeredWindowAttributes(self.hwnd, key_color, 0, win32con.LWA_COLORKEY)

            # Explicitly assert HWND_TOPMOST so DWM places the overlay above other windows
            win32gui.SetWindowPos(
                self.hwnd,
                win32con.HWND_TOPMOST,
                0, 0, self.width, self.height,
                win32con.SWP_SHOWWINDOW | win32con.SWP_NOACTIVATE | win32con.SWP_FRAMECHANGED
            )

            self.clock = pygame.time.Clock()
            logging.info("Overlay window initialized successfully.")
            return True
        except Exception as e:
            logging.error(f"Failed to initialize overlay window: {e}")
            return False

    def clear(self):
        """Clears overlay frame using Chroma Key background fill."""
        if self.screen:
            self.screen.fill(self.chroma_key)

    def draw_entity_box(self, x: int, y: int, width: int = 30, height: int = 60, color: Tuple[int, int, int] = (0, 255, 0)):
        """Draws bounding rectangle centered at projected screen position (x, y)."""
        if self.screen:
            top_left_x = int(x - width // 2)
            top_left_y = int(y - height // 2)
            # Outline border for high contrast against any game background
            pygame.draw.rect(self.screen, (0, 0, 0), (top_left_x - 1, top_left_y - 1, width + 2, height + 2), 1)
            pygame.draw.rect(self.screen, color, (top_left_x, top_left_y, width, height), 2)
            pygame.draw.rect(self.screen, (0, 0, 0), (top_left_x + 2, top_left_y + 2, max(0, width - 4), max(0, height - 4)), 1)

    def draw_health_bar(
        self,
        sx: int,
        sy: int,
        health: int,
        width: int = 30,
        height: int = 60,
        bar_width: int = 5,
    ):
        """Draws a vertical health bar and health percentage text with an offset to the left of the ESP box."""
        if not self.screen:
            return

        hp = max(0, min(health, 100))
        bar_x = int(sx - width // 2 - bar_width - 4)
        bar_y = int(sy - height // 2)

        # Background
        bg_rect = pygame.Rect(bar_x, bar_y, bar_width, height)
        pygame.draw.rect(self.screen, (0, 0, 0), (bar_x - 1, bar_y - 1, bar_width + 2, height + 2), 1)
        pygame.draw.rect(self.screen, (40, 40, 40), bg_rect)

        # Health fill
        fill_h = int(height * hp / 100.0)
        fill_y = bar_y + (height - fill_h)
        fill_rect = pygame.Rect(bar_x, fill_y, bar_width, fill_h)

        # Dynamic color (red -> yellow -> green)
        r = int(255 * (1.0 - hp / 100.0))
        g = int(255 * (hp / 100.0))
        pygame.draw.rect(self.screen, (r, g, 30), fill_rect)

        # Health percentage label offset to the left of the health bar
        if self.font:
            hp_str = f"{hp}%"
            shadow = self.font.render(hp_str, True, (0, 0, 0))
            text = self.font.render(hp_str, True, (r, g, 80))
            text_x = bar_x - text.get_width() - 3
            text_y = fill_y - text.get_height() // 2 if height > 25 else bar_y
            text_y = max(bar_y - 2, min(bar_y + height - text.get_height() + 2, text_y))
            self.screen.blit(shadow, (text_x + 1, text_y + 1))
            self.screen.blit(text, (text_x, text_y))

    def draw_stylized_skeleton(
        self,
        sx: int,
        sy: int,
        width: int,
        height: int,
        color: Tuple[int, int, int] = (255, 215, 0),
        is_crouching: bool = False,
        aspect_angle_deg: float = 180.0,
        thickness: int = 2
    ):
        """
        Renders an anatomical stick-figure (omino stilizzato) inside the bounding box:
          - 1 head circle
          - 1 torso segment (neck -> pelvis)
          - 2 segments per arm (shoulder -> elbow -> hand)
          - 2 segments per leg (pelvis -> knee -> foot)
        Scales with box dimensions, dynamically rotates with entity yaw,
        and adapts posture (standing vs crouching).
        """
        if not self.screen or height < 14 or width < 6:
            return

        top = int(sy - height // 2)
        line_w = max(1, thickness)

        rad = math.radians(aspect_angle_deg)
        sin_a = math.sin(rad)
        cos_a = math.cos(rad)
        w_scale = width * 0.5

        def proj(xf: float, yr: float, zd: float) -> Tuple[int, int]:
            """Projects 3D normalized coordinates (forward xf, right yr, down zd) to 2D screen."""
            px = int(sx + (xf * sin_a + yr * cos_a) * w_scale)
            py = int(top + zd * height)
            px = max(-100, min(self.width + 100, px))
            py = max(-100, min(self.height + 100, py))
            return px, py

        if not is_crouching:
            # ── Postura in piedi (standing) ──────────────────────────────────
            head_cx = int(sx + 0.04 * sin_a * w_scale)
            head_cy = int(top + max(2, height * 0.09))
            head_radius = max(2, min(width // 5, int(height * 0.05)))

            # Busto: 1 singolo segmento dal collo al bacino
            torso_top = proj(0.02, 0.0, 0.17)
            torso_bot = proj(-0.02, 0.0, 0.52)

            # Braccio Sinistro: 2 segmenti
            elbow_l = proj(0.12, -0.60, 0.35)
            hand_l  = proj(0.35, -0.22, 0.45)

            # Braccio Destro: 2 segmenti
            elbow_r = proj(0.08, 0.60, 0.37)
            hand_r  = proj(0.30, 0.18, 0.45)

            # Gamba Sinistra: 2 segmenti
            knee_l  = proj(0.06, -0.40, 0.74)
            foot_l  = proj(0.10, -0.36, 0.98)

            # Gamba Destra: 2 segmenti
            knee_r  = proj(-0.06, 0.40, 0.74)
            foot_r  = proj(-0.10, 0.36, 0.98)
        else:
            # ── Postura abbassata (crouching / squatting) ────────────────────
            head_cx = int(sx + 0.22 * sin_a * w_scale)
            head_cy = int(top + max(2, height * 0.14))
            head_radius = max(2, min(width // 5, int(height * 0.05)))

            # Busto: 1 singolo segmento inclinato in avanti
            torso_top = proj(0.18, 0.0, 0.24)
            torso_bot = proj(-0.22, 0.0, 0.54)

            # Braccio Sinistro: 2 segmenti
            elbow_l = proj(0.30, -0.62, 0.44)
            hand_l  = proj(0.45, -0.22, 0.56)

            # Braccio Destro: 2 segmenti
            elbow_r = proj(0.25, 0.62, 0.46)
            hand_r  = proj(0.40, 0.18, 0.56)

            # Gamba Sinistra: 2 segmenti
            knee_l  = proj(0.45, -0.52, 0.70)
            foot_l  = proj(-0.08, -0.30, 0.98)

            # Gamba Destra: 2 segmenti
            knee_r  = proj(0.40, 0.52, 0.72)
            foot_r  = proj(-0.14, 0.30, 0.98)

        # 9 segmenti anatomici
        segments = [
            (torso_top, torso_bot),   # Busto (1 linea)
            (torso_top, elbow_l),     # Braccio sx - spalla/gomito
            (elbow_l, hand_l),        # Braccio sx - gomito/mano
            (torso_top, elbow_r),     # Braccio dx - spalla/gomito
            (elbow_r, hand_r),        # Braccio dx - gomito/mano
            (torso_bot, knee_l),      # Gamba sx - anca/ginocchio
            (knee_l, foot_l),         # Gamba sx - ginocchio/piede
            (torso_bot, knee_r),      # Gamba dx - anca/ginocchio
            (knee_r, foot_r),         # Gamba dx - ginocchio/piede
        ]

        # 1. Testa
        head_cx = max(-100, min(self.width + 100, head_cx))
        head_cy = max(-100, min(self.height + 100, head_cy))
        head_radius = max(2, min(min(width // 5, int(height * 0.05)), 40))

        # Contorno scuro per massimo contrasto
        outline_w = line_w + 2
        bg_color = (0, 0, 0)

        pygame.draw.circle(self.screen, bg_color, (head_cx, head_cy), head_radius + 1, outline_w)
        for p1, p2 in segments:
            pygame.draw.line(self.screen, bg_color, p1, p2, outline_w)

        # Disegno omino principale colorato
        pygame.draw.circle(self.screen, color, (head_cx, head_cy), head_radius, line_w)
        for p1, p2 in segments:
            pygame.draw.line(self.screen, color, p1, p2, line_w)

        # Giunture articolari (spalle, gomiti, mani, ginocchia, piedi)
        if 35 <= height <= int(self.height * 1.2):
            joints = [torso_top, torso_bot, elbow_l, hand_l, elbow_r, hand_r, knee_l, foot_l, knee_r, foot_r]
            for jx, jy in joints:
                if 0 <= jx <= self.width and 0 <= jy <= self.height:
                    pygame.draw.circle(self.screen, (255, 255, 255), (jx, jy), max(1, line_w))

    def update(self, fps: int = 60):
        """Refreshes overlay display and reasserts topmost Z-order."""
        if pygame and self.clock:
            pygame.display.update()
            if self.hwnd:
                # If window was minimized, restore it
                if win32gui.IsIconic(self.hwnd):
                    win32gui.ShowWindow(self.hwnd, win32con.SW_RESTORE)
                # Reassert topmost over target game window
                win32gui.SetWindowPos(
                    self.hwnd,
                    win32con.HWND_TOPMOST,
                    0, 0, 0, 0,
                    win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_NOACTIVATE | win32con.SWP_SHOWWINDOW
                )
            self.clock.tick(fps)

# ==============================================================================
# MAIN ENTRY POINT
# ==============================================================================
def main():
    print("=" * 60)
    print("Spatial Telemetry & Real-time Graphics Overlay Prototype")
    print("=" * 60)

    if not IS_WINDOWS:
        print("[!] Warning: Windows OS environment required to execute live memory attachment and Win32 transparent overlays.")
        print("[+] Mathematical and structural classes are initialized and ready for deployment.")
        return

    config = DEFAULT_CONFIG
    offsets = config["offsets"]

    reader = MemoryReader(config["process_name"], config["module_name"])
    if not reader.attach():
        print("[-] Target process not found. Ensure target process is running in debug mode.")
        return

    overlay = OverlayWindow(config["screen_width"], config["screen_height"], config["chroma_key"])
    if not overlay.initialize():
        return

    print("[+] Telemetry loop starting. Press CTRL+C to exit.")
    running = True

    try:
        while running:
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    running = False

            overlay.clear()

            # Read frame view matrix
            view_matrix = reader.read_view_matrix(offsets["dwViewMatrix"])

            if view_matrix is not None:
                # Read local player position to compute relative line-of-sight angle
                local_pos = reader.read_local_player_origin(
                    offsets.get("dwLocalPlayerPawn", 0x2562808),
                    offsets.get("m_pGameSceneNode", 0x330),
                    offsets.get("m_vecAbsOrigin", 0xC8),
                )

                # Poll entity pool (slots 1 to 64 for player controllers)
                for i in range(1, 65):
                    telem = reader.read_entity_telemetry(
                        offsets["dwEntityList"],
                        i,
                        offsets.get("dwEntityStride", 112),
                        offsets.get("m_vecAbsOrigin", 0xC8),
                        offsets.get("index_mask", 0x1FF),
                        offsets.get("m_pGameSceneNode", 0x330),
                        offsets.get("m_hPlayerPawn", 0x92C),
                        offsets.get("m_fFlags", 0x3F4),
                        offsets.get("m_angEyeAngles", 0x35F0),
                        offsets.get("m_iHealth", 0x34C),
                        offsets.get("m_lifeState", 0x354),
                    )
                    if telem is None or not telem.is_alive or telem.health <= 0:
                        continue

                    world_pos = telem.position
                    if world_pos == (0.0, 0.0, 0.0):
                        continue

                    # Skip local player (don't draw ESP on self - camera is at local player position)
                    if local_pos:
                        dx = world_pos[0] - local_pos[0]
                        dy = world_pos[1] - local_pos[1]
                        dz = world_pos[2] - local_pos[2]
                        if (dx * dx + dy * dy + dz * dz) < 625.0:
                            continue

                    head_z = 58.0 if telem.is_crouching else 75.0
                    head_world = (world_pos[0], world_pos[1], world_pos[2] + head_z)

                    screen_feet = SpatialProjection.world_to_screen(
                        world_pos,
                        view_matrix,
                        config["screen_width"],
                        config["screen_height"]
                    )
                    screen_head = SpatialProjection.world_to_screen(
                        head_world,
                        view_matrix,
                        config["screen_width"],
                        config["screen_height"]
                    )

                    # Require both feet and head to be in front of near-plane to prevent wild line flickers
                    if screen_feet is None or screen_head is None:
                        continue

                    # Dynamic perspective height
                    box_height = int(abs(screen_feet[1] - screen_head[1]))
                    if box_height < 8 or box_height > int(config["screen_height"] * 1.5):
                        continue

                    # Standard human body aspect ratio with shoulders
                    box_width = max(6, int(box_height * 0.52))

                    # Center of the entity (midpoint between head and feet)
                    center_x = int((screen_feet[0] + screen_head[0]) / 2)
                    center_y = int((screen_feet[1] + screen_head[1]) / 2)

                    # Cull entities completely outside viewport
                    if (center_x + box_width // 2 < -50 or center_x - box_width // 2 > config["screen_width"] + 50 or
                        center_y + box_height // 2 < -50 or center_y - box_height // 2 > config["screen_height"] + 50):
                        continue

                    # Compute relative aspect angle (3D observation perspective)
                    aspect_angle = 180.0
                    if local_pos:
                        dx = world_pos[0] - local_pos[0]
                        dy = world_pos[1] - local_pos[1]
                        los_yaw = math.degrees(math.atan2(dy, dx))
                        diff = (los_yaw - telem.yaw) % 360.0
                        if diff > 180.0:
                            diff -= 360.0
                        aspect_angle = diff

                    # 1. Bounding box esterno centrato
                    overlay.draw_entity_box(center_x, center_y, box_width, box_height)

                    # 2. Barra salute con percentuale della vita ad offset
                    overlay.draw_health_bar(center_x, center_y, telem.health, box_width, box_height)

                    # 3. Omino stilizzato interno (2 linee braccia/gambe, 1 busto/testa, rotazione e postura)
                    overlay.draw_stylized_skeleton(
                        center_x,
                        center_y,
                        box_width,
                        box_height,
                        color=(255, 215, 0),  # Giallo oro ad alta visibilità
                        is_crouching=telem.is_crouching,
                        aspect_angle_deg=aspect_angle,
                    )

            overlay.update(config["target_fps"])

    except KeyboardInterrupt:
        print("\n[+] Exiting telemetry loop.")
    finally:
        pygame.quit()

if __name__ == "__main__":
    main()
