﻿# Python + FastAPI 学习项目

一个「边写接口边学 Python」的演示项目：用中文注释把 Python 核心知识点串起来，
每个知识点都配有可运行的示例，方便对照学习。

## 文件结构

| 文件 | 作用 |
| --- | --- |
| `main.py` | FastAPI 接口代码，每个接口融合一个 Python 知识点；入口挂了登录门禁中间件 |
| `auth.py` | 登录 / 会话 / 访问控制（PBKDF2 密码哈希 + HMAC 签名 Cookie + 中间件，只用标准库） |
| `python_basics.py` | Python 基础语法速查（可直接运行） |
| `python_advanced.py` | Python 进阶知识点速查（可直接运行） |
| `run.py` | PyCharm 调试入口（直接用 Debug 按钮启动服务） |
| `douyin_downloader.py` | 抖音视频下载网页工具（页面在 `/douyin-download`） |
| `launcher.py` | EXE 启动入口：启动服务并自动打开浏览器 |
| `ai_chart.py` | AI 数据图表分析（页面在 `/ai-chart`，模型 Key 见下方「模型配置」） |
| `buy_helper.py` | 购物比价助手（页面在 `/buy-helper`；没配 Key 也能直接跳平台搜索） |
| `bill_analysis.py` | 账单分析（上传月度账单 Excel/CSV 到 `/bill-analysis`，DeepSeek 分类汇总消费并给省钱建议） |
| `translator.py` | 翻译助手（页面在 `/translator`：输入中文 -> 免费接口返回英文，并转换小写/大写/驼峰命名格式） |
| `ocr.py` | OCR图片识别（页面在 `/ocr`：粘贴截图或上传图片 -> 免费云端接口 OCR.space 识别成文字，无需本地模型，部署体积小） |
| `judgement_trainer.py` | 判断力小工具（页面在 `/judgement-trainer`：认出判断点 -> 拆解决策 -> 记录账本 -> 校准回访，纯浏览器计算） |
| `sdd_decomposer.py` | SDD 需求拆解器（页面在 `/sdd-decomposer`：把模糊想法按七段式逼成结构化规格，实时编译成可丢给 agent 的提示词，纯浏览器计算） |
| `socratic.py` | 苏格拉底提问（页面在 `/socratic`：一句结论进去，用六类问题追问到底，出题优先走大模型，没配 Key 走内置题库，纯浏览器计算） |
| `writing_ideas.py` | 写作选题（页面在 `/writing-ideas`：给一点线索或什么都不给，大模型一次出一批选题，挑中一个拆成启动包（标题 / 结构 / 证据 / 反驳），没配 Key 走内置配方，数据存浏览器） |
| `ecs_ssh.py` | ECS SSH 管理（页面在 `/ecs-ssh`：网页上连 ECS，AI 出命令方案、人点确认才执行；凭据只在服务端内存，主机指纹首次连接自动记下、变了会告警，只读模式默认开，灾难级命令永久禁止） |
| `predict_diary.py` | 预判日记（页面在 `/predict-diary`：写下你拿不准的一件事和现在几成把握，以后来一条新消息就更新一次；数字全在 Python 里用贝叶斯算，大模型只负责听懂人话；记录存浏览器） |
| `langchain_learn.py` | LangChain 学习（页面在 `/langchain-learn`：七课入门实操，讲一个概念就跑一次真代码，invoke / 提示词模板 / LCEL 管道 / 结构化输出 / 少样本 / 工具调用 / 流式） |
| `templates/` 与 `static/` | 各工具页面模板与本地静态资源（含登录页 `templates/login.html`、会话兜底 `static/auth-guard.js`） |
| `requirements.txt` | 项目依赖（fastapi + uvicorn + requests + yt-dlp + playwright + openpyxl） |

## 怎么运行

先激活虚拟环境并安装依赖：

```bash
# Windows
.venv\Scripts\activate
pip install -r requirements.txt

# macOS / Linux
source .venv/bin/activate
pip install -r requirements.txt
```

运行 Python 学习脚本（推荐先跑这两个）：

```bash
python python_basics.py
python python_advanced.py
```

启动服务（两种方式任选其一）：

