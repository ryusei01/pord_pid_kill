#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""開発でよく使うポートの使用状況を見て、プロセスを終了する。

起動方法:
  起動.vbs をダブルクリックする（コンソールは出ません）
  または pythonw kill.py / pyw -3 kill.py
"""

from __future__ import annotations

import csv
import ctypes
import locale
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
import tkinter.font as tkfont
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from tkinter import messagebox, ttk

APP_TITLE = "ポート終了ツール"
APP_DIR = Path(__file__).resolve().parent
APP_PATH = Path(__file__).resolve()
ERROR_LOG = APP_DIR / "kill_error.log"
MAX_ROWS = 200

# 起動直後に出す主要ポート。(ポート, 用途)
DEV_PORTS: list[tuple[int, str]] = [
    (1433, "SQL Server"),
    (3000, "React / Next.js / Rails"),
    (3001, "開発サーバ (予備)"),
    (3306, "MySQL"),
    (4000, "開発サーバ"),
    (4173, "Vite preview"),
    (4200, "Angular"),
    (4321, "Astro"),
    (5000, "Flask / ASP.NET"),
    (5001, "ASP.NET"),
    (5173, "Vite"),
    (5174, "Vite (予備)"),
    (5432, "PostgreSQL"),
    (5500, "Live Server"),
    (6379, "Redis"),
    (7860, "Gradio"),
    (8000, "Django / FastAPI"),
    (8080, "開発サーバ"),
    (8081, "開発サーバ (予備)"),
    (8501, "Streamlit"),
    (8888, "Jupyter"),
    (9000, "開発サーバ"),
    (9229, "Node デバッガ"),
    (11434, "Ollama"),
    (27017, "MongoDB"),
]
DEV_PORT_LABELS = dict(DEV_PORTS)

# Windows の常駐待ち受け。主要ポート以外の一覧には出さない。
SKIP_EXTRA_PORTS = {
    135, 137, 138, 139, 445, 593, 2179, 2869, 3389,
    5040, 5357, 5985, 7680, 47001,
}

# 主要ポート以外で、開発サーバらしいプロセスだけ追加表示する。
EXTRA_DEV_NAMES = {
    "node",
    "nodejs",
    "python",
    "pythonw",
    "java",
    "javaw",
    "ruby",
    "php",
    "php-cgi",
    "perl",
    "deno",
    "bun",
    "dotnet",
    "uvicorn",
    "gunicorn",
    "nginx",
    "httpd",
    "postgres",
    "mysqld",
    "mariadbd",
    "redis-server",
    "mongod",
    "sqlservr",
    "docker",
    "beam.smp",
}

BLOCKED_PIDS = {"0", "4"}
BLOCKED_NAMES = {
    "system",
    "system idle process",
    "idle",
    "registry",
    "secure system",
    "memory compression",
    "smss.exe",
    "csrss.exe",
    "wininit.exe",
    "services.exe",
    "lsass.exe",
    "lsaiso.exe",
    "winlogon.exe",
    "fontdrvhost.exe",
    "dwm.exe",
}

COLUMNS = ("port", "usage", "pid", "name", "state", "address")
COLUMN_HEADINGS = {
    "port": "ポート",
    "usage": "用途",
    "pid": "PID",
    "name": "プロセス",
    "state": "状態",
    "address": "アドレス",
}
COLUMN_WIDTHS = {
    "port": 80,
    "usage": 170,
    "pid": 80,
    "name": 160,
    "state": 110,
    "address": 280,
}
COLUMN_ANCHORS = {
    "port": "center",
    "usage": "w",
    "pid": "center",
    "name": "w",
    "state": "center",
    "address": "w",
}

_mutex_handle = None


class CommandError(RuntimeError):
    pass


@dataclass(frozen=True)
class Conn:
    proto: str
    local: str
    port: int
    state: str
    pid: str


@dataclass
class Row:
    port: int
    usage: str
    pid: str
    name: str
    state: str
    address: str
    kind: str  # busy / extra / free

    def values(self) -> tuple[str, str, str, str, str, str]:
        return (
            str(self.port),
            self.usage,
            self.pid,
            self.name,
            self.state,
            self.address,
        )


def enable_dpi() -> None:
    if os.name != "nt":
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def focus_existing_window() -> bool:
    if os.name != "nt":
        return False
    user32 = ctypes.windll.user32
    user32.FindWindowW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p]
    user32.FindWindowW.restype = ctypes.c_void_p
    user32.ShowWindow.argtypes = [ctypes.c_void_p, ctypes.c_int]
    user32.SetForegroundWindow.argtypes = [ctypes.c_void_p]
    user32.SetForegroundWindow.restype = ctypes.c_bool
    hwnd = user32.FindWindowW(None, APP_TITLE)
    if not hwnd:
        return False
    user32.ShowWindow(hwnd, 9)  # SW_RESTORE
    user32.SetForegroundWindow(hwnd)
    return True


def claim_single_instance() -> bool:
    """同じツールを二重に開かない。すでに開いていればそのウィンドウを前面へ出す。"""
    global _mutex_handle
    if os.name != "nt":
        return True
    kernel32 = ctypes.windll.kernel32
    kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    kernel32.SetLastError.argtypes = [ctypes.c_ulong]
    kernel32.GetLastError.restype = ctypes.c_ulong
    kernel32.SetLastError(0)
    _mutex_handle = kernel32.CreateMutexW(None, False, "Local\\PidPortKill.DevPorts")
    if kernel32.GetLastError() != 183:  # ERROR_ALREADY_EXISTS
        return True
    if not focus_existing_window():
        root = tk.Tk()
        root.withdraw()
        messagebox.showinfo(APP_TITLE, "すでに起動しています。")
        root.destroy()
    return False


def is_admin() -> bool:
    if os.name != "nt":
        return False
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def relaunch_as_admin() -> bool:
    exe = Path(sys.executable)
    if exe.name.lower() == "python.exe":
        pythonw = exe.with_name("pythonw.exe")
        if pythonw.exists():
            exe = pythonw
    params = subprocess.list2cmdline([str(APP_PATH)])
    shell32 = ctypes.windll.shell32
    shell32.ShellExecuteW.argtypes = [
        ctypes.c_void_p,
        ctypes.c_wchar_p,
        ctypes.c_wchar_p,
        ctypes.c_wchar_p,
        ctypes.c_wchar_p,
        ctypes.c_int,
    ]
    shell32.ShellExecuteW.restype = ctypes.c_void_p
    rc = shell32.ShellExecuteW(None, "runas", str(exe), params, str(APP_DIR), 1)
    return int(rc or 0) > 32


def normalize_digits(text: str) -> str:
    return text.strip().translate(str.maketrans("０１２３４５６７８９", "0123456789"))


def parse_port_text(text: str) -> int | None:
    text = normalize_digits(text)
    if not text.isdigit():
        return None
    value = int(text)
    if 1 <= value <= 65535:
        return value
    return None


def parse_local_port(addr: str) -> int | None:
    addr = addr.strip()
    if addr.startswith("["):
        end = addr.find("]")
        if end == -1 or end + 1 >= len(addr) or addr[end + 1] != ":":
            return None
        port_text = addr[end + 2 :]
    elif ":" in addr:
        port_text = addr.rsplit(":", 1)[1]
    else:
        return None
    if not port_text.isdigit():
        return None
    port = int(port_text)
    if 0 <= port <= 65535:
        return port
    return None


def parse_netstat(text: str) -> list[Conn]:
    found: list[Conn] = []
    seen: set[Conn] = set()
    for raw in text.splitlines():
        parts = raw.split()
        if len(parts) < 4:
            continue
        proto = parts[0].upper()
        if proto not in {"TCP", "UDP"}:
            continue
        port = parse_local_port(parts[1])
        if port is None or not parts[-1].isdigit():
            continue
        if proto == "TCP":
            if len(parts) < 5:
                continue
            state = parts[-2].upper()
        else:
            state = "UDP"
        conn = Conn(proto, parts[1], port, state, parts[-1])
        if conn not in seen:
            seen.add(conn)
            found.append(conn)
    return found


def parse_tasklist(text: str) -> dict[str, str]:
    names: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("INFO:"):
            continue
        try:
            cols = next(csv.reader([line]))
        except csv.Error:
            continue
        if len(cols) >= 2 and cols[1].isdigit():
            names[cols[1]] = cols[0]
    return names


def run_cmd(args: list[str], timeout: float = 25) -> str:
    encoding = locale.getpreferredencoding(False) or "utf-8"
    kwargs: dict = {
        "args": args,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "stdin": subprocess.DEVNULL,
        "timeout": timeout,
    }
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        proc = subprocess.run(**kwargs)
    except subprocess.TimeoutExpired as exc:
        raise CommandError("一覧の取得がタイムアウトしました。もう一度更新してください。") from exc
    except OSError as exc:
        raise CommandError(f"コマンドを実行できませんでした: {exc}") from exc
    out = proc.stdout.decode(encoding, errors="replace")
    err = proc.stderr.decode(encoding, errors="replace")
    if proc.returncode != 0 and not out.strip():
        detail = err.strip() or " ".join(args)
        raise CommandError(detail)
    return out


def load_snapshot() -> tuple[list[Conn], dict[str, str]]:
    if os.name == "nt":
        with ThreadPoolExecutor(max_workers=2) as pool:
            net_future = pool.submit(run_cmd, ["netstat", "-ano"])
            task_future = pool.submit(run_cmd, ["tasklist", "/FO", "CSV", "/NH"])
            net_text = net_future.result()
            try:
                task_text = task_future.result()
            except CommandError:
                task_text = ""
        return parse_netstat(net_text), parse_tasklist(task_text)
    return load_snapshot_posix()


def load_snapshot_posix() -> tuple[list[Conn], dict[str, str]]:
    conns: list[Conn] = []
    names: dict[str, str] = {}
    if shutil_which("lsof"):
        text = run_cmd(["lsof", "-nP", "-iTCP", "-sTCP:LISTEN"])
        for line in text.splitlines()[1:]:
            parts = line.split()
            if len(parts) < 9:
                continue
            port = parse_local_port(parts[-1])
            pid = parts[1]
            if port is None or not pid.isdigit():
                continue
            conns.append(Conn("TCP", parts[-1], port, "LISTENING", pid))
            names[pid] = parts[0]
        return conns, names
    if shutil_which("ss"):
        text = run_cmd(["ss", "-ltnp"])
        for line in text.splitlines():
            port = None
            for token in line.split():
                maybe = parse_local_port(token)
                if maybe is not None:
                    port = maybe
                    local = token
                    break
            if port is None:
                continue
            pid = ""
            name = ""
            marker = line.find("pid=")
            if marker != -1:
                pid_text = line[marker + 4 :].split(",", 1)[0].split(")", 1)[0]
                if pid_text.isdigit():
                    pid = pid_text
            if not pid:
                continue
            conns.append(Conn("TCP", local, port, "LISTENING", pid))
            names.setdefault(pid, name or "不明")
    return conns, names


def shutil_which(name: str) -> str | None:
    import shutil

    return shutil.which(name)


def is_occupying(conn: Conn) -> bool:
    if conn.proto == "UDP":
        return True
    return conn.state == "LISTENING"


def groups_for_port(
    conns: list[Conn], port: int, occupying_only: bool
) -> dict[tuple[str, str], list[str]]:
    grouped: dict[tuple[str, str], list[str]] = {}
    for conn in conns:
        if conn.port != port:
            continue
        if occupying_only and not is_occupying(conn):
            continue
        addrs = grouped.setdefault((conn.pid, conn.state), [])
        if conn.local not in addrs:
            addrs.append(conn.local)
    return grouped


def rows_from_groups(
    port: int,
    usage: str,
    kind: str,
    groups: dict[tuple[str, str], list[str]],
    names: dict[str, str],
) -> list[Row]:
    rows: list[Row] = []
    for (pid, state), addrs in groups.items():
        rows.append(
            Row(
                port=port,
                usage=usage,
                pid=pid,
                name=names.get(pid) or "不明",
                state=state,
                address=", ".join(addrs),
                kind=kind,
            )
        )
    return rows


def is_extra_dev_server(name: str) -> bool:
    base = Path(name).name.casefold()
    if base.endswith(".exe"):
        base = base[:-4]
    if base in EXTRA_DEV_NAMES:
        return True
    return base.startswith(("python", "node", "ruby", "php-cgi", "dotnet"))


def build_dev_rows(conns: list[Conn], names: dict[str, str]) -> list[Row]:
    busy: list[Row] = []
    free: list[Row] = []
    for port, label in DEV_PORTS:
        groups = groups_for_port(conns, port, occupying_only=True)
        if groups:
            busy.extend(rows_from_groups(port, label, "busy", groups, names))
        else:
            free.append(Row(port, label, "", "", "未使用", "", "free"))

    dev_ports = set(DEV_PORT_LABELS)
    extra_ports = sorted(
        {
            conn.port
            for conn in conns
            if conn.proto == "TCP"
            and conn.state == "LISTENING"
            and conn.port not in dev_ports
            and conn.port not in SKIP_EXTRA_PORTS
            and 1024 <= conn.port < 49152
        }
    )
    extras: list[Row] = []
    for port in extra_ports:
        groups = groups_for_port(conns, port, occupying_only=True)
        for row in rows_from_groups(port, "その他の開発サーバ", "extra", groups, names):
            if is_extra_dev_server(row.name):
                extras.append(row)
    return busy + extras + free


def build_port_rows(conns: list[Conn], names: dict[str, str], port: int) -> list[Row]:
    label = DEV_PORT_LABELS.get(port, "指定ポート")
    groups = groups_for_port(conns, port, occupying_only=False)
    if not groups:
        return [Row(port, label, "", "", "未使用", "", "free")]
    rows = rows_from_groups(port, label, "busy", groups, names)
    if len(rows) > MAX_ROWS:
        return rows[:MAX_ROWS]
    return rows


def block_reason(pid: str, name: str) -> str | None:
    if pid in BLOCKED_PIDS or name.casefold() in BLOCKED_NAMES:
        return f"{name or 'システム'} (PID {pid}) は Windows のシステムプロセスなので終了しません。"
    return None


def kill_pid(pid: str) -> tuple[bool, str, bool]:
    """戻り値は (成功したか, メッセージ, 権限不足か)。"""
    if os.name == "nt":
        encoding = locale.getpreferredencoding(False) or "utf-8"
        kwargs: dict = {
            "args": ["taskkill", "/PID", pid, "/F", "/T"],
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "stdin": subprocess.DEVNULL,
            "timeout": 20,
            "creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0),
        }
        try:
            proc = subprocess.run(**kwargs)
        except subprocess.TimeoutExpired:
            return False, "終了処理がタイムアウトしました。", False
        except OSError as exc:
            return False, str(exc), False
        blob = (
            proc.stdout.decode(encoding, errors="replace")
            + proc.stderr.decode(encoding, errors="replace")
        ).strip()
        lowered = blob.casefold()
        if proc.returncode == 0 or "見つかりません" in blob or "not found" in lowered:
            return True, "", False
        denied = "アクセスが拒否" in blob or "access is denied" in lowered
        return False, blob or "終了できませんでした。", denied

    try:
        proc = subprocess.run(
            ["kill", "-9", pid],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc), False
    if proc.returncode == 0:
        return True, "", False
    return False, (proc.stderr or proc.stdout or "").strip(), False


def enable_row_colors(style: ttk.Style) -> None:
    def fixed_map(option: str):
        return [
            item
            for item in style.map("Treeview", query_opt=option)
            if item[:2] != ("!disabled", "!selected")
        ]

    style.map(
        "Treeview",
        foreground=fixed_map("foreground"),
        background=fixed_map("background"),
    )


class App:
    def __init__(self, autostart: bool = True) -> None:
        self.root = tk.Tk()
        self.root.title(APP_TITLE)
        self.root.minsize(760, 460)
        self.alive = True
        self.admin = is_admin()
        self.generation = 0
        self.mode = "dev"
        self.ports: list[int] = []
        self.all_rows: list[Row] = []
        self.sort_column = ""
        self.sort_reverse = False
        self.killing = False
        self.results: queue.Queue = queue.Queue()

        self.port_var = tk.StringVar()
        self.pid_var = tk.StringVar()
        self.show_free = tk.BooleanVar(value=True)
        self.status_var = tk.StringVar(value="主要ポートを確認しています...")

        self._apply_style()
        self._build()
        self._place_window()
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.bind("<F5>", lambda _event: self.refresh())
        self.root.after(80, self.poll)
        if autostart:
            self.show_dev_ports()

    def _apply_style(self) -> None:
        families = set(tkfont.families(self.root))
        font_name = "TkDefaultFont"
        for candidate in ("Yu Gothic UI", "Meiryo UI", "Yu Gothic", "Meiryo", "Segoe UI"):
            if candidate in families:
                font_name = candidate
                break
        self.font = (font_name, 10)
        self.root.option_add("*Font", self.font)
        style = ttk.Style(self.root)
        if "vista" in style.theme_names():
            style.theme_use("vista")
        style.configure("Treeview", font=self.font, rowheight=26)
        style.configure("Treeview.Heading", font=self.font)
        style.configure("TButton", padding=(10, 4))
        enable_row_colors(style)

    def _build(self) -> None:
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(1, weight=1)

        top = ttk.Frame(self.root, padding=(12, 12, 12, 4))
        top.grid(row=0, column=0, sticky="ew")
        top.columnconfigure(4, weight=1)

        ttk.Button(top, text="主要ポートを更新", command=self.show_dev_ports).grid(
            row=0, column=0, padx=(0, 12)
        )
        ttk.Label(top, text="ポート番号").grid(row=0, column=1, padx=(0, 4))
        self.port_entry = ttk.Entry(top, textvariable=self.port_var, width=8)
        self.port_entry.grid(row=0, column=2)
        self.port_entry.bind("<Return>", self.on_port_enter)
        ttk.Button(top, text="検索", command=self.search_port).grid(row=0, column=3, padx=(6, 0))
        ttk.Checkbutton(
            top,
            text="未使用も表示",
            variable=self.show_free,
            command=self.render,
        ).grid(row=0, column=5, padx=(12, 0))

        hint = ttk.Label(
            top,
            wraplength=920,
            text="よく使う開発ポートを最初から表示します。使用中が上、未使用が下です。node や python など、一覧にない開発サーバも出します。",
        )
        hint.grid(row=1, column=0, columnspan=6, sticky="w", pady=(8, 0))

        table = ttk.Frame(self.root, padding=(12, 8, 12, 4))
        table.grid(row=1, column=0, sticky="nsew")
        table.columnconfigure(0, weight=1)
        table.rowconfigure(0, weight=1)

        self.tree = ttk.Treeview(
            table,
            columns=COLUMNS,
            show="headings",
            selectmode="extended",
        )
        for column in COLUMNS:
            self.tree.heading(
                column,
                text=COLUMN_HEADINGS[column],
                command=lambda name=column: self.on_sort(name),
            )
            self.tree.column(
                column,
                width=COLUMN_WIDTHS[column],
                minwidth=60,
                anchor=COLUMN_ANCHORS[column],
                stretch=column == "address",
            )
        self.tree.tag_configure("busy", background="#fff1dc")
        self.tree.tag_configure("extra", background="#e7f1fb")
        self.tree.tag_configure("free", foreground="#8a8a8a")
        self.tree.tag_configure("pending", foreground="#8a8a8a")
        self.tree.grid(row=0, column=0, sticky="nsew")
        ybar = ttk.Scrollbar(table, orient="vertical", command=self.tree.yview)
        ybar.grid(row=0, column=1, sticky="ns")
        xbar = ttk.Scrollbar(table, orient="horizontal", command=self.tree.xview)
        xbar.grid(row=1, column=0, sticky="ew")
        self.tree.configure(yscrollcommand=ybar.set, xscrollcommand=xbar.set)
        self.tree.bind("<Double-1>", self.on_double_click)
        self.tree.bind("<Delete>", lambda _event: self.kill_selected())
        self.tree.bind("<Control-c>", lambda _event: self.copy_pid())
        self.tree.bind("<Button-3>", self.on_right_click)

        self.menu = tk.Menu(self.root, tearoff=0)
        self.menu.add_command(label="このプロセスを終了", command=self.kill_selected)
        self.menu.add_command(label="PIDをコピー", command=self.copy_pid)
        self.menu.add_separator()
        self.menu.add_command(label="このポートだけ再検索", command=self.search_selected_port)

        bottom = ttk.Frame(self.root, padding=(12, 4, 12, 8))
        bottom.grid(row=2, column=0, sticky="ew")
        bottom.columnconfigure(4, weight=1)
        self.kill_button = ttk.Button(bottom, text="選択したプロセスを終了", command=self.kill_selected)
        self.kill_button.grid(row=0, column=0, padx=(0, 16))
        ttk.Label(bottom, text="PID").grid(row=0, column=1, padx=(0, 4))
        self.pid_entry = ttk.Entry(bottom, textvariable=self.pid_var, width=10)
        self.pid_entry.grid(row=0, column=2)
        self.pid_entry.bind("<Return>", lambda _event: self.kill_typed_pid())
        self.pid_button = ttk.Button(bottom, text="このPIDを終了", command=self.kill_typed_pid)
        self.pid_button.grid(row=0, column=3, padx=(6, 0))
        if os.name == "nt":
            admin_text = "管理者として実行中" if self.admin else "管理者として再起動"
            self.admin_button = ttk.Button(
                bottom,
                text=admin_text,
                command=self.relaunch,
                state="disabled" if self.admin else "normal",
            )
            self.admin_button.grid(row=0, column=5)

        status = ttk.Label(self.root, textvariable=self.status_var, anchor="w", padding=(12, 6))
        status.grid(row=3, column=0, sticky="ew")
        self.port_entry.focus_set()

    def _place_window(self) -> None:
        width, height = 1000, 720
        self.root.update_idletasks()
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        x = max(0, (screen_w - width) // 2)
        y = max(0, (screen_h - height) // 2)
        self.root.geometry(f"{width}x{height}+{x}+{y}")

    def close(self) -> None:
        self.alive = False
        self.root.destroy()

    def set_status(self, text: str) -> None:
        self.status_var.set(text)

    def show_dev_ports(self) -> None:
        self.mode = "dev"
        self.ports = []
        self.sort_column = ""
        self.fill_placeholders()
        self.request_scan()

    def refresh(self) -> None:
        self.request_scan()

    def search_port(self) -> None:
        port = parse_port_text(self.port_var.get())
        if port is None:
            messagebox.showerror(
                APP_TITLE,
                "ポート番号は 1 から 65535 の数字で入力してください。",
            )
            self.port_entry.focus_set()
            self.port_entry.selection_range(0, "end")
            return
        self.mode = "port"
        self.ports = [port]
        self.port_var.set(str(port))
        self.sort_column = ""
        self.set_status(f"ポート {port} を確認しています...")
        self.request_scan()

    def on_port_enter(self, _event=None):
        if self.port_var.get().strip():
            self.search_port()
        else:
            self.show_dev_ports()
        return "break"

    def request_scan(self) -> None:
        self.generation += 1
        generation = self.generation
        mode = self.mode
        ports = list(self.ports)
        if mode == "dev":
            self.set_status("主要ポートを確認しています...")
        threading.Thread(
            target=self._scan_worker,
            args=(generation, mode, ports),
            daemon=True,
        ).start()

    def _scan_worker(self, generation: int, mode: str, ports: list[int]) -> None:
        try:
            conns, names = load_snapshot()
            if mode == "port" and ports:
                rows = build_port_rows(conns, names, ports[0])
            else:
                rows = build_dev_rows(conns, names)
            self.results.put(("rows", generation, rows))
        except Exception as exc:
            self.results.put(("error", generation, str(exc)))

    def fill_placeholders(self) -> None:
        self.tree.delete(*self.tree.get_children())
        for port, label in DEV_PORTS:
            self.tree.insert(
                "",
                "end",
                values=(str(port), label, "", "", "確認中", ""),
                tags=("pending",),
            )

    def render(self) -> None:
        if not self.all_rows:
            return
        rows = list(self.all_rows)
        if not self.show_free.get():
            rows = [row for row in rows if row.pid]
        if self.sort_column:
            rows.sort(key=lambda row: self._sort_value(row), reverse=self.sort_reverse)
        self.tree.delete(*self.tree.get_children())
        for row in rows:
            self.tree.insert("", "end", values=row.values(), tags=(row.kind,))
        self.update_status_count()

    def update_status_count(self) -> None:
        busy_ports = {row.port for row in self.all_rows if row.kind == "busy"}
        extra_ports = {row.port for row in self.all_rows if row.kind == "extra"}
        if self.mode == "port" and self.ports:
            port = self.ports[0]
            if any(row.pid for row in self.all_rows):
                count = len({row.pid for row in self.all_rows if row.pid})
                text = f"ポート {port} は {count} プロセスが使用中です。"
            else:
                text = f"ポート {port} は空いています。"
        else:
            text = (
                f"使用中 {len(busy_ports) + len(extra_ports)} ポート"
                f"（主要 {len(busy_ports)}、その他 {len(extra_ports)}）。"
                " F5 で更新、ダブルクリックか Delete で終了できます。"
            )
        self.set_status(text)

    def _sort_value(self, row: Row):
        if self.sort_column == "port":
            return row.port
        if self.sort_column == "pid":
            return int(row.pid) if row.pid.isdigit() else -1
        return getattr(row, self.sort_column).casefold()

    def on_sort(self, column: str) -> None:
        if not self.all_rows:
            return
        if self.sort_column == column:
            self.sort_reverse = not self.sort_reverse
        else:
            self.sort_column = column
            self.sort_reverse = False
        self.render()

    def poll(self) -> None:
        if not self.alive:
            return
        try:
            while True:
                kind, generation, payload = self.results.get_nowait()
                if generation != self.generation and kind in {"rows", "error"}:
                    continue
                if kind == "rows":
                    self.all_rows = payload
                    self.killing = False
                    self.render()
                elif kind == "error":
                    self.killing = False
                    self.tree.delete(*self.tree.get_children())
                    self.set_status("一覧を取得できませんでした。F5 で再試行できます。")
                    messagebox.showerror(APP_TITLE, payload)
                elif kind == "killed":
                    self._finish_kill(payload)
        except queue.Empty:
            pass
        if self.alive:
            self.root.after(80, self.poll)

    def selected_targets(self) -> list[tuple[str, str, str]]:
        targets: list[tuple[str, str, str]] = []
        seen: set[str] = set()
        for item in self.tree.selection():
            values = self.tree.item(item, "values")
            if len(values) < 4:
                continue
            pid = str(values[2]).strip()
            if not pid.isdigit() or pid in seen:
                continue
            seen.add(pid)
            targets.append((pid, str(values[3]), str(values[0])))
        return targets

    def on_double_click(self, event) -> None:
        if self.tree.identify_region(event.x, event.y) != "cell":
            return
        item = self.tree.identify_row(event.y)
        if not item:
            return
        values = self.tree.item(item, "values")
        if len(values) < 3 or not str(values[2]).strip():
            port = values[0] if values else ""
            self.set_status(f"ポート {port} は空いています。")
            return
        self.kill_selected()

    def on_right_click(self, event) -> None:
        item = self.tree.identify_row(event.y)
        if not item:
            return
        if item not in self.tree.selection():
            self.tree.selection_set(item)
        try:
            self.menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.menu.grab_release()

    def copy_pid(self) -> None:
        targets = self.selected_targets()
        if not targets:
            self.set_status("コピーする行を選択してください。")
            return
        text = "\n".join(pid for pid, _name, _port in targets)
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.set_status("PIDをコピーしました。")

    def search_selected_port(self) -> None:
        selected = self.tree.selection()
        if not selected:
            return
        values = self.tree.item(selected[0], "values")
        if not values:
            return
        self.port_var.set(str(values[0]))
        self.search_port()

    def kill_selected(self) -> None:
        targets = self.selected_targets()
        if not targets:
            self.set_status("終了するプロセスを選択してください。")
            return
        self.confirm_and_kill(targets)

    def kill_typed_pid(self) -> None:
        pid = normalize_digits(self.pid_var.get())
        if not pid.isdigit() or int(pid) <= 0:
            messagebox.showerror(APP_TITLE, "PIDは 1 以上の数字で入力してください。")
            self.pid_entry.focus_set()
            return
        name = "不明"
        port = "-"
        for row in self.all_rows:
            if row.pid == pid:
                name = row.name
                port = str(row.port)
                break
        self.pid_var.set(pid)
        self.confirm_and_kill([(pid, name, port)])

    def confirm_and_kill(self, targets: list[tuple[str, str, str]]) -> None:
        if self.killing:
            self.set_status("終了処理の途中です。")
            return
        allowed: list[tuple[str, str, str]] = []
        blocked: list[str] = []
        for pid, name, port in targets:
            reason = block_reason(pid, name)
            if reason:
                blocked.append(reason)
            else:
                allowed.append((pid, name, port))
        if blocked:
            messagebox.showwarning(APP_TITLE, "\n".join(blocked))
        if not allowed:
            return
        lines = [f"ポート {port}    {name}    (PID {pid})" for pid, name, port in allowed]
        message = "次のプロセスを強制終了します。\n子プロセスも一緒に終了します。\n\n" + "\n".join(lines[:12])
        if len(lines) > 12:
            message += f"\n... ほか {len(lines) - 12} 件"
        if not messagebox.askyesno(APP_TITLE, message):
            return
        self.killing = True
        self.generation += 1
        generation = self.generation
        pids = [pid for pid, _name, _port in allowed]
        self.set_status("終了しています...")
        threading.Thread(
            target=self._kill_worker,
            args=(generation, pids),
            daemon=True,
        ).start()

    def _kill_worker(self, generation: int, pids: list[str]) -> None:
        ok = 0
        errors: list[str] = []
        denied = False
        for pid in pids:
            success, detail, access_denied = kill_pid(pid)
            if success:
                ok += 1
            else:
                errors.append(f"PID {pid}: {detail}")
                denied = denied or access_denied
        self.results.put(("killed", generation, {"ok": ok, "errors": errors, "denied": denied}))

    def _finish_kill(self, payload: dict) -> None:
        self.killing = False
        errors = payload["errors"]
        relaunched = False
        if errors:
            text = "終了できないプロセスがありました。\n\n" + "\n".join(errors[:8])
            if payload["denied"] and os.name == "nt" and not self.admin:
                if messagebox.askyesno(
                    APP_TITLE,
                    text + "\n\n権限が足りない可能性があります。管理者として再起動しますか？",
                ):
                    relaunched = self.relaunch()
            else:
                messagebox.showerror(APP_TITLE, text)
        if not relaunched and self.alive:
            self.request_scan()

    def relaunch(self) -> bool:
        if relaunch_as_admin():
            self.close()
            return True
        messagebox.showerror(
            APP_TITLE,
            "管理者としての起動をキャンセルしたか、起動できませんでした。",
        )
        return False


def self_test() -> None:
    sample = """
  TCP    0.0.0.0:3000           0.0.0.0:0              LISTENING       111
  TCP    127.0.0.1:5173         0.0.0.0:0              LISTENING       222
  TCP    [::]:8080              [::]:0                 LISTENING       333
  TCP    [::]:8080              [::]:0                 LISTENING       333
  TCP    192.168.0.5:30000      1.2.3.4:443            ESTABLISHED     444
  UDP    0.0.0.0:6379           *:*                                    555
  TCP    [::]:8765              [::]:0                 LISTENING       666
  TCP    0.0.0.0:445            0.0.0.0:0              LISTENING       4
  TCP    127.0.0.1:17477        0.0.0.0:0              LISTENING       777
