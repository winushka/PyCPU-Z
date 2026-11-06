"""PyCPU-Z — компактный просмотр сведений о процессоре и системе.

Запуск:  python cpuz.py
"""

from __future__ import annotations

import ctypes
import os
import re
import threading
import time
import tkinter as tk
from datetime import datetime
from tkinter import filedialog, ttk

import sysinfo

APP_VERSION = "1.0.1"

# --------------------------------------------------------------------------
# Оформление
# --------------------------------------------------------------------------

BG = "#1b1e23"
PANEL = "#22262d"
PANEL_ALT = "#2a2f38"
BORDER = "#333a45"
TEXT = "#e8eaed"
MUTED = "#98a1ad"
ACCENT = "#4c9aff"
GREEN = "#37b06b"
RED = "#e85a5a"

FONT = ("Segoe UI", 8)
FONT_SMALL = ("Segoe UI", 7)
FONT_BOLD = ("Segoe UI Semibold", 8)
FONT_BIG = ("Segoe UI Semibold", 11)
FONT_NUM = ("Segoe UI Semibold", 13)

CPU_ROWS = [
    "Сокет",
    "Тип",
    "Кодовое имя",
    "Семейство / модель / степпинг",
    "Инструкции",
    "Ядра / потоки",
    "Базовая частота",
    "Текущая частота",
    "Множитель",
    "Шина",
    "Кэш L2",
    "Кэш L3",
    "Напряжение",
    "Виртуализация",
    "SLAT / VT-d",
    "Разрядность",
]

MEMORY_ROWS = [
    "Всего памяти",
    "Используется",
    "Свободно",
    "Загрузка",
    "Тип памяти",
    "Частота",
    "Форм-фактор",
    "Слоты",
]

SPD_ROWS = [
    "Слот",
    "Объём",
    "Тип",
    "Частота",
    "Производитель",
    "Партийный номер",
    "Серийный номер",
    "Форм-фактор",
    "Ширина шины",
]

GPU_ROWS = [
    "Производитель",
    "Видеопамять",
    "Драйвер",
    "Дата драйвера",
    "Разрешение",
    "Частота обновления",
]

OS_ROWS = [
    "Система",
    "Версия",
    "Сборка",
    "Архитектура",
    "Установлена",
    "Время работы",
    "Компьютер",
    "Пользователь",
    "Плата",
    "Чипсет",
    "BIOS",
    "SMBIOS",
    "Серийный номер",
]

VENDOR_STYLES = {
    "intel": {
        "text": "intel", "fg": "#5aa9e6", "bg": "#0f2030",
        "border": "#2f5c80", "font": ("Segoe UI", 12, "bold"),
    },
    "amd": {
        "text": "AMD", "fg": "#f2f2f2", "bg": "#1d1d1f",
        "border": "#4a4a4d", "font": ("Segoe UI", 11, "bold"),
    },
    "nvidia": {
        "text": "NVIDIA", "fg": "#76b900", "bg": "#151f09",
        "border": "#3c5216", "font": ("Segoe UI", 8, "bold"),
    },
    "other": {
        "text": "CPU", "fg": MUTED, "bg": PANEL_ALT,
        "border": BORDER, "font": ("Segoe UI", 8, "bold"),
    },
}


def detect_vendor(*texts) -> str:
    joined = " ".join(str(item or "") for item in texts).lower()
    if "nvidia" in joined:
        return "nvidia"
    if "intel" in joined:
        return "intel"
    if "amd" in joined or "advanced micro devices" in joined or "ati " in joined:
        return "amd"
    return "other"


def load_color(percent: float) -> str:
    """Плавный переход: синий → оранжевый → красный."""
    stops = [
        (0.0, (76, 154, 255)),
        (0.60, (76, 154, 255)),
        (0.85, (232, 163, 61)),
        (1.0, (232, 90, 90)),
    ]
    value = max(0.0, min(1.0, percent / 100.0))
    for index in range(1, len(stops)):
        left_pos, left_color = stops[index - 1]
        right_pos, right_color = stops[index]
        if value <= right_pos:
            span = right_pos - left_pos or 1.0
            ratio = (value - left_pos) / span
            rgb = tuple(
                int(left_color[i] + (right_color[i] - left_color[i]) * ratio)
                for i in range(3)
            )
            return "#{:02x}{:02x}{:02x}".format(*rgb)
    return "#e85a5a"


def fmt_mhz(value: float | None) -> str:
    if not value:
        return "—"
    return f"{value:,.0f}".replace(",", " ") + " МГц"


def plural(count, one: str, few: str, many: str) -> str:
    """Русская форма слова для числа: 1 ядро, 2 ядра, 5 ядер."""
    number = abs(int(count or 0)) % 100
    if 11 <= number <= 14:
        return many
    number %= 10
    if number == 1:
        return one
    if 2 <= number <= 4:
        return few
    return many


def _rounded_rect(canvas: tk.Canvas, x0, y0, x1, y1, radius, **kwargs):
    points = [
        x0 + radius, y0, x1 - radius, y0, x1, y0,
        x1, y0 + radius, x1, y1 - radius, x1, y1,
        x1 - radius, y1, x0 + radius, y1, x0, y1,
        x0, y1 - radius, x0, y0 + radius, x0, y0,
    ]
    return canvas.create_polygon(points, smooth=True, **kwargs)


# --------------------------------------------------------------------------
# Виджеты
# --------------------------------------------------------------------------

class VendorBadge(tk.Canvas):
    """Значок производителя процессора или видеокарты."""

    def __init__(self, master, vendor: str, width: int = 62, height: int = 34,
                 bg: str = BG):
        super().__init__(master, width=width, height=height, bg=bg,
                         highlightthickness=0, bd=0)
        style = VENDOR_STYLES.get(detect_vendor(vendor), VENDOR_STYLES["other"])
        _rounded_rect(self, 1, 1, width - 1, height - 1, 7,
                      fill=style["bg"], outline=style["border"], width=1)
        # у AMD — фирменный зелёный треугольник
        text_x = width / 2
        if style is VENDOR_STYLES["amd"]:
            self.create_polygon(
                width - 14, height / 2 - 6, width - 14, height / 2 + 6,
                width - 6, height / 2, fill="#7cbf3f", outline="",
            )
            text_x = width / 2 - 4
        self.create_text(text_x, height / 2, text=style["text"],
                         fill=style["fg"], font=style["font"])


