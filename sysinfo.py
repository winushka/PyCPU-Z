"""Сбор сведений о компьютере для PyCPU-Z.

Используется только стандартная библиотека Python и встроенные средства
Windows: PowerShell/CIM для паспортных данных, PDH и NtQuerySystemInformation
для живой телеметрии.
"""

from __future__ import annotations

import ctypes
import json
import os
import re
import subprocess
import threading
import time
from ctypes import wintypes
from datetime import datetime, timedelta

CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0

# --------------------------------------------------------------------------
# PowerShell / CIM
# --------------------------------------------------------------------------

_PS_SCRIPT = r"""
$ErrorActionPreference = 'SilentlyContinue'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

function CimInfo([string]$class, [string[]]$props, [string]$filter) {
    if ($filter) { $items = @(Get-CimInstance -ClassName $class -Filter $filter) }
    else         { $items = @(Get-CimInstance -ClassName $class) }
    @($items | Select-Object $props)
}

$data = [ordered]@{
    cs = CimInfo 'Win32_ComputerSystem' @(
        'Manufacturer','Model','SystemFamily','SystemSKUNumber','TotalPhysicalMemory',
        'NumberOfLogicalProcessors','NumberOfProcessors','SystemType','Domain',
        'Workgroup','UserName','Name','PartNumber') $null

    os = CimInfo 'Win32_OperatingSystem' @(
        'Caption','Version','BuildNumber','OSArchitecture','InstallDate',
        'LastBootUpTime','CSName','RegisteredUser','SerialNumber','WindowsDirectory',
        'TotalVisibleMemorySize','FreePhysicalMemory','TotalVirtualMemorySize',
        'FreeVirtualMemory','SystemDrive','LocalDateTime') $null

    cpu = CimInfo 'Win32_Processor' @(
        'Name','Manufacturer','Description','NumberOfCores','NumberOfLogicalProcessors',
        'MaxClockSpeed','CurrentClockSpeed','L2CacheSize','L3CacheSize',
        'SocketDesignation','ProcessorId','Revision','Stepping','Family','Version',
        'Architecture','AddressWidth','VirtualizationFirmwareEnabled',
        'SecondLevelAddressTranslationExtensions','VMMonitorModeExtensions',
        'ExtClock','CurrentVoltage','LoadPercentage') $null

    board = CimInfo 'Win32_BaseBoard' @(
        'Manufacturer','Product','Version','SerialNumber','Tag') $null

    bios = CimInfo 'Win32_BIOS' @(
        'Manufacturer','SMBIOSBIOSVersion','ReleaseDate','Version',
        'SMBIOSMajorVersion','SMBIOSMinorVersion','SerialNumber') $null

    memory = CimInfo 'Win32_PhysicalMemory' @(
        'BankLabel','DeviceLocator','Capacity','Speed','ConfiguredClockSpeed',
        'Manufacturer','PartNumber','SerialNumber','MemoryType','SMBIOSMemoryType',
        'FormFactor','DataWidth','TotalWidth') $null

    memarray = CimInfo 'Win32_PhysicalMemoryArray' @(
        'MemoryDevices','MaxCapacityEx','MemoryErrorCorrection') $null

    gpu = CimInfo 'Win32_VideoController' @(
        'Name','AdapterRAM','DriverVersion','DriverDate','VideoModeDescription',
        'CurrentHorizontalResolution','CurrentVerticalResolution','CurrentRefreshRate',
        'VideoProcessor','Status','AdapterCompatibility','PNPDeviceID') $null

    disks = CimInfo 'Win32_DiskDrive' @(
        'Model','Size','InterfaceType','MediaType','SerialNumber','Partitions',
        'FirmwareRevision','PNPDeviceID') $null

    net = CimInfo 'Win32_NetworkAdapterConfiguration' @(
        'Description','MACAddress','IPAddress','DefaultIPGateway','DHCPEnabled',
        'IPSubnet','DNSServerSearchOrder') 'IPEnabled=True'

    sound = CimInfo 'Win32_SoundDevice' @(
        'Name','Manufacturer','Status') $null

    battery = CimInfo 'Win32_Battery' @(
        'Name','EstimatedChargeRemaining','BatteryStatus') $null

    chipset = CimInfo 'Win32_PnPEntity' @(
        'Name','DeviceID') "Name LIKE '%Chipset%'"
}

$data | ConvertTo-Json -Depth 4 -Compress
"""


