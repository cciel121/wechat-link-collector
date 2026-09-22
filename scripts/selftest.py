#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""自检：用构造样本验证两个脚本的核心逻辑（不联网、不碰你的剪贴板）。

    python selftest.py
"""

import json
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable


def check(name, cond, extra=''):
    print(('  PASS  ' if cond else '  FAIL  ') + name + (f'  {extra}' if extra else ''))
    return cond


def test_clipboard_helpers():
    print('\n[1] clipboard_watch 的链接识别与去重')
    sys.path.insert(0, HERE)
    import clipboard_watch as cw

    ok = True
    u1 = 'https://mp.weixin.qq.com/s?__biz=MzI5Nz==&mid=2247485001&idx=1&sn=aaa&timestamp=1789980851&signature=zzz'
    u2 = 'https://mp.weixin.qq.com/s?__biz=MzI5Nz==&mid=2247485001&idx=1&sn=aaa'
    ok &= check('签名链与永久链归一为同一 key', cw.dedup_key(u1) == cw.dedup_key(u2), cw.dedup_key(u1))

    u3 = 'https://mp.weixin.qq.com/s/AbCdEfGhIjKlMn'
    ok &= check('短链有独立归一 key', cw.dedup_key(u3) == 'short:AbCdEfGhIjKlMn')

    ok &= check('从带修饰的文本里抠出链接',
                cw.extract_url('快看这篇 https://mp.weixin.qq.com/s/AbCdEfGhIjKlMn 真的不错', False)
                == 'https://mp.weixin.qq.com/s/AbCdEfGhIjKlMn')
    ok &= check('忽略非微信链接（默认模式）',
                cw.extract_url('https://www.example.com/x', False) is None)
    ok &= check('忽略非微信链接（--include-all 模式可收）',
                cw.extract_url('https://www.example.com/x', True) == 'https://www.example.com/x')
    ok &= check('空文本安全', cw.extract_url('', False) is None and cw.extract_url(None, False) is None)
    return ok


def test_analyzer():
    print('\n[2] analyze_capture 的链接提取与列表接口判定')

    ok = True
    tmp = tempfile.mkdtemp(prefix='cap_')
    jsonl = os.path.join(tmp, 'cap.jsonl')

    art1 = ('https://mp.weixin.qq.com/s?__biz=MzI5Nz==&mid=2247485001&idx=1&sn=aaa'
            '&timestamp=1789980851&signature=zzz')
    art1_permanent = 'https://mp.weixin.qq.com/s?__biz=MzI5Nz==&mid=2247485001&idx=1&sn=aaa'
    art2 = 'https://mp.weixin.qq.com/s?__biz=MzI5Nz==&mid=2247485002&idx=1&sn=bbb'

    rows = [
        {
            'time': '2026-09-21 17:00:01', 'method': 'GET', 'host': 'mp.weixin.qq.com',
            'path': '/mp/profile_ext',
            'url': 'https://mp.weixin.qq.com/mp/profile_ext?action=getmsg&__biz=MzI5Nz=='
                   '&offset=0&count=10&key=k&pass_ticket=p&uin=1',
            'query_keys': ['action', '__biz', 'offset', 'count', 'key', 'pass_ticket', 'uin'],
            'status': 200, 'body_len': 9000, 'embedded_links': [art1, art2],
        },
        {
            'time': '2026-09-21 17:00:05', 'method': 'GET', 'host': 'mp.weixin.qq.com',
            'path': '/s', 'url': art1_permanent,
            'query_keys': ['__biz', 'mid', 'idx', 'sn'], 'status': 200,
            'body_len': 40000, 'embedded_links': [],
        },
        {
            'time': '2026-09-21 17:00:06', 'method': 'GET', 'host': 'mmbiz.qpic.cn',
            'path': '/mmbiz_png/x.png', 'url': 'https://mmbiz.qpic.cn/mmbiz_png/x.png',
            'query_keys': [], 'status': 200, 'body_len': None, 'embedded_links': [],
        },
    ]
    with open(jsonl, 'w', encoding='utf-8') as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + '\n')

    outdir = os.path.join(tmp, 'report')
    proc = subprocess.run([PY, os.path.join(HERE, 'analyze_capture.py'), jsonl, '-o', outdir],
                          capture_output=True, text=True, encoding='utf-8')
    ok &= check('分析器退出码为 0', proc.returncode == 0, proc.stderr.strip()[:200])

    links_path = os.path.join(outdir, 'links.txt')
    ok &= check('生成 links.txt', os.path.exists(links_path))
    if os.path.exists(links_path):
        with open(links_path, encoding='utf-8') as fh:
            got = [l.strip() for l in fh if l.strip()]
        ok &= check('去重后正好 2 条链接（签名链与永久链合并）', len(got) == 2, f'实得 {len(got)}')

    md_path = os.path.join(outdir, 'analysis.md')
    md = open(md_path, encoding='utf-8').read() if os.path.exists(md_path) else ''
    ok &= check('识别出 profile_ext 为列表接口候选', 'profile_ext' in md and '列表接口候选' in md)
    ok &= check('候选里出现 getmsg 判据', '列表接口关键词' in md)
    ok &= check('无内嵌链接的图片请求未被误判为列表',
                md.count('### ') <= 3, f"候选数 {md.count('### ')}")

    # HAR 通道
    har = os.path.join(tmp, 'session.har')
    with open(har, 'w', encoding='utf-8') as fh:
        json.dump({'log': {'entries': [{
            'startedDateTime': '2026-09-21T17:00:05.000Z',
            'request': {'method': 'GET', 'url': art2,
                        'queryString': [{'name': '__biz'}, {'name': 'mid'}]},
            'response': {'status': 200,
                         'content': {'mimeType': 'text/html', 'text': '<a href="%s">x</a>' % art1}},
        }]}}, fh)

    outdir2 = os.path.join(tmp, 'report_har')
    proc2 = subprocess.run([PY, os.path.join(HERE, 'analyze_capture.py'), har, '-o', outdir2],
                           capture_output=True, text=True, encoding='utf-8')
    ok &= check('HAR 通道退出码为 0', proc2.returncode == 0, proc2.stderr.strip()[:200])
    txt2 = os.path.join(outdir2, 'links.txt')
    if os.path.exists(txt2):
        got2 = [l.strip() for l in open(txt2, encoding='utf-8') if l.strip()]
        ok &= check('HAR：请求本身 + 响应体内嵌链接都抓到，去重为 2 条', len(got2) == 2, f'实得 {len(got2)}')

    return ok


def test_saz():
    print('\n[3] Fiddler .saz 会话归档解析')
    import zipfile

    ok = True
    tmp = tempfile.mkdtemp(prefix='saz_')
    saz = os.path.join(tmp, 'session.saz')

    art1 = 'https://mp.weixin.qq.com/s?__biz=MzI5Nz==&mid=2247485001&idx=1&sn=aaa'
    art2 = 'https://mp.weixin.qq.com/s?__biz=MzI5Nz==&mid=2247485002&idx=1&sn=bbb'

    list_req = (
        'GET http://mp.weixin.qq.com/mp/profile_ext?action=getmsg&__biz=MzI5Nz=='
        '&offset=0&count=10&key=k&pass_ticket=p HTTP/1.1\r\n'
        'Host: mp.weixin.qq.com\r\n'
        'User-Agent: MicroMessenger/8.0\r\n\r\n'
    )
    list_resp = (
        'HTTP/1.1 200 OK\r\n'
        'Content-Type: text/html; charset=utf-8\r\n\r\n'
        f'<html><a href="{art1}">a</a><a href="{art2}">b</a></html>'
    )
    art_req = f'GET {art1}&timestamp=1789980851&signature=zz HTTP/1.1\r\nHost: mp.weixin.qq.com\r\n\r\n'
    art_resp = 'HTTP/1.1 200 OK\r\nContent-Type: text/html\r\n\r\n<html>正文</html>'
    img_req = 'GET http://mmbiz.qpic.cn/mmbiz_png/x.png HTTP/1.1\r\nHost: mmbiz.qpic.cn\r\n\r\n'
    img_resp = 'HTTP/1.1 200 OK\r\nContent-Type: image/png\r\n\r\n\x89PNG'

    with zipfile.ZipFile(saz, 'w') as zf:
        for i, (c, s) in enumerate([(list_req, list_resp), (art_req, art_resp), (img_req, img_resp)], 1):
            zf.writestr(f'raw/{i}_c.txt', c.encode('utf-8'))
            zf.writestr(f'raw/{i}_s.txt', s.encode('utf-8'))

    outdir = os.path.join(tmp, 'report')
    proc = subprocess.run([PY, os.path.join(HERE, 'analyze_capture.py'), saz, '-o', outdir],
                          capture_output=True, text=True, encoding='utf-8')
    ok &= check('SAZ 解析退出码为 0', proc.returncode == 0, proc.stderr.strip()[:200])

    links_path = os.path.join(outdir, 'links.txt')
    if os.path.exists(links_path):
        got = [l.strip() for l in open(links_path, encoding='utf-8') if l.strip()]
        ok &= check('SAZ：请求行 URL 与响应体内嵌链接都能抓到', len(got) == 2, f'实得 {len(got)}')
        ok &= check('SAZ：签名链与永久链已合并', len({g.split("signature")[0] for g in got}) == 2)
    else:
        ok &= check('SAZ：生成 links.txt', False)

    md_path = os.path.join(outdir, 'analysis.md')
    md = open(md_path, encoding='utf-8').read() if os.path.exists(md_path) else ''
    ok &= check('SAZ：命中请求数为 3（含图片）', '载入记录数：**3**' in md)
    ok &= check('SAZ：profile_ext 被列为列表接口候选', 'profile_ext' in md)
    return ok


def test_clipboard_loop():
    """用假剪贴板跑完整主循环，验证 links.jsonl 与 links.txt 都会落盘。

    这一条是补上一次真实踩坑：只在这两个脚本各自自检、没有跨脚本联调时，
    剪贴板产物喂给分析器会被当成空壳请求，静默返回 0 条链接。
    """
    print('\n[4] clipboard_watch 主循环：两个产物都要落盘')
    sys.path.insert(0, HERE)
    import clipboard_watch as cw

    ok = True
    tmp = tempfile.mkdtemp(prefix='clip_')
    out = os.path.join(tmp, 'links.jsonl')

    u1 = 'https://mp.weixin.qq.com/s?__biz=MzI5Nz==&mid=2247485001&idx=1&sn=aaa'
    u1_signed = u1 + '&timestamp=1789980851&signature=zzz'
    u2 = 'https://mp.weixin.qq.com/s?__biz=MzI5Nz==&mid=2247485002&idx=1&sn=bbb'

    seq = iter([u1, u1_signed, u2, KeyboardInterrupt])

    def fake_reader():
        def read():
            nxt = next(seq)
            if nxt is KeyboardInterrupt:
                raise KeyboardInterrupt
            return nxt
        return read

    cw.make_clipboard_reader = fake_reader
    old_argv = sys.argv
    sys.argv = ['clipboard_watch.py', '-o', out, '--interval', '0']
    try:
        cw.main()
    finally:
        sys.argv = old_argv

    txt = os.path.join(tmp, 'links.txt')
    ok &= check('主循环后 links.jsonl 存在', os.path.exists(out))
    ok &= check('主循环后 links.txt 也存在（不再依赖 Ctrl+C）', os.path.exists(txt))
    if os.path.exists(txt):
        got = [l.strip() for l in open(txt, encoding='utf-8') if l.strip()]
        ok &= check('签名链与永久链合并，去重为 2 条', len(got) == 2, f'实得 {len(got)}')

    # 关键回归：把这份产物直接喂给分析器，不能被当成空壳请求
    outdir = os.path.join(tmp, 'report')
    proc = subprocess.run([PY, os.path.join(HERE, 'analyze_capture.py'), out, '-o', outdir],
                          capture_output=True, text=True, encoding='utf-8')
    ok &= check('分析器能读懂剪贴板 jsonl（退出码 0）', proc.returncode == 0, proc.stderr.strip()[:200])
    ok &= check('分析器识别为链接清单而非抓包记录', '链接清单' in proc.stderr)
    links_txt = os.path.join(outdir, 'links.txt')
    if os.path.exists(links_txt):
        got2 = [l.strip() for l in open(links_txt, encoding='utf-8') if l.strip()]
        ok &= check('跨脚本联调：链接完整传递（2 条，非 0 条）', len(got2) == 2, f'实得 {len(got2)}')
    else:
        ok &= check('跨脚本联调：生成 links.txt', False)
    return ok


def test_addon_syntax():
    print('\n[5] mitmproxy addon 语法检查（不装 mitmproxy，仅编译）')
    import py_compile
    try:
        py_compile.compile(os.path.join(HERE, 'mitm_mp_capture.py'), doraise=True)
        return check('mitm_mp_capture.py 可编译', True)
    except py_compile.PyCompileError as e:
        return check('mitm_mp_capture.py 可编译', False, str(e)[:200])


def test_collect_contract():
    """UIA 采集器（collect_wechat_uia.py）的编译与产物契约。

    它没法做无 GUI 自检（需要真实窗口 + pywinauto 真点击），所以这里只守两件事：
      1) 编译检查 —— 防止语法/缩进错误漏到交付
      2) **产物契约回归** —— 拿它产物形态的 jsonl 直接喂分析器

    第 2 条是补踩过的坑：collect 的输出有 url/key 但非 http 时，分析器会
    静默跳过并报「载入 0 条请求记录」，退出码却是 0 —— 看着像"没采到"，
    其实是解析器不认。这个交界处坏过两次，必须由自检守住。
    """
    print('\n[6] collect_wechat_uia 的编译与产物契约')
    import py_compile

    ok = True
    for f in ('collect_wechat_uia.py', 'diag_uia_mode.py'):
        try:
            py_compile.compile(os.path.join(HERE, f), doraise=True)
            ok &= check(f'{f} 可编译', True)
        except py_compile.PyCompileError as e:
            ok &= check(f'{f} 可编译', False, str(e)[:200])

    fx = os.path.join(HERE, 'fixtures')
    need = ['list_blank.html', 'list_same.html', 'a1.html', 'a2.html', 'a3.html']
    missing = [n for n in need if not os.path.exists(os.path.join(fx, n))]
    ok &= check('回归夹具齐全（5 个 html）', not missing, f'缺 {missing}' if missing else '')

    sys.path.insert(0, HERE)
    import analyze_capture as ac

    tmp = tempfile.mkdtemp(prefix='uia_')
    p = os.path.join(tmp, 'links.jsonl')
    urls = ['https://mp.weixin.qq.com/s/yyHY_MsK94k6BEbfhcA3QQ',
            'https://mp.weixin.qq.com/s/_OplKEZ5BSBSyJKM5Nca2g',
            'https://mp.weixin.qq.com/s/z3e4HQD0Uf_Q76N8s-_7WQ']
    with open(p, 'w', encoding='utf-8') as fh:
        for u in urls:
            fh.write(json.dumps({
                'url': u, 'key': 's:' + u.split('/s/')[1], 'title': '标题',
                'source': 'uia', 'list_window': '历史消息', 'opened_via': 'invoke',
                'collected_at': '2026-09-21 17:40:00',
            }, ensure_ascii=False) + '\n')

    recs = ac.load_any(p)
    ok &= check('collect 产物被识别为链接清单（3 条）',
                len(recs) == 3 and all(r['source'] == 'linklist' for r in recs),
                f'实得 {len(recs)}')

    p2 = os.path.join(tmp, 'file_links.jsonl')
    with open(p2, 'w', encoding='utf-8') as fh:
        for u in ('file:///C:/x/a1.html', 'file:///C:/x/a2.html'):
            fh.write(json.dumps({'url': u, 'key': 'u:' + u}, ensure_ascii=False) + '\n')
    r = subprocess.run([PY, os.path.join(HERE, 'analyze_capture.py'), p2,
                        '-o', os.path.join(tmp, 'r2')],
                       capture_output=True, text=True, encoding='utf-8')
    ok &= check('非 http 条目会明确提示，而非静默吞成 0 条',
                '不是 http' in (r.stderr or ''), (r.stderr or '').strip()[:110])
    return ok


def test_profile_parse():
    print('\n[7] profile_parse 的文章索引解析规则（公众号主页窗口）')
    sys.path.insert(0, HERE)
    import profile_parse as pp

    ok = True

    # 真实结构：扁平 Text，日期/标题/阅读数 三元一组，前后还有账号名和标签页
    real = [
        '华东师大全民数字素养培训基地',
        '国家级全民数字素养与技能培训基地（华东师范大学）',
        '3个朋友关注', '已关注', '私信',
        '全部', '文章',
        '4月23日',
        '智启未来！ECNUer专场共探“教师版小龙虾”培训活动圆满成功！',
        '阅读 251 赞 6 2个朋友看过',
        '2025年10月28日',
        '数据科学与工程学院“X-lab AI开源社区”入选上海市AI开源奖励计划，打造高校引领开源生态建设新范式',
        '阅读 109',
        '正在加载...',
    ]
    rows = pp.extract_pairs(real)
    ok &= check('解析出 2 条而非 0 条（账号名/标签页不能串位）', len(rows) == 2, f'实际 {len(rows)}')
    ok &= check('第 1 条日期正确', rows and rows[0]['date'] == '4月23日')
    ok &= check('第 1 条标题正确', rows and rows[0]['title'].startswith('智启未来'))
    ok &= check('阅读数带赞', rows and rows[0]['reads'] == '251' and rows[0]['likes'] == '6')
    ok &= check('无赞时 likes 为空', len(rows) > 1 and rows[1]['reads'] == '109'
                and rows[1]['likes'] == '')
    ok &= check('「正在加载...」没被当成标题',
                all('正在加载' not in r['title'] for r in rows))

    # 真实边界：标题里含"月""日"字样，不能误判为日期行
    tricky = [
        '5月6日',
        '关于5月20日举办开放日的通知',
        '阅读 120 赞 3',
    ]
    t2 = pp.extract_pairs(tricky)
    ok &= check('标题含"5月20日"不被误判为日期', len(t2) == 1 and t2[0]['title'].startswith('关于5月'),
                f'实际 {t2}')

    # 只有日期没标题时不能崩、也不能造出空条目
    ok &= check('日期后跟日期 → 不产出空条目',
                pp.extract_pairs(['4月1日', '4月2日']) == [])
    ok &= check('空输入安全', pp.extract_pairs([]) == [] and pp.extract_pairs(['']) == [])
    ok &= check('has_loading_more 只在有占位时为真',
                pp.has_loading_more(real) is True and pp.has_loading_more(['a', 'b']) is False)

    # 编译检查（这两个脚本要能跑起来）
    import py_compile
    for fn in ('read_profile_list.py', 'profile_parse.py'):
        try:
            py_compile.compile(os.path.join(HERE, fn), doraise=True)
            ok &= check(f'{fn} 可编译', True)
        except py_compile.PyCompileError as e:
            ok &= check(f'{fn} 可编译', False, str(e)[:160])
    return ok


def test_browser_route():
    """路径 A+++（微信开进系统默认浏览器 → 读 Document）里最容易错的两处。

    这两条都是**实测踩出来的真 bug**，不是假想的边界：
      1. 同一个浏览器窗口会同时暴露多个标签页的 Document，取第一个会读串篇
      2. 标题匹配也用来做「Ctrl+W 关标签前」的最后确认，判断错就会关错标签
    """
    print('\n[8] 路径 A+++：标签窗口定位 + 目标文档挑选')
    ok = True
    import profile_parse as pp

    # --- title_matches ---
    art = '第二期招募开启！ECNUer免费体验“教师版小龙虾”ZClaw-EDU！'
    ok &= check('标题匹配：带浏览器后缀',
                pp.title_matches(art + ' - Google Chrome', art) is True)
    ok &= check('标题匹配：Edge 后缀',
                pp.title_matches(art + ' - Microsoft Edge', art) is True)
    ok &= check('标题匹配：前 12 字兜底（页面标题被改过）',
                pp.title_matches('第二期招募开启！ECNUer免费体验活动', art) is True)
    ok &= check('标题匹配：别的文章不能匹配上',
                pp.title_matches('智启未来！ECNUer专场共探培训活动圆满成功！ - Google Chrome',
                                 art) is False)
    ok &= check('标题匹配：空值安全',
                pp.title_matches('', art) is False and pp.title_matches('x', '') is False)

    # --- pick_article_doc：读串篇的回归测试 ---
    url1 = 'https://mp.weixin.qq.com/s/yyHY_MsK94k6BEbfhcA3QQ'
    url2 = 'https://mp.weixin.qq.com/s/_OplKEZ5BSBSyJKM5Nca2g'
    t1 = '智启未来！ECNUer专场共探“教师版小龙虾”培训活动圆满成功！'
    two = [(t1, url1), (art, url2)]          # Document 顺序 = 标签顺序，第一篇在前
    u, nm, how = pp.pick_article_doc(two, art)
    ok &= check('两个标签页在树里：按标题挑中第 2 篇（不能取第一个）',
                u == url2 and how == 'doc-name', f'得到 {u} / {how}')

    u, nm, how = pp.pick_article_doc([(t1, url1)], art)
    ok &= check('只有一个候选：标题对不上也用（only-one）',
                u == url1 and how == 'only-one', f'得到 {u} / {how}')

    known = {'s:yyHY_MsK94k6BEbfhcA3QQ'}
    u, nm, how = pp.pick_article_doc(two, '完全不同的标题啊啊啊', known_keys=known,
                                     key_fn=lambda x: 's:' + x.split('/s/')[1])
    ok &= check('多个候选、标题都不匹配：用「不在已收清单里」挑（not-known）',
                u == url2 and how == 'not-known', f'得到 {u} / {how}')

    u, nm, how = pp.pick_article_doc(two, '完全不同的标题啊啊啊')
    ok &= check('歧义时**不猜**，返回原因',
                u is None and '无法确定' in how, f'得到 {u} / {how}')
    u, nm, how = pp.pick_article_doc([], art)
    ok &= check('没有候选时给出原因（不是静默 None）',
                u is None and how != '', f'{how}')

    # --- 编译检查 ---
    import py_compile
    for fn in ('collect_via_browser.py', 'profile_parse.py'):
        try:
            py_compile.compile(os.path.join(HERE, fn), doraise=True)
            ok &= check(f'{fn} 可编译', True)
        except py_compile.PyCompileError as e:
            ok &= check(f'{fn} 可编译', False, str(e)[:160])
    return ok


def test_report_join():
    """「文章索引 + 链接清单 → 可读报告」这一步的跨脚本契约。

    真 bug 回顾：第一版随手写的生成器直接按 `links.jsonl` 的顺序排表，
    而重试补采的单篇会被**追加到文件末尾**，于是报告里"时间跨度"印成
    `2024年10月15日 → 4月23日`，看着像数据坏了。排序必须以**索引**为准。
    """
    print('\n[9] 报告合成：索引顺序为准 + 缺失必须列出来 + md/html 同源')
    ok = True
    with tempfile.TemporaryDirectory() as td:
        idx = os.path.join(td, 'articles.jsonl')
        lnk = os.path.join(td, 'links.jsonl')
        out = os.path.join(td, 'report.md')
        # 索引：新→旧。链接：故意把最旧的那篇追加到末尾（模拟重试补采）
        with open(idx, 'w', encoding='utf-8') as fh:
            for d, t in (('4月23日', '最新一篇'), ('3月1日', '中间一篇'), ('2020年7月2日', '最旧一篇')):
                fh.write(json.dumps({'date': d, 'title': t, 'reads': '1'},
                                    ensure_ascii=False) + '\n')
        with open(lnk, 'w', encoding='utf-8') as fh:
            for t in ('最新一篇', '中间一篇', '最旧一篇'):
                fh.write(json.dumps(
                    {'url': f'https://mp.weixin.qq.com/s/{t}', 'title': t,
                     'date': '', 'account': '测试号', 'gh_id': 'gh_test'},
                    ensure_ascii=False) + '\n')

        r = subprocess.run([PY, os.path.join(HERE, 'make_link_report.py'),
                            '--index', idx, '--links', lnk, '-o', out],
                           capture_output=True, text=True)
        ok &= check('make_link_report.py 正常退出', r.returncode == 0,
                    (r.stderr or '')[:120])
        md = open(out, encoding='utf-8').read() if os.path.exists(out) else ''
        lines = [l for l in md.splitlines() if l.startswith('| ') and '# ' not in l]
        ok &= check('表里 3 行（含表头行之外的 3 条数据）',
                    len([l for l in lines if '打开' in l]) == 3, f'{len(lines)} 行')
        order_ok = md.index('最新一篇') < md.index('中间一篇') < md.index('最旧一篇')
        ok &= check('按索引顺序（新→旧）排列，不受链接文件顺序影响', order_ok)
        ok &= check('时间跨度按索引首尾算（老→新）',
                    '2020年7月2日 → 4月23日' in md)

        # 大标题：默认必须自动带上账号名。
        # 真缺陷回顾：--title 默认值是通用名，而 run-report.bat 不传 --title，
        # 于是双击生成的报告标题永远是「公众号文章链接清单」，账号名白采了 ——
        # 报告本身"看着正常"，没人会发现。这里把自动取账号名钉死。
        ok &= check('★大标题自动取数据里的账号名（不用手传 --title）',
                    '# 测试号 —— 公众号文章链接清单' in md, md.splitlines()[:1])
        r = subprocess.run([PY, os.path.join(HERE, 'make_link_report.py'),
                            '--index', idx, '--links', lnk, '-o', out,
                            '--title', '自定义标题'],
                           capture_output=True, text=True)
        md_t = open(out, encoding='utf-8').read() if os.path.exists(out) else ''
        ok &= check('--title 显式传入时能覆盖自动标题',
                    r.returncode == 0 and md_t.startswith('# 自定义标题'),
                    md_t.splitlines()[:1])

        # 索引里有、链接里没有 → 必须明确列出来，不能静默丢
        with open(idx, 'a', encoding='utf-8') as fh:
            fh.write(json.dumps({'date': '2月1日', 'title': '没采到的一篇', 'reads': '9'},
                                ensure_ascii=False) + '\n')
        r = subprocess.run([PY, os.path.join(HERE, 'make_link_report.py'),
                            '--index', idx, '--links', lnk, '-o', out],
                           capture_output=True, text=True)
        md = open(out, encoding='utf-8').read() if os.path.exists(out) else ''
        ok &= check('未采到的条目被明确列出（不静默丢弃）',
                    '未采到链接' in md and '没采到的一篇' in md)
        ok &= check('未采到时给出补救办法（--skip 重试）', '--skip' in md)

        # 链接文件里一条 url 都没有 → 必须报错退出，不能产出空报告
        with open(lnk, 'w', encoding='utf-8') as fh:
            fh.write('')
        r = subprocess.run([PY, os.path.join(HERE, 'make_link_report.py'),
                            '--index', idx, '--links', lnk, '-o', out],
                           capture_output=True, text=True)
        ok &= check('链接为空时返回非 0 且说明原因（不是静默产出空报告）',
                    r.returncode != 0 and '没有任何带 url' in (r.stderr + r.stdout))

        # 索引缺失 → 会**静默**退回采集顺序、跨度也没了。必须出声。
        # 真 bug 回顾：make_link_report 的 load_jsonl 对不存在的文件返回空列表，
        # 于是 --index 指错了路径也照跑不误，报告"看着正常"其实排序是错的；
        # run-report.bat 当时也只守卫 links.jsonl、没守卫 articles.jsonl。
        with open(lnk, 'w', encoding='utf-8') as fh:
            fh.write(json.dumps({'url': 'https://mp.weixin.qq.com/s/x', 'title': '唯一一篇',
                                 'date': '', 'account': '测试号', 'gh_id': ''},
                                ensure_ascii=False) + '\n')
        r = subprocess.run([PY, os.path.join(HERE, 'make_link_report.py'),
                            '--index', os.path.join(td, '根本不存在.jsonl'),
                            '--links', lnk, '-o', out],
                           capture_output=True, text=True)
        ok &= check('★索引文件不存在时在 stderr 明确警告（不静默降级）',
                    '索引' in (r.stderr or ''), (r.stderr or '')[:100])
        md_now = open(out, encoding='utf-8').read() if os.path.exists(out) else ''
        ok &= check('★警告与实际一致：这种报告里确实没有「时间跨度」',
                    '时间跨度' not in md_now)

        # ---- HTML 版：内容与 md 同源，默认一起产出 ----
        # 用户要的是"同样的内容、另一个格式"。两份必须由**同一份内存数据**渲染，
        # 否则迟早漂移（一个改了另一个忘改）。这里把「同源」「转义」「可关」钉死。
        nasty = '<b>加粗？</b> & "引号" | 竖线'
        h_idx = os.path.join(td, 'h_idx.jsonl')
        h_lnk = os.path.join(td, 'h_lnk.jsonl')
        h_url = 'https://mp.weixin.qq.com/s/z3e4HQD0Uf_Q76N8s-_7WQ'
        with open(h_idx, 'w', encoding='utf-8') as fh:
            fh.write(json.dumps({'date': '4月23日', 'title': nasty, 'reads': '7'},
                                ensure_ascii=False) + '\n')
        with open(h_lnk, 'w', encoding='utf-8') as fh:
            fh.write(json.dumps({'url': h_url, 'title': nasty, 'date': '4月23日',
                                 'account': '测试号', 'gh_id': 'gh_x'},
                                ensure_ascii=False) + '\n')
        h_md = os.path.join(td, 'h.md')
        h_html = os.path.join(td, 'h.html')

        r = subprocess.run([PY, os.path.join(HERE, 'make_link_report.py'),
                            '--index', h_idx, '--links', h_lnk, '-o', h_md],
                           capture_output=True, text=True)
        ok &= check('默认同时产出 .md 与 .html（不用额外加参数）',
                    r.returncode == 0 and os.path.exists(h_md) and os.path.exists(h_html),
                    f'md={os.path.exists(h_md)} html={os.path.exists(h_html)}')
        H = open(h_html, encoding='utf-8').read() if os.path.exists(h_html) else ''
        ok &= check('html 是能独立打开的完整页面（doctype + <title>）',
                    H.startswith('<!DOCTYPE html>') and '<title>' in H)
        ok &= check('html 自带样式、不依赖外部 css/js（可离线双击打开）',
                    '<style>' in H and '<link' not in H and ' src=' not in H)
        h_urls = set(re.findall(r'href="(https?://[^"]+)"', H))
        m_urls = set(re.findall(r'\]\((https?://[^)]+)\)',
                                open(h_md, encoding='utf-8').read()))
        ok &= check('★html 与 md 的链接集合完全一致（同一份数据渲染，不会漂移）',
                    bool(m_urls) and m_urls == h_urls,
                    f'md {len(m_urls)} 个 / html {len(h_urls)} 个')
        ok &= check('★html 里标题被转义（< 变 &lt;，标题冲不坏页面结构）',
                    '&lt;b&gt;加粗？&lt;/b&gt;' in H and '<b>加粗？</b>' not in H)
        ok &= check('html 里文章标题本身可点（比 md 多的一点便利）',
                    bool(m_urls) and f'href="{sorted(m_urls)[0]}"' in H)
        ok &= check('html 带「复制全部链接」按钮与纯链接区',
                    'id="cp"' in H and 'id="rawtext"' in H)
        ok &= check('html 里账号名出现在大标题里',
                    '测试号 —— 公众号文章链接清单' in H)

        if os.path.exists(h_html):
            os.remove(h_html)
        r = subprocess.run([PY, os.path.join(HERE, 'make_link_report.py'),
                            '--index', h_idx, '--links', h_lnk, '-o', h_md, '--no-html'],
                           capture_output=True, text=True)
        ok &= check('--no-html 时确实不生成 .html',
                    r.returncode == 0 and not os.path.exists(h_html))

        # 同名标题：必须**按索引顺序逐一消费**，不能按标题去重。
        # 真缺陷回顾：早先 `gather` 用 `by_title` 字典 + `used` 集合配对，
        # 于是**同名文章只保留第一篇、其余静默丢掉** —— 实测某号一批 30 篇里
        # 「上海志愿服务动态」重复 6 次，报告被写成 24 行，而且因为"标题在字典里
        # 存在"，这些丢掉的文章**不会**进「未采到链接」一节。报告看着完全正常。
        d_idx = os.path.join(td, 'dup_idx.jsonl')
        d_lnk = os.path.join(td, 'dup_lnk.jsonl')
        d_md = os.path.join(td, 'dup.md')
        titles = ['同一标题', '另一个标题', '同一标题', '同一标题']
        tags = ['A', 'B', 'C', 'D']
        with open(d_idx, 'w', encoding='utf-8') as fh:
            for t, s in zip(titles, tags):
                fh.write(json.dumps({'date': f'{s}日', 'title': t, 'reads': '1'},
                                    ensure_ascii=False) + '\n')
        with open(d_lnk, 'w', encoding='utf-8') as fh:
            for t, s in zip(titles, tags):
                fh.write(json.dumps({'url': f'https://mp.weixin.qq.com/s/{s}',
                                     'title': t, 'date': f'{s}日',
                                     'account': '测试号', 'gh_id': 'gh_t'},
                                    ensure_ascii=False) + '\n')
        r = subprocess.run([PY, os.path.join(HERE, 'make_link_report.py'),
                            '--index', d_idx, '--links', d_lnk, '-o', d_md, '--no-html'],
                           capture_output=True, text=True)
        dmd = open(d_md, encoding='utf-8').read() if os.path.exists(d_md) else ''
        n_rows = len([l for l in dmd.splitlines() if l.startswith('| ') and '打开' in l])
        ok &= check('★同名标题不被去重：4 条索引配 4 条链接 → 表里就是 4 行',
                    r.returncode == 0 and n_rows == 4, f'{n_rows} 行')
        ok &= check('★同名的每一条都配到了自己的链接（按序消费，不错位）',
                    all(f'/s/{s})' in dmd for s in tags))
        ok &= check('报告头里明确标出「同名标题」条数（不静默）', '同名标题' in dmd)
    return ok


def test_doctor():
    """`--doctor` 的环境判定规则。

    为什么值得单测：预检是**唯一能在花掉 11 分钟和抢前台之前**拦住错误的一步。
    判定错了有两种代价，都很贵：
      - 假通过 → 用户白等 11 分钟，还采到半截数据；
      - 假拦截 → 用户以为环境坏了，其实只是没滚到底。

    其中第 8 条是**回归「20 篇误判」**：上一轮我把「列表没加载完」错误地
    写成了「脚本只能采 20 篇」。这里把正确措辞钉死。
    """
    print('\n[10] --doctor 环境预检判定')
    import profile_parse as pp

    ok = True
    ready = {
        'pywinauto_ok': True, 'python': 'python.exe',
        'wechat_pids': [(2524, 'WeChat.exe')],
        'profile_windows': 1, 'profile_doc_count': 1,
        'account': '某公众号', 'article_count': 103,
        'loading_more': False, 'appex_windows': 0,
        'browser_windows': [{'cls': 'Chrome_WidgetWin_1', 'title': 'X - Google Chrome'}],
    }
    items = pp.judge_environment(ready)
    ok &= check('环境齐全 → 结论 ready', pp.verdict(items) == 'ready'
                and all(i['level'] == 'ok' for i in items),
                f"{pp.verdict(items)} / levels={[i['level'] for i in items]}")

    okay = pp.judge_environment(dict(ready, pywinauto_ok=False))
    ok &= check('缺 pywinauto → blocked，并给出安装办法',
                pp.verdict(okay) == 'blocked'
                and any('pywinauto' in i['text'] and 'pip install' in i['text'] for i in okay))

    ok &= check('没有微信进程 → blocked',
                pp.verdict(pp.judge_environment(dict(ready, wechat_pids=[]))) == 'blocked')

    only_appex = pp.judge_environment(dict(ready, wechat_pids=[(1, 'WeChatAppEx.exe')]))
    ok &= check('只有内置浏览器进程、没有 WeChat.exe → blocked（不能当成微信在跑）',
                pp.verdict(only_appex) == 'blocked', str([i['text'] for i in only_appex])[:90])

    noprof = pp.judge_environment(dict(ready, profile_windows=0))
    ok &= check('没有公众号主页窗口 → blocked', pp.verdict(noprof) == 'blocked')
    ok &= check('并且点明「历史消息」窗口已下线（免得用户去找错窗口）',
                any('历史消息' in i['text'] and '下线' in i['text'] for i in noprof))

    multi = pp.judge_environment(dict(ready, profile_windows=3))
    ok &= check('开了多个公众号窗口 → attention（提醒可能读错号）',
                pp.verdict(multi) == 'attention'
                and any('第 1 个' in i['text'] for i in multi))

    zero = pp.judge_environment(dict(ready, article_count=0))
    ok &= check('解析出 0 篇 → blocked，且声明「不要据此判断该号没有文章」',
                pp.verdict(zero) == 'blocked'
                and any('不要' in i['text'] and '没有文章' in i['text'] for i in zero))

    # ★ 回归「20 篇误判」
    few = pp.judge_environment(dict(ready, article_count=20, loading_more=True))
    ok &= check('★只解析到 20 篇且挂着「正在加载...」→ 提示手动滚到底，不是脚本上限',
                pp.verdict(few) == 'attention'
                and any('滚到底' in i['text'] for i in few)
                and any('不是' in i['text'] and '上限' in i['text'] for i in few),
                str([i['text'] for i in few if i['level'] == 'warn'])[:110])

    appex = pp.judge_environment(dict(ready, appex_windows=2))
    ok &= check('发现内置浏览器窗口 → 提示「使用系统默认浏览器打开网页」没开',
                any('使用系统默认浏览器打开网页' in i['text'] for i in appex)
                and pp.verdict(appex) == 'attention')

    nobrowser = pp.judge_environment(dict(ready, browser_windows=[]))
    ok &= check('没有已开浏览器窗口 → 只是提醒（并提示 Firefox 不适用），不拦',
                pp.verdict(nobrowser) == 'attention'
                and any('Firefox' in i['text'] for i in nobrowser))

    ok &= check('空 facts → unknown，不能算「全部通过」',
                pp.verdict(pp.judge_environment({})) == 'unknown')
    ok &= check('facts 缺键不崩（只给一个键也能判）',
                pp.verdict(pp.judge_environment({'pywinauto_ok': True})) == 'ready')

    # CLI 契约：--doctor 真的挂上了（纯文本检查，不需要 pywinauto）
    src = open(os.path.join(HERE, 'collect_via_browser.py'), encoding='utf-8').read()
    ok &= check("CLI 里注册了 --doctor 且实现了 cmd_doctor()",
                "'--doctor'" in src and 'def cmd_doctor()' in src and 'judge_environment' in src)

    # 产物字段清单：docstring 里写的键，必须和真正写盘的键一字不差。
    # 真缺陷回顾：docstring 写的是 9 个键，实际写盘 11 个 —— 漏了 likes / page_title，
    # 而 links.jsonl 是下游（报告、分析器）的唯一输入，文档少两个键就是少两条线索。
    # 两边都从源码里抽，不写死，这样以后加字段忘了同步文档也会立刻报错。
    m_write = re.search(r"store\[key\]\s*=\s*\{(.*?)\n\s*\}", src, re.S)
    m_doc = re.search(r"links\.jsonl\s+每行\s*\{(.+?)\}", src, re.S)
    written = set(re.findall(r"'(\w+)':\s", m_write.group(1))) if m_write else set()
    doc = set(re.findall(r"[A-Za-z_]\w*", m_doc.group(1))) if m_doc else set()
    ok &= check('links.jsonl 的文档字段清单 == 实际写盘字段',
                bool(written) and written == doc,
                f'写盘 {len(written)} 个 / 文档 {len(doc)} 个；'
                f'差异：{sorted(written ^ doc)}')

    # 分发到别人机器上时，最先卡住的是依赖。清单必须存在、且一条命令能装齐。
    root = os.path.dirname(HERE)
    req = os.path.join(root, 'requirements.txt')
    ok &= check('技能根目录有 requirements.txt', os.path.exists(req))
    if os.path.exists(req):
        txt = open(req, encoding='utf-8').read()
        ok &= check('requirements.txt 列了 pywinauto（UIA 脚本的必需依赖）',
                    'pywinauto' in txt)
        ok &= check('requirements.txt 交代了可选依赖（mitmproxy / brotli）',
                    'mitmproxy' in txt and 'brotli' in txt)
        deps = [l.split('#')[0].strip() for l in txt.splitlines()]
        deps = [d for d in deps if d]
        ok &= check('生效依赖只有 pywinauto 一条（其余都是注释里的可选项）',
                    deps == ['pywinauto>=0.6.8'], str(deps))

        # 不靠人记：扫一遍源码，凡是顶层 import pywinauto 的脚本，
        # 都必须出现在上面那份「必需」注释清单里。
        # （2026-09-22 真漏过：新增折叠布局采集器后没登记，而当时没有任何检查会出声。）
        scdir = os.path.join(root, 'scripts')
        need_pyw = []
        for fn in sorted(os.listdir(scdir)):
            if not fn.endswith('.py'):
                continue
            body = open(os.path.join(scdir, fn), encoding='utf-8').read()
            if re.search(r'^\s*(import pywinauto|from pywinauto)', body, re.M):
                need_pyw.append(fn)
        miss = [f for f in need_pyw if f not in txt]
        ok &= check(f'★requirements.txt 的「必需」清单穷举了全部 {len(need_pyw)} 个用 pywinauto 的脚本',
                    bool(need_pyw) and not miss, f'漏登记：{miss}')

    # SKILL.md 是「渐进披露第 2 层」，正文超长就该往 references/ 搬。
    # 加这条是因为实测超过一次（折叠分组那轮把正文写到 5408 中文字），
    # 而当时**没有任何检查会出声** —— 正是本项目最忌讳的「静默降级」。
    sp = os.path.join(os.path.dirname(HERE), 'SKILL.md')
    if os.path.exists(sp):
        txt = open(sp, encoding='utf-8').read()
        fm = re.match(r'(?s)^---.*?---', txt)
        body = txt[fm.end():] if fm else txt        # frontmatter 是触发规格，不计
        n = len(re.findall(r'[\u4e00-\u9fff]', body))
        ok &= check('★SKILL.md 正文中文字数 < 5000（超了就往 references/ 搬，别硬塞）',
                    n < 5000, f'{n} 字')

    # 不要再把 WorkBuddy 运行时的 venv 说成"技能自带"——它不在分发包里，会误导外部用户
    fpy = open(os.path.join(HERE, '_findpy.bat'), encoding='utf-8').read()
    ok &= check('启动器不再把那个 venv 说成「技能自带」（它不在包内）',
                '技能自带的托管' not in fpy)
    ok &= check('启动器缺依赖时会给出 pip 安装命令',
                'pip install pywinauto' in fpy)
    return ok


def test_grouped_layout():
    """「折叠分组」布局的解析（`profile_parse.parse_grouped` / `absolutize_date`）。

    为什么值得单测：在这个布局下**文章标题不在无障碍树里**，只能从正文纯文本里抠，
    而「标题 ↔ 可点元素」的配对只能靠**位置**。解析错一位，产出的就是一份
    「标题与链接错位」的报告 —— 而报告本身看不出任何异常。
    所以把真实正文文本的形状钉死在这里（样本照抄实测的 `Document.Value`）。

    ⚠️ 这些纯函数住在 `profile_parse`（不是 `collect_grouped_layout`）：
    后者顶层导入 pywinauto，一旦被自检 import 就会在**没有 pywinauto 的环境**
    里 `sys.exit`，把整个自检进程顶掉。这个坑实测踩过。
    """
    print('\n[11] 折叠分组布局：从正文抠标题 + 相对日期换算')
    ok = True
    try:
        import profile_parse as G
    except ImportError as e:
        ok &= check('能导入 profile_parse（纯函数，不需要 pywinauto）',
                    False, f'{type(e).__name__}: {e}')
        return ok

    # 实测样本的骨架：账号头部 → 首个日期组头（相对标签「星期四」）→ 3 条 →
    # 「余下 N 篇」→ 新的日期组头 → 2 条。\u2004/\u2005 是微信用的窄空格。
    sample = (
        '  上海志愿者  上海 徐汇  有心皆志愿！欢迎加入 视频号 : 上海志愿者 '
        '2篇原创内容 42个朋友关注  已关注 私信    全部 文章 视频号     '
        '星期四 上海志愿服务动态 阅读\u20067915\u2004\u2005赞\u200622\u2004\u2005   '
        '让急救知识走出医院 阅读\u2006301\u2004\u2005    '
        '温暖申城 阅读\u2006115\u2004\u2005赞\u20061\u2004\u2005    '
        '余下 5 篇  9月10日 中社部首次亮相 阅读\u20061116\u2004\u2005赞\u20068\u2004\u2005   '
        '2026年9-10月菜单 阅读\u2006683\u2004\u2005')
    items = G.parse_grouped(sample)
    ok &= check('解析出 5 条', len(items) == 5, f'{len(items)} 条')
    if len(items) == 5:
        ok &= check('★第 1 条标题从账号头部里切出来（不含「上海志愿者/已关注」）',
                    items[0]['title'] == '上海志愿服务动态', repr(items[0]['title']))
        ok &= check('第 1 条的日期用首个组头「星期四」', items[0]['date'] == '星期四')
        ok &= check('第 2 条标题干净（不带前一条的残留）',
                    items[1]['title'] == '让急救知识走出医院', repr(items[1]['title']))
        ok &= check('阅读数 / 点赞数解析正确',
                    (items[0]['reads'], items[0]['likes']) == ('7915', '22'),
                    f"{items[0]['reads']}/{items[0]['likes']}")
        ok &= check('★没有点赞的条目 likes 是空串（不是 0、不报错）',
                    items[1]['likes'] == '', repr(items[1]['likes']))
        ok &= check('★「余下 N 篇」+ 新日期组头一起被剥掉，第 4 条是「中社部首次亮相」',
                    items[3]['title'] == '中社部首次亮相', repr(items[3]['title']))
        ok &= check('第 4 条的日期换成新组头「9月10日」', items[3]['date'] == '9月10日')
        ok &= check('标题里带年份数字（2026年9-10月菜单）不会被误当成日期组头',
                    items[4]['title'] == '2026年9-10月菜单', repr(items[4]['title']))
        ok &= check('第 5 条沿用本组日期组头', items[4]['date'] == '9月10日')
        ok &= check('没有空标题（空标题说明配对错位）',
                    all(it['title'].strip() for it in items))

    from datetime import datetime
    today = datetime(2026, 9, 22)          # 星期二
    ok &= check('★「星期四」换算成绝对日期（今天之前最近的那个周四）',
                G.absolutize_date('星期四', today)[0] == '9月17日',
                G.absolutize_date('星期四', today)[0])
    ok &= check('「昨天」换算成绝对日期', G.absolutize_date('昨天', today)[0] == '9月21日')
    ok &= check('原来的相对标签留在 date_raw 里（可回溯）',
                G.absolutize_date('星期四', today)[1] == '星期四')
    ok &= check('已经是绝对日期的标签原样返回、不做换算',
                G.absolutize_date('9月10日', today)[0] == '9月10日'
                and G.absolutize_date('2025年12月31日', today)[0] == '2025年12月31日')
    ok &= check('跨年的「星期X」会带上年份（不会静默算成本年）',
                G.absolutize_date('星期一', datetime(2026, 1, 2))[0] == '2025年12月29日',
                G.absolutize_date('星期一', datetime(2026, 1, 2))[0])

    # --- 漏采闸门 ---------------------------------------------------------
    # 折叠组的文章标题**不在页面正文里**：不展开就采 = 静默漏采，报告却看着正常。
    # 所以「还剩多少折叠」必须能被数出来、并且采集器必须接线报警。
    mk, hid = G.folded_markers(sample, ['余下 5 篇'])
    ok &= check('★数得出「余下 N 篇」标记个数与累计折叠篇数', (mk, hid) == (1, 5), f'{mk}/{hid}')
    mk2, hid2 = G.folded_markers(sample, [])
    ok &= check('★标记只在树里、折叠数只在正文里，两条路径各自独立可用',
                (mk2, hid2) == (0, 5), f'{mk2}/{hid2}')
    ok &= check('数折叠数时不受正文里其他数字干扰（「2026年9-10月」不算）',
                G.folded_markers('2026年9-10月菜单 余下 2 篇', [])[1] == 2)

    items4 = [{'date': d} for d in ('9月17日', '9月17日', '9月10日', '9月10日')]
    ok &= check('★截断落在一天中间时能定位到是哪天、还剩几篇',
                G.truncation_info(items4, 3) == ('9月10日', 1), repr(G.truncation_info(items4, 3)))
    ok &= check('截断正好落在某天末尾时不告警（组边界是对齐的）',
                G.truncation_info(items4, 2) is None)
    ok &= check('没截断（取满）不告警', G.truncation_info(items4, 4) is None)
    ok &= check('一条都没取不告警', G.truncation_info(items4, 0) is None)

    # --- 索引 ↔ 链接 必须同条数（分批续采的真缺陷）---------------------------
    # 索引原先只写**本次**的 records：先采 30 篇、再 `--skip 30` 补 40 篇，
    # 就得到「索引 40 条 / 链接 70 条」—— 报告照样生成，只是排序错了、
    # 前面那些没有日期，**看不出任何异常**。改成按 store 顺序出索引。
    st = {
        'k1': {'date': '9月17日', 'title': 'A', 'reads': '7915', 'likes': '22',
               'account': '某号', 'gh_id': 'gh_x'},
        'k2': {'date': '9月17日', 'title': 'B', 'reads': '302', 'likes': None,
               'account': '某号', 'gh_id': 'gh_x'},
        'k3': {'date': '9月1日', 'title': 'C', 'reads': '10', 'likes': 0,
               'account': '某号', 'gh_id': 'gh_x'},
    }
    rows_idx = G.index_rows_from_store(st, ['k1', 'k2', 'k3'])
    ok &= check('★索引按 store 顺序生成（== 页面顺序，不是本次 records 的顺序）',
                [r['title'] for r in rows_idx] == ['A', 'B', 'C'],
                repr([r['title'] for r in rows_idx]))
    ok &= check('★索引条数 == 已采总条数（分批续采时不会只剩最后一批）',
                len(rows_idx) == len(st) == 3)
    ok &= check('索引字段清单与原来一致（date/title/reads/likes/account/gh_id）',
                list(rows_idx[0].keys())
                == ['date', 'title', 'reads', 'likes', 'account', 'gh_id'],
                repr(list(rows_idx[0].keys())))
    ok &= check('★likes 为 None 写成空串、为 0 保留 0（不把「0 赞」吃成空）',
                rows_idx[1]['likes'] == '' and rows_idx[2]['likes'] == 0,
                f"{rows_idx[1]['likes']!r}/{rows_idx[2]['likes']!r}")
    ok &= check('order 里有 store 不存在的 key 时不抛异常（容错）',
                G.index_rows_from_store({'k1': st['k1']}, ['k1', 'nope'])[1]['title'] == '')

    src = open(os.path.join(HERE, 'collect_grouped_layout.py'), encoding='utf-8').read()
    ok &= check('★采集器默认就展开（逃生舱是 --no-expand，而不是可选的 --expand）',
                'if not args.no_expand:' in src)
    ok &= check('★采集器接线了漏采闸门 folded_markers()', 'report_folded(' in src)
    ok &= check('★采集器接线了截断告警 truncation_info()', 'truncation_info(' in src)
    ok &= check('★采集器写索引走 index_rows_from_store（不再只写本次 records）',
                'index_rows_from_store(' in src and 'for it in records:' not in src)
    m = re.search(r'if args\.expand:(.*?)\n    if not args\.no_expand:', src, re.S)
    blk = m.group(1) if m else ''
    ok &= check('★--expand 仍是「展开完就退出」，不会突然开始采几百篇（向后兼容）',
                'return 0' in blk and 'enumerate_grouped' not in blk)
    return ok


if __name__ == '__main__':
    results = [test_clipboard_helpers(), test_analyzer(), test_saz(),
               test_clipboard_loop(), test_addon_syntax(), test_collect_contract(),
               test_profile_parse(), test_browser_route(), test_report_join(),
               test_doctor(), test_grouped_layout()]
    print('\n' + ('全部通过' if all(results) else '存在失败项'))
    sys.exit(0 if all(results) else 1)
