#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
doubao_pipeline.py — 豆包播客全自动流水线（交互式入口）

整合流程：
    Markdown → PDF → 上传豆包 → 生成播客 → 保存聊天地址
    读取聊天地址 → 扫描 → 下载 → 压缩MP3 → 绑定Obsidian Markdown

用法：
    python doubao_pipeline.py

模式：
    [a] 生成播客 — Markdown 转 PDF → 上传 → 点击"生成播客"
    [b] 下载播客 — 扫描下载已有播客 → 压缩 → 绑定 Markdown
"""

import asyncio
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from playwright.async_api import async_playwright

from bridge_json_log import LATEST_A_FILE, now_text, task_id, write_task_log
from md2pdf import md_to_pdf

# 直接复用 uploader 的核心函数（pipeline 自己控制浏览器生命周期）
from doubao_uploader import (
    upload_pdf,
    click_generate_podcast,
    wait_for_podcast,
    ensure_chat_open,
    load_progress,
    save_progress,
    take_debug_screenshot,
    STATE_FILE as DOUBAO_STATE_FILE,
    DEFAULT_URL,
    DEFAULT_WAIT_TIMEOUT,
    log as uploader_log,
    log_error as uploader_log_error,
    log_warn as uploader_log_warn,
    log_success as uploader_log_success,
)

# ============ 上传/下载绑定记录 ============
OBSIDIAN_VAULT = Path(os.environ.get("DOUBAO_OBSIDIAN_VAULT", r"E:\Obsidian\主仓库"))
RECORD_FILE = OBSIDIAN_VAULT / "总报告" / "豆包播客代码上传与下载绑定记录.md"


def _ensure_record_file():
    """确保记录文件存在，不存在则创建标题"""
    if not RECORD_FILE.exists():
        RECORD_FILE.parent.mkdir(parents=True, exist_ok=True)
        header = (
            "# 豆包播客上传与下载绑定记录\n\n"
            "---\n\n"
        )
        with open(RECORD_FILE, "w", encoding="utf-8") as f:
            f.write(header)


def append_upload_record(filename: str, chat_url: str):
    """追加一条上传成功记录（单条模式，下载绑定用）"""
    _ensure_record_file()
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    entry = (
        f"### [上传] {now}\n\n"
        f"- **文件名**: `{filename}`\n"
        f"- **聊天链接**: [{chat_url}]({chat_url})\n"
        f"- **状态**: ✅ 上传成功\n\n"
        f"---\n\n"
    )
    with open(RECORD_FILE, "a", encoding="utf-8") as f:
        f.write(entry)
    print(f"  📝 已记录到: {RECORD_FILE.name}")


def write_batch_upload_records(filenames: list, chat_url: str):
    """批量写入上传记录（所有完成后一次性写入，避免逐个写文件卡顿）
    文件列表使用 Obsidian WikiLink 格式 [[文件名]]，可直接点击跳转到对应 Markdown。"""
    if not filenames:
        return
    _ensure_record_file()
    now = time.strftime("%Y-%m-%d %H:%M:%S")

    # 读取 md_mapping.json，把 PDF 文件名映射回 Markdown 文件名
    md_mapping = {}
    mapping_file = SCRIPT_DIR / "md_mapping.json"
    if mapping_file.exists():
        try:
            with open(mapping_file, "r", encoding="utf-8") as f:
                md_mapping = json.load(f)
        except Exception:
            pass

    lines = [
        f"### [批量上传] {now}\n\n",
        f"- **聊天链接**: [{chat_url}]({chat_url})\n",
        f"- **文件数量**: {len(filenames)} 个\n",
        f"- **文件列表**:\n",
    ]
    for name in filenames:
        md_path = md_mapping.get(name)
        if md_path:
            md_name = Path(md_path).stem
            lines.append(f"  - [[{md_name}]]\n")
        else:
            lines.append(f"  - `{name}`\n")
    lines.append(f"- **状态**: ✅ 全部上传成功\n\n")
    lines.append("---\n\n")
    with open(RECORD_FILE, "a", encoding="utf-8") as f:
        f.writelines(lines)
    print(f"  📝 批量上传记录已写入: {RECORD_FILE.name} ({len(filenames)} 个文件)")


def append_download_bind_record(filename: str, chat_url: str = ""):
    """追加一条下载绑定成功记录"""
    _ensure_record_file()
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    url_line = f"- **聊天链接**: [{chat_url}]({chat_url})\n" if chat_url else ""
    entry = (
        f"### [下载绑定] {now}\n\n"
        f"- **文件名**: `{filename}`\n"
        f"{url_line}"
        f"- **状态**: ✅ 绑定成功\n\n"
        f"---\n\n"
    )
    with open(RECORD_FILE, "a", encoding="utf-8") as f:
        f.write(entry)
    print(f"  📝 已记录到: {RECORD_FILE.name}")


# ============ 路径配置 ============
SCRIPT_DIR = Path(os.environ.get("DOUBAO_BRIDGE_APP_DIR", Path(__file__).parent))
MD2PDF_SCRIPT = SCRIPT_DIR / "md2pdf.py"
UPLOADER_SCRIPT = SCRIPT_DIR / "doubao_uploader.py"
SCANNER_SCRIPT = SCRIPT_DIR / "doubao_scanner.py"
DOWNLOADER_SCRIPT = SCRIPT_DIR / "doubao_downloader.py"
POST_PROCESS_SCRIPT = SCRIPT_DIR / "post_process.py"
FULL_SCRIPT = SCRIPT_DIR / "doubao_full.py"
STATE_FILE = SCRIPT_DIR / "pipeline_state.json"
UPLOAD_PROGRESS_FILE = SCRIPT_DIR / "upload_progress.json"
DEFAULT_MARKDOWN_SCAN_DIR = os.environ.get(
    "DOUBAO_MD_SCAN_DIR",
    str(OBSIDIAN_VAULT),
)
SCAN_EXCLUDED_DIRS = {
    ".git",
    ".obsidian",
    "__pycache__",
    "node_modules",
    ".venv",
    "venv",
    "pdf_output",
    "doubao_debug",
}

PYTHON_EXE = sys.executable
STOP_REQUESTED = False


def request_stop():
    """请求正在运行的流程尽快停止。"""
    global STOP_REQUESTED
    STOP_REQUESTED = True


def clear_stop_request():
    """清除停止请求。"""
    global STOP_REQUESTED
    STOP_REQUESTED = False


def should_stop():
    return STOP_REQUESTED


def load_state():
    """读取流水线状态（URL、时间、PDF列表等）"""
    if STATE_FILE.exists():
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_state(state):
    """保存流水线状态"""
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"⚠️  保存状态失败: {e}")


def is_real_doubao_chat_url(url: str) -> bool:
    """判断是否为可复用的豆包真实聊天地址，排除 chat/local_ 临时地址。"""
    if not url:
        return False
    return bool(re.match(r"^https://www\.doubao\.com/chat/(?!local_)\d+", url.strip()))


def get_saved_chat_url():
    """读取已保存且有效的真实聊天地址。"""
    chat_url_txt = SCRIPT_DIR / "chat_url.txt"
    if chat_url_txt.exists():
        try:
            url = chat_url_txt.read_text(encoding="utf-8").strip()
            if is_real_doubao_chat_url(url):
                return url
        except Exception:
            pass

    state = load_state()
    url = state.get("chat_url", "")
    if is_real_doubao_chat_url(url):
        return url
    return ""


async def wait_for_real_chat_url(page, timeout=12):
    """等待豆包把 chat/local_ 临时地址切换成真实聊天地址。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        current_url = page.url
        if is_real_doubao_chat_url(current_url):
            return current_url
        await asyncio.sleep(1)
    return page.url