class InfoTable(tk.Frame):
    """Таблица «параметр — значение» на обычных Label."""

    def __init__(self, master, keys, value_wrap: int = 330,
                 key_width: int | None = None):
        super().__init__(master, bg=PANEL, highlightbackground=BORDER,
                         highlightthickness=1, bd=0)
        self._values: dict[str, tk.Label] = {}
        self.columnconfigure(1, weight=1)
        self.columnconfigure(0, minsize=118)

        for row, key in enumerate(keys):
            options = {}
            if key_width:
                options["width"] = key_width
            tk.Label(
                self, text=key, bg=PANEL, fg=MUTED, font=FONT, anchor="w",
                padx=9, pady=1, **options,
            ).grid(row=row, column=0, sticky="nw")
            value = tk.Label(
                self, text="—", bg=PANEL, fg=TEXT, font=FONT, anchor="w",
                justify="left", wraplength=value_wrap, padx=5, pady=1,
            )
            value.grid(row=row, column=1, sticky="nwe")
            self._values[key] = value

    def set(self, key: str, value) -> None:
        label = self._values.get(key)
        if label is None:
            return
        text = "—" if value in (None, "", 0) else str(value)
        label.config(text=text)

    def update_values(self, mapping: dict) -> None:
        for key, value in mapping.items():
            self.set(key, value)


class CoreBars(tk.Canvas):
    """Столбики загрузки для каждого логического ядра."""

    def __init__(self, master, height: int = 86):
        super().__init__(master, bg=PANEL, highlightthickness=1,
                         highlightbackground=BORDER, bd=0, height=height)
        self._bars: list[int] = []
        self._percent_text: list[int] = []
        self._labels: list[int] = []
        self._values: list[float] = []
        self.bind("<Configure>", lambda _event: self._redraw())

    def set_values(self, values) -> None:
        values = list(values)
        if len(values) != len(self._bars):
            self._build(len(values))
        self._values = values
        self._redraw()

    def _build(self, count: int) -> None:
        self.delete("all")
        self._bars.clear()
        self._percent_text.clear()
        self._labels.clear()
        if count <= 0:
            return
        for index in range(count):
            self._bars.append(self.create_rectangle(0, 0, 1, 1, width=0, fill=ACCENT))
            self._percent_text.append(
                self.create_text(0, 0, text="", fill=MUTED, font=("Segoe UI", 7),
                                 state="hidden")
            )
            self._labels.append(
                self.create_text(0, 0, text=f"C{index}", fill=MUTED,
                                 font=("Segoe UI", 7))
            )

    def _redraw(self) -> None:
        if not self._bars or not self._values:
            return
        width = max(self.winfo_width(), 200)
        height = max(self.winfo_height(), 60)
        base = height - 18
        top = 8
        cell = width / len(self._bars)
        self.delete("base")
        self.create_line(6, base, width - 6, base, fill=BORDER, tags="base")
        self.tag_lower("base")

        for index, value in enumerate(self._values):
            left = index * cell + 3
            right = (index + 1) * cell - 3
            bar_height = max(2.0, (base - top) * max(0.0, min(100.0, value)) / 100.0)
            self.coords(self._bars[index], left, base - bar_height, right, base)
            self.itemconfig(self._bars[index], fill=load_color(value))
            center = (left + right) / 2
            self.coords(self._labels[index], center, height - 8)
            if cell >= 34:
                self.coords(self._percent_text[index], center, base - bar_height - 7)
                self.itemconfig(self._percent_text[index],
                                text=f"{value:.0f}", state="normal")
            else:
                self.itemconfig(self._percent_text[index], state="hidden")


class Metric(tk.Frame):
    """Компактный показатель с подписью и полосой загрузки."""

    def __init__(self, master, caption: str, with_bar: bool = True):
        super().__init__(master, bg=PANEL)
        self.with_bar = with_bar
        tk.Label(self, text=caption.upper(), bg=PANEL, fg=MUTED,
                 font=("Segoe UI", 7), anchor="w").pack(
            fill="x", padx=9, pady=(6, 0))
        self.value_label = tk.Label(self, text="—", bg=PANEL, fg=TEXT,
                                    font=FONT_NUM, anchor="w")
        self.value_label.pack(fill="x", padx=9)
        self.bar = None
        self.sub_label = None
        if with_bar:
            self.bar = ttk.Progressbar(self, style="Dark.Horizontal.TProgressbar",
                                       maximum=100, value=0)
            self.bar.pack(fill="x", padx=9, pady=(1, 6))
        else:
            self.sub_label = tk.Label(self, text="", bg=PANEL, fg=MUTED,
                                      font=FONT_SMALL, anchor="w")
            self.sub_label.pack(fill="x", padx=9, pady=(1, 8))

    def set(self, text: str, percent: float | None = None,
            color: str | None = None) -> None:
        self.value_label.config(text=text, fg=color or TEXT)
        if self.bar is not None and percent is not None:
            self.bar["value"] = max(0.0, min(100.0, percent))

    def set_sub(self, text: str) -> None:
        if self.sub_label is not None:
            self.sub_label.config(text=text)


