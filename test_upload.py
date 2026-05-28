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

import argparse
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
    load_progress,
    save_progress,
    log,
    log_success,
    log_error,
    log_warn,
    PROGRESS_FILE,
)

# Playwright
from playwright.async_api import async_playwright

DEFAULT_URL = "https://www.doubao.com"
STATE_FILE = SCRIPT_DIR / "doubao_state.json"


async def test_upload_single(page, pdf_path: Path):
    """
    测试上传单个 PDF，详细记录每一步的耗时和结果。
    返回: (success: bool, detail: dict)
    """
    detail = {
        "file": pdf_path.name,
        "file_size_mb": round(pdf_path.stat().st_size / (1024 * 1024), 2),
        "steps": [],
    }
    overall_start = time.time()

    # 步骤1: 点击 "+" 按钮
    step_start = time.time()
    log(f"[测试] 步骤1: 点击 '+' 按钮...")

    # 先尝试点击 "+"
    plus_clicked = False
    for attempt in range(3):
        try:
            clicked = await page.evaluate(
                """
                () => {
                    const SIDEBAR_WIDTH = 320;
                    const all = document.querySelectorAll('button, div, svg');
                    for (const el of all) {
                        const rect = el.getBoundingClientRect();
                        const text = (el.textContent || el.innerText || '').trim();
                        const aria = el.getAttribute('aria-label') || '';
                        if (rect.left > SIDEBAR_WIDTH && rect.width > 0 && rect.height > 0) {
                            if (text === '+' || aria.includes('添加') || el.className.includes('plus') || el.className.includes('add')) {
                                el.click();
                                return {found: true, text: text || aria, left: rect.left, top: rect.top};
                            }
                        }
                    }
                    return {found: false};
                }
                """
            )
            if clicked and clicked.get("found"):
                log(f"  ✓ 第 {attempt + 1} 次点击 '+' 成功: {clicked}")
                plus_clicked = True
                break
            else:
                log(f"  ⚠ 第 {attempt + 1} 次未找到 '+'，重试...")
                await asyncio.sleep(2)
        except Exception as e:
            log_warn(f"  点击 '+' 出错: {e}")
            await asyncio.sleep(2)

    detail["steps"].append({
        "name": "点击 '+'",
        "success": plus_clicked,
        "duration_sec": round(time.time() - step_start, 2),
    })

    if not plus_clicked:
        detail["overall_sec"] = round(time.time() - overall_start, 2)
        detail["success"] = False
        detail["fail_reason"] = "点击 '+' 按钮失败"
        return False, detail

    # 步骤2: 等待菜单弹出
    step_start = time.time()
    log(f"[测试] 步骤2: 等待菜单弹出...")

    menu_found = False
    for attempt in range(10):
        try:
            upload_locator = page.get_by_text("上传文件或图片")
            count = await upload_locator.count()
            if count > 0:
                log(f"  ✓ 检测到菜单（'上传文件或图片'已出现）")
                menu_found = True
                break
            else:
                log(f"  ... 等待菜单 ({attempt + 1}/10)")
                await asyncio.sleep(1)
        except Exception as e:
            log_warn(f"  检测菜单出错: {e}")
            await asyncio.sleep(1)

    detail["steps"].append({
        "name": "等待菜单弹出",
        "success": menu_found,
        "duration_sec": round(time.time() - step_start, 2),
    })

    if not menu_found:
        detail["overall_sec"] = round(time.time() - overall_start, 2)
        detail["success"] = False
        detail["fail_reason"] = "菜单未弹出"
        return False, detail

    # 步骤3: 通过 filechooser 上传
    step_start = time.time()
    log(f"[测试] 步骤3: filechooser 上传...")

    upload_success = False
    try:
        async with page.expect_file_chooser(timeout=10000) as fc_info:
            upload_locator = page.get_by_text("上传文件或图片")
            count = await upload_locator.count()
            if count > 0:
                await upload_locator.first.click(timeout=3000)
            else:
                raise Exception("上传按钮消失")

        filechooser = await fc_info.value
        await filechooser.set_files(str(pdf_path))
        log(f"  ✓ filechooser 上传成功: {pdf_path.name}")
        upload_success = True
    except Exception as e:
        log_error(f"  filechooser 上传失败: {e}")

    detail["steps"].append({
        "name": "filechooser 上传",
        "success": upload_success,
        "duration_sec": round(time.time() - step_start, 2),
    })

    # 步骤4: 等待上传完成（检测 PDF 卡片出现）
    step_start = time.time()
    log(f"[测试] 步骤4: 等待 PDF 卡片出现...")

    card_found = False
    for attempt in range(20):
        try:
            # 查找页面上包含 PDF 文件名的元素
            pdf_name = pdf_path.stem
            found = await page.evaluate(
                f"""
                () => {{
                    const all = document.querySelectorAll('*');
                    for (const el of all) {{
                        const text = (el.textContent || el.innerText || '').trim();
                        if (text.includes('{pdf_name.replace("'", "\\'")}')) {{
                            return {{found: true, text: text.slice(0, 50)}};
                        }}
                    }}
                    return {{found: false}};
                }}
                """
            )
            if found and found.get("found"):
                log(f"  ✓ PDF 卡片已出现")
                card_found = True
                break
            else:
                log(f"  ... 等待 PDF 卡片 ({attempt + 1}/20)")
                await asyncio.sleep(1)
        except Exception as e:
            log_warn(f"  检测 PDF 卡片出错: {e}")
            await asyncio.sleep(1)

    detail["steps"].append({
        "name": "等待 PDF 卡片",
        "success": card_found,
        "duration_sec": round(time.time() - step_start, 2),
    })

    detail["overall_sec"] = round(time.time() - overall_start, 2)
    detail["success"] = upload_success and card_found
    if not detail["success"]:
        detail["fail_reason"] = detail["steps"][-1]["name"] + " 失败"

    return detail["success"], detail


async def main():
    parser = argparse.ArgumentParser(description="测试豆包 PDF 上传")
    parser.add_argument("paths", nargs="+", help="PDF 文件或包含 PDF 的目录")
    parser.add_argument("--url", default=DEFAULT_URL, help="豆包 URL")
    parser.add_argument("--interval", type=int, default=5, help="文件间间隔秒数（默认5秒）")
    args = parser.parse_args()

    # 收集所有 PDF
    pdf_files = []
    for p in args.paths:
        path = Path(p)
        if path.is_dir():
            pdf_files.extend(sorted(path.rglob("*.pdf")))
        elif path.suffix.lower() == ".pdf":
            pdf_files.append(path)

    if not pdf_files:
        print("❌ 未找到 PDF 文件")
        return

    print(f"\n{'='*60}")
    print(f"📋 PDF 上传测试")
    print(f"{'='*60}")
    print(f"待测试文件数: {len(pdf_files)}")
    print(f"文件间间隔: {args.interval} 秒")
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
        await page.goto(args.url)
        await asyncio.sleep(3)

        # 确保聊天窗口打开
        if not await ensure_chat_open(page):
            print("❌ 无法打开聊天窗口")
            await browser.close()
            return

        # 逐个测试上传
        for idx, pdf_path in enumerate(pdf_files, 1):
            print(f"\n{'-'*50}")
            print(f"[{idx}/{len(pdf_files)}] 测试: {pdf_path.name}")
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
                print(f"\n  ⏳ 等待 {args.interval} 秒...")
                await asyncio.sleep(args.interval)

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
