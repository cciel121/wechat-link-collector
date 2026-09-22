---
name: wechat-link-collector
description: 采集指定微信公众号的全部文章，合成一份可直接交付的清单报告（Markdown + HTML 两种格式，内容相同，按时间排列，含日期 / 文章标题 / 阅读数 / 可点击链接）以及 links.txt。只收链接、不抓正文。当用户说「导出这个公众号的所有文章链接」「把公众号文章清单整理出来」「做一份带日期和阅读数的文章清单」「要一份网页版/HTML 的文章清单」「批量拿到公众号文章地址」「让 agent 自动点开公众号文章拿链接」「用微信客户端/Fiddler 抓链接」时使用。自带环境预检（--doctor），可读公众号主页窗口的文章索引（日期/标题/阅读数），并用 UI Automation 驱动系统默认浏览器采到永久短链；另有剪贴板监听、抓包文件分析等备选路径。
agent_created: true
---

# 微信公众号文章链接采集

**定位**：把「指定公众号的全部文章」变成一份**可直接交付的成品清单** ——
一张按发布时间排好的表，每行是 **日期 / 文章标题 / 阅读数 / 可点击链接**（形态见 §10），
外加同内容的 `links.txt`（纯 URL，一行一个）。

**不抓正文是刻意的设计决定，不是能力缺口。** 用户要的就是这份清单；确实需要正文时
才走可选的 `wechat-mp-archive`（见 §10），本技能的产出不依赖它。

---

## 0. 动手前必须跟用户说清（别跳）

**① 没有任何免登录方案能一次列出全量文章。** 微信已在 2026 年关闭了「枚举公众号全部文章」的
接口（时间线与连带后果见 `references/background.md`）。本技能靠的是：

1. 读**公众号主页窗口**里的文章索引（日期/标题/阅读数）——已完整验证；
2. 用 UIA 让微信把每篇开进**系统默认浏览器**，读**永久短链**（`/s/xxx`）——已完整验证，
   **实测某公众号 103 篇全量 103/103 成功**（主路径 A+++）；
3. 若列表接口还活着，重放它一次拿全（路径 C，需先验证）。

**② 能读到多少，取决于页面加载了多少 —— 这是最容易被误判的一点。**

- 同一窗口、同一脚本，实测读到过 **20 篇**，也读到过 **103 篇**（覆盖该号 2020→2026 全部）；
  差别不在脚本，在**列表有没有加载完**。
- 所以：**先请用户在公众号主页窗口里手动滚到底，再跑脚本**；
  **不要跑一次就告诉用户「这个号只有 20 篇」**——犯过这个错。
- 脚本会自动合并去重，可分批累加。

**③ 公众号主页有**两种**布局，先用 `--doctor` 判清是哪种 —— 判错会得出「这个号只有 1 篇」的假结论。**

- 判别**不能靠「有没有标题元素」，要用比例**：标题数 ÷ 阅读数元素数 ≥ 0.8 → **平铺**
  （用 `collect_via_browser.py`）；≤ 0.3 → **折叠分组**（用 `collect_grouped_layout.py`）。
  两个 `--doctor` 都会打印这个比例和结论。
- 折叠分组下**文章标题根本不在无障碍树里**，树里只有日期组头 / `阅读 N 赞 M` /
  `余下 N 篇` / `正在加载...`；取数方式见 §4 末节。两种布局对比见 `alternate-paths.md`。

---

## 1. 前置条件（两个，缺一不可）

| # | 条件 | 怎么确认 |
|---|------|----------|
| 1 | 微信「设置 → 通用设置」开启 **「使用系统默认浏览器打开网页」** | 没开的话文章落进微信内置浏览器（`WeChatAppEx.exe`），那里**没有任何无障碍对象，读不到 URL**。`--doctor` 会检测 |
| 2 | 在微信里打开目标公众号，**并在那个窗口里手动把列表滚到底** | 不做这步只能读到 ~20 篇，而那**不是全量** |

