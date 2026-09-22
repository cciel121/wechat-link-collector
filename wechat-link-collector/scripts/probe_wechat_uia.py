#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""探针：判断 PC 微信的各个窗口能不能被 UI Automation 读到。

注：老的「历史消息」窗口在 PC 微信 3.9.x 上**已经下线**，现在承担文章索引的是
「公众号主页窗口」（类名 `H5SubscriptionProfileWnd`）。所以**别再去找那个窗口** ——
探针会把**当前所有**微信窗口都扫一遍并给出结论 A/B/C/D，用结论说话，不要预设。

为什么先跑这个再写点击脚本
--------------------------
微信 PC 的文章窗口用的是 Chromium 内核。Chromium 的无障碍（UIA）树**默认是
关的、惰性开启**，而它一旦开了，网页就能被程序读到。

本探针已验证的两件事（在真实 Chrome 上跑通）：
  1. 发 `WM_GETOBJECT(OBJID_CLIENT)` 可以把惰性无障碍「叫醒」。
     实测：叫醒前 Document=0 / Text=0 / Hyperlink=0；
           叫醒后 Document=1 / Text=36 / Hyperlink=1。
  2. `Document` 元素的 **Value 属性就是页面 URL**，而且是永久短链形态。
     实测拿到 `https://mp.weixin.qq.com/s/_OplKEZ5BSBSyJKM5Nca2g`。

这两点合起来意味着：**如果微信的内置浏览器也支持无障碍，就完全不用点击、
不用复制链接**——直接读 Document 的 URL 即可。要验证的就是这一点。

注意：Chromium 的**超链接元素本身不带 href**（ValuePattern 是空的），能拿到的是
链接文字、包围盒、可点击坐标和 InvokePattern。所以「批量拿链接」要靠 Document，
「定位某个链接」要靠超链接元素。

用法
----
    # 1) 在微信里打开目标公众号的主页窗口（或任意一篇微信文章窗口），
    #    让它停在屏幕上，不要最小化
    # 2) 跑探针
    python probe_wechat_uia.py
    python probe_wechat_uia.py --pid 12345        # 定向探某个进程
    python probe_wechat_uia.py --all              # 全部窗口都探，排查用
    python probe_wechat_uia.py -o probe.txt

只读：只发 WM_GETOBJECT（无害的无障碍查询消息），不模拟任何输入、不点击。

依赖
----
    pip install pywinauto
