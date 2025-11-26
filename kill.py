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


def get_process_info(pid):
    """PIDからプロセス情報を取得（存在確認も含む）"""
    os_name = platform.system().lower()
    try:
        if os_name == "windows":
            out = run_cmd(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"])
            if out and "INFO:" not in out:  # "INFO: 指定した条件を満たすタスクは実行されていません。"を除外
                # CSV形式: "プロセス名","PID","セッション名",...
                match = re.match(r'"([^"]+)"', out)
                if match:
                    return match.group(1), True  # (プロセス名, 存在する)
            return None, False  # プロセスが存在しない
        else:
            out = run_cmd(["ps", "-p", pid, "-o", "comm="])
            if out and out.strip():
                return out.strip(), True
            return None, False
    except:
        pass
    return None, False


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
                    state = cols[3] if len(cols) >= 4 else ""

                    # プロセス名を取得（存在しない場合は「不明」）
                    process_name, exists = get_process_info(pid)
                    if not exists:
                        process_name = "不明"
                    results.append((pid, process_name, state))
    else:
        if shutil.which("lsof"):
            out = run_cmd(["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN"])
            for line in out.splitlines()[1:]:
                cols = re.split(r"\s+", line)
                if len(cols) >= 2:
                    pid = cols[1]
                    process_name, exists = get_process_info(pid)
                    if not exists:
                        process_name = "不明"
                    results.append((pid, process_name, "LISTEN"))
        elif shutil.which("ss"):
            out = run_cmd(["ss", "-ltnp"])
            for line in out.splitlines():
                if f":{port} " in line:
                    m = re.search(r"pid=(\d+)", line)
                    if m:
                        pid = m.group(1)
                        process_name, exists = get_process_info(pid)
                        if not exists:
                            process_name = "不明"
                        results.append((pid, process_name, "LISTEN"))

    # 重複削除(PIDベース)
    seen = set()
    unique_results = []
    for pid, name, state in results:
        if pid not in seen:
            seen.add(pid)
            unique_results.append((pid, name, state))

    return unique_results


def kill_pid(pid):
    os_name = platform.system().lower()
    try:
        if os_name == "windows":
            # /T オプションで子プロセスも含めて終了
            cmd = ["taskkill", "/PID", str(pid), "/F", "/T"]
            print("実行コマンド:", " ".join(cmd))
            result = subprocess.run(cmd,
                                  stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE,
                                  text=True)
            if result.returncode != 0:
                return False, result.stderr.strip() or result.stdout.strip()
            return True, None
        else:
            # プロセスグループ全体を終了
            try:
                # まず子プロセスのリストを取得
                out = run_cmd(["pgrep", "-P", pid])
                child_pids = [p.strip() for p in out.splitlines() if p.strip()]

                # 子プロセスを終了
                for child_pid in child_pids:
                    subprocess.run(["kill", "-9", child_pid], stderr=subprocess.PIPE)

                # 親プロセスを終了
                result = subprocess.run(["kill", "-9", pid],
                                      stderr=subprocess.PIPE,
                                      text=True)
                if result.returncode != 0:
                    return False, result.stderr.strip()
                return True, None
            except:
                # pgrep がない場合は通常のkill
                result = subprocess.run(["kill", "-9", pid],
                                      stderr=subprocess.PIPE,
                                      text=True)
                if result.returncode != 0:
                    return False, result.stderr.strip()
                return True, None
    except Exception as e:
        return False, str(e)


def on_search():
    port = entry_port.get()
    if not port.isdigit():
        messagebox.showerror("エラー", "ポート番号は数字で入力してください")
        return

    tree.delete(*tree.get_children())
    results = find_pids(port)
    if not results:
        messagebox.showinfo("結果", "該当プロセスがありません")
        return

    for pid, process_name, state in results:
        tree.insert("", tk.END, values=(pid, process_name, state))


def on_kill():
    selected = tree.selection()
    if not selected:
        messagebox.showwarning("警告", "PIDを選択してください")
        return

    pid = str(tree.item(selected[0])['values'][0])
    if not pid.isdigit():
        messagebox.showerror("エラー", "無効なPIDです")
        return
    kill_process(pid)


def on_kill_pid():
    pid = entry_pid.get().strip()
    if not pid.isdigit():
        messagebox.showerror("エラー", "PIDは数字で入力してください")
        return
    kill_process(pid)


def kill_process(pid, refresh=True):
    if messagebox.askyesno("確認", f"PID {pid} を終了しますか?"):
        success, error_msg = kill_pid(pid)
        if success:
            messagebox.showinfo("完了", "プロセスを終了しました")
            if refresh and entry_port.get().isdigit():
                on_search()
        else:
            error_text = f"プロセスの終了に失敗しました\n\n詳細:\n{error_msg}" if error_msg else "プロセスの終了に失敗しました"
            messagebox.showerror("失敗", error_text)


# --- GUI構築 ---
root = tk.Tk()
root.title("Port Killer GUI")
root.geometry("500x350")

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
col = ("PID", "プロセス名", "状態")
tree = ttk.Treeview(root, columns=col, show='headings', height=10)
tree.heading("PID", text="PID")
tree.heading("プロセス名", text="プロセス名")
tree.heading("状態", text="状態")
tree.column("PID", width=80)
tree.column("プロセス名", width=150)
tree.column("状態", width=100)
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
