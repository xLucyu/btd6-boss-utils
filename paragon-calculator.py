"""
BTD6 paragon degree tracker — live, milestone view.

Windows only, 64-bit Python. Run with the same privileges you'd use for the
Rust version. Ctrl+C to quit.

    python paragon_tracker.py
    python paragon_tracker.py --interval 1.0 --milestones 10,25,50,75,100
"""

from __future__ import annotations

import argparse
import ctypes
import os
import shutil
import struct
import sys
import time
from collections import defaultdict
from ctypes import wintypes
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Win32 plumbing
# ---------------------------------------------------------------------------

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_VM_READ = 0x0010
SYNCHRONIZE = 0x00100000
TH32CS_SNAPPROCESS = 0x00000002
TH32CS_SNAPMODULE = 0x00000008
TH32CS_SNAPMODULE32 = 0x00000010
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
STILL_ACTIVE = 259


# ---------------------------------------------------------------------------
# BTD6 memory offsets
# Update this section after a game patch
# ---------------------------------------------------------------------------
GAME_ASSEMBLY_OFFSET            = 0x4A75DD8 
INGAME_INSTANCE                 = 0x0        # InGame.instance
INGAME_BRIDGE                   = 0xC0    # InGame.bridge
BRIDGE_SIMULATION               = 0x28    # UnityToSimulation.simulation
BRIDGE_TOWERS                   = 0x58    # UnityToSimulation.ttss
SIMULATION_GAMEMODEL            = 0x20    # Simulation.model
GAMEMODEL_UPGRADES              = 0x110    # GameModel.upgrades
MODEL_NAME                      = 0x20    # Model._name
UPGRADEMODEL_COST               = 0x28    # UpgradeModel.cost
TTS_TOWER                       = 0x18    # TowerToSimulation.tower
TOWER_WORTH                     = 0x158    # Tower.worth
TOWER_DAMAGE_DEALT              = 0x160    # Tower.damageDealt
TOWER_CASH_EARNED               = 0x170    # Tower.cashEarned
TOWER_TOWERMODEL                = 0x1A0    # Tower.towerModel
TOWERMODEL_TIER                 = 0x64    # TowerModel.tier
TOWERMODEL_APPLIED_UPGRADES     = 0xE8    # TowerModel.appliedUpgrades
TOWERMODEL_BASE_ID              = 0x28    # EntityModel.baseId


# ---------------------------------------------------------------------------
# IL2CPP runtime / collection layout
# Usually unchanged by normal BTD6 patches
# ---------------------------------------------------------------------------

IL2CPP_CLASS_STATIC_FIELDS        = 0xB8

IL2CPP_STRING_LENGTH              = 0x10
IL2CPP_STRING_DATA                = 0x14

IL2CPP_LIST_ITEMS                 = 0x10
IL2CPP_LIST_COUNT                 = 0x18

IL2CPP_ARRAY_LENGTH               = 0x18
IL2CPP_ARRAY_DATA                 = 0x20


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", wintypes.LONG),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", wintypes.WCHAR * 260),
    ]


class MODULEENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("th32ModuleID", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("GlblcntUsage", wintypes.DWORD),
        ("ProccntUsage", wintypes.DWORD),
        ("modBaseAddr", ctypes.POINTER(ctypes.c_byte)),
        ("modBaseSize", wintypes.DWORD),
        ("hModule", wintypes.HMODULE),
        ("szModule", wintypes.WCHAR * 256),
        ("szExePath", wintypes.WCHAR * 260),
    ]


kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
kernel32.Module32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MODULEENTRY32W)]
kernel32.Module32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MODULEENTRY32W)]
kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
kernel32.GetStdHandle.argtypes = [wintypes.DWORD]
kernel32.GetStdHandle.restype = wintypes.HANDLE
kernel32.GetConsoleMode.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
kernel32.SetConsoleMode.argtypes = [wintypes.HANDLE, wintypes.DWORD]
kernel32.ReadProcessMemory.argtypes = [
    wintypes.HANDLE,
    wintypes.LPCVOID,
    wintypes.LPVOID,
    ctypes.c_size_t,
    ctypes.POINTER(ctypes.c_size_t),
]
kernel32.ReadProcessMemory.restype = wintypes.BOOL


class ReadError(Exception):
    """A memory read failed — usually means the game moved on (menu, restart)."""


