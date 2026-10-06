"""
validate_offsets.py
───────────────────
Step-by-step single-entity validation script.
Offsets are now loaded from cs2_offsets.py (real values from your dump).

Instructions:
  1. Launch CS2 and load a map
  2. Run:  python validate_offsets.py
  3. Read the output — each step should show a non-zero address
"""

import struct
import sys

# ── Load REAL offsets from your dump ──────────────────────────────────────────
from cs2_offsets import (
    ClientDll, C_BaseEntity, CGameSceneNode,
    ENTITY_ENTRY_SIZE,
)

PROCESS_NAME      = "cs2.exe"
MODULE_NAME       = "client.dll"

# Map to the names the rest of the script uses
OFF_ENTITY_SYSTEM = ClientDll.dwEntityList          # 0x2717828
OFF_ENTITY_LIST   = 0x10                            # field within CGameEntitySystem
OFF_SCENE_NODE    = C_BaseEntity.m_pGameSceneNode   # 0x330
OFF_ORIGIN        = CGameSceneNode.m_vecAbsOrigin   # 0xC8
# ──────────────────────────────────────────────────────────────────────────────

try:
    import pymem
    import pymem.process
    import pymem.exception
except ImportError:
    sys.exit("pymem not installed.  Run:  pip install pymem")


def sep(label: str):
    print(f"\n{'-'*56}")
    print(f"  {label}")
    print(f"{'-'*56}")


