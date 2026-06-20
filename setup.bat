@echo off
echo [1/3] 安装 Python 依赖...
pip install -r requirements.txt

echo [2/3] 安装 Playwright 浏览器...
playwright install chromium

echo [3/3] 完成！可以运行了。
pause
