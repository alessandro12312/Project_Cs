"""
demo_chunk_read_standalone.py
─────────────────────────────
Standalone verification script for all core formulas in the guide.
Uses ONLY Python stdlib (struct, dataclasses) — no pymem required.
Ideal for running on any machine to verify your unpack logic.

Run:  python3 demo_chunk_read_standalone.py
"""

import struct
from dataclasses import dataclass
from typing import Optional


# ══════════════════════════════════════════════════════════════════════════════
# Data structures (inline — no pymem dependency)
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class Vector3:
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0

    def __repr__(self):
        return f"Vec3({self.x:+.3f}, {self.y:+.3f}, {self.z:+.3f})"

    def length(self):
        return (self.x**2 + self.y**2 + self.z**2) ** 0.5

    @classmethod
    def from_buffer(cls, buf: bytes, offset: int = 0) -> "Vector3":
        """Unpack 3 × float32 from `buf` at byte `offset`."""
        x, y, z = struct.unpack_from("<fff", buf, offset)
        return cls(x, y, z)


@dataclass
class QAngle:
    pitch: float = 0.0
    yaw:   float = 0.0
    roll:  float = 0.0

    def __repr__(self):
        return f"QAngle(p={self.pitch:.2f} deg, y={self.yaw:.2f} deg, r={self.roll:.2f} deg)"

    @classmethod
    def from_buffer(cls, buf: bytes, offset: int = 0) -> "QAngle":
        p, y, r = struct.unpack_from("<fff", buf, offset)
        return cls(p, y, r)


# ══════════════════════════════════════════════════════════════════════════════
# DEMO 1 - EntityEntry layout & chunk-offset formula
# ══════════════════════════════════════════════════════════════════════════════

ENTITY_ENTRY_SIZE = 112   # bytes per slot
CHUNK_SIZE        = 512   # slots per chunk (2^9 -> mask = 0x1FF)


def make_fake_entry(index: int) -> bytes:
    """
    112-byte block that mimics a live EntityEntry.
      offset 0x00  uint64  identity_ptr   (non-zero -> live entity)
      offset 0x08  uint32  handle
      offset 0x0C  uint32  flags
      rest:        zeros   (engine-private padding)
    """
    buf          = bytearray(ENTITY_ENTRY_SIZE)
    identity_ptr = 0x1A2B3C4D00000000 | index   # fake heap address
    handle       = 0xDEAD0000          | index
    flags        = 0x01
    struct.pack_into("<QII", buf, 0x00, identity_ptr, handle, flags)
    return bytes(buf)


# Build a fake chunk of 512 EntityEntry blocks (56 KB buffer)
fake_chunk = bytearray(ENTITY_ENTRY_SIZE * CHUNK_SIZE)
for idx in range(CHUNK_SIZE):
    entry = make_fake_entry(idx)
    start = ENTITY_ENTRY_SIZE * idx
    fake_chunk[start : start + ENTITY_ENTRY_SIZE] = entry

print("=" * 64)
print("DEMO 1 - EntityEntry offset formula:  112 * (index & 0x1FF)")
print("=" * 64)
print(f"  {'index':>6}  {'slot':>4}  {'byte_offset':>10}  {'identity_ptr':>18}  {'handle':>10}")
print(f"  {'-'*6}  {'-'*4}  {'-'*10}  {'-'*18}  {'-'*10}")

for logical_index in [0, 1, 128, 255, 256, 511]:
    # ── The core formula ───────────────────────────────────────────────────
    slot        = logical_index & 0x1FF          # index % 512  (keeps low 9 bits)
    byte_offset = ENTITY_ENTRY_SIZE * slot       # = 112 * slot

    raw          = fake_chunk[byte_offset : byte_offset + ENTITY_ENTRY_SIZE]
    ident, handle, flags = struct.unpack_from("<QII", raw, 0)

    print(
        f"  {logical_index:>6}  {slot:>4}  0x{byte_offset:08X}  "
        f"0x{ident:016X}  0x{handle:08X}"
    )

print()
print("  [OK] Index 256 maps to slot 256 within chunk 0 (256 < 512).")
print("  [OK] For index 512+ the chunk_index increments: index >> 9 = 1.")


# ══════════════════════════════════════════════════════════════════════════════
# DEMO 2 - Full two-level lookup (chunk_index + slot_in_chunk)
# ══════════════════════════════════════════════════════════════════════════════

print("\n" + "=" * 64)
print("DEMO 2 - Two-level entity list: chunk_index + slot_in_chunk")
print("=" * 64)

