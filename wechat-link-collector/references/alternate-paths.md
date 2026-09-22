# 备选路径与窗口技术细节

> 这是 `SKILL.md` 的深度补充材料。SKILL.md 里每条路径只留了速查；
> 走非主路径、或要核对窗口结构时读本文件。

## 路径 A++：读公众号主页窗口的文章索引（已验证）

微信 PC 3.9.x 的老「历史消息」窗口已经没了，但**公众号主页窗口还在**，它就是搜索/点击
公众号后打开的那个标题为「公众号」的独立窗口：

| 项 | 实测值 |
|----|--------|
| 窗口类名 | `H5SubscriptionProfileWnd`（属 `WeChat.exe`，不是主窗口） |
| 渲染 | Chromium，`Framework='Chrome'` |
| `Document.Value` | `weixin://resourceid/SubscriptionProfile/profile.html?userName=gh_xxxx&showName=...` |
| 可读性 | **可以完整读到**（实测 `Document=1 Hyperlink=2 Text=317` / 103 篇） |

读出来的内容长这样（每条 = 日期 / 标题 / 阅读数，三元一组）：

```
Text  华东师大全民数字素养培训基地            ← 账号名
Text  国家级全民数字素养与技能培训基地（华东师范大学）
Hyperlink 全部 / 文章                        ← 标签页
Text  4月23日
Text  智启未来！ECNUer专场共探"教师版小龙虾"培训活动圆满成功！
Text  阅读 251 赞 6 2个朋友看过
Text  ...（循环）
Text  正在加载...                            ← 列表是懒加载的
```

```bash
python scripts/read_profile_list.py                      # 读当前公众号窗口
python scripts/read_profile_list.py --foreground --scroll 6
python scripts/read_profile_list.py -o articles.jsonl
```

产出 `articles.jsonl`：每行 `{date, title, reads, likes, account, gh_id}`。**只读，不打开文章。**

### 已确认的事实

1. **窗口最小化时也能读到**（实测 Minimized 状态下 `Text=68` 完全可读）。不必把它摆到屏幕上。
   但注意下一条：**能读到 ≠ 已经加载完**。
2. `Document` 的直接子节点是**扁平 Text，没有「行容器」**。配对规则只能是
   「日期后面紧跟的那个非『阅读…』的 Text 就是标题」。**不要再去找 ListItem/行容器**，这版页面不生成。
3. 从 `Document.Value` 能解析出 `gh_` 原始 ID 和账号名（`userName=` / `showName=`），
   可用于校验「读到的是不是目标公众号」。
4. **能读到多少条取决于页面已加载多少**（同 `background.md` 的警告）。有 `正在加载...` 占位时通常是没加载完。
   这是两点观测得出的规律，不是已验证的机制 —— 所以判断「能不能拿全」最稳的办法
   还是**请用户把列表滚到底**，再跑 `--list` 看条数。

---

## 两种页面布局：平铺 vs 折叠分组（先判这个，判错就全错）

同一个窗口类名（`H5SubscriptionProfileWnd`）下，不同账号的页面**结构不一样**。
实测两台/两号的差别：

| | 平铺布局 | 折叠分组布局 |
|---|---|---|
| 页面组织 | 一条时间线，每篇一行 | 按「一次推送」分组，**每组只展开 3 篇**，其余折成「余下 N 篇」 |
| 标题 | 独立 `Text`，**可 `Invoke`** | **根本不在无障碍树里**（只在正文纯文本里） |
| 树里有什么 | 日期 / 标题 / 阅读数 | 日期组头 / `阅读 N 赞 M` / `余下 N 篇` / `正在加载...` |
| 可点把手 | 标题元素 | **「阅读数」元素**（`Text`，带 `InvokePattern`） |
| 日期形态 | `4月23日`、`2025年12月18日` | 同样是绝对日期，但**最新那一组是相对标签**（`星期四` / `今天`） |
| 用什么 | `collect_via_browser.py`、`read_profile_list.py` | `collect_grouped_layout.py` |

