@echo off
chcp 65001 >nul
setlocal
title 微信文章链接采集 - 全流程
cd /d "%~dp0"

rem ============================================================
rem  三步全流程：预检 → 清单 → 采集
rem
rem  跑之前请先做完两步：
rem    1) 微信「设置 → 通用设置」里开启「使用系统默认浏览器打开网页」
rem       （没开的话文章会落到微信内置浏览器，那里读不到任何 URL）
rem    2) 在微信里打开目标公众号，并在那个窗口里**手动把列表滚到底**
rem
rem  采集期间会抢前台，请不要动鼠标键盘。想中断按 Ctrl+C。
rem  重复采集是安全的：会按 URL 去重并合并进已有文件。
rem
rem  输出目录: 脚本同级的 output\  （links.jsonl / links.txt）
rem ============================================================

set "OUT=%~dp0output"
if not exist "%OUT%" mkdir "%OUT%"

call "%~dp0_findpy.bat" pywinauto
if errorlevel 1 goto :need

echo ===== 1/3 环境预检（不会打开任何文章）=====
echo.
"%PY%" "%~dp0collect_via_browser.py" --doctor
echo.
echo 上面若有 ❌ 项，请先处理掉再继续（直接按 Ctrl+C 退出即可）。
pause

echo.
echo ===== 2/3 文章清单（先确认条数对不对）=====
echo.
"%PY%" "%~dp0collect_via_browser.py" --list
echo.

echo ===== 3/3 开始采集 =====
echo  输出: "%OUT%\links.jsonl"  和  "%OUT%\links.txt"
echo  ！期间会抢前台，请不要动鼠标键盘；中断按 Ctrl+C。
echo.
"%PY%" "%~dp0collect_via_browser.py" -o "%OUT%\links.jsonl" %*

echo.
echo ===== 采集结束 =====
echo  下一步：双击 run-report.bat 生成可读的链接清单报告。
echo.
pause
exit /b 0

:need
echo.
echo 跑不了：缺少 pywinauto。按上面的提示装好后再试。
pause
exit /b 9
