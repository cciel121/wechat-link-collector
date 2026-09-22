#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""读取微信「公众号」窗口里的文章清单（只读，不点击）。

背景（本机实测，2026-09）
------------------------
微信 PC 端的老「历史消息」窗口已下线，但**公众号主页窗口**还在，窗口类名
`H5SubscriptionProfileWnd`，属 WeChat.exe，Chromium 内核。它是搜索/点击公众号
后打开的那个「公众号」窗口（带「全部 / 文章」标签）。

这个窗口可以被 UI Automation **完整读到**，包括每条文章的日期、标题、阅读数：

    Text  华东师大全民数字素养培训基地
    Text  国家级全民数字素养与技能培训基地（华东师范大学）
    Hyperlink 全部 / 文章
    Text  4月23日
    Text  智启未来！ECNUer专场共探“教师版小龙虾”培训活动圆满成功！
    Text  阅读 251 赞 6 2个朋友看过
    ...

要点：
  - Document 的子节点是**扁平 Text**，日期 / 标题 / 阅读数 三元一组，
    **没有"行容器"**，所以只能按「日期后面紧跟的 Text 就是标题」来配对。
  - 窗口**最小化时也能读到**（实测 Minimized 状态下 Text=68 全部可读）。
  - 列表是懒加载的，末尾会出现 `正在加载...`，需要滚动才能拿到更多。
  - 本脚本**只读**：不点击、不打开文章、不修改任何东西。

用法
----
    python read_profile_list.py                       # 读当前公众号窗口
    python read_profile_list.py --scroll 6            # 向下滚动 6 轮，尽量读全
    python read_profile_list.py -o articles.jsonl

产出
----
    articles.jsonl  每行一条 {date, title, reads, likes, account, gh_id}
    awards.txt      （未用）文章链接仍需另行获取——见 SKILL.md「链接从哪来」
