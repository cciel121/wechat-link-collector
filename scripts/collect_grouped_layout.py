#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""collect_grouped_layout.py — 「折叠分组」布局的公众号主页采集。

为什么需要单独一个脚本（这是它存在的唯一理由）
================================================
公众号主页有**两种**布局，`collect_via_browser.py` 只覆盖第一种：

  ① 平铺布局（实测那个号是这个）
     树里「日期 / 标题 / 阅读数」都是独立 `Text`，**标题可 `Invoke`**。
     → 用 `collect_via_browser.py`（路径 A+++）。

  ② 折叠分组布局（本脚本）
     页面按「一次推送」分组，每组只展开 3 篇，其余折成「余下 N 篇」。
     **文章标题根本不在无障碍树里** —— 它只存在于 `Document.Value` 的纯文本里。
     树里只有：日期组头 / `阅读 N 赞 M` / `余下 N 篇` / `正在加载...`。
     → ① 的「日期→标题」配对规则在这里**永远只能配出 1 篇**；
       `collect_via_browser.py --doctor` 报「解析出 1 篇」就是撞在这个布局上，
       **不是环境问题、也不是「这个号文章少」**。

实测链路（2026-09-22 在本机逐条验证过，微信 3.9.12.55 / Windows / Chrome）
==========================================================================
1. 点「余下 N 篇」→ 折叠组**原地展开**（实测 33 → 37 篇，与「余下 4 篇」吻合）。
2. 每篇文章的「阅读数」元素**可 `Invoke`**；**屏幕外（`rect=0`）的照样能点**。
3. `Invoke` 阅读数 → 文章开进系统默认浏览器 → 读**地址栏**得到
   `mp.weixin.qq.com/s/xxxx` 永久短链。实测单篇约 **3 秒**（含开关窗口约 4.5 秒）。
4. 标题从 `Document.Value` 解析；**用「第 i 个阅读数元素的名字 == 第 i 条解析出的
   阅读数」来证明配对没错位** —— 这是硬校验，不是假设。

⚠️ 本布局下「标题 ↔ 链接」**没有第二重证据**：打开文章的瞬间浏览器窗口标题还停在
「微信公众平台」，来不及变成文章标题。所以位置校验必须过，否则宁可不产出。
（`--verify-first` 会盯住第一篇、等窗口标题落定后比对，作为最后一道防线。）

为什么不能从无障碍属性里直接拿 URL（省掉开浏览器）
--------------------------------------------------
实测把阅读数元素的底层 UIA 属性全问了一遍
（`HelpText` / `FullDescription` / `ItemStatus` / `AriaProperties` / `LegacyIAccessible`
/ `AutomationId` / `ClassName`）：**没有一处带 href，全是空**。
所以「逐篇打开」不是偷懒，是当前唯一可行路径。

用法
====
    python collect_grouped_layout.py --doctor            # 只读预检：判断是不是这种布局
    python collect_grouped_layout.py --list              # 只解析清单，不打开任何文章
    python collect_grouped_layout.py --max 30 -o links.jsonl
    python collect_grouped_layout.py --skip 30 --max 30  # 接着上次往后跑
    python collect_grouped_layout.py --expand            # 只展开「余下 N 篇」，展开完退出
    python collect_grouped_layout.py --no-expand         # 不展开（**会漏采**，仅排查用）
    python collect_grouped_layout.py --no-verify-first   # 跳过首篇标题校验（不推荐）

⚠️ **采集时默认会先自动展开所有「余下 N 篇」**。
   折叠组的文章标题**不在**页面正文里，不展开就采 = 静默漏采，
   而报告看上去完全正常。所以展开是默认值，不是可选项。
   跑完会数一遍还剩多少折叠标记并在有剩余时**大声告警**
   （`folded_markers()`）；`--max` 若把某一天从中间截断也会告警
   （`truncation_info()`）——因为那会让报告的「时间跨度」变成断的。