def find_process(name: str = "BloonsTD6.exe") -> Optional[Tuple[int, int]]:
    snapshot = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snapshot == INVALID_HANDLE_VALUE:
        return None
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        ok = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
        while ok:
            if entry.szExeFile.lower() == name.lower():
                pid = entry.th32ProcessID
                handle = kernel32.OpenProcess(
                    PROCESS_QUERY_INFORMATION | PROCESS_VM_READ | SYNCHRONIZE,
                    False,
                    pid,
                )
                if handle:
                    return pid, handle
                return None
            ok = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snapshot)
    return None


def find_module_base(pid: int, module: str = "GameAssembly.dll") -> Optional[int]:
    snapshot = kernel32.CreateToolhelp32Snapshot(
        TH32CS_SNAPMODULE | TH32CS_SNAPMODULE32, pid
    )
    if snapshot == INVALID_HANDLE_VALUE:
        return None
    try:
        entry = MODULEENTRY32W()
        entry.dwSize = ctypes.sizeof(MODULEENTRY32W)
        ok = kernel32.Module32FirstW(snapshot, ctypes.byref(entry))
        while ok:
            if module.lower() in entry.szModule.lower():
                return ctypes.cast(entry.modBaseAddr, ctypes.c_void_p).value
            ok = kernel32.Module32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snapshot)
    return None


def process_alive(handle: int) -> bool:
    code = wintypes.DWORD()
    if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
        return False
    return code.value == STILL_ACTIVE


# ---------------------------------------------------------------------------
# Memory reading
# ---------------------------------------------------------------------------


def read_bytes(handle: int, address: int, size: int) -> bytes:
    if address <= 0 or size <= 0:
        raise ReadError(f"invalid read at {address:#x} ({size} bytes)")
    buf = (ctypes.c_ubyte * size)()
    read = ctypes.c_size_t(0)
    ok = kernel32.ReadProcessMemory(
        handle, ctypes.c_void_p(address), ctypes.byref(buf), size, ctypes.byref(read)
    )
    if not ok or read.value != size:
        raise ReadError(
            f"read failed at {address:#x} (winerr {ctypes.get_last_error()})"
        )
    return bytes(buf)


def read_memory(handle: int, offsets: List[int], fmt: str = "<Q"):
    """Pointer chain: accumulate offsets, dereference between every pair."""
    address = 0
    last = len(offsets) - 1
    for i, off in enumerate(offsets):
        address += off
        if i != last:
            address = struct.unpack("<Q", read_bytes(handle, address, 8))[0]
    return struct.unpack(fmt, read_bytes(handle, address, struct.calcsize(fmt)))[0]


def read_il2cpp_string(handle: int, addr: int) -> str:
    length = read_memory(handle, [addr + IL2CPP_STRING_LENGTH], "<i")
    if length <= 0 or length > 4096:
        return ""
    return read_bytes(handle, addr + IL2CPP_STRING_DATA, length * 2).decode(
        "utf-16-le", "replace"
    )


def read_ptr_block(handle: int, base: int, count_off: int, data_off: int) -> List[int]:
    """Read a contiguous block of `int32 count` + pointer payload in one go."""
    length = read_memory(handle, [base + count_off], "<i")
    if length <= 0 or length > 100_000:
        return []
    raw = read_bytes(handle, base + data_off, length * 8)
    return list(struct.unpack(f"<{length}Q", raw))


@dataclass(frozen=True)
class _Addr:
    address: int


class GameAssembly(_Addr):
    def get_ingame_instance(self, h: int) -> "InGame":
        return InGame(
            read_memory(
                h,
                [
                    self.address + GAME_ASSEMBLY_OFFSET,
                    IL2CPP_CLASS_STATIC_FIELDS,
                    INGAME_INSTANCE,
                ],
            )
        )


class InGame(_Addr):
    def get_unity_to_simulation(self, h: int) -> "UnityToSimulation":
        return UnityToSimulation(read_memory(h, [self.address + INGAME_BRIDGE]))


class UnityToSimulation(_Addr):
    def get_simulation(self, h: int) -> "Simulation":
        return Simulation(read_memory(h, [self.address + BRIDGE_SIMULATION]))

    def get_all_towers(self, h: int) -> List["TowerToSimulation"]:
        # List<T> -> backing array -> pointer array payload.
        lst = read_memory(h, [self.address + BRIDGE_TOWERS])
        items = read_memory(h, [lst + IL2CPP_LIST_ITEMS])
        length = read_memory(h, [lst + IL2CPP_LIST_COUNT], "<i")
        if length <= 0 or length > 100_000:
            return []
        raw = read_bytes(h, items + IL2CPP_ARRAY_DATA, length * 8)
        return [TowerToSimulation(p) for p in struct.unpack(f"<{length}Q", raw)]


