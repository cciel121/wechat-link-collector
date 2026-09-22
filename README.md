# wechat-link-collector

采集**指定微信公众号**的全部文章，合成一份可直接交付的**清单报告**：按发布时间排好，
每行含 日期 / 文章标题 / 阅读数 / 可点击链接。**只收链接，不抓正文**（这是刻意的取舍，
不是能力缺口）。同时产出 `links.txt`（一行一个 URL）。

报告默认出**两种格式、内容相同**：`链接清单.md` 和 `链接清单.html`
（HTML 双击即可用浏览器打开，自带样式、不依赖外部文件，标题和链接都能点）。

> 面向使用者的操作手册是 `SKILL.md`；本文档讲**环境、目录结构、分发与打包**。

## 它能做什么

| 能力 | 说明 |
|------|------|
| 读文章索引 | 从微信「公众号主页窗口」读出日期 / 标题 / 阅读数（只读，不打开文章） |
| 采永久短链 | 用 UI Automation 驱动**系统默认浏览器**逐篇打开，读出 `/s/xxx` 永久短链 |
| 识别两种布局 | 平铺布局用主采集器；**折叠分组布局**（按"一次推送"成组、只展开 3 篇）用 `collect_grouped_layout.py` |
| 环境预检 | `--doctor`，5 秒只读检查，跑全量前先做（会顺带判定布局） |
| 合成报告 | `make_link_report.py` 一次出 `.md` + `.html` 两份（内容相同，同一份数据渲染） |
| 备选路径 | 剪贴板监听（零依赖）、抓包文件分析（Fiddler `.saz` / `.har`） |

**实测记录**：微信 3.9.12.55 + Chrome。

- 平铺布局：某公众号 **103 篇全量采集 103/103 成功**，耗时 642 秒，103 个标签页全部自动关闭。
- 折叠分组布局：某公众号 **30 篇试采 30/30 成功**，耗时 56 秒（约 1.9 秒/篇）；
  同一窗口滚动到底后**可见** 485 条（**不是该号总数**，折叠的还没算进去）。
- 逐篇耗时随布局与机器浮动（1.9–4.5 秒/篇），**采集期间不能动键鼠**。

## 不能做什么

微信已在 2026 年关闭「枚举公众号全部文章」的接口，**没有任何免登录方案能一次列出全量**。
本技能是「读出主页列表 + 逐篇打开」的自动化，**不是一键抓全站**。
并且**能读到多少条取决于列表加载了多少**：同一窗口实测读到过 20 篇，也读到过 103 篇
（滚到底才是全部）。**折叠分组布局**下同样如此——只展开的组能直接读，折起的
「余下 N 篇」需要先点开，且滚动到底前读到的永远只是已加载部分。开工前请先阅读 `SKILL.md` §0。

## 环境要求

- **Windows** + **PC 微信**
- 默认浏览器为 **Chromium 系**（Chrome / Edge）。Firefox 读不到 URL。
- 微信开启 **「设置 → 通用设置 → 使用系统默认浏览器打开网页」**
- Python 3.9+，UIA 脚本需要 `pywinauto` —— 装法见下

## 安装

```bash
pip install -r requirements.txt      # 等价于 pip install pywinauto
```

装到**实际用来跑脚本的那个 Python** 里。`pywinauto` 会带上 `comtypes` / `pywin32` / `six`，
不用再单独装别的。拿不准是哪个 Python？先双击 `scripts/run-doctor.bat`，
缺依赖时它会把该敲的命令连路径一起打印出来。

> 若在 WorkBuddy 里跑：托管虚拟环境
> `%USERPROFILE%\.workbuddy\binaries\python\envs\default\Scripts\python.exe`
> 已预装 pywinauto，启动器会优先用它。**这个环境不在本技能包内**，外部用户按上面装即可。

## 快速开始

```bash
# 0) 装依赖（首次）
pip install -r requirements.txt

# 1) 预检（5 秒，只读）
python scripts/collect_via_browser.py --doctor

# 2) 在微信里打开目标公众号，手动把列表滚到底，然后看清单条数
python scripts/collect_via_browser.py --list

# 3) 小量试跑 3 篇
python scripts/collect_via_browser.py --max 3

# 4) 全量
python scripts/collect_via_browser.py -o links.jsonl

# 5) 合成报告（一次出 .md 和 .html 两份，内容相同）
python scripts/make_link_report.py --index articles.jsonl --links links.jsonl -o 链接清单.md
```

不方便敲命令就直接双击 `scripts/run-doctor.bat` → `run-list.bat` → `run-collect.bat`
→ `run-report.bat`。启动器会自动挑出装了 `pywinauto` 的 Python，缺依赖会直接告诉你装哪一行。

