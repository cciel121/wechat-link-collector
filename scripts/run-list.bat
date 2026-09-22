@echo off
chcp 65001 >nul
setlocal
title 微信文章链接采集 - 读文章清单
cd /d "%~dp0"

rem ============================================================
rem  读「公众号主页窗口」里的文章清单（只读，不打开文章）
rem
rem  跑之前请先做完两步：
rem    1) 在微信里打开目标公众号 —— 会出现一个标题为「公众号」的独立窗口
rem       （老的「历史消息」窗口已下线，不是那个）
rem    2) **在这个窗口里手动把列表滚到底** —— 这一步决定了能不能拿到全部文章
rem       实测不滚到底只能读到 ~20 篇，滚到底能读到 103 篇（某号 2020→2026 全部）
rem ============================================================

set "OUT=%~dp0output"
if not exist "%OUT%" mkdir "%OUT%"

call "%~dp0_findpy.bat" pywinauto
if errorlevel 1 goto :need

echo ===== 1/2 文章清单（日期 / 标题 / 阅读数）=====
echo.
"%PY%" "%~dp0collect_via_browser.py" --list
echo.
echo ===== 2/2 导出索引（给生成报告用）=====
echo.
"%PY%" "%~dp0read_profile_list.py" -o "%OUT%\articles.jsonl"
echo.
echo 索引已写到: "%OUT%\articles.jsonl"
echo 下一步：双击 run-collect.bat 开始采集链接。
echo.
pause
exit /b 0

:need
echo.
echo 跑不了：缺少 pywinauto。按上面的提示装好后再试。
pause
exit /b 9
