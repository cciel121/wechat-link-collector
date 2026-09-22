#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把「文章索引」+「链接清单」合成一份可读的报告（Markdown + HTML）。

为什么要用两个文件
------------------
- `articles.jsonl`（`read_profile_list.py` 产出）：按页面顺序 = **新→旧** 排列，
  含 日期 / 标题 / 阅读数。它是**顺序的权威**。
- `links.jsonl`（`collect_via_browser.py` 产出）：键是**采集顺序**。
  重试补采的单篇会被追加到末尾，所以**不能直接用它排表**（第一版就这么错了，
  于是"时间跨度"显示成 `2024年10月15日 → 4月23日`，看着像数据坏了）。

本脚本以索引顺序为准排表，再用标题把链接贴上去；索引里有、链接里没有的会
**明确列在"未采到链接"一节**，不静默丢弃。

用法
----
    python make_link_report.py --index articles.jsonl --links links.jsonl -o 清单.md
    python make_link_report.py --links links.jsonl -o 清单.md
        # 没有索引时按链接顺序（会在 stderr 上明确警告，不静默降级）

**默认同时产出两份内容相同的文件**：

    清单.md     给人看 / 复制
    清单.html   双击就能用浏览器打开、标题和链接都能点、自带样式

不想要 HTML 就加 `--no-html`。两份文件由**同一份内存数据**渲染，不存在谁落后谁。

大标题默认取数据里的**账号名**，自动拼成

    <账号> —— 公众号文章链接清单

不需要手传（早先版本只认 `--title`，于是用启动器生成时标题永远是通用名，
账号名白采了）。要覆盖就用 `--title`。
"""

import argparse
import html
import json
import os
import sys

from profile_parse import dup_title_count     # 纯函数，不引入 pywinauto 依赖


def load_jsonl(path):
    rows = []
    if not path or not os.path.exists(path):
        return rows
    with open(path, encoding='utf-8') as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def gather(links, index):
    """把两个 jsonl 合成一份渲染用的中间数据（md / html 共用同一份）。

    顺序以 `index` 为准；索引里没有、链接里有的补在后面；索引里有、链接里没有的
    单独列出。三种情况都不静默丢弃。

    ⚠️ 配对必须**按序消费**，不能「按标题查字典」。
    公众号里同一个标题会反复出现（实测某号「上海志愿服务动态」在一批 30 篇里
    重复 6 次）。早先版本用 `by_title` 字典 + `used` 集合配对，结果是
    **同名文章只保留第一篇、其余静默丢掉**：实测 30 篇被写成 24 行，
    而且因为"标题在字典里存在"，这些丢掉的文章**不会**出现在「未采到链接」一节 ——
    报告看着完全正常。这正是本项目最忌讳的"看着正常但实际降级"。
    """
    used, ordered, missing = set(), [], []
    if index:
        for r in index:
            t = r.get('title', '')
            hit = None
            for k, lr in enumerate(links):
                if k not in used and lr.get('title', '') == t:
                    hit = k
                    break
            if hit is None:
                missing.append(r)
            else:
                used.add(hit)
                ordered.append(links[hit])
        extra = [lr for k, lr in enumerate(links) if k not in used]
    else:
        ordered, extra, missing = list(links), [], []

    first = ordered[0] if ordered else (links[0] if links else {})
    return {
        'ordered': ordered,
        'extra': extra,
        'missing': missing,
        'account': first.get('account', ''),
        'gh_id': first.get('gh_id', ''),
        'count': len(ordered),
        'indexed': bool(index),
        'index_len': len(index),
        # 同名标题的重复情况：配对是按序消费的，重复时必须让用户看得见
        'dup_titles': dup_title_count([r.get('title', '') for r in index]) if index else 0,
        # 跨度：索引末条（最老）→ 索引首条（最新）
        'span': (f"{index[-1].get('date', '')} → {index[0].get('date', '')}"
                 f"（索引共 {len(index)} 条）") if index else '',
    }


def resolve_title(args, d):
    """报告大标题：优先用 --title；没传就自动取数据里的账号名。

    为什么要自动：启动器/手敲命令时很容易忘传 --title，那样标题会退化成
    通用名 `公众号文章链接清单`，账号名白采了 —— 而报告"看着正常"，没人会发现。
    """
    title = args.title.strip()
    if title:
        return title
    acct = (d['account'] or '').strip()
    return f'{acct} —— 公众号文章链接清单' if acct else '公众号文章链接清单'


def render_md(d, title):
    """Markdown 版。**这份的输出格式被用户当成成品，改动前先跑回归比对。**"""
    L = [f'# {title}', '']
    L.append('采集方式：UI Automation 驱动系统默认浏览器逐篇打开并读取**永久短链**。')
    L.append('')
    L.append(f"- 账号：{d['account']}")
    L.append(f"- 原始 ID：`{d['gh_id']}`")
    L.append(f"- 条数：**{d['count']}**")
    if d.get('dup_titles'):
        L.append(f"- 同名标题：**{d['dup_titles']}** 条 —— 配对按**索引顺序**逐一消费，"
                 '不做标题去重（同名文章都在表里）')
    if d['span']:
        L.append(f"- 时间跨度：{d['span']}")
    L.append('')
    L.append('| # | 日期 | 文章标题 | 阅读 | 链接 |')
    L.append('|---|---|---|---|---|')
    for i, r in enumerate(d['ordered'], 1):
        t = (r.get('title') or '').replace('|', '\\|')
        L.append(f"| {i} | {r.get('date', '')} | {t} | {r.get('reads', '')} "
                 f"| [打开]({r['url']}) |")
    L.append('')
    L.append('## 纯链接')
    L.append('')
    L.append('以下为纯链接（一行一个，可直接复制使用）：')
    L.append('')
    L.append('```text')
    L += [r['url'] for r in d['ordered']]
    L.append('```')
    if d['missing']:
        L += ['', f'## ⚠️ 未采到链接（{len(d["missing"])} 条）', '',
              '索引里有、但链接清单里没有，**不是"该号没有这篇"**：']
        L.append('')
        for r in d['missing']:
            L.append(f"- {r.get('date', '')}  {r.get('title', '')}")
        L.append('')
        L.append('补救：按它在索引里的序号单独重试，例如 `--skip <序号-1> --max 1`。')
    if d['extra']:
        L += ['', f'## 附：索引外的 {len(d["extra"])} 条', '',
              '这些链接拿到了，但在索引里找不到对应标题（可能是标题被改过）：', '']
        for r in d['extra']:
            L.append(f"- {r.get('title', '')}  {r['url']}")
    L.append('')

    return '\n'.join(L)


CSS = """
:root{--fg:#1f2328;--muted:#656d76;--line:#d8dee4;--bg:#fff;--accent:#0969da;--soft:#f6f8fa}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);
 font:15px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif}
