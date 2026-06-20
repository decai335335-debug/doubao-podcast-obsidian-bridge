#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
doubao_frontend.py
桌面前端：扫描 Obsidian 仓库中的 Markdown，勾选后运行模式 A 生成播客。

用法:
    python doubao_frontend.py
"""

import os
import asyncio
import json
import queue
import re
import hashlib
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import tkinter as tk
import webbrowser
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk


def configure_playwright_browsers():
    if os.environ.get("PLAYWRIGHT_BROWSERS_PATH"):
        return
    local_app_data = os.environ.get("LOCALAPPDATA")
    if not local_app_data:
        return
    browsers = Path(local_app_data) / "ms-playwright"
    if browsers.exists():
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(browsers)


configure_playwright_browsers()

import doubao_pipeline as pipeline
from bridge_json_log import JSON_LOG_DIR
from playwright.async_api import async_playwright


RESOURCE_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
APP_DIR = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
B_LINK_BATCH_SIZE = 25
EXTERNAL_TOOLS = {
    "video_sub": {
        "title": "Video Sub MD",
        "path": Path(r"E:\Projects\ai\video-sub-md"),
        "packed": RESOURCE_DIR / "tools" / "video-sub-md",
    },
    "github_downloader": {
        "title": "GitHub Repo Downloader",
        "path": Path(r"E:\Projects\tools\github-repo-downloader"),
        "packed": RESOURCE_DIR / "tools" / "github-repo-downloader",
    },
    "auto_unzip": {
        "title": "Auto Unzip",
        "path": Path(r"E:\Projects\tools\auto-unzip"),
        "packed": RESOURCE_DIR / "tools" / "auto-unzip",
    },
}


def resolve_python_exe():
    """Find a real Python interpreter for helper scripts when the frontend is frozen."""
    configured = os.environ.get("DOUBAO_PYTHON_EXE")
    candidates = [configured, shutil.which("python"), shutil.which("py")]
    if not getattr(sys, "frozen", False):
        candidates.extend([getattr(sys, "_base_executable", None), sys.executable])
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return str(Path(candidate))
    return ""


HELPER_PYTHON = resolve_python_exe()


class QueueWriter:
    """把 print 输出转发到 Tk 主线程消费的队列。"""

    def __init__(self, log_queue):
        self.log_queue = log_queue

    def write(self, text):
        if text:
            self.log_queue.put(("log", text))

    def flush(self):
        pass


class DoubaoFrontend(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("豆包播客桥接工具")
        self.geometry("1280x820")
        self.minsize(1080, 700)
        self.configure(bg="#F5F3EE")

        self.scanned_items = []
        self.selected_paths = set()
        self.b_links = []
        self.b_podcasts_by_url = {}
        self.b_link_items = {}
        self.b_podcast_items = {}
        self.loaded_b_links = set()
        self.selected_podcasts = set()
        self.b_visible_link_count = B_LINK_BATCH_SIZE
        self.log_queue = queue.Queue()
        self.worker_thread = None
        self.current_process = None
        self.stop_requested = False
        self.bound_markdown_index = None
        self.login_confirm_event = None
        self.login_confirmed = False
        log_dir = APP_DIR / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        self.log_file = log_dir / f"frontend_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"

        self.vault_var = tk.StringVar(value=str(pipeline.OBSIDIAN_VAULT))
        self.limit_var = tk.StringVar(value="80")
        self.since_time_var = tk.StringVar(value=datetime.now().strftime("%Y-%m-%d 00:00"))
        self.browser_visible_var = tk.BooleanVar(value=True)
        self.status_var = tk.StringVar(value="就绪")
        self.selected_count_var = tk.StringVar(value="已选 0 个")
        self.b_link_var = tk.StringVar(value="")
        self.b_count_var = tk.StringVar(value="播客 0 个，已选 0 个")

        self._setup_style()
        self._apply_window_icon()
        self._build_ui()
        os.environ["DOUBAO_BRIDGE_APP_DIR"] = str(APP_DIR)
        self._apply_vault_environment()
        self._ensure_packaged_login_state()
        pipeline.PYTHON_EXE = HELPER_PYTHON
        pipeline.SCRIPT_DIR = APP_DIR
        pipeline.MD2PDF_SCRIPT = RESOURCE_DIR / "md2pdf.py"
        pipeline.SCANNER_SCRIPT = RESOURCE_DIR / "doubao_scanner.py"
        pipeline.DOWNLOADER_SCRIPT = RESOURCE_DIR / "doubao_downloader.py"
        pipeline.POST_PROCESS_SCRIPT = RESOURCE_DIR / "post_process.py"
        pipeline.FULL_SCRIPT = RESOURCE_DIR / "doubao_full.py"
        pipeline.STATE_FILE = APP_DIR / "pipeline_state.json"
        pipeline.UPLOAD_PROGRESS_FILE = APP_DIR / "upload_progress.json"
        pipeline.DOUBAO_STATE_FILE = APP_DIR / "doubao_state.json"
        self._append_log(f"[启动] Helper Python: {HELPER_PYTHON}\n")
        self.load_b_history()
        self.after(300, self.scan_files)
        self.after(100, self._drain_log_queue)

    def _ensure_packaged_login_state(self):
        packaged_state = RESOURCE_DIR / "doubao_state.json"
        app_state = APP_DIR / "doubao_state.json"
        if RESOURCE_DIR == APP_DIR or not packaged_state.exists():
            return
        try:
            if not app_state.exists():
                shutil.copy2(packaged_state, app_state)
                self._append_log(f"[启动] 已初始化登录态: {app_state}\n")
            else:
                self._append_log(f"[启动] 使用现有登录态: {app_state}\n")
        except Exception as exc:
            self._append_log(f"[启动] 同步登录态失败: {exc}\n")

    def _apply_window_icon(self):
        for icon_path in (RESOURCE_DIR / "assets" / "doubao_bridge.ico", APP_DIR / "assets" / "doubao_bridge.ico"):
            if not icon_path.exists():
                continue
            try:
                self.iconbitmap(str(icon_path))
                return
            except Exception:
                pass

    def _audio_dir(self):
        vault = Path(self.vault_var.get().strip()).expanduser()
        return Path(os.environ.get("DOUBAO_AUDIO_DIR", str(vault / "60-附件集中仓" / "音频" / "播客")))

    def _apply_vault_environment(self):
        vault = Path(self.vault_var.get().strip()).expanduser()
        audio_dir = vault / "60-附件集中仓" / "音频" / "播客"
        os.environ["DOUBAO_OBSIDIAN_VAULT"] = str(vault)
        os.environ["DOUBAO_AUDIO_DIR"] = str(audio_dir)
        self.bound_markdown_index = None

    def _browser_env(self, browser_visible):
        env = os.environ.copy()
        env["DOUBAO_BROWSER_VISIBLE"] = "1" if browser_visible else "0"
        env["DOUBAO_HEADLESS"] = "0" if browser_visible else "1"
        return env

    def _setup_style(self):
        style = ttk.Style(self)
        style.theme_use("clam")
        self.colors = {
            "bg": "#F5F3EE",
            "surface": "#FFFFFF",
            "border": "#D9D4C8",
            "text": "#1E2430",
            "muted": "#667085",
            "primary": "#2F6B5F",
            "primary_hover": "#285B51",
            "danger": "#B42318",
            "log_bg": "#111827",
            "log_fg": "#E5E7EB",
        }
        style.configure(".", font=("Microsoft YaHei UI", 10), background=self.colors["bg"], foreground=self.colors["text"])
        style.configure("App.TFrame", background=self.colors["bg"])
        style.configure("Surface.TFrame", background=self.colors["surface"])
        style.configure("TLabel", background=self.colors["surface"], foreground=self.colors["text"])
        style.configure("TRadiobutton", background=self.colors["surface"], foreground=self.colors["text"])
        style.map("TRadiobutton", background=[("active", self.colors["surface"])])
        style.configure("Panel.TLabelframe", background=self.colors["surface"], bordercolor=self.colors["border"], relief=tk.SOLID)
        style.configure("Panel.TLabelframe.Label", background=self.colors["bg"], foreground=self.colors["text"], font=("Microsoft YaHei UI", 10, "bold"))
        style.configure("Title.TLabel", background=self.colors["bg"], foreground=self.colors["text"], font=("Microsoft YaHei UI", 20, "bold"))
        style.configure("Subtitle.TLabel", background=self.colors["bg"], foreground=self.colors["muted"], font=("Microsoft YaHei UI", 10))
        style.configure("Section.TLabel", background=self.colors["surface"], foreground=self.colors["text"], font=("Microsoft YaHei UI", 12, "bold"))
        style.configure("Subtle.TLabel", background=self.colors["surface"], foreground=self.colors["muted"])
        style.configure("Status.TLabel", background="#E7F0EC", foreground=self.colors["primary"], padding=(10, 4), font=("Microsoft YaHei UI", 9, "bold"))
        style.configure("TButton", background="#EFECE5", foreground=self.colors["text"], bordercolor=self.colors["border"], focusthickness=0, padding=(10, 7))
        style.map("TButton", background=[("active", "#E4E0D7")])
        style.configure("Primary.TButton", background=self.colors["primary"], foreground="#FFFFFF", font=("Microsoft YaHei UI", 10, "bold"), padding=(10, 9))
        style.map("Primary.TButton", background=[("active", self.colors["primary_hover"]), ("disabled", "#D7D3CA")], foreground=[("disabled", "#8A8175")])
        style.configure("Danger.TButton", background="#FEE4E2", foreground=self.colors["danger"], padding=(10, 8))
        style.map("Danger.TButton", background=[("active", "#FCD5D2")])
        style.configure("TNotebook", background=self.colors["surface"], borderwidth=0)
        style.configure("TNotebook.Tab", background="#ECE8DE", foreground=self.colors["muted"], padding=(14, 8), font=("Microsoft YaHei UI", 10, "bold"))
        style.map("TNotebook.Tab", background=[("selected", "#FFFFFF")], foreground=[("selected", self.colors["text"])])
        style.configure("Treeview", background="#FFFFFF", fieldbackground="#FFFFFF", foreground=self.colors["text"], bordercolor=self.colors["border"], rowheight=32, font=("Microsoft YaHei UI", 10))
        style.configure("Treeview.Heading", background="#F0EEE8", foreground=self.colors["text"], relief=tk.FLAT, font=("Microsoft YaHei UI", 10, "bold"))
        style.map("Treeview", background=[("selected", "#DDEBE6")], foreground=[("selected", self.colors["text"])])

    def _build_ui(self):
        root = ttk.Frame(self, padding=16, style="App.TFrame")
        root.pack(fill=tk.BOTH, expand=True)

        header = ttk.Frame(root, style="App.TFrame")
        header.pack(fill=tk.X)
        title_block = ttk.Frame(header, style="App.TFrame")
        title_block.pack(side=tk.LEFT)
        ttk.Label(title_block, text="豆包播客桥接工具", style="Title.TLabel").pack(anchor=tk.W)
        ttk.Label(
            title_block,
            text="扫描 Markdown、生成播客、下载音频并绑定回 Obsidian",
            style="Subtitle.TLabel",
        ).pack(anchor=tk.W, pady=(3, 0))
        ttk.Label(
            header,
            textvariable=self.status_var,
            style="Status.TLabel",
        ).pack(side=tk.RIGHT, anchor=tk.N, pady=(4, 0))

        switcher = ttk.Frame(root, style="App.TFrame")
        switcher.pack(fill=tk.X, pady=(14, 0))
        self.tool_buttons = {}
        for key, label in (
            ("doubao", "豆包播客"),
            ("video_sub", "Video Sub MD"),
            ("github_downloader", "GitHub 下载器"),
            ("auto_unzip", "Auto Unzip"),
        ):
            btn = ttk.Button(switcher, text=label, command=lambda name=key: self.show_tool(name))
            btn.pack(side=tk.LEFT, padx=(0, 8), ipadx=8)
            self.tool_buttons[key] = btn

        self.tool_container = ttk.Frame(root, style="App.TFrame")
        self.tool_container.pack(fill=tk.BOTH, expand=True, pady=(12, 0))
        self.tool_frames = {}

        doubao_frame = ttk.Frame(self.tool_container, style="App.TFrame")
        self.tool_frames["doubao"] = doubao_frame

        body = ttk.PanedWindow(doubao_frame, orient=tk.HORIZONTAL)
        body.pack(fill=tk.BOTH, expand=True)

        left = ttk.Frame(body, width=300, style="App.TFrame")
        middle = ttk.Frame(body, style="App.TFrame")
        body.add(left, weight=0)
        body.add(middle, weight=1)

        self._build_left_panel(left)
        self._build_action_panel(middle)
        self._build_file_table(middle)
        self.tool_frames["video_sub"] = self._build_video_sub_tool(self.tool_container)
        self.tool_frames["github_downloader"] = self._build_github_downloader_tool(self.tool_container)
        self.tool_frames["auto_unzip"] = self._build_auto_unzip_tool(self.tool_container)
        self.show_tool("doubao")

    def show_tool(self, name):
        for frame in self.tool_frames.values():
            frame.pack_forget()
        frame = self.tool_frames.get(name)
        if frame:
            frame.pack(fill=tk.BOTH, expand=True)
        for key, button in getattr(self, "tool_buttons", {}).items():
            button.state(["pressed"] if key == name else ["!pressed"])

    def _external_tool_dir(self, key):
        info = EXTERNAL_TOOLS[key]
        for path in (info["packed"], info["path"]):
            if path.exists():
                return path
        return info["path"]

    def _build_tool_shell(self, parent, title, subtitle):
        frame = ttk.Frame(parent, style="App.TFrame")
        header = ttk.Frame(frame, style="App.TFrame")
        header.pack(fill=tk.X)
        ttk.Label(header, text=title, style="Section.TLabel").pack(anchor=tk.W)
        ttk.Label(header, text=subtitle, style="Subtitle.TLabel").pack(anchor=tk.W, pady=(3, 0))
        content = ttk.Frame(frame, style="App.TFrame")
        content.pack(fill=tk.BOTH, expand=True, pady=(14, 0))
        return frame, content

    def _build_video_sub_tool(self, parent):
        frame, content = self._build_tool_shell(
            parent,
            "Video Sub MD",
            "启动原项目的 Web 界面，处理视频字幕下载、分析和 Markdown 输出。",
        )
        group = ttk.LabelFrame(content, text="启动 Web 工具", padding=16, style="Panel.TLabelframe")
        group.pack(fill=tk.X)
        self.video_sub_port_var = tk.StringVar(value="7860")
        ttk.Label(group, text="服务端口").pack(anchor=tk.W)
        ttk.Entry(group, textvariable=self.video_sub_port_var, width=12).pack(anchor=tk.W, pady=(5, 10))
        row = ttk.Frame(group, style="Surface.TFrame")
        row.pack(fill=tk.X)
        ttk.Button(row, text="启动 Video Sub Web", style="Primary.TButton", command=self.start_video_sub_web).pack(
            side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 8), ipady=5
        )
        ttk.Button(row, text="打开浏览器页面", command=self.open_video_sub_web).pack(
            side=tk.LEFT, fill=tk.X, expand=True, padx=(8, 0), ipady=5
        )
        ttk.Label(
            group,
            text="这个项目本身已有 Web 前端。这里先作为独立服务启动，日志进入 C 运行日志，避免重写它的交互流程。",
            style="Subtle.TLabel",
            wraplength=760,
        ).pack(anchor=tk.W, pady=(12, 0))
        return frame

    def _build_github_downloader_tool(self, parent):
        frame, content = self._build_tool_shell(
            parent,
            "GitHub Repo Downloader",
            "粘贴 GitHub 仓库或子目录链接，下载到指定文件夹。",
        )
        group = ttk.LabelFrame(content, text="下载任务", padding=16, style="Panel.TLabelframe")
        group.pack(fill=tk.BOTH, expand=True)
        ttk.Label(group, text="GitHub 链接（每行一个）").pack(anchor=tk.W)
        self.github_links_text = tk.Text(group, height=8, wrap=tk.WORD, font=("Consolas", 10))
        self.github_links_text.pack(fill=tk.X, pady=(5, 10))
        self.github_output_var = tk.StringVar(value=r"E:\Projects\downloads\github-repos")
        ttk.Label(group, text="保存目录").pack(anchor=tk.W)
        out_row = ttk.Frame(group, style="Surface.TFrame")
        out_row.pack(fill=tk.X, pady=(5, 10))
        ttk.Entry(out_row, textvariable=self.github_output_var).pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(out_row, text="选择目录", command=self.choose_github_output_dir).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(group, text="开始下载", style="Primary.TButton", command=self.start_github_download).pack(
            fill=tk.X, ipady=6
        )
        return frame

    def _build_auto_unzip_tool(self, parent):
        frame, content = self._build_tool_shell(
            parent,
            "Auto Unzip",
            "批量解压 ZIP 文件夹，或选择多个压缩包解压并删除源压缩包。",
        )
        group = ttk.LabelFrame(content, text="自动解压", padding=16, style="Panel.TLabelframe")
        group.pack(fill=tk.X)
        self.unzip_folder_var = tk.StringVar(value=str(Path.home() / "Downloads"))
        ttk.Label(group, text="扫描 ZIP 的文件夹").pack(anchor=tk.W)
        folder_row = ttk.Frame(group, style="Surface.TFrame")
        folder_row.pack(fill=tk.X, pady=(5, 10))
        ttk.Entry(folder_row, textvariable=self.unzip_folder_var).pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(folder_row, text="选择文件夹", command=self.choose_unzip_folder).pack(side=tk.LEFT, padx=(8, 0))
        row = ttk.Frame(group, style="Surface.TFrame")
        row.pack(fill=tk.X)
        ttk.Button(row, text="批量解压该文件夹 ZIP", style="Primary.TButton", command=self.start_auto_unzip_folder).pack(
            side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 8), ipady=5
        )
        ttk.Button(row, text="选择压缩包解压", command=self.start_auto_unzip_files).pack(
            side=tk.LEFT, fill=tk.X, expand=True, padx=(8, 0), ipady=5
        )
        ttk.Label(
            group,
            text="会调用 auto-unzip 原项目脚本。成功解压后，原脚本会按自己的规则删除源压缩包。",
            style="Subtle.TLabel",
            wraplength=760,
        ).pack(anchor=tk.W, pady=(12, 0))
        return frame

    def _build_left_panel(self, parent):
        group = ttk.LabelFrame(parent, text="仓库与筛选", padding=14, style="Panel.TLabelframe")
        group.pack(fill=tk.BOTH, expand=True)

        ttk.Label(group, text="Obsidian 仓库").pack(anchor=tk.W)
        entry = ttk.Entry(group, textvariable=self.vault_var)
        entry.pack(fill=tk.X, pady=(5, 8))

        ttk.Button(group, text="选择文件夹", command=self.choose_vault).pack(fill=tk.X)

        ttk.Label(group, text="显示最新文件数").pack(anchor=tk.W, pady=(16, 0))
        ttk.Spinbox(group, from_=10, to=500, increment=10, textvariable=self.limit_var).pack(
            fill=tk.X, pady=(5, 10)
        )

        ttk.Button(group, text="扫描最新 Markdown", style="Primary.TButton", command=self.scan_files).pack(
            fill=tk.X, pady=(4, 6)
        )
        ttk.Button(group, text="从文件选择 Markdown", command=self.pick_markdown_files).pack(fill=tk.X)

        ttk.Separator(group).pack(fill=tk.X, pady=16)

        ttk.Button(group, text="登录/刷新豆包登录态", command=self.start_login_refresh).pack(fill=tk.X, pady=2)
        ttk.Label(
            group,
            text="登录状态保存在 EXE 旁边的 doubao_state.json，A/B 会共同读取。",
            style="Subtle.TLabel",
            wraplength=240,
        ).pack(anchor=tk.W, pady=(6, 0))

        ttk.Separator(group).pack(fill=tk.X, pady=16)

        ttk.Button(group, text="只选今天新增/修改", command=self.select_today).pack(fill=tk.X, pady=2)

        ttk.Label(group, text="选择此时间之后新增/修改").pack(anchor=tk.W, pady=(12, 0))
        ttk.Entry(group, textvariable=self.since_time_var).pack(fill=tk.X, pady=(5, 6))
        ttk.Button(group, text="选择此时间之后", command=self.select_since_time).pack(fill=tk.X, pady=2)

        ttk.Separator(group).pack(fill=tk.X, pady=16)
        ttk.Label(
            group,
            text="A 列表显示已绑定、已生成、未生成；B 清单从历史 JSON 读取。",
            style="Subtle.TLabel",
            wraplength=240,
        ).pack(
            anchor=tk.W
        )

    def _build_file_table(self, parent):
        table_frame = ttk.Frame(parent, style="Surface.TFrame")
        table_frame.pack(fill=tk.BOTH, expand=True, pady=(12, 0))
        self.notebook = ttk.Notebook(table_frame)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=1, pady=1)

        markdown_tab = ttk.Frame(self.notebook, padding=(12, 12, 12, 10), style="Surface.TFrame")
        podcast_tab = ttk.Frame(self.notebook, padding=(12, 12, 12, 10), style="Surface.TFrame")
        log_tab = ttk.Frame(self.notebook, padding=(12, 12, 12, 10), style="Surface.TFrame")
        self.notebook.add(markdown_tab, text="A Markdown")
        self.notebook.add(podcast_tab, text="B 播客状态")
        self.notebook.add(log_tab, text="C 运行日志")

        top = ttk.Frame(markdown_tab, style="Surface.TFrame")
        top.pack(fill=tk.X)
        ttk.Label(top, text="Markdown 文件列表", style="Section.TLabel").pack(side=tk.LEFT)
        ttk.Label(top, textvariable=self.selected_count_var, style="Subtle.TLabel").pack(side=tk.RIGHT)

        columns = ("checked", "status", "name", "modified", "path")
        self.tree = ttk.Treeview(markdown_tab, columns=columns, show="headings", selectmode="browse")
        self.tree.heading("checked", text="选择")
        self.tree.heading("status", text="状态")
        self.tree.heading("name", text="文件名")
        self.tree.heading("modified", text="时间")
        self.tree.heading("path", text="相对路径")
        self.tree.column("checked", width=58, minwidth=58, anchor=tk.CENTER, stretch=False)
        self.tree.column("status", width=90, minwidth=80, anchor=tk.CENTER, stretch=False)
        self.tree.column("name", width=250, minwidth=160)
        self.tree.column("modified", width=140, minwidth=120, stretch=False)
        self.tree.column("path", width=430, minwidth=240)
        self.tree.pack(fill=tk.BOTH, expand=True, pady=(8, 0))
        self.tree.bind("<Button-1>", self.on_tree_click)
        self.tree.bind("<Double-1>", self.on_tree_double_click)
        self.tree.bind("<Button-3>", self.on_markdown_right_click)

        scrollbar = ttk.Scrollbar(markdown_tab, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree.tag_configure("status_bound", foreground="#2F6B5F")
        self.tree.tag_configure("status_generated", foreground="#B56A2A")
        self.tree.tag_configure("status_pending", foreground="#667085")

        btop = ttk.Frame(podcast_tab, style="Surface.TFrame")
        btop.pack(fill=tk.X)
        ttk.Label(btop, text="豆包链接播客清单（按新到旧）", style="Section.TLabel").pack(side=tk.LEFT)
        ttk.Label(btop, textvariable=self.b_count_var, style="Subtle.TLabel").pack(side=tk.RIGHT)

        podcast_columns = ("checked", "status", "title", "duration")
        self.podcast_tree = ttk.Treeview(podcast_tab, columns=podcast_columns, show="tree headings", selectmode="browse")
        self.podcast_tree.heading("#0", text="豆包链接 / PDF")
        self.podcast_tree.heading("checked", text="选择")
        self.podcast_tree.heading("status", text="绑定状态")
        self.podcast_tree.heading("title", text="播客名")
        self.podcast_tree.heading("duration", text="时长")
        self.podcast_tree.column("#0", width=420, minwidth=260)
        self.podcast_tree.column("checked", width=58, minwidth=58, anchor=tk.CENTER, stretch=False)
        self.podcast_tree.column("status", width=110, minwidth=90, stretch=False)
        self.podcast_tree.column("title", width=330, minwidth=200)
        self.podcast_tree.column("duration", width=80, minwidth=70, stretch=False)
        self.podcast_tree.pack(fill=tk.BOTH, expand=True, pady=(8, 0))
        self.podcast_tree.bind("<Button-1>", self.on_podcast_click)
        self.podcast_tree.bind("<Double-1>", self.on_podcast_double_click)
        self.podcast_tree.bind("<<TreeviewOpen>>", self.on_podcast_tree_open)

        podcast_scrollbar = ttk.Scrollbar(podcast_tab, orient=tk.VERTICAL, command=self.podcast_tree.yview)
        self.podcast_tree.configure(yscrollcommand=podcast_scrollbar.set)
        podcast_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.podcast_tree.tag_configure("status_bound", foreground="#2F6B5F")
        self.podcast_tree.tag_configure("status_generated", foreground="#B56A2A")
        self.podcast_tree.tag_configure("status_failed", foreground="#B42318")
        self.podcast_tree.tag_configure("status_pending", foreground="#667085")

        self._build_log_panel(log_tab)

    def _build_action_panel(self, parent):
        actions = ttk.Frame(parent, style="App.TFrame")
        actions.pack(fill=tk.X)

        group = ttk.LabelFrame(actions, text="A 生成播客", padding=14, style="Panel.TLabelframe")
        group.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 8))

        ttk.Label(group, text="浏览器模式").pack(anchor=tk.W)
        ttk.Radiobutton(group, text="可见，方便观察和登录", variable=self.browser_visible_var, value=True).pack(
            anchor=tk.W, pady=(6, 2)
        )
        ttk.Radiobutton(group, text="隐藏，后台运行", variable=self.browser_visible_var, value=False).pack(
            anchor=tk.W
        )

        ttk.Separator(group).pack(fill=tk.X, pady=12)

        a_buttons = ttk.Frame(group, style="Surface.TFrame")
        a_buttons.pack(fill=tk.X)

        self.start_button = ttk.Button(
            a_buttons,
            text="生成播客",
            style="Primary.TButton",
            command=self.start_generate,
        )
        self.start_button.pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=6, padx=(0, 8))

        self.stop_button = ttk.Button(a_buttons, text="停止任务", style="Danger.TButton", command=self.stop_current_task)
        self.stop_button.pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=6)
        self.stop_button.configure(state=tk.DISABLED)

        self.progress = ttk.Progressbar(group, mode="indeterminate")
        self.progress.pack(fill=tk.X, pady=(12, 6))

        ttk.Label(
            group,
            text="勾选 A 列表里的 Markdown 后运行：转 PDF、上传豆包、生成播客、记录聊天地址。",
            style="Subtle.TLabel",
            wraplength=420,
        ).pack(anchor=tk.W, pady=(4, 0))

        bgroup = ttk.LabelFrame(actions, text="B 下载 / 绑定", padding=14, style="Panel.TLabelframe")
        bgroup.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(8, 0))

        b_row1 = ttk.Frame(bgroup, style="Surface.TFrame")
        b_row1.pack(fill=tk.X)
        ttk.Button(b_row1, text="刷新历史链接", command=self.load_b_history).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 6))
        ttk.Button(b_row1, text="扫描选中链接", command=self.scan_b_link).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(6, 0))

        b_row2 = ttk.Frame(bgroup, style="Surface.TFrame")
        b_row2.pack(fill=tk.X, pady=(10, 0))
        ttk.Button(b_row2, text="B 下载并绑定当前链接", style="Primary.TButton", command=self.start_b_full).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 6), ipady=5)
        ttk.Button(b_row2, text="重跑选中项", command=self.start_b_retry).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(6, 0), ipady=5)

        ttk.Button(bgroup, text="只选未绑定/失败播客", command=self.select_failed_podcasts).pack(fill=tk.X, pady=(10, 0))

        ttk.Label(
            bgroup,
            text="在 B 清单里展开链接查看播客；可只重跑失败或未绑定项。",
            style="Subtle.TLabel",
            wraplength=420,
        ).pack(anchor=tk.W, pady=(8, 0))

    def _build_log_panel(self, parent):
        frame = ttk.LabelFrame(parent, text="运行日志", padding=10, style="Panel.TLabelframe")
        frame.pack(fill=tk.BOTH, expand=True)
        log_header = ttk.Frame(frame, style="Surface.TFrame")
        log_header.pack(fill=tk.X, pady=(0, 8))
        ttk.Label(log_header, text="实时输出会同步保存到 logs 文件夹", style="Subtle.TLabel").pack(side=tk.LEFT)
        ttk.Button(log_header, text="清空日志", command=self.clear_log).pack(side=tk.RIGHT)
        self.log_text = tk.Text(
            frame,
            height=18,
            wrap=tk.WORD,
            font=("Consolas", 10),
            bg=self.colors["log_bg"],
            fg=self.colors["log_fg"],
            insertbackground=self.colors["log_fg"],
            relief=tk.FLAT,
            padx=12,
            pady=10,
        )
        self.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        log_scrollbar = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=log_scrollbar.set)
        log_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

    def _run_external_command(self, title, cmd, cwd=None, stdin_text=None):
        if self.worker_thread and self.worker_thread.is_alive():
            messagebox.showinfo("任务运行中", "当前已有任务在运行。")
            return
        self.progress.start(10)
        self.stop_requested = False
        self.stop_button.configure(state=tk.NORMAL)
        self.status_var.set(f"{title} 运行中")
        if hasattr(self, "notebook"):
            self.notebook.select(2)
        self._append_log(f"\n[{title}] 命令: {' '.join(str(part) for part in cmd)}\n")
        self.worker_thread = threading.Thread(
            target=self._external_command_worker,
            args=(title, cmd, cwd, stdin_text),
            daemon=True,
        )
        self.worker_thread.start()

    def _external_command_worker(self, title, cmd, cwd, stdin_text):
        ok = False
        try:
            self.current_process = subprocess.Popen(
                cmd,
                cwd=str(cwd) if cwd else None,
                stdin=subprocess.PIPE if stdin_text is not None else None,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            if stdin_text is not None and self.current_process.stdin:
                self.current_process.stdin.write(stdin_text)
                self.current_process.stdin.close()
            for line in self.current_process.stdout or []:
                self.log_queue.put(("log", line))
            code = self.current_process.wait()
            ok = code == 0
            self.log_queue.put(("log", f"\n[{title}] 退出码: {code}\n"))
        except Exception as exc:
            self.log_queue.put(("log", f"\n[{title}错误] {exc}\n"))
        finally:
            self.current_process = None
            self.log_queue.put(("done", ok))

    def _python_or_warn(self):
        if HELPER_PYTHON:
            return HELPER_PYTHON
        messagebox.showerror("缺少 Python", "找不到可用 python.exe，请设置 DOUBAO_PYTHON_EXE。")
        return ""

    def start_video_sub_web(self):
        python_exe = self._python_or_warn()
        if not python_exe:
            return
        tool_dir = self._external_tool_dir("video_sub")
        web_app = tool_dir / "web_app.py"
        if not web_app.exists():
            messagebox.showerror("缺少工具", f"找不到 Video Sub Web 入口:\n{web_app}")
            return
        port = self.video_sub_port_var.get().strip() or "7860"
        cmd = [python_exe, "-m", "uvicorn", "web_app:app", "--host", "127.0.0.1", "--port", port]
        self._run_external_command("Video Sub MD", cmd, cwd=tool_dir)
        self.after(1500, self.open_video_sub_web)

    def open_video_sub_web(self):
        port = self.video_sub_port_var.get().strip() or "7860"
        webbrowser.open(f"http://127.0.0.1:{port}")

    def choose_github_output_dir(self):
        path = filedialog.askdirectory(initialdir=self.github_output_var.get() or str(Path.home()))
        if path:
            self.github_output_var.set(path)

    def start_github_download(self):
        python_exe = self._python_or_warn()
        if not python_exe:
            return
        tool_dir = self._external_tool_dir("github_downloader")
        script = tool_dir / "github_batch_downloader.py"
        if not script.exists():
            messagebox.showerror("缺少工具", f"找不到 GitHub 下载器入口:\n{script}")
            return
        links = [line.strip() for line in self.github_links_text.get("1.0", tk.END).splitlines() if line.strip()]
        if not links:
            messagebox.showwarning("缺少链接", "请先粘贴至少一个 GitHub 链接。")
            return
        output_dir = self.github_output_var.get().strip()
        Path(output_dir).mkdir(parents=True, exist_ok=True)
        cmd = [python_exe, str(script), "--links", "\n".join(links)]
        self._run_external_command("GitHub 下载器", cmd, cwd=tool_dir, stdin_text=f"{output_dir}\n")

    def choose_unzip_folder(self):
        path = filedialog.askdirectory(initialdir=self.unzip_folder_var.get() or str(Path.home()))
        if path:
            self.unzip_folder_var.set(path)

    def start_auto_unzip_folder(self):
        python_exe = self._python_or_warn()
        if not python_exe:
            return
        tool_dir = self._external_tool_dir("auto_unzip")
        script = tool_dir / "scripts" / "auto_unzip.py"
        folder = self.unzip_folder_var.get().strip()
        if not Path(folder).is_dir():
            messagebox.showwarning("目录不存在", "请选择一个存在的文件夹。")
            return
        self._run_external_command("Auto Unzip", [python_exe, str(script), folder], cwd=tool_dir)

    def start_auto_unzip_files(self):
        python_exe = self._python_or_warn()
        if not python_exe:
            return
        tool_dir = self._external_tool_dir("auto_unzip")
        script = tool_dir / "scripts" / "drop_unzip.py"
        files = filedialog.askopenfilenames(
            title="选择压缩包",
            filetypes=[("Archives", "*.zip *.7z *.rar *.tar *.gz *.tgz *.bz2 *.xz"), ("All files", "*.*")],
        )
        if not files:
            return
        self._run_external_command("Auto Unzip", [python_exe, str(script), *files], cwd=tool_dir)

    def choose_vault(self):
        path = filedialog.askdirectory(initialdir=self.vault_var.get() or str(Path.home()))
        if path:
            self.vault_var.set(path)

    def start_login_refresh(self):
        if self.worker_thread and self.worker_thread.is_alive():
            messagebox.showinfo("任务运行中", "当前已有任务在运行。")
            return
        state_file = APP_DIR / "doubao_state.json"
        ok = messagebox.askokcancel(
            "登录豆包",
            "程序将打开豆包登录页。\n\n请在浏览器里完成登录，确认已经进入豆包账号后，回到本窗口等待自动保存登录态。",
        )
        if not ok:
            return
        self.progress.start(10)
        self.stop_button.configure(state=tk.NORMAL)
        self.status_var.set("正在刷新豆包登录态")
        self.notebook.select(2)
        self._append_log(f"\n[登录] 打开豆包登录页，登录态将保存到: {state_file}\n")
        self.worker_thread = threading.Thread(target=self._run_login_worker, daemon=True)
        self.worker_thread.start()

    def _run_login_worker(self):
        writer = QueueWriter(self.log_queue)
        ok = False
        try:
            with redirect_stdout(writer), redirect_stderr(writer):
                ok = asyncio.run(self._login_and_save_state())
        except Exception as exc:
            self.log_queue.put(("log", f"\n[登录错误] 刷新登录态失败: {exc}\n"))
        finally:
            self.log_queue.put(("done", ok))

    async def _login_and_save_state(self):
        state_file = APP_DIR / "doubao_state.json"
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=False)
            context_kwargs = {"viewport": {"width": 1280, "height": 900}}
            if state_file.exists():
                context_kwargs["storage_state"] = str(state_file)
                print(f"[登录] 已加载现有登录态: {state_file}")
            context = await browser.new_context(**context_kwargs)
            page = await context.new_page()
            await page.goto("https://www.doubao.com", wait_until="domcontentloaded", timeout=60000)
            print("[登录] 浏览器已打开。请完成登录，然后回到本程序点击确认。")
            self.login_confirm_event = threading.Event()
            self.login_confirmed = False
            self.log_queue.put(("login_confirm", None))
            self.login_confirm_event.wait()
            if not self.login_confirmed:
                await browser.close()
                print("[登录] 已取消保存登录态")
                return False
            await context.storage_state(path=str(state_file))
            await browser.close()
            print(f"[登录] 登录态已保存: {state_file}")
            return True

    def scan_files(self):
        vault = self.vault_var.get().strip()
        if not vault:
            messagebox.showwarning("缺少仓库目录", "请先选择 Obsidian 仓库文件夹。")
            return
        try:
            limit = max(1, int(self.limit_var.get().strip() or "80"))
        except ValueError:
            limit = 80
            self.limit_var.set("80")

        self._apply_vault_to_pipeline()
        self.status_var.set("正在扫描 Markdown...")
        self.update_idletasks()
        scanned = pipeline.scan_markdown_files(vault, limit=limit)
        self.scanned_items = scanned
        self.selected_paths.clear()
        self._refresh_table()
        self.status_var.set(f"扫描完成：{len(scanned)} 个文件")
        self._append_log(f"[扫描] {vault}\n[扫描] 找到 {len(scanned)} 个 Markdown 文件\n")

    def pick_markdown_files(self):
        files = filedialog.askopenfilenames(
            title="选择 Markdown 文件",
            initialdir=self.vault_var.get() or str(Path.home()),
            filetypes=[("Markdown", "*.md *.markdown"), ("All files", "*.*")],
        )
        if not files:
            return
        items = []
        for file_path in files:
            path = Path(file_path)
            try:
                timestamp = max(path.stat().st_ctime, path.stat().st_mtime)
            except Exception:
                timestamp = datetime.now().timestamp()
            items.append((timestamp, path.resolve()))
        items.sort(key=lambda item: item[0], reverse=True)
        self.scanned_items = items
        self.selected_paths = {str(path) for _, path in items}
        self._refresh_table()
        self.status_var.set(f"已导入：{len(items)} 个文件")

    def _refresh_table(self):
        self.tree.delete(*self.tree.get_children())
        root = Path(self.vault_var.get().strip()).expanduser()
        generated_stems = self._generated_markdown_stems()
        bound_stems = self._bound_markdown_stems()
        for timestamp, path in self.scanned_items:
            path_text = str(path)
            checked = "☑" if path_text in self.selected_paths else "☐"
            status = self._markdown_status(path, generated_stems, bound_stems)
            try:
                relative = path.relative_to(root)
            except ValueError:
                relative = path
            self.tree.insert(
                "",
                tk.END,
                iid=path_text,
                values=(checked, status, path.name, pipeline._format_file_time(timestamp), str(relative)),
                tags=(self._status_tag(status),),
            )
        self._update_selected_count()

    def _status_tag(self, status):
        if status == "已绑定":
            return "status_bound"
        if status == "已生成":
            return "status_generated"
        if status in {"绑定失败", "缺少音频"}:
            return "status_failed"
        return "status_pending"

    def _generated_markdown_stems(self):
        generated = set()
        for data in self._read_json_logs():
            if data.get("task_type") != "A_GENERATE_PODCAST":
                continue
            for item in data.get("pdf_files", []):
                if item.get("uploaded"):
                    md_path = item.get("markdown_path", "")
                    if md_path:
                        generated.add(Path(md_path).stem)
                    else:
                        generated.add(Path(item.get("name", "")).stem)
        return generated

    def _bound_markdown_stems(self):
        bound = set()
        for data in self._read_json_logs():
            if data.get("task_type") == "B_DOWNLOAD_BIND":
                for item in data.get("bound_markdown", []):
                    if item.get("stem"):
                        bound.add(item["stem"])
        return bound

    def _markdown_has_embedded_podcast(self, path):
        try:
            text = Path(path).read_text(encoding="utf-8", errors="ignore")
            return (
                "配套播客" in text
                or "[[附件/音频/" in text
                or "附件/音频" in text
                or "60-附件集中仓/音频/播客" in text
                or "60-附件集中仓\\音频\\播客" in text
            )
        except Exception:
            return False

    def _bound_markdown_file_index(self):
        if self.bound_markdown_index is not None:
            return self.bound_markdown_index
        vault = Path(self.vault_var.get().strip()).expanduser()
        index = {}
        try:
            for md in vault.rglob("*.md"):
                if self._markdown_has_embedded_podcast(md):
                    index.setdefault(md.stem, md)
        except Exception:
            pass
        self.bound_markdown_index = index
        return index

    def _markdown_status(self, path, generated_stems=None, bound_stems=None):
        generated_stems = generated_stems if generated_stems is not None else self._generated_markdown_stems()
        bound_stems = bound_stems if bound_stems is not None else self._bound_markdown_stems()
        stem = Path(path).stem
        if stem in bound_stems or self._markdown_has_embedded_podcast(path):
            return "已绑定"
        if stem in generated_stems:
            return "已生成"
        return "未生成"

    def on_tree_click(self, event):
        region = self.tree.identify("region", event.x, event.y)
        if region != "cell":
            return
        column = self.tree.identify_column(event.x)
        if column != "#1":
            return
        item = self.tree.identify_row(event.y)
        if not item:
            return
        self.toggle_item(item)

    def on_tree_double_click(self, event):
        item = self.tree.identify_row(event.y)
        if item:
            self.selected_paths = {item}
            self._refresh_table()

    def on_markdown_right_click(self, event):
        item = self.tree.identify_row(event.y)
        if not item:
            return
        self.tree.focus(item)
        self.tree.selection_set(item)
        path = Path(item)
        menu = tk.Menu(self, tearoff=0)
        menu.add_command(label="在 Obsidian 中打开 Markdown", command=lambda: self.open_markdown_file(path))
        menu.add_command(label="打开对应豆包链接", command=lambda: self.open_markdown_doubao_link(path))
        menu.add_command(label="复制最近豆包链接", command=lambda: self.copy_markdown_doubao_link(path))
        menu.add_separator()
        menu.add_command(label="查看绑定/生成记录", command=lambda: self.show_markdown_records(path))
        menu.tk_popup(event.x_root, event.y_root)

    def open_markdown_file(self, path):
        if not path.exists():
            messagebox.showwarning("文件不存在", f"找不到 Markdown 文件:\n{path}")
            return
        try:
            os.startfile(str(path))
        except Exception as exc:
            messagebox.showerror("打开失败", str(exc))

    def _record_matches_markdown(self, item, stem, path_text):
        values = [
            item.get("stem", ""),
            item.get("name", ""),
            item.get("pdf", ""),
            item.get("pdf_name", ""),
            item.get("markdown_path", ""),
            item.get("md_path", ""),
            item.get("path", ""),
        ]
        for value in values:
            if not value:
                continue
            try:
                if Path(str(value)).stem == stem:
                    return True
            except Exception:
                pass
            if path_text and path_text in str(value):
                return True
        return False

    def _records_for_markdown(self, path):
        stem = path.stem
        path_text = str(path)
        records = []
        seen = set()
        for data in self._read_json_logs():
            url = data.get("chat_url", "")
            if not pipeline.is_real_doubao_chat_url(url):
                continue
            created_at = data.get("created_at", "")
            task_type = data.get("task_type", "")
            json_path = data.get("_json_path", "")
            status = ""
            matched = False
            if task_type == "A_GENERATE_PODCAST":
                for item in data.get("markdown_files", []):
                    if isinstance(item, dict) and self._record_matches_markdown(item, stem, path_text):
                        matched = True
                        status = item.get("status", "") or "A Markdown"
                for item in data.get("pdf_files", []):
                    if isinstance(item, dict) and self._record_matches_markdown(item, stem, path_text):
                        matched = True
                        status = "已上传" if item.get("uploaded") else "生成记录"
            elif task_type == "B_DOWNLOAD_BIND":
                for key, label in (
                    ("bound_markdown", "已绑定"),
                    ("failed_bindings", "绑定失败"),
                    ("missing_audio", "缺少音频"),
                ):
                    for item in data.get(key, []):
                        if isinstance(item, dict) and self._record_matches_markdown(item, stem, path_text):
                            matched = True
                            status = label
            if not matched:
                continue
            dedupe = (url, created_at, status, task_type)
            if dedupe in seen:
                continue
            seen.add(dedupe)
            records.append(
                {
                    "time": created_at,
                    "status": status or task_type,
                    "url": url,
                    "task_type": task_type,
                    "json_path": json_path,
                }
            )
        records.sort(key=lambda item: item.get("time", ""), reverse=True)
        return records

    def open_markdown_doubao_link(self, path):
        records = self._records_for_markdown(path)
        if not records:
            messagebox.showinfo("无对应链接", "这个 Markdown 暂时没有对应豆包链接记录。")
            return
        if len(records) == 1:
            webbrowser.open(records[0]["url"])
            return
        self.show_markdown_records(path)

    def copy_markdown_doubao_link(self, path):
        records = self._records_for_markdown(path)
        if not records:
            messagebox.showinfo("无对应链接", "这个 Markdown 暂时没有对应豆包链接记录。")
            return
        self.clipboard_clear()
        self.clipboard_append(records[0]["url"])
        self.status_var.set("已复制最近豆包链接")

    def show_markdown_records(self, path):
        records = self._records_for_markdown(path)
        if not records:
            messagebox.showinfo("无对应记录", "这个 Markdown 暂时没有对应豆包链接记录。")
            return
        win = tk.Toplevel(self)
        win.title(f"历史豆包链接 - {path.name}")
        win.geometry("860x360")
        win.transient(self)
        frame = ttk.Frame(win, padding=12, style="Surface.TFrame")
        frame.pack(fill=tk.BOTH, expand=True)
        columns = ("time", "status", "task_type", "url")
        tree = ttk.Treeview(frame, columns=columns, show="headings", selectmode="browse")
        for col, text, width in (
            ("time", "时间", 150),
            ("status", "状态", 100),
            ("task_type", "来源", 160),
            ("url", "豆包链接", 420),
        ):
            tree.heading(col, text=text)
            tree.column(col, width=width)
        tree.pack(fill=tk.BOTH, expand=True)
        for index, record in enumerate(records):
            tree.insert(
                "",
                tk.END,
                iid=str(index),
                values=(record["time"], record["status"], record["task_type"], record["url"]),
            )
        tree.selection_set("0")
        tree.focus("0")

        def selected_record():
            item = tree.focus()
            if not item:
                return records[0]
            return records[int(item)]

        def open_selected(_event=None):
            webbrowser.open(selected_record()["url"])

        def copy_selected():
            self.clipboard_clear()
            self.clipboard_append(selected_record()["url"])
            self.status_var.set("已复制豆包链接")

        tree.bind("<Double-1>", open_selected)
        row = ttk.Frame(frame, style="Surface.TFrame")
        row.pack(fill=tk.X, pady=(10, 0))
        ttk.Button(row, text="打开选中链接", style="Primary.TButton", command=open_selected).pack(
            side=tk.LEFT, padx=(0, 8)
        )
        ttk.Button(row, text="复制选中链接", command=copy_selected).pack(side=tk.LEFT)
        ttk.Button(row, text="关闭", command=win.destroy).pack(side=tk.RIGHT)

    def toggle_item(self, path_text):
        if path_text in self.selected_paths:
            self.selected_paths.remove(path_text)
        else:
            self.selected_paths.add(path_text)
        self._refresh_table()

    def select_all(self):
        self.selected_paths = {str(path) for _, path in self.scanned_items}
        self._refresh_table()

    def invert_selection(self):
        all_paths = {str(path) for _, path in self.scanned_items}
        self.selected_paths = all_paths - self.selected_paths
        self._refresh_table()

    def select_today(self):
        today = datetime.now().date()
        selected = set()
        for timestamp, path in self.scanned_items:
            if datetime.fromtimestamp(timestamp).date() == today:
                selected.add(str(path))
        self.selected_paths = selected
        self._refresh_table()

    def select_since_time(self):
        text = self.since_time_var.get().strip()
        formats = ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M", "%Y-%m-%d")
        since = None
        for fmt in formats:
            try:
                since = datetime.strptime(text, fmt)
                break
            except ValueError:
                pass
        if since is None:
            messagebox.showwarning("时间格式不正确", "请使用类似 2026-06-20 14:30 的格式。")
            return
        cutoff = since.timestamp()
        self.selected_paths = {str(path) for timestamp, path in self.scanned_items if timestamp >= cutoff}
        self._refresh_table()

    def clear_selection(self):
        self.selected_paths.clear()
        self._refresh_table()

    def _update_selected_count(self):
        self.selected_count_var.set(f"已选 {len(self.selected_paths)} 个")

    def _normalize_pdf_name(self, pdf_name):
        pdf_name = (pdf_name or "").strip()
        match = re.search(r"根据\s+(.+?\.pdf)", pdf_name, re.IGNORECASE)
        if match:
            return match.group(1).strip()
        return pdf_name

    def _stem_from_pdf(self, pdf_name):
        return Path(self._normalize_pdf_name(pdf_name)).stem

    def _read_json_logs(self):
        logs = []
        if not JSON_LOG_DIR.exists():
            return logs
        for path in sorted(JSON_LOG_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                data["_json_path"] = str(path)
                logs.append(data)
            except Exception:
                continue
        return logs

    def _hash_id(self, text):
        return hashlib.sha1(text.encode("utf-8", errors="ignore")).hexdigest()[:16]

    def _link_iid(self, url):
        return f"link::{self._hash_id(url)}"

    def _podcast_iid(self, url, pdf):
        return f"pod::{self._hash_id(url + '|' + pdf)}"

    def _selection_key(self, url, pdf):
        return f"{url}|{pdf}"

    def _selected_link_url(self):
        item = self.podcast_tree.focus()
        if not item:
            return self.b_link_var.get().strip()
        if item in self.b_link_items:
            return self.b_link_items[item]
        if item in self.b_podcast_items:
            return self.b_podcast_items[item]["url"]
        return self.b_link_var.get().strip()

    def load_b_history(self):
        logs = self._read_json_logs()
        link_meta = {}

        def add_link(url, timestamp=0, source="", update_time=True):
            if not pipeline.is_real_doubao_chat_url(url):
                return
            item = link_meta.setdefault(url, {"url": url, "timestamp": 0, "sources": set()})
            if update_time and timestamp:
                if not item["timestamp"] or timestamp < item["timestamp"]:
                    item["timestamp"] = timestamp
            if source:
                item["sources"].add(source)

        state_url = pipeline.load_state().get("chat_url", "")
        if pipeline.is_real_doubao_chat_url(state_url):
            state_time = 0
            try:
                last_run = pipeline.load_state().get("last_run", "")
                if last_run:
                    state_time = datetime.strptime(last_run, "%Y-%m-%d %H:%M:%S").timestamp()
            except Exception:
                pass
            add_link(state_url, state_time, "当前状态")
        for data in logs:
            url = data.get("chat_url", "")
            timestamp = 0
            try:
                timestamp = datetime.strptime(data.get("created_at", ""), "%Y-%m-%d %H:%M:%S").timestamp()
            except Exception:
                try:
                    timestamp = Path(data.get("_json_path", "")).stat().st_mtime
                except Exception:
                    pass
            add_link(url, timestamp, data.get("task_type", "JSON"))
        record_file = pipeline.RECORD_FILE
        if record_file.exists():
            try:
                content = record_file.read_text(encoding="utf-8", errors="ignore")
                for url in re.findall(r"https://www\.doubao\.com/chat/(?!local_)\d+", content):
                    add_link(url, 0, "Markdown记录", update_time=False)
            except Exception as exc:
                self._append_log(f"[B] 读取 Markdown 历史记录失败: {exc}\n")
        self.b_links = sorted(link_meta.values(), key=lambda item: item["timestamp"], reverse=True)
        self.b_visible_link_count = B_LINK_BATCH_SIZE
        self._refresh_podcast_tree_links()
        self._append_log(f"[B] 已读取历史豆包链接 {len(self.b_links)} 个\n")

    def _binding_status_maps(self, url):
        bound = {}
        failed = {}
        missing_audio = {}
        for data in self._read_json_logs():
            if data.get("chat_url") != url or data.get("task_type") != "B_DOWNLOAD_BIND":
                continue
            for item in data.get("bound_markdown", []):
                bound[item.get("stem", "")] = item
            for item in data.get("failed_bindings", []):
                failed[item.get("stem", "")] = item
            for item in data.get("missing_audio", []):
                missing_audio[item.get("stem", "")] = item
        return bound, failed, missing_audio

    def _detect_existing_binding(self, stem):
        vault = Path(self.vault_var.get().strip()).expanduser()
        audio_dir = self._audio_dir()
        mp3_exists = (audio_dir / f"{stem}.mp3").exists()
        bound_index = self._bound_markdown_file_index()
        if stem in bound_index:
            return "已绑定"
        for md_stem in bound_index:
            if stem in md_stem or md_stem in stem:
                return "已绑定"
        candidates = list(vault.rglob(f"{stem}.md"))
        if not candidates:
            candidates = [p for p in vault.rglob("*.md") if stem in p.stem]
        for md in candidates[:5]:
            try:
                text = md.read_text(encoding="utf-8", errors="ignore")
                if "配套播客" in text and f"{stem}.mp3" in text:
                    return "已绑定"
            except Exception:
                pass
        return "已下载未绑定" if mp3_exists else "未绑定"

    def _status_for_stem(self, url, stem):
        detected = self._detect_existing_binding(stem)
        if detected == "已绑定":
            return "已绑定"
        bound, failed, missing_audio = self._binding_status_maps(url)
        if stem in bound:
            return "已绑定"
        if stem in failed:
            return "绑定失败"
        if stem in missing_audio:
            return "缺少音频"
        return detected

    def _refresh_podcast_tree_links(self):
        self.podcast_tree.delete(*self.podcast_tree.get_children())
        self.b_link_items.clear()
        self.b_podcast_items.clear()
        self.loaded_b_links.clear()
        total_known = 0
        visible_links = self.b_links[:self.b_visible_link_count]
        for link in visible_links:
            url = link["url"]
            iid = self._link_iid(url)
            known_count = len(self._load_podcasts_for_url(url))
            total_known += known_count
            when = "-"
            if link.get("timestamp"):
                when = datetime.fromtimestamp(link["timestamp"]).strftime("%Y-%m-%d %H:%M")
            sources = " / ".join(sorted(link.get("sources", [])))
            self.b_link_items[iid] = url
            self.podcast_tree.insert(
                "",
                tk.END,
                iid=iid,
                text=f"{when}  {url}",
                values=("", f"{known_count} 个已知播客", sources, ""),
                open=False,
            )
            self.podcast_tree.insert(iid, tk.END, iid=f"loading::{iid}", text="展开后加载播客...", values=("", "", "", ""))
        if self.b_visible_link_count < len(self.b_links):
            self.podcast_tree.insert(
                "",
                tk.END,
                iid="more_links",
                text=f"加载更多历史链接...（{self.b_visible_link_count}/{len(self.b_links)}）",
                values=("", "", "", ""),
            )
        self.b_count_var.set(f"链接 {len(visible_links)}/{len(self.b_links)} 个，已知播客 {total_known} 个，已选 {len(self.selected_podcasts)} 个")

    def _load_podcasts_for_url(self, url):
        logs = self._read_json_logs()
        podcasts = {}

        for data in logs:
            if data.get("chat_url") != url:
                continue
            if data.get("task_type") == "A_GENERATE_PODCAST":
                for item in data.get("pdf_files", []):
                    pdf = item.get("name", "")
                    if pdf:
                        podcasts[pdf] = {
                            "pdf": pdf,
                            "title": "",
                            "duration": "",
                            "source": "A记录",
                        }
            if data.get("task_type") == "B_DOWNLOAD_BIND":
                for item in data.get("bound_markdown", []) + data.get("failed_bindings", []):
                    stem = item.get("stem", "")
                    if stem:
                        pdf = f"{stem}.pdf"
                        podcasts.setdefault(pdf, {"pdf": pdf, "title": "", "duration": "", "source": "B记录"})

        result = []
        for pdf, item in sorted(podcasts.items()):
            stem = self._stem_from_pdf(pdf)
            status = self._status_for_stem(url, stem)
            result.append({
                "pdf": pdf,
                "title": item.get("title", ""),
                "duration": item.get("duration", ""),
                "status": status,
            })
        return result

    def _load_link_children(self, url):
        link_iid = self._link_iid(url)
        if link_iid in self.loaded_b_links:
            return
        for child in self.podcast_tree.get_children(link_iid):
            self.podcast_tree.delete(child)
        podcasts = self.b_podcasts_by_url.get(url)
        if podcasts is None:
            podcasts = self._load_podcasts_for_url(url)
            self.b_podcasts_by_url[url] = podcasts
        for item in podcasts:
            pdf = item.get("pdf", "")
            iid = self._podcast_iid(url, pdf)
            key = self._selection_key(url, pdf)
            self.b_podcast_items[iid] = {"url": url, **item}
            self.podcast_tree.insert(
                link_iid,
                tk.END,
                iid=iid,
                text=pdf,
                values=(
                    "☑" if key in self.selected_podcasts else "☐",
                    item.get("status", "未绑定"),
                    item.get("title", ""),
                    item.get("duration", ""),
                ),
                tags=(self._status_tag(item.get("status", "未绑定")),),
            )
        if not podcasts:
            self.podcast_tree.insert(link_iid, tk.END, iid=f"empty::{link_iid}", text="暂无本地记录。可点击“扫描选中链接播客”。", values=("", "", "", ""))
        self.loaded_b_links.add(link_iid)
        self._update_b_count()

    def load_selected_link_status(self):
        url = self._selected_link_url()
        if url:
            self.b_link_var.set(url)
            self._load_link_children(url)
            self.notebook.select(1)

    def _refresh_podcast_table(self):
        for iid, item in self.b_podcast_items.items():
            key = self._selection_key(item["url"], item["pdf"])
            item["status"] = self._status_for_stem(item["url"], self._stem_from_pdf(item["pdf"]))
            self.podcast_tree.set(iid, "checked", "☑" if key in self.selected_podcasts else "☐")
            self.podcast_tree.set(iid, "status", item["status"])
            self.podcast_tree.item(iid, tags=(self._status_tag(item["status"]),))
        self._update_b_count()

    def _update_b_count(self):
        known = sum(len(items) for items in self.b_podcasts_by_url.values())
        self.b_count_var.set(f"链接 {len(self.b_links)} 个，已加载播客 {known} 个，已选 {len(self.selected_podcasts)} 个")

    def on_podcast_tree_open(self, _event):
        item = self.podcast_tree.focus()
        if item in self.b_link_items:
            url = self.b_link_items[item]
            self.b_link_var.set(url)
            self._load_link_children(url)

    def on_podcast_click(self, event):
        region = self.podcast_tree.identify("region", event.x, event.y)
        if region not in {"cell", "tree"}:
            return
        element = self.podcast_tree.identify("element", event.x, event.y)
        if "indicator" in str(element):
            return
        item = self.podcast_tree.identify_row(event.y)
        if item:
            if item == "more_links":
                self.b_visible_link_count += B_LINK_BATCH_SIZE
                self._refresh_podcast_tree_links()
                return
            if item in self.b_link_items:
                url = self.b_link_items[item]
                self.b_link_var.set(url)
                self._load_link_children(url)
                self.podcast_tree.item(item, open=not self.podcast_tree.item(item, "open"))
                return
            self.toggle_podcast(item)

    def on_podcast_double_click(self, event):
        item = self.podcast_tree.identify_row(event.y)
        if item == "more_links":
            self.b_visible_link_count += B_LINK_BATCH_SIZE
            self._refresh_podcast_tree_links()
        elif item in self.b_link_items:
            url = self.b_link_items[item]
            self.b_link_var.set(url)
            self._load_link_children(url)
            self.podcast_tree.item(item, open=not self.podcast_tree.item(item, "open"))
        elif item:
            self.toggle_podcast(item)

    def toggle_podcast(self, iid):
        item = self.b_podcast_items.get(iid)
        if not item:
            return
        key = self._selection_key(item["url"], item["pdf"])
        if key in self.selected_podcasts:
            self.selected_podcasts.remove(key)
        else:
            self.selected_podcasts.add(key)
        self._refresh_podcast_table()

    def select_failed_podcasts(self):
        url = self._selected_link_url()
        if url:
            self._load_link_children(url)
        for iid, item in self.b_podcast_items.items():
            if url and item["url"] != url:
                continue
            if item.get("status") in {"未绑定", "绑定失败", "缺少音频", "已下载未绑定"}:
                self.selected_podcasts.add(self._selection_key(item["url"], item["pdf"]))
        self._refresh_podcast_table()

    def scan_b_link(self):
        url = self._selected_link_url()
        if not url:
            messagebox.showwarning("缺少链接", "请先在 B 播客状态树里选择一个豆包链接。")
            return
        if self.worker_thread and self.worker_thread.is_alive():
            messagebox.showinfo("任务运行中", "当前已有任务在运行。")
            return
        self.progress.start(10)
        self.stop_requested = False
        self.stop_button.configure(state=tk.NORMAL)
        self.status_var.set("正在扫描豆包链接播客...")
        self.notebook.select(2)
        browser_visible = self.browser_visible_var.get()
        self._append_log(f"[B] 浏览器模式: {'可见' if browser_visible else '隐藏'}\n")
        self.worker_thread = threading.Thread(target=self._run_scan_b_worker, args=(url, browser_visible), daemon=True)
        self.worker_thread.start()

    def _run_scan_b_worker(self, url, browser_visible):
        writer = QueueWriter(self.log_queue)
        ok = False
        try:
            with redirect_stdout(writer), redirect_stderr(writer):
                if not HELPER_PYTHON:
                    raise RuntimeError("找不到可用的 python.exe，请设置 DOUBAO_PYTHON_EXE")
                cmd = [HELPER_PYTHON, str(RESOURCE_DIR / "doubao_scanner.py"), url]
                if not browser_visible:
                    cmd.append("--headless")
                self.current_process = subprocess.Popen(cmd, text=True, encoding="utf-8", errors="replace", env=self._browser_env(browser_visible))
                result_code = self.current_process.wait()
                self.current_process = None
                result = type("Result", (), {"returncode": result_code})()
                ok = result.returncode == 0
                if ok:
                    podcasts_path = APP_DIR / "podcasts_list.json"
                    with open(podcasts_path, "r", encoding="utf-8") as f:
                        scanned = json.load(f)
                    self.log_queue.put(("b_scanned", {"url": url, "podcasts": scanned}))
        except Exception as exc:
            self.log_queue.put(("log", f"\n[B错误] 扫描链接失败: {exc}\n"))
        finally:
            self.log_queue.put(("done", ok))

    def start_b_retry(self):
        url = self._selected_link_url()
        selected_urls = {key.split("|", 1)[0] for key in self.selected_podcasts}
        if len(selected_urls) == 1:
            url = next(iter(selected_urls))
        selected = []
        for key in self.selected_podcasts:
            item_url, pdf = key.split("|", 1)
            if item_url == url and pdf:
                selected.append(pdf)
        if not url:
            messagebox.showwarning("缺少链接", "请先选择豆包链接。")
            return
        if not selected:
            messagebox.showwarning("未选择播客", "请先勾选要重跑下载/绑定的播客。")
            return
        if self.worker_thread and self.worker_thread.is_alive():
            messagebox.showinfo("任务运行中", "当前已有任务在运行。")
            return
        self.progress.start(10)
        self.stop_requested = False
        self.stop_button.configure(state=tk.NORMAL)
        self.status_var.set(f"B 重跑中：{len(selected)} 个播客")
        self._append_log(f"\n[B] 开始重跑选中项：{len(selected)} 个\n")
        self.notebook.select(2)
        browser_visible = self.browser_visible_var.get()
        self._append_log(f"[B] 浏览器模式: {'可见' if browser_visible else '隐藏'}\n")
        self.worker_thread = threading.Thread(target=self._run_b_retry_worker, args=(url, selected, browser_visible), daemon=True)
        self.worker_thread.start()

    def start_b_full(self):
        url = self._selected_link_url()
        if not url:
            messagebox.showwarning("缺少链接", "请先选择豆包链接。")
            return
        if self.worker_thread and self.worker_thread.is_alive():
            messagebox.showinfo("任务运行中", "当前已有任务在运行。")
            return
        self.progress.start(10)
        self.stop_requested = False
        self.stop_button.configure(state=tk.NORMAL)
        self.status_var.set("B 全流程运行中")
        self._append_log(f"\n[B] 开始下载并绑定当前链接：{url}\n")
        self.notebook.select(2)
        browser_visible = self.browser_visible_var.get()
        self._append_log(f"[B] 浏览器模式: {'可见' if browser_visible else '隐藏'}\n")
        self.worker_thread = threading.Thread(target=self._run_b_full_worker, args=(url, browser_visible), daemon=True)
        self.worker_thread.start()

    def _run_b_full_worker(self, url, browser_visible):
        writer = QueueWriter(self.log_queue)
        ok = False
        try:
            with redirect_stdout(writer), redirect_stderr(writer):
                script_dir = RESOURCE_DIR
                scanner_cmd = [HELPER_PYTHON, str(script_dir / "doubao_scanner.py"), url]
                downloader_cmd = [HELPER_PYTHON, str(script_dir / "doubao_downloader.py"), url, "--all"]
                if not browser_visible:
                    scanner_cmd.append("--headless")
                    downloader_cmd.append("--headless")
                steps = [
                    scanner_cmd,
                    downloader_cmd,
                    [HELPER_PYTHON, str(script_dir / "post_process.py"), "--bind-existing", "--all-wav"],
                ]
                ok = True
                for cmd in steps:
                    if self.stop_requested:
                        ok = False
                        break
                    if not HELPER_PYTHON:
                        raise RuntimeError("找不到可用的 python.exe，请设置 DOUBAO_PYTHON_EXE")
                    self.current_process = subprocess.Popen(cmd, text=True, encoding="utf-8", errors="replace", env=self._browser_env(browser_visible))
                    result_code = self.current_process.wait()
                    self.current_process = None
                    if result_code != 0:
                        ok = False
        except Exception as exc:
            self.log_queue.put(("log", f"\n[B错误] 全流程失败: {exc}\n"))
        finally:
            self.log_queue.put(("done", ok))

    def _run_b_retry_worker(self, url, selected_pdfs, browser_visible):
        writer = QueueWriter(self.log_queue)
        ok = False
        try:
            with redirect_stdout(writer), redirect_stderr(writer):
                if not HELPER_PYTHON:
                    raise RuntimeError("找不到可用的 python.exe，请设置 DOUBAO_PYTHON_EXE")
                normalized_pdfs = [self._normalize_pdf_name(pdf) for pdf in selected_pdfs]
                stems = [Path(pdf).stem for pdf in normalized_pdfs]
                script_dir = RESOURCE_DIR
                downloader_cmd = [HELPER_PYTHON, str(script_dir / "doubao_downloader.py"), url, *normalized_pdfs]
                if not browser_visible:
                    downloader_cmd.append("--headless")
                self.current_process = subprocess.Popen(downloader_cmd, text=True, encoding="utf-8", errors="replace", env=self._browser_env(browser_visible))
                result1_code = self.current_process.wait()
                self.current_process = None
                if self.stop_requested:
                    raise RuntimeError("用户请求停止")
                result1 = type("Result", (), {"returncode": result1_code})()

                with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8", suffix=".txt") as f:
                    for stem in stems:
                        f.write(stem + "\n")
                    targets_file = f.name
                try:
                    post_cmd = [
                        HELPER_PYTHON,
                        str(script_dir / "post_process.py"),
                        "--targets-file",
                        targets_file,
                        "--bind-existing",
                    ]
                    self.current_process = subprocess.Popen(post_cmd, text=True, encoding="utf-8", errors="replace", env=self._browser_env(browser_visible))
                    result2_code = self.current_process.wait()
                    self.current_process = None
                    result2 = type("Result", (), {"returncode": result2_code})()
                    ok = result1.returncode == 0 and result2.returncode == 0
                finally:
                    try:
                        Path(targets_file).unlink()
                    except Exception:
                        pass
        except Exception as exc:
            self.log_queue.put(("log", f"\n[B错误] 重跑失败: {exc}\n"))
        finally:
            self.log_queue.put(("done", ok))

    def start_generate(self):
        if self.worker_thread and self.worker_thread.is_alive():
            messagebox.showinfo("任务运行中", "当前已有生成任务在运行。")
            return
        selected = [str(path) for _, path in self.scanned_items if str(path) in self.selected_paths]
        if not selected:
            messagebox.showwarning("未选择文件", "请先勾选要生成播客的 Markdown 文件。")
            return
        if len(selected) > 1:
            preview = "\n".join(f"• {Path(path).name}" for path in selected[:8])
            if len(selected) > 8:
                preview += f"\n……另有 {len(selected) - 8} 个"
            ok = messagebox.askyesno(
                "确认批量生成",
                f"当前勾选了 {len(selected)} 个 Markdown，将全部生成播客：\n\n{preview}\n\n确定继续吗？",
            )
            if not ok:
                return

        self._apply_vault_to_pipeline()
        browser_visible = self.browser_visible_var.get()
        self._append_log(f"[A] 浏览器模式: {'可见' if browser_visible else '隐藏'}\n")
        self.start_button.configure(state=tk.DISABLED)
        self.stop_requested = False
        pipeline.clear_stop_request()
        self.stop_button.configure(state=tk.NORMAL)
        self.progress.start(10)
        self.status_var.set(f"生成中：{len(selected)} 个 Markdown")
        self._append_log(f"\n[任务] 开始生成播客，文件数：{len(selected)}\n")
        self.notebook.select(2)

        self.worker_thread = threading.Thread(
            target=self._run_generate_worker,
            args=(selected, browser_visible),
            daemon=True,
        )
        self.worker_thread.start()

    def _run_generate_worker(self, selected, browser_visible):
        writer = QueueWriter(self.log_queue)
        ok = False
        try:
            with redirect_stdout(writer), redirect_stderr(writer):
                ok = pipeline.run_generate_for_markdown_files(
                    selected,
                    browser_visible=browser_visible,
                )
        except Exception as exc:
            self.log_queue.put(("log", f"\n[错误] 前端任务异常: {exc}\n"))
        finally:
            self.log_queue.put(("done", ok))

    def stop_current_task(self):
        self.stop_requested = True
        pipeline.request_stop()
        proc = self.current_process
        if proc and proc.poll() is None:
            try:
                proc.terminate()
            except Exception:
                pass
        self._append_log("\n[停止] 已请求停止当前任务，正在收尾...\n")

    def _apply_vault_to_pipeline(self):
        vault = Path(self.vault_var.get().strip()).expanduser()
        self._apply_vault_environment()
        os.environ["DOUBAO_OBSIDIAN_VAULT"] = str(vault)
        pipeline.OBSIDIAN_VAULT = vault
        pipeline.RECORD_FILE = vault / "总报告" / "豆包播客代码上传与下载绑定记录.md"
        pipeline.DEFAULT_MARKDOWN_SCAN_DIR = str(vault)

    def _drain_log_queue(self):
        try:
            while True:
                kind, payload = self.log_queue.get_nowait()
                if kind == "log":
                    self._append_log(payload)
                elif kind == "b_scanned":
                    self._apply_scanned_podcasts(payload["url"], payload["podcasts"])
                elif kind == "login_confirm":
                    self.login_confirmed = messagebox.askokcancel(
                        "保存登录态",
                        "确认已经登录豆包了吗？\n\n点“确定”保存当前登录状态。",
                    )
                    if self.login_confirm_event:
                        self.login_confirm_event.set()
                elif kind == "done":
                    self.progress.stop()
                    self.start_button.configure(state=tk.NORMAL)
                    self.stop_button.configure(state=tk.DISABLED)
                    self.current_process = None
                    pipeline.clear_stop_request()
                    self.status_var.set("任务完成" if payload else "任务结束：存在失败或中断")
                    self._append_log("\n[任务] 流程结束\n")
                    self.after(300, self.scan_files)
        except queue.Empty:
            pass
        self.after(100, self._drain_log_queue)

    def _append_log(self, text):
        if not hasattr(self, "log_text"):
            return
        self.log_text.insert(tk.END, text)
        self.log_text.see(tk.END)
        try:
            with open(self.log_file, "a", encoding="utf-8") as f:
                f.write(text)
        except Exception:
            pass

    def _apply_scanned_podcasts(self, url, scanned):
        self.b_link_var.set(url)
        podcasts = []
        for item in scanned:
            pdf = self._normalize_pdf_name(item.get("pdf", ""))
            stem = self._stem_from_pdf(pdf)
            podcasts.append({
                "pdf": pdf,
                "title": item.get("title", ""),
                "duration": item.get("duration", ""),
                "status": self._status_for_stem(url, stem),
            })
        self.b_podcasts_by_url[url] = podcasts
        link_iid = self._link_iid(url)
        if link_iid not in self.b_link_items:
            self.b_links.insert(0, {"url": url, "timestamp": time.time(), "sources": {"扫描结果"}})
            self.b_visible_link_count = max(self.b_visible_link_count, 1)
            self._refresh_podcast_tree_links()
        self.loaded_b_links.discard(link_iid)
        self._load_link_children(url)
        if self.podcast_tree.exists(link_iid):
            self.podcast_tree.item(link_iid, open=True)
        self.notebook.select(1)
        self.status_var.set(f"B 扫描完成：{len(podcasts)} 个播客")
        self._append_log(f"[B] 扫描完成：{len(podcasts)} 个播客\n")

    def clear_log(self):
        self.log_text.delete("1.0", tk.END)


def main():
    app = DoubaoFrontend()
    app.mainloop()


if __name__ == "__main__":
    main()
