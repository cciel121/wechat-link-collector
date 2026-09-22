@echo off
chcp 65001 >nul
setlocal
title 微信文章链接采集 - 生成报告
cd /d "%~dp0"

rem ============================================================
rem  把「文章索引 + 链接清单」合成一份给人看的报告
rem
rem  一次出两份、内容相同：
rem    链接清单.md    ------ 给人看 / 复制
rem    链接清单.html  ------ 双击就能用浏览器打开，标题和链接都能点
rem  两份由同一份数据渲染，不会一个改了另一个忘改。
rem
rem  为什么用索引排序而不是链接文件的顺序：links.jsonl 的顺序是**采集顺序**，
rem  重试补采的单篇会被追加到末尾，直接拿它排表会把"时间跨度"印错。
rem  索引里有、链接里没有的会**明确列在「未采到链接」一节**，不会静默丢掉。
rem
rem  不需要 pywinauto，纯离线脚本。
rem ============================================================

set "OUT=%~dp0output"

call "%~dp0_findpy.bat"
if errorlevel 1 goto :need

if not exist "%OUT%\links.jsonl" (
  echo [!] 没找到 "%OUT%\links.jsonl"。
  echo     请先双击 run-collect.bat 完成采集。
  echo     如果你把结果放在别处，请改用命令行：
  echo       "%PY%" "%~dp0make_link_report.py" --index 索引.jsonl --links 链接.jsonl -o 清单.md
  echo.
  pause
  exit /b 3
)

if not exist "%OUT%\articles.jsonl" (
  echo [!] 没找到 "%OUT%\articles.jsonl"（文章索引）。
  echo     报告仍会生成，但会**退回采集顺序**排列，并且不显示「时间跨度」——
  echo     补采过的篇目排在末尾，跨度看着会是错的。
  echo     想要正确顺序：先双击 run-list.bat 生成索引，再跑本脚本。
  echo.
)

echo ===== 生成链接清单报告 =====
echo.
"%PY%" "%~dp0make_link_report.py" ^
  --index "%OUT%\articles.jsonl" ^
  --links "%OUT%\links.jsonl" ^
  -o "%OUT%\链接清单.md"
echo.
echo 报告:   "%OUT%\链接清单.md"
echo 网页版: "%OUT%\链接清单.html"  —— 双击打开，标题和链接都能点
echo 纯链接: "%OUT%\links.txt"  —— 一行一个 URL
echo.
pause
exit /b 0

:need
echo.
echo 跑不了：没找到可用的 Python。
pause
exit /b 9
