"""Live, read-only BTD6 Sun Avatar / Temple sacrifice tracker.

Usage:
    python sacrifice_tracker.py
    python sacrifice_tracker.py --interval 0.25
"""

import argparse
import ctypes
import os
import struct
import sys
import time
from ctypes import wintypes

# Updated in-place by update_offsets.py.
# BEGIN AUTO OFFSETS
GAME_ASSEMBLY_OFFSET = 0x4A75DD8
IL2CPP_CLASS_STATIC_FIELDS = 0xB8
IL2CPP_STRING_LENGTH = 0x10
IL2CPP_STRING_DATA = 0x14
IL2CPP_LIST_ITEMS = 0x10
IL2CPP_LIST_COUNT = 0x18
IL2CPP_ARRAY_LENGTH = 0x18
IL2CPP_ARRAY_DATA = 0x20
INGAME_INSTANCE = 0x0
INGAME_PLAYER_CONTEXTS = 0x70
INGAME_BRIDGE = 0xC0
PLAYERCONTEXT_CONTEXT = 0x20
PLAYERCONTEXT_TOWER_SELECTION_MENU = 0x48
TSM_SELECTED_TOWER = 0x1F0
TSM_SHOWING = 0x241
BRIDGE_TOWERS = 0x58
TTS_TOWER = 0x18
ROOTBEHAVIOR_ENTITY = 0x48
ENTITY_TRANSFORM = 0x50
TRANSFORM_POSITION = 0x58
VECTOR3BOXED_DATA = 0x10
TOWER_WORTH = 0x158
TOWER_TOWERMODEL = 0x1A0
ENTITYMODEL_BASE_ID = 0x28
TOWERMODEL_RANGE = 0x5C
TOWERMODEL_TIERS = 0x68
TOWERMODEL_TOWERSET = 0x70
TOWERMODEL_IS_SUB_TOWER = 0x100
TOWERMODEL_POWER_NAME = 0x108
TOWERMODEL_IS_PARAGON = 0x12C
TOWERMODEL_GERALDO_ITEM_NAME = 0x138
# END AUTO OFFSETS

PROCESS_ACCESS = 0x0400 | 0x0010 | 0x00100000
TH32CS_SNAPPROCESS = 0x2
TH32CS_SNAPMODULE = 0x8 | 0x10
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
STILL_ACTIVE = 259
TOWER_SETS = {1: "Primary", 2: "Military", 4: "Magic", 8: "Support"}

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
        ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", wintypes.LONG),
        ("dwFlags", wintypes.DWORD), ("szExeFile", wintypes.WCHAR * 260),
    ]


class MODULEENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD), ("th32ModuleID", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD), ("GlblcntUsage", wintypes.DWORD),
        ("ProccntUsage", wintypes.DWORD),
        ("modBaseAddr", ctypes.POINTER(ctypes.c_byte)),
        ("modBaseSize", wintypes.DWORD), ("hModule", wintypes.HMODULE),
        ("szModule", wintypes.WCHAR * 256), ("szExePath", wintypes.WCHAR * 260),
    ]


kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
kernel32.Module32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MODULEENTRY32W)]
kernel32.Module32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MODULEENTRY32W)]
kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.ReadProcessMemory.argtypes = [
    wintypes.HANDLE, wintypes.LPCVOID, wintypes.LPVOID,
    ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t),
]
kernel32.ReadProcessMemory.restype = wintypes.BOOL


class ReadError(Exception):
    pass


def find_process(name):
    snap = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snap == INVALID_HANDLE_VALUE:
        return None
    try:
        e = PROCESSENTRY32W()
        e.dwSize = ctypes.sizeof(e)
        ok = kernel32.Process32FirstW(snap, ctypes.byref(e))
        while ok:
            if e.szExeFile.lower() == name.lower():
                h = kernel32.OpenProcess(PROCESS_ACCESS, False, e.th32ProcessID)
                return (e.th32ProcessID, h) if h else None
            ok = kernel32.Process32NextW(snap, ctypes.byref(e))
    finally:
        kernel32.CloseHandle(snap)
    return None