产出
====
    links.jsonl   每行 {url,key,title,page_title,date,date_raw,reads,likes,
                        account,gh_id,source,collected_at}   （13 个键）
    links.txt     纯 URL，一行一个
    articles.jsonl（`--index-out`，默认与 -o 同目录）已采到的索引，
                  可直接喂 `make_link_report.py --index ... --links ...`

依赖：pip install -r requirements.txt（等价于 pip install pywinauto）
"""

import argparse
import ctypes
import json
import os
import re
import sys
import time
from datetime import datetime

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    pass

try:
    from pywinauto.uia_defines import IUIA
except ImportError:
    sys.exit('缺少依赖，请先安装（技能根目录）：pip install -r requirements.txt\n'
             '         或直接：pip install pywinauto')

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from probe_wechat_uia import (CT_TEXT, find_all, flat, prop,              # noqa: E402
                              wake_accessibility, P_NAME, P_VALUE)
from profile_parse import (GROUPED_DATE_RES, GROUPED_YUXIA_RE,            # noqa: E402
                           absolutize_date, folded_markers, index_rows_from_store,
                           is_read_name, normalize_read, parse_grouped,
                           read_key_of_item, truncation_info)
from collect_via_browser import (browser_windows, find_profile_windows,   # noqa: E402
                                 flush, foreground, invoke, load_existing,
                                 normalize_key, parse_account, read_doc_urls,
                                 say, send_ctrl_w, win_title)

CT_EDIT = 50004

# --------------------------------------------------------------------------
# 解析：从 Document.Value 里把「日期 / 标题 / 阅读」抠出来
# --------------------------------------------------------------------------
#
# 页面文本的形状（实测样本，\u2004/\u2005 是微信用的窄空格）：
#
#   星期四 上海志愿服务动态 阅读 7915 赞 22   让急救知识走出医院…阅读 301
#   温暖申城 榜样力量 | 扎根静安15年…阅读 115 赞 1    余下 5 篇
#   9月10日 中社部首次亮相国新办发布会，介绍了这些情况 阅读 1116 赞 8 ...
#
# 解析规则本体（`parse_grouped` 等）**放在 `profile_parse.py`**：那是纯字符串处理，
# 必须能在**没有 pywinauto** 的环境里被自检直接调用。放在本文件会让
# `import collect_grouped_layout` 连带拉起 pywinauto，自检就跑不了这一组了
# （这个坑实测踩过：自检跑到第 11 组直接被 `sys.exit` 顶掉整个进程）。
# 账号头部里的这几句会被误当标题，仅在 --doctor 的展示里用来过滤噪音
HEADER_NOISE = re.compile(r'(原创内容|个朋友关注|^已关注$|^私信$|^公众号$|^服务号$)')


# --------------------------------------------------------------------------
# 读页面
# --------------------------------------------------------------------------

def read_page_text(hwnd):
    """取出承载文章清单的**正文文本**。

    ⚠️ 取的是 `element_info.rich_text`，**不是 `Value`（UIA 30045）**。
    本窗口 Document 的 `Value` 是页面来源串 `weixin://resourceid/SubscriptionProfile/`，
    正文（实测 24747 字符）在 `rich_text` 里。**取错属性会得出「页面是空的」这种假结论** ——
    这个坑实测撞过：`find_all(Document)` + `P_VALUE` 读回来只有那个 resourceid 串。

    ⚠️ 同时注意：这个窗口里 Document 可能不止一个（页面来源串 + 正文），
    所以**挑含「阅读」且最长的那个**，不能取第一个。
    """
    from pywinauto import Desktop
    wake_accessibility(hwnd, times=2, gap=0.25)
    win = Desktop(backend='uia').window(handle=hwnd)
    try:
        docs = win.descendants(control_type='Document')
    except Exception as e:
        return None, f'枚举 Document 失败：{type(e).__name__}: {str(e)[:80]}'
    if not docs:
        return None, '窗口里一个 Document 都没有（窗口可能被遮挡，或被完全最小化）'
    best, seen = '', []
    for d in docs:
        t = getattr(d.element_info, 'rich_text', '') or ''
        seen.append(len(t))
        if '阅读' in t and len(t) > len(best):
            best = t
    if not best:
        return None, (f'{len(docs)} 个 Document 里没有含「阅读」的正文'
                      f'（各文档长度：{seen}）。先确认页面加载完、窗口没被遮挡。')
    return best, ''


def read_texts(iuia, root):
    """返回 (全部 Text 元素, 名字列表)。"""
    texts, err = find_all(iuia, root, CT_TEXT, timeout=20)
    if not texts:
        return [], [], f'读不到任何 Text（err={err}）'
    return texts, [str(prop(t, P_NAME) or '') for t in texts], ''


def enumerate_grouped(iuia, root, hwnd):
    """返回 (items, texts, notes)。

    items 的每一项会挂上 'el'（对应的、可 Invoke 的阅读数元素）与 'idx'。
    **配对靠位置，并用元素名逐条校验**：任何一条对不上就整条丢弃并出声。
    """
    notes = []
    value, err = read_page_text(hwnd)
    if value is None:
        return [], [], [err]
    items = parse_grouped(value)
    if not items:
        notes.append('Document.Value 里一条「阅读」都没解析出来 —— '
                     '先怀疑解析或页面未加载，别当成「这个号没有文章」')
        return [], [], notes

    for it in items:            # 相对日期（星期四/今天/昨天）换成绝对日期，原标签留在 date_raw
        it['date'], it['date_raw'] = absolutize_date(it['date'])

    texts, names, err2 = read_texts(iuia, root)
    if err2:
        notes.append(err2)
        return items, [], notes
    read_els = [(i, n) for i, n in enumerate(names) if is_read_name(n)]
    if not read_els:
        notes.append('树里没有「阅读」元素 —— 这个布局下它们就是标题的替身把手，'
                     '没有它们就没法 Invoke，采集做不下去')
        return items, [], notes

    if len(read_els) != len(items):
        notes.append(f'⚠️ 数量不一致：树里阅读数元素 {len(read_els)} 个 vs '
                     f'Document.Value 解析出 {len(items)} 条。'
                     f'位置配对不可信，**本次不产出**（宁可空手也别给错的对照表）')
        return items, [], notes

    bad = []
    for k, (el_idx, nm) in enumerate(read_els):
        if normalize_read(nm) != read_key_of_item(items[k]):
            bad.append((k, nm, read_key_of_item(items[k])))
    if bad:
        for k, got, want in bad[:5]:
            notes.append(f'  · 第 {k+1} 条错位：元素={got!r} 解析={want!r}')
        notes.append(f'⚠️ {len(bad)} 条错位 —— 位置配对失效，**本次不产出**')
        return items, [], notes

    for k, (el_idx, _nm) in enumerate(read_els):
        items[k]['el'] = texts[el_idx]
        items[k]['idx'] = k
    notes.append(f'位置校验通过：{len(items)} 条「阅读数」与解析结果逐条对齐')
    return items, texts, notes


# --------------------------------------------------------------------------
# 展开折叠组
# --------------------------------------------------------------------------

def expand_all(iuia, hwnd, max_rounds=500, say_fn=say):
    """反复点「余下 N 篇」直到没有。

    每点一次页面会重渲染，元素会失效，所以**每轮都重新枚举**，
    并且**永远点当前第一个**「余下 N 篇」——它对应最靠前那个还没展开的组。
    """
    done, t0 = 0, time.time()
    for _ in range(max_rounds):
        root = iuia.ElementFromHandle(hwnd)
        texts, names, err = read_texts(iuia, root)
        if not names:
            say_fn(f'  ⚠️ 展开中断：读不到 Text（{err}）')
            break
        target = None
        for t, nm in zip(texts, names):
            if GROUPED_YUXIA_RE.fullmatch((nm or '').strip()) or re.match(r'^余下\s*\d+\s*篇$', (nm or '').strip()):
                target = t
                break
        if target is None:
            break
        ok, why = invoke(target)
        if not ok:
            say_fn(f'  ⚠️ 展开「{prop(target, P_NAME)}」失败：{why}')
            break
        done += 1
        time.sleep(1.0)
        if done % 10 == 0:
            say_fn(f'  · 已展开 {done} 组（{time.time()-t0:.0f}s）')
    return done


def report_folded(iuia, root, hwnd):
    """读一次页面，数还剩多少「余下 N 篇」。返回 (标记个数, 累计折叠篇数)。

    **这是防空采的那道闸**：折叠的文章标题不在页面正文里，
    不展开就采 = 静默漏采，而报告看上去完全正常。
    """
    value, _err = read_page_text(hwnd)
    if value is None:
        return 0, 0
    _texts, names, _e = read_texts(iuia, root)
    return folded_markers(value, names)


# --------------------------------------------------------------------------
# 从浏览器窗口读 URL
# --------------------------------------------------------------------------

def read_url_from_window(iuia, hwnd, timeout=10.0):
    """读地址栏（最直接）；失败再退回 Document。返回 (url, 说明)。"""
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            root = iuia.ElementFromHandle(hwnd)
        except Exception as e:
            return None, f'ElementFromHandle 失败：{type(e).__name__}'
        wake_accessibility(hwnd, times=1, gap=0.2)
        edits, _ = find_all(iuia, root, CT_EDIT, timeout=6)
        for e in edits:
            nm = str(prop(e, P_NAME) or '')
            if '地址' in nm or 'Address' in nm:
                v = str(prop(e, P_VALUE) or '')
                m = re.search(r'mp\.weixin\.qq\.com/s/[\w-]+', v)
                if m:
                    return 'https://' + m.group(0), 'omnibox'
        time.sleep(0.4)
    # 退回 Document
    docs, _ = read_doc_urls(hwnd, iuia, tag='[文章] ')
    if docs:
        for _nm, v in docs:
            m = re.search(r'https?://mp\.weixin\.qq\.com/(?:s|mp)/[\w-]+', v)
            if m:
                return m.group(0), 'document'
    return None, f'{timeout:.0f}s 内地址栏和 Document 都没读到 mp.weixin 链接'


def wait_new_browser(iuia, base, timeout=15.0):
    """等一个新出现的浏览器窗口（文章落地窗口）。

    实测：每篇会**新开一个浏览器窗口**（上一篇用 Ctrl+W 关掉后，这个窗口就没了），
    所以「新 hwnd」是可靠判据；但我们同时也接受「标题发生变化的窗口」作为兜底。
    """
    t0 = time.time()
    while time.time() - t0 < timeout:
        for r in browser_windows():
            if r['hwnd'] not in base:
                return r['hwnd'], 'new-window'
        time.sleep(0.3)
    for r in browser_windows():
        if base.get(r['hwnd']) != r['title'] and r['title']:
            return r['hwnd'], 'title-changed'
    return None, f'{timeout:.0f}s 内没有出现新的浏览器窗口'


# --------------------------------------------------------------------------
# 预检
# --------------------------------------------------------------------------

def cmd_doctor():
    ws = find_profile_windows()
    say('=== 折叠分组布局预检 ===')
    say('')
    if not ws:
        say('  ❌ 没有「公众号主页窗口」—— 请在微信里搜索并点开目标公众号')
        say('')
        say('结论：blocked')
        return 2
    say(f'  · 找到 {len(ws)} 个公众号窗口，用第 1 个'
        + ('。⚠️ 同时开多个号可能读错号，建议只留目标号' if len(ws) > 1 else ''))
    iuia = IUIA().iuia
    w = ws[0]
    fg_ok, how = foreground(w.handle)
    say(f'  · 抢前台：{"成功(" + how + ")" if fg_ok else "失败 —— 读不到页面就加 --foreground"}')
    root = iuia.ElementFromHandle(w.handle)
    value, err = read_page_text(w.handle)
    texts, names, err2 = read_texts(iuia, root)

    if value is None:
        say(f'  ❌ {err}')
        say('')
        say('结论：blocked')
        return 2
    items = parse_grouped(value)
    read_els = [n for n in names if is_read_name(n)]
    yuxia = [n for n in names if '余下' in (n or '')]
    dates = [n for n in names if any(p.fullmatch((n or '').strip()) for p in GROUPED_DATE_RES)]
    title_like = [n for n in names if n and not is_read_name(n) and '余下' not in n
                  and not any(p.fullmatch(n.strip()) for p in GROUPED_DATE_RES)
                  and n not in ('上海志愿者', '已关注', '私信', '公众号', '服务号')
                  and not HEADER_NOISE.search(n)]
    hid = sum(int(x) for x in re.findall(r'余下\s*(\d+)\s*篇', value))

    say('')
    say(f'  · 页面文本 {len(value)} 字符，账号头部首行：{value.strip().split()[0][:20]!r}')
    say(f'  · 日期组头 {len(dates)} 个（最新 {dates[0] if dates else "?"!r}）')
    say(f'  · 「阅读数」元素 {len(read_els)} 个 ← 这是本布局下每篇文章的可点把手')
    say(f'  · 「余下 N 篇」标记 {len(yuxia)} 个，累计折叠 {hid} 篇'
        + ('（折叠的**不在** DOM 里，必须 --expand 才拿得到）' if yuxia else ''))
    say(f'  · 能当标题用的独立 Text：{len(title_like)} 个'
        + ('  ← 0 个正是「折叠分组布局」的特征' if not title_like else ''))
    say(f'  · Document.Value 解析出 {len(items)} 条')
    say(f'  · {"页面仍在「正在加载...」→ 列表可能没到底，先滚到底再跑" if "正在加载" in value else "列表已无「正在加载...」"}')
    say('')

    if read_els:
        ratio = len(title_like) / len(read_els)
        say(f'  · 标题/阅读数 比例 = {len(title_like)}/{len(read_els)} = {ratio:.2f}'
            '   ← 两种布局的判别依据')
        say('')
        if ratio >= 0.8:
            say(f'结论：这是**平铺布局**（每篇都有独立标题 Text）'
                '—— 用 `collect_via_browser.py`，不要用本脚本')
            return 0
        if ratio <= 0.3:
            say(f'结论：**折叠分组布局** —— 用本脚本采集。当前可采 {len(read_els)} 篇'
                + (f'，另有 {hid} 篇被折叠（先 --expand 才拿得到）' if hid else ''))
            say('       （`collect_via_browser.py` 在这个布局下只能配出 1 篇，别用它）')
            return 0
        say(f'结论：unknown —— 比例 {ratio:.2f} 落在两可区间，两种布局都不像。'
            '把上面的原始输出留着一起看，别硬跑')
        return 2
    say('结论：unknown —— 两种布局的特征都没凑齐，把上面的原始输出留着一起看')
    return 2


# --------------------------------------------------------------------------
# 主流程
# --------------------------------------------------------------------------

def cmd_main(args):
    iuia = IUIA().iuia
    ws = find_profile_windows()
    if not ws:
        say('❌ 没有「公众号主页窗口」—— 请在微信里搜索并点开目标公众号')
        return 2
    if len(ws) > 1:
        say(f'⚠️ 有 {len(ws)} 个公众号窗口，用第 1 个。同时开多个号可能采错号 —— '
            '建议只留目标号再跑。')
    w = ws[0]
    fg_ok, how = foreground(w.handle)
    if not fg_ok:
        say(f'⚠️ 没能把公众号窗口抢到前台（试过 restore/topmost/alt/minimize/switchto）。'
            '页面可能读不全，继续但请留意条数。')

    # ⚠️ 展开是**默认行为**：不展开就会静默漏掉折叠的文章。
    #    （本项目定的规矩：凡「要点一下参数才对」的地方，默认值就得是对的。）

    # 旧用法 `--expand`：只展开、不采集。行为保持不变 —— 否则老命令会突然开始采几百篇。
    if args.expand:
        say('=== 只展开「余下 N 篇」（不采集；展开完就退出）===')
        n = expand_all(iuia, w.handle)
        say(f'  共展开 {n} 组')
        time.sleep(1.0)
        root = iuia.ElementFromHandle(w.handle)
        markers, hidden = report_folded(iuia, root, w.handle)
        if markers or hidden:
            say(f'  ⚠️ 仍有 {markers} 个「余下 N 篇」标记、{hidden} 篇折叠 —— '
                '展开没做完（看上面的报错）。')
            return 1
        say('  ✅ 已全部展开，现在可以直接采集了。')
        return 0

    if not args.no_expand:
        say('=== 展开所有「余下 N 篇」 ===')
        n = expand_all(iuia, w.handle)
        say(f'  共展开 {n} 组'
            + ('（页面本来就没有折叠组）' if n == 0 else ''))
        time.sleep(1.0)
    else:
        say('⚠️ 已按 --no-expand 跳过展开 —— 折叠的文章**会被漏掉**，'
            '报告不会显示缺了谁。')

    root = iuia.ElementFromHandle(w.handle)
    items, texts, notes = enumerate_grouped(iuia, root, w.handle)
    for n in notes:
        say('  ' + n)
    if not items:
        say('❌ 没有解析到任何文章，停止（不产出空报告）')
        return 2
    with_el = [it for it in items if 'el' in it]
    if not with_el:
        say('❌ 解析到了条目，但没有一条能配上可点元素 —— 停止')
        return 2

    # ★ 漏采闸门：折叠的文章标题不在正文里，数一数还剩多少没展开。
    markers, hidden = report_folded(iuia, root, w.handle)
    if markers or hidden:
        say('')
        say(f'  ⚠️⚠️ 页面仍有 {markers} 个「余下 N 篇」标记，累计折叠 **{hidden} 篇**：')
        say('       这些文章的标题**不在页面正文里**，本次采集拿不到它们，'
            '而报告看上去完全正常、也不会标注缺了谁。')
        say('       → 先单独跑 `--expand`（只展开、不采集），'
            '等这条提示消失后再采。')
        say('')
    else:
        say('  ✅ 已无「余下 N 篇」——本次解析到的就是页面上全部可见文章。')

    # 账号名 / 原始 ID
    account, gh_id = '', ''
    docs, _ = read_doc_urls(w.handle, iuia, tag='[公众号] ')
    if docs:
        account, gh_id = parse_account(docs)
    if not account and texts:
        nm0 = str(prop(texts[0], P_NAME) or '').strip()
        if nm0 and not is_read_name(nm0) and not any(p.fullmatch(nm0) for p in GROUPED_DATE_RES):
            account = nm0
    say(f'  · 账号：{account or "(未解析出)"}   原始ID：{gh_id or "(未解析出)"}')

    if args.list:
        say('')
        say(f'=== 清单（共 {len(with_el)} 篇，按页面顺序 = 新→旧）===')
        for i, it in enumerate(with_el, 1):
            say(f'  {i:4d}. {it["date"]:>10}  {it["reads"]:>7} 阅  {flat(it["title"], 46)}')
        say('')
        say('（--list 只解析，不打开任何文章）')
        return 0

    pool = with_el[args.skip:]
    todo = pool[:args.max] if args.max else pool
    if not todo:
        say(f'❌ 没有要采的条目（skip={args.skip}，实际只有 {len(with_el)} 条）')
        return 2

    # ★ 截断落在一天中间时，「时间跨度」是断的 —— 出声，别让用户以为采到了整天。
    ti = truncation_info(pool, len(todo))
    if ti:
        d, rem = ti
        say(f'  ⚠️ 第 {len(todo)} 篇处截断，而同一天（{d}）还有 **{rem} 篇**没取 —— '
            f'报告的「时间跨度」是断的。')
        say(f'     → 要取整天用 `--max {len(todo) + rem}`，或去掉 --max 采完。')

    out_path = args.out
    out_dir = os.path.dirname(os.path.abspath(out_path))
    if out_dir and not os.path.isdir(out_dir):
        os.makedirs(out_dir, exist_ok=True)
    store, seen_urls = load_existing(out_path)
    order = list(store.keys())
    index_path = args.index_out or os.path.join(out_dir, 'articles.jsonl')

    say('')
    say(f'=== 开始采集 {len(todo)} 篇（会抢前台，期间请勿使用鼠标键盘）===')
    if args.no_verify_first:
        say('  ⚠️ 已按 --no-verify-first 关闭首篇标题校验 —— 失了最后一道防线')
    base = {r['hwnd']: r['title'] for r in browser_windows()}
    ok_n, fail_n, consec = 0, 0, 0
    records, failures = [], []
    t_start = time.time()
    for i, it in enumerate(todo):
        tag = f'[{i+1}/{len(todo)}]'
        t0 = time.time()
        ok, why = invoke(it['el'])
        if not ok:
            consec += 1
            fail_n += 1
            failures.append((it, f'invoke 失败：{why}'))
            say(f'  {tag} ❌ invoke 失败：{why}  {flat(it["title"], 36)}')
            if consec >= args.max_fail:
                say(f'  连续失败 {consec} 次，停止（用 --skip {args.skip+i-consec+1} 补采）')
                break
            continue
        hwnd, how_w = wait_new_browser(iuia, base, timeout=args.timeout)
        if not hwnd:
            consec += 1
            fail_n += 1
            failures.append((it, why_w))
            say(f'  {tag} ❌ {why_w}  {flat(it["title"], 36)}')
            if consec >= args.max_fail:
                say(f'  连续失败 {consec} 次，停止')
                break
            continue
        # ⚠️ 先把新窗口记进 base，避免下一篇又认成「新的」
        try:
            base[hwnd] = win_title(hwnd)
        except Exception:
            base[hwnd] = ''
        url, why_u = read_url_from_window(iuia, hwnd, timeout=args.timeout)
        page_title = ''
        if not url:
            consec += 1
            fail_n += 1
            failures.append((it, why_u))
            say(f'  {tag} ❌ 读到窗口但拿不到 URL：{why_u}')
            if not args.no_close:
                foreground(hwnd)
                send_ctrl_w()
                time.sleep(0.8)
            if consec >= args.max_fail:
                say(f'  连续失败 {consec} 次，停止')
                break
            continue

        # 首篇校验：等窗口标题落定，比对是不是这一篇
        if i == 0 and not args.no_verify_first:
            t_wait = time.time()
            got = ''
            while time.time() - t_wait < 8.0:
                try:
                    got = win_title(hwnd)
                except Exception:
                    got = ''
                if got and '微信公众平台' not in got and 'Google Chrome' not in got:
                    break
                time.sleep(0.5)
            from profile_parse import title_matches
            if got and '微信公众平台' not in got:
                norm = re.sub(r'\s*-\s*Google Chrome$', '', got).strip()
                if title_matches(norm, it['title']):
                    say(f'  ✅ 首篇校验通过：窗口标题 {norm!r} == 解析标题')
                else:
                    say('')
                    say(f'  ❌❌ 首篇标题对不上，**停止采集**（继续跑会产出一份错位的对照表）：')
                    say(f'       窗口标题：{norm!r}')
                    say(f'       解析标题：{it["title"]!r}')
                    say(f'       拿到的 URL：{url}')
                    say('     这通常意味着标题与「阅读数」的位置配对错了。'
                        '请把以上信息反馈，不要加 --force 硬跑。')
                    if not args.no_close:
                        foreground(hwnd)
                        send_ctrl_w()
                    return 3
            else:
                say('  ⚠️ 首篇校验跳过：窗口标题一直没落定（页面加载慢），'
                    '本轮没有第二重证据，请留意报告里的标题是否对得上。')
        consec = 0
        ok_n += 1
        it['url'] = url
        it['page_title'] = page_title
        key = normalize_key(url)
        rec = {
            'url': url, 'key': key, 'title': it['title'],
            'page_title': page_title, 'date': it['date'], 'date_raw': it.get('date_raw', ''),
            'reads': it['reads'], 'likes': it['likes'],
            'account': account, 'gh_id': gh_id,
            'source': 'uwb-grouped', 'collected_at': datetime.now().isoformat(timespec='seconds'),
        }
        store[key] = rec
        if key not in order:
            order.append(key)
        records.append(it)
        say(f'  {tag} ✅ {time.time()-t0:.1f}s {it["date"]:>9} {flat(it["title"], 34)} → {url}')
        flush(out_path, store, order)
        if not args.no_close:
            foreground(hwnd)
            send_ctrl_w()
            time.sleep(0.6)

    # 索引 = **已采到的全部条目**，按 store 的写入顺序（== 页面顺序）—— 直接喂
    # make_link_report.py。⚠️ 不能只写本次的 `records`：分批续采（先 30 再 --skip 30）
    #    会把上次的索引覆盖掉，变成「索引 N 条 / 链接 M 条」，
    #    而报告照样生成、排序也是错的，**看不出任何异常**。
    if store:
        with open(index_path, 'w', encoding='utf-8') as fh:
            for row in index_rows_from_store(store, order):
                fh.write(json.dumps(row, ensure_ascii=False) + '\n')

    txt_path = os.path.splitext(out_path)[0] + '.txt'
    dt = time.time() - t_start
    say('')
    say(f'=== 完成：成功 {ok_n} / 失败 {fail_n}，用时 {dt:.0f}s'
        + (f'（平均 {dt/max(ok_n,1):.1f}s/篇）' if ok_n else '') + ' ===')
    say(f'  · 链接：{out_path}')
    say(f'  · 纯链接：{txt_path}')
    if store:
        say(f'  · 索引：{index_path}（{len(order)} 条，与链接同条数；'
            '可喂 make_link_report.py --index）')
    if failures:
        say(f'  ⚠️ 失败 {len(failures)} 篇：')
        for it, why in failures[:10]:
            say(f'      - {it["date"]} {flat(it["title"], 34)}  ← {why}')
    return 0 if fail_n == 0 else 1


def main():
    ap = argparse.ArgumentParser(
        description='采集「折叠分组」布局的公众号主页文章链接（标题不在控件树里，'
                    '靠阅读数元素 + Document.Value 配对）')
    ap.add_argument('--doctor', action='store_true', help='只读预检：判断是不是这种布局')
    ap.add_argument('--list', action='store_true',
                    help='只解析清单，不打开文章（会先展开折叠组，好把条数数准）')
    ap.add_argument('--no-expand', action='store_true',
                    help='不展开「余下 N 篇」——**会把折叠的文章漏掉**，'
                         '只有排查问题时才用')
    ap.add_argument('--expand', action='store_true',
                    help='只展开所有「余下 N 篇」然后退出（不采集）。'
                         '注意：采集时**本来就会先自动展开**，一般用不到这个')
    ap.add_argument('--max', type=int, default=0, help='最多采几篇（0=不限）')
    ap.add_argument('--skip', type=int, default=0, help='跳过前 N 篇（补采用）')
    ap.add_argument('--max-fail', type=int, default=3, help='连续失败几篇就停（默认 3）')
    ap.add_argument('--timeout', type=float, default=15.0, help='等浏览器窗口/URL 的秒数')
    ap.add_argument('--no-close', action='store_true', help='不关标签页（调试）')
    ap.add_argument('--no-verify-first', action='store_true', help='跳过首篇标题校验')
    ap.add_argument('-o', '--out', default='links.jsonl', help='输出 jsonl（默认 links.jsonl）')
    ap.add_argument('--index-out', default='', help='索引输出（默认与 -o 同目录的 articles.jsonl）')
    args = ap.parse_args()
    if args.doctor:
        sys.exit(cmd_doctor())
    sys.exit(cmd_main(args))


if __name__ == '__main__':
    main()
