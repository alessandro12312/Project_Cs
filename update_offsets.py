"""
update_offsets.py
─────────────────
Automated updater for CS2 offsets.
Fetches the latest offsets directly from https://github.com/a2x/cs2-dumper,
updates local .cs files (offsets.cs, client_dll.cs, buttons.cs),
regenerates cs2_offsets.py, and syncs spatial_telemetry.py.

Usage:
    python update_offsets.py          # Checks & updates from GitHub, verifies with CS2
    python update_offsets.py --local  # Regenerates from existing local .cs files only
"""

import sys
import re
import os
import json
import urllib.request
import urllib.error
from datetime import datetime, timezone

REPO_API_URL = "https://api.github.com/repos/a2x/cs2-dumper/commits?per_page=1"
RAW_BASE_URL = "https://raw.githubusercontent.com/a2x/cs2-dumper/main/output/"

FILES_TO_DOWNLOAD = [
    "offsets.cs",
    "client_dll.cs",
    "buttons.cs",
]

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def log(msg: str):
    print(f"[+] {msg}")


def log_warn(msg: str):
    print(f"[!] {msg}")


def log_err(msg: str):
    print(f"[-] {msg}")


def download_file(filename: str) -> str:
    url = f"{RAW_BASE_URL}{filename}"
    log(f"Downloading {filename} from {url} ...")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        content = resp.read().decode("utf-8")
    local_path = os.path.join(BASE_DIR, filename)
    with open(local_path, "w", encoding="utf-8") as f:
        f.write(content)
    log(f"Saved {filename} ({len(content)} bytes)")
    return content


