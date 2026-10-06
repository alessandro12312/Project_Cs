"""
demo_chunk_read.py
──────────────────
Standalone demo script that exercises the core formulas WITHOUT needing
a live process attached. Uses a synthetic in-memory byte buffer to simulate
what you'd get from ReadProcessMemory — ideal for unit-testing your unpack
logic before connecting to a real engine.

Run:  python demo_chunk_read.py
"""

import struct
import random
from memory_reader import Vector3, QAngle

# ─── Simulate a 112-byte EntityEntry block ───────────────────────────────────

def make_fake_entry(index: int) -> bytes:
    """
    Build a realistic-looking 112-byte EntityEntry for index `index`.
    identity_ptr  @ offset 0   (uint64) — non-zero to signal "live"
    handle        @ offset 8   (uint32)
    flags         @ offset 12  (uint32)
    (rest padded with zeros)
    """
    buf = bytearray(112)
    identity_ptr = 0x1A2B3C4D5E000000 + index   # fake heap address
    handle       = 0xABCD0000 | index
    flags        = 0x01
    struct.pack_into("<QII", buf, 0, identity_ptr, handle, flags)
    return bytes(buf)


# ─── Simulate a chunk of 512 entries ─────────────────────────────────────────

ENTITY_ENTRY_SIZE = 112
CHUNK_SIZE        = 512

fake_chunk = bytearray(ENTITY_ENTRY_SIZE * CHUNK_SIZE)
for idx in range(CHUNK_SIZE):
    entry = make_fake_entry(idx)
    fake_chunk[ENTITY_ENTRY_SIZE * idx : ENTITY_ENTRY_SIZE * (idx + 1)] = entry

# ─── Demonstrate the offset formula ──────────────────────────────────────────

print("=" * 60)
print("DEMO: EntityEntry lookup formula")
print("      offset = 112 * (index & 0x1FF)")
print("=" * 60)

for logical_index in [0, 1, 255, 256, 511]:
    slot        = logical_index & 0x1FF          # always < 512
    byte_offset = ENTITY_ENTRY_SIZE * slot       # = 112 * slot

    raw   = fake_chunk[byte_offset : byte_offset + ENTITY_ENTRY_SIZE]
    ident, handle, flags = struct.unpack_from("<QII", raw, 0)

    print(
        f"  index={logical_index:4d}  slot={slot:4d}  "
        f"byte_offset=0x{byte_offset:05X}  "
        f"identity=0x{ident:016X}  handle=0x{handle:08X}"
    )

# ─── Demonstrate Vector3 from_buffer ─────────────────────────────────────────

print("\n" + "=" * 60)
print("DEMO: Vector3 unpack from synthetic CGameSceneNode block")
print("=" * 60)

OFF_ORIGIN = 0xC0   # 192 bytes into the node

# Build a fake 256-byte node block
fake_node = bytearray(256)
test_positions = [
    (100.5, -200.25, 350.0),
    (0.0,   0.0,    0.0),
    (-999.9, 512.1, 1.0),
]

for i, (x, y, z) in enumerate(test_positions):
    # In a real scenario there would be one node per entity;
    # here we write into the same buffer to show how offsets work.
    struct.pack_into("<fff", fake_node, OFF_ORIGIN, x, y, z)
    vec = Vector3.from_buffer(fake_node, OFF_ORIGIN)
    print(f"  Packed ({x:+.2f}, {y:+.2f}, {z:+.2f})  ->  read back: {vec}")

# ─── Demonstrate pointer-chain concept (address arithmetic only) ──────────────

print("\n" + "=" * 60)
print("DEMO: Pointer chain arithmetic (no live process needed)")
print("=" * 60)

CLIENT_BASE       = 0x7FF000000000   # hypothetical module base
OFF_ENT_SYSTEM    = 0x00A0B000       # hypothetical static offset
ENT_SYSTEM_ADDR   = 0x1F2E3D4C5B    # hypothetical heap allocation
ENT_LIST_OFFSET   = 0x10
CHUNK_0_ADDR      = 0x2A3B4C5D6E    # hypothetical chunk 0 pointer value

# Simulate the chain without actual reads:
step1 = CLIENT_BASE + OFF_ENT_SYSTEM
step2 = ENT_SYSTEM_ADDR            # *(uint64*)step1  (simulated)
step3 = step2 + ENT_LIST_OFFSET    # entity_list_base

logical_idx   = 42
chunk_idx     = logical_idx >> 9
slot          = logical_idx & 0x1FF
chunk_ptr_at  = step3 + chunk_idx * 8
chunk_base    = CHUNK_0_ADDR       # *(uint64*)chunk_ptr_at (simulated)
entry_addr    = chunk_base + ENTITY_ENTRY_SIZE * slot

print(f"  client_base            = 0x{CLIENT_BASE:016X}")
print(f"  + off_entity_system    = 0x{step1:016X}")
print(f"  *(ptr) -> entity_system = 0x{step2:016X}")
print(f"  + off_entity_list      = 0x{step3:016X}  (entity_list_base)")
print(f"  Logical index          = {logical_idx}")
print(f"    chunk_index          = {chunk_idx}   (index >> 9)")
print(f"    slot_in_chunk        = {slot}  (index & 0x1FF)")
print(f"  chunk_ptr lives at     = 0x{chunk_ptr_at:016X}")
print(f"  *(ptr) -> chunk_base    = 0x{chunk_base:016X}")
print(f"  entry_address          = 0x{entry_addr:016X}")
print(f"                           = chunk_base + 112 * {slot}")
print(f"                           = 0x{chunk_base:X} + 0x{112*slot:X}")
