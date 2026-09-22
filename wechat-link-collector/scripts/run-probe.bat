@echo off
chcp 65001 >nul
setlocal
title 微信 UIA 只读探针
cd /d "%~dp0"

rem ============================================================
rem  只读探针：把微信各个窗口扫一遍，判断哪些窗口能被 UI Automation 读到，
rem  并给出结论 A / B / C / D。
rem
rem  注：老的「历史消息」窗口在 PC 微信 3.9.x 上**已经下线**，现在承担
rem  文章索引的是「公众号主页窗口」（类名 H5SubscriptionProfileWnd）。
rem  探针会把当前所有微信窗口都扫一遍，别预设哪个窗口有内容。
rem
rem  只读：只发 WM_GETOBJECT（无害的无障碍查询消息），不模拟输入、不点击。
rem ============================================================

call "%~dp0_findpy.bat" pywinauto
if errorlevel 1 goto :need

echo ===== 先打开目标公众号的主页窗口，再继续 =====
echo.
"%PY%" "%~dp0probe_wechat_uia.py" %*
echo.
echo ===== 上面这段输出请整份复制回给 agent =====
echo.
pause
exit /b 0

:need
echo.
echo 探针跑不了：缺少 pywinauto。按上面的提示装好后再试。
pause
exit /b 9
