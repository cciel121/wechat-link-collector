#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""collect_via_browser.py — 让微信把文章开进「系统默认浏览器」，再从浏览器窗口读出永久短链。

链路（每一步都在本机实测过，2026-09，微信 3.9.12.55 / Windows）
----------------------------------------------------------------
1. 在**公众号主页窗口**（类名 `H5SubscriptionProfileWnd`）读出文章清单：
   `日期 / 标题 / 阅读数` 三元一组，标题元素支持 `InvokePattern`。
2. 用 `InvokePattern.Invoke()` **程序化打开**这篇文章（不移动鼠标）。
3. 微信设置开启「使用系统默认浏览器打开网页」后，文章会落到系统默认浏览器
   —— 实测是**在已有浏览器窗口里新开一个标签页**（不是新窗口！），并且
   **不会抢前台**。
4. 把那个浏览器窗口顶到前台 + 发 `WM_GETOBJECT`，它的 `Document.Value`
   就是文章 URL，且是 `https://mp.weixin.qq.com/s/xxxx` 这种**永久短链**。
5. 关掉标签页（`Ctrl+W`），回到第 2 步。

关键坑（都是实测撞出来的，别绕开）
----------------------------------
- **浏览器被遮挡时 `Document=0`**。Chromium 只为「可见/在前台」的窗口构建无障碍树，
  单纯发 `WM_GETOBJECT` 叫不醒。**必须先把窗口顶到前台**，`Document` 才会出现。
- **不是新开窗口，是在已有窗口里新标签页**。所以「等新窗口出现」这个判据是错的
  （第一版就栽在这里：`Invoke` 成功了却报「未捕获到 URL」）。要按**窗口标题**
  定位（浏览器窗口标题 = 当前标签页标题），或按「标题发生变化的浏览器窗口」。
- **窗口里 68 个 Text 全都报 `InvokePattern` 可用**（Chromium 把整条文章行的
  可点性下发给了每个子节点，`DefaultAction` 是「点击祖先实体」）。所以**不能**
  靠「哪个元素可点」来挑文章，必须靠「日期→标题→阅读数」的**结构配对**。
- **`BoundingRectangle` 第 3/4 个分量是宽高**，不是 right/bottom。
  （这个坑本项目踩过两次。）
- **`GetCurrentPattern` 返回裸指针，必须 `QueryInterface`**，否则会得出
  「每个元素都支持全部 19 种模式」的荒谬结论。

代价（必读）
------------
- 自动化期间**会抢前台**，请勿同时使用鼠标键盘。
- 需要用户先在微信里打开目标公众号的主页窗口，并把微信设为「使用系统默认浏览器打开网页」。
- **能采到多少条取决于列表加载了多少，不是本脚本的上限。** 实测同一脚本读到过 20 篇，
  也读到过 **103 篇**（该号 2020→2026 的全部文章）。程序化滚动没能打通懒加载
  （Chromium 的 WebArea 不支持 `ScrollPattern`，必抛 `COMError 0x80131509`），
  **但用户手动把列表滚到底之后，整个列表在 UIA 里都是可读的**。
  → 跑之前先问一句「列表滚到底了吗」。没滚过时拿到的条数**不是该号的全部文章**，
    **不要据此说「这个号只有 20 篇」**（这里犯过错、还写错过文档）。
  → 分多次跑会自动按 URL 去重、合并进已有文件，可以累加。
- 扫码登录必须用户本人完成，本脚本不碰登录。

用法
----
    python collect_via_browser.py --doctor             # 先做环境预检（5 秒，不打开文章）
    python collect_via_browser.py --list              # 只看清单，不打开
    python collect_via_browser.py --max 5             # 最多采 5 篇（先小量试）
    python collect_via_browser.py -o links.jsonl      # 全量；自动与已有文件合并去重
    python collect_via_browser.py --no-close          # 不关标签页（调试用）
    python collect_via_browser.py --skip 10           # 跳过前 10 篇

产出
----
    links.jsonl   每行 {url,key,title,page_title,date,reads,likes,account,gh_id,
                        source,collected_at}   （共 11 个键，详见写盘处）
    links.txt     纯 URL，一行一个，可直接喂 wechat-mp-archive 的 fetch.py

依赖：pip install -r requirements.txt   （等价于 pip install pywinauto）
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
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    pass