def _run_powershell(script: str, timeout: int = 120) -> str:
    cmd = [
        "powershell", "-NoProfile", "-NonInteractive",
        "-ExecutionPolicy", "Bypass", "-Command", script,
    ]
    proc = subprocess.run(
        cmd, capture_output=True, timeout=timeout,
        creationflags=CREATE_NO_WINDOW,
    )
    if proc.returncode != 0:
        err = proc.stderr.decode("utf-8", "replace").strip()
        raise RuntimeError(err or f"PowerShell завершился с кодом {proc.returncode}")
    return proc.stdout.decode("utf-8", "replace")


def _as_list(value) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


# --------------------------------------------------------------------------
# CPUID-подобные данные и реестр
# --------------------------------------------------------------------------

def _cpu_features() -> list[str]:
    """Набор инструкций, который сообщает сама Windows."""
    if os.name != "nt":
        return []
    try:
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.IsProcessorFeaturePresent.argtypes = [wintypes.DWORD]
        k32.IsProcessorFeaturePresent.restype = wintypes.BOOL
    except OSError:
        return []

    checks = [
        ("MMX", 3), ("SSE", 6), ("SSE2", 10), ("SSE3", 13),
        ("SSE4.1", 37), ("SSE4.2", 38), ("AVX", 39), ("AVX2", 40),
        ("AVX-512F", 41), ("3DNow!", 4), ("NX", 12), ("RDTSCP", 32),
    ]
    return [name for name, code in checks if k32.IsProcessorFeaturePresent(code)]


class _CpuId:
    """Вызов инструкции CPUID через крошечный машинный код.

    Нужен, чтобы напрямую узнать поддержку VT-x/AMD-V, AES, BMI и других
    расширений — WMI часть этих битов не отдаёт.
    """

    _CODE = bytes([
        0x53,                   # push rbx
        0x89, 0xC8,             # mov eax, ecx        ; leaf
        0x44, 0x89, 0xC1,       # mov ecx, r8d        ; subleaf
        0x49, 0x89, 0xD1,       # mov r9, rdx         ; out
        0x0F, 0xA2,             # cpuid
        0x41, 0x89, 0x01,       # mov [r9], eax
        0x41, 0x89, 0x59, 0x04, # mov [r9+4], ebx
        0x41, 0x89, 0x49, 0x08, # mov [r9+8], ecx
        0x41, 0x89, 0x51, 0x0C, # mov [r9+0C], edx
        0x5B,                   # pop rbx
        0xC3,                   # ret
    ])

    def __init__(self):
        self.ok = False
        self._fn = None
        if os.name != "nt":
            return
        try:
            k32 = ctypes.WinDLL("kernel32", use_last_error=True)
            k32.VirtualAlloc.restype = ctypes.c_void_p
            k32.VirtualAlloc.argtypes = [
                ctypes.c_void_p, ctypes.c_size_t, wintypes.DWORD, wintypes.DWORD,
            ]
            address = k32.VirtualAlloc(
                None, len(self._CODE), 0x3000, 0x40  # commit/reserve, RWX
            )
            if not address:
                return
            ctypes.memmove(address, self._CODE, len(self._CODE))
            prototype = ctypes.CFUNCTYPE(
                None, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), wintypes.DWORD
            )
            self._fn = prototype(address)
            self.ok = True
        except (OSError, AttributeError):
            self.ok = False

    def __call__(self, leaf: int, subleaf: int = 0) -> tuple[int, int, int, int]:
        out = (wintypes.DWORD * 4)()
        self._fn(leaf, out, subleaf)
        return tuple(out)