def save_chat_url_if_real(url: str) -> bool:
    """只保存真实聊天地址，避免 local_ 临时地址覆盖正确记录。"""
    if not is_real_doubao_chat_url(url):
        print(f"⚠️  当前页面仍是临时聊天地址，未覆盖已保存地址: {url}")
        return False
    url_file = SCRIPT_DIR / "chat_url.txt"
    try:
        with open(url_file, "w", encoding="utf-8") as f:
            f.write(url)
        print(f"聊天地址已保存: {url_file} -> {url}")
        return True
    except Exception as e:
        print(f"⚠️  保存聊天地址失败: {e}")
        return False


def _get_paths_from_clipboard_hdrop():
    """
    从 Windows 剪贴板读取 CF_HDROP 格式（文件拖放格式）。
    这是从文件资源管理器复制文件时最可靠的方式，路径不会被截断。
    使用 ctypes（标准库），无需额外安装。
    """
    try:
        import ctypes
        from ctypes import wintypes

        # Windows API 常量
        CF_HDROP = 15

        # 加载 DLL
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        shell32 = ctypes.windll.shell32

        # 函数原型
        user32.OpenClipboard.argtypes = [wintypes.HWND]
        user32.OpenClipboard.restype = wintypes.BOOL
        user32.CloseClipboard.restype = wintypes.BOOL
        user32.GetClipboardData.argtypes = [wintypes.UINT]
        user32.GetClipboardData.restype = wintypes.HANDLE
        kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
        kernel32.GlobalLock.restype = wintypes.LPVOID
        kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
        kernel32.GlobalUnlock.restype = wintypes.BOOL

        # DragQueryFile 需要 shell32
        shell32.DragQueryFileW.argtypes = [wintypes.HANDLE, wintypes.UINT, wintypes.LPWSTR, wintypes.UINT]
        shell32.DragQueryFileW.restype = wintypes.UINT

        if not user32.OpenClipboard(None):
            return []

        try:
            hDrop = user32.GetClipboardData(CF_HDROP)
            if not hDrop:
                return []

            # 获取文件数量
            file_count = shell32.DragQueryFileW(hDrop, 0xFFFFFFFF, None, 0)
            paths = []
            buffer = ctypes.create_unicode_buffer(260)
            for i in range(file_count):
                shell32.DragQueryFileW(hDrop, i, buffer, 260)
                path = buffer.value
                if path:
                    paths.append(path)
            return paths
        finally:
            user32.CloseClipboard()
    except Exception:
        return []