uvicorn main:app --reload        # 学习接口 + 抖音下载器都在这个应用里
# 或只启动抖音下载器（同样可访问 http://127.0.0.1:8000/douyin-download）
python douyin_downloader.py
python video_downloader.py
```

然后在浏览器打开（**首次会先跳到登录页，创建账号后才能进**）：

- `http://127.0.0.1:8000/login` **登录 / 首次创建账号**
- `http://127.0.0.1:8000/` **工具箱主页**：所有工具入口，右上角可改密码 / 退出登录
- `http://127.0.0.1:8000/hello/小明` 路径参数
- `http://127.0.0.1:8000/items/42?q=abc` 路径参数 + 查询参数
- `http://127.0.0.1:8000/calc?a=10&b=3&op=div` 四则运算
- `http://127.0.0.1:8000/python-demo` 用接口演示 Python 小技巧
- `http://127.0.0.1:8000/docs` **重点**：FastAPI 自动生成的接口文档
- `http://127.0.0.1:8000/douyin-download` **抖音下载**：粘贴抖音分享链接即可下载视频
- `http://127.0.0.1:8000/ai-chart` **AI 数据图表**：输入主题，大模型自动整理数据并生成图表，支持导出 Excel
- `http://127.0.0.1:8000/buy-helper` **购物比价助手**：输入商品与需求，对比淘宝 / 京东销量价格并给出建议与购买入口
- `http://127.0.0.1:8000/translator` **翻译助手**：中文翻译成英文，一键复制小写 / 大写 / camelCase / PascalCase 命名
- `http://127.0.0.1:8000/ocr` **OCR图片识别**：粘贴截图或上传图片，免费云端接口识别成文字，可复制 / 下载 txt
- `http://127.0.0.1:8000/judgement-trainer` **判断力小工具**：在流水账里认出判断点、拆解决策、记决策账本并回看校准，纯浏览器计算
- `http://127.0.0.1:8000/sdd-decomposer` **SDD 需求拆解器**：一句粗糙需求进去，沿七段式逼问痛点 / 目标与非目标 / 用户 / 功能 / 交互 / 技术 / 验收，实时编译成提示词，纯浏览器计算
- `http://127.0.0.1:8000/socratic` **苏格拉底提问**：写下你已经认定的一句话，用澄清 / 前提 / 证据 / 视角 / 推演 / 反思六类问题追问到底，最后汇成一份复盘
- `http://127.0.0.1:8000/writing-ideas` **写作选题**：没灵感也能开始，给一点线索（或留空）就出一批选题，挑中一个再深挖成写作启动包（可导出 .md）
- `http://127.0.0.1:8000/ecs-ssh` **ECS SSH 管理**：填主机 / 用户名就能连上服务器，用大白话让 AI 出命令清单，勾选后自己点执行；危险命令要二次确认，几条灾难级命令直接禁止
- `http://127.0.0.1:8000/predict-diary` **预判日记**：写下你在赌什么 —— 一件事加现在几成把握，之后来一条新消息就更新一次，攒够 8 条有结果的记录就能看出你平时是不是容易高估自己
- `http://127.0.0.1:8000/langchain-learn` **LangChain 学习**：七课入门实操，每一课都是「看一段最短的代码 → 改个输入 → 点一下真跑」，直接看模型真实返回

## 登录与访问控制

打开 `http://127.0.0.1:8000/` 会先跳到 `/login`：**第一次使用先创建账号**（用户名 + 密码），之后每次进来都要登录。
没登录的人既看不到页面，也调不到任何接口。

怎么实现（都在 `auth.py`）：

- **密码**：PBKDF2-HMAC-SHA256，20 万轮 + 每条账号独立随机盐，存 `data/auth.json`，文件里没有明文；
- **会话**：HMAC-SHA256 签名的令牌放在 HttpOnly Cookie（`tb_session`）里，服务端不存 session 表；
  签名密钥首次启动自动生成到 `data/secret.key`，所以重启服务不会把已登录的人踢下线；
- **门禁**：`LoginGate` 中间件在所有路由之前执行 —— 页面请求没登录就 303 跳 `/login?next=原地址`，
  接口请求没登录就返回 401 JSON；白名单只有 `/login`、`/api/auth/*` 和 `/static/`；
- **兜底**：`static/auth-guard.js` 挂在每个页面里，任何请求拿到 401 会自动回到登录页（并记住原地址）。

另外三条防护：

- 连续输错 6 次密码，同一 IP 锁 5 分钟（`429`）；
- 跨站发来的写请求（`Origin` / `Sec-Fetch-Site` 不匹配）直接 `403`，防 CSRF；
- 改密码会让所有旧会话立即失效（令牌里带了密码指纹），改完当前设备自动续期。

