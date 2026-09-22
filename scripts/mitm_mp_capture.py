#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""mitmproxy addon：把微信相关请求自动落盘，你不需要人工看抓包窗口。

为什么用 mitmproxy 而不是 Fiddler/Charles：
  它的产物是纯文本 jsonl，agent 能直接读、直接分析。
  Fiddler/Charles 更适合人眼浏览，导出成 HAR 也能用（见 analyze_capture.py）。

用法:
    # 只抓微信相关域名，输出默认 mp_capture.jsonl
    mitmdump -s mitm_mp_capture.py --allow-hosts 'mp.weixin.qq.com|channels.weixin.qq.com|weixin.sogou.com'

    # 自定义输出与是否存响应体
    set MP_CAPTURE_OUT=D:\\cap\\mp.jsonl
    mitmdump -s mitm_mp_capture.py

环境变量:
    MP_CAPTURE_OUT      输出路径，默认 mp_capture.jsonl
    MP_CAPTURE_BODY     是否保存文本响应体（1/0，默认 1）
    MP_CAPTURE_BODY_MAX 单个响应体上限字节，默认 2_000_000

抓完之后，把 jsonl 交给 analyze_capture.py 分析即可。
"""

import json
import os
import re
import time
from datetime import datetime

from mitmproxy import http

OUT_PATH = os.environ.get('MP_CAPTURE_OUT', 'mp_capture.jsonl')
SAVE_BODY = os.environ.get('MP_CAPTURE_BODY', '1') != '0'
BODY_MAX = int(os.environ.get('MP_CAPTURE_BODY_MAX', '2000000'))

WECHAT_HOSTS = (
    'mp.weixin.qq.com',
    'channels.weixin.qq.com',
    'weixin.sogou.com',
    'mp.weixinbridge.com',
)

TEXTUAL = ('json', 'javascript', 'text', 'xml', 'html', 'x-www-form-urlencoded')

# 响应体里内嵌的文章链接（列表接口的典型特征）
EMBEDDED_LINK_RE = re.compile(r'https?://mp\.weixin\.qq\.com/s[^\s"\'\\<>]{4,}')

# 只看这几个头，避免把整包 cookie 写进文件
KEEP_REQ_HEADERS = ('user-agent', 'referer', 'origin', 'accept', 'content-type')
KEEP_RESP_HEADERS = ('content-type', 'content-length', 'location', 'set-cookie')

_fh = None


def _writer():
    global _fh
    if _fh is None:
        _fh = open(OUT_PATH, 'a', encoding='utf-8')
    return _fh


def _host_of(req) -> str:
    host = (getattr(req, 'host', None) or req.pretty_host or '').lower()
    return host.rsplit(':', 1)[0] if host.count(':') == 1 else host


def _is_wechat(host: str) -> bool:
    host = (host or '').lower()
    return any(host == h or host.endswith('.' + h) for h in WECHAT_HOSTS)


def _pick(headers, keep):
    """只保留白名单头部。set-cookie 只记 cookie 名，不记值（里面有 key/pass_ticket）。"""
    out = {}
    for k in keep:
        try:
            vals = headers.get_all(k)
        except AttributeError:
            v = headers.get(k)
            vals = [v] if v else []
        if not vals:
            continue
        if k == 'set-cookie':
            out[k] = sorted({c.split('=', 1)[0].strip() for c in vals if c})
        else:
            out[k] = vals[0][:400]
    return out


def response(flow: http.HTTPFlow) -> None:
    req = flow.request
    host = _host_of(req)
    if not _is_wechat(host):
        return

    resp = flow.response
    ctype = (resp.headers.get('content-type') or '').lower() if resp else ''
    rec = {
        'time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'method': req.method,
        'host': host,
        'path': req.path.split('?')[0],
        'url': req.pretty_url,
        'query_keys': sorted(set(req.query.keys())),
        'status': resp.status_code if resp else None,
        'req_headers': _pick(req.headers, KEEP_REQ_HEADERS),
        'resp_headers': _pick(resp.headers, KEEP_RESP_HEADERS) if resp else {},
    }

    body = None
    if resp is not None and SAVE_BODY and any(t in ctype for t in TEXTUAL):
        try:
            raw = resp.get_text(strict=False) or ''
        except Exception:
            raw = ''
        if raw:
            body = raw[:BODY_MAX]
            rec['body_len'] = len(raw)
            rec['body_truncated'] = len(raw) > BODY_MAX
            links = sorted(set(EMBEDDED_LINK_RE.findall(raw)))
            if links:
                rec['embedded_links'] = links
                rec['embedded_link_count'] = len(links)
    rec['is_textual'] = any(t in ctype for t in TEXTUAL)

    with _writer() as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + '\n')
        fh.flush()

    # 可疑的"列表接口"打星标到控制台，方便你边点边看
    p = req.path.lower()
    if any(k in p for k in ('getmsg', 'profile_ext', 'appmsg', 'list')):
        n = rec.get('embedded_link_count', 0)
        print(f'[列表候选] {host}{rec["path"]} -> {rec["status"]} 内嵌链接 {n} 条')


def done() -> None:
    global _fh
    if _fh:
        _fh.close()
        _fh = None
    print(f'抓包记录已写入: {os.path.abspath(OUT_PATH)}')
