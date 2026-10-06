"""
cs2_offsets.py
──────────────
Auto-generated from cs2-dumper files via update_offsets.py
Commit / Info: 4116de00 (2026-10-06T09:12:36Z) - Merge pull request #691 from Enoouo/main
Generated at:  2026-10-06 20:22:19 UTC

DO NOT edit by hand — run python update_offsets.py to regenerate.
All values are integer byte offsets (hex).
"""


# ══════════════════════════════════════════════════════════════════════════════
# offsets.cs  →  Module-level static pointers
# These are offsets FROM the module base address to a pointer stored in the
# module's data segment.  Dereference once to get the runtime object address.
# ══════════════════════════════════════════════════════════════════════════════

class ClientDll:
    """Static pointers inside client.dll  (client_base + offset → pointer)."""

    # ── Entity system ──────────────────────────────────────────────────────
    dwEntityList                          = 0x2717828   # CGameEntitySystem*
    dwGameEntitySystem                    = 0x2717828   # alias (same address)
    dwGameEntitySystem_highestEntityIndex = 0x2120      # field WITHIN the system

    # ── Local player ──────────────────────────────────────────────────────
    dwLocalPlayerController               = 0x253A068   # CCSPlayerController*
    dwLocalPlayerPawn                     = 0x2562808   # C_CSPlayerPawn*

    # ── View / camera ─────────────────────────────────────────────────────
    dwViewAngles                          = 0x25787E8   # QAngle
    dwViewMatrix                          = 0x2567FA0   # ViewMatrix4x4
    dwViewRender                          = 0x2568968   # CViewRender*

    # ── Input ──────────────────────────────────────────────────────────────
    dwCSGOInput                           = 0x2578160   # CCSGOInput*

    # ── Misc ───────────────────────────────────────────────────────────────
    dwGameRules                           = 0x255EE50   # CCSGameRules*
    dwGlobalVars                          = 0x222DE98   # CGlobalVarsBase*
    dwGlowManager                         = 0x255EE60   # CGlowManager*
    dwSensitivity                         = 0x255F998   # CSensitivity*
    dwSensitivity_sensitivity             = 0x58        # float field within it
    dwPlantedC4                           = 0x24CA930   # C_PlantedC4*
    dwPrediction                          = 0x2562710   # CPrediction*
    dwWeaponC4                            = 0x24C6AF0   # C_C4*


class Engine2Dll:
    """Static pointers inside engine2.dll  (engine2_base + offset → pointer)."""

    dwBuildNumber                         = 0x61CFE8
    dwNetworkGameClient                   = 0x91AFC0    # CNetworkGameClient*
    dwNetworkGameClient_clientTickCount   = 0x398       # int field within it
    dwNetworkGameClient_deltaTick         = 0x24C
    dwNetworkGameClient_isBackgroundMap   = 0x2C143F
    dwNetworkGameClient_localPlayer       = 0xF8
    dwNetworkGameClient_maxClients        = 0x240
    dwNetworkGameClient_serverTickCount   = 0x24C
    dwNetworkGameClient_signOnState       = 0x230
    dwWindowHeight                        = 0x91F334
    dwWindowWidth                         = 0x91F330


class Buttons:
    """Static button offsets in client.dll."""
    attack                                = 0x2231FD0
    attack2                               = 0x2232060
    jump                                  = 0x22324E0
    duck                                  = 0x2232570
    forward                               = 0x2232210
    back                                  = 0x22322A0
    left                                  = 0x2232330
    right                                 = 0x22323C0

# ══════════════════════════════════════════════════════════════════════════════
# client_dll.cs  →  Field offsets within C++ objects
# These are offsets FROM the start of an object instance to a member field.
# ══════════════════════════════════════════════════════════════════════════════

class C_BaseEntity:
    """
    Source: client_dll.cs — C_BaseEntity (and C_BasePlayerPawn which inherits it)
    Field offsets relative to the entity object pointer.
    """
    m_pGameSceneNode = 0x330    # CGameSceneNode*
    m_iHealth        = 0x34C    # int32
    m_lifeState      = 0x354    # uint8  (0=alive)
    m_iTeamNum       = 0x3E7    # uint8  (2=T,3=CT)
    m_fFlags         = 0x3F4    # uint32 (bit 1 = FL_DUCKING)
    m_vecVelocity    = 0x430    # CNetworkVelocityVector → 3×float


class CGameSceneNode:
    """
    Source: client_dll.cs — CGameSceneNode
    Field offsets relative to the scene-node pointer.
    """
    m_vecAbsOrigin   = 0xC8     # VectorWS / Vector3


class C_CSPlayerPawn:
    """
    Source: client_dll.cs — C_CSPlayerPawnBase (inherits C_BasePlayerPawn)
    Player-specific fields.
    """
    m_angEyeAngles   = 0x35F0   # QAngle  pitch/yaw/roll


class CCSPlayerController:
    """
    Source: client_dll.cs — CCSPlayerController
    Controller (scoreboard) → Pawn linkage.
    """
    m_hPlayerPawn    = 0x92C    # CHandle<C_CSPlayerPawn>
    m_iszPlayerName  = 0x6FC    # char[128] player name
    m_bPawnIsAlive   = 0x934    # bool
    m_iPawnHealth    = 0x938    # uint32


# ══════════════════════════════════════════════════════════════════════════════
# Derived / computed constants (do not change)
# ══════════════════════════════════════════════════════════════════════════════

ENTITY_ENTRY_SIZE  = 112    # sizeof(EntityEntry) in the entity list chunk
ENTITY_CHUNK_SIZE  = 512    # slots per chunk  (2^9)
ENTITY_CHUNK_MASK  = 0x1FF  # = CHUNK_SIZE - 1, used for (index & CHUNK_MASK)
MAX_ENTITIES       = 64     # dwGameEntitySystem_highestEntityIndex typical max

# ── Life-state values (m_lifeState) ───────────────────────────────────────
LIFE_ALIVE = 0
LIFE_DYING = 1
LIFE_DEAD  = 2

# ── Team numbers (m_iTeamNum) ──────────────────────────────────────────────
TEAM_UNASSIGNED = 0
TEAM_SPECTATOR  = 1
TEAM_T          = 2
TEAM_CT         = 3
