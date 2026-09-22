#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""公众号主页窗口的「文章索引」解析规则。

单独放一个文件的原因：这是最需要回归测试的部分（页面结构一变就错），
而它本身是纯字符串处理。跟 `read_profile_list.py` 放一起会把 pywinauto
依赖传染给自检，让没有 pywinauto 的环境测不了规则。

输入是 UIA 读出来的**扁平 Text 名称列表**，实测结构为：

    账号名 / 账号简介 / 关注数 / 已关注 / 私信 / [Hyperlink 全部] / [Hyperlink 文章]
    4月23日
    智启未来！ECNUer专场共探“教师版小龙虾”培训活动圆满成功！
    阅读 251 赞 6 2个朋友看过
    4月9日
    第二期招募开启！ECNUer免费体验“教师版小龙虾”ZClaw-EDU！
    阅读 239 赞 1
    ...
    正在加载...

即「日期 / 标题 / 阅读数」三元一组循环，**没有行容器**。
"""

import re

# 日期：`4月23日` 或 `2025年12月18日`
DATE_RE = re.compile(r'^(\d{1,2}月\d{1,2}日|\d{4}年\d{1,2}月\d{1,2}日)$')
# 阅读数：`阅读 251 赞 6 2个朋友看过` / `阅读 1991 赞 20` / `阅读 109`
READ_RE = re.compile(r'^阅读\s*([\d.]+万?)(?:\s*赞\s*([\d.]+万?))?')
LOADING_HINT = '正在加载'


def extract_pairs(names):
    """从扁平的 Text 名称列表里抽出 [{'date','title','reads','likes'}, ...]。

    规则：遇到一个**整串就是一个日期**的 Text，就往后面最多看 3 个，取其中第一个
    「不是日期、不是阅读数、不是"正在加载…"」的非空 Text 当标题。

    为什么不能"取下一个非空 Text 当标题"了事：真实数据里出现过
    `阅读 …` 和 `正在加载...` 这种非标题项，直接取会串位。
    """
    rows, i = [], 0
    while i < len(names):
        nm = names[i]
        if not DATE_RE.match(nm):
            i += 1
            continue
        title, reads, likes = '', '', ''
        for j in range(i + 1, min(i + 4, len(names))):
            n2 = names[j]
            if not n2 or DATE_RE.match(n2):
                break
            m = READ_RE.match(n2)
            if m:
                reads, likes = m.group(1), (m.group(2) or '')
                break
            if LOADING_HINT in n2:
                break
            if not title:
                title = n2
        if title:
            rows.append({'date': nm, 'title': title, 'reads': reads, 'likes': likes})
        i += 1
    return rows


def has_loading_more(names):
    """列表末尾是否还有「正在加载...」——说明懒加载还没到底。

    注意：**不能把这个当成"确实还有更多"的确证**。实测即使滚动不生效，
    这个占位也会一直挂着，所以它只是提示"可能还有"。
    """
    return any(LOADING_HINT in (n or '') for n in names)


# ==========================================================================
# 第二种页面布局：「折叠分组」
# ==========================================================================
#
# 公众号主页有**两种**布局，上面的 `extract_pairs` 只覆盖第一种：
#
#   ① 平铺布局：树里「日期 / 标题 / 阅读数」都是独立 Text，标题可 Invoke。
#
#   ② 折叠分组布局（下面这套规则）：页面按「一次推送」分组，每组只展开 3 篇，
#      其余折成「余下 N 篇」。**标题根本不在无障碍树里**，只能从
#      `Document.Value`（正文纯文本，来自 `element_info.rich_text`）里抠。
#      在扁平 Text 列表上长出来的 `extract_pairs` 拿这种页面永远只能配出 1 篇。
#
# 正文的形状（实测样本，\u2004/\u2005 是微信用的窄空格）：
#
#   星期四 上海志愿服务动态 阅读 7915 赞 22   让急救知识走出医院…阅读 301
#   温暖申城 榜样力量 | 扎根静安15年…阅读 115 赞 1    余下 5 篇
#   9月10日 中社部首次亮相国新办发布会，介绍了这些情况 阅读 1116 赞 8 ...
#
# 规律：**每条「阅读」前面那一段就是本篇标题**；段首可能挂着上一组的
# 「余下 N 篇」和本组的日期组头，把它们反复剥掉即可。
#
# ⚠️ 本布局下「标题 ↔ 可点元素」只能靠**位置**配对，没有第二重证据。
#    所以必须用 `normalize_read` / `read_key_of_item` 逐条核对阅读数，
#    任何一条对不上就宁可不产出 —— 配错位会得到一份「标题与链接错位」的报告，
#    而报告本身看不出任何异常。

GROUPED_READ_RE = re.compile(r'阅读[\s\u2004\u2005\u00a0\u3000]*([\d.]+万?)'
                             r'(?:[\s\u2004\u2005\u00a0\u3000]*赞[\s\u2004\u2005\u00a0\u3000]*(\d+))?')
GROUPED_YUXIA_RE = re.compile(r'余下[\s\u2004\u2005\u00a0\u3000]*\d+[\s\u2004\u2005\u00a0\u3000]*篇')
# 日期组头：绝对日期 / 相对日期（最新那一组会是「星期四」「今天」这类）
GROUPED_DATE_RES = [
    re.compile(r'\d{4}[\s]*年[\s]*\d{1,2}[\s]*月[\s]*\d{1,2}[\s]*日'),
    re.compile(r'(?<!\d)\d{1,2}[\s]*月[\s]*\d{1,2}[\s]*日'),
    re.compile(r'星期[一二三四五六日天]'),
    re.compile(r'(?:今天|昨天|前天)'),
]
GROUPED_WS_RE = re.compile(r'^[\s\u2004\u2005\u00a0\u3000]+')


def _strip_group_lead(seg, cur_date):
    """反复剥掉段首的 空白 / 「余下 N 篇」 / 日期组头。

    返回 (剩余文本, 本段的日期组头或原值)。
    """
    while True:
        seg = GROUPED_WS_RE.sub('', seg)
        m = GROUPED_YUXIA_RE.match(seg)
        if m:
            seg = seg[m.end():]
            continue
        hit = None
        for pat in GROUPED_DATE_RES:
            hit = pat.match(seg)
            if hit:
                break
        if hit:
            cur_date = hit.group(0).strip()
            seg = seg[hit.end():]
            continue
        return seg, cur_date


def parse_grouped(value):
    """从「折叠分组」布局的正文里解析出有序的 [{'date','title','reads','likes'}]。

    ⚠️ 第 1 段前面挂着整个账号头部（账号名/简介/已关注/私信/标签页…），
    所以第 1 段要从**第一个日期组头**处切开；后面各段不需要这样处理
    （它们前面只可能有「余下 N 篇」和日期组头，`_strip_group_lead` 会剥掉）。
    """
    items, prev_end, cur_date = [], 0, ''
    for i, m in enumerate(GROUPED_READ_RE.finditer(value)):
        seg = value[prev_end:m.start()]
        prev_end = m.end()
        if i == 0:
            cut = None
            for pat in GROUPED_DATE_RES:
                s = pat.search(seg)
                if s and (cut is None or s.start() < cut):
                    cut = s.start()
            if cut is not None:
                seg = seg[cut:]
        seg, cur_date = _strip_group_lead(seg, cur_date)
        items.append({'date': cur_date, 'title': seg.strip(),
                      'reads': m.group(1), 'likes': m.group(2) or ''})
    return items


def absolutize_date(label, today=None):
    """把相对日期标签换成绝对日期，返回 (显示用日期, 原始标签)。

    为什么要换：折叠布局里**最新那一组的组头是「星期四」这类相对标签**，
    而项目既有约定是「今年只写 `M月D日`，跨年才写 `YYYY年M月D日`」。
    「星期四」直接进报告，时间跨度会变成「2023年5月22日 → 星期四」，看着像坏了。
    原始标签保留在 `date_raw` 里，可回溯。

    不做无根据的猜测：只在**标签确实是相对日期**时才换算；「星期X」取
    「今天之前最近的那个 X」（微信对今天/昨天另有名字，所以不会撞车）。
    """
    from datetime import datetime, timedelta
    lab = (label or '').strip()
    today = today or datetime.now()
    if not lab:
        return '', lab
    if lab in ('今天', '昨天', '前天'):
        d = today - timedelta(days={'今天': 0, '昨天': 1, '前天': 2}[lab])
    else:
        m = re.fullmatch(r'星期([一二三四五六日天])', lab)
        if not m:
            return lab, lab          # 已经是绝对日期，原样返回
        idx = '一二三四五六日'.index(m.group(1)) if m.group(1) in '一二三四五六日' else 6
        off = (today.weekday() - idx) % 7
        if off == 0:
            off = 7                  # 今天/昨天不会显示成「星期X」，故至少退一周
        d = today - timedelta(days=off)
    if d.year == today.year:
        return f'{d.month}月{d.day}日', lab
    return f'{d.year}年{d.month}月{d.day}日', lab


def is_read_name(nm):
    """这个 Text 是不是折叠布局里的「阅读数」（本布局下它是每篇的可点把手）。"""
    return bool(re.match(r'^阅读', (nm or '').strip()))


def normalize_read(nm):
    """把「阅读数」元素的名字归一成和 `read_key_of_item` 同一个形状，供逐条比对。

    元素名形如 `阅读\\u20067915\\u2004\\u2005赞\\u200622\\u2004\\u2005`
    → `阅读7915|22`；没有点赞则 `阅读301|`。
    """
    s = (nm or '').replace('\u2004', ' ').replace('\u2005', ' ').replace('\u00a0', ' ')
    m = GROUPED_READ_RE.search(s)
    return f'阅读{m.group(1)}|{m.group(2) or ""}' if m else ''


def read_key_of_item(it):
    return f"阅读{it['reads']}|{it['likes'] or ''}"


def dup_title_count(titles):
    """索引里有多少条标题是**重复的**（按多出来的条数计，不是按种类）。

    为什么要统计：同名标题下「索引 ↔ 链接」的配对是靠**顺序**逐一消费的
    （`make_link_report.gather`）。有这个数，用户才知道这批数据里有靠顺序配对的
    条目、一旦两边顺序不一致就会静默错位。
    """
    seen, dup = set(), 0
    for t in titles:
        if t in seen:
            dup += 1
        else:
            seen.add(t)
    return dup


_GROUPED_YUXIA_LOOSE_RE = re.compile(
    r'余下[\s\u2004\u2005\u00a0\u3000]*(\d+)[\s\u2004\u2005\u00a0\u3000]*篇')


def index_rows_from_store(store, order):
    """由「**已采到的全部**条目」生成索引行 —— 不是只写本次采到的那几条。

    索引（`articles.jsonl`）的职责是 **页面顺序**：`make_link_report.py` 靠它排序，
    并靠它与 `links.jsonl` **逐条配对**。所以索引必须与 `links.jsonl` 同条数、同顺序。

    ⚠️ 分批续采时的真缺陷（2026-09-22 修）：采集器原先写索引用的是**本次的 records**，
    于是「先采 30 篇，再 `--skip 30` 补 40 篇」会得到 **索引 40 条 / 链接 70 条**
    ——报告照样生成，只是前 30 条没有日期、排序也乱，**看不出任何异常**。
    改为按 `store`（jsonl 的累计内容）的写入顺序 `order` 输出后，
    「**索引条数 == 链接条数**」成了不变式（自检第 11 组有断言钉住）。
    """
    rows = []
    for k in order:
        r = store.get(k) or {}
        rows.append({
            'date': r.get('date') or '',
            'title': r.get('title') or '',
            'reads': r.get('reads') if r.get('reads') is not None else '',
            'likes': r.get('likes') if r.get('likes') is not None else '',
            'account': r.get('account') or '',
            'gh_id': r.get('gh_id') or '',
        })
    return rows


def folded_markers(value, names):
    """页面还剩多少个「余下 N 篇」标记，以及它们累计折叠掉多少篇。

    返回 `(标记个数, 累计折叠篇数)`。纯函数，便于自检。

    **为什么必须报出来**：折叠的文章标题不在 `Document` 正文里，
    **不展开就采 = 静默漏采**——报告"看着正常"，只是比别人少了几篇。
    这正是本项目反复踩的那类坑，所以采集前必须大声报这两个数。
    """
    markers = 0
    for n in (names or []):
        s = (n or '').strip()
        if GROUPED_YUXIA_RE.fullmatch(s) or re.fullmatch(r'余下\s*\d+\s*篇', s):
            markers += 1
    hidden = sum(int(x) for x in _GROUPED_YUXIA_LOOSE_RE.findall(value or ''))
    return markers, hidden


def truncation_info(items, taken):
    """`--max` / `--skip` 截断后，末尾那一天的文章是否被**从中间切断**。

    返回 `None`，或 `(当天日期, 同一天还剩几篇没取)`。

    为什么要报：截断点落在一天中间时，报告的「时间跨度」是**断的**——
    用户以为采到了 7月31日为止，其实 7月31日 只采了一半。这属于
    "结果看着正常但实际降级"，必须出声。
    """
    if taken <= 0 or taken >= len(items):
        return None
    last = (items[taken - 1] or {}).get('date')
    nxt = (items[taken] or {}).get('date')
    if not last or last != nxt:
        return None
    n = 0
    for it in items[taken:]:
        if (it or {}).get('date') != last:
            break
        n += 1
    return (last, n)


BROWSER_SUFFIXES = (' - Google Chrome', ' - Microsoft Edge', ' - Chromium',
                    ' - Brave', ' - Vivaldi', ' - Opera', ' - 360安全浏览器')


def title_matches(win_title_text, art_title):
    """浏览器窗口标题 vs 文章标题。

    原理：浏览器窗口标题 = **当前标签页标题**（+ 浏览器后缀），而标签页标题就是
    文章页面的 `<title>`。实测微信文章页的 `<title>` 与公众号列表里的标题
    **完全一致**，所以直接包含判断就够。

    `collect_via_browser.py` 用它做两件事，都是要害：
      1. 定位文章落到了哪个浏览器窗口
      2. 发 Ctrl+W 关标签**之前**的最后一道确认（标题不匹配就宁可不关，
         免得把用户正在看的标签页关掉）

    退化到「前 12 字」是为了容忍公众号运营者改过页面标题的情况；
    门槛取 6 是为了避免「4月23日」这种短串乱匹配。
    """
    w = ' '.join((win_title_text or '').split())
    a = ' '.join((art_title or '').split())
    if not w or not a:
        return False
    if a in w:
        return True
    head = a[:12]
    return len(head) >= 6 and head in w


def pick_article_doc(cands, art_title, known_keys=None, key_fn=None):
    """从窗口里读到的多个 `(页面标题, URL)` 中挑出目标文章那一个。

    为什么需要它（实测踩过的坑）：**同一个浏览器窗口里会同时暴露多个标签页的
    Document**。收完 2 篇之后，那个窗口里有 2 个 mp.weixin 的 `Document`
    （第 1 篇和第 2 篇）。早先版本直接取第一个，于是**第 2 篇读回了第 1 篇的
    URL，而报告看起来完全正常** —— 又一个静默的错。

    判定顺序：文档标题匹配 → 只有一个候选 → 排除已收的之后只剩一个。
    全都不成立就返回失败原因，**不猜**。`key_fn` 用于去重口径（注入以保持
    本模块无依赖）。返回 `(url, name, how)` 或 `(None, '', 原因)`。
    """
    if not cands:
        return None, '', '没有任何 mp.weixin 的 Document'
    for nm, v in cands:
        if title_matches(nm, art_title):
            return v, nm, 'doc-name'
    if len(cands) == 1:
        nm, v = cands[0]
        return v, nm, 'only-one'
    if known_keys and key_fn:
        fresh = [(nm, v) for nm, v in cands if key_fn(v) not in known_keys]
        if len(fresh) == 1:
            nm, v = fresh[0]
            return v, nm, 'not-known'
    listing = ' | '.join(f'{nm[:26]}=>{v[-14:]}' for nm, v in cands)
    return None, '', (f'窗口里有 {len(cands)} 个 mp.weixin Document，但标题都对不上'
                      f'「{(art_title or "")[:20]}」，无法确定是哪一篇（不猜）：{listing}')


# ---------------------------------------------------------------------------
# 环境预检（--doctor）
# ---------------------------------------------------------------------------
#
# 单独放这里的原因和上面一样：判定规则要能被自检覆盖，而它本身是纯逻辑，
# 不需要 pywinauto。`collect_via_browser.py --doctor` 只负责**收集事实**，
# 是否放行由本函数决定。
#
# 动机：路径 A+++ 全量跑一次要 ~11 分钟且会抢前台。把「微信没开」「打开方式
# 没设成系统默认浏览器」这类问题放到跑之前 5 秒查出来，比跑完再猜便宜得多。

CHROMIUM_WINDOW_CLASS = 'Chrome_WidgetWin_1'   # 普通 Chromium 系浏览器窗口
INAPP_WINDOW_CLASS = 'Chrome_WidgetWin_0'      # 微信内置浏览器（WeChatAppEx.exe）

# 少于这个条数、且列表末尾还挂着「正在加载...」时，提醒用户手动滚到底。
# 取 30 是因为实测的两个观测点是 20（没加载完）和 103（加载完）。
FEW_ARTICLES = 30


def judge_environment(facts):
    """把「环境事实」判成一份给人看的检查清单。

    纯函数：`facts` 是普通 dict，缺键/`None` 都安全。**不懂的事实就不报**，
    绝不默认通过——这是本项目的铁律（SKILL.md 硬知识 23）。

    返回 `[{'level': 'ok'|'warn'|'fail', 'text': str}, ...]`。
    """
    f = facts or {}
    out = []

    def add(level, text):
        out.append({'level': level, 'text': text})

    # 1. 依赖
    if f.get('pywinauto_ok') is False:
        add('fail', 'pywinauto 不可用 → 先装依赖（技能根目录）：'
                    'pip install -r requirements.txt；'
                    '或用 run-doctor.bat，它会自动找装好依赖的 Python')
    elif f.get('pywinauto_ok'):
        add('ok', f"pywinauto 可用（{f.get('python') or 'python'}）")

    # 2. 微信进程。注意要把主进程和内置浏览器进程（WeChatAppEx.exe）分开：
    #    内置浏览器**进程常驻是正常的**，只有它的**窗口**出现才说明文章落错了地方。
    if 'wechat_pids' in f:
        procs = f.get('wechat_pids') or []
        main = [(p, n) for p, n in procs if (n or '').lower().startswith('wechat.exe')]
        appex = [(p, n) for p, n in procs if (n or '').lower().startswith('wechatappex')]
        if not procs:
            add('fail', '没有发现微信进程 → 先启动 PC 微信并登录')
        elif not main:
            add('fail', f'只发现 {len(appex)} 个内置浏览器进程（WeChatAppEx.exe），'
                        f'没有微信主进程 WeChat.exe → 微信可能没正常启动')
        else:
            add('ok', f"微信主进程 {len(main)} 个："
                      + '、'.join(f'{n}(pid {p})' for p, n in main[:2])
                      + (f'；另有内置浏览器进程 {len(appex)} 个'
                         f'（进程常驻是正常的，要看窗口才算数）' if appex else ''))

    # 3. 公众号主页窗口
    if 'profile_windows' in f:
        pw = f.get('profile_windows')
        if not pw:
            add('fail', '没有「公众号主页窗口」→ 请在微信里搜索并打开目标公众号，'
                        '会多出一个标题为「公众号」的独立窗口。'
                        '（老的「历史消息」窗口已下线，不是那个）')
        elif pw == 1:
            add('ok', '公众号主页窗口 1 个')
        else:
            add('warn', f'公众号主页窗口有 {pw} 个 → 脚本只会用第 1 个，'
                        f'建议只留目标公众号那一个，免得读到别的号')

    # 4. 公众号窗口的 Document 可读性（拿到账号名/gh_id）
    if 'profile_doc_count' in f:
        dc = f.get('profile_doc_count')
        if dc == 0:
            add('warn', '公众号窗口读不到 Document（拿不到账号名和原始 ID，'
                        '但文章清单通常仍可读）→ 把窗口点出来别让它被遮住，或加 --foreground 重跑')
        elif isinstance(dc, int) and dc > 0:
            add('ok', f"公众号窗口 Document 可读，账号：{f.get('account') or '(未解析出名称)'}")

    # 5. 文章清单能不能解析出来
    n = f.get('article_count')
    if 'article_count' in f:
        if n == 0:
            add('fail', '文章清单解析出 0 篇 → 若上面的 Text 数 >0，说明页面结构变了，'
                        '把 --list 的完整输出发回来修解析规则。'
                        '**不要因为这一条就判断「该号没有文章」**')
        elif isinstance(n, int):
            add('ok', f'文章清单解析出 {n} 篇')

    # 6. 懒加载：这是最容易误判的一步（我就误判过，把"没加载完"写成了"脚本上限"）
    if f.get('loading_more'):
        if isinstance(n, int) and 0 < n <= FEW_ARTICLES:
            add('warn', f'列表末尾挂着「正在加载...」且只解析出 {n} 篇 → 很可能**没加载完**。'
                        f'请**在公众号窗口里手动把列表滚到底**再重跑。'
                        f'实测同一脚本读到过 20 篇、也读到过 103 篇，差别只在加载了多少，'
                        f'这**不是**脚本能采到的上限')
        else:
            add('warn', '列表末尾挂着「正在加载...」→ 先确认已在窗口里手动滚到底，'
                        '否则可能还有更早的文章没进来')

    # 7. 微信内置浏览器：路径 A+++ 的头号杀手
    appex = f.get('appex_windows') or 0
    if appex:
        add('warn', f'检测到 {appex} 个微信内置浏览器窗口（{INAPP_WINDOW_CLASS} / WeChatAppEx.exe）。'
                    f'若采集时文章落到了这里，说明微信「设置 → 通用设置 → 使用系统默认浏览器打开网页」'
                    f'没开——内置浏览器是 D3D 合成，**没有任何无障碍对象，读不到 URL**')

    # 8. 浏览器窗口
    if 'browser_windows' in f:
        bw = f.get('browser_windows') or []
        if not bw:
            add('warn', f'当前没有已打开的 {CHROMIUM_WINDOW_CLASS} 浏览器窗口 → '
                        f'微信会自行拉起默认浏览器，通常没问题；'
                        f'但若系统默认浏览器不是 Chromium 系（如 Firefox），'
                        f'本路径读不到 Document，请改用路径 A（剪贴板监听）')
        else:
            tag = '、'.join((r.get('title') or r.get('cls') or '?')[:24] for r in bw[:3])
            add('ok', f'浏览器窗口 {len(bw)} 个：{tag}')
    return out


def verdict(items):
    """由检查项汇总结论：`blocked` / `attention` / `ready` / `unknown`。

    **一项都没查出来时返回 `unknown`，不返回 `ready`** —— 「什么都没确认」和
    「全部确认通过」是两回事，混为一谈就是本项目最常见的失败模式。
    """
    items = items or []
    if not items:
        return 'unknown'
    levels = {i['level'] for i in items}
    if 'fail' in levels:
        return 'blocked'
    if 'warn' in levels:
        return 'attention'
    return 'ready'