"""

import argparse
import ctypes
import json
import os
import re
import sys
import time
from ctypes import wintypes

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    pass

from pywinauto import Desktop                                          # noqa: E402
from pywinauto.uia_defines import IUIA                                 # noqa: E402

try:
    from comtypes.gen import UIAutomationClient as UIAc
except ImportError:
    UIAc = None

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from probe_wechat_uia import (CT_DOCUMENT, CT_TEXT,                     # noqa: E402
                              find_all, flat, prop, wake_accessibility,
                              P_NAME, P_RECT, P_VALUE)
from profile_parse import extract_pairs, has_loading_more               # noqa: E402

PROFILE_CLASS = 'H5SubscriptionProfileWnd'
GH_RE = re.compile(r'userName=(gh_[0-9a-zA-Z]+)')
NAME_RE = re.compile(r'showName=([^&]+)')
UIA_ScrollPatternId = 10004
UIA_ScrollItemPatternId = 10017
SCROLL_LARGE_INCREMENT = 3
WM_MOUSEWHEEL = 0x020A
user32 = ctypes.windll.user32


def _win_rect(hwnd):
    class R(ctypes.Structure):
        _fields_ = [('left', ctypes.c_long), ('top', ctypes.c_long),
                    ('right', ctypes.c_long), ('bottom', ctypes.c_long)]
    r = R()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    return (r.left, r.top, r.right, r.bottom)


def _scroll_via_item(iuia, root, say):
    """兜底 1：对最后一个可见元素调 ScrollItemPattern.ScrollIntoView()。"""
    if UIAc is None:
        say('      · ScrollItemPattern 跳过（UIAutomationClient 不可用）')
        return False
    iface = getattr(UIAc, 'IUIAutomationScrollItemPattern', None)
    if iface is None:
        say('      · ScrollItemPattern 跳过（接口不存在）')
        return False
    texts, _ = find_all(iuia, root, CT_TEXT, timeout=10)
    # 取 y 最大（最靠下）的那个 Text。
    # 注意 P_RECT(30001) 返回的是 **(left, top, width, height)**，不是 (l,t,r,b)！
    # 早先按 r[3]-r[1]（= height-top，通常为负）判有效性，导致每个元素都被跳过。
    best, best_y = None, None
    for t in texts:
        r = prop(t, P_RECT)
        try:
            if not r or len(r) != 4:
                continue
            left, top, w, h = r
            if w <= 0 or h <= 0:
                continue
            if best_y is None or top > best_y:
                best, best_y = t, top
        except Exception:
            continue
    if best is None:
        say('      · ScrollItemPattern 跳过（没有拿到有效 rect 的 Text）')
        return False
    try:
        ptr = best.GetCurrentPattern(UIA_ScrollItemPatternId)
        if ptr is None:
            say('      · ScrollItemPattern 返回空指针')
            return False
        ptr.QueryInterface(iface).ScrollIntoView()
        return True
    except Exception as e:
        say(f'      · ScrollItemPattern 失败: {type(e).__name__}: {e}')
        return False


def _scroll_via_wheel(hwnd, say, clicks=3):
    """兜底 2：直接给窗口发 WM_MOUSEWHEEL（lParam 用窗口中心，不移动真实鼠标）。"""
    try:
        l, t, rr, b = _win_rect(hwnd)
        x, y = (l + rr) // 2, (t + b) // 2
        lparam = ((y & 0xFFFF) << 16) | (x & 0xFFFF)
        for _ in range(clicks):
            # wParam: 高 16 位是 delta，-120 = 向下滚一格
            user32.SendMessageW(hwnd, WM_MOUSEWHEEL,
                                ctypes.c_ulong((-120 & 0xFFFF) << 16).value & 0xFFFFFFFF,
                                ctypes.c_long(lparam))
            time.sleep(0.08)
        return True
    except Exception as e:
        say(f'      · WM_MOUSEWHEEL 失败: {type(e).__name__}: {e}')
        return False


def _scroll_via_real_wheel(hwnd, say, clicks=5):
    """兜底 3：把窗口顶到最前 + 鼠标移到窗口中心 + 发真实滚轮。

    实测：Chromium 在窗口被遮挡时会把页面元素 rect 全部归零、并挂起布局，
    此时 SendMessage 版滚轮（兜底 2）完全无效。只有窗口真的在最前、
    且指针落在窗口内，滚轮才会生效。**会移动真实鼠标**，所以放在最后降级。
    """
    try:
        HWND_TOPMOST, SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE = -1, 0x1, 0x2, 0x10
        user32.SetWindowPos(wintypes.HWND(hwnd), wintypes.HWND(HWND_TOPMOST), 0, 0, 0, 0,
                            SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE)
        user32.SetForegroundWindow(wintypes.HWND(hwnd))
        time.sleep(0.6)
        l, t, rr, b = _win_rect(hwnd)
        cx, cy = (l + rr) // 2, (t + b) // 2
        user32.SetCursorPos(cx, cy)
        time.sleep(0.3)
        for _ in range(clicks):
            user32.mouse_event(0x0800, 0, 0, ctypes.c_ulong(-120).value, 0)  # WHEEL
            time.sleep(0.12)
        return True
    except Exception as e:
        say(f'      · 真实滚轮失败: {type(e).__name__}: {e}')
        return False


def raise_window(hwnd):
    """把窗口顶到最前（不改变大小/位置、不激活），让 Chromium 恢复布局。"""
    try:
        HWND_TOPMOST, SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE = -1, 0x1, 0x2, 0x10
        user32.ShowWindow(wintypes.HWND(hwnd), 9)          # SW_RESTORE
        user32.SetWindowPos(wintypes.HWND(hwnd), wintypes.HWND(HWND_TOPMOST), 0, 0, 0, 0,
                            SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE)
        user32.SetForegroundWindow(wintypes.HWND(hwnd))
        time.sleep(0.8)
        return True
    except Exception:
        return False


def scroll_document(doc, hwnd, iuia, root, say):
    """尽量向下滚动一屏。四条路依次降级，每条失败都当场报出来。

    实测结论（微信 3.9.12 / Chromium WebArea）：
      1. ScrollPattern          —— 必然抛 COMError 0x80131509，WebArea 不支持
      2. ScrollItemPattern      —— 依赖元素 rect，窗口被遮挡时 rect 全为 0，不可用
      3. WM_MOUSEWHEEL(SendMessage) —— 窗口不在最前时无效
      4. 真实滚轮（SetWindowPos 顶到最前 + SetCursorPos + mouse_event）—— 最可靠

    失败原因必须**立刻打印**：早先版本把警告写进不刷出的缓冲区，结果
    "滚动失败 → break" 看起来跟"列表已到底"一模一样——又是一个静默的零。
    """
    if UIAc is not None:
        iface = getattr(UIAc, 'IUIAutomationScrollPattern', None)
        if iface is not None:
            try:
                ptr = doc.GetCurrentPattern(UIA_ScrollPatternId)
                if ptr is not None:
                    ptr.QueryInterface(iface).Scroll(2, SCROLL_LARGE_INCREMENT)
                    return True, 'ScrollPattern'
            except Exception as e:
                say(f'      · ScrollPattern 不可用: {type(e).__name__}: {str(e)[:52]}')
    if _scroll_via_item(iuia, root, say):
        return True, 'ScrollItemPattern'
    # SendMessage 版滚轮**没有回执**，无法判断是否真的滚了：只有在窗口确实是
    # 前台窗口时才可能有意义，否则它会"假装成功"并挡住真正有效的真实滚轮。
    try:
        fg = user32.GetForegroundWindow()
        is_fg = (int(fg) == int(hwnd))
    except Exception:
        is_fg = False
    if is_fg and _scroll_via_wheel(hwnd, say):
        return True, 'WM_MOUSEWHEEL'
    if _scroll_via_real_wheel(hwnd, say):
        return True, '真实滚轮'
    if not is_fg and _scroll_via_wheel(hwnd, say):
        return True, 'WM_MOUSEWHEEL(可能无效)'
    return False, None


def parse_query(value):
    out = {}
    m = GH_RE.search(value or '')
    if m:
        out['gh_id'] = m.group(1)
    m = NAME_RE.search(value or '')
    if m:
        from urllib.parse import unquote
        out['gh_name'] = unquote(m.group(1))
    return out


def main():
    ap = argparse.ArgumentParser(description='读取微信公众号窗口的文章清单（只读）')
    ap.add_argument('--scroll', type=int, default=0, help='向下滚动的轮数（默认 0）')
    ap.add_argument('--foreground', action='store_true',
                    help='先把公众号窗口顶到最前（Chromium 被遮挡时会挂起布局，rect 全为 0）')
    ap.add_argument('--interval', type=float, default=1.6, help='每轮滚动后等待秒数')
    ap.add_argument('-o', '--out', default='articles.jsonl', help='输出文件')
    args = ap.parse_args()

    buf = []

    def say(s=''):
        buf.append(str(s))
        print(s)

    target = None
    for w in Desktop(backend='uia').windows():
        try:
            if w.element_info.class_name == PROFILE_CLASS:
                target = w
                break
        except Exception:
            continue
    if not target:
        say(f'未找到公众号窗口（类名 {PROFILE_CLASS}）。')
        say('请先在微信里搜索并打开目标公众号（会出现一个标题为「公众号」的独立窗口），')
        say('让它停在屏幕上或最小化都可以，然后重跑。')
        sys.exit(2)

    hwnd = target.handle
    say(f'公众号窗口 hwnd=0x{hwnd:X}  pid={target.element_info.process_id}')
    if args.foreground:
        say(f'  顶到最前: {"成功" if raise_window(hwnd) else "失败"}')
    wake_accessibility(hwnd)
    time.sleep(1.2)

    iuia = IUIA().iuia
    root = iuia.ElementFromHandle(hwnd)

    account, gh = '', {}
    seen, order = {}, []
    stalls = 0

    for round_ in range(args.scroll + 1):
        docs, derr = find_all(iuia, root, CT_DOCUMENT)
        if not docs:
            say(f'❌ 读不到 Document（{derr or "空"}）。窗口可能真的关了，或微信改版。')
            break
        doc = docs[0]
        doc_url = str(prop(doc, P_VALUE) or '')
        if not account:
            gh = parse_query(doc_url)
            account = gh.get('gh_name', '')
            say(f'账号: {account or "(未解析出名称)"}   原始ID: {gh.get("gh_id", "?")}')

        texts, _ = find_all(iuia, root, CT_TEXT)
        names = [str(prop(t, P_NAME) or '') for t in texts]
        rows = extract_pairs(names)

        new_n = 0
        for r in rows:
            k = r['title']
            if k in seen:
                if r['reads'] and not seen[k]['reads']:
                    seen[k].update(r)
                continue
            r['account'] = account
            r['gh_id'] = gh.get('gh_id', '')
            seen[k] = r
            order.append(r)
            new_n += 1

        loading = has_loading_more(names)
        say(f'  第 {round_ + 1} 轮: Text={len(names)} 本轮解析 {len(rows)} 条，'
            f'新增 {new_n} 条，累计 {len(order)} 条'
            + ('  [列表末尾有「正在加载...」]' if loading else ''))

        if round_ >= args.scroll:
            break
        if new_n == 0:
            stalls += 1
            if stalls >= 2:
                say(f'    ⚠️ 连续 {stalls} 轮没有新条目，停止滚动。')
                say('       可能是：①列表真的到底了；②滚动没生效（窗口被遮挡/不在最前）。')
                say('       想确认是哪一种，加 --foreground 重跑。')
                break
        else:
            stalls = 0
        ok, how = scroll_document(doc, hwnd, iuia, root, say)
        if not ok:
            say('    ⚠️ 四条程序化滚动路径都不通，本轮到此为止。')
            say('       **这不是"该号只有这些文章"** —— 实测同一脚本读到过 20 篇，也读到过 103 篇，')
            say('       差别只在页面加载了多少。请让用户**在窗口里手动把列表滚到底**，再重跑一次；')
            say('       本脚本会按标题去重并合并，可以放心累加。')
            break
        say(f'    滚动方式: {how}')
        time.sleep(args.interval)
        wake_accessibility(hwnd, times=1, gap=0.3)

    say('')
    say(f'=== 共 {len(order)} 篇文章 ===')
    say('')
    say(f'{"日期":<14}{"标题":<52}阅读')
    for r in order:
        say(f'{r["date"]:<14}{flat(r["title"], 50):<52}{r["reads"]}')

    if not order:
        say('')
        say('⚠️ 一条都没解析出来。请不要据此判断"该公众号没有文章"，')
        say('   先看上面的 Text 计数：若 Text>0 但解析为 0，说明页面结构变了，')
        say('   把整个输出发回以便修解析规则。')

    with open(args.out, 'w', encoding='utf-8') as fh:
        for r in order:
            fh.write(json.dumps(r, ensure_ascii=False) + '\n')
    say('')
    say(f'已写出: {os.path.abspath(args.out)}')
    say('注意：本清单**只有日期和标题，没有链接**——链接的取法见 SKILL.md「链接从哪来」。')


if __name__ == '__main__':
    main()