_CPUID_VENDORS = {
    "GenuineIntel": "intel",
    "AuthenticAMD": "amd",
}


def _cpuid_info() -> dict:
    """Собирает расширения и сведения о виртуализации из CPUID."""
    cpuid = _CpuId()
    if not cpuid.ok:
        return {}

    try:
        leaf0 = cpuid(0)
        raw_vendor = (
            leaf0[1].to_bytes(4, "little")
            + leaf0[3].to_bytes(4, "little")
            + leaf0[2].to_bytes(4, "little")
        )
        vendor_string = raw_vendor.decode("ascii", "replace")
        max_leaf = leaf0[0]

        leaf1 = cpuid(1)
        ecx1, edx1 = leaf1[2], leaf1[3]
        hypervisor = bool(ecx1 & (1 << 31))

        json_features: list[tuple[str, bool]] = [
            ("MMX", bool(edx1 & (1 << 23))),
            ("SSE", bool(edx1 & (1 << 25))),
            ("SSE2", bool(edx1 & (1 << 26))),
            ("SSE3", bool(ecx1 & (1 << 0))),
            ("SSSE3", bool(ecx1 & (1 << 9))),
            ("SSE4.1", bool(ecx1 & (1 << 19))),
            ("SSE4.2", bool(ecx1 & (1 << 20))),
            ("FMA3", bool(ecx1 & (1 << 12))),
            ("AVX", bool(ecx1 & (1 << 28))),
            ("AES", bool(ecx1 & (1 << 25))),
        ]

        if max_leaf >= 7:
            leaf7 = cpuid(7, 0)
            ebx7 = leaf7[1]
            json_features += [
                ("AVX2", bool(ebx7 & (1 << 5))),
                ("BMI1", bool(ebx7 & (1 << 3))),
                ("BMI2", bool(ebx7 & (1 << 8))),
                ("AVX-512F", bool(ebx7 & (1 << 16))),
            ]

        ext_max = cpuid(0x80000000)[0]
        svm = False
        nx = False
        lm = False
        if ext_max >= 0x80000001:
            ext1 = cpuid(0x80000001)
            svm = bool(ext1[2] & (1 << 2))
            nx = bool(ext1[3] & (1 << 20))
            lm = bool(ext1[3] & (1 << 29))

        vmx = bool(ecx1 & (1 << 5))
        json_features += [("NX", nx), ("RDTSCP", False)]
        if vmx or svm:
            json_features.append(("VT-x" if vmx else "AMD-V", True))

        kind = "vt-x" if _CPUID_VENDORS.get(vendor_string) == "intel" else "amd-v"
        return {
            "vendor": vendor_string,
            "signature": leaf1[0],
            "features": [name for name, supported in json_features if supported],
            "vmx": vmx,
            "svm": svm,
            "hypervisor": hypervisor,
            "long_mode": lm,
            "virt_kind": kind,
            "max_leaf": max_leaf,
        }
    except (OSError, ValueError):
        return {}



_VRAM_KEY = r"SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}"


def _gpu_vram() -> dict[str, int]:
    """Реальный объём видеопамяти из реестра (WMI обрезает его на 4 ГБ)."""
    if os.name != "nt":
        return {}
    try:
        import winreg
    except ImportError:
        return {}

    result: dict[str, int] = {}
    try:
        root = winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE, _VRAM_KEY, 0,
            winreg.KEY_READ | winreg.KEY_WOW64_64KEY,
        )
    except OSError:
        return result

    with root:
        index = 0
        while True:
            try:
                sub_name = winreg.EnumKey(root, index)
            except OSError:
                break
            index += 1
            if not re.fullmatch(r"\d{4}", sub_name):
                continue
            try:
                with winreg.OpenKey(root, sub_name) as key:
                    desc = winreg.QueryValueEx(key, "DriverDesc")[0]
                    try:
                        size = winreg.QueryValueEx(key, "HardwareInformation.qwMemorySize")[0]
                    except OSError:
                        continue
                    result[str(desc)] = int(size)
            except OSError:
                continue
    return result