---

## 2. 快速上手

> **首次使用先装依赖**（在技能根目录执行，一条命令）：
> `pip install -r requirements.txt`（等价于 `pip install pywinauto`，见 §7）

### 入口一：双击启动器（给不方便敲命令的场景）

都在 `scripts/` 下。启动器自动找装好依赖的 Python（见 §7），输出落在 `scripts/output/`。

| 启动器 | 作用 |
|--------|------|
| `run-doctor.bat` | **只做环境预检**（约 5 秒，只读）。跑全量前先点这个 |
| `run-list.bat` | 读文章清单 + 导出索引 `articles.jsonl`（只读，不打开文章） |
| `run-collect.bat` | 全流程：预检 → 清单 → 采集，产出 `output/links.jsonl` + `links.txt` |
| `run-report.bat` | 生成可读报告 `output/链接清单.md` + `链接清单.html`（离线，不需要 pywinauto）。**需要 `run-list.bat` 的索引 + `run-collect.bat` 的链接都在**，缺索引会警告并降级 |
| `run-probe.bat` | 只读探针，环境异常时把输出整份发回来 |

### 入口二：命令行

```bash
# 0) 预检：5 秒，只读，不打开任何文章
python scripts/collect_via_browser.py --doctor

# 1) 看清单，确认条数对不对（这一步能立刻暴露「没滚到底」）
python scripts/collect_via_browser.py --list
python scripts/read_profile_list.py -o articles.jsonl     # 索引（含阅读数，报告排序用）

# 2) 小量试跑 3 篇，确认链路通
python scripts/collect_via_browser.py --max 3

# 3) 全量（重复的会自动合并去重）
python scripts/collect_via_browser.py -o links.jsonl

# 4) 合成给人看的报告（一次出 .md + .html 两份，内容相同）
python scripts/make_link_report.py --index articles.jsonl --links links.jsonl -o 链接清单.md
```

> ⚠️ 上面写 `python` 只是示意——**前提是那个 Python 装了 pywinauto**（见 §7）。
> 没装时 UIA 相关脚本会直接退出并打印装法，不会给你一堆 traceback。
> Windows 上更省事的是直接用 `scripts/run-*.bat` 启动器。

---

## 3. 路由

```
用户要「某公众号的文章清单」
├─ 手上还没有链接
│   ├─ 首选路径 A+++：UIA 驱动「系统默认浏览器」采永久短链（全自动）
│   │    前提：微信设置里开启「使用系统默认浏览器打开网页」
│   ├─ 不能开那个开关 → 路径 A+（普通 Chromium 窗口）/ 路径 A（剪贴板监听，零依赖最稳）
│   └─ 想拿到全部历史文章 → 请用户先把公众号主页列表**手动滚到底**再跑
│        想彻底免掉逐条打开 → 做路径 C 验证；列表接口能重放就一次拿全（O(1)）
└─ 手上已经有链接清单
    └─ 直接跑 make_link_report.py 合成清单报告 —— **交付即完成**
       只有确实要正文时，才再拿 links.txt 喂 wechat-mp-archive（可选，本技能不依赖它）
```

**先判布局**（两种布局的脚本不同，判错会得出「只有 1 篇」的假结论）：

```
两个 --doctor 看「标题/阅读数 比例」
├─ ≥0.8  平铺     → collect_via_browser.py
└─ ≤0.3  折叠分组 → collect_grouped_layout.py（**会自动展开**折叠组；不展开只能拿到每组前 3 篇）
```

---

## 4. 主路径 A+++：UIA 驱动系统默认浏览器采永久短链（首选）

`scripts/collect_via_browser.py`。完整链路，每一步都在本机实测过
（微信 3.9.12.55 / Windows / 默认浏览器 Chrome）：