数据文件都在 `data/`（已在 `.gitignore` 里）：`auth.json` 账号、`secret.key` 签名密钥。
忘了密码就删掉 `data/auth.json`，刷新页面会回到「创建账号」。

### 部署到 Vercel 这类只读环境

云函数的文件系统除 `/tmp` 外是只读的，`data/` 写不进去（创建账号会 500）。
所以 `auth.py` 支持「用环境变量当账号」，配了环境变量就不再读写任何文件：

| 变量 | 必填 | 说明 |
| --- | --- | --- |
| `TB_USERNAME` | 是 | 登录名 |
| `TB_PASSWORD` | 是 | 登录密码（首尾空格会被忽略） |
| `TB_SECRET` | 否 | 会话签名密钥，随便一串长随机字符；不配就从账号凭据派生 |
| `TB_PASSWORD_DIGEST` / `TB_PASSWORD_SALT` / `TB_PASSWORD_ROUNDS` | 否 | 只想放摘要、不想放明文时用，可替代 `TB_PASSWORD` |

配好之后：登录页自动变成「登录」而不是「创建账号」，`/api/auth/setup` 和改密码接口会明确拒绝
（而不是 500）。改了变量要**重新部署**才生效；一个变量都不配时，退回本机 `data/auth.json` 那套，本地开发流程不变。

## 浅色 / 深色主题

首页右上角有个太阳 / 月亮按钮：默认浅色（太阳），点一下整站变深色（月亮）。

- 状态存在浏览器 `localStorage['tb_theme']`，按域名共享，所以**在首页切一次，所有工具页都跟着变**，不用每个工具再切一遍；
- 颜色全部收在 `static/theme.css` 的 `--tb-*` 变量里（浅色一组，`[data-theme="dark"]` 一组），`static/theme.js` 负责切换、画按钮和多标签页同步；
- 每个页面 `<head>` 里先跑一段内联脚本把 `data-theme` 写上，所以不会出现「先白一下再变黑」的闪烁；
- 图表（ECharts）的颜色改成 `TBc('--tb-muted')` 这种写法，渲染时取当前主题的值，切主题后重新出图就是新配色；
- 判断力小工具和 SDD 拆解器自带的主题按钮也写到同一个 key 上，在哪切都是整站生效。

## 动态背景（粒子星链）

每个页面都挂着一层动态粒子背景，切到哪个工具都在，不用单独配。

- `static/particles.js` 生成一个铺满视口的 canvas（`#tb-fx`），画在内容背后（`z-index:-1`，`pointer-events:none`，不挡点击）；
- 四层叠出效果：星云光斑（缓慢游走的大色块）→ 星链连线（按距离连，鼠标靠近会推开粒子并点亮连线）→ 微粒光晕 → 每十几秒一颗流星；
- 颜色跟着浅色 / 深色主题走，深色模式用更亮的颜色加 `lighter` 叠加，浅色模式用加深后的同色系；
- 为了让它露出来，`static/theme.css` 里把 `body` 底色压成了透明，页面底色与氛围光斑改由 `html` 提供（`--tb-page-glow-1/2/3`）；
- 尊重 `prefers-reduced-motion`（只静态画一帧）、标签页切到后台自动暂停、粒子数按视口面积自适应、DPR 上限 2。

## 模型配置（本地 .env / 线上环境变量）

所有需要大模型或第三方 Key 的页面（`/ai-chart`、`/buy-helper`、`/bill-analysis`、`/study-assistant`、`/ocr`）
都不再让用户手填 Key，统一由服务端读取。读取顺序：**真实环境变量 > 项目根目录 `.env` > 代码默认值**。

本地开发：

```bash
cp .env.example .env     # Windows: copy .env.example .env
# 然后编辑 .env，把 DEEPSEEK_API_KEY 换成你自己的
```

线上部署：变量名完全一样，加到平台的环境变量面板里（`.env` 不会进仓库，Vercel 上也读不到）。