**判别用比例，不要用「有没有」**：`标题类 Text 数 ÷ 阅读数元素数`
≥0.8 → 平铺；≤0.3 → 折叠分组；中间 → `unknown`，别硬跑。
（实测折叠布局那个号的比例是 **4/485 = 0.01** —— 那 4 个里还有一个是账号简介，
一个是恰好暴露成 Text 的标题。早先版本写的是「有标题元素就算平铺」，于是判错。）

### 折叠分组布局的实测细节（某号，滚动到底后**可见** 485 条）

⚠️ 这里的 **485 是「页面上可见的条目数」，不是「该号文章总数」**。折叠在「余下 N 篇」
里的文章既不在 DOM、也不在正文里，**只有展开后才数得到**。

- **正文取法**：`Document.Value`（UIA 30045）给的是页面来源串
  `weixin://resourceid/SubscriptionProfile/`，**正文在 `element_info.rich_text`** 里
  （实测 24747 字符）。窗口里 Document 可能不止一个，**挑含「阅读」且最长的那个**。
- **正文形状**（`\u2004`/`\u2005` 是微信用的窄空格）：

  ```
  星期四 上海志愿服务动态 阅读 7915 赞 22   让急救知识走出医院…阅读 301
  温暖申城 榜样力量 | 扎根静安15年…阅读 115 赞 1    余下 5 篇
  9月10日 中社部首次亮相国新办发布会，介绍了这些情况 阅读 1116 赞 8 ...
  ```

  规律：**每条「阅读」前面那一段就是本篇标题**；段首可能挂着上一组的「余下 N 篇」
  和本组的日期组头，反复剥掉即可（`profile_parse.parse_grouped`）。
  **第 1 段要特殊处理** —— 它前面挂着整个账号头部（账号名/简介/已关注/私信/标签页），
  要从**第一个日期组头**处切开。
- **「余下 N 篇」必须点开**：被折叠的文章**不在 DOM 里**（`Document.Value` 里也没有），
  所以不展开就是**永久缺失**，而且报告上看不出少了什么。
  实测点一次「余下 4 篇」→ 阅读数元素 33 → 37、正文 1584 → 1771 字符，**原地展开**。
  展开要**每轮重新枚举、永远点当前第一个**「余下 N 篇」（点一次页面就重渲染，元素会失效）。
- **屏幕外元素照样能 `Invoke`**：485 个**可见**阅读数元素里绝大多数 `rect=(0,0,0,0)`，
  但 Invoke 后文章都能正确打开。**别用 rect 非零去筛**。
- **配对只能靠位置**，没有第二重证据（打开的那一瞬浏览器窗口标题还停在「微信公众平台」）。
  因此 `collect_grouped_layout.py` 有**两道闸**：逐条核对「第 i 个阅读数元素的名字 ==
  第 i 条解析出的阅读数」（任一条对不上整批不产出），以及首篇等浏览器标题落定后比对标题
  （对不上直接停，退出码 3）。
- **实测速度**：**未展开**状态下 485 条可见条目全部解析成功；采集 **1.9 秒/篇**
  （30 篇 56 秒，0 失败），比平铺布局那套快一倍多 —— 因为关窗口的等待更短。
  ⚠️ 那次 30 篇是**展开之前**取的，所以**不能拿它当「该号前 30 篇」的证据**
  （中间可能夹着折叠组）。现在脚本默认先展开再采。

---

## 路径 A+：普通 Chromium 窗口的 UIA 采集

`scripts/collect_wechat_uia.py`。循环是：枚举列表里的**超链接** → `InvokePattern` 打开
→ 读新出现的 `Document` 的 URL → 退回列表 → 下一条。已在模拟页 + 真实 Chrome 上端到端验证
（两种打开模式各 3/3）。

**它不适用于公众号主页窗口**（那里是扁平 Text，没有 Hyperlink 条目）。适用场景是
「列表页的条目是真正的超链接元素，且文章页落在普通 Chromium 窗口」。走路径 A+++ 时**不需要**它。

```bash
python scripts/probe_wechat_uia.py --all      # 先跑探针看落在结论 A/B/C/D 哪一格
python scripts/collect_wechat_uia.py --dry-run
```