def module_base(pid, name="GameAssembly.dll"):
    snap = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPMODULE, pid)
    if snap == INVALID_HANDLE_VALUE:
        return None
    try:
        e = MODULEENTRY32W()
        e.dwSize = ctypes.sizeof(e)
        ok = kernel32.Module32FirstW(snap, ctypes.byref(e))
        while ok:
            if e.szModule.lower() == name.lower():
                return ctypes.cast(e.modBaseAddr, ctypes.c_void_p).value
            ok = kernel32.Module32NextW(snap, ctypes.byref(e))
    finally:
        kernel32.CloseHandle(snap)
    return None


def alive(h):
    code = wintypes.DWORD()
    return bool(kernel32.GetExitCodeProcess(h, ctypes.byref(code)) and code.value == STILL_ACTIVE)


def read(h, addr, fmt):
    size = struct.calcsize(fmt)
    buf = (ctypes.c_ubyte * size)()
    done = ctypes.c_size_t()
    if not addr or not kernel32.ReadProcessMemory(h, ctypes.c_void_p(addr), buf, size, ctypes.byref(done)) or done.value != size:
        raise ReadError
    return struct.unpack(fmt, bytes(buf))


def ptr(h, addr):
    return read(h, addr, "<Q")[0]


def i32(h, addr):
    return read(h, addr, "<i")[0]


def f32(h, addr):
    return read(h, addr, "<f")[0]


def boolean(h, addr):
    return read(h, addr, "<?")[0]


def string(h, p):
    if not p:
        return ""
    n = i32(h, p + IL2CPP_STRING_LENGTH)
    if not 0 < n < 4096:
        return ""
    chars = read(h, p + IL2CPP_STRING_DATA, f"<{n * 2}s")[0]
    return chars.decode("utf-16-le", "replace")


def list_ptrs(h, p):
    if not p:
        return []
    count = i32(h, p + IL2CPP_LIST_COUNT)
    if not 0 < count < 100000:
        return []
    items = ptr(h, p + IL2CPP_LIST_ITEMS)
    return list(read(h, items + IL2CPP_ARRAY_DATA, f"<{count}Q"))


def tiers(h, model):
    arr = ptr(h, model + TOWERMODEL_TIERS)
    if not arr:
        return (0, 0, 0)
    count = i32(h, arr + IL2CPP_ARRAY_LENGTH)
    if count < 3:
        return (0, 0, 0)
    return read(h, arr + IL2CPP_ARRAY_DATA, "<iii")


def base_id(h, model):
    return string(h, ptr(h, model + ENTITYMODEL_BASE_ID))


def model_of(h, tower):
    return ptr(h, tower + TOWER_TOWERMODEL)


def position(h, tower):
    entity = ptr(h, tower + ROOTBEHAVIOR_ENTITY)
    transform = ptr(h, entity + ENTITY_TRANSFORM)
    boxed = ptr(h, transform + TRANSFORM_POSITION)
    return read(h, boxed + VECTOR3BOXED_DATA, "<fff")


def get_ingame(h, base):
    type_info = ptr(h, base + GAME_ASSEMBLY_OFFSET)
    static_fields = ptr(h, type_info + IL2CPP_CLASS_STATIC_FIELDS)
    return ptr(h, static_fields + INGAME_INSTANCE)


def selected_tower(h, ingame):
    contexts = list_ptrs(h, ptr(h, ingame + INGAME_PLAYER_CONTEXTS))
    for pc in contexts:
        try:
            context = ptr(h, pc + PLAYERCONTEXT_CONTEXT)
            menu = ptr(h, context + PLAYERCONTEXT_TOWER_SELECTION_MENU)
            if menu and boolean(h, menu + TSM_SHOWING):
                tts = ptr(h, menu + TSM_SELECTED_TOWER)
                if tts:
                    return ptr(h, tts + TTS_TOWER)
        except ReadError:
            pass
    return 0


def all_towers(h, ingame):
    bridge = ptr(h, ingame + INGAME_BRIDGE)
    tts_list = ptr(h, bridge + BRIDGE_TOWERS)
    towers = []
    for tts in list_ptrs(h, tts_list):
        try:
            tower = ptr(h, tts + TTS_TOWER)
            if tower:
                towers.append(tower)
        except ReadError:
            pass
    return towers


