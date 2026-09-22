#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""定判据用的一次性诊断：invoke 一个链接后，两种打开模式下
「列表页 Document 还在不在」到底差在哪。

不参与交付，只是把猜测换成观测。
"""
import argparse
import os
import sys
import time
from ctypes import wintypes

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from pywinauto import Desktop
from pywinauto.uia_defines import IUIA
import collect_wechat_uia as C


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('title')
    args = ap.parse_args()

    desk = Desktop(backend='uia')
    wins = [w for w in desk.windows() if args.title in (w.element_info.name or '')]
    if not wins:
        sys.exit(f'找不到标题含 {args.title!r} 的窗口')
    hwnd = wins[0].handle
    print(f'窗口: {wins[0].element_info.name}')
    print(f'hwnd: 0x{hwnd:X}')

    uia = IUIA()
    root = uia.iuia.ElementFromHandle(wintypes.HWND(hwnd))
    C.wake_accessibility(hwnd)
    time.sleep(1.2)

    def dump(tag):
        allu, _ = C.all_doc_urls(uia.iuia, root)
        m, _ = C.doc_map(uia.iuia, root)
        print(f'\n{tag}')
        print(f'  all_doc_urls（不过滤可见性）: {len(allu)} 个')
        for u in sorted(allu):
            print(f'      …{u[-64:]}')
        print(f'  doc_map（带 Rect 过滤）: {len(m)} 个')
        for k in list(m):
            print(f'      …{k[1][-64:]}')
        return allu

    base = dump('== 初始 ==')
    links, err, raw = C.enum_links(uia.iuia, root)
    print(f'\n枚举：raw={raw} 过滤后={len(links)}  err={err}')
    for it in links:
        print(f'   - {it["name"][:40]}  rect={it["rect"]}')

    if not links:
        sys.exit('没有可点的条目')

    list_url = sorted(base)[0] if base else ''
    print(f'\n记下的「列表页 URL」: …{list_url[-64:]}')

    C.focus_window(hwnd)
    how = C.invoke_element(links[0]['el'])
    print(f'invoke 方式: {how}')
    time.sleep(2.0)

    after = dump('== invoke 之后 ==')
    print('')
    print(f'  列表页 URL 还在树里吗？ {"在 → 新标签页模式" if list_url in after else "不在 → 同窗口导航模式"}')
    m, _ = C.doc_map(uia.iuia, root)
    print(f'  对比一下：带 Rect 过滤时列表页还在吗？ '
          f'{"在" if any(k[1] == list_url for k in m) else "不在（会被误判！）"}')


if __name__ == '__main__':
    main()
