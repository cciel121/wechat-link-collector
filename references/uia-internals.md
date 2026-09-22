# UIA 与窗口机制：29 条踩坑记录

> 这是 `SKILL.md` 的深度补充材料。每一条都是实测踩出来的，**不是推测**。
> 改 `collect_via_browser.py` / `collect_grouped_layout.py` / `read_profile_list.py` /
> `probe_wechat_uia.py` 之前建议先读一遍，否则很容易把已经修好的坑重新踩回去。

1. **微信主协议走 mmtls，不经过系统代理**，抓包只看得到文章页这类 WebView 流量。
2. **PC 微信 3.9.x 早于 4.x 的改造**。3.x 上的 `getmsg` 有可能还活着，值得先测一次再下结论。
3. **`set-cookie` 只记名字不记值**。里面含 `key` / `pass_ticket`，属可重放会话凭据，
   不该落盘或写进交付物。脚本已按此处理，改动时别改坏。
4. **去重必须按 `__biz:mid:idx` 归一化**，不能按 URL 去重，否则同一篇会被收两遍。
5. **剪贴板要用 ctypes 读 `CF_UNICODETEXT`，且 `OpenClipboard` 要重试**——剪贴板常被其他
   进程短暂占用，一次失败就放弃会漏收。
6. **Fiddler Classic 的原生格式是 `.saz`（zip，内含 `raw/N_c.txt`、`N_s.txt`），不是 HAR**。
   分析器两条路都支持，别让用户去装 HAR 导出插件。
7. **抓包需要把根证书装进系统信任区**，有安全代价。用户只是归档一批文章时，
   路径 A+++ / A + `wechat-mp-archive` 完全够用，**优先推荐不需要碰证书的方案**。
8. **`BoundingRectangle`(30001) 的后两个分量是宽高，不是 right/bottom。**
   实测得到 `(588,658,576,48)`：top 每次递进 75px，而按 right 解释则 `right(576) < left(588)`，
   不可能。**这个坑踩了两次**（`collect_wechat_uia.py` 与 `read_profile_list.py` 滚动兜底），
   后者表现为「所有元素都被判成无效 rect」，直接跳过了本该成功的那条路径。
   判断可见性用 `width>0 and height>0`；取中心用 `(left+w/2, top+h/2)`。
9. **元素指针不能跨「页面导航」复用。** 同窗口导航后 DOM 重建，上一轮缓存的 COM 指针
   全部失效，抛 `NULL COM pointer access`，表现为「第 1 条成功、后面全失败」。
   采集器**每轮重新枚举**，绝不跨轮持有指针。
10. **不能靠 Document 树区分「新标签页」和「同窗口导航」。** 实测两种模式下 invoke 之后
    UIA 都只暴露活动标签的 Document。**正确做法是「先后退，不行再关标签」的自愈序列，
    顺序不能反**：反了会把列表页标签关掉。
11. **回退要等，不能只快照一次。** bfcache 恢复 + UIA 树重建是异步的（1~3 秒）。
    只 sleep 0.6s 就重枚举会拿到「可见 0 条」，被误判成「没有更多条目」而结束——
    实测那次只收到 1/3，而报告看起来完全正常。
12. **`WM_GETOBJECT` 叫醒不是 100% 成功**（实测 Edge 完全叫不醒）；`pywinauto` 的
    `UIAElementInfo` **没有** `process_name` 字段（取它抛 AttributeError）。
    这两处都踩过「异常被吞 → 静默输出 0」。
13. **`GetCurrentPattern` 返回的是裸指针，必须 `QueryInterface` 才能判可用性。**
    不 QueryInterface 时它对**不支持的模式也返回非 None**，于是「每个元素都支持全部 19 种模式」
    ——一个完全无意义的结论。正确写法：
    `ptr = el.GetCurrentPattern(UIA_InvokePatternId); pat = ptr.QueryInterface(UIAc.IUIAutomationInvokePattern)`。
14. **Chromium 的 WebArea 不支持 `ScrollPattern`。** 调它必然抛
    `COMError 0x80131509 (COR_E_INVALIDOPERATION)`。兜底顺序应为
    `ScrollPattern` → `ScrollItemPattern.ScrollIntoView()` → 窗口 `WM_MOUSEWHEEL` → 真实滚轮。
    **`WM_MOUSEWHEEL` 的 SendMessage 版没有回执、无法验证是否真的滚了**，它会在前面
    「假装成功」挡住真正有效的真实滚轮。即便这样，公众号主页窗口的懒加载**仍未被打通**。
    ⚠️ **但不要把「我方滚动不通」等同于「只能拿到 20 篇」**——
    用户手动滚到底之后，整个列表在 UIA 里都可读。详见 `background.md`，**别漏掉这一步**。
