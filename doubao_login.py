#!/usr/bin/env python3
"""
豆包登录态刷新工具
用法：python doubao_login.py
功能：打开浏览器让你扫码登录，登录成功后自动保存状态到 doubao_state.json
"""
import asyncio
from pathlib import Path
from playwright.async_api import async_playwright

BASE = Path(__file__).parent
STATE_FILE = BASE / "doubao_state.json"
URL = "https://www.doubao.com"

async def main():
    print("=" * 50)
    print("豆包登录态刷新工具")
    print("=" * 50)
    print("\n即将打开浏览器，请完成扫码/手机号登录...")
    print("登录成功后，浏览器会自动关闭并保存状态。\n")
    
    async with async_playwright() as p:
        # 尝试加载旧状态（如果有）
        storage_state = str(STATE_FILE) if STATE_FILE.exists() else None
        
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context(
            storage_state=storage_state,
            viewport={"width": 1280, "height": 800}
        )
        page = await context.new_page()
        
        # 打开豆包
        await page.goto(URL, wait_until="domcontentloaded")
        
        print("浏览器已打开。请登录豆包...")
        print("登录成功后，按回车键保存登录态并退出。")
        print("如果不需要登录（已经是登录状态），直接按回车即可。\n")
        
        input("[按回车保存登录态] ")
        
        # 保存状态
        await context.storage_state(path=str(STATE_FILE))
        await browser.close()
        
        print(f"\n✅ 登录态已保存到: {STATE_FILE}")
        print("现在可以运行 doubao_full.py 或分步脚本了。\n")

if __name__ == "__main__":
    asyncio.run(main())
