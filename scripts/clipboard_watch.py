#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""剪贴板收集器：微信里「复制链接」出来的文章链接，自动落盘。

这是最省事的方案——不用装证书、不用解 TLS、不用管抓包。
你在微信里逐篇点「复制链接」（文章页右上角「…」→ 复制链接），这个脚本负责自动收下、去重、落盘。

用法:
    python clipboard_watch.py                 # 默认写到 ./links.jsonl
    python clipboard_watch.py -o my.jsonl     # 指定输出
    python clipboard_watch.py --include-all   # 也收非 mp.weixin.qq.com 的文本

停止: Ctrl+C。links.txt（纯链接清单，一行一个，可直接喂 fetch.py）在每收到一条
      链接后就会刷新，所以即使直接关掉终端窗口也已落盘。

Windows 专用（依赖 ctypes 读剪贴板，无第三方依赖）。
"""

import argparse
import atexit
import ctypes
import json
import os
import re
import sys
import time
from ctypes import wintypes
from datetime import datetime
from urllib.parse import urlparse, parse_qs

URL_RE = re.compile(r'https?://mp\.weixin\.qq\.com/[^\s"\'）)】\]]+')
ANY_URL_RE = re.compile(r'https?://[^\s"\'）)】\]]+')


def make_clipboard_reader():
    """构造一个 Windows 剪贴板读取函数。非 Windows 直接退出。"""
    if not sys.platform.startswith('win'):
        sys.exit('本脚本依赖 Windows 剪贴板 API，当前系统不支持。')

    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32

    user32.OpenClipboard.argtypes = [wintypes.HWND]
    user32.OpenClipboard.restype = wintypes.BOOL
    user32.CloseClipboard.restype = wintypes.BOOL
    user32.IsClipboardFormatAvailable.argtypes = [wintypes.UINT]
    user32.IsClipboardFormatAvailable.restype = wintypes.BOOL
    user32.GetClipboardData.argtypes = [wintypes.UINT]
    user32.GetClipboardData.restype = wintypes.HANDLE
    kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalUnlock.restype = wintypes.BOOL

    CF_UNICODETEXT = 13

    def read_clipboard():
        # 剪贴板可能被别的进程占着，重试几次再放弃
        for _ in range(5):
            if user32.OpenClipboard(None):
                break
            time.sleep(0.05)
        else:
            return None
        try:
            if not user32.IsClipboardFormatAvailable(CF_UNICODETEXT):
                return None
            handle = user32.GetClipboardData(CF_UNICODETEXT)
            if not handle:
                return None
            ptr = kernel32.GlobalLock(handle)
            if not ptr:
                return None
            try:
                return ctypes.c_wchar_p(ptr).value
            finally:
                kernel32.GlobalUnlock(handle)
        except OSError:
            return None
        finally:
            user32.CloseClipboard()

    return read_clipboard


def dedup_key(url):
    """同一篇文章有临时签名链和永久短链两种形态，只按 URL 去重会重复收录。

    优先用 (__biz, mid, idx) 三元组当身份；短链形态退化为路径本身。
    """
    try:
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
    except ValueError:
        pass
    return 'raw:' + url.split('#')[0]


def extract_url(text, include_all):
    """从剪贴板文本里挑出第一个候选链接。"""
    if not text:
        return None
    m = URL_RE.search(text)
    if m:
        return m.group(0).rstrip('.,;')
    if include_all:
        m = ANY_URL_RE.search(text)
        if m:
            return m.group(0).rstrip('.,;')
    return None


def main():
    ap = argparse.ArgumentParser(description='监听剪贴板收集微信公众号文章链接')
    ap.add_argument('-o', '--output', default='links.jsonl', help='输出 jsonl 路径（默认 links.jsonl）')
    ap.add_argument('--include-all', action='store_true', help='同时收集非 mp.weixin.qq.com 链接')
    ap.add_argument('--interval', type=float, default=0.3, help='轮询间隔秒数（默认 0.3）')
    args = ap.parse_args()

    read_clipboard = make_clipboard_reader()

    seen = {}
    order = []
    last_raw = None

    print('开始监听剪贴板，去微信里逐篇点「复制链接」。Ctrl+C 结束。', file=sys.stderr)
    print(f'输出文件: {os.path.abspath(args.output)}', file=sys.stderr)

    def flush_links_txt():
        """每次收到新链接都重写一份纯链接清单。

        不要只在 Ctrl+C 时写——直接关终端窗口、任务管理器结束进程都不会走
        KeyboardInterrupt，那样 links.txt 就永远不落地（jsonl 有，txt 没有，
        很容易让人以为"没采到"）。增量重写成本极低，换来的是随时可用的产物。
        """
        if not order:
            return
        txt_path = os.path.splitext(args.output)[0] + '.txt'
        try:
            with open(txt_path, 'w', encoding='utf-8') as fh:
                for rec in order:
                    fh.write(rec['url'] + '\n')
        except OSError as e:
            print(f'[警告] 写 links.txt 失败: {e}', file=sys.stderr)

    atexit.register(flush_links_txt)

    try:
        while True:
            raw = read_clipboard()
            if raw != last_raw:
                last_raw = raw
                url = extract_url(raw, args.include_all)
                if url:
                    key = dedup_key(url)
                    if key in seen:
                        seen[key]['hits'] += 1
                        print(f'[重复] {url[:80]}', file=sys.stderr)
                    else:
                        rec = {
                            'url': url,
                            'key': key,
                            'captured_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                            'hits': 1,
                        }
                        seen[key] = rec
                        order.append(rec)
                        with open(args.output, 'a', encoding='utf-8') as fh:
                            fh.write(json.dumps(rec, ensure_ascii=False) + '\n')
                        print(f'[+{len(order)}] {url[:90]}', file=sys.stderr)
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print('\n停止监听。', file=sys.stderr)
        flush_links_txt()
        print(f'共收集 {len(order)} 条去重链接。', file=sys.stderr)


if __name__ == '__main__':
    main()