def _get_paths_from_clipboard():
    """
    从 Windows 剪贴板读取文件路径。
    优先级：
      1. CF_HDROP（文件资源管理器复制，最可靠，不会截断）
      2. pyperclip（纯文本复制，Unicode 支持好）
      3. win32clipboard（Windows 原生 API）
      4. tkinter（标准库回退）
    """
    paths = []

    # 方案1：CF_HDROP（从文件资源管理器复制文件时的原生格式）
    paths = _get_paths_from_clipboard_hdrop()
    if paths:
        return paths

    # 方案2~4：读取纯文本，按行解析
    text = ""

    # 方案2：pyperclip
    try:
        import pyperclip
        text = pyperclip.paste()
    except Exception:
        pass

    # 方案3：win32clipboard CF_UNICODETEXT
    if not text:
        try:
            import win32clipboard
            win32clipboard.OpenClipboard()
            if win32clipboard.IsClipboardFormatAvailable(win32clipboard.CF_UNICODETEXT):
                text = win32clipboard.GetClipboardData(win32clipboard.CF_UNICODETEXT)
            win32clipboard.CloseClipboard()
        except Exception:
            pass

    # 方案4：tkinter
    if not text:
        try:
            import tkinter as tk
            root = tk.Tk()
            root.withdraw()
            try:
                text = root.clipboard_get()
            except tk.TclError:
                pass
            finally:
                root.destroy()
        except Exception:
            pass

    if not text or not isinstance(text, str):
        return []

    # 解析纯文本中的路径
    for line in text.strip().splitlines():
        item = line.strip().strip('"').strip("'")
        if not item or len(item) < 3:
            continue
        # 过滤掉标题行
        if '（' in item and '）' in item and ('个' in item or '目录' in item):
            continue
        if item.startswith('C:') or item.startswith('D:') or item.startswith('E:'):
            paths.append(item)
        elif ':\\' in item or ':/' in item:
            paths.append(item)
        elif '/' in item or '\\' in item:
            paths.append(item)
    return paths


def _get_paths_from_file(file_path):
    """从文本文件读取路径列表（每行一个路径）"""
    paths = []
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            for line in f:
                item = line.strip().strip('"').strip("'")
                if not item or item.startswith('#'):
                    continue
                if len(item) < 3:
                    continue
                paths.append(item)
    except Exception as e:
        print(f"⚠️  读取文件失败: {e}")
    return paths


def _format_file_time(timestamp):
    """把文件时间戳格式化为便于选择时查看的文本。"""
    try:
        return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return "-"


def _get_best_file_time(path):
    """Windows 上优先用创建时间，其他情况用较新的修改时间兜底。"""
    stat = path.stat()
    return max(getattr(stat, "st_ctime", 0), getattr(stat, "st_mtime", 0))


def scan_markdown_files(scan_dir, limit=80):
    """扫描目录下的 Markdown 文件，按最新添加/修改时间倒序排列。"""
    root = Path(scan_dir).expanduser()
    if not root.exists() or not root.is_dir():
        print(f"❌ 扫描目录不存在或不是文件夹: {root}")
        return []

    files = []
    for md_file in root.rglob("*"):
        try:
            if md_file.is_dir():
                continue
            if any(part in SCAN_EXCLUDED_DIRS for part in md_file.parts):
                continue
            if md_file.suffix.lower() not in (".md", ".markdown"):
                continue
            if "未命名" in md_file.stem:
                continue
            best_time = _get_best_file_time(md_file)
            files.append((best_time, md_file.resolve()))
        except Exception:
            continue

    files.sort(key=lambda item: item[0], reverse=True)
    if limit and limit > 0:
        files = files[:limit]
    return files


def _parse_selection(selection, total):
    """解析 1,3,5-8 / all 这类终端选择。"""
    selection = selection.strip().lower()
    if selection in ("all", "a", "*"):
        return list(range(total))

    indexes = set()
    for part in selection.replace("，", ",").split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            start_text, end_text = part.split("-", 1)
            if not start_text.strip().isdigit() or not end_text.strip().isdigit():
                raise ValueError(part)
            start = int(start_text)
            end = int(end_text)
            if start > end:
                start, end = end, start
            for number in range(start, end + 1):
                if 1 <= number <= total:
                    indexes.add(number - 1)
            continue
        if not part.isdigit():
            raise ValueError(part)
        number = int(part)
        if 1 <= number <= total:
            indexes.add(number - 1)

    return sorted(indexes)


def choose_markdown_files_from_scan():
    """扫描文件夹并让用户选择要走模式 A 的 Markdown 文件。"""
    default_dir = DEFAULT_MARKDOWN_SCAN_DIR
    print("\n📁 扫描 Markdown 文件")
    print(f"默认目录: {default_dir}")
    try:
        scan_dir = input("请输入要扫描的文件夹（回车使用默认，q 取消）: ").strip()
    except (EOFError, KeyboardInterrupt):
        return []
    if scan_dir.lower() == "q":
        return []
    if not scan_dir:
        scan_dir = default_dir

    try:
        limit_text = input("最多显示多少个最新文件？（默认 80）: ").strip()
    except (EOFError, KeyboardInterrupt):
        limit_text = ""
    limit = 80
    if limit_text:
        try:
            limit = max(1, int(limit_text))
        except ValueError:
            print("⚠️  数量无效，使用默认 80。")

    scanned = scan_markdown_files(scan_dir, limit=limit)
    if not scanned:
        print("⚠️  没有扫描到可处理的 Markdown 文件。")
        return []

    root = Path(scan_dir).expanduser()
    print(f"\n✅ 找到 {len(scanned)} 个最新 Markdown 文件（新到旧）:")
    for i, (timestamp, path) in enumerate(scanned, 1):
        try:
            display_path = path.relative_to(root)
        except ValueError:
            display_path = path
        print(f"   [{i:>2}] {_format_file_time(timestamp)}  {display_path}")

    print("\n选择要生成播客的文件编号，支持 1,3,5-8；输入 all 全选；输入 q 取消。")
    while True:
        try:
            selection = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            return []
        if not selection or selection.lower() == "q":
            return []
        try:
            indexes = _parse_selection(selection, len(scanned))
        except ValueError:
            print("⚠️  选择格式无效，请重新输入，例如 1,3,5-8 或 all。")
            continue
        if not indexes:
            print("⚠️  没有选中任何有效编号，请重新输入。")
            continue
        return [str(scanned[i][1]) for i in indexes]