```
读公众号主页窗口的文章清单（日期/标题/阅读数）
   ↓ 对标题元素调 InvokePattern.Invoke()      ← 不移动鼠标
微信把文章开进系统默认浏览器                    ← 是已有窗口里**新开标签页**，且不抢前台
   ↓ 定位那个窗口（按窗口标题 = 当前标签页标题）
顶到前台 + 发 WM_GETOBJECT                      ← 被遮挡时 Chromium 不建无障碍树，这步不能省
   ↓ 读 Document.Value
https://mp.weixin.qq.com/s/xxxx                  ← 永久短链，不过期
   ↓ 确认前台 + 标题仍匹配 → Ctrl+W 关标签
下一条
```

### --doctor：跑之前先花 5 秒

```bash
python scripts/collect_via_browser.py --doctor
```

检查：pywinauto 是否可用、微信主进程在不在、公众号主页窗口有几个、窗口 Document 能否读、
文章清单解析出多少篇、列表是否还挂着「正在加载...」、有没有微信内置浏览器窗口、
有没有 Chromium 浏览器窗口。每条给 ✅/⚠️/❌，最后给结论：

- `blocked`（有 ❌）→ 退出码 2，先处理再跑；
- `unknown`（一项都没能确认，打印 `❓`）→ 退出码 2，**这不是「通过」**，
  把原始输出留着一起看；
- `attention`（只有 ⚠️）→ 能跑，但要先看警告，**尤其「没加载完」和「内置浏览器」两条**；
- `ready` → 退出码 0。

判定规则在 `profile_parse.judge_environment()`（纯函数，被自检第 10 组覆盖 21 条断言）。
**价值**：全量采集要 ~11 分钟且会抢前台，把「微信没开」这类问题放到跑之前查出来很划算。

### 其他开关

```bash
python scripts/collect_via_browser.py --list            # 只看清单，不打开
python scripts/collect_via_browser.py --max 3           # 小量试跑
python scripts/collect_via_browser.py -o links.jsonl    # 全量；自动与已有文件合并去重
python scripts/collect_via_browser.py --skip 20         # 接着上次往后跑
python scripts/collect_via_browser.py --no-close        # 不关标签页（调试）
python scripts/collect_via_browser.py --max-fail 3      # 连续失败几篇就停（默认 3）
```

产出 `links.jsonl` + `links.txt`（纯 URL，一行一个）。**注意这不是终点**——
下一步要接「合成清单报告」，别停在 jsonl 上。

### 最后合成报告（别省这一步）

`links.jsonl` 的顺序是**采集顺序**，重试补采的单篇会被追加到末尾，直接拿它排表会把
「时间跨度」印错。用索引对齐重排：

```bash
python scripts/make_link_report.py --index articles.jsonl --links links.jsonl -o 链接清单.md
```

报告以 **`articles.jsonl`（索引）** 的顺序为准；索引里有、链接里没有的会**明确列在
「未采到链接」一节并给出 `--skip` 重试命令**，不会静默丢弃。

**一次出两份、内容完全相同**（由同一份内存数据渲染，不存在谁落后谁）：`链接清单.md` +
`链接清单.html`；`--no-html` 只出 md，`--html <路径>` 换位置 —— **用户要网页版不用额外做什么。**
大标题默认**自动取数据里的账号名**（`<账号> —— 公众号文章链接清单`），要自定义加 `--title`。
报告的完整形态、各字段、以及「到链接为止」的边界见 §10。

⚠️ **索引必须真的存在**。若 `articles.jsonl` 缺失或为空，报告仍会生成，但会**退回采集顺序**
（跨度和排序都不对）——脚本会在 stderr 上打出警告，`run-report.bat` 也会提示。
**这种报告不要当成品交付**，先补跑 `run-list.bat` 再重生成。

### 代价（跑之前必须跟用户说）

1. **会抢前台**，自动化期间不能用鼠标键盘；
2. 依赖 Chromium 无障碍树与窗口标题，微信/浏览器改版都可能失效（失效时会明确报原因）；
3. 扫码登录必须用户本人完成，脚本不碰登录；
4. **能读到多少条取决于页面加载了多少** —— 跑之前先问一句「滚到底了吗」（见 §0 ②）。