def valid_sacrifice(h, tower, model):
    tower_set = i32(h, model + TOWERMODEL_TOWERSET)
    if tower_set not in TOWER_SETS:
        return False
    if boolean(h, model + TOWERMODEL_IS_SUB_TOWER) or boolean(h, model + TOWERMODEL_IS_PARAGON):
        return False
    if string(h, ptr(h, model + TOWERMODEL_POWER_NAME)):
        return False
    if string(h, ptr(h, model + TOWERMODEL_GERALDO_ITEM_NAME)):
        return False
    if base_id(h, model) in {"TempleBase-TempleBase", "ParagonPowerTotem"}:
        return False
    return f32(h, tower + TOWER_WORTH) > 0


def calculate(h, base):
    ingame = get_ingame(h, base)
    if not ingame:
        raise ReadError

    selected = selected_tower(h, ingame)
    if not selected:
        return None, "Select a Sun Avatar (or Sun Temple)."

    selected_model = model_of(h, selected)
    paths = tiers(h, selected_model)
    tower_id = base_id(h, selected_model)

    if tower_id != "SuperMonkey" or paths[0] not in (3, 4):
        return None, f"Selected {tower_id} {paths[0]}-{paths[1]}-{paths[2]} — not a Sun Avatar/Temple."

    sx, sy, _ = position(h, selected)
    radius = f32(h, selected_model + TOWERMODEL_RANGE)
    radius_sq = radius * radius
    totals = {k: [0.0, 0] for k in TOWER_SETS}

    for tower in all_towers(h, ingame):
        if tower == selected:
            continue
        try:
            model = model_of(h, tower)
            if not valid_sacrifice(h, tower, model):
                continue
            x, y, _ = position(h, tower)
            if (x - sx) ** 2 + (y - sy) ** 2 > radius_sq:
                continue
            tower_set = i32(h, model + TOWERMODEL_TOWERSET)
            totals[tower_set][0] += f32(h, tower + TOWER_WORTH)
            totals[tower_set][1] += 1
        except ReadError:
            pass

    name = "Sun Avatar" if paths[0] == 3 else "Sun Temple"
    return (name, paths, radius, totals), ""


def draw(result, status):
    lines = ["BTD6 Temple Sacrifice Tracker", "=" * 45, ""]
    if result is None:
        lines.append(status)
    else:
        name, paths, radius, totals = result
        lines += [
            f"Selected: {name} {paths[0]}-{paths[1]}-{paths[2]}",
            f"Range:    {radius:.1f}", "",
            f"{'Category':<12} {'Worth':>14} {'Towers':>8}",
            "-" * 36,
        ]
        for tower_set, category in TOWER_SETS.items():
            worth, count = totals[tower_set]
            lines.append(f"{category:<12} ${worth:>12,.0f} {count:>8}")
    sys.stdout.write("\x1b[H\x1b[2J" + "\n".join(lines) + "\n")
    sys.stdout.flush()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--interval", type=float, default=0.25)
    parser.add_argument("--process", default="BloonsTD6.exe")
    args = parser.parse_args()

    if os.name != "nt":
        raise SystemExit("Windows only.")

    handle = base = None
    try:
        while True:
            result = None
            status = f"Waiting for {args.process} ..."

            if handle is None or not alive(handle):
                if handle:
                    kernel32.CloseHandle(handle)
                handle = base = None
                found = find_process(args.process)
                if found:
                    pid, handle = found
                    base = module_base(pid)
                    if not base:
                        kernel32.CloseHandle(handle)
                        handle = None

            if handle and base:
                try:
                    result, status = calculate(handle, base)
                except ReadError:
                    status = "Not in a match / memory offsets may be outdated."

            draw(result, status)
            time.sleep(max(0.05, args.interval))
    except KeyboardInterrupt:
        pass
    finally:
        if handle:
            kernel32.CloseHandle(handle)


if __name__ == "__main__":
    main()