class Simulation(_Addr):
    def get_gamemodel(self, h: int) -> "GameModel":
        return GameModel(read_memory(h, [self.address + SIMULATION_GAMEMODEL]))


class GameModel(_Addr):
    def get_paragon_upgrades_costs(self, h: int) -> Dict[str, int]:
        upgrades = read_memory(h, [self.address + GAMEMODEL_UPGRADES])
        result: Dict[str, int] = {}
        for entry in read_ptr_block(
            h, upgrades, IL2CPP_ARRAY_LENGTH, IL2CPP_ARRAY_DATA
        ):
            name = read_il2cpp_string(h, read_memory(h, [entry + MODEL_NAME]))
            if "Paragon" in name and "Sentry" not in name:
                result[name] = read_memory(h, [entry + UPGRADEMODEL_COST], "<i")
        return result


class TowerToSimulation(_Addr):
    def get_tower(self, h: int) -> "Tower":
        return Tower(read_memory(h, [self.address + TTS_TOWER]))


class Tower(_Addr):
    def get_worth(self, h: int) -> float:
        return read_memory(h, [self.address + TOWER_WORTH], "<f")

    def get_damage_dealt(self, h: int) -> int:
        return read_memory(h, [self.address + TOWER_DAMAGE_DEALT], "<q")

    def get_cash_earned(self, h: int) -> float:
        return read_memory(h, [self.address + TOWER_CASH_EARNED], "<f")

    def get_tower_model(self, h: int) -> "TowerModel":
        return TowerModel(read_memory(h, [self.address + TOWER_TOWERMODEL]))


class TowerModel(_Addr):
    def get_tier(self, h: int) -> int:
        return read_memory(h, [self.address + TOWERMODEL_TIER], "<i")

    def get_total_upgrades(self, h: int) -> int:
        return read_memory(
            h,
            [self.address + TOWERMODEL_APPLIED_UPGRADES, IL2CPP_ARRAY_LENGTH],
            "<i",
        )

    def get_base_id(self, h: int) -> str:
        return read_il2cpp_string(
            h, read_memory(h, [self.address + TOWERMODEL_BASE_ID])
        )


# ---------------------------------------------------------------------------
# Degree maths
# ---------------------------------------------------------------------------


def power_required(degree: int) -> float:
    return (50 * degree**3 + 5025 * degree**2 + 168324 * degree + 843000) / 600.0


def degree_from_power(power: float) -> int:
    for degree in range(2, 101):
        if power_required(degree) >= power:
            return degree - 1
    return 100


@dataclass
class DegreeFactors:
    worth: float = 0.0
    damage_dealt: int = 0
    cash_earned: float = 0.0
    total_upgrades: int = 0
    tier_5s: int = 0
    paragon_cost: float = 0.0
    totems: int = 0
    tower_count: int = 0

    def get_milestone_costs(
        self, milestones: Tuple[int, ...]
    ) -> Tuple[int, List[Tuple[int, Optional[float]]]]:
        """
        Returns (current_degree, [(milestone, cash_needed_or_None), ...]).
        cash is 0.0 if the milestone is already reached, and None if it is
        out of reach even with the cash slider maxed out.
        """

        current_power = self.get_total_power()
        current_degree = degree_from_power(current_power)
        max_power_from_slider = 60000.0 - self.get_power_from_worth()
        cash_per_power = self.paragon_cost * 1.05 / 20000.0
        rows: List[Tuple[int, Optional[float]]] = []
        for degree in milestones:
            if degree <= current_degree:
                rows.append((degree, 0.0))
                continue
            needed = power_required(degree) - current_power
            if needed > max_power_from_slider:
                rows.append((degree, None))
            else:
                rows.append((degree, needed * cash_per_power))
        return current_degree, rows

    def get_total_power(self) -> float:
        return (
            self.get_power_from_worth()
            + self.get_power_from_tier5s()
            + self.get_power_from_upgrades()
            + self.get_power_from_pops_and_cash_generated()
            + self.get_power_from_totems()
        )

    def get_power_from_worth(self) -> float:
        if self.paragon_cost <= 0:
            return 0.0
        return min(self.worth / (self.paragon_cost / 20000.0), 60000.0)

    def get_power_from_tier5s(self) -> float:
        return max(min((self.tier_5s - 3) * 6000.0, 50000.0), 0.0)

    def get_power_from_upgrades(self) -> float:
        return min(self.total_upgrades * 100.0, 10000.0)

    def get_power_from_pops_and_cash_generated(self) -> float:
        return min(self.damage_dealt / 180.0 + self.cash_earned / 45.0, 90000.0)

    def get_power_from_totems(self) -> float:
        return float(self.totems * 2000)


