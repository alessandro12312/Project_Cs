"""
memory_reader.py
────────────────
External telemetry library for CS2 (Source 2 architecture).
Uses real offsets from cs2_offsets.py — generated 2026-10-06.

Usage:
    from memory_reader import CS2Telemetry
    telem = CS2Telemetry()
    for player in telem.get_players():
        print(player)
"""

from __future__ import annotations

import struct
import time
from dataclasses import dataclass, field
from typing import Iterator, Optional

# ── import our real offsets ────────────────────────────────────────────────
from cs2_offsets import (
    ClientDll, Engine2Dll,
    C_BaseEntity, CGameSceneNode, C_CSPlayerPawn, CCSPlayerController,
    ENTITY_ENTRY_SIZE, ENTITY_CHUNK_SIZE, ENTITY_CHUNK_MASK,
    LIFE_ALIVE, TEAM_T, TEAM_CT,
)

try:
    import pymem
    import pymem.process
    import pymem.exception
except ImportError:
    raise SystemExit("pymem required:  pip install pymem")


# ══════════════════════════════════════════════════════════════════════════════
# Primitive types
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class Vector3:
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0

    def __repr__(self):
        return f"({self.x:+.2f}, {self.y:+.2f}, {self.z:+.2f})"

    def length(self):
        return (self.x**2 + self.y**2 + self.z**2) ** 0.5

    @classmethod
    def from_buffer(cls, buf: bytes, offset: int = 0) -> "Vector3":
        x, y, z = struct.unpack_from("<fff", buf, offset)
        return cls(x, y, z)

    @classmethod
    def read(cls, pm: pymem.Pymem, addr: int) -> "Vector3":
        raw = pm.read_bytes(addr, 12)
        return cls.from_buffer(raw)


@dataclass
class QAngle:
    pitch: float = 0.0
    yaw:   float = 0.0
    roll:  float = 0.0

    def __repr__(self):
        return f"QAngle(p={self.pitch:.1f}°, y={self.yaw:.1f}°)"

    @classmethod
    def from_buffer(cls, buf: bytes, offset: int = 0) -> "QAngle":
        p, y, r = struct.unpack_from("<fff", buf, offset)
        return cls(p, y, r)

    @classmethod
    def read(cls, pm: pymem.Pymem, addr: int) -> "QAngle":
        raw = pm.read_bytes(addr, 12)
        return cls.from_buffer(raw)


# ══════════════════════════════════════════════════════════════════════════════
# Player snapshot — what we capture each tick
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class PlayerSnapshot:
    """
    One frame of telemetry data for a single player entity.
    All fields sourced from the dump offsets in cs2_offsets.py.
    """
    entity_index: int           # slot in the entity list (0-63)
    team:         int           # 2 = T,  3 = CT,  other = spectator/unassigned
    health:       int           # 0-100
    life_state:   int           # 0 = alive
    origin:       Vector3       # world position (m_vecAbsOrigin)
    velocity:     Vector3       # movement vector (m_vecVelocity)
    eye_angles:   QAngle        # aim direction  (m_angEyeAngles)
    flags:        int = 0       # entity flags (m_fFlags, bit 1 = crouch)
    name:         str = ""      # player name (m_iszPlayerName)

    @property
    def is_alive(self) -> bool:
        return self.life_state == LIFE_ALIVE and self.health > 0

    @property
    def is_crouching(self) -> bool:
        """Returns True if player is ducked / crouching (FL_DUCKING bit set)."""
        return bool(self.flags & (1 << 1))

    @property
    def team_name(self) -> str:
        return {2: "T", 3: "CT"}.get(self.team, "SPEC")

    def __repr__(self):
        alive = "ALIVE" if self.is_alive else "DEAD "
        name_str = f" '{self.name}'" if self.name else ""
        return (
            f"[{self.entity_index:2d}]{name_str} [{self.team_name}] {alive}"
            f"  HP={self.health:3d}"
            f"  pos={self.origin}"
            f"  vel={self.velocity.length():5.1f} u/s"
            f"  aim={self.eye_angles}"
        )


# ══════════════════════════════════════════════════════════════════════════════
# Core memory reader
# ══════════════════════════════════════════════════════════════════════════════

