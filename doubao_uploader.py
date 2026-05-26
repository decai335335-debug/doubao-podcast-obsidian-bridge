#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
doubao_uploader.py - 自动将 PDF 上传到豆包并触发播客生成

用法:
    python doubao_uploader.py <PDF目录或文件> [选项]

示例:
    python doubao_uploader.py ./pdfs
    python doubao_uploader.py file1.pdf file2.pdf
    python doubao_uploader.py ./pdfs --resume

说明:
    - 在同一个聊天窗口内逐个上传 PDF，上传后点击"生成播客"按钮，等待播客生成完成
    - 自动复用现有的登录态 doubao_state.json
"""

import asyncio
import json
import os
import sys
import time
from pathlib import Path
from datetime import datetime

from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout

# ===================== 配置常量 =====================
SCRIPT_DIR = Path(__file__).parent
STATE_FILE = SCRIPT_DIR / "doubao_state.json"
PROGRESS_FILE = SCRIPT_DIR / "upload_progress.json"
DEBUG_DIR = SCRIPT_DIR / "doubao_debug"
DEFAULT_URL = "https://www.doubao.com"
# 不再需要默认提示词，改为点击"生成播客"按钮
# DEFAULT_PROMPT = "请根据这份PDF的内容为我生成一段播客"
DEFAULT_WAIT_TIMEOUT = 600  # 等待播客生成的超时时间（秒）
POLL_INTERVAL = 3  # 轮询间隔（秒）


# ===================== 日志工具 =====================
def log(msg, level="INFO"):
    """打印带时间戳的日志"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{now}] [{level}] {msg}")


def log_error(msg):
    log(msg, "ERROR")


def log_success(msg):
    log(msg, "SUCCESS")


def log_warn(msg):
    log(msg, "WARN")


