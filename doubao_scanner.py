#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
doubao_scanner.py
豆包播客扫描器：持续向上滚动聊天记录，收集所有播客信息

用法:
    python doubao_scanner.py <URL>
"""

import asyncio
import re
import sys
from pathlib import Path

from playwright.async_api import async_playwright

CHAT_URL = sys.argv[1] if len(sys.argv) > 1 else "https://www.doubao.com/chat/38424121600911362"
STATE_FILE = Path(__file__).parent / "doubao_state.json"
LOGIN_WAIT_SECONDS = 8


async def scroll_up_and_collect(page):
    """持续向上滚动，收集所有播客卡片信息"""
    all_podcasts = []
    seen_pdfs = set()
    
    print("[扫描] 开始向上滚动加载历史消息...")
    
    # 先滚动到底部（确保我们在最新位置）
    await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
    await asyncio.sleep(1)
    
    prev_scroll_top = None
    stuck_count = 0
    
    for i in range(100):  # 最多滚动100次
        # 1. 收集当前视口内的播客卡片
        # 尝试多种选择器定位播客卡片
        cards = await page.query_selector_all(
            '[data-plugin-identifier*="receive-podcast-content"]'
        )
        
        for card in cards:
            try:
                text = await card.inner_text()
                # 提取PDF名称
                pdf_matches = re.findall(r'([^\\/:*?"<>|\s]+\.pdf)', text, re.IGNORECASE)
                for pdf in pdf_matches:
                    if pdf not in seen_pdfs:
                        seen_pdfs.add(pdf)
                        # 提取播客标题（通常在文本中的《》里或第一行）
                        title = "未知标题"
                        title_match = re.search(r'《(.+?)》', text)
                        if title_match:
                            title = title_match.group(1)
                        else:
                            # 取非PDF的第一行作为标题
                            lines = [l.strip() for l in text.split('\n') if l.strip() and '.pdf' not in l]
                            if lines:
                                title = lines[0][:50]
                        
                        # 提取时长
                        duration = "未知"
                        dur_match = re.search(r'(\d+:\d{2})', text)
                        if dur_match:
                            duration = dur_match.group(1)
                        
                        all_podcasts.append({
                            'pdf': pdf,
                            'title': title,
                            'duration': duration,
                            'preview': text[:150].replace('\n', ' ')
                        })
            except Exception:
                pass
        
        # 2. 向上滚动
        # 找到滚动容器（overflow 的 div）
        current_scroll_top = await page.evaluate("""
            () => {
                let candidates = [];
                for (const div of document.querySelectorAll('div')) {
                    const style = window.getComputedStyle(div);
                    if ((style.overflowY === 'auto' || style.overflowY === 'scroll') 
                        && div.scrollHeight > div.clientHeight + 200) {
                        candidates.push({
                            el: div,
                            scrollTop: div.scrollTop,
                            scrollHeight: div.scrollHeight,
                            clientHeight: div.clientHeight
                        });
                    }
                }
                // 找scrollHeight最大的那个（通常是主列表）
                if (candidates.length > 0) {
                    candidates.sort((a, b) => b.scrollHeight - a.scrollHeight);
                    const main = candidates[0];
                    main.el.scrollTop = Math.max(0, main.el.scrollTop - 600);
                    return main.el.scrollTop;
                }
                // fallback: window scroll
                window.scrollTo(0, Math.max(0, window.scrollY - 600));
                return window.scrollY;
            }
        """)
        
        await asyncio.sleep(0.8)
        
        # 3. 检查是否到顶（scrollTop 不再变化）
        if current_scroll_top == prev_scroll_top:
            stuck_count += 1
            if stuck_count >= 3:
                print("[扫描] 已到达页面顶部，停止滚动")
                break
        else:
            stuck_count = 0
        
        prev_scroll_top = current_scroll_top
        
        if i % 5 == 0:
            print(f"[扫描] 已滚动 {i} 次，发现 {len(all_podcasts)} 个播客...")
    
    return all_podcasts


async def main():
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
        
        print(f"\n[等待] {LOGIN_WAIT_SECONDS} 秒后自动开始扫描...")
        await asyncio.sleep(LOGIN_WAIT_SECONDS)
        
        # 保存登录态
        await context.storage_state(path=str(STATE_FILE))
        
        # 扫描所有播客
        podcasts = await scroll_up_and_collect(page)
        
        # 保存JSON
        import json
        with open('podcasts_list.json', 'w', encoding='utf-8') as f:
            json.dump(podcasts, f, ensure_ascii=False, indent=2)
        
        # 输出结果
        print(f"\n{'='*70}")
        print(f"扫描完成！共发现 {len(podcasts)} 个播客：")
        print(f"{'='*70}")
        
        for i, pc in enumerate(podcasts, 1):
            print(f"\n[{i}] PDF: {pc['pdf']}")
            print(f"    标题: {pc['title']}")
            print(f"    时长: {pc['duration']}")
            print(f"    预览: {pc['preview'][:80]}...")
        
        print(f"\n{'='*70}")
        print("结果已保存到 podcasts_list.json")
        print("请告诉我你要下载哪几个（输入序号，如 1 3 5，或输入 'all' 下载全部）")
        print(f"{'='*70}")
        
        # 截图保存最后状态
        await page.screenshot(path="doubao_debug/scan_final.png", full_page=True)
        print("\n[截图] 最终页面状态已保存: doubao_debug/scan_final.png")
        
        await asyncio.sleep(2)
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
