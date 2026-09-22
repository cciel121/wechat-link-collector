#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""抓包结果分析器：从抓包文件里拎出所有微信公众号文章链接，
并指出哪个请求是「列表接口」。

支持四种输入，自动识别：
    .jsonl  mitmproxy addon 的产物（抓包记录，字段最全）
    .jsonl  clipboard_watch.py 的产物（链接清单，按字段形态自动区分）
    .har    Fiddler Everywhere / Charles / Chrome 导出
    .saz    Fiddler Classic 原生会话归档（File > Save > All Sessions）

用法:
    python analyze_capture.py session.saz
    python analyze_capture.py mp_capture.jsonl
    python analyze_capture.py session.har -o ./report

产出（默认写到 ./report/）:
    report/links.txt        去重后的文章链接，一行一个，可直接喂 fetch.py
    report/links.jsonl      带来源请求等元信息
    report/analysis.md      接口分布 + 列表接口候选 + 结论

无第三方依赖。brotli 压缩的响应体需要额外装 brotli，没装会跳过而不是报错。
"""

import argparse
import json
import os
import re
import sys
from collections import Counter, OrderedDict
from urllib.parse import urlparse, parse_qs, unquote

ARTICLE_RE = re.compile(r'https?://mp\.weixin\.qq\.com/s[^\s"\'\\<>]{4,}')

# 这些路径关键词历史上属于"枚举文章列表"的接口
LIST_HINTS = ('getmsg', 'profile_ext', 'appmsg', 'appmsgpublish', 'articles', 'history', 'list')

# 列表接口通常必须带的参数
KEY_PARAMS = ('__biz', 'mid', 'idx', 'key', 'pass_ticket', 'uin', 'wxtoken', 'offset', 'count', 'scene')


def unescape_url(u):
    return u.replace('\\x26', '&').replace('\\u0026', '&').replace('&amp;', '&').replace('\\/', '/')


def norm_article_key(url):
    """同一篇文章的签名链与短链归一化，避免重复。"""
    url = unescape_url(url)
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    biz = (qs.get('__biz') or [''])[0]
    mid = (qs.get('mid') or [''])[0]
    idx = (qs.get('idx') or [''])[0]
    if biz and mid:
        return f'{biz}:{mid}:{idx or "1"}'
    path = parsed.path.rstrip('/')
    if path.startswith('/s/') and len(path) > 3:
        return 'short:' + path[3:]
    return 'raw:' + url


# ---------- 载入 ----------

def _iter_json_objects(path):
    """逐行读 jsonl，跳过坏行。"""
    with open(path, encoding='utf-8') as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def load_link_jsonl(path):
    """clipboard_watch.py 的产物。

    注意这份 jsonl 里装的是**文章链接清单**，不是抓包请求记录。
    两者字段长得像（都有 url），但语义完全不同——早先的版本会把这种输入
    当成空壳请求解析，最后报告"提取到 0 条链接"却静默退出，属于最坏的失败模式。
    这里显式区分，避免误判。
    """
    out = []
    skipped_non_http = 0
    for r in _iter_json_objects(path):
        url = unescape_url(r.get('url') or '')
        if not url.startswith('http'):
            # 不静默跳过。本工具只整理公众号文章链接，非 http(s) 的条目要如实
            # 报出来，否则会出现「文件里明明有 N 条、却报载入 0 条」的假象
            # —— 这是本项目反复出现的失败模式：看着像没数据，其实是解析器不认。
            skipped_non_http += 1
            continue
        parsed = urlparse(url)
        out.append({
            'source': 'linklist',
            'time': r.get('captured_at', ''),
            'method': 'CLIP',
            'host': parsed.netloc,
            'path': parsed.path,
            'url': url,
            'query_keys': sorted(set(parse_qs(parsed.query).keys())),
            'status': None,
            'body': None,
            'embedded_links': [],
            'body_len': None,
        })
    if skipped_non_http:
        print(f'注意：链接清单里 {skipped_non_http} 条不是 http(s) 链接，已跳过'
              f'（本工具只整理公众号文章链接）。', file=sys.stderr)
    return out


def load_jsonl(path):
    """mitmproxy addon 的产物（真的抓包请求记录）。"""
    records = list(_iter_json_objects(path))
    out = []
    for r in records:
        out.append({
            'source': 'mitm',
            'time': r.get('time', ''),
            'method': r.get('method', ''),
            'host': r.get('host', ''),
            'path': r.get('path', ''),
            'url': r.get('url', ''),
            'query_keys': r.get('query_keys') or [],
            'status': r.get('status'),
            'body': None,
            'embedded_links': r.get('embedded_links') or [],
            'body_len': r.get('body_len'),
        })
    return out


def load_har(path):
    """Fiddler Everywhere / Charles / Chrome 导出的 HAR。"""
    with open(path, encoding='utf-8') as fh:
        har = json.load(fh)
    entries = (har.get('log') or {}).get('entries') or []
    out = []
    for e in entries:
        req = e.get('request') or {}
        resp = e.get('response') or {}
        url = req.get('url') or ''
        parsed = urlparse(url)
        content = resp.get('content') or {}
        text = None
        if content.get('text') and not content.get('encoding') == 'base64':
            ctype = (content.get('mimeType') or '').lower()
            if any(t in ctype for t in ('json', 'javascript', 'text', 'xml', 'html')):
                text = content['text']
        qkeys = [q.get('name') for q in (req.get('queryString') or []) if q.get('name')]
        out.append({
            'source': 'har',
            'time': (e.get('startedDateTime') or '')[:19].replace('T', ' '),
            'method': req.get('method', ''),
            'host': parsed.netloc,
            'path': parsed.path,
            'url': url,
            'query_keys': sorted(set(qkeys)),
            'status': resp.get('status'),
            'body': text,
            'embedded_links': sorted(set(ARTICLE_RE.findall(unescape_url(text)))) if text else [],
            'body_len': len(text) if text else None,
        })
    return out


def _decode_body(raw, content_encoding, mime):
    """SAZ 里的响应体可能是压缩的，按需解压后再判断是否文本。"""
    if not raw:
        return None
    enc = (content_encoding or '').lower()
    data = raw
    try:
        if 'gzip' in enc:
            import gzip
            data = gzip.decompress(raw)
        elif 'deflate' in enc:
            import zlib
            data = zlib.decompress(raw, -15)
        elif 'br' in enc:
            import brotli  # 可选依赖，没装就放弃
            data = brotli.decompress(raw)
    except Exception:
        data = raw

    if not any(t in (mime or '') for t in ('json', 'javascript', 'text', 'xml', 'html', 'form-urlencoded')):
        return None
    try:
        return data.decode('utf-8')
    except UnicodeDecodeError:
        try:
            return data.decode('gbk')
        except UnicodeDecodeError:
            return None


def _parse_raw_http(blob):
    """把 Fiddler 的 raw HTTP 报文拆成 (起始行, 头部字典, 正文bytes)。"""
    head, sep, body = blob.partition(b'\r\n\r\n')
    if not sep:
        head, sep, body = blob.partition(b'\n\n')
    lines = head.replace(b'\r\n', b'\n').split(b'\n')
    start_line = lines[0].decode('latin-1', 'replace').strip() if lines else ''
    headers = {}
    for line in lines[1:]:
        if b':' not in line:
            continue
        k, _, v = line.decode('latin-1', 'replace').partition(':')
        headers[k.strip().lower()] = v.strip()
    return start_line, headers, body


def load_saz(path):
    """Fiddler Classic 的 .saz 会话归档（本质是个 zip，内含 raw/*_c.txt 与 *_s.txt）。"""
    import zipfile

    out = []
    with zipfile.ZipFile(path) as zf:
        names = zf.namelist()
        clients, servers = {}, {}
        for n in names:
            base = n.replace('\\', '/').rsplit('/', 1)[-1]
            if base.endswith('_c.txt'):
                clients[base[:-len('_c.txt')]] = n
            elif base.endswith('_s.txt'):
                servers[base[:-len('_s.txt')]] = n

        for key in sorted(clients, key=lambda s: (len(s), s)):
            req_blob = zf.read(clients[key])
            req_line, req_headers, _ = _parse_raw_http(req_blob)

            # 起始行形如：GET http://mp.weixin.qq.com/xxx?y=z HTTP/1.1
            parts = req_line.split()
            url = parts[1] if len(parts) >= 2 and parts[1].startswith('http') else ''
            if not url:
                host = req_headers.get('host', '')
                path = parts[1] if len(parts) >= 2 else '/'
                url = f'http://{host}{path}' if host else ''

            status, body = None, None
            if key in servers:
                srv_line, srv_headers, srv_body = _parse_raw_http(zf.read(servers[key]))
                sp = srv_line.split()
                if len(sp) >= 2 and sp[1].isdigit():
                    status = int(sp[1])
                body = _decode_body(srv_body, srv_headers.get('content-encoding'),
                                    srv_headers.get('content-type'))

            parsed = urlparse(url)
            out.append({
                'source': 'saz',
                'time': '',
                'method': parts[0] if parts else '',
                'host': parsed.netloc,
                'path': parsed.path,
                'url': url,
                'query_keys': sorted(set(parse_qs(parsed.query).keys())),
                'status': status,
                'body': body,
                'embedded_links': sorted(set(ARTICLE_RE.findall(unescape_url(body)))) if body else [],
                'body_len': len(body) if body else None,
            })
    return out


def load_any(path):
    lower = path.lower()
    if lower.endswith('.har'):
        return load_har(path)
    if lower.endswith('.saz'):
        return load_saz(path)
    # .jsonl 有两种来源，靠字段形态区分：抓包记录有 host/method，链接清单只有 url/key
    for first in _iter_json_objects(path):
        if 'url' in first and 'host' not in first and 'method' not in first:
            return load_link_jsonl(path)
        break
    return load_jsonl(path)


# ---------- 分析 ----------

def collect_links(records):
    """从 URL 本身 + 响应体里收集文章链接。"""
    found = OrderedDict()

    def add(url, origin):
        url = unescape_url(url).rstrip('.,;')
        if not url.startswith('http'):
            return
        key = norm_article_key(url)
        if key in found:
            found[key]['hits'] += 1
            return
        found[key] = {'url': url, 'key': key, 'origin': origin, 'hits': 1,
                      'first_seen': origin.get('time', '')}

    for r in records:
        # 请求本身就是一个文章页
        if 'mp.weixin.qq.com' in r['host'] and r['path'].startswith('/s'):
            add(r['url'], {'type': 'article_request', 'host': r['host'],
                           'path': r['path'], 'time': r['time']})
        # 响应体里批量内嵌的链接（列表接口的特征）
        for link in r.get('embedded_links') or []:
            add(link, {'type': 'embedded_in_response', 'host': r['host'],
                       'path': r['path'], 'time': r['time']})
    return list(found.values())


def score_list_candidates(records):
    """给每个端点打分，判断它像不像"文章列表接口"。"""
    agg = {}
    for r in records:
        if 'weixin' not in r['host']:
            continue
        sig = f"{r['host']}{r['path']}"
        a = agg.setdefault(sig, {
            'sig': sig, 'host': r['host'], 'path': r['path'],
            'count': 0, 'status': Counter(), 'embedded': 0, 'max_embedded': 0,
            'params': set(), 'sample_query': '', 'first_time': r['time'],
        })
        a['count'] += 1
        a['status'][r.get('status')] += 1
        n = len(r.get('embedded_links') or [])
        a['embedded'] += n
        a['max_embedded'] = max(a['max_embedded'], n)
        a['params'].update(r.get('query_keys') or [])
        if not a['sample_query'] and '?' in r['url']:
            a['sample_query'] = r['url'].split('?', 1)[1][:300]

    scored = []
    for a in agg.values():
        score = 0
        reasons = []
        low = a['path'].lower()
        if any(h in low for h in LIST_HINTS):
            score += 40
            reasons.append('路径含列表接口关键词')
        if a['max_embedded'] >= 2:
            score += min(40, 10 + 5 * a['max_embedded'])
            reasons.append(f"响应体内嵌 {a['max_embedded']} 条文章链接")
        hit = a['params'] & set(KEY_PARAMS)
        if len(hit) >= 3:
            score += 20
            reasons.append('带 ' + '/'.join(sorted(hit)) + ' 等关键参数')
        if a['host'].startswith('channels.'):
            score += 15
            reasons.append('走 channels.weixin.qq.com（PC 微信 4.x 新通道）')
        a['score'] = score
        a['reasons'] = reasons
        scored.append(a)
    scored.sort(key=lambda x: (-x['score'], -x['count']))
    return scored


def write_report(records, links, candidates, outdir, is_linklist=False):
    os.makedirs(outdir, exist_ok=True)

    with open(os.path.join(outdir, 'links.txt'), 'w', encoding='utf-8') as fh:
        for rec in links:
            fh.write(rec['url'] + '\n')
    with open(os.path.join(outdir, 'links.jsonl'), 'w', encoding='utf-8') as fh:
        for rec in links:
            fh.write(json.dumps(rec, ensure_ascii=False) + '\n')

    host_counter = Counter(r['host'] for r in records)
    path_counter = Counter(f"{r['host']}{r['path']}" for r in records)

    lines = ['# 抓包分析结果', '']
    if is_linklist:
        lines.append('> 输入是**链接清单**（`clipboard_watch.py` 剪贴板采集 / '
                     '`collect_wechat_uia.py` UIA 采集），不是抓包记录。')
        lines.append('> 因此这里只做链接整理与去重；「列表接口候选」分析对它不适用。')
        lines.append('')
    lines.append(f'- 载入记录数：**{len(records)}**')
    lines.append(f'- 覆盖域名：{len(host_counter)} 个')
    lines.append(f'- 提取到去重文章链接：**{len(links)}** 条')
    if records and not links:
        lines.append('')
        lines.append('> ⚠️ **载入了记录但一条链接都没提取到。**'
                     '这通常意味着输入格式与解析器不匹配，或抓包里确实没有文章链接。')
        lines.append('> 前者比后者常见——请先确认输入文件来源，再下"没有结果"的结论。')
    lines.append('')

    lines.append('## 域名分布')
    lines.append('')
    lines.append('| 域名 | 请求数 |')
    lines.append('|---|---|')
    for host, cnt in host_counter.most_common(20):
        lines.append(f'| {host} | {cnt} |')
    lines.append('')

    lines.append('## 请求最多的端点')
    lines.append('')
    lines.append('| 端点 | 次数 |')
    lines.append('|---|---|')
    for sig, cnt in path_counter.most_common(20):
        lines.append(f'| {sig} | {cnt} |')
    lines.append('')

    lines.append('## 列表接口候选（按可信度排序）')
    lines.append('')
    if not candidates:
        lines.append('未发现带文章列表特征的请求。')
    for c in candidates[:10]:
        lines.append(f"### {c['sig']}")
        lines.append(f"- 命中分数：{c['score']}")
        lines.append(f"- 出现次数：{c['count']}，状态码分布：{dict(c['status'])}")
        lines.append(f"- 累计内嵌链接：{c['embedded']}")
        lines.append(f"- 判定依据：{'；'.join(c['reasons']) or '无'}")
        if c['sample_query']:
            lines.append(f"- 样例 query：`{c['sample_query']}`")
        lines.append('')

    with open(os.path.join(outdir, 'analysis.md'), 'w', encoding='utf-8') as fh:
        fh.write('\n'.join(lines) + '\n')

    return '\n'.join(lines)


def main():
    ap = argparse.ArgumentParser(description='分析微信抓包结果，提取公众号文章链接')
    ap.add_argument('input', help='输入文件：.jsonl(抓包记录或剪贴板链接清单) / .har / .saz')
    ap.add_argument('-o', '--outdir', default='report', help='输出目录（默认 report）')
    args = ap.parse_args()

    if not os.path.exists(args.input):
        sys.exit(f'找不到文件: {args.input}')

    records = load_any(args.input)
    is_linklist = bool(records) and all(r.get('source') == 'linklist' for r in records)
    print(f"载入 {len(records)} 条{'链接清单' if is_linklist else '请求记录'}", file=sys.stderr)

    links = collect_links(records)
    candidates = score_list_candidates(records)
    report = write_report(records, links, candidates, args.outdir, is_linklist)

    print(report)
    if records and not links:
        print('警告: 载入了记录但未提取到任何链接，请核对输入文件格式。', file=sys.stderr)
    print(f'\n产物目录: {os.path.abspath(args.outdir)}', file=sys.stderr)


if __name__ == '__main__':
    main()