try:
    from pywinauto import Desktop
    from pywinauto.uia_defines import IUIA
except ImportError:
    sys.exit('缺少依赖，请先安装（技能根目录）：pip install -r requirements.txt\n'
             '         或直接：pip install pywinauto')

try:
    from comtypes.gen import UIAutomationClient as UIAc
except ImportError:
    UIAc = None

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from probe_wechat_uia import (CT_DOCUMENT, CT_TEXT, find_all, flat,        # noqa: E402
                              prop, proc_name, wake_accessibility,
                              P_NAME, P_VALUE)
from profile_parse import (extract_pairs, has_loading_more,               # noqa: E402
                           judge_environment, verdict,
                           pick_article_doc, title_matches, INAPP_WINDOW_CLASS)
from collect_wechat_uia import normalize_key                              # noqa: E402

PROFILE_CLASS = 'H5SubscriptionProfileWnd'
CHROMIUM_CLASS_HINT = 'Chrome_WidgetWin_1'

UIA_InvokePatternId = 10000
MP_RE = re.compile(r'https?://mp\.weixin\.qq\.com/(?:s|mp)/[^\s"\']*')
GH_RE = re.compile(r'userName=(gh_[0-9a-zA-Z]+)')
NAME_RE = re.compile(r'showName=([^&]+)')

WM_GETOBJECT = 0x003D
OBJID_CLIENT = 0xFFFFFFFC
HWND_TOPMOST, HWND_NOTOPMOST = -1, -2
SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE, SWP_SHOWWINDOW = 0x1, 0x2, 0x10, 0x40
VK_CONTROL, VK_W = 0x11, 0x57
KEYEVENTF_KEYUP = 0x0002

user32 = ctypes.windll.user32

_log = []


def say(s=''):
    _log.append(str(s))
    print(s)


# --------------------------------------------------------------------------
# 窗口
# --------------------------------------------------------------------------

def all_top_windows():
    """全部顶层窗口（含不可见），返回 [{hwnd, cls, title, pid, visible}]。"""
    PROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    rows = []

    def cb(h, _):
        n = user32.GetWindowTextLengthW(h)
        b = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(h, b, n + 1)
        c = ctypes.create_unicode_buffer(300)
        user32.GetClassNameW(h, c, 300)
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(h, ctypes.byref(pid))
        rows.append({'hwnd': h, 'cls': c.value, 'title': b.value,
                     'pid': pid.value, 'visible': bool(user32.IsWindowVisible(h))})
        return True

    user32.EnumWindows(PROC(cb), 0)
    return rows


def win_title(hwnd):
    n = user32.GetWindowTextLengthW(wintypes.HWND(hwnd))
    b = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(wintypes.HWND(hwnd), b, n + 1)
    return b.value


def find_profile_windows():
    """全部公众号主页窗口（可能同时开了好几个号）。"""
    out = []
    for w in Desktop(backend='uia').windows():
        try:
            if w.element_info.class_name == PROFILE_CLASS:
                out.append(w)
        except Exception:
            continue
    return out


def find_profile_window():
    ws = find_profile_windows()
    return ws[0] if ws else None


def wechat_processes():
    """[(pid, exe_name)]，去重。"""
    seen, out = set(), []
    for r in all_top_windows():
        if r['pid'] in seen:
            continue
        if is_wechat_pid(r['pid']):
            seen.add(r['pid'])
            out.append((r['pid'], proc_name(r['pid'])))
    return out


def inapp_browser_windows():
    """微信内置浏览器（WeChatAppEx.exe）的可见窗口。

    它有窗口就说明用户点开过文章、而且**落进了内置浏览器**——大概率是
    「使用系统默认浏览器打开网页」没开。路径 A+++ 读不到它的任何内容。
    """
    out = []
    for r in all_top_windows():
        if r['cls'] == INAPP_WINDOW_CLASS and r['visible']:
            out.append(r)
    return out


def parse_account(docs):
    """从公众号窗口的 Document.Value 里解析 (账号名, 原始 gh_id)。"""
    from urllib.parse import unquote
    account, gh_id = '', ''
    for _nm, v in (docs or []):
        if 'SubscriptionProfile' not in v and 'mp.weixin' not in v:
            continue
        m = GH_RE.search(v)
        if m:
            gh_id = m.group(1)
        m2 = NAME_RE.search(v)
        if m2:
            account = unquote(m2.group(1))
        break
    return account, gh_id