main{max-width:1080px;margin:0 auto;padding:40px 24px 64px}
h1{font-size:26px;line-height:1.35;margin:0 0 8px}
h2{font-size:18px;margin:40px 0 12px;padding-top:20px;border-top:1px solid var(--line)}
p.how{color:var(--muted);margin:0 0 18px}
ul.meta{list-style:none;margin:0 0 28px;padding:14px 18px;background:var(--soft);
 border:1px solid var(--line);border-radius:10px;display:flex;flex-wrap:wrap;gap:8px 28px}
ul.meta li{white-space:nowrap}
ul.meta b{color:var(--muted);font-weight:600}
code{background:var(--soft);border:1px solid var(--line);border-radius:5px;
 padding:1px 5px;font-size:.92em;font-family:ui-monospace,SFMono-Regular,Consolas,monospace}
table{width:100%;border-collapse:collapse;font-size:14.5px}
th,td{border-bottom:1px solid var(--line);padding:8px 10px;text-align:left;vertical-align:top}
thead th{position:sticky;top:0;background:var(--bg);border-bottom:2px solid var(--line);
 font-weight:600;white-space:nowrap;z-index:1}
tbody tr:hover{background:var(--soft)}
td.n,th.n{text-align:right;white-space:nowrap;font-variant-numeric:tabular-nums}
th.n{color:var(--fg)}
td.n{color:var(--muted)}
td.idx{color:var(--muted)}
a{color:var(--accent);text-decoration:none}
a:hover{text-decoration:underline}
pre{background:var(--soft);border:1px solid var(--line);border-radius:10px;padding:14px 16px;
 overflow:auto;max-height:420px;
 font:13px/1.65 ui-monospace,SFMono-Regular,Consolas,monospace}