def print_banner():
    print("=" * 60)
    print("  🎙️  豆包播客全自动流水线")
    print("=" * 60)
    print()
    print("  [a] 生成播客 — Markdown → PDF → 上传 → 生成播客")
    print("  [b] 下载播客 — 扫描 → 下载 → 压缩 → 绑定 Markdown")
    print()


async def scroll_to_bottom(page):
    """滚动页面到底部，同时处理 body 和所有可滚动容器，确保 '+' 按钮可见"""
    print("  📜 开始滚动页面到底部...")
    try:
        # 方式1: 反复滚动 body / documentElement（DOM 更新后 scrollHeight 可能变化）
        prev_scroll = None
        for _ in range(5):
            await page.evaluate("""
                () => {
                    const h = Math.max(
                        document.body.scrollHeight,
                        document.documentElement.scrollHeight
                    );
                    window.scrollTo(0, h);
                    document.documentElement.scrollTop = h;
                    document.body.scrollTop = h;
                }
            """)
            await asyncio.sleep(0.3)
            current_scroll = await page.evaluate(
                "() => document.documentElement.scrollTop || document.body.scrollTop || 0"
            )
            if prev_scroll is not None and abs(current_scroll - prev_scroll) < 5:
                break
            prev_scroll = current_scroll

        # 方式2: 找到所有 overflow-y: auto/scroll 的容器并滚到底
        await page.evaluate("""
            () => {
                const els = Array.from(document.querySelectorAll('*'))
                    .filter(el => {
                        const s = window.getComputedStyle(el);
                        return (s.overflowY === 'auto' || s.overflowY === 'scroll')
                            && el.scrollHeight > el.clientHeight;
                    });
                els.forEach(el => { el.scrollTop = el.scrollHeight; });
            }
        """)
        await asyncio.sleep(0.3)

        # 方式3: 模拟 End 键兜底
        await page.keyboard.press('End')
        await asyncio.sleep(0.2)

        print("  ✅ 页面已滚动到底部")
    except Exception as e:
        print(f"  ⚠️  滚动到底部失败: {e}")