> 实测数据（单篇耗时、总耗时、成功率）见 `references/dev-guide.md`。

### 另一种页面布局：折叠分组（`collect_grouped_layout.py`）

同一套「Invoke → 系统默认浏览器 → 读永久短链」的链路，**但读清单的方式不同**：
折叠布局下标题不是控件，改从正文纯文本里解析，并把**每篇的「阅读数」当可点把手**
（实测阅读数元素可 Invoke，屏幕外 `rect=0` 的也能点）。

```bash
python scripts/collect_grouped_layout.py --doctor        # 判定布局（只读）
python scripts/collect_grouped_layout.py --list          # 只解析清单，不打开（会先展开）
python scripts/collect_grouped_layout.py --max 30 -o links.jsonl
python scripts/collect_grouped_layout.py --skip 30 -o links.jsonl   # 接着往后跑
python scripts/collect_grouped_layout.py --expand        # 只展开，展开完退出（不采集）
```

- ★ **展开折叠组是默认行为，不用加参数**：被折叠的文章不在 DOM 里，不展开就是永久缺失，
  且**报告上看不出少了什么**。逃生舱是 `--no-expand`（仅排查用）。
  跑完会**再数一遍**：还有折叠就告警 `⚠️⚠️ 页面仍有 …`（`folded_markers()`）。
- ★ `--max` **把某一天从中间截断**时也会告警（`truncation_info()`），并给出该用的篇数。
- 除了 `links.jsonl` / `links.txt`，它**另外**输出一份索引（`--index-out`，默认与 `-o` 同目录的
  `articles.jsonl`），可直接喂 `make_link_report.py --index`。实测数据见 `dev-guide.md` §1b。
- ⚠️ 「标题 ↔ 链接」在这个布局下只能靠**位置**配对，没有第二重证据。脚本因此做了两道闸：
  逐条核对「第 i 个阅读数元素的名字 == 第 i 条解析出的阅读数」，**任一条对不上就整批不产出**；
  首篇还会等浏览器标题落定后比对解析出的标题，**对不上直接停下**（退出码 3，不要加参数硬跑）。

---

## 5. 备选路径速查

细节（窗口结构、配对规则、判别信号）见 `references/alternate-paths.md`。

| 路径 | 脚本 | 何时用 | 关键命令 |
|------|------|--------|----------|
| **A++** | `read_profile_list.py` | 只想要**文章索引**（日期/标题/阅读数），不打开文章 | `python scripts/read_profile_list.py -o articles.jsonl` |
| **A+** | `collect_wechat_uia.py` | 列表条目是**真超链接**、且文章落在普通 Chromium 窗口。走 A+++ 时不需要 | 先 `probe_wechat_uia.py --all` 看落在结论 A/B/C/D 哪格 |
| **A** | `clipboard_watch.py` | 不能改微信设置时的兜底：用户手动「复制链接」，脚本收下 | `python scripts/clipboard_watch.py -o links.jsonl` |
| **B** | `analyze_capture.py` | 用户已经用 Fiddler / Charles / mitmproxy 抓过包 | `python scripts/analyze_capture.py session.saz` |
| **C** | 判断列表接口能否重放 | 想知道「能不能一次拿全量」 | 抓一次包后看 `report/analysis.md` 的「列表接口候选」 |

要点（窗口结构、配对规则、增量落盘、链归一、`offset` 翻页 / mmtls 判定见 `alternate-paths.md`）：

- **路径 B** 支持 `.saz`（Fiddler Classic 原生）、`.har`、mitmproxy 的 `.jsonl`，
  也能直接整理剪贴板/UIA 的链接清单。产出在 `report/`。
  ⚠️ 若输出「载入 N 条记录」但「提取到去重文章链接：0 条」，**按格式不匹配处理，
  不要当成「确实没抓到链接」**。

