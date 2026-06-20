#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
doubao_frontend.py
桌面前端：扫描 Obsidian 仓库中的 Markdown，勾选后运行模式 A 生成播客。

用法:
    python doubao_frontend.py
"""

import os
import json
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import tkinter as tk
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


RESOURCE_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
APP_DIR = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent


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
        self.geometry("1180x760")
        self.minsize(980, 640)

        self.scanned_items = []
        self.selected_paths = set()
        self.b_links = []
        self.b_podcasts = []
        self.selected_pdfs = set()
        self.log_queue = queue.Queue()
        self.worker_thread = None
        self.current_process = None
        self.stop_requested = False
        log_dir = APP_DIR / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        self.log_file = log_dir / f"frontend_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"

        self.vault_var = tk.StringVar(value=str(pipeline.OBSIDIAN_VAULT))
        self.limit_var = tk.StringVar(value="80")
        self.browser_visible_var = tk.BooleanVar(value=True)
        self.status_var = tk.StringVar(value="就绪")
        self.selected_count_var = tk.StringVar(value="已选 0 个")
        self.b_link_var = tk.StringVar(value="")
        self.b_count_var = tk.StringVar(value="播客 0 个，已选 0 个")

        self._setup_style()
        self._build_ui()
        os.environ["DOUBAO_BRIDGE_APP_DIR"] = str(APP_DIR)
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
        self._append_log(f"[启动] Helper Python: {HELPER_PYTHON}\n")
        self.load_b_history()
        self.after(100, self._drain_log_queue)

    def _ensure_packaged_login_state(self):
        packaged_state = RESOURCE_DIR / "doubao_state.json"
        app_state = APP_DIR / "doubao_state.json"
        if RESOURCE_DIR == APP_DIR or not packaged_state.exists():
            return
        try:
            if not app_state.exists() or packaged_state.stat().st_size > app_state.stat().st_size * 0.8:
                shutil.copy2(packaged_state, app_state)
                self._append_log(f"[启动] 已同步登录态: {app_state}\n")
        except Exception as exc:
            self._append_log(f"[启动] 同步登录态失败: {exc}\n")

    def _setup_style(self):
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure(".", font=("Microsoft YaHei UI", 10))
        style.configure("Title.TLabel", font=("Microsoft YaHei UI", 17, "bold"))
        style.configure("Subtle.TLabel", foreground="#687385")
        style.configure("Primary.TButton", font=("Microsoft YaHei UI", 10, "bold"))
        style.configure("Treeview", rowheight=30, font=("Microsoft YaHei UI", 10))
        style.configure("Treeview.Heading", font=("Microsoft YaHei UI", 10, "bold"))

    def _build_ui(self):
        root = ttk.Frame(self, padding=14)
        root.pack(fill=tk.BOTH, expand=True)

        header = ttk.Frame(root)
        header.pack(fill=tk.X)
        ttk.Label(header, text="豆包播客桥接工具", style="Title.TLabel").pack(side=tk.LEFT)
        ttk.Label(
            header,
            textvariable=self.status_var,
            style="Subtle.TLabel",
        ).pack(side=tk.RIGHT)

        body = ttk.PanedWindow(root, orient=tk.HORIZONTAL)
        body.pack(fill=tk.BOTH, expand=True, pady=(14, 10))

        left = ttk.Frame(body, width=280)
        middle = ttk.Frame(body)
        right = ttk.Frame(body, width=260)
        body.add(left, weight=0)
        body.add(middle, weight=1)
        body.add(right, weight=0)

        self._build_left_panel(left)
        self._build_file_table(middle)
        self._build_log_panel(middle)
        self._build_action_panel(right)

    def _build_left_panel(self, parent):
        group = ttk.LabelFrame(parent, text="文件来源", padding=12)
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

        ttk.Button(group, text="全选", command=self.select_all).pack(fill=tk.X, pady=2)
        ttk.Button(group, text="反选", command=self.invert_selection).pack(fill=tk.X, pady=2)
        ttk.Button(group, text="只选今天新增/修改", command=self.select_today).pack(fill=tk.X, pady=2)
        ttk.Button(group, text="清空选择", command=self.clear_selection).pack(fill=tk.X, pady=2)

        ttk.Separator(group).pack(fill=tk.X, pady=16)
        ttk.Label(group, text="列表按最新添加/修改时间从新到旧排列。", style="Subtle.TLabel", wraplength=230).pack(
            anchor=tk.W
        )

    def _build_file_table(self, parent):
        self.notebook = ttk.Notebook(parent)
        self.notebook.pack(fill=tk.BOTH, expand=True)

        markdown_tab = ttk.Frame(self.notebook, padding=(0, 8, 0, 0))
        podcast_tab = ttk.Frame(self.notebook, padding=(0, 8, 0, 0))
        self.notebook.add(markdown_tab, text="A Markdown")
        self.notebook.add(podcast_tab, text="B 播客状态")

        top = ttk.Frame(markdown_tab)
        top.pack(fill=tk.X)
        ttk.Label(top, text="Markdown 文件列表", font=("Microsoft YaHei UI", 12, "bold")).pack(side=tk.LEFT)
        ttk.Label(top, textvariable=self.selected_count_var, style="Subtle.TLabel").pack(side=tk.RIGHT)

        columns = ("checked", "name", "modified", "path")
        self.tree = ttk.Treeview(markdown_tab, columns=columns, show="headings", selectmode="browse")
        self.tree.heading("checked", text="选择")
        self.tree.heading("name", text="文件名")
        self.tree.heading("modified", text="时间")
        self.tree.heading("path", text="相对路径")
        self.tree.column("checked", width=58, minwidth=58, anchor=tk.CENTER, stretch=False)
        self.tree.column("name", width=260, minwidth=160)
        self.tree.column("modified", width=140, minwidth=120, stretch=False)
        self.tree.column("path", width=430, minwidth=240)
        self.tree.pack(fill=tk.BOTH, expand=True, pady=(8, 0))
        self.tree.bind("<Button-1>", self.on_tree_click)
        self.tree.bind("<Double-1>", self.on_tree_double_click)

        scrollbar = ttk.Scrollbar(markdown_tab, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        btop = ttk.Frame(podcast_tab)
        btop.pack(fill=tk.X)
        ttk.Label(btop, text="豆包链接播客清单", font=("Microsoft YaHei UI", 12, "bold")).pack(side=tk.LEFT)
        ttk.Label(btop, textvariable=self.b_count_var, style="Subtle.TLabel").pack(side=tk.RIGHT)

        podcast_columns = ("checked", "status", "pdf", "title", "duration")
        self.podcast_tree = ttk.Treeview(podcast_tab, columns=podcast_columns, show="headings", selectmode="browse")
        self.podcast_tree.heading("checked", text="选择")
        self.podcast_tree.heading("status", text="绑定状态")
        self.podcast_tree.heading("pdf", text="PDF")
        self.podcast_tree.heading("title", text="播客名")
        self.podcast_tree.heading("duration", text="时长")
        self.podcast_tree.column("checked", width=58, minwidth=58, anchor=tk.CENTER, stretch=False)
        self.podcast_tree.column("status", width=110, minwidth=90, stretch=False)
        self.podcast_tree.column("pdf", width=310, minwidth=200)
        self.podcast_tree.column("title", width=330, minwidth=200)
        self.podcast_tree.column("duration", width=80, minwidth=70, stretch=False)
        self.podcast_tree.pack(fill=tk.BOTH, expand=True, pady=(8, 0))
        self.podcast_tree.bind("<Button-1>", self.on_podcast_click)
        self.podcast_tree.bind("<Double-1>", self.on_podcast_double_click)

        podcast_scrollbar = ttk.Scrollbar(podcast_tab, orient=tk.VERTICAL, command=self.podcast_tree.yview)
        self.podcast_tree.configure(yscrollcommand=podcast_scrollbar.set)
        podcast_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

    def _build_action_panel(self, parent):
        group = ttk.LabelFrame(parent, text="A 生成播客", padding=12)
        group.pack(fill=tk.X)

        ttk.Label(group, text="浏览器模式").pack(anchor=tk.W)
        ttk.Radiobutton(group, text="可见，方便观察和登录", variable=self.browser_visible_var, value=True).pack(
            anchor=tk.W, pady=(6, 2)
        )
        ttk.Radiobutton(group, text="隐藏，后台运行", variable=self.browser_visible_var, value=False).pack(
            anchor=tk.W
        )

        ttk.Separator(group).pack(fill=tk.X, pady=18)

        self.start_button = ttk.Button(
            group,
            text="生成播客",
            style="Primary.TButton",
            command=self.start_generate,
        )
        self.start_button.pack(fill=tk.X, ipady=6)

        self.stop_button = ttk.Button(group, text="停止当前任务", command=self.stop_current_task)
        self.stop_button.pack(fill=tk.X, pady=(10, 0))
        self.stop_button.configure(state=tk.DISABLED)

        ttk.Button(group, text="清空日志", command=self.clear_log).pack(fill=tk.X, pady=(10, 0))

        self.progress = ttk.Progressbar(group, mode="indeterminate")
        self.progress.pack(fill=tk.X, pady=(18, 6))

        ttk.Label(
            group,
            text="勾选文件后点击生成播客，会执行原来的模式 A 流程：Markdown 转 PDF、上传豆包、点击生成播客、保存聊天地址。",
            style="Subtle.TLabel",
            wraplength=230,
        ).pack(anchor=tk.W, pady=(12, 0))

        bgroup = ttk.LabelFrame(parent, text="B 下载 / 绑定", padding=12)
        bgroup.pack(fill=tk.BOTH, expand=True, pady=(12, 0))

        ttk.Button(bgroup, text="刷新历史链接", command=self.load_b_history).pack(fill=tk.X)
        ttk.Label(bgroup, text="历史豆包链接").pack(anchor=tk.W, pady=(12, 0))
        self.b_link_combo = ttk.Combobox(bgroup, textvariable=self.b_link_var, state="readonly")
        self.b_link_combo.pack(fill=tk.X, pady=(5, 8))
        self.b_link_combo.bind("<<ComboboxSelected>>", lambda _e: self.load_selected_link_status())

        ttk.Button(bgroup, text="扫描该链接播客", command=self.scan_b_link).pack(fill=tk.X, pady=2)
        ttk.Button(bgroup, text="B 下载并绑定当前链接", style="Primary.TButton", command=self.start_b_full).pack(
            fill=tk.X, pady=(8, 2), ipady=5
        )
        ttk.Button(bgroup, text="只选未绑定/失败", command=self.select_failed_podcasts).pack(fill=tk.X, pady=2)
        ttk.Button(bgroup, text="B 重跑选中项", command=self.start_b_retry).pack(
            fill=tk.X, pady=(10, 2), ipady=5
        )

        ttk.Label(
            bgroup,
            text="B 会从历史 JSON 读取链接和绑定结果。扫描后可看到该链接里的播客名、PDF 和绑定状态；重跑只处理你勾选的项。",
            style="Subtle.TLabel",
            wraplength=230,
        ).pack(anchor=tk.W, pady=(12, 0))

    def _build_log_panel(self, parent):
        frame = ttk.LabelFrame(parent, text="运行日志", padding=8)
        frame.pack(fill=tk.BOTH, expand=False, pady=(10, 0))
        frame.configure(height=220)
        frame.pack_propagate(False)
        self.log_text = tk.Text(
            frame,
            height=14,
            wrap=tk.WORD,
            font=("Consolas", 10),
            bg="#111827",
            fg="#E5E7EB",
            insertbackground="#E5E7EB",
            relief=tk.FLAT,
        )
        self.log_text.pack(fill=tk.BOTH, expand=True)

    def choose_vault(self):
        path = filedialog.askdirectory(initialdir=self.vault_var.get() or str(Path.home()))
        if path:
            self.vault_var.set(path)

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
        for timestamp, path in self.scanned_items:
            path_text = str(path)
            checked = "☑" if path_text in self.selected_paths else "☐"
            try:
                relative = path.relative_to(root)
            except ValueError:
                relative = path
            self.tree.insert(
                "",
                tk.END,
                iid=path_text,
                values=(checked, path.name, pipeline._format_file_time(timestamp), str(relative)),
            )
        self._update_selected_count()

    def on_tree_click(self, event):
        region = self.tree.identify("region", event.x, event.y)
        if region != "cell":
            return
        item = self.tree.identify_row(event.y)
        if not item:
            return
        self.toggle_item(item)

    def on_tree_double_click(self, event):
        item = self.tree.identify_row(event.y)
        if item:
            self.toggle_item(item)

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

    def load_b_history(self):
        logs = self._read_json_logs()
        links = []
        seen = set()
        state_url = pipeline.load_state().get("chat_url", "")
        if pipeline.is_real_doubao_chat_url(state_url):
            links.append(state_url)
            seen.add(state_url)
        for data in logs:
            url = data.get("chat_url", "")
            if pipeline.is_real_doubao_chat_url(url) and url not in seen:
                links.append(url)
                seen.add(url)
        record_file = pipeline.RECORD_FILE
        if record_file.exists():
            try:
                content = record_file.read_text(encoding="utf-8", errors="ignore")
                for url in re.findall(r"https://www\.doubao\.com/chat/(?!local_)\d+", content):
                    if url not in seen:
                        links.append(url)
                        seen.add(url)
            except Exception as exc:
                self._append_log(f"[B] 读取 Markdown 历史记录失败: {exc}\n")
        self.b_links = links
        self.b_link_combo["values"] = links
        if links and not self.b_link_var.get():
            self.b_link_var.set(links[0])
            self.load_selected_link_status()
        self._append_log(f"[B] 已读取历史豆包链接 {len(links)} 个\n")

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
        audio_dir = vault / "附件" / "音频"
        mp3_exists = (audio_dir / f"{stem}.mp3").exists()
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
        bound, failed, missing_audio = self._binding_status_maps(url)
        if stem in bound:
            return "已绑定"
        if stem in failed:
            return "绑定失败"
        if stem in missing_audio:
            return "缺少音频"
        return self._detect_existing_binding(stem)

    def load_selected_link_status(self):
        url = self.b_link_var.get().strip()
        if not url:
            return
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

        self.b_podcasts = []
        for pdf, item in podcasts.items():
            stem = self._stem_from_pdf(pdf)
            status = self._status_for_stem(url, stem)
            self.b_podcasts.append({
                "pdf": pdf,
                "title": item.get("title", ""),
                "duration": item.get("duration", ""),
                "status": status,
            })
        self.selected_pdfs.clear()
        self._refresh_podcast_table()
        self.notebook.select(1)

    def _refresh_podcast_table(self):
        self.podcast_tree.delete(*self.podcast_tree.get_children())
        for item in self.b_podcasts:
            pdf = item.get("pdf", "")
            checked = "☑" if pdf in self.selected_pdfs else "☐"
            self.podcast_tree.insert(
                "",
                tk.END,
                iid=pdf,
                values=(checked, item.get("status", "未绑定"), pdf, item.get("title", ""), item.get("duration", "")),
            )
        self.b_count_var.set(f"播客 {len(self.b_podcasts)} 个，已选 {len(self.selected_pdfs)} 个")

    def on_podcast_click(self, event):
        if self.podcast_tree.identify("region", event.x, event.y) != "cell":
            return
        item = self.podcast_tree.identify_row(event.y)
        if item:
            self.toggle_podcast(item)

    def on_podcast_double_click(self, event):
        item = self.podcast_tree.identify_row(event.y)
        if item:
            self.toggle_podcast(item)

    def toggle_podcast(self, pdf):
        if pdf in self.selected_pdfs:
            self.selected_pdfs.remove(pdf)
        else:
            self.selected_pdfs.add(pdf)
        self._refresh_podcast_table()

    def select_failed_podcasts(self):
        self.selected_pdfs = {
            item.get("pdf", "")
            for item in self.b_podcasts
            if item.get("status") in {"未绑定", "绑定失败", "缺少音频", "已下载未绑定"}
        }
        self._refresh_podcast_table()

    def scan_b_link(self):
        url = self.b_link_var.get().strip()
        if not url:
            messagebox.showwarning("缺少链接", "请先选择或刷新历史豆包链接。")
            return
        if self.worker_thread and self.worker_thread.is_alive():
            messagebox.showinfo("任务运行中", "当前已有任务在运行。")
            return
        self.progress.start(10)
        self.stop_requested = False
        self.stop_button.configure(state=tk.NORMAL)
        self.status_var.set("正在扫描豆包链接播客...")
        self.worker_thread = threading.Thread(target=self._run_scan_b_worker, args=(url,), daemon=True)
        self.worker_thread.start()

    def _run_scan_b_worker(self, url):
        writer = QueueWriter(self.log_queue)
        ok = False
        try:
            with redirect_stdout(writer), redirect_stderr(writer):
                if not HELPER_PYTHON:
                    raise RuntimeError("找不到可用的 python.exe，请设置 DOUBAO_PYTHON_EXE")
                cmd = [HELPER_PYTHON, str(RESOURCE_DIR / "doubao_scanner.py"), url]
                self.current_process = subprocess.Popen(cmd, text=True, encoding="utf-8", errors="replace")
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
        url = self.b_link_var.get().strip()
        selected = [pdf for pdf in self.selected_pdfs if pdf]
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
        self.worker_thread = threading.Thread(target=self._run_b_retry_worker, args=(url, selected), daemon=True)
        self.worker_thread.start()

    def start_b_full(self):
        url = self.b_link_var.get().strip()
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
        self.worker_thread = threading.Thread(target=self._run_b_full_worker, args=(url,), daemon=True)
        self.worker_thread.start()

    def _run_b_full_worker(self, url):
        writer = QueueWriter(self.log_queue)
        ok = False
        try:
            with redirect_stdout(writer), redirect_stderr(writer):
                script_dir = RESOURCE_DIR
                steps = [
                    [HELPER_PYTHON, str(script_dir / "doubao_scanner.py"), url],
                    [HELPER_PYTHON, str(script_dir / "doubao_downloader.py"), url, "--all"],
                    [HELPER_PYTHON, str(script_dir / "post_process.py"), "--bind-existing"],
                ]
                ok = True
                for cmd in steps:
                    if self.stop_requested:
                        ok = False
                        break
                    if not HELPER_PYTHON:
                        raise RuntimeError("找不到可用的 python.exe，请设置 DOUBAO_PYTHON_EXE")
                    self.current_process = subprocess.Popen(cmd, text=True, encoding="utf-8", errors="replace")
                    result_code = self.current_process.wait()
                    self.current_process = None
                    if result_code != 0:
                        ok = False
        except Exception as exc:
            self.log_queue.put(("log", f"\n[B错误] 全流程失败: {exc}\n"))
        finally:
            self.log_queue.put(("done", ok))

    def _run_b_retry_worker(self, url, selected_pdfs):
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
                self.current_process = subprocess.Popen(downloader_cmd, text=True, encoding="utf-8", errors="replace")
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
                    self.current_process = subprocess.Popen(post_cmd, text=True, encoding="utf-8", errors="replace")
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

        self._apply_vault_to_pipeline()
        browser_visible = self.browser_visible_var.get()
        self.start_button.configure(state=tk.DISABLED)
        self.stop_requested = False
        pipeline.clear_stop_request()
        self.stop_button.configure(state=tk.NORMAL)
        self.progress.start(10)
        self.status_var.set(f"生成中：{len(selected)} 个 Markdown")
        self._append_log(f"\n[任务] 开始生成播客，文件数：{len(selected)}\n")

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
                elif kind == "done":
                    self.progress.stop()
                    self.start_button.configure(state=tk.NORMAL)
                    self.stop_button.configure(state=tk.DISABLED)
                    self.current_process = None
                    pipeline.clear_stop_request()
                    self.status_var.set("任务完成" if payload else "任务结束：存在失败或中断")
                    self._append_log("\n[任务] 流程结束\n")
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
        self.b_podcasts = []
        for item in scanned:
            pdf = self._normalize_pdf_name(item.get("pdf", ""))
            stem = self._stem_from_pdf(pdf)
            self.b_podcasts.append({
                "pdf": pdf,
                "title": item.get("title", ""),
                "duration": item.get("duration", ""),
                "status": self._status_for_stem(url, stem),
            })
        self.selected_pdfs.clear()
        self._refresh_podcast_table()
        self.notebook.select(1)
        self.status_var.set(f"B 扫描完成：{len(self.b_podcasts)} 个播客")
        self._append_log(f"[B] 扫描完成：{len(self.b_podcasts)} 个播客\n")

    def clear_log(self):
        self.log_text.delete("1.0", tk.END)


def main():
    app = DoubaoFrontend()
    app.mainloop()


if __name__ == "__main__":
    main()