async def run_generate_flow(pdf_files, browser_visible=True):
    """
    直接调用 uploader 函数逐个上传 PDF 并生成播客。
    与原来的 subprocess 方式的区别：
    每个 PDF 成功生成播客后，会自动滚动页面到底部，
    确保下一个 '+' 按钮可见，避免页面过长导致按钮点不到。
    """
    progress = load_progress()
    # 默认不跳过已处理的文件（和原来 subprocess.run 不带 --resume 的行为一致）
    processed = set()
    failed = set(progress.get("failed", []))
    upload_success_list = []

    pending = [str(p) for p in pdf_files]
    if not pending:
        print("没有可处理的 PDF 文件")
        return {"ok": True, "processed": [], "failed": [], "chat_url": "", "upload_success": []}

    print(f"待处理 PDF 数量: {len(pending)}")
    print("启动浏览器...")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=not browser_visible)

        if DOUBAO_STATE_FILE.exists():
            print(f"加载登录态: {DOUBAO_STATE_FILE}")
            context = await browser.new_context(storage_state=str(DOUBAO_STATE_FILE))
        else:
            print("⚠️  未找到登录态文件，将以未登录状态启动")
            context = await browser.new_context()

        page = await context.new_page()
        page.set_default_timeout(30000)

        print(f"导航到: {DEFAULT_URL}")
        await page.goto(DEFAULT_URL)
        await asyncio.sleep(3)

        if not await ensure_chat_open(page):
            print("❌ 无法打开聊天窗口，退出")
            await browser.close()
            return {"ok": False, "processed": [], "failed": pending, "chat_url": "", "upload_success": []}

        # 会话级播客计数器（解决虚拟滚动导致 DOM 计数不准的问题）
        session_podcast_count = 0
        for idx, pdf_path in enumerate(pending, 1):
            if should_stop():
                print("⚠️  已请求停止，后续 PDF 不再处理。")
                failed.update(Path(p).name for p in pending[idx - 1:])
                break
            pdf_path = Path(pdf_path)
            print(f"\n{'='*50}")
            print(f"[{idx}/{len(pending)}] 处理: {pdf_path.name}")
            print(f"{'='*50}")

            # 上传 PDF（失败时重试 3 次）
            upload_ok = False
            for attempt in range(3):
                if await upload_pdf(page, pdf_path):
                    upload_ok = True
                    break
                uploader_log_warn(f"上传失败，第 {attempt + 1}/3 次重试...")
                await asyncio.sleep(5)
            if not upload_ok:
                uploader_log_error(f"上传失败 3 次，跳过: {pdf_path.name}")
                failed.add(pdf_path.name)
                progress["failed"] = list(failed)
                save_progress(progress)
                continue

            # 点击"生成播客"（失败时重试 3 次）
            gen_ok = False
            for attempt in range(3):
                if await click_generate_podcast(page):
                    gen_ok = True
                    break
                uploader_log_warn(f"点击'生成播客'失败，第 {attempt + 1}/3 次重试...")
                await asyncio.sleep(3)
            if not gen_ok:
                uploader_log_error(f"点击'生成播客'失败 3 次，跳过: {pdf_path.name}")
                failed.add(pdf_path.name)
                progress["failed"] = list(failed)
                save_progress(progress)
                continue

            # 点击生成后，给豆包时间开始生成，不逐个等待完成（避免轮询卡顿）
            print("  ⏳ 等待 10 秒让播客开始生成...")
            await asyncio.sleep(10)

            # 滚动页面让新卡片进入视口（触发虚拟滚动加载）
            print("  📜 滚动页面到底部...")
            await scroll_to_bottom(page)

            # 标记为已处理（不逐个检测播客是否生成完成，最后统一看）
            processed.add(pdf_path.name)
            if pdf_path.name in failed:
                failed.remove(pdf_path.name)
            session_podcast_count += 1
            upload_success_list.append(pdf_path.name)

            # 保存进度
            progress["processed"] = list(processed)
            progress["failed"] = list(failed)
            save_progress(progress)

            # 如果不是最后一个，等待几秒继续下一个
            if idx < len(pending):
                print("  ⏳ 等待 3 秒后处理下一个 PDF...")
                await asyncio.sleep(3)

        # 保存当前聊天页面 URL，供后续下载使用；避免 local_ 临时地址覆盖真实地址
        current_url = await wait_for_real_chat_url(page)
        saved_real_url = current_url if save_chat_url_if_real(current_url) else get_saved_chat_url()

        # 所有 PDF 上传+点击生成完成后，统一等待剩余播客生成
        if upload_success_list:
            print("\n所有 PDF 已上传并触发生成，统一等待 30 秒...")
            await asyncio.sleep(30)
            await scroll_to_bottom(page)
            try:
                cards = await page.query_selector_all('[data-plugin-identifier*="receive-podcast-content"]')
                total = len(cards)
                print(f"当前页面播客卡片总数: {total} 个（上传了 {len(upload_success_list)} 个）")
                if total < len(upload_success_list):
                    print(f"⚠️  部分播客可能尚未生成完成（{total}/{len(upload_success_list)}）")
                else:
                    print("✅ 播客卡片数量与上传数量一致")
            except Exception as e:
                print(f"⚠️  统一检测失败: {e}")

            # 一次性写入上传记录
            write_batch_upload_records(upload_success_list, saved_real_url or page.url)

        await take_debug_screenshot(page, "upload_complete")
        await context.close()
        await browser.close()

    print(f"\n{'='*50}")
    print("所有 PDF 处理完毕")
    print(f"成功: {len(processed)} 个")
    print(f"失败: {len(failed)} 个")
    print(f"{'='*50}")

    return {
        "ok": len(failed) == 0,
        "processed": sorted(processed),
        "failed": sorted(failed),
        "chat_url": get_saved_chat_url(),
        "upload_success": upload_success_list,
    }