---

## 6. 路径 A++：读文章索引（已验证，只读）

微信 PC 3.9.x 的老「历史消息」窗口已经没了，但**公众号主页窗口还在**——就是搜索/点击公众号后
打开的那个标题为「公众号」的独立窗口（窗口类名 `H5SubscriptionProfileWnd`，Chromium 渲染）。
窗口结构与解析规则见 `references/alternate-paths.md`。

```bash
python scripts/read_profile_list.py                      # 读当前公众号窗口
python scripts/read_profile_list.py --foreground --scroll 6
python scripts/read_profile_list.py -o articles.jsonl
```

产出 `articles.jsonl`：每行 `{date, title, reads, likes, account, gh_id}`。**只读，不打开文章。**

关键事实（完整版见 `references/alternate-paths.md`）：

- **窗口最小化时也能读到**，不必摆到屏幕上。但**能读到 ≠ 已经加载完**。
- `Document` 的直接子节点是**扁平 Text，没有行容器**。配对规则只能是「日期后面紧跟的那个
  非『阅读…』的 Text 就是标题」。**不要再去找 ListItem/行容器**，这版页面不生成。
- 从 `Document.Value` 能解析出 `gh_` 原始 ID 和账号名，可用于校验「读到的是不是目标公众号」。

---

## 7. 依赖

UIA 相关脚本需要 `pywinauto`。在**技能根目录**执行：

```bash
pip install -r requirements.txt      # 等价于 pip install pywinauto
```

- 装到**实际跑脚本的那个 Python** 里。拿不准是哪个？先双击 `run-doctor.bat`——
  缺依赖时它会把该敲的那一行命令连路径一起打出来。
- **Windows 上优先双击 `scripts/run-*.bat`**：它们按
  「WorkBuddy 托管 venv（存在才用）→ `py -3` → `python`」顺序探测，并**实测
  `import pywinauto` 能不能过**，不行就直接打印修复命令。
- 若在 WorkBuddy 里跑，托管环境
  `%USERPROFILE%\.workbuddy\binaries\python\envs\default\Scripts\python.exe`
  已预装 pywinauto，启动器会优先用它。**该环境不在技能包内**，外部用户按上面自己装。
- **不需要 pywinauto**（普通 `python` 就能跑）：`clipboard_watch.py`、
  `analyze_capture.py`、`make_link_report.py`、`profile_parse.py`、`selftest.py`。

> 装依赖的完整说明、**脚本清单（含哪个脚本要 pywinauto）**、以及**分发要传哪些文件**，
> 见 `README.md`。`scripts/output/` 是启动器写数据的地方，**不是技能内容**。

---

## 8. 失败排查表