# --------------------------------------------------------------------------
# Форматирование
# --------------------------------------------------------------------------

def fmt_bytes(value, decimals: int = 1) -> str:
    if value is None:
        return "—"
    try:
        value = float(value)
    except (TypeError, ValueError):
        return "—"
    if value <= 0:
        return "0 Б"
    units = ["Б", "КБ", "МБ", "ГБ", "ТБ", "ПБ"]
    step = 1024.0
    index = 0
    while value >= step and index < len(units) - 1:
        value /= step
        index += 1
    if index == 0:
        return f"{int(value)} {units[index]}"
    text = f"{value:.{decimals}f}".replace(".", ",")
    return f"{text} {units[index]}"


def _cim_date(value):
    """CIM-дата приходит как '/Date(1453766400000)/' или ISO-строкой."""
    if not value:
        return None
    match = re.search(r"/Date\((-?\d+)\)/", str(value))
    if match:
        try:
            return datetime.utcfromtimestamp(int(match.group(1)) / 1000)
        except (ValueError, OSError):
            return None
    text = str(value)
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y%m%d%H%M%S.%f%z", "%Y%m%d%H%M%S"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def fmt_date(value) -> str:
    dt = _cim_date(value)
    return dt.strftime("%d.%m.%Y") if dt else "—"


def fmt_uptime(seconds: float) -> str:
    total = int(max(0, seconds))
    days, rem = divmod(total, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)
    parts = []
    if days:
        parts.append(f"{days} дн.")
    if hours:
        parts.append(f"{hours} ч")
    if minutes:
        parts.append(f"{minutes} мин")
    if not parts:
        parts.append(f"{secs} с")
    return " ".join(parts)


_STEPPING_RE = re.compile(
    r"Family\s+(\d+)\s+Model\s+(\d+)\s+Stepping\s+(\d+)", re.IGNORECASE
)

_CODE_NAMES = {
    # Intel, семейство 6
    (6, 60): "Haswell", (6, 61): "Broadwell", (6, 63): "Haswell-E",
    (6, 69): "Haswell", (6, 70): "Haswell", (6, 71): "Broadwell",
    (6, 78): "Skylake", (6, 85): "Skylake-X", (6, 94): "Skylake",
    (6, 95): "Braswell", (6, 97): "Broadwell", (6, 106): "Ice Lake",
    (6, 108): "Ice Lake", (6, 122): "Goldmont", (6, 126): "Ice Lake",
    (6, 140): "Tiger Lake", (6, 141): "Tiger Lake", (6, 142): "Kaby Lake",
    (6, 143): "Sapphire Rapids", (6, 151): "Alder Lake", (6, 154): "Alder Lake",
    (6, 158): "Kaby Lake / Coffee Lake", (6, 165): "Comet Lake",
    (6, 166): "Comet Lake", (6, 170): "Meteor Lake", (6, 172): "Meteor Lake",
    (6, 183): "Raptor Lake", (6, 186): "Raptor Lake", (6, 189): "Lunar Lake",
    (6, 190): "Raptor Lake Refresh", (6, 191): "Raptor Lake Refresh",
    (6, 207): "Emerald Rapids",
    # AMD
    (23, None): "Zen / Zen 2", (25, None): "Zen 3 / Zen 4",
    (26, None): "Zen 5", (25, 97): "Zen 4",
}

