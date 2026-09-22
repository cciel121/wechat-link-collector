@echo off
chcp 65001 >nul
setlocal
title 微信文章链接采集 - 环境预检
cd /d "%~dp0"

rem ============================================================
rem  环境预检（只读，不打开任何文章，约 5 秒）
rem
rem  跑全量采集要十几分钟并且会抢前台，所以先在这里把
rem  「微信没开」「打开方式没设成系统默认浏览器」「列表没滚到底」
rem  这类问题查出来。有 ❌ 就先别跑采集。
rem ============================================================

call "%~dp0_findpy.bat" pywinauto
if errorlevel 1 goto :need

echo ===== 环境预检（不会打开任何文章）=====
echo.
"%PY%" "%~dp0collect_via_browser.py" --doctor
echo.
pause
exit /b 0

:need
echo.
echo 预检跑不了：缺少 pywinauto。按上面的提示装好后再试。
pause
exit /b 9