**回归自测**：`scripts/fixtures/` 有 5 个模拟页（`list_same.html` 同窗口导航 /
`list_blank.html` 新标签页 / `a1~a3.html` 文章页）。

```bash
chrome --new-window "file:///<技能目录>/scripts/fixtures/list_blank.html"
python scripts/collect_wechat_uia.py --title BLANK --url-regex "a[0-9]+\.html" --out links.jsonl
```

预期 3 条全中，**两种模式都要过**（行为完全不同）。判据不清楚时用
`python scripts/diag_uia_mode.py BLANK` 观察真实差异。

---

## 路径 A：剪贴板监听（零依赖，最稳，兜底首选）

不用装根证书、不用碰 TLS、不用管抓包工具。用户手动点文章右上角「…」→「复制链接」，
脚本管收集、去重、排序。**不需要 pywinauto。**

```bash
python scripts/selftest.py                        # 先自检环境（不联网、不碰剪贴板）
python scripts/clipboard_watch.py -o links.jsonl  # 开始监听
```

- 签名链（`?timestamp=…&signature=…`）和永久链（`/s/xxx`）会归一成同一篇，不会重复。
- **`links.txt` 是增量写的**，直接关终端也不会丢。
- 用户一次给一大批时，也可以直接粘进一个 txt 一行一个，跳过监听。

---

## 路径 B：抓包文件分析

用户已经用 Fiddler / Charles / mitmproxy 抓过包时，**不要人工看抓包窗口**，把文件丢给分析器：

```bash
python scripts/analyze_capture.py session.saz       # Fiddler Classic 原生格式
python scripts/analyze_capture.py session.har       # Fiddler Everywhere / Charles / Chrome
python scripts/analyze_capture.py mp_capture.jsonl  # mitmproxy addon 产物
python scripts/analyze_capture.py links.jsonl       # 也可以直接整理剪贴板/UIA 的链接清单
```

产出在 `report/`：`links.txt`、`links.jsonl`、`analysis.md`（接口分布 + 列表接口候选排行）。

> **`.jsonl` 有三种来源，脚本按字段形态自动区分**：抓包记录带 `host`/`method`；
> 链接清单只有 `url`/`key`；`articles.jsonl` 带 `title`/`date` 但**没有 url**——
> 它只是索引，被喂进分析器时会被识别为「文章索引，非链接清单」并明确说明，不会假装成功。
>
> ⚠️ **判别失败的信号**：输出「载入 N 条记录」但「提取到去重文章链接：0 条」时，
> **默认按格式不匹配处理，不要当成「确实没抓到链接」**。脚本会往 stderr 打警告。
>
> ⚠️ 链接清单里的**非 http(s) 条目会被跳过并明确提示**——用 `file://` 夹具做回归时会看到
> 「N 条不是 http(s) 链接，已跳过」，那是**正常**的。

### Fiddler 用户的两种导出方式

1. 手动一次：`File > Save > All Sessions…` 存成 `.saz` —— 分析器原生支持。
2. 实时自动：用 `scripts/fiddler_autodump.js`（FiddlerScript 片段）。**未在本机验证过**，
   装之前务必让用户备份 `CustomRules.js`。

### mitmproxy 用户的实时自动

```bash
mitmdump -s scripts/mitm_mp_capture.py --allow-hosts 'mp.weixin.qq.com|channels.weixin.qq.com'
```

---

## 路径 C：判断列表接口能不能重放（决定「能不能拿全量」的一步）

让用户抓一次「打开公众号主页 + 随便点开两三篇」，跑路径 B 的分析器，看 `analysis.md`
的「列表接口候选」：

- **有得分高的候选，且响应体内嵌多条文章链接** → 列表走 HTTP，可以重放。拿住候选请求的
  `__biz` / `key` / `pass_ticket` / `uin`，只改 `offset` 翻页，一次拿全。
  **这是最好的结果，O(1) 而不是 O(n)。** 注意返回正文是 HTML 转义 + JS 变量拼接的，
  链接要 unescape 后再提取。
- **找不到带列表特征的请求** → 列表走微信私有通道（mmtls，不走系统代理），抓包永远看不到，
  **只能靠 A+++ 逐条打开**。