_MEMORY_TYPES = {
    20: "DDR", 21: "DDR2", 24: "DDR3", 26: "DDR4", 27: "LPDDR",
    28: "LPDDR2", 29: "LPDDR3", 30: "LPDDR4", 34: "DDR5", 35: "LPDDR5",
}

_FORM_FACTORS = {
    8: "DIMM", 12: "SODIMM", 11: "RIMM", 13: "SRIMM", 19: "LGA",
    20: "FB-DIMM", 7: "SIMM", 9: "TSOP",
}


def cpu_details(cpu: dict) -> dict:
    """Разбирает описание процессора на семейство/модель/степпинг и кодовое имя."""
    description = str(cpu.get("Description") or "")
    match = _STEPPING_RE.search(description)
    if match:
        family, model, stepping = (int(x) for x in match.groups())
    else:
        family = model = stepping = None

    if family is None:
        # Win32_Processor.Family = 205 у Intel (0xCD), 107 у AMD
        raw_family = cpu.get("Family")
        family = {205: 6, 107: 23}.get(raw_family, raw_family)

    codename = "—"
    if family is not None:
        codename = _CODE_NAMES.get((family, model)) or _CODE_NAMES.get((family, None), "—")

    return {
        "family": family,
        "model": model,
        "stepping": stepping,
        "codename": codename,
    }


# --------------------------------------------------------------------------
# Статический сбор
# --------------------------------------------------------------------------

def _virtualization(cpuid: dict, cpu: dict) -> dict:
    """Итоговый статус аппаратной виртуализации."""
    wmi_enabled = bool(cpu.get("VMMonitorModeExtensions"))
    wmi_slat = bool(cpu.get("SecondLevelAddressTranslationExtensions"))
    vendor = str(cpu.get("Manufacturer") or "").lower()
    kind = "VT-x" if ("intel" in vendor or cpuid.get("virt_kind") == "vt-x") else "AMD-V"

    if not cpuid:
        return {
            "kind": kind,
            "status": "включена" if wmi_enabled else "отключена или не поддерживается",
            "hypervisor": False,
            "slat": "есть" if wmi_slat else "нет",
        }

    if cpuid.get("hypervisor"):
        return {
            "kind": kind,
            "status": "используется гипервизором (Hyper-V)",
            "hypervisor": True,
            "slat": "недоступно (Hyper-V)",
        }

    supported = bool(cpuid.get("vmx") if kind == "VT-x" else cpuid.get("svm"))
    return {
        "kind": kind,
        "status": "включена" if supported else "отключена или не поддерживается",
        "hypervisor": False,
        "slat": "есть" if wmi_slat else "нет",
    }


def collect_static() -> dict:
    """Возвращает словарь с паспортными данными компьютера."""
    raw = json.loads(_run_powershell(_PS_SCRIPT))
    data = {key: _as_list(value) for key, value in raw.items()}

    first_cpu = (data.get("cpu") or [{}])[0]
    cpuid = _cpuid_info()
    data["cpuid"] = cpuid
    data["features"] = cpuid.get("features") or _cpu_features()
    data["virtualization"] = _virtualization(cpuid, first_cpu)
    data["cpu_details"] = cpu_details(first_cpu)

    os_info = (data.get("os") or [{}])[0]
    boot = _cim_date(os_info.get("LastBootUpTime"))
    data["uptime_seconds"] = (
        (datetime.utcnow() - boot).total_seconds() if boot else None
    )

    vram = _gpu_vram()
    for adapter in data.get("gpu", []):
        adapter["VRAM"] = vram.get(str(adapter.get("Name")), 0) or adapter.get("AdapterRAM") or 0

    return data


# --------------------------------------------------------------------------
# Живая телеметрия
# --------------------------------------------------------------------------

class _SPI(ctypes.Structure):
    """SYSTEM_PROCESSOR_PERFORMANCE_INFORMATION."""

    _fields_ = [
        ("IdleTime", ctypes.c_int64),
        ("KernelTime", ctypes.c_int64),
        ("UserTime", ctypes.c_int64),
        ("DpcTime", ctypes.c_int64),
        ("InterruptTime", ctypes.c_int64),
        ("InterruptCount", ctypes.c_uint32),
    ]


