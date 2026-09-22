#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""诊断微信内置浏览器窗口（WeChatAppEx.exe / Chrome_WidgetWin_0）。

文章被打开后落在哪个窗口、那窗口能不能通过 UIA 读到内容。
关键疑点：WM_GETOBJECT 发到顶层 HWND 可能到不了 renderer，
需要枚举子窗口逐个叫醒（Chrome_RenderWidgetHostHWND 才是真正渲染层）。
"""
import ctypes
import sys
import time
from ctypes import wintypes

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    pass

from pywinauto import Desktop                                          # noqa: E402
from pywinauto.uia_defines import IUIA                                 # noqa: E402

sys.path.insert(0, r'C:\Users\A\.workbuddy\skills\wechat-link-collector\scripts')
from probe_wechat_uia import (CT_DOCUMENT, CT_HYPERLINK, CT_TEXT,       # noqa: E402
                              find_all, flat, prop, P_NAME, P_VALUE, P_RECT)

user32 = ctypes.windll.user32
WM_GETOBJECT, OBJID_CLIENT = 0x003D, 0xFFFFFFFC

ENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)


def cls_of(h):
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(wintypes.HWND(h), buf, 256)
    return buf.value


def text_of(h):
    n = user32.GetWindowTextLengthW(wintypes.HWND(h))
    buf = ctypes.create_unicode_buffer(n + 2)
    user32.GetWindowTextW(wintypes.HWND(h), buf, n + 2)
    return buf.value


def rect_of(h):
    class R(ctypes.Structure):
        _fields_ = [('left', ctypes.c_long), ('top', ctypes.c_long),
                    ('right', ctypes.c_long), ('bottom', ctypes.c_long)]
    r = R()
    user32.GetWindowRect(wintypes.HWND(h), ctypes.byref(r))
    return (r.left, r.top, r.right, r.bottom)


def children_of(h):
    out = []

    def cb(child, _):
        out.append(child)
        return True
    user32.EnumChildWindows(wintypes.HWND(h), ENUMPROC(cb), 0)
    return out


def wake(hwnd, times=3, gap=0.35):
    for _ in range(times):
        user32.SendMessageW(wintypes.HWND(hwnd), WM_GETOBJECT, 0, ctypes.c_long(OBJID_CLIENT))
        time.sleep(gap)


def uia_counts(iuia, hwnd):
    try:
        root = iuia.ElementFromHandle(wintypes.HWND(hwnd))
    except Exception as e:
        return f'ElementFromHandle 失败: {type(e).__name__}: {e}'
    d, _ = find_all(iuia, root, CT_DOCUMENT, timeout=8)
    t, _ = find_all(iuia, root, CT_TEXT, timeout=8)
    h, _ = find_all(iuia, root, CT_HYPERLINK, timeout=8)
    urls = [str(prop(x, P_VALUE) or '') for x in d]
    return (f'Document={len(d)} Text={len(t)} Hyperlink={len(h)}'
            + (f'  URLs={[flat(u, 70) for u in urls]}' if urls else ''))


def main():
    pid_filter = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    iuia = IUIA().iuia

    targets = []
    for w in Desktop(backend='uia').windows():
        try:
            ei = w.element_info
            if pid_filter and ei.process_id != pid_filter:
                continue
            if 'WeChat' in (ei.class_name or '') or 'wechat' in (ei.name or '').lower() \
               or ei.process_id == pid_filter:
                targets.append((w.handle, ei.class_name, ei.name, ei.process_id))
        except Exception:
            pass

    if not targets:
        print('未找到目标窗口。当前窗口清单：')
        for w in Desktop(backend='uia').windows():
            try:
                ei = w.element_info
                print(f'  {flat(ei.name, 40):<42}{ei.class_name:<28}pid={ei.process_id}')
            except Exception:
                pass
        return

    for hwnd, cls, name, pid in targets:
        print(f'\n{"="*72}')
        print(f'顶层窗口 {flat(name, 40)!r} | {cls} | pid={pid} | hwnd=0x{hwnd:X}')
        r = rect_of(hwnd)
        print(f'  rect={r}  宽高=({r[2]-r[0]}x{r[3]-r[1]})  '
              f'visible={bool(user32.IsWindowVisible(wintypes.HWND(hwnd)))}  '
              f'iconic={bool(user32.IsIconic(wintypes.HWND(hwnd)))}')

        kids = children_of(hwnd)
        print(f'  子窗口 {len(kids)} 个:')
        kid_info = []
        for k in kids:
            kr = rect_of(k)
            kid_info.append((k, cls_of(k), text_of(k), kr))
            print(f'    hwnd=0x{k:X} {cls_of(k):<32} {flat(text_of(k), 30):<32} '
                  f'({kr[2]-kr[0]}x{kr[3]-kr[1]})')

        print(f'  叫醒前(顶层): {uia_counts(iuia, hwnd)}')

        # 逐个叫醒：顶层 + 所有子窗口。renderer 层往往在子 HWND 上。
        wake(hwnd)
        for k, kc, _, _ in kid_info:
            if 'Chrome' in kc or 'Render' in kc or 'Widget' in kc or not kc:
                wake(k, times=2, gap=0.3)
        time.sleep(1.6)
        print(f'  叫醒后(顶层): {uia_counts(iuia, hwnd)}')

        for k, kc, kt, kr in kid_info:
            if 'Chrome' not in kc and 'Render' not in kc:
                continue
            n = uia_counts(iuia, k)
            print(f'  子窗口 0x{k:X} ({kc}): {n}')


if __name__ == '__main__':
    main()