def get_latest_commit_info():
    req = urllib.request.Request(REPO_API_URL, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if data and len(data) > 0:
                c = data[0]
                sha = c["sha"][:8]
                date = c["commit"]["committer"]["date"]
                msg = c["commit"]["message"].split("\n")[0]
                return sha, date, msg
    except Exception as e:
        log_warn(f"Could not retrieve GitHub commit info: {e}")
    return "unknown", datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"), "Manual update"


def parse_cs_class(text: str, class_name: str) -> dict:
    """Extracts 'public const nint VarName = 0x...;' from a specific C# static class."""
    pattern = rf'public static class {class_name}\s*\{{(.*?)\}}'
    m = re.search(pattern, text, re.DOTALL)
    if not m:
        return {}
    body = m.group(1)
    fields = {}
    for fm in re.finditer(r'public const nint\s+(\w+)\s*=\s*(0x[0-9a-fA-F]+|\d+);', body):
        name = fm.group(1)
        val = int(fm.group(2), 0)
        fields[name] = val
    return fields


def find_field_offset(text: str, field_name: str) -> int:
    """Finds the offset of a field anywhere in C# file."""
    m = re.search(rf'public const nint\s+{field_name}\s*=\s*(0x[0-9a-fA-F]+|\d+);', text)
    if m:
        return int(m.group(1), 0)
    return 0


def generate_cs2_offsets_py(offsets_cs: str, client_cs: str, commit_info: str) -> str:
    """Regenerates cs2_offsets.py with extracted values."""
    client_dll = parse_cs_class(offsets_cs, "ClientDll")
    engine2_dll = parse_cs_class(offsets_cs, "Engine2Dll")

    # C_BaseEntity
    base_ent = parse_cs_class(client_cs, "C_BaseEntity")
    m_pGameSceneNode = base_ent.get("m_pGameSceneNode", find_field_offset(client_cs, "m_pGameSceneNode"))
    m_iHealth = base_ent.get("m_iHealth", find_field_offset(client_cs, "m_iHealth"))
    m_lifeState = base_ent.get("m_lifeState", find_field_offset(client_cs, "m_lifeState"))
    m_iTeamNum = base_ent.get("m_iTeamNum", find_field_offset(client_cs, "m_iTeamNum"))
    m_fFlags = base_ent.get("m_fFlags", find_field_offset(client_cs, "m_fFlags"))
    m_vecVelocity = base_ent.get("m_vecVelocity", find_field_offset(client_cs, "m_vecVelocity"))

    # CGameSceneNode
    scene_node = parse_cs_class(client_cs, "CGameSceneNode")
    m_vecAbsOrigin = scene_node.get("m_vecAbsOrigin", find_field_offset(client_cs, "m_vecAbsOrigin"))

    # C_CSPlayerPawn
    m_angEyeAngles = find_field_offset(client_cs, "m_angEyeAngles")

    # CCSPlayerController
    ctrl = parse_cs_class(client_cs, "CCSPlayerController")
    m_hPlayerPawn = ctrl.get("m_hPlayerPawn", find_field_offset(client_cs, "m_hPlayerPawn"))
    m_iszPlayerName = find_field_offset(client_cs, "m_iszPlayerName")
    m_bPawnIsAlive = ctrl.get("m_bPawnIsAlive", find_field_offset(client_cs, "m_bPawnIsAlive"))
    m_iPawnHealth = ctrl.get("m_iPawnHealth", find_field_offset(client_cs, "m_iPawnHealth"))

    # Optional buttons
    buttons_file = os.path.join(BASE_DIR, "buttons.cs")
    buttons = {}
    if os.path.exists(buttons_file):
        with open(buttons_file, "r", encoding="utf-8") as bf:
            buttons = parse_cs_class(bf.read(), "Buttons")

    lines = [
        '"""',
        'cs2_offsets.py',
        '──────────────',
        "Auto-generated from cs2-dumper files via update_offsets.py",
        f"Commit / Info: {commit_info}",
        f"Generated at:  {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}",
        '',
        'DO NOT edit by hand — run python update_offsets.py to regenerate.',
        'All values are integer byte offsets (hex).',
        '"""',
        '',
        '',
        '# ══════════════════════════════════════════════════════════════════════════════',
        '# offsets.cs  →  Module-level static pointers',
        "# These are offsets FROM the module base address to a pointer stored in the",
        "# module's data segment.  Dereference once to get the runtime object address.",
        '# ══════════════════════════════════════════════════════════════════════════════',
        '',
        'class ClientDll:',
        '    """Static pointers inside client.dll  (client_base + offset → pointer)."""',
        '',
        '    # ── Entity system ──────────────────────────────────────────────────────',
        f'    dwEntityList                          = 0x{client_dll.get("dwEntityList", 0):X}   # CGameEntitySystem*',
        f'    dwGameEntitySystem                    = 0x{client_dll.get("dwGameEntitySystem", client_dll.get("dwEntityList", 0)):X}   # alias (same address)',
        f'    dwGameEntitySystem_highestEntityIndex = 0x{client_dll.get("dwGameEntitySystem_highestEntityIndex", 0x2120):X}      # field WITHIN the system',
        '',
        '    # ── Local player ──────────────────────────────────────────────────────',
        f'    dwLocalPlayerController               = 0x{client_dll.get("dwLocalPlayerController", 0):X}   # CCSPlayerController*',
        f'    dwLocalPlayerPawn                     = 0x{client_dll.get("dwLocalPlayerPawn", 0):X}   # C_CSPlayerPawn*',
        '',
        '    # ── View / camera ─────────────────────────────────────────────────────',
        f'    dwViewAngles                          = 0x{client_dll.get("dwViewAngles", 0):X}   # QAngle',
        f'    dwViewMatrix                          = 0x{client_dll.get("dwViewMatrix", 0):X}   # ViewMatrix4x4',
        f'    dwViewRender                          = 0x{client_dll.get("dwViewRender", 0):X}   # CViewRender*',
        '',
        '    # ── Input ──────────────────────────────────────────────────────────────',
        f'    dwCSGOInput                           = 0x{client_dll.get("dwCSGOInput", 0):X}   # CCSGOInput*',
        '',
        '    # ── Misc ───────────────────────────────────────────────────────────────',
        f'    dwGameRules                           = 0x{client_dll.get("dwGameRules", 0):X}   # CCSGameRules*',
        f'    dwGlobalVars                          = 0x{client_dll.get("dwGlobalVars", 0):X}   # CGlobalVarsBase*',
        f'    dwGlowManager                         = 0x{client_dll.get("dwGlowManager", 0):X}   # CGlowManager*',
        f'    dwSensitivity                         = 0x{client_dll.get("dwSensitivity", 0):X}   # CSensitivity*',
        f'    dwSensitivity_sensitivity             = 0x{client_dll.get("dwSensitivity_sensitivity", 0x58):X}        # float field within it',
        f'    dwPlantedC4                           = 0x{client_dll.get("dwPlantedC4", 0):X}   # C_PlantedC4*',
        f'    dwPrediction                          = 0x{client_dll.get("dwPrediction", 0):X}   # CPrediction*',
        f'    dwWeaponC4                            = 0x{client_dll.get("dwWeaponC4", 0):X}   # C_C4*',
        '',
        '',
        'class Engine2Dll:',
        '    """Static pointers inside engine2.dll  (engine2_base + offset → pointer)."""',
        '',
        f'    dwBuildNumber                         = 0x{engine2_dll.get("dwBuildNumber", 0):X}',
        f'    dwNetworkGameClient                   = 0x{engine2_dll.get("dwNetworkGameClient", 0):X}    # CNetworkGameClient*',
        f'    dwNetworkGameClient_clientTickCount   = 0x{engine2_dll.get("dwNetworkGameClient_clientTickCount", 0x398):X}       # int field within it',
        f'    dwNetworkGameClient_deltaTick         = 0x{engine2_dll.get("dwNetworkGameClient_deltaTick", 0x24C):X}',
        f'    dwNetworkGameClient_isBackgroundMap   = 0x{engine2_dll.get("dwNetworkGameClient_isBackgroundMap", 0x2C143F):X}',
        f'    dwNetworkGameClient_localPlayer       = 0xF8',
        f'    dwNetworkGameClient_maxClients        = 0x240',
        f'    dwNetworkGameClient_serverTickCount   = 0x24C',
        f'    dwNetworkGameClient_signOnState       = 0x230',
        f'    dwWindowHeight                        = 0x{engine2_dll.get("dwWindowHeight", 0):X}',
        f'    dwWindowWidth                         = 0x{engine2_dll.get("dwWindowWidth", 0):X}',
        '',
    ]

    if buttons:
        lines.extend([
            '',
            'class Buttons:',
            '    """Static button offsets in client.dll."""',
            f'    attack                                = 0x{buttons.get("attack", 0):X}',
            f'    attack2                               = 0x{buttons.get("attack2", 0):X}',
            f'    jump                                  = 0x{buttons.get("jump", 0):X}',
            f'    duck                                  = 0x{buttons.get("duck", 0):X}',
            f'    forward                               = 0x{buttons.get("forward", 0):X}',
            f'    back                                  = 0x{buttons.get("back", 0):X}',
            f'    left                                  = 0x{buttons.get("left", 0):X}',
            f'    right                                 = 0x{buttons.get("right", 0):X}',
        ])

    lines.extend([
        '',
        '# ══════════════════════════════════════════════════════════════════════════════',
        '# client_dll.cs  →  Field offsets within C++ objects',
        '# These are offsets FROM the start of an object instance to a member field.',
        '# ══════════════════════════════════════════════════════════════════════════════',
        '',
        'class C_BaseEntity:',
        '    """',
        '    Source: client_dll.cs — C_BaseEntity (and C_BasePlayerPawn which inherits it)',
        '    Field offsets relative to the entity object pointer.',
        '    """',
        f'    m_pGameSceneNode = 0x{m_pGameSceneNode:X}    # CGameSceneNode*',
        f'    m_iHealth        = 0x{m_iHealth:X}    # int32',
        f'    m_lifeState      = 0x{m_lifeState:X}    # uint8  (0=alive)',
        f'    m_iTeamNum       = 0x{m_iTeamNum:X}    # uint8  (2=T,3=CT)',
        f'    m_fFlags         = 0x{m_fFlags:X}    # uint32 (bit 1 = FL_DUCKING)',
        f'    m_vecVelocity    = 0x{m_vecVelocity:X}    # CNetworkVelocityVector → 3×float',
        '',
        '',
        'class CGameSceneNode:',
        '    """',
        '    Source: client_dll.cs — CGameSceneNode',
        '    Field offsets relative to the scene-node pointer.',
        '    """',
        f'    m_vecAbsOrigin   = 0x{m_vecAbsOrigin:X}     # VectorWS / Vector3',
        '',
        '',
        'class C_CSPlayerPawn:',
        '    """',
        '    Source: client_dll.cs — C_CSPlayerPawnBase (inherits C_BasePlayerPawn)',
        '    Player-specific fields.',
        '    """',
        f'    m_angEyeAngles   = 0x{m_angEyeAngles:X}   # QAngle  pitch/yaw/roll',
        '',
        '',
        'class CCSPlayerController:',
        '    """',
        '    Source: client_dll.cs — CCSPlayerController',
        '    Controller (scoreboard) → Pawn linkage.',
        '    """',
        f'    m_hPlayerPawn    = 0x{m_hPlayerPawn:X}    # CHandle<C_CSPlayerPawn>',
        f'    m_iszPlayerName  = 0x{m_iszPlayerName:X}    # char[128] player name',
        f'    m_bPawnIsAlive   = 0x{m_bPawnIsAlive:X}    # bool',
        f'    m_iPawnHealth    = 0x{m_iPawnHealth:X}    # uint32',
        '',
        '',
        '# ══════════════════════════════════════════════════════════════════════════════',
        '# Derived / computed constants (do not change)',
        '# ══════════════════════════════════════════════════════════════════════════════',
        '',
        'ENTITY_ENTRY_SIZE  = 112    # sizeof(EntityEntry) in the entity list chunk',
        'ENTITY_CHUNK_SIZE  = 512    # slots per chunk  (2^9)',
        'ENTITY_CHUNK_MASK  = 0x1FF  # = CHUNK_SIZE - 1, used for (index & CHUNK_MASK)',
        'MAX_ENTITIES       = 64     # dwGameEntitySystem_highestEntityIndex typical max',
        '',
        '# ── Life-state values (m_lifeState) ───────────────────────────────────────',
        'LIFE_ALIVE = 0',
        'LIFE_DYING = 1',
        'LIFE_DEAD  = 2',
        '',
        '# ── Team numbers (m_iTeamNum) ──────────────────────────────────────────────',
        'TEAM_UNASSIGNED = 0',
        'TEAM_SPECTATOR  = 1',
        'TEAM_T          = 2',
        'TEAM_CT         = 3',
        '',
    ])

    return "\n".join(lines)


def update_spatial_telemetry_file(dw_entity_list: int, dw_view_matrix: int, m_p_game_scene_node: int, m_vec_abs_origin: int):
    st_path = os.path.join(BASE_DIR, "spatial_telemetry.py")
    if not os.path.exists(st_path):
        return
    with open(st_path, "r", encoding="utf-8") as f:
        content = f.read()

    # Update offsets inside DEFAULT_CONFIG
    old_block_match = re.search(r'("offsets":\s*\{)(.*?)(\})', content, re.DOTALL)
    if old_block_match:
        new_offsets_str = (
            '\n'
            f'        "dwEntityList": 0x{dw_entity_list:X},\n'
            f'        "dwViewMatrix": 0x{dw_view_matrix:X},\n'
            f'        "m_pGameSceneNode": 0x{m_p_game_scene_node:X},\n'
            f'        "m_vecAbsOrigin": 0x{m_vec_abs_origin:X},\n'
            f'        "m_hPlayerPawn": 0x92C,\n'
            f'        "dwEntityStride": 112,\n'
            f'        "index_mask": 0x1FF,\n'
            '    '
        )
        content = content[:old_block_match.start(2)] + new_offsets_str + content[old_block_match.end(2):]
        with open(st_path, "w", encoding="utf-8") as f:
            f.write(content)
        log("Updated spatial_telemetry.py DEFAULT_CONFIG offsets.")


def main():
    print("=" * 60)
    print("  CS2 Offset Updater & Synchronizer")
    print("=" * 60)

    use_local_only = "--local" in sys.argv
    commit_info = "Local dump files"

    if not use_local_only:
        log("Checking remote repository a2x/cs2-dumper...")
        sha, date, msg = get_latest_commit_info()
        commit_info = f"{sha} ({date}) - {msg}"
        log(f"Latest remote commit: {commit_info}")

        for fname in FILES_TO_DOWNLOAD:
            try:
                download_file(fname)
            except Exception as e:
                log_warn(f"Failed to download {fname}: {e}. Falling back to local file.")
    else:
        log("Using local .cs files without downloading.")

    offsets_cs_path = os.path.join(BASE_DIR, "offsets.cs")
    client_cs_path = os.path.join(BASE_DIR, "client_dll.cs")

    if not os.path.exists(offsets_cs_path) or not os.path.exists(client_cs_path):
        log_err("offsets.cs or client_dll.cs missing!")
        sys.exit(1)

    with open(offsets_cs_path, "r", encoding="utf-8") as f:
        offsets_cs = f.read()

    with open(client_cs_path, "r", encoding="utf-8") as f:
        client_cs = f.read()

    generated_code = generate_cs2_offsets_py(offsets_cs, client_cs, commit_info)

    out_py = os.path.join(BASE_DIR, "cs2_offsets.py")
    with open(out_py, "w", encoding="utf-8") as f:
        f.write(generated_code)
    log(f"Wrote regenerated offsets to {out_py}")

    # Extract primary offsets
    client_dll = parse_cs_class(offsets_cs, "ClientDll")
    dw_entity_list = client_dll.get("dwEntityList", 0)
    dw_view_matrix = client_dll.get("dwViewMatrix", 0)
    base_ent = parse_cs_class(client_cs, "C_BaseEntity")
    m_p_game_scene_node = base_ent.get("m_pGameSceneNode", 0x330)
    scene_node = parse_cs_class(client_cs, "CGameSceneNode")
    m_vec_abs_origin = scene_node.get("m_vecAbsOrigin", 0xC8)

    print("\n" + "-" * 50)
    print("  CURRENT PRIMARY OFFSETS:")
    print("-" * 50)
    print(f"  dwEntityList:     0x{dw_entity_list:X}")
    print(f"  dwViewMatrix:     0x{dw_view_matrix:X}")
    print(f"  m_pGameSceneNode: 0x{m_p_game_scene_node:X}")
    print(f"  m_vecAbsOrigin:   0x{m_vec_abs_origin:X}")
    print("-" * 50 + "\n")

    update_spatial_telemetry_file(dw_entity_list, dw_view_matrix, m_p_game_scene_node, m_vec_abs_origin)

    # Validate against running game if active
    val_path = os.path.join(BASE_DIR, "validate_offsets.py")
    if os.path.exists(val_path):
        print("[*] Running validation test...")
        import subprocess
        res = subprocess.run([sys.executable, val_path], capture_output=True, text=True)
        if res.returncode == 0:
            print("[+] Live game validation succeeded!\n")
            print(res.stdout)
        else:
            print("[!] Validation script output:")
            print(res.stdout if res.stdout else res.stderr)


if __name__ == "__main__":
    main()