# ===================== 进度管理 =====================
def load_progress():
    """加载处理进度"""
    if PROGRESS_FILE.exists():
        try:
            with open(PROGRESS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            log_warn(f"加载进度文件失败: {e}，将重新开始")
    return {"processed": [], "failed": []}


def save_progress(progress):
    """保存处理进度"""
    try:
        with open(PROGRESS_FILE, "w", encoding="utf-8") as f:
            json.dump(progress, f, ensure_ascii=False, indent=2)
    except Exception as e:
        log_error(f"保存进度失败: {e}")


# ===================== 截图调试 =====================
async def take_debug_screenshot(page, name):
    """保存调试截图"""
    try:
        DEBUG_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = DEBUG_DIR / f"{name}_{ts}.png"
        await page.screenshot(path=str(path), full_page=True)
        log(f"调试截图已保存: {path}")
    except Exception as e:
        log_warn(f"截图失败: {e}")


async def save_debug_html(page, name):
    """保存页面 HTML 用于调试"""
    try:
        DEBUG_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = DEBUG_DIR / f"{name}_{ts}.html"
        html = await page.content()
        with open(path, "w", encoding="utf-8") as f:
            f.write(html)
        log(f"调试 HTML 已保存: {path}")
    except Exception as e:
        log_warn(f"保存 HTML 失败: {e}")


# ===================== DOM 操作 =====================
async def click_new_chat(page):
    """
    点击"新建对话"按钮，打开一个新的聊天窗口。
    尝试多种选择器策略，直到成功。
    """
    log("尝试点击'新建对话'按钮...")

    # 策略 1: 常见文本匹配
    text_selectors = [
        'button:has-text("新对话")',
        'button:has-text("新建对话")',
        'div:has-text("新对话")',
        'div:has-text("新建对话")',
        '[class*="new-chat"]',
        '[class*="newChat"]',
    ]
    for sel in text_selectors:
        try:
            btn = await page.query_selector(sel)
            if btn:
                await btn.click(timeout=3000)
                log("通过选择器点击'新建对话': " + sel)
                await asyncio.sleep(2)
                return True
        except Exception:
            pass

    # 策略 2: JavaScript 遍历按钮，匹配文本内容
    clicked = await page.evaluate(
        """
        () => {
            const candidates = document.querySelectorAll('button, div[role="button"], a[role="button"]');
            for (const el of candidates) {
                const text = (el.textContent || el.innerText || '').trim();
                if (text.includes('新对话') || text.includes('新建') || text === '+') {
                    el.click();
                    return true;
                }
                // 也检查 aria-label
                const ariaLabel = el.getAttribute('aria-label') || '';
                if (ariaLabel.includes('新') || ariaLabel.includes('新建')) {
                    el.click();
                    return true;
                }
            }
            return false;
        }
        """
    )
    if clicked:
        log("通过 JavaScript 匹配文本点击'新建对话'")
        await asyncio.sleep(2)
        return True

    # 策略 3: 查找包含加号 SVG 或图标的按钮（通常在侧边栏顶部）
    clicked = await page.evaluate(
        """
        () => {
            // 找侧边栏区域的第一个可操作按钮
            const sidebar = document.querySelector('[class*="sidebar"]') 
                          || document.querySelector('aside')
                          || document.querySelector('nav');
            if (sidebar) {
                const btns = sidebar.querySelectorAll('button, div[role="button"]');
                if (btns.length > 0) {
                    btns[0].click();
                    return true;
                }
            }
            // 兜底：找页面中位置偏左上、带有加号图标的按钮
            const allBtns = document.querySelectorAll('button');
            for (const btn of allBtns) {
                const hasPlus = btn.innerHTML.includes('plus') 
                             || btn.innerHTML.includes('+')
                             || btn.querySelector('svg');
                const rect = btn.getBoundingClientRect();
                if (hasPlus && rect.left < 300 && rect.top < 200) {
                    btn.click();
                    return true;
                }
            }
            return false;
        }
        """
    )
    if clicked:
        log("通过图标/位置推断点击'新建对话'")
        await asyncio.sleep(2)
        return True

    log_error("未能找到'新建对话'按钮，请检查页面结构")
    await take_debug_screenshot(page, "new_chat_failed")
    await save_debug_html(page, "new_chat_failed")
    return False


async def upload_pdf(page, pdf_path: Path):
    """
    上传单个 PDF 文件到当前聊天窗口。
    流程：点击底部 "+" 按钮 → 点击 "上传文件或图片" → Playwright filechooser 设置文件
    """
    pdf_path = Path(pdf_path).resolve()
    if not pdf_path.exists():
        log_error(f"PDF 文件不存在: {pdf_path}")
        return False

    log(f"准备上传 PDF: {pdf_path.name}")

    try:
        # Step 1: 点击底部 "+" 按钮
        log("尝试点击 '+' 按钮...")
        plus_clicked = False

        # 策略 A: 找底部输入框区域的所有可操作元素，逐个尝试点击，
        #         然后检查是否出现了 "上传文件或图片"
        bottom_buttons = await page.evaluate(
            """
            () => {
                const results = [];
                const candidates = document.querySelectorAll('button, div[role="button"], span[role="button"], svg');
                for (const el of candidates) {
                    const rect = el.getBoundingClientRect();
                    // 只关注页面底部区域（y > 窗口高度的 80%）
                    if (rect.bottom > window.innerHeight * 0.8 && rect.width > 10 && rect.height > 10) {
                        const text = (el.textContent || el.innerText || '').trim();
                        const html = el.outerHTML.toLowerCase();
                        results.push({
                            text: text,
                            tag: el.tagName,
                            html_preview: html.substring(0, 200),
                            bottom: rect.bottom,
                            isVisible: el.offsetParent !== null
                        });
                    }
                }
                return results;
            }
            """
        )
        log(f"底部候选按钮数量: {len(bottom_buttons)}")
        for info in bottom_buttons[:10]:
            log(f"  候选: text='{info['text']}' tag={info['tag']} bottom={info['bottom']:.0f}")

        # 尝试通过文本匹配点击 "+"
        for info in bottom_buttons:
            if info['text'] == '+' or info['text'] == '＋':
                try:
                    # 用 JS 点击（通过坐标或文本匹配）
                    clicked = await page.evaluate(
                        """
                        () => {
                            const candidates = document.querySelectorAll('button, div[role="button"], span[role="button"]');
                            for (const el of candidates) {
                                const text = (el.textContent || el.innerText || '').trim();
                                if (text === '+' || text === '＋') {
                                    el.click();
                                    return true;
                                }
                            }
                            return false;
                        }
                        """
                    )
                    if clicked:
                        log("通过 JS 文本 '+' 点击成功")
                        plus_clicked = True
                        break
                except Exception:
                    pass

        # 策略 B: 如果文本匹配失败，尝试点击底部最左侧的按钮（通常是 "+"）
        if not plus_clicked and bottom_buttons:
            # 找 bottom 值最大的（最靠下的）且最靠左的
            bottom_row = [b for b in bottom_buttons if b.get('isVisible')]
            if bottom_row:
                bottom_row.sort(key=lambda x: x['bottom'], reverse=True)
                max_bottom = bottom_row[0]['bottom']
                # 取最下面一行的元素，再按 left 排序取最左边的
                # 这里简化：直接尝试点击最下面一行的前几个
                clicked = await page.evaluate(
                    """
                    () => {
                        const candidates = [...document.querySelectorAll('button, div[role="button"]')];
                        const bottomEls = candidates.filter(el => {
                            const rect = el.getBoundingClientRect();
                            return rect.bottom > window.innerHeight * 0.85 && rect.width > 15 && rect.height > 15;
                        });
                        // 按 left 排序，取最左边的一个
                        bottomEls.sort((a, b) => a.getBoundingClientRect().left - b.getBoundingClientRect().left);
                        if (bottomEls.length > 0) {
                            bottomEls[0].click();
                            return true;
                        }
                        return false;
                    }
                    """
                )
                if clicked:
                    log("通过 JS 点击底部最左侧按钮（推断为 '+'）")
                    plus_clicked = True

        if not plus_clicked:
            log_error("未能点击 '+' 按钮")
            await take_debug_screenshot(page, "plus_button_not_found")
            return False

        await asyncio.sleep(1.5)

        # Step 2: 点击 "上传文件或图片"
        log("尝试点击 '上传文件或图片'...")
        upload_clicked = False

        # 策略 A: Playwright Locator
        try:
            upload_locator = page.get_by_text("上传文件或图片")
            if await upload_locator.count() > 0:
                await upload_locator.first.click(timeout=3000)
                log("通过 get_by_text('上传文件或图片') 点击成功")
                upload_clicked = True
        except Exception:
            pass

        # 策略 B: JavaScript 遍历
        if not upload_clicked:
            upload_clicked = await page.evaluate(
                """
                () => {
                    const all = document.querySelectorAll('button, div, span, a, [role="button"]');
                    for (const el of all) {
                        const text = (el.textContent || el.innerText || '').trim();
                        if (text.includes('上传文件') || text.includes('上传文件或图片')) {
                            if (typeof el.click === 'function') {
                                el.click();
                                return true;
                            }
                        }
                    }
                    return false;
                }
                """
            )
            if upload_clicked:
                log("通过 JS 找到并点击 '上传文件或图片'")

        if not upload_clicked:
            log_error("未能点击 '上传文件或图片'")
            await take_debug_screenshot(page, "upload_option_not_found")
            return False

        await asyncio.sleep(1)

        # Step 3: Playwright filechooser 设置文件
        log("等待文件选择对话框并设置文件...")
        try:
            async with page.expect_file_chooser(timeout=8000) as fc_info:
                # 如果 "上传文件或图片" 的点击已经触发了 filechooser，
                # expect_file_chooser 会捕获它
                pass
            file_chooser = await fc_info.value
            await file_chooser.set_files(str(pdf_path))
            log(f"通过 filechooser 上传成功: {pdf_path.name}")
            await asyncio.sleep(2)
            return True
        except Exception as e1:
            log_warn(f"filechooser 方式失败: {e1}")
            # 兜底：找出现的 file input
            file_inputs = await page.query_selector_all('input[type="file"]')
            if file_inputs:
                try:
                    await file_inputs[0].set_input_files(str(pdf_path))
                    log(f"通过 file input 上传成功: {pdf_path.name}")
                    await asyncio.sleep(2)
                    return True
                except Exception as e2:
                    log_warn(f"file input 方式也失败: {e2}")

    except Exception as e:
        log_warn(f"上传流程异常: {e}")

    log_error(f"上传 PDF 失败: {pdf_path.name}")
    await take_debug_screenshot(page, "upload_failed")
    await save_debug_html(page, "upload_failed")
    return False


async def click_generate_podcast(page):
    """
    上传 PDF 后，点击 PDF 下方出现的"生成播客"按钮。
    按钮文字为"生成播客 →"，用文本匹配即可定位。
    """
    log("等待并点击'生成播客'按钮...")

    # 先等待一下，让豆包解析 PDF 并显示按钮
    await asyncio.sleep(3)

    # 策略 1: Playwright Locator 文本匹配（推荐）
    try:
        locator = page.get_by_text("生成播客")
        count = await locator.count()
        if count > 0:
            await locator.first.click(timeout=5000)
            log("通过 get_by_text('生成播客') 点击成功")
            await asyncio.sleep(1)
            return True
    except Exception as e:
        log_warn(f"Locator 文本匹配失败: {e}")

    # 策略 2: CSS 文本内容匹配
    text_selectors = [
        'button:has-text("生成播客")',
        'div:has-text("生成播客")',
        'span:has-text("生成播客")',
        'a:has-text("生成播客")',
        '[role="button"]:has-text("生成播客")',
    ]
    for sel in text_selectors:
        try:
            btn = await page.query_selector(sel)
            if btn:
                await btn.click(timeout=3000)
                log(f"通过选择器点击'生成播客': {sel}")
                await asyncio.sleep(1)
                return True
        except Exception:
            pass

    # 策略 3: JavaScript 遍历所有元素，匹配包含"生成播客"文本的元素
    clicked = await page.evaluate(
        """
        () => {
            // 遍历所有可见的元素
            const allElements = document.querySelectorAll('button, div, span, a, [role="button"]');
            for (const el of allElements) {
                // 检查文本内容
                const text = (el.textContent || el.innerText || '').trim();
                if (text === '生成播客' || text.includes('生成播客')) {
                    // 确保元素可见且可点击
                    const rect = el.getBoundingClientRect();
                    if (rect.width > 0 && rect.height > 0) {
                        el.click();
                        return {found: true, text: text, tag: el.tagName};
                    }
                }
                // 也检查 aria-label
                const ariaLabel = el.getAttribute('aria-label') || '';
                if (ariaLabel.includes('生成播客')) {
                    el.click();
                    return {found: true, text: ariaLabel, tag: el.tagName};
                }
            }
            return {found: false};
        }
        """
    )
    if clicked and clicked.get("found"):
        log(f"通过 JavaScript 文本匹配点击'生成播客': {clicked.get('text')} (tag: {clicked.get('tag')})")
        await asyncio.sleep(1)
        return True

    # 策略 4: 兜底——找 PDF 卡片附近的所有可操作按钮，逐个尝试点击
    clicked = await page.evaluate(
        """
        () => {
            // 找包含 .pdf 文字的元素
            const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, null, false);
            let pdfNode = null;
            while (walker.nextNode()) {
                if (walker.currentNode.textContent.toLowerCase().includes('.pdf')) {
                    pdfNode = walker.currentNode;
                    break;
                }
            }
            if (pdfNode) {
                // 向上追溯到包含整个消息/卡片的父元素
                let parent = pdfNode.parentElement;
                for (let i = 0; i < 10 && parent; i++) {
                    const btns = parent.querySelectorAll('button, [role="button"], [class*="button"]');
                    for (const btn of btns) {
                        const rect = btn.getBoundingClientRect();
                        if (rect.width > 0 && rect.height > 0) {
                            btn.click();
                            return {found: true, text: btn.textContent || btn.innerText || '', tag: btn.tagName};
                        }
                    }
                    parent = parent.parentElement;
                }
            }
            return {found: false};
        }
        """
    )
    if clicked and clicked.get("found"):
        log(f"通过 PDF 附近按钮推断点击: {clicked.get('text')} (tag: {clicked.get('tag')})")
        await asyncio.sleep(1)
        return True

    log_error("未能找到'生成播客'按钮")
    await take_debug_screenshot(page, "generate_podcast_not_found")
    await save_debug_html(page, "generate_podcast_not_found")
    return False


async def wait_for_podcast(page, pdf_name: str, timeout: int = DEFAULT_WAIT_TIMEOUT):
    """
    等待豆包生成播客完成。
    在点击"生成播客"前先记录当前播客卡片数量作为基准，
    然后检测是否有**新增**的播客卡片出现。
    """
    log(f"等待播客生成完成（超时: {timeout}秒）...")
    start = time.time()

    # 获取基准卡片数量（已有的播客）
    try:
        base_cards = await page.query_selector_all('[data-plugin-identifier*="receive-podcast-content"]')
        base_count = len(base_cards)
        log(f"当前已有播客卡片: {base_count} 个（作为基准）")
    except Exception:
        base_count = 0

    last_count = base_count
    stable_count = 0  # 连续几次检测到相同数量的新卡片

    while time.time() - start < timeout:
        try:
            # 检测播客卡片
            cards = await page.query_selector_all('[data-plugin-identifier*="receive-podcast-content"]')
            current_count = len(cards)

            # 如果卡片数量比基准值增加了，说明有新播客生成
            if current_count > base_count:
                # 额外确认：连续两次检测到相同的增加量，避免瞬态变化
                if current_count == last_count:
                    stable_count += 1
                    if stable_count >= 2:
                        log_success(f"确认新播客生成！从 {base_count} 个增加到 {current_count} 个")
                        await asyncio.sleep(3)
                        return True
                else:
                    stable_count = 0
                    log(f"检测到播客卡片数量变化: {last_count} → {current_count}")

                last_count = current_count

            # 也检测是否有错误提示
            error_el = await page.query_selector('[class*="error"], [class*="fail"]')
            if error_el:
                error_text = await error_el.inner_text()
                if error_text and len(error_text) > 0:
                    log_warn(f"页面出现错误提示: {error_text[:100]}")

        except Exception as e:
            log_warn(f"轮询检测时出错: {e}")

        await asyncio.sleep(POLL_INTERVAL)

    log_error(f"等待播客生成超时 ({timeout}秒)")
    await take_debug_screenshot(page, "podcast_timeout")
    return False


async def ensure_chat_open(page):
    """
    确保当前已经打开了一个聊天窗口。
    如果 URL 中没有 /chat/，则尝试点击新建对话。
    """
    current_url = page.url
    log(f"当前页面 URL: {current_url}")

    if "/chat/" in current_url:
        log("当前已在聊天页面")
        return True

    # 尝试点击新建对话
    if await click_new_chat(page):
        # 等待 URL 变化
        try:
            await page.wait_for_url("**/chat/**", timeout=10000)
            log(f"新聊天已打开: {page.url}")
            return True
        except PlaywrightTimeout:
            log_warn("URL 未变化到 /chat/，但可能已打开新聊天")
            await asyncio.sleep(2)
            return True
    return False


# ===================== 主流程 =====================
async def process_pdfs(pdf_files, args):
    """
    主处理流程：在同一个聊天窗口内逐个上传 PDF 并生成播客。
    """
    # 加载进度
    progress = load_progress()
    if args.resume:
        processed = set(progress.get("processed", []))
        failed = set(progress.get("failed", []))
        log(f"断点续传模式：已处理 {len(processed)} 个，失败 {len(failed)} 个")
    else:
        processed = set()
        failed = set()

    # 过滤已处理的文件
    pending = []
    for pf in pdf_files:
        name = Path(pf).name
        if name in processed and args.resume:
            log(f"跳过已处理: {name}")
            continue
        pending.append(pf)

    if not pending:
        log("所有 PDF 已处理完毕，无需上传")
        return

    log(f"待处理 PDF 数量: {len(pending)}")

    # 启动浏览器
    log("启动浏览器...")
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = browser

        # 加载登录态
        if STATE_FILE.exists():
            log(f"加载登录态: {STATE_FILE}")
            context = await browser.new_context(storage_state=str(STATE_FILE))
        else:
            log_warn("未找到登录态文件，将以未登录状态启动")
            context = await browser.new_context()

        page = await context.new_page()
        page.set_default_timeout(30000)

        # 打开豆包页面
        target_url = args.url or DEFAULT_URL
        log(f"导航到: {target_url}")
        await page.goto(target_url)
        await asyncio.sleep(3)

        # 确保打开了聊天窗口（只需新建一次）
        if not await ensure_chat_open(page):
            log_error("无法打开聊天窗口，退出")
            await browser.close()
            return

        # 逐个处理 PDF
        for idx, pdf_path in enumerate(pending, 1):
            pdf_path = Path(pdf_path)
            log(f"\n{'='*50}")
            log(f"[{idx}/{len(pending)}] 处理: {pdf_path.name}")
            log(f"{'='*50}")

            # 上传 PDF
            if not await upload_pdf(page, pdf_path):
                log_error(f"上传失败，跳过: {pdf_path.name}")
                failed.add(pdf_path.name)
                progress["failed"] = list(failed)
                save_progress(progress)
                continue

            # 点击"生成播客"按钮
            if not await click_generate_podcast(page):
                log_error(f"点击'生成播客'失败，跳过: {pdf_path.name}")
                failed.add(pdf_path.name)
                progress["failed"] = list(failed)
                save_progress(progress)
                continue

            # 等待播客生成
            success = await wait_for_podcast(page, pdf_path.name, timeout=args.wait)

            if success:
                log_success(f"播客生成完成: {pdf_path.name}")
                processed.add(pdf_path.name)
                if pdf_path.name in failed:
                    failed.remove(pdf_path.name)
            else:
                log_error(f"播客生成超时或失败: {pdf_path.name}")
                failed.add(pdf_path.name)

            # 保存进度
            progress["processed"] = list(processed)
            progress["failed"] = list(failed)
            save_progress(progress)

            # 处理完一个后稍作等待，让页面稳定
            if idx < len(pending):
                log("等待 5 秒后处理下一个 PDF...")
                await asyncio.sleep(5)

        log(f"\n{'='*50}")
        log("所有 PDF 处理完毕")
        log(f"成功: {len(processed)} 个")
        log(f"失败: {len(failed)} 个")
        log(f"{'='*50}")

        # 最终截图
        await take_debug_screenshot(page, "upload_complete")

        await context.close()
        await browser.close()


# ===================== 参数解析 =====================
def parse_args():
    """解析命令行参数"""
    import argparse

    parser = argparse.ArgumentParser(
        description="自动将 PDF 上传到豆包并触发播客生成",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python doubao_uploader.py ./pdfs
  python doubao_uploader.py file1.pdf file2.pdf
  python doubao_uploader.py ./pdfs --resume --wait 600
        """,
    )
    parser.add_argument(
        "paths",
        nargs="+",
        help="PDF 文件或包含 PDF 的目录路径",
    )
    parser.add_argument(
        "--url",
        default=None,
        help="豆包页面 URL（默认: https://www.doubao.com）",
    )
    parser.add_argument(
        "--wait",
        type=int,
        default=DEFAULT_WAIT_TIMEOUT,
        help=f"等待播客生成的超时时间，单位秒（默认: {DEFAULT_WAIT_TIMEOUT}）",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="断点续传模式，跳过 upload_progress.json 中已标记为成功的文件",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="无头模式运行（不显示浏览器窗口）",
    )

    args = parser.parse_args()

    # 解析路径：如果是目录，收集其中所有 .pdf 文件
    pdf_files = []
    for p in args.paths:
        path = Path(p)
        if path.is_dir():
            pdfs = sorted(path.glob("*.pdf"))
            pdf_files.extend([str(f) for f in pdfs])
        elif path.suffix.lower() == ".pdf":
            pdf_files.append(str(path))
        else:
            log_warn(f"忽略非 PDF 文件: {p}")

    if not pdf_files:
        parser.error("未找到任何 PDF 文件")

    args.pdf_files = pdf_files
    return args


# ===================== 入口 =====================
def main():
    args = parse_args()
    try:
        asyncio.run(process_pdfs(args.pdf_files, args))
    except KeyboardInterrupt:
        log_warn("用户中断，进度已保存到 upload_progress.json")
    except Exception as e:
        log_error(f"程序异常: {e}")
        raise


if __name__ == "__main__":
    main()