| 症状 | 最可能原因 | 怎么做 |
|------|-----------|--------|
| ❌ 没有「公众号主页窗口」 | 没打开目标公众号；或去找了已下线的「历史消息」窗口 | 在微信里搜索并点开目标公众号，会出现标题为「公众号」的独立窗口 |
| ❌ 检测到内置浏览器窗口 | 「使用系统默认浏览器打开网页」没开 | 微信「设置 → 通用设置」开启；**改完要重开一篇文章才生效** |
| ⚠️ 只解析到 ~20 篇、末尾有「正在加载...」 | **列表没加载完，不是脚本上限** | 在公众号窗口里**手动滚到底**再重跑（见 §0 ②）。**别告诉用户「只有 20 篇」** |
| ⚠️ 只解析出 **1 篇** | **这个号是「折叠分组」布局**，标题不是控件，`extract_pairs` 那套配对配不出来 | 不是环境问题、也不是「只有 1 篇」。改用 `collect_grouped_layout.py`（先 `--doctor` 看比例 ≤0.3） |
| 折叠布局采到的条数偏少、或某天只剩 3 篇 | 折叠组没展开（现在默认展开） | 正常应打印 `✅ 已无「余下 N 篇」`；若见 `⚠️⚠️ 页面仍有 …` 就是没展完，先单跑 `--expand`。**报告上看不出少了什么，别交付** |
| 报告「时间跨度」像断在某天 | `--max` 把这一天从中间截断了 | 脚本会告警并给出该用的 `--max` 值。按它重跑，或去掉 `--max` |
| 重采后条数成了两批之和（30 → 60） | `-o` 指向了上次的 `links.jsonl`：脚本**按 url 合并续采**（分批累加的设计，不是 bug） | 想干净重采就换 `-o` 路径，或先把上次产物改名归档。**修完 bug 重采尤其要看这个** |
| 折叠布局「位置校验」失败 / 首篇标题对不上 | 正文与元素错位、页面结构变了 | 脚本会**拒绝产出**（宁可空手也别给错的对照表）。先 `--list`，持续失败跑 `probe_wechat_uia.py --all` |
| 找到窗口但「读不到任何 Document」 | 窗口被遮挡/最小化 | 别让它被挡；或加 `--foreground`；探针可先用 `--all` 看全景 |
| 第 2 篇读出第 1 篇的 URL | 多标签页 Document 判据（`uia-internals.md` 第 19 条） | 已由 `pick_article_doc` 按标题匹配修掉。若复现，用 `--no-close` 比窗口标题与列表标题 |
| Ctrl+W 之后标签没关掉 | 抢前台失败 | 安全守卫会跳过（**设计行为**）。手动关掉多余标签页即可 |
| Invoke 失败「打开失败」 | 页面重渲染致元素失效 | 脚本会自动重取元素再试一次；持续失败说明页面结构变了，跑 `probe_wechat_uia.py` 确认 |
| 分析器报「载入 N 条」但「0 条链接」 | **格式不匹配**，不是"没抓到" | 按格式问题处理；看 stderr 的警告，确认喂进去的是抓包文件还是链接清单 |
| `ModuleNotFoundError: No module named 'pywinauto'` | 这个 Python 没装依赖 | 在技能根目录 `pip install -r requirements.txt`（见 §7）；或用 `run-*.bat` |
| 报告里**没有「时间跨度」**、顺序像乱的 | 没给索引（`articles.jsonl` 缺失或为空） | 先跑 `run-list.bat` / `read_profile_list.py -o articles.jsonl`，再加 `--index` 重生成。**别当成品交付** |
| `ModuleNotFoundError: No module named 'probe_wechat_uia'` / `'collect_wechat_uia'` / `'profile_parse'` | **打包时漏了文件**，不是环境问题 | 见 `README.md` 的文件清单：主路径必须四个 `.py` 成套，缺一即 `import` 就失败 |
| 「没找到文章落地的浏览器窗口」 | 三种原因，脚本会分情况提示 | ① 打开方式没设；② 默认浏览器不是 Chromium 系（Firefox 读不到）；③ 浏览器被最小化 |
| 采到一半连续失败 | 窗口被遮挡 / 前台被别的窗口抢走 | 用 `--skip N --max 1` 按序号逐篇补采，别整批作废 |
| 全量跑完发现缺几篇 | 遮挡等偶发原因 | 报告里「未采到链接」一节已列出是哪几篇 + 重试命令，照做 |

---

## 9. 交付前校验

1. 跑 `--doctor`：结论不是 `blocked`（`attention` 要先逐条解释给用户）。
2. `links.txt` 行数 == `links.jsonl` 行数。
3. 链接数 > 0。**若为 0 且「载入记录数」> 0，先怀疑格式/解析问题，
   不要直接回复用户「没搜到」。**
4. 抽查几条链接能否在浏览器打开（打不开的多半是签名过期，或被截断）。
5. 把标题/链接清单给用户扫一眼确认没漏。
6. 走 A+++ / A++ 时：先确认**用户在窗口里手动滚动过、列表已加载完**。没滚过时条数可能
   只有 ~20，**那不是该号的全部文章**——明确说明并请用户滚到底后重跑（见 §0 ②）。
