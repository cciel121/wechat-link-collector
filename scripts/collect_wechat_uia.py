#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""collect_wechat_uia.py — 用 UI Automation 驱动 Chromium 窗口逐条打开文章并读出 URL。

为什么需要它
------------
实测（见 probe_wechat_uia.py）已确认：Chromium 窗口里的 `Document` 元素，
其 **Value 属性就是页面 URL**，且是永久短链形态。

但要把整批文章收下来，仍然需要一个「打开」动作：从列表页点进每篇文章。
本脚本把这个动作交给程序 —— 枚举列表里的超链接 → 用 InvokePattern 打开
（失败则降级到坐标点击）→ 读新出现的 Document 的 URL → 关标签/返回 → 下一条。

所以准确说法是：**用户不用点，程序仍要逐条打开。** 这是「零人工操作」，
不是「零操作」。

代价（必读）
------------
- **会抢前台**：自动化期间请不要用鼠标键盘。
- 依赖 Chromium 无障碍树，微信改版可能失效（失效时报告会打出「0 条」并说明原因）。
- 扫码登录必须用户本人完成，本脚本不碰登录。
- 只读采集：不修改任何数据、不发送任何网络请求、不写注册表。

用法
----
    # 1) 在微信里打开目标公众号的「历史消息」窗口，停在屏幕上，别最小化
    # 2) 先干跑，确认能枚举到文章条目
    python collect_wechat_uia.py --dry-run

    # 3) 确认无误后正式采集
    python collect_wechat_uia.py --out links.jsonl

    python collect_wechat_uia.py --pid 12345 --max 50
    python collect_wechat_uia.py --title 历史消息 --dry-run