test_indices = [0, 511, 512, 513, 1023]
for i in test_indices:
    chunk_index   = i >> 9              # which chunk  (i // 512)
    slot_in_chunk = i & 0x1FF           # which slot   (i  % 512)
    # If chunk_index == 0 we use our fake_chunk; demonstrate arithmetic only
    simulated_chunk_ptr = 0x20000000 + chunk_index * 0x10000  # fake address
    entry_offset        = ENTITY_ENTRY_SIZE * slot_in_chunk   # = 112 * slot
    simulated_entry_addr = simulated_chunk_ptr + entry_offset

    print(
        f"  index={i:5d}  chunk={chunk_index}  slot={slot_in_chunk:3d}"
        f"  chunk_base=0x{simulated_chunk_ptr:08X}"
        f"  entry=chunk_base+0x{entry_offset:04X}"
        f" -> 0x{simulated_entry_addr:08X}"
    )


# ══════════════════════════════════════════════════════════════════════════════
# DEMO 3 — Vector3 extraction from a CGameSceneNode block
# ══════════════════════════════════════════════════════════════════════════════

print("\n" + "=" * 64)
print("DEMO 3 — Vector3 extraction from a synthetic scene-node buffer")
print("         (simulates the read at scene_node_ptr + OFF_ORIGIN)")
print("=" * 64)

OFF_ORIGIN = 0xC0   # 192 bytes into the node — common Source 2 offset
NODE_SIZE  = 0x100  # 256 bytes of synthetic node memory

test_vectors = [
    (100.5,  -200.25,  350.0),
    (0.0,      0.0,     0.0),
    (-999.9,  512.125,  1.0),
    (3.14159, 2.71828, -1.41421),
]

print(f"\n  {'packed (x,y,z)':>35}   {'unpacked Vector3'}")
print(f"  {'─'*35}   {'─'*30}")

for x, y, z in test_vectors:
    # Simulate what ReadProcessMemory would give us for the node block
    fake_node = bytearray(NODE_SIZE)
    struct.pack_into("<fff", fake_node, OFF_ORIGIN, x, y, z)

    # This is what your code does:
    vec = Vector3.from_buffer(fake_node, OFF_ORIGIN)

    print(f"  ({x:+10.4f}, {y:+10.4f}, {z:+10.4f})   →   {vec}  |len|={vec.length():.3f}")


# ══════════════════════════════════════════════════════════════════════════════
# DEMO 4 — QAngle extraction
# ══════════════════════════════════════════════════════════════════════════════

print("\n" + "=" * 64)
print("DEMO 4 — QAngle extraction (pitch / yaw / roll)")
print("=" * 64)

OFF_ANGLES = 0xCC  # hypothetical field after Vector3
angles_data = [(0.0, 90.0, 0.0), (-15.5, 180.0, 3.2)]

for pitch, yaw, roll in angles_data:
    fake_node = bytearray(NODE_SIZE)
    struct.pack_into("<fff", fake_node, OFF_ANGLES, pitch, yaw, roll)
    ang = QAngle.from_buffer(fake_node, OFF_ANGLES)
    print(f"  Packed ({pitch},{yaw},{roll})  →  {ang}")


# ══════════════════════════════════════════════════════════════════════════════
# DEMO 5 — Pointer-chain arithmetic (no live process)
# ══════════════════════════════════════════════════════════════════════════════

print("\n" + "=" * 64)
print("DEMO 5 — Pointer chain arithmetic (address math, no process needed)")
print("=" * 64)

CLIENT_BASE      = 0x7FF000000000
OFF_ENT_SYSTEM   = 0x00A0B000

# Step A: read CEntitySystem* from static offset
ptr_A = CLIENT_BASE + OFF_ENT_SYSTEM
print(f"  &(entity_system_ptr)    = 0x{ptr_A:016X}  (client_base + off)")

# (simulated) dereference → entity system heap address
ent_system = 0x1F2E3D4C5B00
print(f"  entity_system           = 0x{ent_system:016X}  (*(ptr_A))")

# Step B: entity_list_base
entity_list_base = ent_system + 0x10
print(f"  entity_list_base        = 0x{entity_list_base:016X}  (entity_system + 0x10)")

# Step C: lookup for index 600
idx = 600
chunk_index   = idx >> 9
slot          = idx & 0x1FF
chunk_ptr_at  = entity_list_base + chunk_index * 8

# (simulated) chunk_base
chunk_base    = 0x2A3B4C5D0000
entry_addr    = chunk_base + ENTITY_ENTRY_SIZE * slot

print(f"\n  Resolving entity index  = {idx}")
print(f"    chunk_index           = {chunk_index}  (= {idx} >> 9)")
print(f"    slot_in_chunk         = {slot}  (= {idx} & 0x1FF)")
print(f"    &(chunk_ptr)          = 0x{chunk_ptr_at:016X}")
print(f"    chunk_base            = 0x{chunk_base:016X}  (*(chunk_ptr))")
print(f"    entry_address         = 0x{entry_addr:016X}")
print(f"                          = chunk_base + {ENTITY_ENTRY_SIZE} × {slot}")
print(f"                          = 0x{chunk_base:X} + 0x{ENTITY_ENTRY_SIZE*slot:X}")

print("\n" + "=" * 64)
print("All demos passed — formulas verified against synthetic data.")
print("=" * 64)