15. **窗口被遮挡时 Chromium 会把页面元素 rect 全部归零、并挂起布局。** 读文字没问题，
    但依赖坐标/滚动的操作都会失败。
16. **微信内置浏览器（`WeChatAppEx.exe` / `Chrome_WidgetWin_0`）没有任何无障碍对象。**
    子窗口只有 `Intermediate D3D Window`（DirectComposition/D3D 合成），
    `Document/Text/Hyperlink` 全为 0，`WM_GETOBJECT` 叫不醒。**别在这上面继续投入。**
    → 这正是「让微信用系统默认浏览器打开」是必需前提的原因。
    注：`WeChatAppEx.exe` **进程常驻是正常的**，只有它的**窗口**出现才说明文章落错了地方；
    `--doctor` 就是按「窗口」而不是「进程」判的。
17. **浏览器窗口被遮挡时 `Document=0`，而 `WM_GETOBJECT` 单独发是叫不醒的。**
    实测同一次扫描：被遮挡的文章窗口 `Document=0`；顶到前台后 `Document=3`，
    其中就有文章 URL。**「顶到前台」是必须的一步，不是可选的优化。**
18. **微信/Chrome 是在「已有浏览器窗口里新开一个标签页」，不是新开窗口。**
    实测：开了文章之后，窗口标题从 `ChatGPT` 变成文章标题，窗口句柄没变。
    → 所以「等新窗口出现」这个判据是错的（第一版就栽在这里：Invoke 成功了却报「未捕获到 URL」）。
    **正确判据是「窗口标题匹配文章标题」**（浏览器窗口标题 = 当前标签页标题 = 页面 `<title>`），
    其次才是「标题发生变化的浏览器窗口」。
19. **同一个浏览器窗口里会同时暴露多个标签页的 `Document`。**
    收完 2 篇后，那个窗口的 `Document` 就有 2 个（第 1 篇和第 2 篇）。
    早先版本直接取第一个 mp.weixin 的 Document，于是**第 2 篇读回了第 1 篇的 URL，
    而报告看起来完全正常**。**必须按 `Document.Name`（页面标题）匹配目标文章**，
    匹配不上就报歧义，**不猜**。这条逻辑已抽成 `profile_parse.pick_article_doc()` 并被自检覆盖。
20. **抢前台需要「ALT 解锁」这一招。** 单独调 `SetForegroundWindow` 会被前台锁拒掉。
    实测有效顺序：`ShowWindow(SW_RESTORE)` + `SetWindowPos(HWND_TOPMOST)` →
    `SetForegroundWindow` → **按一次 ALT 键解除前台锁再 `SetForegroundWindow`** →
    最小化再还原 → `SwitchToThisWindow`。实测 20 篇里 `restore+topmost` / `already` /
    `alt-unlock` 三种都出现过，说明**必须做降级链，不能只试一种**。
21. **Chrome 不暴露标签页元素**（实测 `TabItem=0`）。所以没有「点关闭按钮」这条不用键盘的路，
    只能 `Ctrl+W`。**发 `Ctrl+W` 之前必须同时确认「窗口是前台」和「标题仍是这篇文章」**，
    否则按键会打到别的窗口上、可能关掉用户正在看的标签页——**宁可不关，也不要盲发**。
    （实测 20 篇里有 1 篇因前台没抢到而被安全守卫正确地跳过了。）
22. **公众号主页窗口里 68 个 Text 全都报 `InvokePattern` 可用。**
    Chromium 把整条文章行的可点性下发到了每个子节点
    （`LegacyIAccessible.CurrentDefaultAction = '点击祖先实体'`），连「4月23日」「阅读 251」
    都可点。**所以不能靠「哪个元素可点」来挑文章**，必须靠「日期→标题→阅读数」的结构配对。
    （第一次试跑就栽在这：Invoke 了账号标题，什么都没发生。）