依赖：pip install pywinauto
"""

import argparse
import ctypes
import json
import os
import re
import sys
import time
from ctypes import wintypes
from datetime import datetime

try:
    from pywinauto import Desktop
    from pywinauto.uia_defines import IUIA
except ImportError:
    sys.exit('缺少依赖，请先安装：pip install pywinauto')

try:
    from comtypes.gen import UIAutomationClient as UIAc
except ImportError:
    UIAc = None

# ---- UIA 标准属性 ID（MSDN 整数，不依赖 pywinauto 的常量名）----
P_RECT      = 30001
P_CTYPE     = 30003
P_NAME      = 30005
P_AID       = 30011
P_CLICKABLE = 30014
P_VALUE     = 30045
P_OFFSCREEN = 30022

CT_HYPERLINK = 50005
CT_DOCUMENT  = 50030

SCOPE_DESCENDANTS = 4

UIA_InvokePatternId = 10000

WM_GETOBJECT = 0x003D
OBJID_CLIENT = 0xFFFFFFFC

WECHAT_HINTS = ('wechat',)
CHROMIUM_HINTS = ('Chrome_WidgetWin', 'Chrome_RenderWidgetHostHWND')

# 列表页里的导航项，不该被当成文章条目
NAV_WORDS = ('下一页', '上一页', '返回', '更多', '历史消息', '公众号', '搜索',
             '确定', '取消', '投诉', '举报', '使用完整服务', '预览')

VK_CONTROL, VK_W, VK_MENU, VK_LEFT = 0x11, 0x57, 0x12, 0x25
KEYEVENTF_KEYUP = 0x0002
MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP = 0x0002, 0x0004

user32 = ctypes.windll.user32

_PROC_CACHE = {}
_log_lines = []


def say(s=''):
    """统一输出：既打印也给报告留底。"""
    _log_lines.append(str(s))
    print(s)


def flat(s, n=60):
    t = ' '.join(str(s or '').split())
    return t if len(t) <= n else t[:n] + '…'


def proc_name(pid):
    if pid in _PROC_CACHE:
        return _PROC_CACHE[pid]
    name = ''
    try:
        import win32api
        import win32con
        import win32process
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


def prop(el, pid):
    try:
        v = el.GetCurrentPropertyValue(pid)
        return '' if v is None else v
    except Exception:
        return ''


def rect_of(el):
    """返回 (l, t, r, b)；取不到返回 None。"""
    r = prop(el, P_RECT)
    if not r:
        return None
    try:
        return (int(r[0]), int(r[1]), int(r[2]), int(r[3]))
    except Exception:
        return None


def wake_accessibility(hwnd, times=3, gap=0.4):
    """发 WM_GETOBJECT 叫醒 Chromium 的惰性无障碍树（已在真实 Chrome 上验证）。"""
    try:
        for _ in range(times):
            user32.SendMessageW(wintypes.HWND(hwnd), WM_GETOBJECT, 0,
                                ctypes.c_long(OBJID_CLIENT))
            time.sleep(gap)
        return True
    except Exception:
        return False


def find_all(iuia, root, ctype, timeout=15):
    """FindAll 可能在大树上卡死。这里不做线程超时（会与 COM 抢线程），
    但把异常如实返回，绝不静默成空列表。"""
    try:
        cond = iuia.CreatePropertyCondition(P_CTYPE, ctype)
        found = root.FindAll(SCOPE_DESCENDANTS, cond)
    except Exception as e:
        return [], f'{type(e).__name__}: {e}'
    out = []
    try:
        for i in range(found.Length):
            out.append(found.GetElement(i))
    except Exception as e:
        return out, f'{type(e).__name__}: {e}'
    return out, None


def doc_map(iuia, root):
    """当前窗口内所有「可见且带 URL」的 Document，键为 (name, url)。

    过滤掉 Rect 全 0 的节点 —— 那些是 about:srcdoc / 隐藏 frame，
    它们会让「新增 Document」的 diff 产生假阳性。
    """
    docs, err = find_all(iuia, root, CT_DOCUMENT)
    m = {}
    for d in docs:
        url = str(prop(d, P_VALUE) or '')
        r = rect_of(d)
        if not url or not r or r[2] <= 0 or r[3] <= 0:   # width/height 必须为正
            continue
        m[(str(prop(d, P_NAME) or ''), url)] = d
    return m, err


def all_doc_urls(iuia, root):
    """所有非空 Document URL，**不按可见性过滤**（诊断用）。

    留作诊断工具：工作区的 diag_mode.py 用它观察「列表页 Document 在不在树里」。

    ⚠️ 但它**不能**用来区分打开模式。实测（diag_mode.py）：
    无论新标签页还是同窗口导航，invoke 之后两种模式的 all_doc_urls 都只剩
    1 个（文章页）—— Chromium 不给后台标签建无障碍树，列表页的 Document
    在两种模式下都不在树里。所以「列表页还在不在」和「Document 数量有没有
    增加」这两个判据都是错的，会把新标签模式误判成同窗口。
    真正的做法见主循环里的「先后退、不行再关标签」自愈序列。
    """
    docs, err = find_all(iuia, root, CT_DOCUMENT)
    out = set()
    for d in docs:
        u = str(prop(d, P_VALUE) or '')
        if u.startswith('http') or u.startswith('file:'):
            out.add(u)
    return out, err


def enum_links(iuia, root, limit=400):
    """枚举超链接，按 y 坐标（阅读顺序）排序，并过滤导航项与空链接。

    返回 (list_of_dict, err, raw_count)。raw_count 是**未过滤**的总数，
    用来区分「本来就没有链接」和「全被过滤掉了」。
    """
    links, err = find_all(iuia, root, CT_HYPERLINK)
    raw = len(links)
    out = []
    for l in links:
        name = str(prop(l, P_NAME) or '').strip()
        if not name:
            continue
        if name in NAV_WORDS or any(w == name for w in NAV_WORDS):
            continue
        r = rect_of(l)
        if not r:
            continue
        out.append({
            'el': l, 'name': name, 'rect': r,
            'click': prop(l, P_CLICKABLE),
            'aid': str(prop(l, P_AID) or ''),
            'y': r[1], 'x': r[0],
        })
    # 去重：同一篇文章常被图片和标题两个元素指到，包围盒几乎重合
    dedup, seen = [], []
    for it in sorted(out, key=lambda i: (i['y'], i['x'])):
        dup = any(abs(it['y'] - s['y']) < 6 and abs(it['x'] - s['x']) < 40 for s in seen)
        if dup:
            continue
        seen.append(it)
        dedup.append(it)
    return dedup[:limit], err, raw


def invoke_element(el):
    """优先用 InvokePattern（不移动鼠标、不受遮挡影响），失败才降级坐标点击。

    返回描述实际所用路径的字符串；两条路都失败返回 None。**不静默降级**。
    """
    # 1) InvokePattern
    if UIAc is not None:
        try:
            ptr = el.GetCurrentPattern(UIA_InvokePatternId)
            pat = ptr.QueryInterface(UIAc.IUIAutomationInvokePattern)
            pat.Invoke()
            return 'invoke'
        except Exception as e:
            err1 = f'{type(e).__name__}: {e}'
    else:
        err1 = 'UIAutomationClient 接口不可用'

    # 2) 坐标点击兜底
    cp = prop(el, P_CLICKABLE)
    if cp:
        try:
            x, y = int(cp[0]), int(cp[1])
            user32.SetCursorPos(x, y)
            time.sleep(0.08)
            user32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
            time.sleep(0.05)
            user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
            return f'click({x},{y})'
        except Exception as e:
            return None
    say(f'      ⚠️ 无法打开该条目（InvokePattern: {err1}；也无 ClickablePoint）')
    return None


def focus_window(hwnd):
    try:
        user32.SetForegroundWindow(wintypes.HWND(hwnd))
        time.sleep(0.25)
        return True
    except Exception:
        return False


def send_keys(*vk_seq):
    for vk in vk_seq:
        user32.keybd_event(vk, 0, 0, 0)
    for vk in reversed(vk_seq):
        user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)


def wait_for_new_doc(iuia, root, before_keys, rx, timeout=15.0, gap=0.5):
    """等待出现一个「新的、且 URL 匹配文章特征」的 Document。

    统一覆盖两种模式：
      - 新标签页/新窗口：Document 集合里多出一个
      - 同窗口导航：列表页那个 Document 的 Value 变了（键也变了）
    """
    t0 = time.time()
    last = {}
    while time.time() - t0 < timeout:
        m, _ = doc_map(iuia, root)
        last = m
        news = [k for k in m if k not in before_keys]
        hits = [k for k in news if rx.search(k[1])]
        if hits:
            return hits[0], m, 'new-doc'
        time.sleep(gap)
    return None, last, 'timeout'


def wait_left_article(iuia, root, rx, timeout=4.0, wake=False, hwnd=None):
    """等「不再有匹配文章特征的 Document」。

    bfcache 恢复 + UIA 树重建是异步的（实测需要 1~3 秒）。早先版本只 sleep 0.6s
    就重枚举，拿到「可见 0 条」，被误判成「列表页没有更多条目」而结束循环 ——
    实测那次只收到 1/3 的文章，且报告看起来完全正常。
    """
    t0 = time.time()
    cur = ''
    while time.time() - t0 < timeout:
        m, _ = doc_map(iuia, root)
        cur = next(iter(m))[1] if m else ''
        if m and not any(rx.search(k[1]) for k in m):
            return True, cur
        if wake and hwnd:
            wake_accessibility(hwnd, times=1, gap=0.2)
        time.sleep(0.5)
    return False, cur


def normalize_key(url):
    """与 clipboard_watch.py 同口径：尽量折叠成可跨形态去重的键。

    永久短链 /s/xxx 直接用 token；签名链取 __biz+mid+idx。
    """
    m = re.search(r'/s/([A-Za-z0-9_\-]+)', url or '')
    if m:
        return 's:' + m.group(1)
    biz = re.search(r'[?&]__biz=([^&]+)', url or '')
    mid = re.search(r'[?&]mid=([^&]+)', url or '')
    idx = re.search(r'[?&]idx=([^&]+)', url or '')
    if biz or mid:
        return 'm:%s:%s:%s' % (biz.group(1) if biz else '',
                               mid.group(1) if mid else '',
                               idx.group(1) if idx else '')
    return 'u:' + (url or '')


def pick_target(args):
    """定位目标列表窗口。找不到时把候选清单打出来，不静默返回空。"""
    desk = Desktop(backend='uia')
    rows = []
    for w in desk.windows():
        try:
            ei = w.element_info
            pid = ei.process_id
            rows.append({'w': w, 'hwnd': w.handle, 'name': ei.name or '',
                         'cls': ei.class_name or '', 'pid': pid,
                         'pname': proc_name(pid)})
        except Exception:
            continue
    if args.pid:
        return [r for r in rows if r['pid'] in set(args.pid)], rows
    if args.hwnd:
        return [r for r in rows if r['hwnd'] == args.hwnd], rows
    if args.title:
        return [r for r in rows if args.title.lower() in (r['name'] or '').lower()], rows
    cands = [r for r in rows
             if any(h in (r['pname'] or '').lower() for h in WECHAT_HINTS)
             or any(h.lower() in (r['cls'] or '') for h in CHROMIUM_HINTS)]
    return cands, rows


def main():
    ap = argparse.ArgumentParser(
        description='用 UIA 驱动 Chromium 窗口逐条打开文章并读出 URL（会抢前台）')
    ap.add_argument('--pid', type=int, action='append', default=None, help='目标进程（可重复）')
    ap.add_argument('--hwnd', type=lambda s: int(s, 0), default=None, help='目标窗口句柄')
    ap.add_argument('--title', default='', help='按标题子串定位窗口')
    ap.add_argument('--url-regex', default=r'mp\.weixin\.qq\.com/s',
                    help='判定"这是文章页 URL"的正则（默认 mp.weixin.qq.com/s）')
    ap.add_argument('--out', default='', help='输出 links.jsonl 路径（默认 ./links.jsonl）')
    ap.add_argument('--max', type=int, default=200, help='最多打开多少条（默认 200）')
    ap.add_argument('--delay', type=float, default=1.2, help='每次打开后的额外等待秒数')
    ap.add_argument('--dry-run', action='store_true', help='只枚举条目，不点击')
    ap.add_argument('--no-wake', dest='wake', action='store_false', help='不发 WM_GETOBJECT')
    ap.set_defaults(wake=True)
    args = ap.parse_args()

    rx = re.compile(args.url_regex)
    out_jsonl = args.out or 'links.jsonl'
    out_txt = os.path.splitext(out_jsonl)[0] + '.txt'

    say('=== 目标窗口定位 ===')
    targets, rows = pick_target(args)
    if not targets:
        say(f'枚举到顶层窗口 {len(rows)} 个，但没有匹配的目标窗口。')
        say('')
        say(f'{"进程":<18}{"窗口类名":<24}{"标题":<34}PID')
        for r in rows:
            say(f"{flat(r['pname'] or '?', 16):<18}{flat(r['cls'], 22):<24}"
                f"{flat(r['name'], 32):<34}{r['pid']}")
        say('')
        say('请先在微信里打开目标公众号的「历史消息」窗口并让它停在屏幕上，再重跑。')
        say('若微信正开着却匹配不到，用 --pid 或 --title 显式指定上面清单里的窗口。')
        return 2

    if len(targets) > 1:
        say(f'匹配到 {len(targets)} 个窗口，将逐个处理：')
        for r in targets:
            say(f"  - {r['pname']} | {flat(r['cls'], 20)} | {flat(r['name'], 30)} | pid={r['pid']}")

    collected = {}
    order = []
    stats = {'opened': 0, 'skipped': 0, 'failed': 0, 'dupes': 0}

    for r in targets:
        hwnd, root = r['hwnd'], None
        say('')
        say(f"=== 处理 {r['pname']} | {flat(r['cls'], 20)} | {flat(r['name'], 30)} | pid={r['pid']} ===")

        uia = IUIA()
        root = uia.iuia.ElementFromHandle(wintypes.HWND(hwnd))

        if args.wake:
            wake_accessibility(hwnd)
            time.sleep(1.0)

        docs, err = doc_map(uia.iuia, root)
        if err:
            say(f'  ⚠️ 读取 Document 出错：{err}')
        say(f'  可读 Document：{len(docs)} 个')
        for k in list(docs)[:3]:
            say(f'      {flat(k[0], 40)} → {flat(k[1], 80)}')

        links, lerr, raw = enum_links(uia.iuia, root)
        if lerr:
            say(f'  ⚠️ 枚举超链接出错：{lerr}')
        say(f'  超链接：原始 {raw} 个 → 过滤去重后 {len(links)} 个条目')
        if raw and not links:
            say('  ⚠️ 有超链接但全被过滤 —— 可能是列表页结构不同，请把上面的原始清单反馈。')
        for i, it in enumerate(links[:8], 1):
            say(f'    [{i}] {flat(it["name"], 50)}  rect={it["rect"]}  aid={flat(it["aid"], 20)}')
        if len(links) > 8:
            say(f'    … 其余 {len(links) - 8} 条')

        if args.dry_run:
            say('  （--dry-run：不点击）')
            continue

        if not links:
            say('  → 没有可打开的条目，跳过该窗口。')
            continue

        focus_window(hwnd)

        # 每轮**重新枚举**，绝不跨轮复用元素指针。
        # 实测教训：同窗口导航后列表页 DOM 被重建，上一轮缓存的 Hyperlink
        # COM 指针全部失效，第二次调用直接抛 "NULL COM pointer access"，
        # 表现为「第 1 条成功、后面全失败」——很容易被误读成「页面只有一条」。
        handled = set()
        rounds = 0
        while True:
            rounds += 1
            if stats['opened'] >= args.max:
                say(f'  已达到 --max {args.max}，停止。')
                break
            if rounds > args.max * 3 + 10:
                say('  ⚠️ 循环轮数异常（页面结构可能不稳定），停止。')
                break

            cur, cerr, _ = enum_links(uia.iuia, root)
            if cerr:
                say(f'  ⚠️ 重新枚举出错：{cerr}')
            todo = [it for it in cur if it['name'] not in handled]
            if not todo:
                say(f'  第 {rounds} 轮：没有未处理的新条目'
                    f'（可见 {len(cur)} 条，已处理 {len(handled)} 条），结束。')
                break

            it = todo[0]
            handled.add(it['name'])
            i = len(handled)
            tag = f'[{i}] {flat(it["name"], 30)}'

            before, _ = doc_map(uia.iuia, root)

            how = invoke_element(it['el'])
            if not how:
                stats['failed'] += 1
                say(f'  {tag} 打开失败，跳过')
                continue
            stats['opened'] += 1

            time.sleep(args.delay)
            hit, now, status = wait_for_new_doc(uia.iuia, root, set(before), rx)

            if hit:
                name, url = hit
                key = normalize_key(url)
                if key in collected:
                    stats['dupes'] += 1
                    say(f'  {tag} 重复：{flat(name, 40)}')
                else:
                    rec = {
                        'url': url,
                        'key': key,
                        'title': name,
                        'source': 'uia',
                        'list_window': r['name'],
                        'opened_via': how,
                        'collected_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                    }
                    collected[key] = rec
                    order.append(rec)
                    say(f'  {tag} {how} → {flat(url, 88)}')
                    say(f'        标题：{flat(name, 60)}')
            else:
                stats['skipped'] += 1
                say(f'  {tag} {how} → 未捕获到匹配 {args.url_regex} 的 URL '
                    f'（{status}，等待期间可见 {len(now)} 个 Document）')

            # 回到列表页。
            #
            # 这里**不能靠 Document 树区分两种打开模式**。实测（diag_mode.py）：
            # 无论 target=_blank 的新标签页还是同窗口导航，invoke 之后 UIA 都
            # 只暴露活动标签的 Document —— 列表页的 Document 在两种模式下都不在
            # 树里（Chromium 不给后台标签建无障碍树）。所以「列表页还在不在」和
            # 「Document 数量有没有增加」这两个判据都是错的，会把新标签模式
            # 误判成同窗口，卡在文章页上。
            #
            # 改用「先后退，不行再关标签」的自愈序列，**顺序不能反**：
            #   同窗口导航 → 后退生效，直接离开文章页
            #   新标签页   → 新标签没有上一页，后退无效（留在文章页）→ 关标签
            # 反过来先 Ctrl+W 的话，同窗口模式下会把列表页标签本身关掉。
            focus_window(hwnd)
            send_keys(VK_MENU, VK_LEFT)
            ok_back, cur_url = wait_left_article(uia.iuia, root, rx, 4.0, args.wake, hwnd)
            back_mode = '后退'
            if not ok_back:
                send_keys(VK_CONTROL, VK_W)
                back_mode = '后退无效→关标签'
                ok_back, cur_url = wait_left_article(uia.iuia, root, rx, 4.0, args.wake, hwnd)
            if ok_back:
                say(f'        （回列表页：{back_mode}）')
            else:
                say(f'  ⚠️ 后退和关标签都没能离开文章页（当前 {flat(cur_url, 50)}），停止。'
                    f'已采集的 {len(order)} 条链接已保留。')
                break

    # ---- 产出 ----
    os.makedirs(os.path.dirname(os.path.abspath(out_jsonl)), exist_ok=True)
    with open(out_jsonl, 'w', encoding='utf-8') as fh:
        for rec in order:
            fh.write(json.dumps(rec, ensure_ascii=False) + '\n')
    with open(out_txt, 'w', encoding='utf-8') as fh:
        for rec in order:
            fh.write(rec['url'] + '\n')

    say('')
    say('=== 汇总 ===')
    say(f'  成功打开次数：{stats["opened"]}')
    say(f'  采集到去重链接：**{len(order)}** 条')
    say(f'  重复（已在清单中）：{stats["dupes"]}')
    say(f'  打开了但没读到 URL：{stats["skipped"]}')
    say(f'  打开失败：{stats["failed"]}')
    say('')
    say(f'  输出：{os.path.abspath(out_jsonl)}')
    say(f'        {os.path.abspath(out_txt)}')

    if not order:
        say('')
        say('⚠️ 一条都没收到。按可能性排序的排查方向：')
        say('   1) 列表窗口没停在屏幕上 / 被最小化 —— 浏览器此时不建无障碍树。')
        say('   2) 点开后文章是在**新的独立窗口**里打开，而不是当前窗口的新标签页；')
        say('      这种情况下 --pid 只圈了列表窗口，读不到文章窗口的 Document。')
        say('      解决：把文章窗口也纳入目标（用 --pid 指定微信主进程）。')
        say('   3) 微信改版，超链接元素不再暴露 —— 用 --dry-run 看还能不能枚举到条目。')
        return 1

    # 报告留底，便于回传
    rep = os.path.splitext(out_jsonl)[0] + '.log.txt'
    with open(rep, 'w', encoding='utf-8') as fh:
        fh.write('\n'.join(_log_lines) + '\n')
    say(f'  过程日志：{os.path.abspath(rep)}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
