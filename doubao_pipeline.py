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

import json
import os
import subprocess
import sys
import time
from pathlib import Path

# ============ 路径配置 ============
SCRIPT_DIR = Path(__file__).parent
MD2PDF_SCRIPT = SCRIPT_DIR / "md2pdf.py"
UPLOADER_SCRIPT = SCRIPT_DIR / "doubao_uploader.py"
SCANNER_SCRIPT = SCRIPT_DIR / "doubao_scanner.py"
DOWNLOADER_SCRIPT = SCRIPT_DIR / "doubao_downloader.py"
POST_PROCESS_SCRIPT = SCRIPT_DIR / "post_process.py"
FULL_SCRIPT = SCRIPT_DIR / "doubao_full.py"
STATE_FILE = SCRIPT_DIR / "pipeline_state.json"
UPLOAD_PROGRESS_FILE = SCRIPT_DIR / "upload_progress.json"

PYTHON_EXE = sys.executable


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


def print_banner():
    print("=" * 60)
    print("  🎙️  豆包播客全自动流水线")
    print("=" * 60)
    print()
    print("  [a] 生成播客 — Markdown → PDF → 上传 → 生成播客")
    print("  [b] 下载播客 — 扫描 → 下载 → 压缩 → 绑定 Markdown")
    print()


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
            use_clipboard = input("\n是否使用剪贴板中的路径? (y/n/f=从文件读取): ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            use_clipboard = 'n'
        if use_clipboard == 'f':
            md_files = []
            file_input = True
        elif use_clipboard != 'y':
            md_files = []
            file_input = False
        else:
            file_input = False
    else:
        file_input = False

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
    for md_path in valid_md:
        expected_pdf = pdf_dir / f"{Path(md_path).stem}.pdf"
        if expected_pdf.exists():
            pdf_files.append(expected_pdf)

    if not pdf_files:
        print(f"❌ 在 {pdf_output_dir} 中未找到本次生成的 PDF 文件。")
        return

    print(f"\n📁 本次生成 {len(pdf_files)} 个 PDF 文件:")
    for p in pdf_files:
        print(f"   • {p.name}")

    # 步骤3：上传 PDF 到豆包并生成播客
    print("\n" + "-" * 40)
    print("[步骤 2/3] 上传 PDF 到豆包并生成播客...")
    print("-" * 40)
    print("⚠️  即将打开浏览器，请确保已登录豆包。")
    print("   如果是首次运行，请在浏览器内完成登录。\n")

    pdf_paths = [str(p) for p in pdf_files]
    cmd = [PYTHON_EXE, str(UPLOADER_SCRIPT), *pdf_paths]
    result = subprocess.run(cmd)

    if result.returncode != 0:
        print("\n⚠️  上传/生成过程中可能有部分失败。")
        print("   可使用断点续传: python doubao_uploader.py <PDF目录> --resume")

    # 步骤4：保存聊天地址和 PDF 列表到 pipeline_state.json
    print("\n" + "-" * 40)
    print("[步骤 3/3] 保存聊天地址...")
    print("-" * 40)

    # uploader 把 URL 写到了 chat_url.txt，pipeline 把它整合到 pipeline_state.json
    chat_url_txt = SCRIPT_DIR / "chat_url.txt"
    saved_url = None
    if chat_url_txt.exists():
        with open(chat_url_txt, "r", encoding="utf-8") as f:
            saved_url = f.read().strip()

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