23. **任何「读到 0 个」的分支都必须打印原因，不许静默。** 这是本项目反复出现的同一类失败，
    到目前已记录 **11 次**：分析器把剪贴板清单当空壳请求（报 0 条）、探针吞异常（报 0 个窗口）、
    采集器只快照一次（报 0 条未处理条目）、分析器静默跳过非 http 条目（报「载入 0 条」）、
    探针 `patterns_of` 误报「全部模式可用」、`read_profile_list` 把滚动失败写进不刷出的缓冲区、
    `read_profile_list` 用错 rect 语义导致滚动兜底全跳过、采集器取第一个 Document 读串篇、
    **`find_all(Document)` + `P_VALUE` 把页面来源串当正文**（得出「页面是空的」假结论）、
    **报告把同名标题的文章静默去重**（30 篇写成 24 行，且不进「未采到链接」一节）、
    **自检 import 到带 pywinauto 顶层依赖的模块被 `sys.exit` 顶掉整组**（PASS 计数还在涨）。
    **排查任何「没有数据」的结论前，先确认代码真的认得出数据。**
    → 推论：`--doctor` 的判定也要遵守这条 —— **不懂的事实就不报**，
    一项都查不出来时结论是 `unknown` 而不是 `ready`（`profile_parse.verdict()`）。
24. **`Document.Value`（UIA 30045）不是页面正文。** 折叠分组布局下取正文要用
    `element_info.rich_text`：那个窗口 Document 的 `Value` 是页面来源串
    `weixin://resourceid/SubscriptionProfile/`，正文（实测 24747 字符）在 `rich_text` 里。
    用 `find_all(Document)` + `P_VALUE` 读回来只有那个串 —— **会得出「页面是空的」假结论**。
    而且这个窗口里 Document 可能不止一个，**要挑含「阅读」且最长的那个**，不能取第一个。
25. **折叠分组布局下标题根本不在无障碍树里。** 实测把阅读数元素的底层属性全问了一遍
    （`HelpText` / `FullDescription` / `ItemStatus` / `AriaProperties` / `LegacyIAccessible` /
    `AutomationId` / `ClassName` / `Name`）**没有一处带 href，全是空** ——
    所以「逐篇打开」不是偷懒，是当前唯一可行路径；标题只能从正文纯文本里解析出来，
    再靠**位置**和「阅读数」元素配对（`profile_parse.parse_grouped`）。
26. **屏幕外（`rect=0`）的元素照样能 `Invoke`。** 未渲染/被遮挡的节点 Chromium 仍给全树，
    只是矩形是 0。实测这样打开的文章 URL 完全正确。**不要用「rect 非零」来筛可点元素** ——
    会把整页折叠的文章全筛掉（实测 485 个阅读数元素里 35 个以外全是 `rect=0`）。
27. **`pywinauto` 的 `UIAWrapper` 没有 `is_pattern_available()`。** 查模式可用性要用
    `e.iface_xxx` 属性（取不到会抛 `AttributeError`），或对底层元素
    （`e.element_info.element`）调 `GetCurrentPattern` + `QueryInterface`。
    随手写 `is_pattern_available` 只会得到一个虚假的「模式都不可用」结论。
28. **纯解析函数不要和 UI 依赖放同一个文件。** `collect_grouped_layout.py` 顶层 import
    pywinauto（缺依赖时 `sys.exit`），自检用**无 pywinauto 的裸 python** 跑，
    一 import 它就把**整个自检进程顶掉、那一组静默没跑**（PASS 计数照涨，看着像正常）。
    解析规则因此全部放在纯文件 `profile_parse.py` 里。
29. **「展开折叠组」必须是默认行为，不能是可选开关。** 被折叠的文章标题不在正文里，
    漏掉它们**在报告上完全看不出来**——只是比别人少几篇。
    把展开做成 `--expand` 可选开关 = 埋一个「要点一下参数才对」的坑；
    本项目早就有对应的判断：**凡"要点一下参数才对"的地方，默认值就得是对的。**
    所以现在是「默认展开 + `--no-expand` 逃生舱」，并且跑完**再数一遍**还剩多少折叠标记
    （`folded_markers()`），有剩余就大声告警。
    两条配套教训：
    - **`--max` 会把某一天从中间截断**，报告的「时间跨度」就成了断的（用户以为采到整天）。
      `truncation_info()` 负责算出"这一天还剩几篇没取"，并告警给出该用的 `--max` 值。
    - **改默认值时要先想旧命令会怎样**：`--expand` 原来语义是"只展开、不采集"，
      若直接删掉它，按旧文档敲 `--expand` 的人会**突然开始采几百篇**。
      保留它并维持"展开完就退出"，才叫向后兼容 —— 自检里有断言钉住这一点。