| 变量 | 必填 | 说明 |
| --- | --- | --- |
| `DEEPSEEK_API_KEY` | 是 | 大模型 Key，四个大模型工具共用；没配时页面会明确提示缺 Key |
| `DEEPSEEK_BASE_URL` | 否 | 默认 `https://api.deepseek.com`，填任何 OpenAI 兼容接口都行，会自动补 `/chat/completions` |
| `DEEPSEEK_MODEL` | 否 | 默认 `deepseek-chat`，可换 `deepseek-reasoner` 等 |
| `OCR_SPACE_API_KEY` | 否 | 不填就用 OCR.space 公共免费 Key（高峰期会被限流） |

改完 `.env` 要重启服务（`uvicorn` 的 `--reload` 会在改动 `.py` 时重启，改 `.env` 后手动重启一次最稳）。

## 抖音视频下载工具

浏览器打开 `http://127.0.0.1:8000/douyin-download`：

1. 在抖音 App 里点“分享 -> 复制链接”，把复制到的链接 / 整段分享文字粘贴到网页输入框；
2. 点“解析并下载”，等几秒后浏览器会自动保存 mp4 视频（视频越大等待越久）。

技术说明：解析优先使用“浏览器自动解析”（无头打开抖音视频页，拦截抖音页面自带签名的请求拿到直链，
每次成功都会自动刷新并保存游客 Cookie 到 `douyin_cookies.txt`，全程无需登录）；
失败时自动回退到 Cookie 详情接口、yt-dlp、无登录直连等备用通道。抖音官方没有开放下载接口，
反爬会不定期变化，若某天全部解析失败，请先执行 `pip install -U yt-dlp`，再按页面里的“下载失败？先看这里”帮助操作。

## AI 数据图表分析工具

打开 `http://127.0.0.1:8000/ai-chart`：

1. 输入想分析的主题，或直接粘贴一段数据描述（粘贴真实数据时模型会原样转述，最便于核对）；
2. 点击「生成图表」，页面调用大模型整理出结构化数据并绘图；
3. 点击「导出 Excel」下载数据明细，文件里会同时写入“数据可信度”与“来源”信息。
4. 右侧「历史记录」面板会自动保留最近 10 次成功查询，点击任意一条即可在左侧重新查看该次图表结果，
   无需重复调用大模型；记录保存在浏览器 localStorage，点「清空」可一键删除。

图表按“数据分析师视角”自动组合（避免对不合适的数据形态强行画图）：

- 始终展示：柱状图（数值对比）、饼图（占比分布）、折线图（变化趋势）；
- 数据含 ≥2 个数值指标时追加：散点图（观察两指标的关系与异常点）；含 ≥3 个指标且分类适中时
  追加雷达图（给各主体画像，各指标已归一化到 0-100 后比较相对强弱）；
- 分类 ≥3 个时追加：排名图（按主指标 Top 排序，横向条形比纵向柱状更易读）；
- 若维度是“月份 / 年份 / 日期”等时间序列且只有单指标，散点 / 雷达会自动隐藏——
  折线图已足够表达趋势，避免画出无信息量的图表。

调用大模型的 Key / 接口地址 / 模型名统一从服务端读（见下方「模型配置」），页面上不再手填。

### 数据来源与准确性（重要）

- 结果区顶部固定显示「📌 数据来源与可信度说明」卡片：每条来源按序号展示名称、口径说明和可点击的 URL 链接；
  页面直接展示前 10 条，超过 10 条时会显示“下拉查看其余 N 条”，点开即可查看全部来源。
  只输入主题、没有提供数据时，模型只能按公开常识估算，页面会标黄警告“⚠ 估算数据 · 无来源 / 未提供可核验来源”。
- 导出 Excel 时同样会附上“数据可信度”和每条来源的 label / 说明 / 链接。
- 大模型可能出错、甚至“编造来源与链接”，本工具不保证数字准确；引用或做决策前务必点开来源链接核对原文，
  或改用官方报告 / 国家统计局等一手数据复核。最可靠的用法是：把你手上的数据贴进去做分析。

## 账单分析工具

打开 `http://127.0.0.1:8000/bill-analysis`：

1. 上传从微信支付 / 支付宝 / 银行 App 导出的月度账单（`.xlsx` / `.csv`；老版 `.xls` 请先另存为 `.xlsx`）；
2. 点击「开始分析」，后端用 openpyxl/csv 自动识别列并解析成支出流水，再调用 DeepSeek 对每一笔做消费分类；
3. 结果页展示：总支出等统计卡、消费分类占比饼图、每日支出总和与分类细分堆叠柱状图、
   支付方式玫瑰图、单笔消费排行榜，以及结合真实数据的 AI 省钱建议。