button{font:inherit;padding:6px 14px;border:1px solid var(--line);background:var(--bg);
 border-radius:8px;cursor:pointer;color:var(--fg)}
button:hover{background:var(--soft)}
#cpmsg{color:var(--muted);margin-left:10px;font-size:14px}
ol.miss{margin:8px 0 0;padding-left:24px}
@media print{thead th{position:static}button{display:none}pre{max-height:none}}
"""

JS = """
(function () {
  var btn = document.getElementById('cp');
  if (!btn) return;
  var pre = document.getElementById('rawtext');
  var msg = document.getElementById('cpmsg');
  var NL = String.fromCharCode(10);
  function done(n) {
    msg.textContent = '\u5df2\u590d\u5236 ' + n + ' \u6761';
    setTimeout(function () { msg.textContent = ''; }, 2500);
  }
  function selectAll() {
    var r = document.createRange(); r.selectNodeContents(pre);
    var s = window.getSelection(); s.removeAllRanges(); s.addRange(r);
    msg.textContent = '\u81ea\u52a8\u590d\u5236\u5931\u8d25\uff0c\u5df2\u9009\u4e2d\uff0c\u8bf7\u6309 Ctrl+C';
  }
  function fallback(t) {
    var ta = document.createElement('textarea');
    ta.value = t; ta.style.position = 'fixed'; ta.style.top = '-1000px';
    document.body.appendChild(ta); ta.select();
    var ok = false;
    try { ok = document.execCommand('copy'); } catch (e) { ok = false; }
    document.body.removeChild(ta);
    return ok;
  }
  btn.addEventListener('click', function () {
    var t = pre.textContent.replace(/^\\s+|\\s+$/g, '');
    var n = t ? t.split(NL).length : 0;
    if (navigator.clipboard && window.isSecureContext) {
      navigator.clipboard.writeText(t).then(function () { done(n); }, function () {
        if (fallback(t)) { done(n); } else { selectAll(); }
      });
    } else if (fallback(t)) {
      done(n);
    } else {
      selectAll();
    }
  });
})();
"""


def render_html(d, title):
    """HTML 版：内容与 md 一一对应，自带样式，单文件、无外部依赖。

    标题列在这里**本身就是一个链接**（md 里标题是纯文本、只有「打开」可点）——
    这是 HTML 版唯一比 md 多的一点便利，文字内容完全一致。
    """
    e = html.escape
    h = ['<!DOCTYPE html>', '<html lang="zh-CN">', '<head>',
         '<meta charset="utf-8">',
         '<meta name="viewport" content="width=device-width, initial-scale=1">',
         f'<title>{e(title)}</title>',
         f'<style>{CSS}</style>', '</head>', '<body>', '<main>']

    h.append(f'<h1>{e(title)}</h1>')
    h.append('<p class="how">采集方式：UI Automation 驱动系统默认浏览器逐篇打开并读取'
             '<strong>永久短链</strong>。</p>')
    h.append('<ul class="meta">')
    h.append(f'  <li><b>账号：</b>{e(str(d["account"]))}</li>')
    h.append(f'  <li><b>原始 ID：</b><code>{e(str(d["gh_id"]))}</code></li>')
    h.append(f'  <li><b>条数：</b><strong>{d["count"]}</strong></li>')
    if d.get('dup_titles'):
        h.append(f'  <li><b>同名标题：</b>{d["dup_titles"]} 条 —— '
                 '配对按<b>索引顺序</b>逐一消费，不做标题去重（同名文章都在表里）</li>')
    if d['span']:
        h.append(f'  <li><b>时间跨度：</b>{e(d["span"])}</li>')
    h.append('</ul>')

    h.append('<table>')
    h.append('<thead><tr><th class="n">#</th><th>日期</th><th>文章标题</th>'
             '<th class="n">阅读</th><th>链接</th></tr></thead>')
    h.append('<tbody>')
    for i, r in enumerate(d['ordered'], 1):
        u = e(str(r['url']))
        h.append(f'<tr><td class="n idx">{i}</td>'
                 f'<td>{e(str(r.get("date", "")))}</td>'
                 f'<td><a href="{u}" target="_blank" rel="noopener">'
                 f'{e(str(r.get("title", "")))}</a></td>'
                 f'<td class="n">{e(str(r.get("reads", "")))}</td>'
                 f'<td><a href="{u}" target="_blank" rel="noopener">打开</a></td></tr>')
    h.append('</tbody>')
    h.append('</table>')

    h.append('<h2>纯链接</h2>')
    h.append('<p>以下为纯链接（一行一个，可直接复制使用）：</p>')
    h.append('<p><button id="cp" type="button">复制全部链接</button>'
             '<span id="cpmsg"></span></p>')
    h.append('<pre id="rawtext">'
             + '\n'.join(e(str(r['url'])) for r in d['ordered']) + '</pre>')

    if d['missing']:
        h.append(f'<h2>⚠️ 未采到链接（{len(d["missing"])} 条）</h2>')
        h.append('<p>索引里有、但链接清单里没有，'
                 '<strong>不是“该号没有这篇”</strong>：</p>')
        h.append('<ol class="miss">')
        for r in d['missing']:
            h.append(f'  <li>{e(str(r.get("date", "")))}&nbsp;&nbsp;'
                     f'{e(str(r.get("title", "")))}</li>')
        h.append('</ol>')
        h.append('<p>补救：按它在索引里的序号单独重试，例如 '
                 '<code>--skip &lt;序号-1&gt; --max 1</code>。</p>')

    if d['extra']:
        h.append(f'<h2>附：索引外的 {len(d["extra"])} 条</h2>')
        h.append('<p>这些链接拿到了，但在索引里找不到对应标题（可能是标题被改过）：</p>')
        h.append('<ul>')
        for r in d['extra']:
            u = e(str(r['url']))
            h.append(f'  <li>{e(str(r.get("title", "")))} '
                     f'<a href="{u}" target="_blank" rel="noopener">{u}</a></li>')
        h.append('</ul>')

    h.append('</main>')
    h.append(f'<script>{JS}</script>')
    h.append('</body>')
    h.append('</html>')

    return '\n'.join(h) + '\n'


def main():
    ap = argparse.ArgumentParser(description='合成公众号文章链接报告（Markdown + HTML）')
    ap.add_argument('--index', default='', help='articles.jsonl（索引，决定排序）')
    ap.add_argument('--links', required=True, help='links.jsonl（含 url）')
    ap.add_argument('-o', '--out', default='链接清单.md',
                    help='Markdown 输出路径（同名的 .html 也会一起生成）')
    ap.add_argument('--html', default='',
                    help='HTML 输出路径；默认与 -o 同目录同名、后缀换成 .html')
    ap.add_argument('--no-html', action='store_true', help='只出 .md，不生成 .html')
    ap.add_argument('--title', default='',
                    help='报告大标题；默认自动用数据里的账号名拼（<账号> —— 公众号文章链接清单）')
    args = ap.parse_args()

    links = [r for r in load_jsonl(args.links) if r.get('url')]
    index = load_jsonl(args.index)

    # 索引缺失/为空 → 会静默退回「按采集顺序」排列，跨度也会消失。
    # 这不是崩溃，但结果**看着正常、其实排错了**，所以必须出声。
    if not index:
        why = (f'索引文件读不到内容：{args.index}' if args.index
               else '没给索引文件（--index）')
        print(f'⚠️  {why}\n'
              f'    报告将退回**采集顺序**排列，且不显示「时间跨度」；\n'
              f'    重试补采过的篇目会排在末尾，跨度看着会不对。\n'
              f'    要正确顺序：先跑 read_profile_list.py -o articles.jsonl，'
              f'再用 --index 指过来。', file=sys.stderr)

    if not links:
        sys.exit(f'❌ {args.links} 里没有任何带 url 的记录 —— '
                 f'请先确认采集是否真的成功，不要据此认为"这个号没有文章"。')

    d = gather(links, index)
    title = resolve_title(args, d)

    with open(args.out, 'w', encoding='utf-8') as fh:
        fh.write(render_md(d, title))
    print(f'写出 {args.out}（{d["count"]} 条'
          + (f'，未采到链接 {len(d["missing"])} 条' if d['missing'] else '') + '）')

    if args.no_html:
        return
    html_out = args.html or (os.path.splitext(args.out)[0] + '.html')
    with open(html_out, 'w', encoding='utf-8') as fh:
        fh.write(render_html(d, title))
    print(f'写出 {html_out}（同一内容，双击即可用浏览器打开）')


if __name__ == '__main__':
    main()