def is_wechat_pid(pid):
    return 'wechat' in (proc_name(pid) or '').lower()


def browser_windows():
    """候选浏览器窗口：带浏览器窗口类名、且不是微信自己的窗口。"""
    out = []
    for r in all_top_windows():
        if CHROMIUM_CLASS_HINT not in r['cls'] or not r['visible']:
            continue
        if is_wechat_pid(r['pid']):
            continue
        out.append(r)
    return out


def _is_fg(hwnd):
    return int(user32.GetForegroundWindow()) == int(hwnd)


def _fg_alt_trick(hwnd):
    """模拟一次 ALT 键解除前台锁，再 SetForegroundWindow。

    实测：在本机（Windows + 微信 3.9.12）只有这一招能稳定拿到前台，
    `SetForegroundWindow` 单独调用会被前台锁拒掉。ALT 只是空按、无副作用。
    """
    try:
        VK_MENU, KEYUP = 0x12, KEYEVENTF_KEYUP
        user32.keybd_event(VK_MENU, 0, 0, 0)
        user32.keybd_event(VK_MENU, 0, KEYUP, 0)
        time.sleep(0.1)
        user32.SetForegroundWindow(wintypes.HWND(hwnd))
        time.sleep(0.3)
        return _is_fg(hwnd)
    except Exception:
        return False


def foreground(hwnd):
    """把窗口顶到前台。Chromium 只在窗口「可见且未被遮挡」时才建无障碍树。

    这一点是实测出来的，不是推测：
      被遮挡时 `Document=0`，单纯发 WM_GETOBJECT 叫不醒；
      顶到前台后 `Document=3`，其中就有文章 URL。

    返回 (是否拿到前台, 用哪一招)。拿到前台是 Ctrl+W 关标签的**前提**。
    """
    if _is_fg(hwnd):
        return True, 'already'
    user32.ShowWindow(wintypes.HWND(hwnd), 9)          # SW_RESTORE
    user32.SetWindowPos(wintypes.HWND(hwnd), wintypes.HWND(HWND_TOPMOST), 0, 0, 0, 0,
                        SWP_NOSIZE | SWP_NOMOVE | SWP_SHOWWINDOW)
    time.sleep(0.4)
    if _is_fg(hwnd):
        return True, 'restore+topmost'
    user32.SetForegroundWindow(wintypes.HWND(hwnd))
    time.sleep(0.3)
    if _is_fg(hwnd):
        return True, 'setforeground'
    if _fg_alt_trick(hwnd):
        return True, 'alt-unlock'
    # 最小化再还原：还原动作通常会激活窗口
    try:
        user32.ShowWindow(wintypes.HWND(hwnd), 6)      # SW_MINIMIZE
        time.sleep(0.25)
        user32.ShowWindow(wintypes.HWND(hwnd), 9)
        time.sleep(0.5)
        if _is_fg(hwnd):
            return True, 'minimize+restore'
    except Exception:
        pass
    try:
        user32.SwitchToThisWindow(wintypes.HWND(hwnd), True)
        time.sleep(0.4)
        if _is_fg(hwnd):
            return True, 'switchto'
    except Exception:
        pass
    return False, 'none'


def unfix_topmost(hwnd):
    try:
        user32.SetWindowPos(wintypes.HWND(hwnd), wintypes.HWND(HWND_NOTOPMOST),
                            0, 0, 0, 0, SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE)
    except Exception:
        pass


def send_ctrl_w():
    user32.keybd_event(VK_CONTROL, 0, 0, 0)
    user32.keybd_event(VK_W, 0, 0, 0)
    time.sleep(0.05)
    user32.keybd_event(VK_W, 0, KEYEVENTF_KEYUP, 0)
    user32.keybd_event(VK_CONTROL, 0, KEYEVENTF_KEYUP, 0)


# --------------------------------------------------------------------------
# 读文章清单
# --------------------------------------------------------------------------