说明：

- 单次最多分析 500 笔，月度账单较大请按月拆分后分别上传；
- 表格最好包含「日期 / 交易时间」「金额」「收/支」「支付方式」等列，列名自动识别（兼容微信、支付宝、常见银行流水）；
- 缺少「支付方式」列时，会由 AI 根据消费摘要推断渠道并给出提示，仅作参考；
- 分类由 AI 生成、可能存在误差，请以真实账单为准。
- 账单里标为「不计收支」（如微信导出的 `/`、支付宝的「不计收支」，多为转账 / 理财 / 退款）
   会与真实消费分开统计：不计入总消费、每日图表与分类占比，单独展示金额与明细供核对。


## 翻译助手

打开 `http://127.0.0.1:8000/translator`：

1. 输入中文（单词或短语），点击「开始翻译」，后端调用免费的公共翻译接口（MyMemory，无需密钥）返回英文；
2. 翻译结果可编辑，页面实时给出：全部小写、全部大写、单词首字母大写、camelCase（小驼峰）、PascalCase（大驼峰）；
3. 点击任意格式行即可复制，方便直接用作变量名 / 函数名 / 类名。

说明：

- 全程无需配置任何密钥，打开页面即可使用；
- 免费接口为 MyMemory 公共 API，单次请求限制约 500 字节（页面限制 200 字以内），每日有匿名额度，若提示“今日额度已用完”可稍后再试。

## OCR图片识别

打开 `http://127.0.0.1:8000/ocr`：

1. 直接按 `Ctrl + V` 粘贴截图，或点击 / 拖拽上传图片（png / jpg / gif / bmp / tiff 等；webp 或超过 1 MB 的图会在浏览器里自动转码压缩）；
2. 图片加载后自动开始识别，也可点「识别文字」重新识别；
3. 结果可直接编辑，支持「复制」「下载 .txt」「清空」。

说明：

- 识别调用免费的云端接口 OCR.space，本机不装任何模型，只依赖 `requests`，因此在 Vercel 等平台部署时体积很小；
- 默认使用公共免费 Key，无需注册即可用，但高峰期会被限流；想稳定就申请一个免费 Key（https://ocr.space/ocrapi ，每月 25000 次）配到 `.env` 或环境变量 `OCR_SPACE_API_KEY`；
- 免费额度单张图片上限 1 MB，中文简体（同时兼顾英文与数字）识别效果最好。

## 预判日记

打开 `http://127.0.0.1:8000/predict-diary`：

1. 写一件你拿不准、过些天就有答案的事（例：「下周三的评审会能过」）；
2. 点「AI 帮我写清楚」，把它改成一句到时候能明确点头 / 摇头的话，再确认你现在有几成把握；
3. 之后每来一条相关消息（例：「老板在会上说这个先放放」），让 AI 看看它值几个点 —— AI 只给方向和力度，真正把 60% 变成 72% 的那一步是 Python 算的；
4. 结果出来点「发生了 / 没发生」，AI 写一句大白话复盘；
5. 攒够 8 条有结果的记录，列表底部会告诉你：你说 70% 把握的事，实际发生了多少。

说明：

- 你不需要懂任何术语。似然比、先验、后验这些词只出现在代码里，页面上一个都没有；
- 一条消息最多让把握动 22 个百分点，95% / 5% 封顶 —— 故意留一条「还能被推翻」的缝；
- 少于 8 条结算不给任何结论，样本太小全是噪音；
- 记录只存在这台设备的浏览器里（localStorage，key `pd_bets_v1`），可导出 / 导入 JSON；
- 没配 `DEEPSEEK_API_KEY` 也能用：改写赌注那步退回你写的原文，判断新消息那步改成你自己选方向。

## LangChain 学习

打开 `http://127.0.0.1:8000/langchain-learn`。七课，每课只教一个新东西：

| 第几课 | 教什么 | 你会看到 |
| --- | --- | --- |
| 1 | `invoke()` | 模型返回的是 AIMessage，不是字符串；正文和 token 用量在哪 |
| 2 | `ChatPromptTemplate` | 槽位怎么挖、`input_variables` 长什么样、真正发出去的消息是什么 |
| 3 | LCEL 管道 | `prompt \| llm \| parser` 三段各是什么，拼完为什么还能继续接 |
| 4 | `with_structured_output` | 拿到的直接是对象，能 `.mood`、`.score` 接着算 |
| 5 | 少样本 | 3 个例子发出去时展开成什么样，模型怎么照着模仿 |
| 6 | `bind_tools` | 模型只回「想调哪个函数」，真正执行的是你自己 |
| 7 | `.stream()` | 一个字一个字往外蹦，前端打字机效果就是它 |