# ---------------------------------------------------------------------------
# Snapshot
# ---------------------------------------------------------------------------


@dataclass
class Snapshot:
    totems: int = 0
    towers_scanned: int = 0
    factors: Dict[str, DegreeFactors] = field(default_factory=dict)


def take_snapshot(handle: int, module_base: int) -> Snapshot:
    ga = GameAssembly(module_base)
    bridge = ga.get_ingame_instance(handle).get_unity_to_simulation(handle)
    gamemodel = bridge.get_simulation(handle).get_gamemodel(handle)
    paragon_costs = gamemodel.get_paragon_upgrades_costs(handle)
    towers = bridge.get_all_towers(handle)
    by_base: Dict[str, DegreeFactors] = defaultdict(DegreeFactors)
    totems = 0
    scanned = 0
    for tts in towers:
        try:
            tower = tts.get_tower(handle)
            model = tower.get_tower_model(handle)
            base_id = model.get_base_id(handle)
        except ReadError:
            continue  # tower died / was sold mid-scan
        scanned += 1
        if base_id == "ParagonPowerTotem":
            totems += 1
            continue
        if f"{base_id} Paragon" not in paragon_costs:
            continue
        try:
            worth = tower.get_worth(handle)
            total_upgrades = model.get_total_upgrades(handle)
            damage_dealt = tower.get_damage_dealt(handle)
            cash_earned = tower.get_cash_earned(handle)
            tier = model.get_tier(handle)
        except ReadError:
            continue
        entry = by_base[base_id]
        entry.tower_count += 1
        if tier == 6:  # skip existing paragons — lets you plan a sell & rebuy
            continue
        if tier == 5:
            entry.tier_5s += 1
        else:
            entry.worth += worth
            entry.total_upgrades += total_upgrades
        entry.damage_dealt += damage_dealt
        entry.cash_earned += cash_earned
    for base_id, f in by_base.items():
        f.totems = totems
        f.paragon_cost = float(paragon_costs[f"{base_id} Paragon"])
    return Snapshot(totems=totems, towers_scanned=scanned, factors=dict(by_base))


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


HOME = "\x1b[H"
CLEAR_SCREEN = "\x1b[2J"
CLEAR_EOL = "\x1b[K"
CLEAR_BELOW = "\x1b[0J"
HIDE_CURSOR = "\x1b[?25l"
SHOW_CURSOR = "\x1b[?25h"
DIM = "\x1b[2m"
BOLD = "\x1b[1m"
GREEN = "\x1b[32m"
YELLOW = "\x1b[33m"
RESET = "\x1b[0m"


def enable_ansi() -> None:
    if os.name != "nt":
        return
    handle = kernel32.GetStdHandle(-11)
    mode = wintypes.DWORD()
    if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
        kernel32.SetConsoleMode(handle, mode.value | 0x0004)


def money(value: float) -> str:
    return f"${value:,.0f}"


