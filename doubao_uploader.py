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
        # Step 1: 点击底部 "+" 按钮（可能需要多次点击）
        log("尝试点击 '+' 按钮...")
        plus_clicked = False

        # 先滚动到底部确保输入框可见
        await page.evaluate("() => { window.scrollTo(0, document.body.scrollHeight); }")
        await asyncio.sleep(0.5)

        for attempt in range(1, 4):
            log(f"第 {attempt} 次点击 '+'...")

            # 每次都用 JS 实时探测底部最左侧的圆形按钮（不依赖固定选择器）
            btn_info = await page.evaluate(
                """
                () => {
                    const allBtns = document.querySelectorAll('button');
                    let candidates = [];
                    for (const btn of allBtns) {
                        const rect = btn.getBoundingClientRect();
                        // 必须在页面底部区域
                        if (rect.bottom > window.innerHeight * 0.88 &&
                            rect.width > 20 && rect.height > 20 &&
                            rect.width < 60 && rect.height < 60) {
                            candidates.push({
                                left: rect.left,
                                top: rect.top,
                                width: rect.width,
                                height: rect.height,
                                text: (btn.textContent || btn.innerText || '').trim(),
                                html: btn.outerHTML.substring(0, 200)
                            });
                        }
                    }
                    // 按 left 排序取最左侧的
                    candidates.sort((a, b) => a.left - b.left);
                    if (candidates.length > 0) {
                        const target = candidates[0];
                        return {
                            found: true,
                            left: target.left,
                            top: target.top,
                            width: target.width,
                            height: target.height,
                            text: target.text,
                            cx: target.left + target.width / 2,
                            cy: target.top + target.height / 2
                        };
                    }
                    return {found: false};
                }
                """
            )

            if not btn_info.get("found"):
                log_warn("未找到底部候选按钮")
                break

            log(f"探测到 '+' 按钮: ({btn_info['cx']:.0f}, {btn_info['cy']:.0f}) text='{btn_info['text']}'")

            # 用真实鼠标序列点击
            try:
                await page.mouse.move(btn_info['cx'], btn_info['cy'])
                await asyncio.sleep(0.2)
                await page.mouse.down()
                await asyncio.sleep(0.1)
                await page.mouse.up()
            except Exception as e:
                log_warn(f"鼠标序列失败: {e}")

            # 等待菜单出现
            await asyncio.sleep(2)

            # 检查菜单是否出现
            upload_locator = page.get_by_text("上传文件或图片")
            menu_count = await upload_locator.count()
            if menu_count > 0:
                log("菜单已弹出（检测到'上传文件或图片'）")
                plus_clicked = True
                break
            else:
                log_warn("菜单未弹出，准备再次点击 '+'")

        if not plus_clicked:
            log_error("点击 '+' 3 次后菜单仍未弹出")
            await take_debug_screenshot(page, "plus_button_not_found")
            return False

        # Step 2+3: 先开始监听 filechooser，然后点击 "上传文件或图片"，最后设置文件
        # 关键：expect_file_chooser 必须在触发对话框的操作之前/同时开始监听
        log("开始监听 filechooser 并点击 '上传文件或图片'...")

        async def click_upload_option():
            """点击上传选项的异步函数，供 expect_file_chooser 上下文调用"""
            # 等待菜单出现
            await asyncio.sleep(1)

            # 策略 A: Playwright Locator
            upload_locator = page.get_by_text("上传文件或图片")
            count = await upload_locator.count()
            if count > 0:
                await upload_locator.first.click(timeout=3000)
                log("通过 get_by_text('上传文件或图片') 点击成功")
                return True

            # 策略 B: JavaScript 遍历
            clicked = await page.evaluate(
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
            if clicked:
                log("通过 JS 找到并点击 '上传文件或图片'")
                return True

            return False

        try:
            # 在 expect_file_chooser 上下文内执行点击操作
            async with page.expect_file_chooser(timeout=10000) as fc_info:
                upload_ok = await click_upload_option()
                if not upload_ok:
                    raise Exception("未能点击 '上传文件或图片'")

            file_chooser = await fc_info.value
            await file_chooser.set_files(str(pdf_path))
            log(f"通过 filechooser 上传成功: {pdf_path.name}")
            await asyncio.sleep(2)
            return True

        except Exception as e:
            log_warn(f"filechooser 流程失败: {e}")
            await take_debug_screenshot(page, "filechooser_failed")

    except Exception as e:
        log_warn(f"上传流程异常: {e}")

    log_error(f"上传 PDF 失败: {pdf_path.name}")
    await take_debug_screenshot(page, "upload_failed")
    await save_debug_html(page, "upload_failed")
    return False


async def click_generate_podcast(page):
    """
    上传 PDF 后，点击 PDF 下方出现的"生成播客"按钮。
    策略：先定位最新上传的 PDF 卡片，然后只在该卡片内部查找"生成播客"。
    """
    log("等待并点击'生成播客'按钮...")
    await asyncio.sleep(3)

    # 策略：找最新上传的 PDF → 在其消息卡片内找"生成播客"
    clicked = await page.evaluate(
        """
        () => {
            const SIDEBAR_WIDTH = 320; // 严格排除侧边栏

            // 步骤1: 收集所有包含 .pdf 文本的叶子元素
            const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, null, false);
            let pdfNodes = [];
            while (walker.nextNode()) {
                const text = walker.currentNode.textContent.toLowerCase();
                if (text.includes('.pdf')) {
                    const parent = walker.currentNode.parentElement;
                    if (parent) {
                        const rect = parent.getBoundingClientRect();
                        if (rect.left > SIDEBAR_WIDTH) {
                            pdfNodes.push({node: walker.currentNode, parent, rect});
                        }
                    }
                }
            }
            if (pdfNodes.length === 0) return {found: false, reason: 'no_pdf'};

            // 步骤2: 取最下方的 PDF（最新上传的）
            pdfNodes.sort((a, b) => b.rect.top - a.rect.top);
            const latestPdf = pdfNodes[0];

            // 步骤3: 从 PDF 元素向上追溯，找包含"生成播客"按钮的最近父容器
            let container = latestPdf.parent;
            for (let i = 0; i < 12 && container; i++) {
                // 只在该容器内查找按钮
                const btns = container.querySelectorAll('button, div[role="button"], span[role="button"], a[role="button"]');
                for (const btn of btns) {
                    const btnRect = btn.getBoundingClientRect();
                    const btnText = (btn.textContent || btn.innerText || '').trim();
                    // 必须是"生成播客"，且位置在 PDF 下方或附近
                    if ((btnText === '生成播客' || btnText.includes('生成播客')) &&
                        btnRect.left > SIDEBAR_WIDTH &&
                        btnRect.top > latestPdf.rect.top - 50) { // 在 PDF 下方或附近
                        btn.click();
                        return {
                            found: true,
                            text: btnText,
                            tag: btn.tagName,
                            pdfTop: latestPdf.rect.top,
                            btnTop: btnRect.top
                        };
                    }
                }
                container = container.parentElement;
            }

            return {found: false, reason: 'no_button_in_card'};
        }
        """
    )
    if clicked and clicked.get("found"):
        log(f"在最新 PDF 卡片内点击'生成播客': text='{clicked.get('text')}' (PDF top={clicked.get('pdfTop')}, btn top={clicked.get('btnTop')})")
        await asyncio.sleep(1)
        return True

    log_warn(f"在 PDF 卡片内未找到'生成播客': {clicked.get('reason')}")

    # 兜底：如果卡片内没找到，在整个主内容区最下方找"生成播客"
    # 但必须确保不在 sidebar 内
    clicked = await page.evaluate(
        """
        () => {
            const SIDEBAR_WIDTH = 320;
            const all = document.querySelectorAll('button, div, span, a, [role="button"]');
            let candidates = [];
            for (const el of all) {
                const rect = el.getBoundingClientRect();
                const text = (el.textContent || el.innerText || '').trim();
                if ((text === '生成播客' || text.includes('生成播客')) &&
                    rect.left > SIDEBAR_WIDTH && rect.width > 0 && rect.height > 0) {
                    candidates.push({el, rect, text});
                }
            }
            // 取最下方的（最新的）
            candidates.sort((a, b) => b.rect.top - a.rect.top);
            if (candidates.length > 0) {
                candidates[0].el.click();
                return {found: true, text: candidates[0].text, top: candidates[0].rect.top};
            }
            return {found: false};
        }
        """
    )
    if clicked and clicked.get("found"):
        log(f"兜底：点击主内容区最下方的'生成播客': text='{clicked.get('text')}' (top={clicked.get('top')})")
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

            # 检测豆包生成失败提示（关键：检测到失败立即跳过，不等超时）
            page_text = await page.inner_text('body')
            if page_text:
                # 常见失败关键词
                fail_keywords = [
                    "抱歉，暂时无法生成播客",
                    "抱歉，无法生成",
                    "生成失败",
                    "服务繁忙",
                    "请稍后重试",
                    "暂时无法处理",
                    "请求过于频繁",
                ]
                for keyword in fail_keywords:
                    if keyword in page_text:
                        log_error(f"检测到豆包生成失败: '{keyword}' — 跳过此文件，继续下一个")
                        await take_debug_screenshot(page, "podcast_generation_failed")
                        return False

            # 也检测页面上的 error/fail 类元素
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

            # 上传 PDF（失败时重试，不跳过）
            upload_success = False
            for attempt in range(3):
                if await upload_pdf(page, pdf_path):
                    upload_success = True
                    break
                log_warn(f"上传失败，第 {attempt + 1}/3 次重试...")
                await asyncio.sleep(5)
            if not upload_success:
                log_error(f"上传失败 3 次，跳过: {pdf_path.name}")
                failed.add(pdf_path.name)
                progress["failed"] = list(failed)
                save_progress(progress)
                continue

            # 点击"生成播客"按钮（失败时重试，不跳过）
            gen_success = False
            for attempt in range(3):
                if await click_generate_podcast(page):
                    gen_success = True
                    break
                log_warn(f"点击'生成播客'失败，第 {attempt + 1}/3 次重试...")
                await asyncio.sleep(3)
            if not gen_success:
                log_error(f"点击'生成播客'失败 3 次，跳过: {pdf_path.name}")
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

            # 处理完一个后等待 1 秒，让页面稳定后再处理下一个
            if idx < len(pending):
                log("等待 1 秒后处理下一个 PDF...")
                await asyncio.sleep(1)

        log(f"\n{'='*50}")
        log("所有 PDF 处理完毕")
        log(f"成功: {len(processed)} 个")
        log(f"失败: {len(failed)} 个")
        log(f"{'='*50}")

        # 保存当前聊天页面 URL，供后续下载使用
        current_url = page.url
        url_file = SCRIPT_DIR / "chat_url.txt"
        try:
            with open(url_file, "w", encoding="utf-8") as f:
                f.write(current_url)
            log(f"聊天地址已保存: {url_file} -> {current_url}")
        except Exception as e:
            log_warn(f"保存聊天地址失败: {e}")

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
