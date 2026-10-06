"""
world_to_screen.py  ─  Task 2: Spatial Projection Mathematics
──────────────────────────────────────────────────────────────
Implements the full 3D → 2D pipeline:
  1. Read the 4×4 ViewMatrix from CS2 process memory
  2. Multiply a world-space Vector3 by the ViewMatrix (clip-space transform)
  3. Perform the perspective divide (W-coordinate) → NDC
  4. Map NDC to pixel coordinates on screen

Formula summary
───────────────
  clip = ViewMatrix × [x, y, z, 1]ᵀ
  if clip.w <= 0: point is behind camera (discard)
  ndc_x = clip.x / clip.w
  ndc_y = clip.y / clip.w
  screen_x = (ndc_x + 1) / 2 × screen_w
  screen_y = (1 - ndc_y) / 2 × screen_h   ← Y-axis flipped (NDC up = screen up)
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Optional

try:
    import pymem
    import pymem.exception
except ImportError:
    raise SystemExit("pymem required:  pip install pymem")

from cs2_offsets import ClientDll


# ══════════════════════════════════════════════════════════════════════════════
#  Data types
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class Vector3:
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0


@dataclass
class Vector2:
    """A point in 2D screen-space (pixel coordinates)."""
    x: float = 0.0
    y: float = 0.0

    def as_int(self) -> tuple[int, int]:
        return int(self.x), int(self.y)

    def __repr__(self):
        return f"Screen({self.x:.1f}, {self.y:.1f})"


@dataclass
class Matrix4x4:
    """
    Column-major 4×4 float matrix — matches how the Source engine stores
    the ViewMatrix in memory (16 consecutive float32 values).

    Memory layout in process (column-major):
        m[0]  m[4]  m[8]   m[12]
        m[1]  m[5]  m[9]   m[13]
        m[2]  m[6]  m[10]  m[14]
        m[3]  m[7]  m[11]  m[15]

    We index as m[row * 4 + col] (row-major index after reading).
    """
    data: list[float]   # 16 floats, row-major after parsing

    @classmethod
    def from_bytes(cls, raw: bytes) -> "Matrix4x4":
        """Unpack 16 × float32 from a 64-byte buffer."""
        if len(raw) < 64:
            raise ValueError(f"Expected 64 bytes for Matrix4x4, got {len(raw)}")
        floats = list(struct.unpack("<16f", raw[:64]))
        return cls(data=floats)

    @classmethod
    def read(cls, pm: pymem.Pymem, addr: int) -> "Matrix4x4":
        """Read 64 bytes from process memory and parse as a 4×4 matrix."""
        raw = pm.read_bytes(addr, 64)
        return cls.from_bytes(raw)

    def __getitem__(self, rc: tuple[int, int]) -> float:
        """Access element at (row, col) with m[row, col]."""
        row, col = rc
        return self.data[row * 4 + col]

    def transform(self, v: Vector3) -> tuple[float, float, float, float]:
        """
        Multiply the matrix by the homogeneous vector [x, y, z, 1]ᵀ.

        Returns (clip_x, clip_y, clip_z, clip_w).

        Mathematical breakdown:
          clip_x = m[0,0]×x + m[0,1]×y + m[0,2]×z + m[0,3]×1
          clip_y = m[1,0]×x + m[1,1]×y + m[1,2]×z + m[1,3]×1
          clip_z = m[2,0]×x + m[2,1]×y + m[2,2]×z + m[2,3]×1
          clip_w = m[3,0]×x + m[3,1]×y + m[3,2]×z + m[3,3]×1
        """
        m  = self.data
        x, y, z = v.x, v.y, v.z

        clip_x = m[0]*x  + m[1]*y  + m[2]*z  + m[3]
        clip_y = m[4]*x  + m[5]*y  + m[6]*z  + m[7]
        clip_z = m[8]*x  + m[9]*y  + m[10]*z + m[11]
        clip_w = m[12]*x + m[13]*y + m[14]*z + m[15]

        return clip_x, clip_y, clip_z, clip_w


# ══════════════════════════════════════════════════════════════════════════════
#  Core projection function
# ══════════════════════════════════════════════════════════════════════════════

def world_to_screen(
    world_pos:  Vector3,
    view_matrix: Matrix4x4,
    screen_w:   int,
    screen_h:   int,
) -> Optional[Vector2]:
    """
    Project a 3D world-space position onto the 2D screen.

    Algorithm (full pipeline)
    ─────────────────────────
    Step 1 — Clip space
        Multiply the world position (as a homogeneous 4-vector with w=1)
        by the 4×4 ViewProjection matrix provided by the engine:

            [clip_x]   [m00 m01 m02 m03] [world_x]
            [clip_y] = [m10 m11 m12 m13] [world_y]
            [clip_z]   [m20 m21 m22 m23] [world_z]
            [clip_w]   [m30 m31 m32 m33] [    1  ]

    Step 2 — Perspective divide  (clip → NDC)
        The W component encodes perspective depth.
        Dividing by W collapses the 4D clip-space point to 3D NDC:

            ndc_x = clip_x / clip_w      range: [-1 … +1]
            ndc_y = clip_y / clip_w      range: [-1 … +1]

        If clip_w ≤ 0 the point is behind the camera — discard it.

    Step 3 — Viewport transform  (NDC → pixels)
        NDC origin is screen center; Y-axis points UP in NDC but DOWN on screen:

            screen_x =        (ndc_x + 1) / 2  ×  screen_w
            screen_y = (1  -  (ndc_y + 1) / 2) ×  screen_h
                     =        (1 - ndc_y) / 2   ×  screen_h

    Parameters
    ──────────
    world_pos   : 3D position in engine world-space.
    view_matrix : 4×4 ViewProjection matrix read from the engine.
    screen_w    : Render target width  in pixels.
    screen_h    : Render target height in pixels.

    Returns
    ───────
    Vector2 pixel position, or None if the point is behind the camera.
    """

    # ── Step 1: clip space ────────────────────────────────────────────────
    clip_x, clip_y, clip_z, clip_w = view_matrix.transform(world_pos)

    # ── Step 2: perspective divide ────────────────────────────────────────
    if clip_w < 1.0:
        # Point is behind the camera or directly on the near clipping plane (< 2.5cm).
        # Discard to prevent division by near-zero resulting in runaway coordinates.
        return None

    ndc_x = clip_x / clip_w   # normalised device coordinate  x ∈ [-1, 1]
    ndc_y = clip_y / clip_w   # normalised device coordinate  y ∈ [-1, 1]

    # Reject coordinates excessively far from screen bounds to prevent SDL line overflow
    if abs(ndc_x) > 3.0 or abs(ndc_y) > 3.0:
        return None

    # ── Step 3: viewport transform ────────────────────────────────────────
    screen_x = (ndc_x + 1.0) / 2.0 * screen_w
    screen_y = (1.0 - ndc_y) / 2.0 * screen_h   # flip Y: NDC +y = screen top

    return Vector2(screen_x, screen_y)


# ══════════════════════════════════════════════════════════════════════════════
#  Live matrix reader (CS2)
# ══════════════════════════════════════════════════════════════════════════════

class ViewMatrixReader:
    """
    Reads the current ViewProjection matrix from CS2 memory each frame.

    The matrix lives at:
        client_base + dwViewMatrix  (0x2567FA0)

    It is a 4×4 array of float32, laid out row-by-row (64 bytes total).
    The engine updates it every render frame.
    """

    def __init__(self, pm: pymem.Pymem, client_base: int):
        self.pm          = pm
        self.matrix_addr = client_base + ClientDll.dwViewMatrix  # 0x2567FA0

    def read(self) -> Matrix4x4:
        """Read and return the current 4×4 ViewMatrix."""
        return Matrix4x4.read(self.pm, self.matrix_addr)


# ══════════════════════════════════════════════════════════════════════════════
#  Standalone demo (no live process — uses synthetic matrix)
# ══════════════════════════════════════════════════════════════════════════════

def _demo():
    """
    Verify the projection math using a canonical perspective matrix.
    A point directly in front of the camera should project to screen centre.
    """
    import math

    def perspective_matrix(fov_deg: float, aspect: float,
                           near: float, far: float) -> Matrix4x4:
        """Build a standard OpenGL-style perspective projection matrix."""
        fov_rad = math.radians(fov_deg)
        f = 1.0 / math.tan(fov_rad / 2.0)
        data = [
            f / aspect, 0,  0,                            0,
            0,          f,  0,                            0,
            0,          0,  (far + near) / (near - far),  (2*far*near) / (near - far),
            0,          0, -1,                            0,
        ]
        return Matrix4x4(data=data)

    SCREEN_W, SCREEN_H = 1920, 1080
    fov, aspect, near, far = 90.0, SCREEN_W / SCREEN_H, 0.1, 1000.0

    mat = perspective_matrix(fov, aspect, near, far)

    test_cases = [
        # (description, world_pos, expected_result)
        ("On-axis 10 units in front",   Vector3(0, 0, -10),    "centre of screen"),
        ("Left side",                   Vector3(-5, 0, -10),   "left of centre"),
        ("Above centre",                Vector3(0, 3, -10),    "above centre"),
        ("Behind camera",               Vector3(0, 0,  10),    "None (behind)"),
        ("Far right top corner approx", Vector3(10, 5.6, -10), "upper right area"),
    ]

    print("=" * 68)
    print("DEMO - World-to-Screen Projection")
    print(f"       Screen: {SCREEN_W}x{SCREEN_H}  |  FOV: {fov} deg  |  aspect: {aspect:.3f}")
    print("=" * 68)
    print(f"  {'Description':<35} {'World Pos':<22} {'Screen Pos'}")
    print(f"  {'-'*35} {'-'*22} {'-'*20}")

    for desc, pos, note in test_cases:
        result = world_to_screen(pos, mat, SCREEN_W, SCREEN_H)
        pos_str = f"({pos.x:+.1f},{pos.y:+.1f},{pos.z:+.1f})"
        res_str = repr(result) if result else "None (behind cam)"
        print(f"  {desc:<35} {pos_str:<22} {res_str}  <- {note}")


if __name__ == "__main__":
    _demo()