> **先看布局再选采集器**：`--doctor` 会判定当前公众号是「平铺」还是「折叠分组」。
> 折叠分组布局请把上面第 2–4 步的 `collect_via_browser.py` 换成
> `collect_grouped_layout.py`（`--list` / `--max N` / `-o links.jsonl` 参数一致）；报告与
> `links.txt` 的合成方式完全相同。两个脚本的取舍见 `SKILL.md` §2「先判布局」。
>
> ⚠️ 折叠分组布局下，这个采集器**会自动先展开所有「余下 N 篇」**（被折叠的文章标题
> 不在页面正文里，不展开就是永久缺失，而报告上看不出少了什么）。跑完它会打印
> `✅ 已无「余下 N 篇」`；若打印的是 `⚠️⚠️ 页面仍有 …` 就说明没展完，先单跑
> `--expand`（只展开、不采集）再采。`--max` 把某一天从中间截断时也会告警。

## 目录结构

```
wechat-link-collector/
├── SKILL.md                  # 操作手册（agent 读）
├── README.md                 # 本文档（人读）
├── requirements.txt          # 依赖清单（pip install -r requirements.txt）
├── references/               # 深度资料，按需读
│   ├── background.md         #   背景、接口时间线、「20篇 vs 103篇」误判
│   ├── uia-internals.md      #   29 条 UIA/窗口踩坑记录
│   ├── alternate-paths.md    #   备选路径细节、两种页面布局对比
│   └── dev-guide.md          #   实测数据、自检覆盖、改脚本后必做
└── scripts/
    ├── collect_via_browser.py     # 主路径 A+++（平铺布局；+ --doctor）
    ├── collect_grouped_layout.py  # 折叠分组布局采集（+ --doctor）
    ├── profile_parse.py           # 解析/判定纯函数（自检主战场）
    ├── probe_wechat_uia.py        # 只读探针 + 控件常量工具
    ├── collect_wechat_uia.py      # 路径 A+
    ├── read_profile_list.py       # 路径 A++
    ├── clipboard_watch.py         # 路径 A
    ├── analyze_capture.py         # 路径 B
    ├── make_link_report.py        # 报告合成
    ├── mitm_mp_capture.py         # mitmproxy addon
    ├── fiddler_autodump.js        # FiddlerScript 片段（未本机验证）
    ├── diag_uia_mode.py / diag_article_window.py   # 诊断
    ├── selftest.py                # 自检（134 项 / 11 组）
    ├── run-*.bat / _findpy.bat    # 双击启动器
    └── fixtures/                  # 5 个模拟页（路径 A+ 回归用）
```

## 各脚本作用与 pywinauto 依赖

| 脚本 | 作用 | 需要 pywinauto |
|------|------|:---:|
| `collect_via_browser.py` | **首选（平铺布局）**：A+++ 采集永久短链；含 `--doctor` 预检 | ✅ |
| `collect_grouped_layout.py` | **首选（折叠分组布局）**：按"一次推送"成组解析标题/日期/阅读数；含 `--doctor` | ✅ |
| `read_profile_list.py` | 读公众号主页窗口的文章索引（日期/标题/阅读数） | ✅ |
| `profile_parse.py` | 解析规则 + 标题匹配 + 预检判定（纯函数，自检主战场） | ❌ |
| `probe_wechat_uia.py` | 只读探针 + 采集器依赖的控件常量与遍历工具 | ✅ |
| `collect_wechat_uia.py` | 路径 A+：普通 Chromium 列表页的 UIA 采集 | ✅ |
| `clipboard_watch.py` | 路径 A：剪贴板监听 | ❌ |
| `analyze_capture.py` | 路径 B：saz / har / jsonl 分析器 | ❌ |
| `make_link_report.py` | 索引 + 链接 → 报告（`.md` + `.html` 两份） | ❌ |
| `mitm_mp_capture.py` | mitmproxy addon（实时抓包） | ❌ |
| `fiddler_autodump.js` | FiddlerScript 片段（**未在本机验证**） | — |
| `diag_uia_mode.py` / `diag_article_window.py` | 诊断工具 | ✅ |
| `selftest.py` | 自检（**134 项 / 11 组**） | ❌ |



## 打包

用 skill-creator 自带的打包器（会先做规范校验）：

```bash
python <skill-creator>/scripts/package_skill.py <本技能目录> ./dist
```

⚠️ 该打包器用 `rglob('*')` **无差别打包所有文件**，所以**打包前必须先删掉
`scripts/__pycache__/` 和 `scripts/output/`**，否则会把 244 KB 缓存和采集产物一起发出去。

## 自检

```bash
python scripts/selftest.py      # 134 项 / 11 组，不需要 pywinauto
```
