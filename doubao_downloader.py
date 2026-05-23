#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
doubao_downloader.py
根据PDF名称精确下载对应播客，仅保存音频（不生成笔记）

用法:
    python doubao_downloader.py <URL> <pdf名称1> <pdf名称2> ...
    名称要带 .pdf 后缀，或脚本自动补全
"""

import asyncio
import re
import shutil
import sys
from pathlib import Path

from playwright.async_api import async_playwright

CHAT_URL = sys.argv[1] if len(sys.argv) > 1 and ("doubao.com" in sys.argv[1] or sys.argv[1].startswith("http")) else "https://www.doubao.com/chat/38424121600911362"
if "--all" in sys.argv:
    import json
    try:
        with open(Path(__file__).parent / "podcasts_list.json", "r", encoding="utf-8") as f:
            podcasts = json.load(f)
        TARGET_PDFS = [pc["pdf"] for pc in podcasts]
        print(f"[信息] 从JSON加载了 {len(TARGET_PDFS)} 个播客")
    except Exception as e:
        print(f"[错误] 无法加载 podcasts_list.json: {e}")
        TARGET_PDFS = []
elif "--batch" in sys.argv:
    batch_file = sys.argv[sys.argv.index("--batch") + 1]
    try:
        with open(batch_file, "r", encoding="utf-8") as f:
            TARGET_PDFS = [line.strip() for line in f if line.strip()]
        print(f"[信息] 从 {batch_file} 加载了 {len(TARGET_PDFS)} 个播客")
    except Exception as e:
        print(f"[错误] 无法加载 {batch_file}: {e}")
        TARGET_PDFS = []
else:
    TARGET_PDFS = [a for a in sys.argv[1:] if not a.startswith("-") and "doubao.com" not in a and not a.startswith("http")]

DOWNLOADS_DIR = Path.home() / "Downloads"
OBSIDIAN_VAULT = Path.home() / "Documents" / "Obsidian" / "申论真题"
AUDIO_DIR = OBSIDIAN_VAULT / "附件" / "音频"
STATE_FILE = Path(__file__).parent / "doubao_state.json"
LOGIN_WAIT_SECONDS = 8
DOWNLOAD_TIMEOUT = 180


def sanitize(name: str) -> str:
    name = re.sub(r'[\\/:*?"<>|]', '_', name).strip()
    return name


async def scroll_up(page):
    await page.evaluate("""
        () => {
            const containers = document.querySelectorAll('div');
            for (const c of containers) {
                const style = window.getComputedStyle(c);
                if ((style.overflowY === 'auto' || style.overflowY === 'scroll') 
                    && c.scrollHeight > c.clientHeight + 200) {
                    c.scrollTop = Math.max(0, c.scrollTop - 800);
                }
            }
            window.scrollTo(0, Math.max(0, window.scrollY - 800));
        }
    """)


async def find_and_click(page, search_text: str) -> bool:
    """在浏览器内查找包含 search_text 的播客卡片并点击下载按钮"""
    return await page.evaluate("""
        (searchText) => {
            const walker = document.createTreeWalker(
                document.body, NodeFilter.SHOW_TEXT, null, false
            );
            let node;
            while (node = walker.nextNode()) {
                if (node.textContent.includes(searchText)) {
                    let el = node.parentElement;
                    for (let i = 0; i < 15; i++) {
                        if (!el) break;
                        const pluginId = el.getAttribute('data-plugin-identifier');
                        if (pluginId && pluginId.includes('receive-podcast-content')) {
                            el.scrollIntoView({behavior: 'instant', block: 'center'});
                            const dl = el.querySelector('[class*="download" i]');
                            if (dl) {
                                dl.click();
                                return true;
                            }
                            // 备选：找svg下载图标
                            const btns = el.querySelectorAll('button, [role="button"]');
                            for (const btn of btns) {
                                if (btn.querySelector('svg') && btn.innerHTML.includes('download')) {
                                    btn.click();
                                    return true;
                                }
                            }
                            return false;
                        }
                        el = el.parentElement;
                    }
                }
            }
            return false;
        }
    """, search_text)


async def download_single(page, pdf_name: str) -> bool:
    target_name = sanitize(Path(pdf_name).stem)
    search_text = pdf_name if pdf_name.endswith('.pdf') else f"{pdf_name}.pdf"
    
    print(f"\n[处理] 目标: {target_name}")
    
    # 断点续传：检查是否已下载
    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    existing = list(AUDIO_DIR.glob(f"{target_name}.*"))
    if existing:
        print(f"[跳过] 已存在: {existing[0].name}")
        return True
    
    # 下载监听
    download_future = asyncio.get_event_loop().create_future()
    def handle_download(download):
        if not download_future.done():
            download_future.set_result(download)
    page.on("download", handle_download)
    
    # 滚动查找并点击
    clicked = False
    for attempt in range(80):
        clicked = await find_and_click(page, search_text)
        if clicked:
            break
        await scroll_up(page)
        await asyncio.sleep(0.6)
    
    if not clicked:
        print(f"[错误] 滚动80次仍未找到 [{search_text}]")
        return False
    
    # 等待下载
    try:
        download = await asyncio.wait_for(download_future, timeout=DOWNLOAD_TIMEOUT)
    except asyncio.TimeoutError:
        print(f"[错误] [{target_name}] 下载超时")
        return False
    
    # 保存并重命名
    temp_path = DOWNLOADS_DIR / download.suggested_filename
    try:
        await download.save_as(str(temp_path))
    except Exception as e:
        print(f"[错误] 保存失败: {e}")
        return False
    
    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    final_audio = AUDIO_DIR / f"{target_name}{temp_path.suffix}"
    
    counter = 1
    while final_audio.exists():
        final_audio = AUDIO_DIR / f"{target_name}_{counter}{temp_path.suffix}"
        counter += 1
    
    # 等待浏览器释放文件句柄
    for retry in range(10):
        try:
            shutil.move(str(temp_path), str(final_audio))
            print(f"[完成] {final_audio}")
            return True
        except PermissionError:
            await asyncio.sleep(0.5)
    
    print(f"[错误] 文件被占用，无法移动: {temp_path}")
    return False


async def main():
    if not TARGET_PDFS:
        print("[错误] 请提供要下载的PDF名称")
        print(f"用法: python {Path(__file__).name} <URL> <pdf1> <pdf2> ...")
        return
    
    async with async_playwright() as p:
        print("[启动] 正在启动浏览器...")
        browser = await p.chromium.launch(
            headless=False,
            args=["--disable-blink-features=AutomationControlled"]
        )
        
        context_kwargs = {
            "accept_downloads": True,
            "viewport": {"width": 1280, "height": 900},
        }
        if STATE_FILE.exists():
            print("[信息] 加载已保存的登录态...")
            context_kwargs["storage_state"] = str(STATE_FILE)
        
        context = await browser.new_context(**context_kwargs)
        page = await context.new_page()
        
        print(f"[导航] 打开 {CHAT_URL}")
        try:
            await page.goto(CHAT_URL, wait_until="domcontentloaded", timeout=60000)
        except Exception as e:
            print(f"[错误] 页面加载失败: {e}")
            await browser.close()
            return
        
        await asyncio.sleep(3)
        
        print(f"\n[等待] {LOGIN_WAIT_SECONDS} 秒后自动开始下载...")
        await asyncio.sleep(LOGIN_WAIT_SECONDS)
        
        await context.storage_state(path=str(STATE_FILE))
        
        # 逐个下载
        success_count = 0
        for pdf in TARGET_PDFS:
            success = await download_single(page, pdf)
            if success:
                success_count += 1
                await asyncio.sleep(2)
        
        print(f"\n{'='*60}")
        print(f"完成: {success_count}/{len(TARGET_PDFS)}")
        print(f"文件保存在: {AUDIO_DIR}")
        print(f"{'='*60}")
        
        await asyncio.sleep(3)
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
