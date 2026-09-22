@echo off
chcp 65001 >nul
rem ============================================================
rem  Python 探测器 —— 供同目录的 run-*.bat 用 call 调用，不要直接双击。
rem
rem    call "%~dp0_findpy.bat"              只要求能找到 Python
rem    call "%~dp0_findpy.bat" pywinauto     还要求装好 pywinauto
rem
rem  成功：设置环境变量 PY = python.exe 的完整路径（在调用方作用域里生效）
rem  失败：exit /b 9，并打印修复办法
rem
rem  为什么要有这个文件：UIA 相关的脚本依赖 pywinauto（见技能根目录的
rem  requirements.txt），而当前解释器不一定装了它。直接敲 python 往往会因为
rem  缺依赖而失败，让人误以为脚本坏了。
rem ============================================================

set "PY="

rem 1) WorkBuddy 运行时的托管虚拟环境（若你在 WorkBuddy 里跑，它通常已装好 pywinauto；
rem    外部用户不会有这个路径，自动跳到下一步）
set "VENV=%USERPROFILE%\.workbuddy\binaries\python\envs\default\Scripts\python.exe"
if exist "%VENV%" set "PY=%VENV%"

rem 2) py launcher
if not defined PY for /f "delims=" %%I in ('py -3 -c "import sys;print(sys.executable)" 2^>nul') do set "PY=%%I"

rem 3) PATH 里的 python
if not defined PY for /f "delims=" %%I in ('python -c "import sys;print(sys.executable)" 2^>nul') do set "PY=%%I"

if not defined PY (
  echo [!] 没找到可用的 Python。请先安装 Python 3 及其 py launcher，然后重新双击。
  exit /b 9
)

if /i "%~1"=="pywinauto" (
  "%PY%" -c "import pywinauto" >nul 2>&1
  if errorlevel 1 (
    echo [!] 找到了 Python，但它没装 pywinauto：
    echo      "%PY%"
    echo.
    echo     修复办法（复制这一行执行）：
    echo      "%PY%" -m pip install pywinauto
    echo.
    echo     或者先装依赖清单（技能根目录的 requirements.txt）：
    echo      "%PY%" -m pip install -r "%~dp0..\requirements.txt"
    exit /b 9
  )
)

exit /b 0