# --------------------------------------------------------------------------
# Приложение
# --------------------------------------------------------------------------

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("PyCPU-Z — сведения о системе")
        self.configure(bg=BG)

        # Компактное окно, как у оригинала; масштабируется под DPI Windows.
        scale = max(1.0, self.winfo_fpixels("1i") / 96.0)
        width = min(int(540 * scale), self.winfo_screenwidth() - 40)
        height = min(int(660 * scale), self.winfo_screenheight() - 80)
        self.geometry(f"{width}x{height}")
        # Размер окна фиксирован, как в оригинальном CPU-Z.
        self.resizable(False, False)
        self.maxsize(width, height)

        self.data: dict | None = None
        self.live = sysinfo.LiveMonitor(interval=1.0)
        self.live.start()
        self._bench_running = False
        self._bench_one_result: dict | None = None
        self._spd_modules: list[dict] = []
        self._closing = False

        self._init_style()
        self._build_icon()
        self._build_layout()

        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(150, self._pump_live)
        threading.Thread(target=self._load_data, daemon=True,
                         name="PyCPUZ-Collect").start()

    # -- оформление -----------------------------------------------------

    def _init_style(self) -> None:
        style = ttk.Style(self)
        style.theme_use("clam")

        style.configure("TNotebook", background=BG, borderwidth=0)
        style.configure("TNotebook.Tab", background=BG, foreground=MUTED,
                        padding=(11, 4), font=FONT_BOLD, borderwidth=0)
        style.map("TNotebook.Tab",
                  background=[("selected", PANEL)],
                  foreground=[("selected", TEXT)])

        style.configure("Treeview", background=PANEL, fieldbackground=PANEL,
                        foreground=TEXT, rowheight=20, borderwidth=0, font=FONT)
        style.configure("Treeview.Heading", background=PANEL_ALT, foreground=MUTED,
                        relief="flat", borderwidth=0, font=FONT_BOLD,
                        padding=(4, 2))
        style.map("Treeview.Heading", background=[("active", PANEL_ALT)])
        style.map("Treeview",
                  background=[("selected", "#33507a")],
                  foreground=[("selected", TEXT)])

        style.configure("Dark.Horizontal.TProgressbar", troughcolor=PANEL_ALT,
                        background=ACCENT, borderwidth=0, thickness=5)
        style.configure("Tool.TButton", background=PANEL_ALT, foreground=TEXT,
                        borderwidth=0, focusthickness=0, padding=(8, 3),
                        font=FONT)
        style.map("Tool.TButton",
                  background=[("active", "#38404c"), ("pressed", "#2f3641")],
                  foreground=[("disabled", MUTED)])
        style.configure("Vertical.TScrollbar", background=PANEL_ALT,
                        troughcolor=BG, bordercolor=BG, arrowcolor=MUTED,
                        darkcolor=PANEL_ALT, lightcolor=PANEL_ALT,
                        borderwidth=0, arrowsize=11)
        style.map("Vertical.TScrollbar",
                  background=[("active", "#3a4250"), ("pressed", "#3a4250")])

        style.configure("TCombobox", fieldbackground=PANEL_ALT,
                        background=PANEL_ALT, foreground=TEXT,
                        arrowcolor=MUTED, bordercolor=BORDER,
                        lightcolor=PANEL_ALT, darkcolor=PANEL_ALT,
                        padding=3, font=FONT)
        style.map("TCombobox",
                  fieldbackground=[("readonly", PANEL_ALT)],
                  foreground=[("readonly", TEXT)],
                  selectbackground=[("readonly", PANEL_ALT)],
                  selectforeground=[("readonly", TEXT)])

        for option, value in (
            ("*TCombobox*Listbox.background", PANEL),
            ("*TCombobox*Listbox.foreground", TEXT),
            ("*TCombobox*Listbox.selectBackground", "#33507a"),
            ("*TCombobox*Listbox.selectForeground", TEXT),
            ("*TCombobox*Listbox.font", FONT),
        ):
            self.option_add(option, value)

    def _build_icon(self) -> None:
        """Рисует простую иконку «чип» средствами Tk."""
        size = 32
        icon = tk.PhotoImage(width=size, height=size)
        icon.put(BG, to=(0, 0, size, size))
        for x in (6, 12, 18, 24):
            icon.put(ACCENT, to=(x, 1, x + 2, 6))
            icon.put(ACCENT, to=(x, 26, x + 2, 31))
        for y in (6, 12, 18, 24):
            icon.put(ACCENT, to=(1, y, 6, y + 2))
            icon.put(ACCENT, to=(26, y, 31, y + 2))
        icon.put("#2a3a52", to=(6, 6, 26, 26))
        icon.put(ACCENT, to=(8, 8, 24, 24))
        icon.put(BG, to=(11, 11, 21, 21))
        self._icon_image = icon
        try:
            self.iconphoto(True, icon)
        except tk.TclError:
            pass

    # -- разметка -------------------------------------------------------

    def _build_layout(self) -> None:
        top = tk.Frame(self, bg="#15181d", height=34)
        top.pack(fill="x")
        top.pack_propagate(False)

        tk.Label(top, text="PyCPU-Z", bg="#15181d", fg=ACCENT,
                 font=("Segoe UI Semibold", 11)).pack(side="left", padx=(10, 0))

        actions = tk.Frame(top, bg="#15181d")
        actions.pack(side="right", padx=8)
        self.btn_refresh = ttk.Button(actions, text="Обновить",
                                      style="Tool.TButton", command=self._load_data)
        self.btn_refresh.pack(side="right", padx=(6, 0), pady=5)
        ttk.Button(actions, text="Отчёт", style="Tool.TButton",
                   command=self._save_report).pack(side="right", pady=5)

        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill="both", expand=True, padx=8, pady=(6, 2))

        self.tab_cpu = tk.Frame(self.notebook, bg=BG)
        self.tab_memory = tk.Frame(self.notebook, bg=BG)
        self.tab_spd = tk.Frame(self.notebook, bg=BG)
        self.tab_gpu = tk.Frame(self.notebook, bg=BG)
        self.tab_os = tk.Frame(self.notebook, bg=BG)
        self.tab_bench = tk.Frame(self.notebook, bg=BG)

        self.notebook.add(self.tab_cpu, text=" ЦП ")
        self.notebook.add(self.tab_memory, text=" Память ")
        self.notebook.add(self.tab_spd, text=" SPD ")
        self.notebook.add(self.tab_gpu, text=" Графика ")
        self.notebook.add(self.tab_os, text=" ОС ")
        self.notebook.add(self.tab_bench, text=" Тест ")

        self._build_cpu_tab()
        self._build_memory_tab()
        self._build_spd_tab()
        self._build_gpu_tab()
        self._build_os_tab()
        self._build_bench_tab()

        status = tk.Frame(self, bg="#15181d", height=22)
        status.pack(fill="x", side="bottom")
        status.pack_propagate(False)
        self.status = tk.Label(status, text="Сбор данных о системе…", bg="#15181d",
                               fg=MUTED, font=FONT_SMALL, anchor="w")
        self.status.pack(side="left", padx=10)
        tk.Label(status, text=f"PyCPU-Z {APP_VERSION}", bg="#15181d", fg=MUTED,
                 font=FONT_SMALL).pack(side="right", padx=10)

    # -- вкладка «ЦП» ---------------------------------------------------

    def _build_cpu_tab(self) -> None:
        head = tk.Frame(self.tab_cpu, bg=BG)
        head.pack(fill="x", padx=8, pady=(6, 4))

        self.cpu_badge = VendorBadge(head, "cpu", width=60, height=36)
        self.cpu_badge.pack(side="left", padx=(0, 8))

        titles = tk.Frame(head, bg=BG)
        titles.pack(side="left", fill="x", expand=True)
        self.cpu_name = tk.Label(titles, text="Определение процессора…", bg=BG,
                                 fg=TEXT, font=FONT_BIG, anchor="w",
                                 justify="left", wraplength=400)
        self.cpu_name.pack(fill="x")
        self.cpu_sub = tk.Label(titles, text="", bg=BG, fg=MUTED, font=FONT,
                                anchor="w")
        self.cpu_sub.pack(fill="x")

        self.cpu_table = InfoTable(self.tab_cpu, CPU_ROWS, value_wrap=330)
        self.cpu_table.pack(fill="x", padx=8)

        strip = tk.Frame(self.tab_cpu, bg=PANEL, highlightbackground=BORDER,
                         highlightthickness=1)
        strip.pack(fill="x", padx=8, pady=(5, 0))
        self.metric_cpu = Metric(strip, "Загрузка ЦП")
        self.metric_cpu.pack(side="left", fill="both", expand=True)
        tk.Frame(strip, bg=BORDER, width=1).pack(side="left", fill="y")
        self.metric_freq = Metric(strip, "Частота", with_bar=False)
        self.metric_freq.pack(side="left", fill="both", expand=True)
        tk.Frame(strip, bg=BORDER, width=1).pack(side="left", fill="y")
        self.metric_mem = Metric(strip, "Память")
        self.metric_mem.pack(side="left", fill="both", expand=True)

        cores = tk.Frame(self.tab_cpu, bg=BG)
        cores.pack(fill="both", expand=True, padx=8, pady=(4, 6))
        self.cores_caption = tk.Label(cores, text="ЗАГРУЗКА ЯДЕР", bg=BG, fg=MUTED,
                                      font=FONT_SMALL, anchor="w")
        self.cores_caption.pack(fill="x", pady=(0, 3))
        self.core_bars = CoreBars(cores, height=70)
        self.core_bars.pack(fill="both", expand=True)

    # -- вкладка «Память» -----------------------------------------------

    def _build_memory_tab(self) -> None:
        wrap = tk.Frame(self.tab_memory, bg=BG)
        wrap.pack(fill="both", expand=True, padx=8, pady=8)

        tk.Label(wrap, text="Оперативная память", bg=BG, fg=TEXT,
                 font=("Segoe UI Semibold", 10), anchor="w").pack(
            fill="x", pady=(0, 5))

        self.mem_table = InfoTable(wrap, MEMORY_ROWS, value_wrap=330)
        self.mem_table.pack(fill="x")

        bar_box = tk.Frame(wrap, bg=BG)
        bar_box.pack(fill="x", pady=(6, 0))
        self.mem_bar = ttk.Progressbar(bar_box, style="Dark.Horizontal.TProgressbar",
                                       maximum=100, value=0)
        self.mem_bar.pack(fill="x")
        self.mem_bar_text = tk.Label(bar_box, text="", bg=BG, fg=MUTED,
                                     font=FONT, anchor="w")
        self.mem_bar_text.pack(fill="x", pady=(2, 0))

        tk.Label(wrap, text="СЛОТЫ ПАМЯТИ", bg=BG, fg=MUTED,
                 font=FONT_SMALL, anchor="w").pack(fill="x", pady=(10, 4))
        self.mem_slots = tk.Frame(wrap, bg=BG)
        self.mem_slots.pack(fill="x")
        self.mem_slots.columnconfigure(0, weight=1)
        self.mem_slots.columnconfigure(1, weight=1)

        self.mem_table.set("Тип памяти", "определение…")
        self.mem_table.set("Слоты", "определение…")

    # -- вкладка «SPD» --------------------------------------------------

    def _build_spd_tab(self) -> None:
        wrap = tk.Frame(self.tab_spd, bg=BG)
        wrap.pack(fill="both", expand=True, padx=8, pady=8)

        self.spd_title = tk.Label(wrap, text="Модули памяти", bg=BG, fg=TEXT,
                                  font=("Segoe UI Semibold", 10), anchor="w")
        self.spd_title.pack(fill="x", pady=(0, 5))

        self.spd_selector = ttk.Combobox(wrap, state="readonly", values=[],
                                         font=FONT)
        self.spd_selector.pack(fill="x", pady=(0, 6))
        self.spd_selector.bind("<<ComboboxSelected>>",
                               lambda _event: self._show_spd_module())

        self.spd_table = InfoTable(wrap, SPD_ROWS, value_wrap=300)
        self.spd_table.pack(fill="x")

    # -- вкладка «Графика» ----------------------------------------------

    def _build_gpu_tab(self) -> None:
        self.gpu_container = tk.Frame(self.tab_gpu, bg=BG)
        self.gpu_container.pack(fill="both", expand=True, padx=8, pady=8)

    # -- вкладка «ОС» ---------------------------------------------------

    def _build_os_tab(self) -> None:
        self.os_container = tk.Frame(self.tab_os, bg=BG)
        self.os_container.pack(fill="both", expand=True, padx=8, pady=8)

    # -- вкладка «Тест» -------------------------------------------------

    def _build_bench_tab(self) -> None:
        wrap = tk.Frame(self.tab_bench, bg=BG)
        wrap.pack(fill="both", expand=True, padx=8, pady=8)

        tk.Label(wrap, text="Тест производительности", bg=BG, fg=TEXT,
                 font=("Segoe UI Semibold", 10), anchor="w").pack(fill="x")
        tk.Label(
            wrap,
            text=("Процессор хэширует блоки данных алгоритмом SHA-256. "
                  "Один поток — скорость одного ядра, все потоки — работа "
                  "всех логических процессоров."),
            bg=BG, fg=MUTED, font=FONT, anchor="w", justify="left",
            wraplength=500,
        ).pack(fill="x", pady=(3, 8))

        buttons = tk.Frame(wrap, bg=BG)
        buttons.pack(fill="x", pady=(0, 8))
        self.btn_bench_one = ttk.Button(buttons, text="Тест одного потока",
                                        style="Tool.TButton",
                                        command=lambda: self._start_bench(1))
        self.btn_bench_one.pack(side="left")
        self.btn_bench_all = ttk.Button(
            buttons, text=f"Все потоки ({sysinfo.logical_cpu_count()})",
            style="Tool.TButton",
            command=lambda: self._start_bench(sysinfo.logical_cpu_count()),
        )
        self.btn_bench_all.pack(side="left", padx=6)

        self.bench_bar = ttk.Progressbar(wrap, style="Dark.Horizontal.TProgressbar",
                                         mode="determinate", maximum=100)
        self.bench_bar.pack(fill="x")

        self.bench_results = InfoTable(
            wrap, ["Один поток", "Все потоки", "Ускорение"], value_wrap=420,
        )
        self.bench_results.pack(fill="x", pady=(8, 0))

    # -- данные ---------------------------------------------------------

    def _load_data(self) -> None:
        if self._closing:
            return
        self.status.config(text="Сбор данных о системе…")
        self.btn_refresh.state(["disabled"])
        threading.Thread(target=self._collect_worker, daemon=True,
                         name="PyCPUZ-Collect").start()

    def _collect_worker(self) -> None:
        try:
            data = sysinfo.collect_static()
        except Exception as error:  # noqa: BLE001 — показываем пользователю
            self.after(0, lambda: self._data_failed(error))
            return
        self.after(0, lambda: self._apply_data(data))

    def _data_failed(self, error: Exception) -> None:
        self.status.config(text=f"Не удалось получить данные: {error}")
        self.btn_refresh.state(["!disabled"])

    def _apply_data(self, data: dict) -> None:
        self.data = data
        self._fill_cpu()
        self._fill_memory()
        self._fill_spd()
        self._fill_gpu()
        self._fill_os()
        self.btn_refresh.state(["!disabled"])
        self.status.config(text="Данные обновлены: " + time.strftime("%H:%M:%S"))
        self._pump_live()

    def _fill_cpu(self) -> None:
        data = self.data or {}
        cpu = (data.get("cpu") or [{}])[0]
        details = data.get("cpu_details") or {}
        virt = data.get("virtualization") or {}

        name = " ".join(str(cpu.get("Name") or "Неизвестный процессор").split())
        self.cpu_name.config(text=name)
        self.live.set_nominal_mhz(cpu.get("MaxClockSpeed"))
        self.cpu_badge.destroy()
        self.cpu_badge = VendorBadge(
            self.cpu_name.master.master, name + " " + str(cpu.get("Manufacturer") or ""),
            width=60, height=36,
        )
        self.cpu_badge.pack(side="left", padx=(0, 8), before=self.cpu_name.master)

        cores = cpu.get("NumberOfCores")
        threads = cpu.get("NumberOfLogicalProcessors")
        socket = cpu.get("SocketDesignation")
        codename = details.get("codename")
        sub_parts = [
            f"{cores} {plural(cores, 'ядро', 'ядра', 'ядер')} / "
            f"{threads} {plural(threads, 'поток', 'потока', 'потоков')}"
            if cores else None,
            str(socket).strip() if socket else None,
            None if codename in (None, "—") else str(codename),
        ]
        self.cpu_sub.config(text="  •  ".join(part for part in sub_parts if part))

        family = details.get("family")
        model = details.get("model")
        stepping = details.get("stepping")
        fms = "—"
        if family is not None:
            fms = f"Семейство {family}"
            if model is not None:
                fms += f", модель {model}"
            if stepping is not None:
                fms += f", степпинг {stepping}"

        features = data.get("features") or []
        l2 = cpu.get("L2CacheSize")
        l3 = cpu.get("L3CacheSize")
        voltage = cpu.get("CurrentVoltage")
        voltage_text = "—"
        if isinstance(voltage, (int, float)) and 10 <= voltage <= 30:
            voltage_text = f"{voltage / 10:.2f} В"

        virt_text = f"{virt.get('kind', 'VT-x')}: {virt.get('status', '—')}"

        self.cpu_table.update_values({
            "Сокет": cpu.get("SocketDesignation"),
            "Тип": cpu.get("Manufacturer"),
            "Кодовое имя": details.get("codename", "—"),
            "Семейство / модель / степпинг": fms,
            "Инструкции": ", ".join(features) if features else "—",
            "Ядра / потоки": f"{cores or '—'} / {threads or '—'}",
            "Базовая частота": fmt_mhz(cpu.get("MaxClockSpeed")),
            "Шина": fmt_mhz(cpu.get("ExtClock")),
            "Кэш L2": f"{l2} КБ" if l2 else "—",
            "Кэш L3": f"{l3} КБ" if l3 else "—",
            "Напряжение": voltage_text,
            "Виртуализация": virt_text,
            "SLAT / VT-d": virt.get("slat", "—"),
            "Разрядность": f"{cpu.get('AddressWidth') or '—'}-бит",
        })
        self.cpu_table.set("Текущая частота", "измерение…")
        self.cpu_table.set("Множитель", "измерение…")
        self.metric_freq.set_sub(f"Базовая: {fmt_mhz(cpu.get('MaxClockSpeed'))}")
        self.cores_caption.config(
            text=f"ЗАГРУЗКА ЯДЕР — {threads or sysinfo.logical_cpu_count()} логических процессоров"
        )

    def _fill_memory(self) -> None:
        data = self.data or {}
        modules = data.get("memory") or []
        arrays = data.get("memarray") or []
        total = sum(int(m.get("Capacity") or 0) for m in modules)

        if not total:
            system = (data.get("cs") or [{}])[0]
            total = int(system.get("TotalPhysicalMemory") or 0)

        first = modules[0] if modules else {}
        mem_type = sysinfo._MEMORY_TYPES.get(
            int(first.get("SMBIOSMemoryType") or 0),
            sysinfo._MEMORY_TYPES.get(int(first.get("MemoryType") or 0), "—"),
        )
        speed = first.get("ConfiguredClockSpeed") or first.get("Speed")
        form = sysinfo._FORM_FACTORS.get(int(first.get("FormFactor") or 0), "—")
        slots = int((arrays[0] if arrays else {}).get("MemoryDevices") or 0) or len(modules)

        self.mem_table.update_values({
            "Всего памяти": sysinfo.fmt_bytes(total),
            "Тип памяти": mem_type,
            "Частота": f"{speed} МГц" if speed else "—",
            "Форм-фактор": form,
            "Слоты": f"{len(modules)} из {slots}",
        })
        self._fill_memory_slots(modules, slots, mem_type, form)

    def _fill_memory_slots(self, modules, slots, mem_type, form) -> None:
        for child in self.mem_slots.winfo_children():
            child.destroy()

        total_slots = max(slots, len(modules), 1)
        for index in range(total_slots):
            module = modules[index] if index < len(modules) else None
            card = tk.Frame(self.mem_slots, bg=PANEL, height=76,
                            highlightbackground=BORDER, highlightthickness=1)
            card.grid(row=index // 2, column=index % 2, sticky="ew",
                      padx=(0, 6) if index % 2 == 0 else (6, 0),
                      pady=(0, 6))
            card.grid_propagate(False)
            card.columnconfigure(0, weight=1)

            slot_name = (str(module.get("DeviceLocator") or "").strip()
                         if module else f"Слот {index + 1}")
            size = sysinfo.fmt_bytes(module.get("Capacity")) if module else "Пусто"
            module_speed = ((module.get("ConfiguredClockSpeed")
                             or module.get("Speed")) if module else None)
            module_type = "—"
            if module:
                module_type = sysinfo._MEMORY_TYPES.get(
                    int(module.get("SMBIOSMemoryType") or 0),
                    sysinfo._MEMORY_TYPES.get(int(module.get("MemoryType") or 0), "—"),
                )
            detail = (f"{module_type} • {module_speed} МГц"
                      if module and module_speed else
                      (f"{form} • DDR" if module else "свободный слот"))

            tk.Label(card, text=slot_name or "DIMM", bg=PANEL, fg=MUTED,
                     font=FONT_SMALL, anchor="w").grid(
                row=0, column=0, sticky="ew", padx=9, pady=(7, 0))
            tk.Label(card, text=size, bg=PANEL,
                     fg=TEXT if module else MUTED,
                     font=("Segoe UI Semibold", 11), anchor="w").grid(
                row=1, column=0, sticky="ew", padx=9)
            tk.Label(card, text=detail, bg=PANEL, fg=MUTED,
                     font=FONT_SMALL, anchor="w").grid(
                row=2, column=0, sticky="ew", padx=9, pady=(0, 6))

    def _fill_spd(self) -> None:
        self._spd_modules = (self.data or {}).get("memory") or []
        arrays = (self.data or {}).get("memarray") or []
        slots = int((arrays[0] if arrays else {}).get("MemoryDevices") or 0)
        total = sum(int(module.get("Capacity") or 0)
                    for module in self._spd_modules)

        self.spd_title.config(
            text=f"Модули памяти — {len(self._spd_modules)}"
                 + (f" из {slots} слотов" if slots else "")
                 + f", всего {sysinfo.fmt_bytes(total)}"
        )

        labels = []
        for index, module in enumerate(self._spd_modules):
            slot = str(module.get("DeviceLocator") or f"Слот {index + 1}").strip()
            labels.append(f"{slot} — {sysinfo.fmt_bytes(module.get('Capacity'))}")
        self.spd_selector["values"] = labels
        if labels:
            self.spd_selector.current(0)
        self._show_spd_module()

    def _show_spd_module(self) -> None:
        modules = self._spd_modules
        if not modules:
            for key in SPD_ROWS:
                self.spd_table.set(key, "—")
            return
        index = self.spd_selector.current()
        module = modules[index if 0 <= index < len(modules) else 0]
        mem_type = sysinfo._MEMORY_TYPES.get(
            int(module.get("SMBIOSMemoryType") or 0),
            sysinfo._MEMORY_TYPES.get(int(module.get("MemoryType") or 0), "—"),
        )
        speed = module.get("ConfiguredClockSpeed") or module.get("Speed")
        width = module.get("DataWidth")
        self.spd_table.update_values({
            "Слот": str(module.get("DeviceLocator") or "—").strip(),
            "Объём": sysinfo.fmt_bytes(module.get("Capacity")),
            "Тип": mem_type,
            "Частота": f"{speed} МГц" if speed else "—",
            "Производитель": str(module.get("Manufacturer") or "—").strip(),
            "Партийный номер": str(module.get("PartNumber") or "—").strip(),
            "Серийный номер": str(module.get("SerialNumber") or "—").strip(),
            "Форм-фактор": sysinfo._FORM_FACTORS.get(
                int(module.get("FormFactor") or 0), "—"),
            "Ширина шины": f"{width} бит" if width else "—",
        })

    def _fill_gpu(self) -> None:
        for child in self.gpu_container.winfo_children():
            child.destroy()

        adapters = (self.data or {}).get("gpu") or []
        if not adapters:
            tk.Label(self.gpu_container, text="Видеоадаптеры не найдены",
                     bg=BG, fg=MUTED, font=FONT).pack(anchor="w")
            return

        for adapter in adapters:
            name = " ".join(str(adapter.get("Name") or "Видеоадаптер").split())
            head = tk.Frame(self.gpu_container, bg=BG)
            head.pack(fill="x", pady=(0, 4))
            VendorBadge(
                head, name + " " + str(adapter.get("AdapterCompatibility") or ""),
                width=56, height=30,
            ).pack(side="left", padx=(0, 7))
            tk.Label(head, text=name, bg=BG, fg=TEXT,
                     font=("Segoe UI Semibold", 10), anchor="w",
                     justify="left", wraplength=400).pack(
                side="left", fill="x", expand=True)

            resolution = "—"
            if adapter.get("CurrentHorizontalResolution"):
                resolution = (f"{adapter['CurrentHorizontalResolution']} × "
                              f"{adapter.get('CurrentVerticalResolution')}")
            refresh = adapter.get("CurrentRefreshRate")

            table = InfoTable(self.gpu_container, GPU_ROWS, value_wrap=310)
            table.pack(fill="x", pady=(0, 8))
            table.update_values({
                "Производитель": adapter.get("AdapterCompatibility"),
                "Видеопамять": sysinfo.fmt_bytes(adapter.get("VRAM")),
                "Драйвер": adapter.get("DriverVersion"),
                "Дата драйвера": sysinfo.fmt_date(adapter.get("DriverDate")),
                "Разрешение": resolution,
                "Частота обновления": f"{refresh} Гц" if refresh else "—",
            })

    def _fill_os(self) -> None:
        for child in self.os_container.winfo_children():
            child.destroy()

        data = self.data or {}
        os_info = (data.get("os") or [{}])[0]
        cs = (data.get("cs") or [{}])[0]
        bios = (data.get("bios") or [{}])[0]
        board = (data.get("board") or [{}])[0]
        chipset = (data.get("chipset") or [{}])[0]

        chipset_name = ""
        entries = data.get("chipset") or []
        for entry in entries:
            name = str(entry.get("Name") or "")
            if name and "Thermal" not in name:
                chipset_name = name
                break
        if not chipset_name:
            chipset_name = str(chipset.get("Name") or "")
        chipset_name = re.sub(r"\s*-\s*A[0-9A-Fa-f]{3}$", "", chipset_name)
        chipset_name = re.sub(r"\s+(PMC|LPC|SMBus)( Controller)?$", "", chipset_name)

        tk.Label(self.os_container, text="Система", bg=BG, fg=TEXT,
                 font=("Segoe UI Semibold", 10), anchor="w").pack(
            fill="x", pady=(0, 4))

        table = InfoTable(self.os_container, OS_ROWS, value_wrap=330)
        table.pack(fill="x")

        uptime = data.get("uptime_seconds")
        board_name = " ".join(
            part for part in (str(board.get("Manufacturer") or "").strip(),
                              str(board.get("Product") or "").strip()) if part
        ) or "—"
        bios_text = (f"{bios.get('Manufacturer') or ''} "
                     f"{bios.get('SMBIOSBIOSVersion') or ''} "
                     f"({sysinfo.fmt_date(bios.get('ReleaseDate'))})").strip()

        table.update_values({
            "Система": " ".join(str(os_info.get("Caption") or "—").split()),
            "Версия": os_info.get("Version"),
            "Сборка": os_info.get("BuildNumber"),
            "Архитектура": os_info.get("OSArchitecture"),
            "Установлена": sysinfo.fmt_date(os_info.get("InstallDate")),
            "Время работы": sysinfo.fmt_uptime(uptime) if uptime is not None else "—",
            "Компьютер": cs.get("Name") or os_info.get("CSName"),
            "Пользователь": cs.get("UserName") or os_info.get("RegisteredUser"),
            "Плата": board_name,
            "Чипсет": chipset_name,
            "BIOS": bios_text,
            "SMBIOS": (f"{bios.get('SMBIOSMajorVersion')}.{bios.get('SMBIOSMinorVersion')}"
                       if bios.get("SMBIOSMajorVersion") else "—"),
            "Серийный номер": os_info.get("SerialNumber"),
        })

        disks = data.get("disks") or []
        if disks:
            tk.Label(self.os_container, text="НАКОПИТЕЛИ", bg=BG, fg=MUTED,
                     font=FONT_SMALL, anchor="w").pack(fill="x", pady=(8, 3))
            columns = ("model", "size", "media")
            tree = ttk.Treeview(self.os_container, columns=columns,
                                show="headings", height=min(3, len(disks)))
            for column, title, width in (
                ("model", "Модель", 270), ("size", "Объём", 90),
                ("media", "Тип", 140),
            ):
                tree.heading(column, text=title, anchor="w")
                tree.column(column, width=width, anchor="w",
                            stretch=(column == "model"))
            for disk in disks:
                tree.insert("", "end", values=(
                    str(disk.get("Model") or "—").strip(),
                    sysinfo.fmt_bytes(disk.get("Size")),
                    disk.get("MediaType") or "—",
                ))
            tree.pack(fill="x")

    # -- живая телеметрия ------------------------------------------------

    def _pump_live(self) -> None:
        if self._closing:
            return
        sample = self.live.snapshot()
        if sample:
            self._update_live(sample)
        self.after(400, self._pump_live)

    def _update_live(self, sample: dict) -> None:
        overall = sample.get("overall") or 0.0
        self.metric_cpu.set(f"{overall:.0f} %", overall, load_color(overall))

        mhz = sample.get("mhz")
        nominal = 0
        if self.data:
            cpu = (self.data.get("cpu") or [{}])[0]
            nominal = cpu.get("MaxClockSpeed") or 0
        if mhz:
            self.metric_freq.set(fmt_mhz(mhz), None, TEXT)
        elif nominal:
            self.metric_freq.set(fmt_mhz(nominal), None, MUTED)

        memory = sample.get("memory") or {}
        if memory:
            percent = memory.get("percent", 0.0)
            self.metric_mem.set(
                f"{sysinfo.fmt_bytes(memory.get('used'))} / "
                f"{sysinfo.fmt_bytes(memory.get('total'))}",
                percent,
                load_color(percent * 0.6),
            )

        cores = sample.get("cores") or []
        if cores:
            self.core_bars.set_values(cores)

        if self.data and mhz:
            cpu = (self.data.get("cpu") or [{}])[0]
            bus = cpu.get("ExtClock") or 0
            self.cpu_table.set("Текущая частота", fmt_mhz(mhz))
            self.cpu_table.set(
                "Множитель",
                f"× {mhz / bus:.1f}".replace(".", ",") if bus else "—",
            )

        if self.data:
            self._update_memory_live(memory)

    def _update_memory_live(self, memory: dict) -> None:
        if not memory:
            return
        used = memory.get("used", 0)
        total = memory.get("total", 0)
        percent = memory.get("percent", 0.0)
        self.mem_table.update_values({
            "Используется": sysinfo.fmt_bytes(used),
            "Свободно": sysinfo.fmt_bytes(memory.get("free")),
            "Загрузка": f"{percent:.0f} %",
        })
        self.mem_bar["value"] = percent
        self.mem_bar_text.config(
            text=f"{sysinfo.fmt_bytes(used)} из {sysinfo.fmt_bytes(total)} "
                 f"занято ({percent:.0f} %)"
        )

    # -- бенчмарк --------------------------------------------------------

    def _start_bench(self, threads: int) -> None:
        if self._bench_running:
            return
        self._bench_running = True
        self.btn_bench_one.state(["disabled"])
        self.btn_bench_all.state(["disabled"])
        self.bench_bar.configure(mode="indeterminate")
        self.bench_bar.start(12)
        self.status.config(text="Выполняется тест производительности…")

        def worker() -> None:
            result = sysinfo.benchmark_multi(threads)
            self.after(0, lambda: self._finish_bench(result, threads))

        threading.Thread(target=worker, daemon=True, name="PyCPUZ-Bench").start()

    def _finish_bench(self, result: dict, threads: int) -> None:
        self._bench_running = False
        self.btn_bench_one.state(["!disabled"])
        self.btn_bench_all.state(["!disabled"])
        self.bench_bar.stop()
        self.bench_bar.configure(mode="determinate")
        self.bench_bar["value"] = 0

        key = "Один поток" if threads == 1 else "Все потоки"
        text = (f"{result['seconds']:.2f} с   •   "
                f"{result['mb_per_second']:.0f} МБ/с")
        self.bench_results.set(key, text)

        if threads == 1:
            self._bench_one_result = result
        elif self._bench_one_result:
            scaling = (result["mb_per_second"] / self._bench_one_result["mb_per_second"])
            self.bench_results.set("Ускорение", f"× {scaling:.2f}".replace(".", ","))
        self.status.config(text=f"Тест завершён: {key.lower()}")

    # -- отчёт -----------------------------------------------------------

    def _save_report(self) -> None:
        if not self.data:
            self.status.config(text="Данные ещё не собраны")
            return
        default_name = f"PyCPUZ-{datetime.now():%Y-%m-%d_%H-%M}.txt"
        path = filedialog.asksaveasfilename(
            title="Сохранить отчёт", defaultextension=".txt",
            initialfile=default_name,
            filetypes=[("Текстовый файл", "*.txt"), ("Все файлы", "*.*")],
        )
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8-sig") as handle:
                handle.write(self._report_text())
        except OSError as error:
            self.status.config(text=f"Не удалось сохранить: {error}")
            return
        self.status.config(text=f"Отчёт сохранён: {path}")

    def _report_text(self) -> str:
        data = self.data or {}
        cpu = (data.get("cpu") or [{}])[0]
        details = data.get("cpu_details") or {}
        virt = data.get("virtualization") or {}
        modules = data.get("memory") or []
        os_info = (data.get("os") or [{}])[0]
        board = (data.get("board") or [{}])[0]
        bios = (data.get("bios") or [{}])[0]
        sample = self.live.snapshot()

        lines = [
            "=" * 62,
            f" PyCPU-Z {APP_VERSION} — отчёт о системе",
            f" Сформирован: {datetime.now():%d.%m.%Y %H:%M:%S}",
            "=" * 62,
            "",
            "[ Процессор ]",
            f"  Название:        {' '.join(str(cpu.get('Name') or '').split())}",
            f"  Производитель:   {cpu.get('Manufacturer')}",
            f"  Кодовое имя:     {details.get('codename')}",
            f"  Семейство:       {details.get('family')}, "
            f"модель {details.get('model')}, степпинг {details.get('stepping')}",
            f"  Сокет:           {cpu.get('SocketDesignation')}",
            f"  Ядер / потоков:  {cpu.get('NumberOfCores')} / "
            f"{cpu.get('NumberOfLogicalProcessors')}",
            f"  Частота:         {cpu.get('MaxClockSpeed')} МГц (базовая)",
            f"  Шина:            {cpu.get('ExtClock')} МГц",
            f"  Кэш L2 / L3:     {cpu.get('L2CacheSize')} КБ / {cpu.get('L3CacheSize')} КБ",
            f"  Инструкции:      {', '.join(data.get('features') or [])}",
            f"  Виртуализация:   {virt.get('kind', '')} — {virt.get('status', '')}",
            f"  SLAT / VT-d:     {virt.get('slat', '')}",
            "",
            "[ Память ]",
            f"  Всего:           {sysinfo.fmt_bytes(sum(int(m.get('Capacity') or 0) for m in modules))}",
            f"  Модулей:         {len(modules)}",
        ]
        for module in modules:
            mem_type = sysinfo._MEMORY_TYPES.get(
                int(module.get("SMBIOSMemoryType") or 0), "?")
            lines.append(
                f"    - {str(module.get('DeviceLocator') or '?').strip()}: "
                f"{sysinfo.fmt_bytes(module.get('Capacity'))} {mem_type} "
                f"{module.get('ConfiguredClockSpeed') or module.get('Speed')} МГц, "
                f"{str(module.get('Manufacturer') or '').strip()} "
                f"{str(module.get('PartNumber') or '').strip()}"
            )
        if sample.get("memory"):
            memory = sample["memory"]
            lines.append(
                f"  Занято:          {sysinfo.fmt_bytes(memory.get('used'))} "
                f"({memory.get('percent', 0):.0f} %)"
            )
        if sample.get("mhz"):
            lines.append(f"  Частота сейчас:  {sample['mhz']:.0f} МГц")
            lines.append("")

        lines += [
            "[ Системная плата ]",
            f"  Производитель:   {board.get('Manufacturer')}",
            f"  Модель:          {board.get('Product')}",
            f"  BIOS:            {bios.get('SMBIOSBIOSVersion')} "
            f"({sysinfo.fmt_date(bios.get('ReleaseDate'))})",
            "",
            "[ Операционная система ]",
            f"  Название:        {' '.join(str(os_info.get('Caption') or '').split())}",
            f"  Версия:          {os_info.get('Version')} (сборка {os_info.get('BuildNumber')})",
            f"  Архитектура:     {os_info.get('OSArchitecture')}",
            f"  Установлена:     {sysinfo.fmt_date(os_info.get('InstallDate'))}",
            "",
            "[ Видеоадаптеры ]",
        ]
        for adapter in data.get("gpu") or []:
            lines.append(
                f"  {str(adapter.get('Name') or '').strip()}: "
                f"{sysinfo.fmt_bytes(adapter.get('VRAM'))}, "
                f"драйвер {adapter.get('DriverVersion')}"
            )

        lines += ["", "=" * 62, ""]
        return "\n".join(lines)

    # -- завершение -------------------------------------------------------

    def _on_close(self) -> None:
        self._closing = True
        try:
            self.live.stop()
        finally:
            self.destroy()


def enable_dpi_awareness() -> None:
    if os.name != "nt":
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except (AttributeError, OSError):
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except (AttributeError, OSError):
            pass


def main() -> None:
    enable_dpi_awareness()
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