def read_doc_urls(hwnd, iuia, tag=''):
    """返回 [(doc_name, doc_url)]。读不到就返回空 —— 调用方必须报原因。"""
    try:
        root = iuia.ElementFromHandle(hwnd)
    except Exception as e:
        say(f'    ⚠️ {tag}ElementFromHandle 失败: {type(e).__name__}: {str(e)[:60]}')
        return None, None
    wake_accessibility(hwnd, times=2, gap=0.25)
    time.sleep(0.3)
    try:
        docs, err = find_all(iuia, root, CT_DOCUMENT, timeout=10)
    except Exception as e:
        say(f'    ⚠️ {tag}find_all(Document) 异常: {type(e).__name__}')
        return None, root
    out = []
    for d in docs:
        v = str(prop(d, P_VALUE) or '')
        if v:
            out.append((str(prop(d, P_NAME) or ''), v))
    return out, root


def enumerate_articles(iuia, root):
    """读公众号窗口的文章清单，并把每条映射到**可 Invoke 的元素**。

    返回 (items, names)；items 是 [{'date','title','reads','likes','el'}]。
    元素按「日期→标题」的顺序位置配对，避免同名标题串位。
    """
    texts, err = find_all(iuia, root, CT_TEXT, timeout=15)
    if not texts:
        return [], [], err
    names = [str(prop(t, P_NAME) or '') for t in texts]
    rows = extract_pairs(names)
    items, cursor = [], 0
    for r in rows:
        idx = None
        for i in range(cursor, len(names)):
            if names[i] == r['title']:
                idx = i
                break
        if idx is None:
            continue
        cursor = idx + 1
        r2 = dict(r)
        r2['el'] = texts[idx]
        items.append(r2)
    return items, names, err


def invoke(el):
    """调用 InvokePattern。返回 (ok, 描述)。不静默降级。"""
    if UIAc is None:
        return False, 'UIAutomationClient 不可用'
    try:
        ptr = el.GetCurrentPattern(UIA_InvokePatternId)
        if ptr is None:
            return False, 'GetCurrentPattern 返回空指针'
        ptr.QueryInterface(UIAc.IUIAutomationInvokePattern).Invoke()
        return True, 'invoke'
    except Exception as e:
        return False, f'{type(e).__name__}: {str(e)[:70]}'


# --------------------------------------------------------------------------
# 定位文章所在的浏览器窗口
# --------------------------------------------------------------------------

def find_article_window(art_title, base_titles, timeout=12.0, say_fn=say):
    """等文章开进浏览器。返回 (hwnd, how) 或 (None, 原因)。

    判据分两级，避免**误判到早就存在的同名标签页**：
      1. 标题匹配，且这个窗口是新的、或它的标题相对 invoke 前变了 → 确认是新开的
      2. （超时后）标题匹配但标题没变 → 可能是早就存在的同名标签页，
         仍然返回，但把 how 标成 `title-match(existing)`，让调用方和用户都看得见。
    """
    t0 = time.time()
    changed = []
    while time.time() - t0 < timeout:
        for r in browser_windows():
            if not title_matches(r['title'], art_title):
                continue
            if r['hwnd'] not in base_titles or base_titles[r['hwnd']] != r['title']:
                return r['hwnd'], 'title-match(new)'
        now = {r['hwnd']: r['title'] for r in browser_windows()}
        for h, t in now.items():
            if base_titles.get(h) != t and t:
                changed.append((h, t))
        time.sleep(0.5)
    for r in browser_windows():
        if title_matches(r['title'], art_title):
            return r['hwnd'], 'title-match(existing)'
    if changed:
        h, t = changed[-1]
        say_fn(f'    · 没等到标题匹配的窗口，但检测到标题变化的浏览器窗口：'
               f'0x{h:X} {flat(t, 50)!r}')
        return h, 'title-changed'
    return None, f'{timeout:.0f}s 内没有出现标题匹配、也没有标题变化的浏览器窗口'


