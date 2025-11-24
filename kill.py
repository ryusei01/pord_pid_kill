#!/usr/bin/env python3
# 指定ポートのPIDを検索してkillできるGUIツール(Tkinter)

import tkinter as tk
from tkinter import messagebox, ttk
import subprocess
import platform
import re
import shutil


def run_cmd(cmd):
    try:
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        return proc.stdout
    except Exception as e:
        return ""


def find_pids(port):
    port = str(port)
    os_name = platform.system().lower()
    results = []

    if os_name == "windows":
        out = run_cmd(["netstat", "-ano"])
        for line in out.splitlines():
            if f":{port} " in line:
                cols = re.split(r"\s+", line)
                if len(cols) >= 5:
                    pid = cols[-1]
                    results.append(pid)
    else:
        if shutil.which("lsof"):
            out = run_cmd(["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN"])
            for line in out.splitlines()[1:]:
                cols = re.split(r"\s+", line)
                if len(cols) >= 2:
                    pid = cols[1]
                    results.append(pid)
        elif shutil.which("ss"):
            out = run_cmd(["ss", "-ltnp"])
            for line in out.splitlines():
                if f":{port} " in line:
                    m = re.search(r"pid=(\d+)", line)
                    if m:
                        results.append(m.group(1))

    return list(set(results))


def kill_pid(pid):
    os_name = platform.system().lower()
    try:
        if os_name == "windows":
            subprocess.run(["taskkill", "/PID", pid, "/F"], stdout=subprocess.PIPE)
        else:
            subprocess.run(["kill", "-9", pid])
        return True
    except Exception:
        return False


def on_search():
    port = entry_port.get()
    if not port.isdigit():
        messagebox.showerror("エラー", "ポート番号は数字で入力してください")
        return

    tree.delete(*tree.get_children())
    pids = find_pids(port)
    if not pids:
        messagebox.showinfo("結果", "該当プロセスがありません")
        return

    for pid in pids:
        tree.insert("", tk.END, values=(pid,))


def on_kill():
    selected = tree.selection()
    if not selected:
        messagebox.showwarning("警告", "PIDを選択してください")
        return

    pid = tree.item(selected[0])['values'][0]
    kill_process(pid)


def on_kill_pid():
    pid = entry_pid.get().strip()
    if not pid.isdigit():
        messagebox.showerror("エラー", "PIDは数字で入力してください")
        return
    kill_process(pid)


def kill_process(pid, refresh=True):
    if messagebox.askyesno("確認", f"PID {pid} を終了しますか?"):
        if kill_pid(pid):
            messagebox.showinfo("完了", "プロセスを終了しました")
            if refresh and entry_port.get().isdigit():
                on_search()
        else:
            messagebox.showerror("失敗", "プロセスの終了に失敗しました")


# --- GUI構築 ---
root = tk.Tk()
root.title("Port Killer GUI")
root.geometry("400x350")

# ポート検索フレーム
frame = tk.Frame(root)
frame.pack(pady=10)

lbl = tk.Label(frame, text="ポート番号:")
lbl.grid(row=0, column=0, padx=5)

entry_port = tk.Entry(frame)
entry_port.grid(row=0, column=1, padx=5)

btn_search = tk.Button(frame, text="検索", command=on_search)
btn_search.grid(row=0, column=2, padx=5)

# PID一覧表示
col = ("PID",)
tree = ttk.Treeview(root, columns=col, show='headings', height=10)
tree.heading("PID", text="PID")
tree.pack(pady=5)

btn_kill = tk.Button(root, text="選択したPIDをKill", command=on_kill)
btn_kill.pack(pady=5)

# PID直接Kill UI
pid_frame = tk.Frame(root)
pid_frame.pack(pady=5)

pid_lbl = tk.Label(pid_frame, text="PID:")
pid_lbl.grid(row=0, column=0, padx=5)

entry_pid = tk.Entry(pid_frame)
entry_pid.grid(row=0, column=1, padx=5)

btn_pid_kill = tk.Button(pid_frame, text="PIDをKill", command=on_kill_pid)
btn_pid_kill.grid(row=0, column=2, padx=5)

root.mainloop()
