#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
调试剪贴板内容，看看复制文件时剪贴板里到底有什么
"""

import sys

def check_clipboard():
    results = []

    # 方法1: pyperclip
    try:
        import pyperclip
        text = pyperclip.paste()
        results.append(("pyperclip", text))
    except Exception as e:
        results.append(("pyperclip", f"ERROR: {e}"))

    # 方法2: win32clipboard (CF_UNICODETEXT)
    try:
        import win32clipboard
        win32clipboard.OpenClipboard()
        if win32clipboard.IsClipboardFormatAvailable(win32clipboard.CF_UNICODETEXT):
            text = win32clipboard.GetClipboardData(win32clipboard.CF_UNICODETEXT)
            results.append(("win32clipboard CF_UNICODETEXT", text))
        else:
            results.append(("win32clipboard CF_UNICODETEXT", "NOT AVAILABLE"))

        # 检查 CF_HDROP (文件拖放格式)
        CF_HDROP = 15
        if win32clipboard.IsClipboardFormatAvailable(CF_HDROP):
            data = win32clipboard.GetClipboardData(CF_HDROP)
            results.append(("win32clipboard CF_HDROP", str(data)))
        else:
            results.append(("win32clipboard CF_HDROP", "NOT AVAILABLE"))

        win32clipboard.CloseClipboard()
    except Exception as e:
        results.append(("win32clipboard", f"ERROR: {e}"))

    # 方法3: tkinter
    try:
        import tkinter as tk
        root = tk.Tk()
        root.withdraw()
        text = root.clipboard_get()
        root.destroy()
        results.append(("tkinter", text))
    except Exception as e:
        results.append(("tkinter", f"ERROR: {e}"))

    # 打印结果
    print("=" * 60)
    print("剪贴板调试报告")
    print("=" * 60)
    for name, content in results:
        print(f"\n【{name}】")
        if isinstance(content, str) and not content.startswith("ERROR") and not content.startswith("NOT"):
            lines = content.strip().splitlines()
            print(f"  总行数: {len(lines)}")
            for i, line in enumerate(lines[:5], 1):
                safe = line.encode('utf-8', errors='replace').decode('utf-8')
                print(f"  [{i}] (len={len(line)}) {safe[:80]}{'...' if len(safe) > 80 else ''}")
            if len(lines) > 5:
                print(f"  ... 还有 {len(lines) - 5} 行")
        else:
            print(f"  {content}")

    print("\n" + "=" * 60)
    print("诊断建议:")
    print("=" * 60)

    # 检查是否有截断
    has_truncation = False
    for name, content in results:
        if isinstance(content, str) and "ERROR" not in content and "NOT" not in content:
            lines = content.strip().splitlines()
            for line in lines:
                if line.endswith('...') or (len(line) > 50 and not line.endswith('.md') and not line.endswith('.markdown')):
                    has_truncation = True
                    break

    if has_truncation:
        print("⚠️  检测到路径被截断！")
        print("   原因: 从文件资源管理器复制文件时，Windows 剪贴板的文本格式不完整")
        print("   解决: 使用 'f' 从 paths.txt 读取，或修改脚本支持 CF_HDROP 格式")
    else:
        print("✅ 剪贴板内容看起来完整")

if __name__ == "__main__":
    print("请先从文件资源管理器复制一些 md 文件，然后按回车运行检测...")
    input()
    check_clipboard()
    input("\n按回车退出...")