def read_article_url(hwnd, iuia, art_title, known_keys=None):
    """读文章 URL。

    ⚠️ 关键坑（实测踩过）：**同一个浏览器窗口里可能同时暴露多个标签页的 Document**。
    收了 2 篇之后，那个窗口里 `Document` 就有 2 个（第 1 篇和第 2 篇）。
    早先版本直接取第一个 mp.weixin 的 Document，于是**第 2 篇读回了第 1 篇的 URL**，
    而且报告看起来完全正常。

    所以必须按**文档标题**（`Document.Name` = 页面标题）匹配，匹配不上就如实报歧义，
    **不猜**。返回 (url, doc_name, 说明)。
    """
    fg_ok, how_fg = foreground(hwnd)
    if not fg_ok:
        say(f'    ⚠️ 目标窗口 0x{hwnd:X} 没能抢到前台（已试 restore/topmost/alt/minimize/'
            f'switchto）—— 可能读不到 Document，也无法安全关标签')
    docs, _ = read_doc_urls(hwnd, iuia, tag='')
    if docs is None:
        return None, '', 'ElementFromHandle / find_all 失败'
    if not docs:
        return None, '', ('叫醒后仍读不到任何 Document（窗口可能被完全遮挡，'
                          '或该浏览器未构建无障碍树）')
    cands = [(nm, v) for nm, v in docs if 'mp.weixin.qq.com' in v]
    if not cands:
        return None, '', ('窗口里的 Document 都不是 mp.weixin 页面，实际读到：'
                          + ' | '.join(v[:60] for _, v in docs[:3]))

    url, nm, how = pick_article_doc(
        [(nm, (MP_RE.search(v).group(0) if MP_RE.search(v) else v)) for nm, v in cands],
        art_title, known_keys=known_keys, key_fn=normalize_key)
    if not url:
        return None, '', how
    return url, nm, f'ok({how}/{how_fg})'


# --------------------------------------------------------------------------
# 输出
# --------------------------------------------------------------------------

def load_existing(path):
    rows, urls = {}, set()
    if not os.path.exists(path):
        return rows, urls
    with open(path, encoding='utf-8') as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except Exception:
                continue
            u = r.get('url') or ''
            if u:
                rows[normalize_key(u)] = r
                urls.add(u)
    return rows, urls


def flush(out_path, store, order):
    txt_path = os.path.splitext(out_path)[0] + '.txt'
    with open(out_path, 'w', encoding='utf-8') as fh:
        for k in order:
            fh.write(json.dumps(store[k], ensure_ascii=False) + '\n')
    with open(txt_path, 'w', encoding='utf-8') as fh:
        for k in order:
            fh.write(store[k]['url'] + '\n')
    return txt_path


# --------------------------------------------------------------------------
# 环境预检
# --------------------------------------------------------------------------

LEVEL_ICON = {'ok': '✅ ', 'warn': '⚠️  ', 'fail': '❌ '}


def cmd_doctor():
    """花 5 秒确认环境，再决定要不要跑 11 分钟的全量采集。

    为什么值得单独做一个模式：全量采集要 ~11 分钟**并且会抢前台**。
    「微信没开」「打开方式没设成系统默认浏览器」「列表没滚到底」这些问题
    放到跑之前查出来，比跑完再猜便宜得多。判定规则在
    `profile_parse.judge_environment`（纯函数，被自检覆盖）。
    """
    facts = {'pywinauto_ok': True, 'python': sys.executable}

    say('=== 环境预检 ===')
    say('')
    try:
        iuia = IUIA().iuia
    except Exception as e:
        facts['pywinauto_ok'] = False
        iuia = None
        say(f'  ⚠️  初始化 UIA 失败：{type(e).__name__}: {str(e)[:80]}')

    facts['wechat_pids'] = wechat_processes()
    facts['appex_windows'] = len(inapp_browser_windows())
    facts['browser_windows'] = browser_windows()

    profs = find_profile_windows()
    facts['profile_windows'] = len(profs)
    if profs and iuia is not None:
        hwnd = profs[0].handle
        say(f'  · 用第 1 个公众号窗口 hwnd=0x{hwnd:X}'
            + (f'（共有 {len(profs)} 个）' if len(profs) > 1 else ''))
        wake_accessibility(hwnd)
        time.sleep(1.0)
        root = None
        try:
            root = iuia.ElementFromHandle(hwnd)
        except Exception as e:
            say(f'  ⚠️  ElementFromHandle 失败：{type(e).__name__}: {str(e)[:60]}')
        docs, _ = read_doc_urls(hwnd, iuia, tag='[公众号] ')
        facts['profile_doc_count'] = 0 if not docs else len(docs)
        account, gh_id = parse_account(docs)
        facts['account'] = account
        say(f'  · 账号: {account or "(未解析出名称)"}   原始ID: {gh_id or "?"}')
        if root is not None:
            items_now, names, _err = enumerate_articles(iuia, root)
            facts['article_count'] = len(items_now)
            facts['loading_more'] = has_loading_more(names)
            say(f'  · Text {len(names)} 个，解析出文章 {len(items_now)} 篇'
                + ('（列表末尾挂着「正在加载...」）' if facts['loading_more'] else ''))
    say('')

    items = judge_environment(facts)
    for it in items:
        say(f'  {LEVEL_ICON.get(it["level"], "· ")}{it["text"]}')

    v = verdict(items)
    say('')
    if v == 'blocked':
        say('❌ 有致命项没过 —— 先把上面标 ❌ 的处理掉，再回来跑采集。')
        return 2
    if v == 'attention':
        say('⚠️  可以跑，但请先看上面标 ⚠️ 的几条，尤其是「没加载完」和「内置浏览器」这两条。')
    elif v == 'unknown':
        say('❓ 一项都没能确认 —— 这不是「通过」。请把上面的原始输出发给 agent 一起看。')
        return 2
    else:
        say('✅ 环境就绪，可以开跑。')

    say('')
    me = os.path.basename(os.path.abspath(__file__))
    say('建议的下一步（依次验证，别一上来就全量）：')
    say(f'  "{sys.executable}" {me} --list                    # 看清单条数对不对')
    say(f'  "{sys.executable}" {me} --max 3                   # 小量试跑 3 篇')
    say(f'  "{sys.executable}" {me} -o links.jsonl            # 全量（重复的会自动合并去重）')
    return 0