class _MEMORYSTATUSEX(ctypes.Structure):
    _fields_ = [
        ("dwLength", wintypes.DWORD),
        ("dwMemoryLoad", wintypes.DWORD),
        ("ullTotalPhys", ctypes.c_ulonglong),
        ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong),
        ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong),
        ("ullAvailVirtual", ctypes.c_ulonglong),
        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


class _Pdh:
    """Быстрое чтение счётчиков производительности (частота процессора)."""

    _PDH_FMT_DOUBLE = 0x00000200

    class _Value(ctypes.Structure):
        _fields_ = [("CStatus", wintypes.DWORD), ("doubleValue", ctypes.c_double)]

    def __init__(self):
        self.ok = False
        self._query = wintypes.HANDLE()
        self._freq_counter = wintypes.HANDLE()
        self._pdh = None
        if os.name != "nt":
            return
        try:
            pdh = ctypes.WinDLL("pdh.dll")
            pdh.PdhAddEnglishCounterW.argtypes = [
                wintypes.HANDLE, wintypes.LPCWSTR, ctypes.c_size_t,
                ctypes.POINTER(wintypes.HANDLE),
            ]
            pdh.PdhGetFormattedCounterValue.argtypes = [
                wintypes.HANDLE, wintypes.DWORD, ctypes.c_void_p,
                ctypes.POINTER(self._Value),
            ]
            if pdh.PdhOpenQueryW(None, 0, ctypes.byref(self._query)) != 0:
                return
            path = r"\Processor Information(_Total)\Processor Frequency"
            if pdh.PdhAddEnglishCounterW(
                self._query, path, 0, ctypes.byref(self._freq_counter)
            ) != 0:
                return
            self._pdh = pdh
            pdh.PdhCollectQueryData(self._query)
            self.ok = True
        except (OSError, AttributeError):
            self.ok = False

    def _read(self, counter) -> float | None:
        try:
            value = self._Value()
            status = self._pdh.PdhGetFormattedCounterValue(
                counter, self._PDH_FMT_DOUBLE, None, ctypes.byref(value)
            )
            if status == 0 and value.CStatus == 0:
                return float(value.doubleValue)
        except (OSError, AttributeError):
            pass
        return None

    def collect(self) -> None:
        if not self.ok:
            return
        try:
            self._pdh.PdhCollectQueryData(self._query)
        except OSError:
            pass

    def frequency_mhz(self) -> float | None:
        if not self.ok:
            return None
        value = self._read(self._freq_counter)
        return value if value and value > 0 else None

    def close(self):
        if self._pdh is not None and self._query:
            try:
                self._pdh.PdhCloseQuery(self._query)
            except OSError:
                pass


def _per_core_times(count: int):
    if os.name != "nt":
        return None
    buffer = (_SPI * count)()
    returned = ctypes.c_ulong()
    status = ctypes.WinDLL("ntdll").NtQuerySystemInformation(
        8, ctypes.byref(buffer), ctypes.sizeof(buffer), ctypes.byref(returned)
    )
    if status != 0:
        return None
    return [(item.IdleTime, item.KernelTime, item.UserTime) for item in buffer]


def _per_core_usage(previous, current):
    usage = []
    for (idle0, kernel0, user0), (idle1, kernel1, user1) in zip(previous, current):
        total = (kernel1 - kernel0) + (user1 - user0)
        idle = idle1 - idle0
        if total <= 0:
            usage.append(0.0)
        else:
            usage.append(max(0.0, min(100.0, 100.0 * (1.0 - idle / total))))
    return usage