def render(
    snap: Optional[Snapshot],
    status: str,
    prev_degrees: Dict[str, int],
    milestones: Tuple[int, ...],


) -> List[str]:
    width = shutil.get_terminal_size((100, 30)).columns
    now = datetime.now().strftime("%H:%M:%S")
    lines = [
        f"{BOLD}BTD6 Paragon Degree Tracker{RESET}  {DIM}{now}  ·  Ctrl+C to quit{RESET}",
        f"{DIM}{'─' * min(width - 1, 78)}{RESET}",
    ]
    if snap is None:
        lines.append(f"  {YELLOW}{status}{RESET}")
        return lines
    lines.append(
        f"  totems {BOLD}{snap.totems}{RESET}   "
        f"towers {BOLD}{snap.towers_scanned}{RESET}   "
        f"paragon-capable types {BOLD}{len(snap.factors)}{RESET}"
    )
    lines.append("")
    if not snap.factors:
        lines.append(f"  {DIM}no paragon-capable towers placed{RESET}")
        if status:
            lines.append("")
            lines.append(f"  {DIM}{status}{RESET}")
        return lines
    for base_id in sorted(snap.factors):
        factors = snap.factors[base_id]
        current, rows = factors.get_milestone_costs(milestones)
        arrow = ""
        was = prev_degrees.get(base_id)
        if was is not None and current > was:
            arrow = f" {GREEN}▲{RESET}"
        prev_degrees[base_id] = current
        lines.append(
            f"  {BOLD}{base_id:<22}{RESET} "
            f"current {GREEN}D{current}{RESET}{arrow}   "
            f"{DIM}paragon {money(factors.paragon_cost)} · "
            f"power {factors.get_total_power():,.0f}{RESET}"
        )
        cells = []
        for degree, cost in rows:
            label = f"D{degree}"
            if cost is None:
                cells.append(f"{DIM}{label:<4} {'out of reach':>13}{RESET}")
            elif cost == 0.0:
                cells.append(f"{GREEN}{label:<4} {'reached ✓':>13}{RESET}")
            else:
                cells.append(f"{label:<4} {money(cost):>13}")
        for i in range(0, len(cells), 3):
            lines.append("    " + "   ".join(cells[i : i + 3]))
        lines.append("")
    if status:
        lines.append(f"  {DIM}{status}{RESET}")
    return lines


def draw(lines: List[str]) -> None:
    out = [HOME]
    for line in lines:
        out.append(line + CLEAR_EOL + "\n")
    out.append(CLEAR_BELOW)
    sys.stdout.write("".join(out))
    sys.stdout.flush()


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------


def parse_milestones(raw: str) -> Tuple[int, ...]:
    values = sorted({int(x) for x in raw.split(",") if x.strip()})
    bad = [v for v in values if not 1 <= v <= 100]
    if bad:
        raise argparse.ArgumentTypeError(f"degrees must be 1–100, got {bad}")
    if not values:
        raise argparse.ArgumentTypeError("need at least one milestone")
    return tuple(values)


def main() -> None:
    parser = argparse.ArgumentParser(description="Live BTD6 paragon degree tracker")
    parser.add_argument("--interval", type=float, default=0.5, help="refresh seconds")
    parser.add_argument("--milestones", type=parse_milestones, default="20,40,60,80,100",
                        help="comma-separated degrees to show (default 20,40,60,80,100)")
    parser.add_argument("--process", default="BloonsTD6.exe")
    args = parser.parse_args()
    milestones = (
        args.milestones
        if isinstance(args.milestones, tuple)
        else parse_milestones(args.milestones)
    )
    enable_ansi()
    sys.stdout.write(CLEAR_SCREEN + HIDE_CURSOR)
    handle: Optional[int] = None
    module_base: Optional[int] = None
    prev_degrees: Dict[str, int] = {}
    last_good: Optional[Snapshot] = None
    try:
        while True:
            status = ""
            snap: Optional[Snapshot] = None
            # (re)attach if needed
            if handle is None or not process_alive(handle):
                if handle is not None:
                    kernel32.CloseHandle(handle)
                handle, module_base, last_good = None, None, None
                prev_degrees.clear()
                found = find_process(args.process)
                if found:
                    pid, handle = found
                    module_base = find_module_base(pid)
                    if module_base is None:
                        kernel32.CloseHandle(handle)
                        handle = None
                        status = "GameAssembly.dll not loaded yet…"
                    else:
                        status = f"attached to pid {pid}"
                else:
                    status = f"waiting for {args.process}…"
            if handle is not None and module_base is not None:
                try:
                    snap = take_snapshot(handle, module_base)
                    last_good = snap
                except ReadError:
                    # menu, loading screen, or between matches
                    if last_good is not None:
                        snap = last_good
                        status = "not in a match — showing last known state"
                    else:
                        snap = None
                        status = "not in a match"
            draw(render(snap, status, prev_degrees, milestones))
            time.sleep(args.interval)
    except KeyboardInterrupt:
        pass
    finally:
        sys.stdout.write(SHOW_CURSOR + "\n")
        sys.stdout.flush()
        if handle is not None:
            kernel32.CloseHandle(handle)


if __name__ == "__main__":
    main()