说明：

- 每课都有「跑一下」，**服务端真的在跑上面那段 LangChain 代码**，返回的是当前模型的真实输出，不是写死的示例；
- 课程内容写在 `templates/langchain-learn.html` 的 `LESSONS` 数组里，服务端只留一张 `lesson id -> 函数` 的白名单表，页面传什么进来都只能跑到那几个函数，没有执行任意代码的口子；
- 没配 `DEEPSEEK_API_KEY` 时页面会直接告诉你缺 Key，不会白屏；
- 第 4 课有个坑值得记住：DeepSeek 这类接口必须写 `method="function_calling"`，用默认那种会报 400 `This response_format type is unavailable now`。

## 打包成 EXE（可选）

项目支持一键打包成单文件 exe：在项目根目录执行下面命令后，生成 `dist\DouYinDownloader.exe`。
双击 exe 即可启动本地服务并自动打开「工具箱」主页（里面可进入 AI 生成图表、抖音下载器、NEXT DRAW IO、购物比价助手）；关闭黑色控制台窗口即退出。

```bash
pip install pyinstaller
pyinstaller --noconfirm --clean --onefile --console ^
    --name DouYinDownloader ^
    --collect-all playwright ^
    --collect-submodules yt_dlp ^
    --collect-submodules uvicorn ^
    --add-data "static;static" ^
    --add-data "templates;templates" ^
    launcher.py
```

注意事项：

- 首次启动 exe 需要几秒自解压时间，请耐心等待；
- Windows 可能提示「未知发布者」，点「更多信息 -> 仍要运行」即可；
- 游客 Cookie 文件 douyin_cookies.txt 会生成在 exe 同目录，方便下次复用；
- 「浏览器自动解析」通道依赖本机已安装的 Microsoft Edge，无需额外下载浏览器；
- 若杀毒软件误报，属 PyInstaller 打包程序的常见情况，将 exe 加入白名单即可。

## 学习路线建议

1. 先跑 `python_basics.py`：变量、字符串、容器、切片、循环、推导式、函数、类型注解。
2. 再跑 `python_advanced.py`：生成器、装饰器、类、dataclass、with、异常、模块。
3. 回头看 `main.py`：你会发现 FastAPI 的 `@app.get`、`Depends`、`BaseModel`
   全部建立在前两步的知识上。
4. 动手改代码：改参数、加接口、改校验规则，再刷新 `/docs` 观察变化。

## 知识点索引

| 知识点 | 位置 |
| --- | --- |
| import / from ... import | `main.py` 知识点 1 |
| 类的实例化 | `main.py` 知识点 2 |
| 装饰器 | `main.py` 知识点 3、`python_advanced.py` 第 2 节 |
| 类型注解 | `main.py` 知识点 4、`python_basics.py` 第 8 节 |
| 路径参数 / 查询参数 | `main.py` 知识点 5、7 |
| f-string | `main.py` 知识点 6、`python_basics.py` 第 2 节 |
| 类型转换、默认参数 | `main.py` 知识点 7 |
| 异常处理 raise | `main.py` 知识点 8、`python_advanced.py` 第 6 节 |
| Pydantic 数据模型 | `main.py` 知识点 9 |
| async / await | `main.py` 知识点 10 |
| 依赖注入 Depends | `main.py` 知识点 11 |
| 推导式 / enumerate / zip / lambda | `main.py` 知识点 12、`python_basics.py` 第 6 节 |
| Annotated 参数元数据 | `main.py` 知识点 13 |
| `if __name__ == "__main__"` | `main.py` 知识点 14、`python_advanced.py` 第 7 节 |
| 生成器 | `python_advanced.py` 第 1 节 |
| 类与对象 OOP | `python_advanced.py` 第 3 节 |
| dataclass | `python_advanced.py` 第 4 节 |
| 上下文管理器 with | `python_advanced.py` 第 5 节 |
| 虚拟环境与 pip | `python_advanced.py` 第 8 节 |
| json / os / pathlib / datetime | `python_advanced.py` 第 9 节 |