"""

import argparse
import ctypes
import os
import sys
import threading
import time
from collections import Counter
from ctypes import wintypes

try:
    from pywinauto import Desktop
    from pywinauto.uia_defines import IUIA
except ImportError:
    sys.exit('缺少依赖，请先安装：pip install pywinauto')

try:
    import win32api
    import win32con
    import win32process
except ImportError:
    win32api = win32con = win32process = None

# ---- UIA 标准属性 ID / 枚举值（MSDN，直接用整数，不依赖 pywinauto 的常量名）----
P_RUNTIMEID = 30000
P_RECT      = 30001
P_PID       = 30002
P_CTYPE     = 30003
P_NAME      = 30005
P_AID       = 30011
P_CLASS     = 30012
P_CLICKABLE = 30014
P_FRAMEWORK = 30024
P_VALUE     = 30045
P_DEFAULTACTION = 30100

CT_HYPERLINK = 50005
CT_LISTITEM  = 50007
CT_BUTTON    = 50000
CT_TABITEM   = 50019
CT_TEXT      = 50020
CT_PANE      = 50033
CT_DOCUMENT  = 50030

SCOPE_CHILDREN = 2
SCOPE_DESCENDANTS = 4

WM_GETOBJECT = 0x003D
OBJID_CLIENT = 0xFFFFFFFC

WECHAT_HINTS = ('wechat',)
CHROMIUM_HINTS = ('Chrome_WidgetWin', 'Chrome_RenderWidgetHostHWND')

_PROC_CACHE = {}


def proc_name(pid):
    """pid -> 进程名。

    pywinauto 的 UIAElementInfo **没有** process_name 字段，直接取会抛
    AttributeError。早先版本把这异常吞掉，结果 18 个窗口被全部静默跳过、
    输出「0 个窗口」，看着像「微信没开」。这里显式实现，取不到返回空串，
    并让调用方把失败计数打出来——不静默。
    """
    if pid in _PROC_CACHE:
        return _PROC_CACHE[pid]
    name = ''
    if win32api is not None:
        try:
            h = win32api.OpenProcess(
                win32con.PROCESS_QUERY_INFORMATION | win32con.PROCESS_VM_READ, False, pid)
            try:
                name = os.path.basename(win32process.GetModuleFileNameEx(h, 0))
            finally:
                win32api.CloseHandle(h)
        except Exception:
            name = ''
    _PROC_CACHE[pid] = name
    return name


def flat(s, n=40):
    t = ' '.join(str(s or '').split())
    return t if len(t) <= n else t[:n] + '…'


def wake_accessibility(hwnd, times=3, gap=0.4):
    """发 WM_GETOBJECT 叫醒 Chromium 的惰性无障碍树。已在真实 Chrome 上验证有效。"""
    try:
        user32 = ctypes.windll.user32
        for _ in range(times):
            user32.SendMessageW(wintypes.HWND(hwnd), WM_GETOBJECT, 0, ctypes.c_long(OBJID_CLIENT))
            time.sleep(gap)
        return True
    except Exception:
        return False


def call_with_timeout(fn, timeout):
    """限时执行。Chromium 的 UIA 树可能极大，卡住会像脚本死了——
    超时必须如实报出来，不能变成静默的 0。"""
    box = {}

    def run():
        try:
            box['v'] = fn()
        except Exception as e:
            box['e'] = e

    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        return None, 'timeout'
    return box.get('v'), box.get('e')


def prop(el, pid):
    try:
        v = el.GetCurrentPropertyValue(pid)
        return '' if v is None else v
    except Exception:
        return ''


def find_all(iuia, root, ctype, timeout=20):
    def go():
        cond = iuia.CreatePropertyCondition(P_CTYPE, ctype)
        return root.FindAll(SCOPE_DESCENDANTS, cond)
    found, err = call_with_timeout(go, timeout)
    if err:
        return [], err
    if found is None:
        return [], None
    out = []
    try:
        for i in range(found.Length):
            out.append(found.GetElement(i))
    except Exception as e:
        return out, f'{type(e).__name__}: {e}'
    return out, None


CTYPE_NAMES = {
    50000: 'Button', 50001: 'Calendar', 50002: 'CheckBox', 50003: 'ComboBox',
    50004: 'Edit', 50005: 'Hyperlink', 50006: 'Image', 50007: 'ListItem',
    50008: 'List', 50009: 'MenuBar', 50010: 'Menu', 50011: 'MenuItem',
    50012: 'ProgressBar', 50013: 'RadioButton', 50014: 'ScrollBar',
    50015: 'Slider', 50016: 'Spinner', 50017: 'StatusBar', 50018: 'Tab',
    50019: 'TabItem', 50020: 'Text', 50021: 'ToolBar', 50022: 'ToolTip',
    50023: 'Tree', 50024: 'TreeItem', 50025: 'Custom', 50026: 'Group',
    50027: 'Thumb', 50028: 'DataGrid', 50029: 'DataItem', 50030: 'Document',
    50031: 'SplitButton', 50032: 'Window', 50033: 'Pane', 50034: 'Header',
    50035: 'HeaderItem', 50036: 'Table', 50037: 'TitleBar', 50038: 'Separator',
}
# 注意：这张表早先版本从 50008 起整体错位一位（漏了 List），于是 dump 出来的
# 控件树会把 TabItem 显示成 Tab、Text 显示成 ToolBar，排查时会被误导。
# **改这张表前先核对 MSDN 的 UIA Control Type ID**：程序逻辑用的是上面的
# CT_* 常量（那些是对的，实测 Text=50020 / Document=50030 都验证过），
# 这张表只影响 dump 的可读性。


def dump_tree(iuia, el, maxdepth, cap, deadline):
    """自绘窗口排查用：按层级 dump 控件类型 + 名称。

    Chromium 的树要惰性唤醒后才能遍历，且可能极大；这里用 deadline 硬限时，
    超时就如实标注出来（不做静默截断）。"""
    lines, seen = [], [0]

    def walk(node, depth, prefix):
        if depth > maxdepth or seen[0] >= cap or time.time() > deadline:
            return
        try:
            kids = node.FindAll(SCOPE_CHILDREN, iuia.CreateTrueCondition())
        except Exception as e:
            lines.append(f'{prefix}  <取子节点失败: {type(e).__name__}>')
            return
        try:
            n = kids.Length
        except Exception:
            return
        for i in range(n):
            if seen[0] >= cap or time.time() > deadline:
                return
            try:
                kid = kids.GetElement(i)
            except Exception:
                continue
            seen[0] += 1
            ct = prop(kid, P_CTYPE)
            name = flat(prop(kid, P_NAME), 56)
            cname = CTYPE_NAMES.get(ct, f'#{ct}')
            extra = ''
            if cname == 'Document':
                v = str(prop(kid, P_VALUE) or '')
                if v:
                    extra = f'  URL={flat(v, 70)}'
            lines.append(f'{prefix}{cname:<12}{name}{extra}')
            walk(kid, depth + 1, prefix + '  ')
    walk(el, 1, '')
    return lines, seen[0]


def main():
    ap = argparse.ArgumentParser(description='PC 微信 UIA 可读性探针（只读，不点击）')
    ap.add_argument('--all', action='store_true', help='非微信窗口也探')
    ap.add_argument('--pid', type=int, action='append', default=None,
                    help='只探指定进程（可重复）')
    ap.add_argument('--title', default='', help='只探标题含该子串的窗口')
    ap.add_argument('--no-wake', dest='wake', action='store_false',
                    help='不发 WM_GETOBJECT，直接读（用来对比"叫醒前"的状态）')
    ap.add_argument('-l', '--links', type=int, default=25, help='每窗口样例链接数（默认 25）')
    ap.add_argument('--tree', type=int, default=0,
                    help='额外 dump 控件树到该深度（0=不 dump）。排查自绘窗口/列表结构用')
    ap.add_argument('--tree-cap', type=int, default=200, help='树 dump 的节点数上限（默认 200）')
    ap.add_argument('-o', '--output', default='', help='同时保存到文件')
    ap.set_defaults(wake=True)
    args = ap.parse_args()

    buf = []

    def say(s=''):
        buf.append(str(s))
        print(s)

    uia = IUIA()
    iuia = uia.iuia

    say('=== 1. 顶层窗口 ===')
    say('')
    try:
        desk = Desktop(backend='uia')
        wins = desk.windows()
    except Exception as e:
        sys.exit(f'UIA 初始化失败: {e}')

    rows, skipped = [], []
    for w in wins:
        try:
            ei = w.element_info
            pid = ei.process_id
            rows.append({'w': w, 'hwnd': w.handle, 'name': ei.name or '',
                         'cls': ei.class_name or '', 'proc': pid, 'pname': proc_name(pid)})
        except Exception as e:
            skipped.append(f'{type(e).__name__}: {e}')

    say(f'{"进程":<18}{"窗口类名":<24}{"标题":<34}{"PID":<8}HWND')
    for r in rows:
        flag = '' if r['pname'] else '  <无进程名>'
        say(f"{flat(r['pname'] or '?', 16):<18}{flat(r['cls'], 22):<24}"
            f"{flat(r['name'], 32):<34}{r['proc']:<8}0x{r['hwnd']:X}{flag}")

    if skipped:
        say('')
        say(f'⚠️ 有 {len(skipped)} 个窗口读取失败（不是"不存在"，是取属性出错）：')
        for s in skipped[:5]:
            say(f'   {s}')

    cands = [r for r in rows if (any(h in (r['pname'] or '').lower() for h in WECHAT_HINTS)
                                 or any(h in (r['cls'] or '').lower() for h in WECHAT_HINTS)
                                 or any(h.lower() in (r['cls'] or '') for h in CHROMIUM_HINTS))]
    if args.pid:
        cands = [r for r in rows if r['proc'] in set(args.pid)]
    if args.title:
        cands = [r for r in rows if args.title.lower() in (r['name'] or '').lower()]

    say('')
    say(f'枚举到顶层窗口 {len(rows)} 个（{sum(1 for r in rows if not r["pname"])} 个拿不到进程名）')
    say(f'候选窗口（微信进程 / Chromium 内核）: {len(cands)} 个')

    filtered = bool(args.pid or args.title)
    if not cands and not args.all and not filtered:
        say('')
        say('未发现微信或 Chromium 窗口。')
        say('请先在微信里打开目标公众号的主页窗口，让它停在屏幕上，再重跑。')
        say('不要最小化——UIA 读不到最小化窗口的子元素。')
        say('若你确认微信正开着，加 --all 重跑，把窗口清单贴出来排查。')
    else:
        targets = cands if (cands or filtered) else rows
        if not targets:
            say('')
            say('未匹配到任何窗口，请核对上面清单里的 PID / 标题。')
        for i, r in enumerate(targets, 1):
            say('')
            kind = '候选' if r in cands else '其他'
            say(f"=== 2.{i} [{kind}] {r['pname']} | {flat(r['cls'], 22)} | "
                f"{flat(r['name'], 30)} | hwnd=0x{r['hwnd']:X} ===")
            say('')

            root = iuia.ElementFromHandle(r['hwnd'])

            def snapshot(tag):
                docs, e1 = find_all(iuia, root, CT_DOCUMENT)
                links, e2 = find_all(iuia, root, CT_HYPERLINK)
                texts, e3 = find_all(iuia, root, CT_TEXT)
                say(f'  [{tag}] Document={len(docs)}  Hyperlink={len(links)}  Text={len(texts)}'
                    + (f'  (异常: {e1 or e2 or e3})' if (e1 or e2 or e3) else ''))
                return docs, links

            say('  无障碍状态：')
            # 窗口被最小化/遮挡时，浏览器可能根本不构建无障碍树，所以先报状态
            try:
                off = root.GetCurrentPropertyValue(30022)   # IsOffscreen
                say(f'    窗口可见性: IsOffscreen={off}')
            except Exception:
                pass
            if args.wake:
                docs, links = snapshot('叫醒前')
                ok = wake_accessibility(r['hwnd'])
                if not ok:
                    say('    ⚠️ WM_GETOBJECT 发送失败')
                # 无障碍树是异步构建的（实测 Chrome 约 1s，Electron 更快）。
                # 单次快照容易误判成"读不到"，所以轮询几次，取节点最多的那次。
                for round_ in range(1, 7):
                    time.sleep(0.8)
                    d2, _ = find_all(iuia, root, CT_DOCUMENT, timeout=10)
                    l2, _ = find_all(iuia, root, CT_HYPERLINK, timeout=10)
                    if round_ in (1, 3, 6):
                        say(f'    [轮询 {round_}] Document={len(d2)}  Hyperlink={len(l2)}')
                    if len(l2) > len(links) or len(d2) > len(docs):
                        docs, links = d2, l2
                    if links:
                        break
            else:
                docs, links = snapshot('未叫醒')

            r['docs'], r['links'] = docs, links

            if docs:
                say('')
                say('  Document 元素（**Value 就是页面 URL**）：')
                for k, doc in enumerate(docs[:5], 1):
                    say(f"    [{k}] Name : {flat(prop(doc, P_NAME), 70)}")
                    say(f"        Value: {flat(prop(doc, P_VALUE), 100)}")
                    say(f"        Framework={prop(doc, P_FRAMEWORK)!r}  Rect={prop(doc, P_RECT)!r}")

            if links:
                say('')
                say(f'  超链接样例（前 {min(len(links), args.links)} 条）：')
                for k, l in enumerate(links[:args.links], 1):
                    say(f"    [{k}] {flat(prop(l, P_NAME), 60)}")
                    say(f"        rect={prop(l, P_RECT)!r}  click={prop(l, P_CLICKABLE)!r}"
                        f"  aid={flat(prop(l, P_AID), 30)!r}")

            if args.tree > 0:
                say('')
                say(f'  控件树（深度 ≤{args.tree}，节点上限 {args.tree_cap}）：')
                tlines, tn = dump_tree(iuia, root, args.tree, args.tree_cap,
                                       deadline=time.time() + 25)
                for ln in tlines:
                    say('    ' + ln)
                say(f'  （共 dump {tn} 个节点'
                    + ('，已达上限' if tn >= args.tree_cap else '') + '）')

    say('')
    say('=== 3. 结论 ===')
    say('')

    doc_urls = []
    for r in rows:
        for doc in r.get('docs') or []:
            v = str(prop(doc, P_VALUE) or '')
            if v.startswith('http'):
                doc_urls.append(v)

    n_links = sum(len(r.get('links') or []) for r in rows)
    n_docs = sum(len(r.get('docs') or []) for r in rows)

    if doc_urls:
        say('结论 A【最优】：Document 元素暴露了页面 URL —— **不用点击、不用复制**，')
        say('             直接读窗口就能拿到链接。样例：')
        for u in doc_urls[:3]:
            say(f'               {u[:110]}')
        say('             下一步：让 agent 写"枚举窗口 → 读 Document URL → 关窗口"的循环。')
    elif n_links > 0:
        say('结论 B：能读到超链接元素（标题 + 坐标 + InvokePattern），但拿不到 href。')
        say('        可精确点击（比坐标盲点稳），链接得靠 Document URL 或剪贴板收。')
    elif n_docs > 0:
        say('结论 C：能看到 Document 节点但里面是空的 —— 无障碍树没被唤醒或宿主没开启。')
        say('        微信的内置浏览器若是 CEF，需要**宿主程序**主动开无障碍，')
        say('        这种情况下 WM_GETOBJECT 也叫不醒，只能退回截图 + 坐标点击。')
    elif not cands:
        say('结论：未捕获到候选窗口，无法判定。先按上面的提示打开微信窗口。')
    else:
        say('结论 D：UIA 读不到窗口内部（自绘，或完全没实现无障碍）。')
        say('        只剩「截图 + 坐标点击」的图像法，最脆弱但可用。')

    say('')
    say('本探针只读：只发了 WM_GETOBJECT（无害的无障碍查询消息），没有点击、没有输入。')

    if args.output:
        with open(args.output, 'w', encoding='utf-8') as fh:
            fh.write('\n'.join(buf) + '\n')
        print(f'\n已保存到: {args.output}')


if __name__ == '__main__':
    main()