class CS2Telemetry:
    """
    Attaches to CS2 and exposes real-time player telemetry.

    Offset sources:
        dwEntityList        → ClientDll.dwEntityList        (0x2717828)
        m_pGameSceneNode    → C_BaseEntity.m_pGameSceneNode (0x330)
        m_vecAbsOrigin      → CGameSceneNode.m_vecAbsOrigin (0xC8)
        m_iHealth           → C_BaseEntity.m_iHealth        (0x34C)
        m_lifeState         → C_BaseEntity.m_lifeState      (0x354)
        m_iTeamNum          → C_BaseEntity.m_iTeamNum       (0x3E7)
        m_vecVelocity       → C_BaseEntity.m_vecVelocity    (0x430)
        m_angEyeAngles      → C_CSPlayerPawn.m_angEyeAngles (0x35F0)
    """

    PROCESS = "cs2.exe"
    MODULE  = "client.dll"

    def __init__(self):
        self.pm: pymem.Pymem = pymem.Pymem(self.PROCESS)
        mod = pymem.process.module_from_name(self.pm.process_handle, self.MODULE)
        if mod is None:
            raise RuntimeError(f"Module '{self.MODULE}' not found in '{self.PROCESS}'")
        self.client_base: int = mod.lpBaseOfDll
        print(
            f"[+] CS2Telemetry attached\n"
            f"    process    : {self.PROCESS}\n"
            f"    client.dll : 0x{self.client_base:016X}\n"
            f"    dwEntityList offset: 0x{ClientDll.dwEntityList:X}"
        )

    # ── Low-level helpers ──────────────────────────────────────────────────

    def _ptr(self, addr: int) -> int:
        """Read a 64-bit pointer."""
        return self.pm.read_longlong(addr)

    def _bytes(self, addr: int, size: int) -> bytes:
        """Read raw bytes — one syscall."""
        return self.pm.read_bytes(addr, size)

    # ── Entity list navigation ─────────────────────────────────────────────

    def _entity_list_base(self) -> int:
        """
        Direct base address of CGameEntitySystem (dereferencing client_base + dwEntityList).
        """
        try:
            return self._ptr(self.client_base + ClientDll.dwEntityList)
        except Exception:
            return 0

    def _entry_address(self, entity_list_base: int, index: int) -> int:
        """
        Two-level entity list lookup for CS2 Source 2 architecture.
        """
        if entity_list_base == 0:
            return 0
        chunk_index   = index >> 9
        slot_in_chunk = index & ENTITY_CHUNK_MASK   # = index & 0x1FF

        chunk_ptr_addr = entity_list_base + 0x10 + chunk_index * 8
        try:
            chunk_base = self._ptr(chunk_ptr_addr)
        except Exception:
            return 0

        if chunk_base == 0:
            return 0

        return chunk_base + ENTITY_ENTRY_SIZE * slot_in_chunk

    # ── Player snapshot ───────────────────────────────────────────────────

    def _read_player(self, entity_list_base: int, index: int) -> Optional[PlayerSnapshot]:
        """
        Try to build a PlayerSnapshot for entity slot `index` (1 to 64).
        In CS2 Source 2, slot `index` contains a CCSPlayerController.
        We read m_hPlayerPawn (0x92C) to resolve the active C_CSPlayerPawn.
        """
        try:
            # ── A: read controller from chunk 0 ──────────────────────────
            ctrl_entry_addr = self._entry_address(entity_list_base, index)
            if ctrl_entry_addr == 0:
                return None

            ctrl_ptr = self._ptr(ctrl_entry_addr)
            if ctrl_ptr == 0:
                return None

            # ── B: read pawn handle from controller ──────────────────────
            pawn_handle = self.pm.read_uint(ctrl_ptr + CCSPlayerController.m_hPlayerPawn)
            if pawn_handle == 0 or pawn_handle == 0xFFFFFFFF:
                return None

            # Player name from controller
            try:
                name = self.pm.read_string(ctrl_ptr + CCSPlayerController.m_iszPlayerName, 32)
            except Exception:
                name = ""

            # ── C: resolve C_CSPlayerPawn from handle ────────────────────
            pawn_idx = pawn_handle & 0x7FFF
            pawn_entry_addr = self._entry_address(entity_list_base, pawn_idx)
            if pawn_entry_addr == 0:
                return None

            pawn_ptr = self._ptr(pawn_entry_addr)
            if pawn_ptr == 0:
                return None

            # Skip local player pawn if known
            if hasattr(self, "_local_pawn_ptr") and pawn_ptr == self._local_pawn_ptr:
                return None

            # ── D: read pawn properties ──────────────────────────────────
            scene_node_ptr = self._ptr(pawn_ptr + C_BaseEntity.m_pGameSceneNode)
            if scene_node_ptr == 0:
                return None

            origin     = Vector3.read(self.pm, scene_node_ptr + CGameSceneNode.m_vecAbsOrigin)
            health     = self.pm.read_int(pawn_ptr + C_BaseEntity.m_iHealth)
            life_state = self.pm.read_bytes(pawn_ptr + C_BaseEntity.m_lifeState, 1)[0]

            # Filter uninitialized coordinates, dead or invalid pawns
            if origin.x == 0.0 and origin.y == 0.0 and origin.z == 0.0:
                return None
            if health <= 0 or life_state != 0:
                return None

            team       = self.pm.read_bytes(pawn_ptr + C_BaseEntity.m_iTeamNum, 1)[0]
            flags      = self.pm.read_uint(pawn_ptr + C_BaseEntity.m_fFlags)
            velocity   = Vector3.read(self.pm, pawn_ptr + C_BaseEntity.m_vecVelocity)
            eye_angles = QAngle.read(self.pm, pawn_ptr + C_CSPlayerPawn.m_angEyeAngles)

            return PlayerSnapshot(
                entity_index = index,
                team         = team,
                health       = health,
                life_state   = life_state,
                origin       = origin,
                velocity     = velocity,
                eye_angles   = eye_angles,
                flags        = flags,
                name         = name,
            )

        except (pymem.exception.MemoryReadError, struct.error, ValueError):
            return None

    # ── Public API ─────────────────────────────────────────────────────────

    def get_players(self, alive_only: bool = False) -> list[PlayerSnapshot]:
        """
        Return a snapshot of all player entities this tick.

        Parameters
        ----------
        alive_only : if True, skip dead / spectating players.
        """
        try:
            # Refresh local player pawn pointer to ensure self is skipped during entity scan
            try:
                self._local_pawn_ptr = self._ptr(self.client_base + ClientDll.dwLocalPlayerPawn)
            except Exception:
                self._local_pawn_ptr = 0

            entity_list_base = self._entity_list_base()
            if entity_list_base == 0:
                return []

            players: list[PlayerSnapshot] = []
            for i in range(1, 65):
                snap = self._read_player(entity_list_base, i)
                if snap is None:
                    continue
                if snap.team not in (TEAM_T, TEAM_CT):
                    continue   # skip world entities, spectators
                if alive_only and not snap.is_alive:
                    continue
                players.append(snap)

            return players
        except Exception:
            return []

    def get_local_player(self) -> Optional[PlayerSnapshot]:
        """
        Read the local player directly via the dwLocalPlayerPawn static pointer.
        Faster than scanning the full entity list.
        """
        try:
            pawn_ptr = self._ptr(self.client_base + ClientDll.dwLocalPlayerPawn)
            if pawn_ptr == 0:
                return None
            self._local_pawn_ptr = pawn_ptr

            scene_node_ptr = self._ptr(pawn_ptr + C_BaseEntity.m_pGameSceneNode)
            if scene_node_ptr == 0:
                return None
            origin     = Vector3.read(self.pm, scene_node_ptr + CGameSceneNode.m_vecAbsOrigin)
            velocity   = Vector3.read(self.pm, pawn_ptr + C_BaseEntity.m_vecVelocity)
            eye_angles = QAngle.read(self.pm, pawn_ptr + C_CSPlayerPawn.m_angEyeAngles)

            health     = self.pm.read_int(pawn_ptr + C_BaseEntity.m_iHealth)
            life_state = self.pm.read_bytes(pawn_ptr + C_BaseEntity.m_lifeState, 1)[0]
            team       = self.pm.read_bytes(pawn_ptr + C_BaseEntity.m_iTeamNum, 1)[0]
            flags      = self.pm.read_uint(pawn_ptr + C_BaseEntity.m_fFlags)

            name = "LocalPlayer"
            try:
                ctrl_ptr = self._ptr(self.client_base + ClientDll.dwLocalPlayerController)
                if ctrl_ptr != 0:
                    n = self.pm.read_string(ctrl_ptr + CCSPlayerController.m_iszPlayerName, 32)
                    if n:
                        name = n
            except Exception:
                pass

            return PlayerSnapshot(
                entity_index = -1,
                team         = team,
                health       = health,
                life_state   = life_state,
                origin       = origin,
                velocity     = velocity,
                eye_angles   = eye_angles,
                flags        = flags,
                name         = name,
            )
        except (pymem.exception.MemoryReadError, ValueError):
            return None

    def get_view_angles(self) -> Optional[QAngle]:
        """Read the camera view angles from dwViewAngles."""
        try:
            addr = self.client_base + ClientDll.dwViewAngles
            return QAngle.read(self.pm, addr)
        except pymem.exception.MemoryReadError:
            return None


# ══════════════════════════════════════════════════════════════════════════════
# Live telemetry loop
# ══════════════════════════════════════════════════════════════════════════════

def run_telemetry(poll_hz: float = 20.0) -> None:
    """Attach to CS2 and print player data at `poll_hz` Hz."""
    telem = CS2Telemetry()
    tick  = 0
    print(f"\n[+] Polling at {poll_hz} Hz  (Ctrl-C to stop)\n")

    while True:
        t0      = time.perf_counter()
        players = telem.get_players()
        local   = telem.get_local_player()
        view    = telem.get_view_angles()
        elapsed = time.perf_counter() - t0

        print(f"── Tick {tick:05d}  ({len(players)} players)  read={elapsed*1000:.1f} ms ──")

        if local:
            print(f"  LOCAL  HP={local.health:3d}  pos={local.origin}  aim={view or local.eye_angles}")

        ts = sorted(players, key=lambda p: (p.team, p.entity_index))
        for p in ts:
            print(f"  {p}")

        tick += 1
        time.sleep(max(0.0, 1.0 / poll_hz - elapsed))


if __name__ == "__main__":
    run_telemetry(poll_hz=20.0)