# --------------------------------------------------------------------------
# 主流程
# --------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(
        description='让微信把文章开进系统默认浏览器，再从浏览器窗口读永久短链')
    ap.add_argument('--doctor', action='store_true',
                    help='只做环境预检（微信/公众号窗口/浏览器/内置浏览器/列表加载），不打开文章')
    ap.add_argument('--list', action='store_true', help='只列出文章清单，不打开')
    ap.add_argument('--max', type=int, default=0, help='最多采集几篇（0=不限）')
    ap.add_argument('--skip', type=int, default=0, help='跳过前 N 篇（续跑用）')
    ap.add_argument('--interval', type=float, default=1.2,
                    help='每篇之间的间隔秒数（默认 1.2）')
    ap.add_argument('--timeout', type=float, default=12.0,
                    help='等文章开进浏览器的秒数（默认 12）')
    ap.add_argument('--no-close', action='store_true',
                    help='不自动关标签页（调试用；默认会关）')
    ap.add_argument('--max-fail', type=int, default=3,
                    help='连续失败几篇就停（默认 3）')
    ap.add_argument('-o', '--out', default='links.jsonl',
                    help='输出 jsonl（同目录另写一份 links.txt）')
    args = ap.parse_args()

    if args.doctor:
        return cmd_doctor()

    t_start = time.time()
    iuia = IUIA().iuia
    orig_fg = int(user32.GetForegroundWindow())

    prof = find_profile_window()
    if not prof:
        say(f'❌ 未找到公众号主页窗口（类名 {PROFILE_CLASS}）。')
        say('   请先在微信里搜索并打开目标公众号——会出现一个标题为「公众号」的独立窗口。')
        say('   注意：老的「历史消息」窗口已下线，本脚本用的是这个新窗口。')
        return 2
    hwnd = prof.handle
    say(f'公众号窗口 hwnd=0x{hwnd:X} pid={prof.element_info.process_id}')
    wake_accessibility(hwnd)
    time.sleep(1.0)
    root = iuia.ElementFromHandle(hwnd)

    docs, _ = read_doc_urls(hwnd, iuia, tag='[公众号] ')
    account, gh_id = parse_account(docs)
    if docs:
        say(f'账号: {account or "(未解析出名称)"}   原始ID: {gh_id or "?"}')
    else:
        say('⚠️ 读不到公众号窗口的 Document（拿不到账号信息，但文章清单可能仍可读）')

    items, names, err = enumerate_articles(iuia, root)
    loading = has_loading_more(names)
    say(f'Text 数量: {len(names)}'
        + (f'（find_all 报错: {err}）' if err else ''))
    say(f'解析出文章 {len(items)} 篇'
        + ('   列表末尾有「正在加载...」，说明还有更多（滚动未打通，见文档）'
           if loading else ''))

    if not items:
        say('')
        say(f'⚠️ 文章数为 0，但这**不代表该公众号没有文章**。Text={len(names)}。')
        say('   若 Text>0 而解析为 0，说明页面结构变了，把上面的 Text 清单发回来修规则。')
        return 3

    say('')
    say(f'{"#":<4}{"日期":<14}{"标题":<50}阅读')
    for i, it in enumerate(items, 1):
        say(f'{i:<4}{it["date"]:<14}{flat(it["title"], 48):<50}{it["reads"]}')

    targets = items[args.skip:]
    if args.max:
        targets = targets[:args.max]

    if args.list:
        say('')
        say('（--list：只列清单，未打开任何文章）')
        return 0

    store, known_urls = load_existing(args.out)
    order = list(store.keys())
    n_before = len(order)

    say('')
    say(f'=== 开始采集 {len(targets)} 篇（跳过 {args.skip} 篇）===')
    say(f'    已有清单 {n_before} 条，将合并去重写入 {os.path.abspath(args.out)}')
    say('    ⚠️ 期间会抢前台，请不要动鼠标键盘。')
    say('')

    stats = {'ok': 0, 'dup': 0, 'fail': 0, 'closed': 0, 'close_fail': 0}
    fails = []
    consec = 0

    for n, tgt in enumerate(targets, 1):
        label = f'[{n}/{len(targets)}] {flat(tgt["title"], 40)}'
        say(f'{label}')

        # 每篇都重新枚举，避免持有上一轮的元素引用（页面重渲染会导致指针失效）
        items2, _, err2 = enumerate_articles(iuia, root)
        if not items2:
            say(f'  ⚠️ 重新枚举文章清单失败（Text 读到 0，err={err2}），停止。')
            break
        cur = None
        for it in items2:
            if it['title'] == tgt['title'] and it['date'] == tgt['date']:
                cur = it
                break
        if cur is None:
            say(f'  ⚠️ 在当前清单里找不到这一条（列表可能变了），跳过。')
            stats['fail'] += 1
            fails.append((tgt['title'], '列表变化，找不到该条'))
            consec += 1
            if consec >= args.max_fail:
                say(f'  连续 {consec} 篇失败，停止。')
                break
            continue

        base_titles = {r['hwnd']: r['title'] for r in browser_windows()}
        ok, how = invoke(cur['el'])
        if not ok:
            # Invoke 失败常见原因是窗口重渲染后元素失效，重取一次再试
            items3, _, _ = enumerate_articles(iuia, root)
            again = next((x for x in items3
                          if x['title'] == tgt['title'] and x['date'] == tgt['date']), None)
            if again is not None:
                ok, how = invoke(again['el'])
        if not ok:
            say(f'  ⚠️ 打开失败: {how}')
            stats['fail'] += 1
            fails.append((tgt['title'], f'invoke 失败: {how}'))
            consec += 1
            if consec >= args.max_fail:
                say(f'  连续 {consec} 篇失败，停止。')
                break
            continue
        say(f'  已打开（{how}），等浏览器窗口…')

        hwnd2, why = find_article_window(tgt['title'], base_titles,
                                         timeout=args.timeout, say_fn=say)
        if not hwnd2:
            say(f'  ⚠️ 没找到文章落地的浏览器窗口：{why}')
            n_appex = len(inapp_browser_windows())
            if n_appex:
                say(f'  ❗ 同时检测到 {n_appex} 个微信内置浏览器窗口（{INAPP_WINDOW_CLASS}）：'
                    f'文章落进了内置浏览器，')
                say('     说明微信「设置 → 通用设置 → 使用系统默认浏览器打开网页」没生效。'
                    '内置浏览器是 D3D 合成，')
                say('     **没有任何无障碍对象，读不到 URL**。改完设置后要重新打开一篇文章才生效。')
            else:
                say('     常见原因：① 微信「设置 → 通用设置 → 使用系统默认浏览器打开网页」没开；')
                say('     ② 系统默认浏览器不是 Chromium 系（Firefox 等本路径读不到 Document）；')
                say('     ③ 浏览器窗口被最小化或被隐藏了。')
            stats['fail'] += 1
            fails.append((tgt['title'], f'找不到浏览器窗口: {why}'))
            consec += 1
            if consec >= args.max_fail:
                say(f'  连续 {consec} 篇失败，停止。')
                break
            continue
        say(f'  浏览器窗口 0x{hwnd2:X}（{why}），顶到前台读 Document…')

        url, doc_name, note = read_article_url(hwnd2, iuia, tgt['title'],
                                               known_keys=set(store.keys()))
        if not url:
            say(f'  ⚠️ 读不到文章 URL：{note}')
            stats['fail'] += 1
            fails.append((tgt['title'], note))
            unfix_topmost(hwnd2)
            consec += 1
            if consec >= args.max_fail:
                say(f'  连续 {consec} 篇失败，停止。')
                break
            continue

        key = normalize_key(url)
        if key in store:
            stats['dup'] += 1
            say(f'  ↺ 重复（已在清单里）：{url}   [{note}]')
            if store[key].get('url') != url:
                store[key]['url'] = url          # 优先保留永久短链
        else:
            store[key] = {
                'url': url,
                'key': key,
                'title': tgt['title'],
                'page_title': doc_name,
                'date': tgt['date'],
                'reads': tgt['reads'],
                'likes': tgt['likes'],
                'account': account,
                'gh_id': gh_id,
                'source': 'uia-browser',
                'collected_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            }
            order.append(key)
            known_urls.add(url)
            stats['ok'] += 1
            say(f'  ✅ {url}   [{note}]')
        consec = 0
        flush(args.out, store, order)

        # 关标签页：必须同时满足「浏览器是前台」「标题仍是这篇文章」，才发 Ctrl+W。
        # 否则按键会打到别的窗口上（可能关错用户的标签页），宁可不关。
        # 注：Chrome 不暴露 TabItem（实测 TabItem=0），所以没有「点关闭按钮」这条
        # 不用键盘的路，只能靠 Ctrl+W。
        if not args.no_close:
            cur_title = win_title(hwnd2)
            still_fg = _is_fg(hwnd2)
            if still_fg and title_matches(cur_title, tgt['title']):
                send_ctrl_w()
                time.sleep(0.6)
                gone = not title_matches(win_title(hwnd2), tgt['title'])
                for _ in range(6):
                    if gone:
                        break
                    time.sleep(0.4)
                    gone = not title_matches(win_title(hwnd2), tgt['title'])
                if gone:
                    stats['closed'] += 1
                else:
                    stats['close_fail'] += 1
                    say('    ⚠️ Ctrl+W 之后窗口标题仍是这篇文章，标签页可能没关掉')
            else:
                stats['close_fail'] += 1
                say(f'    ⚠️ 没关标签页（前台={still_fg}，标题匹配='
                    f'{title_matches(cur_title, tgt["title"])}）—— 不敢盲发 Ctrl+W，'
                    f'留给用户手动关。')
        unfix_topmost(hwnd2)
        time.sleep(args.interval)

    flush(args.out, store, order)
    txt_path = os.path.splitext(args.out)[0] + '.txt'

    say('')
    say('=== 汇总 ===')
    say(f'  新采集      : {stats["ok"]} 条')
    say(f'  清单里已有  : {stats["dup"]} 条（重复）')
    say(f'  失败        : {stats["fail"]} 条')
    say(f'  已关标签页  : {stats["closed"]} 个'
        + (f'（{stats["close_fail"]} 个没关掉）' if stats['close_fail'] else ''))
    if fails:
        say('  失败明细：')
        for t, why in fails[:10]:
            say(f'    - {flat(t, 40)} :: {why}')
    say(f'  清单总数    : {len(order)} 条（本程序新增 {len(order) - n_before}）')
    say(f'  jsonl: {os.path.abspath(args.out)}')
    say(f'  txt  : {os.path.abspath(txt_path)}')
    say(f'  耗时: {time.time() - t_start:.1f}s')
    if len(order) == 0:
        say('')
        say('⚠️ 一条链接都没拿到。**不要据此判断该公众号没有文章** ——')
        say('   先看上面的失败明细：完全没开出去 / 找到了窗口但读不到 Document / 读到了但不是')
        say('   mp.weixin 页面，三种原因的处理方式完全不同。')

    try:
        if orig_fg:
            user32.SetForegroundWindow(wintypes.HWND(orig_fg))
    except Exception:
        pass
    return 0


if __name__ == '__main__':
    sys.exit(main())
