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

# Windows-specific imports handled conditionally for cross-platform validation
IS_WINDOWS = sys.platform == "win32"
if IS_WINDOWS:
    import pymem
    import pymem.process
    import pygame
    import win32con
    import win32gui

# ==============================================================================
# CONFIGURATION & OFFSETS SCHEME
# ==============================================================================
DEFAULT_CONFIG = {
    "process_name": "cs2.exe",
    "module_name": "client.dll",
    "screen_width": 1920,
    "screen_height": 1080,
    "target_fps": 60,
    "chroma_key": (255, 0, 255),  # Magenta key for transparency
    "offsets": {
        "dwEntityList": 0x1820A80,
        "dwViewMatrix": 0x192E400,
        "m_vecOrigin": 0x30,
        "dwEntityStride": 112,
        "index_mask": 0x1FF,
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

        # Perspective Check: Objects behind near-clipping plane have W < 0.1
        if clip_w < 0.1:
            return None

        # Perspective Divide -> Normalized Device Coordinates (NDC) [-1.0, 1.0]
        ndc_x = clip_x / clip_w
        ndc_y = clip_y / clip_w

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

    def read_entity_position(
        self,
        entity_list_offset: int,
        index: int,
        stride: int,
        pos_offset: int,
        index_mask: int = 0x1FF
    ) -> Optional[Tuple[float, float, float]]:
        """
        Reads Vector3 position of an entity at specified array index using stride formula.
        
        Formula: Address = EntityListBase + Stride * (Index & Mask)
        """
        if not self.pm or not self.module_base:
            return None
        try:
            entity_list_base = self.module_base + entity_list_offset
            masked_index = index & index_mask
            entry_address = entity_list_base + (stride * masked_index)

            # Dereference pointer to entity instance
            entity_ptr = self.pm.read_ulonglong(entry_address)
            if not entity_ptr:
                return None

            # Read 12 bytes for 3 floats (Vector3: X, Y, Z)
            vector_bytes = self.pm.read_bytes(entity_ptr + pos_offset, 12)
            return struct.unpack("3f", vector_bytes)
        except Exception as e:
            logging.debug(f"Error reading entity {index} position: {e}")
            return None

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

    def initialize(self) -> bool:
        """Initializes Pygame window and sets Win32 transparent styles."""
        if not IS_WINDOWS:
            logging.error("PyWin32 transparent overlay requires Windows OS environment.")
            return False
        try:
            pygame.init()
            self.screen = pygame.display.set_mode((self.width, self.height), pygame.NOFRAME)
            self.hwnd = pygame.display.get_wm_info()["window"]

            # Configure Win32 Extended Styles
            extended_style = (
                win32gui.GetWindowLong(self.hwnd, win32con.GWL_EXSTYLE)
                | win32con.WS_EX_LAYERED
                | win32con.WS_EX_TRANSPARENT
                | win32con.WS_EX_TOPMOST
                | 0x08000000  # WS_EX_NOACTIVATE
            )
            win32gui.SetWindowLong(self.hwnd, win32con.GWL_EXSTYLE, extended_style)

            # Set Chroma-key color transparency
            key_color = win32gui.RGB(*self.chroma_key)
            win32gui.SetLayeredWindowAttributes(self.hwnd, key_color, 0, win32con.LWA_COLORKEY)

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
        """Draws bounding rectangle centered at projected screen position."""
        if self.screen:
            top_left_x = x - width // 2
            top_left_y = y - height // 2
            pygame.draw.rect(self.screen, color, (top_left_x, top_left_y, width, height), 2)

    def update(self, fps: int = 60):
        """Refreshes overlay display."""
        if pygame and self.clock:
            pygame.display.update()
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
                # Poll entity pool
                for i in range(32):
                    world_pos = reader.read_entity_position(
                        offsets["dwEntityList"],
                        i,
                        offsets["dwEntityStride"],
                        offsets["m_vecOrigin"],
                        offsets["index_mask"]
                    )
                    if world_pos is None:
                        continue

                    screen_pos = SpatialProjection.world_to_screen(
                        world_pos,
                        view_matrix,
                        config["screen_width"],
                        config["screen_height"]
                    )

                    if screen_pos:
                        overlay.draw_entity_box(screen_pos[0], screen_pos[1])

            overlay.update(config["target_fps"])

    except KeyboardInterrupt:
        print("\n[+] Exiting telemetry loop.")
    finally:
        pygame.quit()

if __name__ == "__main__":
    main()