"""
    conns = parse_netstat(sample)
    assert any(conn.port == 30000 for conn in conns)
    assert not any(conn.port == 3000 and conn.pid == "444" for conn in conns)
    names = {
        "111": "node.exe",
        "222": "node.exe",
        "333": "python.exe",
        "555": "redis-server.exe",
        "666": "python.exe",
        "4": "System",
        "777": "Cursor.exe",
    }
    rows = build_dev_rows(conns, names)
    busy = {row.port for row in rows if row.kind == "busy"}
    extra = {row.port for row in rows if row.kind == "extra"}
    free = {row.port for row in rows if row.kind == "free"}
    assert busy == {3000, 5173, 8080, 6379}
    assert 8765 in extra
    assert 445 not in extra
    assert 17477 not in extra
    assert 30000 not in {row.port for row in rows}
    assert 5000 in free
    ipv6 = [row for row in rows if row.port == 8080]
    assert len(ipv6) == 1
    assert "[::]:8080" in ipv6[0].address
    assert block_reason("4", "System")
    assert parse_port_text("３０") == 30
    assert parse_port_text("0") is None
    assert parse_port_text("65536") is None
    print("self_test ok")


def main() -> None:
    if "--self-test" in sys.argv:
        self_test()
        return
    enable_dpi()
    if not claim_single_instance():
        return
    try:
        App().root.mainloop()
    except Exception:
        import traceback

        detail = traceback.format_exc()
        try:
            ERROR_LOG.write_text(detail, encoding="utf-8")
        except OSError:
            pass
        try:
            root = tk.Tk()
            root.withdraw()
            messagebox.showerror(APP_TITLE, f"起動に失敗しました。\n{ERROR_LOG}")
            root.destroy()
        except Exception:
            pass


if __name__ == "__main__":
    main()