7. 走 A+ 时：采集器报的「成功打开次数」是否 ≈ 列表条目数。差距大就翻过程日志，
   若大量出现「后退无效→关标签」或「没能离开文章页」，说明这版微信窗口行为变了，
   **别直接交付半截结果**。
8. 走 A+++ 时：报告里的「失败明细」必须为空或已逐条解释；**同一篇重复出现很可能是
   「多标签页 Document」判据出了问题**，别当成「脚本跑通了」。
9. 报告里应出现**「时间跨度」一行**。缺了说明索引（`articles.jsonl`）没喂进去，
   排序也是错的——先补索引再重新生成，别直接交付（见 §8 对应行）。
10. **`.md` 和 `.html` 两份都要在**（默认一起产出）。只看到 `.md` 说明加了 `--no-html`
   或生成被中断；HTML 版是用户要的网页形态，别漏交。
11. **报告行数 == `links.jsonl` 行数 == `articles.jsonl` 行数**：报告按序与链接配对，
   旧版会把同名的静默去重（实测 30 篇被写成 24 行）；索引少了则是**分批续采**时被覆盖
   （已修）。头里会标明「同名标题 N 条」，数字对不上就是又退化了。
12. 走折叠分组布局时：采集开头有没有 `✅ 已无「余下 N 篇」`（**没有就是可能漏采**）、
    有没有截断告警、`--index-out` 的索引在不在。

---

## 10. 交付形态（这是成品，不是中间产物）

本技能的最终交付物是 `make_link_report.py` 产出的**清单报告，默认两种格式、内容相同**：
`链接清单.md` 与 `链接清单.html`（HTML 双击即可打开、自带样式不依赖外部文件，
标题和链接都能点，还带一个「复制全部链接」按钮）。

两份都是同一形态：

- **大标题**：`<账号> —— 公众号文章链接清单`（自动从数据里取账号名，不用手传）
- **头部**：账号名、原始 ID、条数、时间跨度
- **主体**：一张表 —— `# | 日期 | 文章标题 | 阅读 | 链接`（HTML 版标题本身可点）
- **尾部**：「纯链接」区，一行一个 URL；有遗漏时另有「未采到链接」一节 + 重试命令

**到「链接」为止，不抓正文。** 实测 103 篇（某号 2020→2026 全量）的成品就是这个形态，
用户拿到即交付完成。同步产出的 `links.jsonl` / `links.txt` 是同一份数据的机器可读版本。

| 技能 | 职责 |
|------|------|
| `wechat-article-search` | 按**关键词**找文章（走搜狗微信，只索引热门，不能用于全量） |
| **本技能** | 从**指定公众号**采集并合成清单报告。**交付即完成** |
| `wechat-mp-archive` | **可选下游**：确实要正文时才用，拿 `links.txt` 批量下载归档 |

`links.txt` 的格式与 `wechat-mp-archive` 的 `scripts/fetch.py` 天然对齐（一行一个 URL），
但那条链是**可选的** —— 本技能不 import、不调用它的任何代码，没有它照样出完整成品。

---

## 附：参考资料索引

改脚本、排障、或要搞清「为什么这样设计」时按需读：

| 文件 | 内容 |
|------|------|
| `references/background.md` | 接口关闭时间线、本技能的真实能力、**「20 篇 vs 103 篇」误判的完整来龙去脉** |
| `references/uia-internals.md` | **UIA/窗口踩坑记录（含折叠分组布局的取数坑）**，改采集器前必读 |
| `references/alternate-paths.md` | 路径 A++/A+/A/B/C 细节、**两种页面布局的判别与样本**、配对规则 |
| `references/dev-guide.md` | 实测数据、自检 11 组覆盖、改脚本后必做、只能真机回归的部分 |
| `README.md` | 面向人的说明：环境要求、目录结构、**分发文件清单**、打包方式 |