class _ProcessorPower(ctypes.Structure):
    """PROCESSOR_POWER_INFORMATION, CallNtPowerInformation(ProcessorInformation)."""

    _fields_ = [
        ("Number", wintypes.ULONG),
        ("MaxMhz", wintypes.ULONG),
        ("CurrentMhz", wintypes.ULONG),
        ("MhzLimit", wintypes.ULONG),
        ("MaxIdleState", wintypes.ULONG),
        ("CurrentIdleState", wintypes.ULONG),
    ]


def _current_mhz() -> float | None:
    """Текущая частота, которую сообщает Windows для каждого ядра."""
    if os.name != "nt":
        return None
    try:
        count = min(max(os.cpu_count() or 1, 1), 128)
        buffer = (_ProcessorPower * count)()
        status = ctypes.WinDLL("powrprof.dll").CallNtPowerInformation(
            11, None, 0, ctypes.byref(buffer), ctypes.sizeof(buffer)
        )
        if status != 0:
            return None
        values = [item.CurrentMhz for item in buffer if item.MaxMhz]
        return sum(values) / len(values) if values else None
    except (OSError, AttributeError):
        return None


def _memory_status() -> dict:
    status = _MEMORYSTATUSEX()
    status.dwLength = ctypes.sizeof(_MEMORYSTATUSEX)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
        return {}
    return {
        "total": status.ullTotalPhys,
        "free": status.ullAvailPhys,
        "used": status.ullTotalPhys - status.ullAvailPhys,
        "percent": float(status.dwMemoryLoad),
    }


class LiveMonitor(threading.Thread):
    """Фоновый поток, обновляющий загрузку ЦП, частоту и память."""

    def __init__(self, interval: float = 1.0):
        super().__init__(daemon=True, name="PyCPUZ-Live")
        self.interval = interval
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self._latest: dict = {}
        self._pdh = _Pdh()
        self._nominal_mhz = 0.0

    def set_nominal_mhz(self, mhz) -> None:
        try:
            self._nominal_mhz = float(mhz or 0)
        except (TypeError, ValueError):
            self._nominal_mhz = 0.0

    def run(self) -> None:
        count = min(max(os.cpu_count() or 1, 1), 128)
        previous = _per_core_times(count)
        while not self._stop_event.wait(self.interval):
            current = _per_core_times(count)
            usage = _per_core_usage(previous, current) if previous and current else []
            previous = current

            self._pdh.collect()
            mhz = _current_mhz() or self._pdh.frequency_mhz() or self._nominal_mhz or None

            sample = {
                "cores": usage,
                "overall": sum(usage) / len(usage) if usage else 0.0,
                "mhz": mhz,
                "memory": _memory_status(),
                "timestamp": time.time(),
            }
            with self._lock:
                self._latest = sample

    def snapshot(self) -> dict:
        with self._lock:
            return dict(self._latest)

    def stop(self) -> None:
        self._stop_event.set()
        self._pdh.close()


# --------------------------------------------------------------------------
# Вспомогательное
# --------------------------------------------------------------------------

def logical_cpu_count() -> int:
    return os.cpu_count() or 1


def benchmark_multi(threads: int, iterations: int = 1200) -> dict:
    """Простой многопоточный бенчмарк на SHA-256 (освобождает GIL)."""
    import hashlib
    from concurrent.futures import ThreadPoolExecutor

    block = b"\xa5" * (256 * 1024)

    def work() -> bytes:
        digest = hashlib.sha256(block).digest()
        for _ in range(iterations):
            digest = hashlib.sha256(digest + block).digest()
        return digest

    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=max(1, threads)) as pool:
        list(pool.map(lambda _: work(), range(max(1, threads))))
    elapsed = time.perf_counter() - started

    megabytes = threads * 256 * 1024 * (iterations + 1) / (1024 * 1024)
    return {
        "seconds": elapsed,
        "mb_per_second": megabytes / elapsed if elapsed else 0.0,
        "threads": threads,
    }
