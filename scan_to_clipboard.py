#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
扫描指定目录下今天12点后新增的 md 文件，将完整路径写入 Windows 剪贴板
"""

import os
import sys
from pathlib import Path
from datetime import datetime

# 默认扫描目录
DEFAULT_SCAN_DIR = r"C:\Users\15403\Documents\Obsidian\申论真题"
# 截止时间（今天0:00）
CUTOFF_TIME = datetime(2026, 5, 29, 0, 0, 0)


def scan_md_files(scan_dir):
    """扫描目录下所有今天0点后修改的 md 文件"""
    paths = []
    scan_path = Path(scan_dir)
    if not scan_path.exists():
        print(f"❌ 目录不存在: {scan_dir}")
        return []

    for md_file in scan_path.rglob("*.md"):
        try:
            # 跳过文件名包含"未命名"的文件
            if "未命名" in md_file.stem:
                continue
            mtime = datetime.fromtimestamp(md_file.stat().st_mtime)
            if mtime >= CUTOFF_TIME:
                paths.append(str(md_file))
        except Exception:
            continue

    return sorted(paths)


def copy_to_clipboard(text):
    """将文本写入 Windows 剪贴板"""
    try:
        import pyperclip
        pyperclip.copy(text)
        return True
    except Exception:
        pass

    try:
        import win32clipboard
        win32clipboard.OpenClipboard()
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardText(text, win32clipboard.CF_UNICODETEXT)
        win32clipboard.CloseClipboard()
        return True
    except Exception:
        pass

    # 回退：用 tkinter
    try:
        import tkinter as tk
        root = tk.Tk()
        root.withdraw()
        root.clipboard_clear()
        root.clipboard_append(text)
        root.destroy()
        return True
    except Exception:
        pass

    return False


def main():
    scan_dir = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_SCAN_DIR
    cutoff_str = sys.argv[2] if len(sys.argv) > 2 else CUTOFF_TIME.strftime("%Y-%m-%d %H:%M")

    print(f"📁 扫描目录: {scan_dir}")
    print(f"⏰ 时间筛选: {cutoff_str} 之后")
    print("-" * 50)

    paths = scan_md_files(scan_dir)

    if not paths:
        print("⚠️  没有找到符合条件的 Markdown 文件")
        return

    print(f"✅ 找到 {len(paths)} 个文件:\n")
    for i, p in enumerate(paths, 1):
        print(f"  [{i}] {p}")

    # 写入剪贴板
    text = "\n".join(paths)
    if copy_to_clipboard(text):
        print(f"\n📋 已复制 {len(paths)} 个路径到剪贴板")
        print("   现在可以直接运行 doubao_pipeline.py 了")
    else:
        print("\n❌ 复制到剪贴板失败，请手动复制上面的路径")


if __name__ == "__main__":
    main()