def validate():
    # ── Step 1: attach ────────────────────────────────────────────────────────
    sep("Step 1 - Attaching to process")
    try:
        pm = pymem.Pymem(PROCESS_NAME)
    except pymem.exception.ProcessNotFound:
        sys.exit(f"[-] Process '{PROCESS_NAME}' not found. Is the game running?")

    mod = pymem.process.module_from_name(pm.process_handle, MODULE_NAME)
    if mod is None:
        sys.exit(f"[-] Module '{MODULE_NAME}' not found in '{PROCESS_NAME}'.")

    base = mod.lpBaseOfDll
    print(f"[+] Attached to '{PROCESS_NAME}'")
    print(f"[+] '{MODULE_NAME}' base address = 0x{base:016X}")

    # ── Step 2: resolve CEntitySystem pointer ─────────────────────────────────
    sep("Step 2 - Resolving CGameEntitySystem pointer")
    ent_sys_ptr_addr = base + OFF_ENTITY_SYSTEM
    print(f"    &(entity_system) = 0x{ent_sys_ptr_addr:016X}  (base + 0x{OFF_ENTITY_SYSTEM:X})")
    try:
        ent_sys = pm.read_longlong(ent_sys_ptr_addr)
    except pymem.exception.MemoryReadError:
        sys.exit("[-] Cannot read CGameEntitySystem pointer.")

    if ent_sys == 0:
        sys.exit("[-] CGameEntitySystem pointer is NULL. Is the game running and map loaded?")
    print(f"[+] CGameEntitySystem  = 0x{ent_sys:016X}")

    # ── Step 3: read chunk[0] pointer ─────────────────────────────────────────
    sep("Step 3 - Reading entity list / chunk[0] pointer")
    chunk_ptr_addr = ent_sys + OFF_ENTITY_LIST
    print(f"    &(chunk[0])      = 0x{chunk_ptr_addr:016X}  (ent_sys + 0x{OFF_ENTITY_LIST:X})")

    try:
        chunk0_base = pm.read_longlong(chunk_ptr_addr)
    except pymem.exception.MemoryReadError:
        sys.exit("[-] Cannot read chunk[0] pointer — off_entity_list may be wrong.")

    if chunk0_base == 0:
        sys.exit(
            "[-] chunk[0] pointer == NULL\n"
            "    -> Make sure the match/map is loaded."
        )
    print(f"[+] chunk[0] base      = 0x{chunk0_base:016X}")

    # ── Step 4: read CCSPlayerController ──────────────────────────────────────
    sep("Step 4 - Reading CCSPlayerController from chunk[0]")
    ctrl_ptr = 0
    ctrl_slot = 0
    for probe in range(1, 16):
        ptr = pm.read_longlong(chunk0_base + 112 * probe)
        if ptr != 0:
            ctrl_ptr = ptr
            ctrl_slot = probe
            break

    if ctrl_ptr == 0:
        sys.exit("[-] No active player controller found in slots 1-15.")

    print(f"[+] Found player controller at slot {ctrl_slot}: 0x{ctrl_ptr:016X}")
    try:
        name = pm.read_string(ctrl_ptr + 0x6FC, 32)
        print(f"    Player Name      = '{name}'")
    except Exception:
        pass

    # ── Step 5: resolve m_hPlayerPawn handle to C_CSPlayerPawn ────────────────
    sep("Step 5 - Resolving m_hPlayerPawn -> C_CSPlayerPawn")
    OFF_PLAYER_PAWN = 0x92C
    pawn_handle = pm.read_uint(ctrl_ptr + OFF_PLAYER_PAWN)
    print(f"    pawn_handle      = 0x{pawn_handle:08X}")
    if pawn_handle == 0 or pawn_handle == 0xFFFFFFFF:
        sys.exit("[-] Controller has no active pawn handle (dead/spectator).")

    pawn_idx = pawn_handle & 0x7FFF
    pawn_chunk_addr = ent_sys + 0x10 + 8 * (pawn_idx >> 9)
    pawn_chunk = pm.read_longlong(pawn_chunk_addr)
    if pawn_chunk == 0:
        sys.exit(f"[-] Pawn chunk is NULL for pawn_idx={pawn_idx}.")

    pawn_ptr = pm.read_longlong(pawn_chunk + 112 * (pawn_idx & 0x1FF))
    if pawn_ptr == 0:
        sys.exit(f"[-] Pawn pointer is NULL for pawn_idx={pawn_idx}.")
    print(f"[+] C_CSPlayerPawn     = 0x{pawn_ptr:016X} (index={pawn_idx})")

    # ── Step 6: follow pawn -> scene node ──────────────────────────────────────
    sep("Step 6 - Following C_CSPlayerPawn -> CGameSceneNode")
    node_ptr_addr = pawn_ptr + OFF_SCENE_NODE
    print(f"    &(scene_node)    = 0x{node_ptr_addr:016X}  (pawn + 0x{OFF_SCENE_NODE:X})")

    try:
        scene_node = pm.read_longlong(node_ptr_addr)
    except pymem.exception.MemoryReadError:
        sys.exit("[-] Cannot read scene_node pointer.")

    if scene_node == 0:
        sys.exit("[-] scene_node == NULL")
    print(f"[+] CGameSceneNode     = 0x{scene_node:016X}")

    # ── Step 7: read Vector3 origin ───────────────────────────────────────────
    sep("Step 7 - Reading m_vecAbsOrigin (Vector3)")
    origin_addr = scene_node + OFF_ORIGIN
    print(f"    &(origin)        = 0x{origin_addr:016X}  (scene_node + 0x{OFF_ORIGIN:X})")

    try:
        raw_origin = pm.read_bytes(origin_addr, 12)
    except pymem.exception.MemoryReadError:
        sys.exit("[-] Cannot read origin - off_origin may be wrong.")

    x, y, z = struct.unpack("<fff", raw_origin)
    print(f"[+] m_vecAbsOrigin     = ({x:.3f}, {y:.3f}, {z:.3f})")

    # ── Final verdict ──────────────────────────────────────────────────────────
    sep("Result")
    print("  [+] ALL STEPS PASSED")
    print()
    print("  Your config block for memory_reader.py:")
    print()
    print("  cfg = OffsetConfig(")
    print(f'      process_name       = "{PROCESS_NAME}",')
    print(f'      module_name        = "{MODULE_NAME}",')
    print(f"      off_entity_system  = 0x{OFF_ENTITY_SYSTEM:X},")
    print(f"      off_entity_list    = 0x{OFF_ENTITY_LIST:X},")
    print(f"      off_scene_node     = 0x{OFF_SCENE_NODE:X},")
    print(f"      off_origin         = 0x{OFF_ORIGIN:X},")
    print("      entity_entry_size  = 112,")
    print("      chunk_size         = 512,")
    print("      max_entities       = 1024,")
    print("  )")
    print()


if __name__ == "__main__":
    validate()
