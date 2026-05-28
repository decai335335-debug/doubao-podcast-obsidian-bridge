#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_upload.py — 豆包 PDF 上传测试工具

用途：专门测试 PDF 上传流程，不上传完成后不点击"生成播客"，
      只验证每个文件能否成功上传到豆包。

用法：
    python test_upload.py <PDF文件或目录> [<PDF文件或目录> ...]

输出：每个文件的上传结果 + 耗时统计
"""

import asyncio
import json
import sys
import time
from pathlib import Path

# 复用 uploader 的核心函数
SCRIPT_DIR = Path(__file__).parent
sys.path.insert(0, str(SCRIPT_DIR))

from doubao_uploader import (
    upload_pdf,
    ensure_chat_open,
    log,
    log_success,
    log_error,
    log_warn,
)
from doubao_pipeline import _get_paths_from_clipboard, _get_paths_from_file

# Playwright
from playwright.async_api import async_playwright

DEFAULT_URL = "https://www.doubao.com"
STATE_FILE = SCRIPT_DIR / "doubao_state.json"


async def test_upload_single(page, pdf_path: Path):
    """
    测试上传单个 PDF，直接复用 doubao_uploader.py 的 upload_pdf 函数，
    详细记录每一步的耗时和结果。
    返回: (success: bool, detail: dict)
    """
    detail = {
        "file": pdf_path.name,
        "file_size_mb": round(pdf_path.stat().st_size / (1024 * 1024), 2),
        "steps": [],
    }
    overall_start = time.time()

    # 直接调用 doubao_uploader.py 中验证过的 upload_pdf 函数
    step_start = time.time()
    log(f"[测试] 调用 upload_pdf: {pdf_path.name}...")

    upload_success = await upload_pdf(page, pdf_path)

    detail["steps"].append({
        "name": "upload_pdf 完整上传",
        "success": upload_success,
        "duration_sec": round(time.time() - step_start, 2),
    })

    detail["overall_sec"] = round(time.time() - overall_start, 2)
    detail["success"] = upload_success
    if not upload_success:
        detail["fail_reason"] = "upload_pdf 返回失败"

    return upload_success, detail


async def main():
    print("=" * 60)
    print("📋 PDF 上传测试工具")
    print("=" * 60)
    print()
    print("用法：复制 PDF 路径到剪贴板（每行一个），或直接粘贴路径")
    print()

    # 获取 PDF 路径
    pdf_files = []

    # 1. 尝试从剪贴板读取
    clipboard_paths = _get_paths_from_clipboard()
    if clipboard_paths:
        print(f"📋 从剪贴板检测到 {len(clipboard_paths)} 个路径:")
        for i, p in enumerate(clipboard_paths[:10], 1):
            display = p if len(p) < 70 else p[:67] + "..."
            print(f"   [{i}] {display}")
        if len(clipboard_paths) > 10:
            print(f"   ... 还有 {len(clipboard_paths) - 10} 个")
        try:
            use_clipboard = input("\n是否使用剪贴板中的路径? (y/n/f=从文件读取): ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            use_clipboard = 'n'
        if use_clipboard == 'y':
            pdf_files = clipboard_paths
        elif use_clipboard == 'f':
            default_path = SCRIPT_DIR / "paths.txt"
            try:
                file_path = input(f"请输入路径列表文件（默认: {default_path}）: ").strip()
            except (EOFError, KeyboardInterrupt):
                file_path = ""
            if not file_path:
                file_path = str(default_path)
            pdf_files = _get_paths_from_file(file_path)
    else:
        print("剪贴板为空，请手动粘贴 PDF 路径（每行一个，输入空行结束）:\n")
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
                    pdf_files.append(item)

    # 验证并过滤
    valid_pdfs = []
    for p in pdf_files:
        path = Path(p.strip().strip('"').strip("'"))
        if not path.exists():
            print(f"⚠️  跳过（不存在）: {path}")
            continue
        if path.suffix.lower() != ".pdf":
            print(f"⚠️  跳过（非 PDF）: {path}")
            continue
        valid_pdfs.append(path)

    if not valid_pdfs:
        print("❌ 未找到有效的 PDF 文件")
        return

    # 设置间隔
    try:
        interval_input = input("\n文件间间隔秒数（默认5秒）: ").strip()
        interval = int(interval_input) if interval_input else 5
    except (EOFError, KeyboardInterrupt, ValueError):
        interval = 5

    print(f"\n{'='*60}")
    print(f"📋 PDF 上传测试")
    print(f"{'='*60}")
    print(f"待测试文件数: {len(valid_pdfs)}")
    print(f"文件间间隔: {interval} 秒")
    print(f"{'='*60}\n")

    results = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)

        # 加载登录态
        context = await browser.new_context()
        if STATE_FILE.exists():
            try:
                with open(STATE_FILE, "r", encoding="utf-8") as f:
                    state = json.load(f)
                await context.add_cookies(state.get("cookies", []))
                for key, value in state.get("localStorage", {}).items():
                    await context.add_init_script(f"localStorage.setItem('{key}', '{value}');")
                log("加载登录态成功")
            except Exception as e:
                log_warn(f"加载登录态失败: {e}")

        page = await context.new_page()
        page.set_default_timeout(30000)

        # 打开豆包
        await page.goto(DEFAULT_URL)
        await asyncio.sleep(3)

        # 确保聊天窗口打开
        if not await ensure_chat_open(page):
            print("❌ 无法打开聊天窗口")
            await browser.close()
            return

        # 逐个测试上传
        for idx, pdf_path in enumerate(valid_pdfs, 1):
            print(f"\n{'-'*50}")
            print(f"[{idx}/{len(valid_pdfs)}] 测试: {pdf_path.name}")
            print(f"{'-'*50}")

            success, detail = await test_upload_single(page, pdf_path)
            results.append(detail)

            # 打印本次结果
            print(f"\n  结果: {'✅ 成功' if success else '❌ 失败'}")
            print(f"  总耗时: {detail['overall_sec']} 秒")
            for step in detail["steps"]:
                status = "✓" if step["success"] else "✗"
                print(f"    {status} {step['name']}: {step['duration_sec']} 秒")
            if not success:
                print(f"    失败原因: {detail.get('fail_reason', '未知')}")

            # 间隔
            if idx < len(pdf_files):
                print(f"\n  ⏳ 等待 {interval} 秒...")
                await asyncio.sleep(interval)

        # 保存登录态
        try:
            cookies = await context.cookies()
            local_storage = await page.evaluate("() => Object.fromEntries(Object.entries(localStorage))")
            with open(STATE_FILE, "w", encoding="utf-8") as f:
                json.dump({"cookies": cookies, "localStorage": local_storage}, f, ensure_ascii=False, indent=2)
        except Exception as e:
            log_warn(f"保存登录态失败: {e}")

        await browser.close()

    # 汇总报告
    print(f"\n{'='*60}")
    print(f"📊 测试报告")
    print(f"{'='*60}")

    success_count = sum(1 for r in results if r["success"])
    fail_count = len(results) - success_count

    print(f"总文件数: {len(results)}")
    print(f"成功: {success_count}")
    print(f"失败: {fail_count}")
    print(f"成功率: {success_count / len(results) * 100:.1f}%")
    print()

    if fail_count > 0:
        print("失败文件:")
        for r in results:
            if not r["success"]:
                print(f"  ❌ {r['file']} ({r.get('fail_reason', '')})")

    # 保存详细报告
    report_file = SCRIPT_DIR / "upload_test_report.json"
    with open(report_file, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"\n详细报告已保存: {report_file}")


if __name__ == "__main__":
    asyncio.run(main())