def run_generate_for_markdown_files(md_files, browser_visible=True):
    """给前端调用的模式 A 入口：对指定 Markdown 列表执行生成播客流程。"""
    if not MD2PDF_SCRIPT.exists():
        print(f"❌ 未找到 md2pdf 工具: {MD2PDF_SCRIPT}")
        print("   请确认 md2pdf.py 已放在本目录下")
        return False

    valid_md = []
    for f in md_files:
        p = Path(str(f).strip().strip('"').strip("'"))
        if not p.exists():
            print(f"⚠️  跳过（不存在）: {p}")
            continue
        if p.suffix.lower() not in (".md", ".markdown"):
            print(f"⚠️  跳过（非 Markdown）: {p}")
            continue
        if "未命名" in p.stem:
            print(f"⚠️  跳过（未命名文件）: {p.name}")
            continue
        valid_md.append(str(p))

    if not valid_md:
        print("没有可处理的 Markdown 文件。")
        return False

    print(f"\n📄 待处理: {len(valid_md)} 个 Markdown 文件")
    for i, f in enumerate(valid_md, 1):
        print(f"   [{i}] {Path(f).name}")

    print("\n" + "-" * 40)
    print("[步骤 1/3] Markdown → PDF 转换...")
    print("-" * 40)

    pdf_output_dir = str(SCRIPT_DIR / "pdf_output")
    pdf_output_path = Path(pdf_output_dir)
    convert_ok = True
    for idx, md_path in enumerate(valid_md, 1):
        if should_stop():
            print("⚠️  已请求停止，PDF 转换中断。")
            convert_ok = False
            break
        print(f"[{idx}/{len(valid_md)}] {Path(md_path).name}")
        if not md_to_pdf(Path(md_path), pdf_output_path):
            convert_ok = False

    if not convert_ok:
        print("❌ PDF 转换失败，退出。")
        return False

    pdf_dir = Path(pdf_output_dir)
    pdf_files = []
    md_mapping = {}
    for md_path in valid_md:
        expected_pdf = pdf_dir / f"{Path(md_path).stem}.pdf"
        if expected_pdf.exists():
            pdf_files.append(expected_pdf)
            md_mapping[expected_pdf.name] = str(Path(md_path).resolve())

    if not pdf_files:
        print(f"❌ 在 {pdf_output_dir} 中未找到本次生成的 PDF 文件。")
        return False

    mapping_file = SCRIPT_DIR / "md_mapping.json"
    try:
        with open(mapping_file, "w", encoding="utf-8") as f:
            json.dump(md_mapping, f, ensure_ascii=False, indent=2)
        print(f"📋 Markdown 路径映射已保存: {mapping_file}")
    except Exception as e:
        print(f"⚠️  保存路径映射失败: {e}")

    print(f"\n📁 本次生成 {len(pdf_files)} 个 PDF 文件:")
    for p in pdf_files:
        print(f"   • {p.name}")

    print("\n" + "-" * 40)
    print("[步骤 2/3] 上传 PDF 到豆包并生成播客...")
    print("-" * 40)
    print("⚠️  即将打开浏览器，请确保已登录豆包。")
    print(f"   浏览器模式: {'可见' if browser_visible else '隐藏'}\n")

    flow_result = {
        "ok": False,
        "processed": [],
        "failed": [],
        "chat_url": "",
        "upload_success": [],
    }
    has_failures = False
    try:
        flow_result = asyncio.run(run_generate_flow(pdf_files, browser_visible=browser_visible))
        if not flow_result.get("ok"):
            has_failures = True
    except KeyboardInterrupt:
        print("\n⚠️  用户中断，进度已保存。")
        has_failures = True
    except Exception as e:
        print(f"\n⚠️  上传/生成过程中异常: {e}")
        has_failures = True

    if has_failures:
        print("\n⚠️  上传/生成过程中可能有部分失败。")
        print("   可使用断点续传: python doubao_uploader.py <PDF目录> --resume")

    print("\n" + "-" * 40)
    print("[步骤 3/3] 保存聊天地址...")
    print("-" * 40)

    saved_url = get_saved_chat_url()

    state = load_state()
    if saved_url:
        state["chat_url"] = saved_url
        state["pdfs"] = [p.name for p in pdf_files]
        state["last_run"] = time.strftime("%Y-%m-%d %H:%M:%S")
        save_state(state)
        print(f"✅ 状态已保存到 pipeline_state.json")
        print(f"   URL: {saved_url}")
        print(f"   本次上传 PDF: {len(pdf_files)} 个")
        for p in pdf_files:
            print(f"      • {p.name}")
        print("\n💡 输入 [b] 即可自动下载该聊天中的所有播客。")
    else:
        print("⚠️  未找到保存的聊天地址，请在浏览器中手动复制地址。")

    a_log = {
        "task_id": task_id("a_generate"),
        "task_type": "A_GENERATE_PODCAST",
        "created_at": now_text(),
        "status": "success" if not has_failures else "partial_or_failed",
        "browser_visible": browser_visible,
        "chat_url": saved_url or flow_result.get("chat_url", ""),
        "markdown_files": [
            {"path": str(Path(md).resolve()), "name": Path(md).name, "stem": Path(md).stem}
            for md in valid_md
        ],
        "pdf_files": [
            {
                "path": str(pdf.resolve()),
                "name": pdf.name,
                "markdown_path": md_mapping.get(pdf.name, ""),
                "uploaded": pdf.name in set(flow_result.get("upload_success", [])),
                "failed": pdf.name in set(flow_result.get("failed", [])),
            }
            for pdf in pdf_files
        ],
        "uploaded_pdfs": flow_result.get("upload_success", []),
        "failed_pdfs": flow_result.get("failed", []),
    }
    try:
        log_path = write_task_log(a_log, latest_file=LATEST_A_FILE)
        print(f"🧾 A流程 JSON 记录已保存: {log_path}")
    except Exception as e:
        print(f"⚠️  保存 A流程 JSON 记录失败: {e}")

    return not has_failures


def mode_generate():
    """模式 A：生成播客"""
    print("\n" + "=" * 60)
    print("  模式 [a]：生成播客")
    print("=" * 60)
    print()

    # 步骤0：检查 md2pdf 是否存在
    if not MD2PDF_SCRIPT.exists():
        print(f"❌ 未找到 md2pdf 工具: {MD2PDF_SCRIPT}")
        print("   请确认 md2pdf.py 已放在本目录下")
        return

    # 步骤1：获取 Markdown 文件路径
    # 优先尝试从 Windows 剪贴板读取（支持一次性复制多个文件）
    md_files = _get_paths_from_clipboard()

    if md_files:
        print(f"\n📋 从剪贴板检测到 {len(md_files)} 个路径:")
        for i, p in enumerate(md_files[:10], 1):
            display = p if len(p) < 70 else p[:67] + "..."
            print(f"   [{i}] {display}")
        if len(md_files) > 10:
            print(f"   ... 还有 {len(md_files) - 10} 个")
        try:
            use_clipboard = input("\n是否使用剪贴板中的路径? (y/n/f=从文件读取/s=扫描文件夹): ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            use_clipboard = 'n'
        if use_clipboard == 'f':
            md_files = []
            file_input = True
        elif use_clipboard == 's':
            md_files = choose_markdown_files_from_scan()
            file_input = False
        elif use_clipboard != 'y':
            md_files = []
            file_input = False
        else:
            file_input = False
    else:
        file_input = False
        try:
            source_choice = input("\n未从剪贴板检测到路径。是否扫描文件夹选择 Markdown？(s=扫描/回车=手动粘贴/f=从文件读取): ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            source_choice = ""
        if source_choice == "s":
            md_files = choose_markdown_files_from_scan()
        elif source_choice == "f":
            file_input = True

    # 从文件读取路径
    if not md_files and file_input:
        default_path = SCRIPT_DIR / "paths.txt"
        try:
            file_path = input(f"请输入路径列表文件（默认: {default_path}）: ").strip()
        except (EOFError, KeyboardInterrupt):
            file_path = ""
        if not file_path:
            file_path = str(default_path)
        md_files = _get_paths_from_file(file_path)
        if md_files:
            print(f"\n📄 从文件读取到 {len(md_files)} 个路径:")
            for i, p in enumerate(md_files[:10], 1):
                display = p if len(p) < 70 else p[:67] + "..."
                print(f"   [{i}] {display}")
            if len(md_files) > 10:
                print(f"   ... 还有 {len(md_files) - 10} 个")

    # 剪贴板/文件都未使用或为空，回退到手动输入
    if not md_files:
        print("\n请粘贴 Markdown 文件路径（每行一个，支持拖放）")
        print("输入空行表示结束:\n")
        while True:
            try:
                line = input("> ").strip()
            except (EOFError, KeyboardInterrupt):
                break
            if not line:
                break
            for item in line.split():
                item = item.strip().strip('"').strip("'")
                if item:
                    md_files.append(item)

    # 验证文件
    valid_md = []
    for f in md_files:
        p = Path(f.strip().strip('"').strip("'"))
        if not p.exists():
            print(f"⚠️  跳过（不存在）: {p}")
            continue
        if p.suffix.lower() not in (".md", ".markdown"):
            print(f"⚠️  跳过（非 Markdown）: {p}")
            continue
        # 跳过文件名包含"未命名"的文件
        if "未命名" in p.stem:
            print(f"⚠️  跳过（未命名文件）: {p.name}")
            continue
        valid_md.append(str(p))

    if not valid_md:
        print("没有可处理的 Markdown 文件。")
        return

    print(f"\n📄 待处理: {len(valid_md)} 个 Markdown 文件")
    for i, f in enumerate(valid_md, 1):
        print(f"   [{i}] {Path(f).name}")

    # 步骤2：Markdown → PDF
    print("\n" + "-" * 40)
    print("[步骤 1/3] Markdown → PDF 转换...")
    print("-" * 40)

    pdf_output_dir = str(SCRIPT_DIR / "pdf_output")
    cmd = [PYTHON_EXE, str(MD2PDF_SCRIPT), *valid_md, "-o", pdf_output_dir]
    result = subprocess.run(cmd)

    # 确保 md2pdf 的浏览器进程完全退出，避免与 uploader 冲突
    time.sleep(2)

    if result.returncode != 0:
        print("❌ PDF 转换失败，退出。")
        return

    # 收集生成的 PDF 文件（只取与传入的 Markdown 对应的 PDF，避免混入旧文件）
    pdf_dir = Path(pdf_output_dir)
    pdf_files = []
    md_mapping = {}  # pdf_name -> md_full_path，供后续绑定精确匹配
    for md_path in valid_md:
        expected_pdf = pdf_dir / f"{Path(md_path).stem}.pdf"
        if expected_pdf.exists():
            pdf_files.append(expected_pdf)
            md_mapping[expected_pdf.name] = str(Path(md_path).resolve())

    if not pdf_files:
        print(f"❌ 在 {pdf_output_dir} 中未找到本次生成的 PDF 文件。")
        return

    # 保存 Markdown 路径映射（避免后续绑定因同名文件找错）
    mapping_file = SCRIPT_DIR / "md_mapping.json"
    try:
        with open(mapping_file, "w", encoding="utf-8") as f:
            json.dump(md_mapping, f, ensure_ascii=False, indent=2)
        print(f"📋 Markdown 路径映射已保存: {mapping_file}")
    except Exception as e:
        print(f"⚠️  保存路径映射失败: {e}")

    print(f"\n📁 本次生成 {len(pdf_files)} 个 PDF 文件:")
    for p in pdf_files:
        print(f"   • {p.name}")

    # 步骤3：上传 PDF 到豆包并生成播客
    print("\n" + "-" * 40)
    print("[步骤 2/3] 上传 PDF 到豆包并生成播客...")
    print("-" * 40)
    print("⚠️  即将打开浏览器，请确保已登录豆包。")
    print("   如果是首次运行，请在浏览器内完成登录。\n")

    # 直接调用 uploader 函数（不再开子进程），这样可以在每个 PDF 成功后插入滚动
    flow_result = {
        "ok": False,
        "processed": [],
        "failed": [],
        "chat_url": "",
        "upload_success": [],
    }
    has_failures = False
    try:
        flow_result = asyncio.run(run_generate_flow(pdf_files))
        if not flow_result.get("ok"):
            has_failures = True
    except KeyboardInterrupt:
        print("\n⚠️  用户中断，进度已保存。")
        has_failures = True
    except Exception as e:
        print(f"\n⚠️  上传/生成过程中异常: {e}")
        has_failures = True

    if has_failures:
        print("\n⚠️  上传/生成过程中可能有部分失败。")
        print("   可使用断点续传: python doubao_uploader.py <PDF目录> --resume")

    # 步骤4：保存聊天地址和 PDF 列表到 pipeline_state.json
    print("\n" + "-" * 40)
    print("[步骤 3/3] 保存聊天地址...")
    print("-" * 40)

    # uploader 把 URL 写到了 chat_url.txt，pipeline 把它整合到 pipeline_state.json
    saved_url = get_saved_chat_url()

    state = load_state()
    if saved_url:
        state["chat_url"] = saved_url
        state["pdfs"] = [p.name for p in pdf_files]
        state["last_run"] = time.strftime("%Y-%m-%d %H:%M:%S")
        save_state(state)
        print(f"✅ 状态已保存到 pipeline_state.json")
        print(f"   URL: {saved_url}")
        print(f"   本次上传 PDF: {len(pdf_files)} 个")
        for p in pdf_files:
            print(f"      • {p.name}")
        print("\n💡 输入 [b] 即可自动下载该聊天中的所有播客。")
    else:
        print("⚠️  未找到保存的聊天地址，请在浏览器中手动复制地址。")

    a_log = {
        "task_id": task_id("a_generate"),
        "task_type": "A_GENERATE_PODCAST",
        "created_at": now_text(),
        "status": "success" if not has_failures else "partial_or_failed",
        "browser_visible": True,
        "chat_url": saved_url or flow_result.get("chat_url", ""),
        "markdown_files": [
            {"path": str(Path(md).resolve()), "name": Path(md).name, "stem": Path(md).stem}
            for md in valid_md
        ],
        "pdf_files": [
            {
                "path": str(pdf.resolve()),
                "name": pdf.name,
                "markdown_path": md_mapping.get(pdf.name, ""),
                "uploaded": pdf.name in set(flow_result.get("upload_success", [])),
                "failed": pdf.name in set(flow_result.get("failed", [])),
            }
            for pdf in pdf_files
        ],
        "uploaded_pdfs": flow_result.get("upload_success", []),
        "failed_pdfs": flow_result.get("failed", []),
    }
    try:
        log_path = write_task_log(a_log, latest_file=LATEST_A_FILE)
        print(f"🧾 A流程 JSON 记录已保存: {log_path}")
    except Exception as e:
        print(f"⚠️  保存 A流程 JSON 记录失败: {e}")

    print("\n" + "=" * 60)
    print("  模式 [a] 执行完毕")
    print("=" * 60)


def mode_download():
    """模式 B：下载播客"""
    print("\n" + "=" * 60)
    print("  模式 [b]：下载播客")
    print("=" * 60)
    print()

    # 读取保存的聊天地址
    state = load_state()
    chat_url = state.get("chat_url")
    if chat_url and not is_real_doubao_chat_url(chat_url):
        print(f"⚠️  已保存的地址是豆包临时地址，已忽略:\n   {chat_url}")
        chat_url = None

    if chat_url:
        print(f"📌 已读取上次保存的聊天地址:\n   {chat_url}")
        print(f"   上次上传 PDF: {len(state.get('pdfs', []))} 个")
        print()
        try:
            use_saved = input("是否使用该地址直接下载? (回车=是 / n=换其他地址): ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            use_saved = ''
        if use_saved == 'n':
            chat_url = None

    # 手动输入地址
    if not chat_url:
        print("请输入豆包聊天页面 URL:")
        try:
            chat_url = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n已取消。")
            return

    if not chat_url:
        print("未输入 URL，返回主菜单。")
        return

    # 更新状态中的 URL
    state["chat_url"] = chat_url
    save_state(state)

    print(f"\n🎯 目标地址: {chat_url}\n")

    # 提供两种下载方式
    print("请选择下载方式:")
    print("  [1] 一键完整流程（扫描 → 下载 → 压缩 → 绑定）")
    print("  [2] 分步执行（适合调试）")

    try:
        choice = input("\n请输入 (1/2): ").strip()
    except (EOFError, KeyboardInterrupt):
        print("\n已取消。")
        return

    if choice == "1":
        print("\n" + "-" * 40)
        print("[一键完整流程]")
        print("-" * 40)
        cmd = [PYTHON_EXE, str(FULL_SCRIPT), chat_url]
        subprocess.run(cmd)

    elif choice == "2":
        # 步骤1：扫描
        print("\n" + "-" * 40)
        print("[步骤 1/3] 扫描页面...")
        print("-" * 40)
        cmd = [PYTHON_EXE, str(SCANNER_SCRIPT), chat_url]
        subprocess.run(cmd)

        # 步骤2：下载
        print("\n" + "-" * 40)
        print("[步骤 2/3] 下载全部...")
        print("-" * 40)
        cmd = [PYTHON_EXE, str(DOWNLOADER_SCRIPT), chat_url, "--all"]
        subprocess.run(cmd)

        # 步骤3：后处理
        print("\n" + "-" * 40)
        print("[步骤 3/3] 后处理（压缩 + 绑定）...")
        print("-" * 40)
        cmd = [PYTHON_EXE, str(POST_PROCESS_SCRIPT)]
        subprocess.run(cmd)

    else:
        print("无效选择，返回主菜单。")
        return

    print("\n" + "=" * 60)
    print("  模式 [b] 执行完毕")
    print("=" * 60)


def main():
    while True:
        print_banner()

        try:
            choice = input("请输入模式 (a/b，或 q 退出): ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\n再见！")
            sys.exit(0)

        if choice == 'q':
            print("再见！")
            break
        elif choice == 'a':
            mode_generate()
        elif choice == 'b':
            mode_download()
        else:
            print("\n❌ 无效输入，请输入 a 或 b。\n")

        print("\n")


if __name__ == "__main__":
    main()
