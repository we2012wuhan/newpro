﻿# Python + FastAPI 学习项目

一个「边写接口边学 Python」的演示项目：用中文注释把 Python 核心知识点串起来，
每个知识点都配有可运行的示例，方便对照学习。

## 文件结构

| 文件 | 作用 |
| --- | --- |
| `main.py` | FastAPI 接口代码，每个接口融合一个 Python 知识点 |
| `python_basics.py` | Python 基础语法速查（可直接运行） |
| `python_advanced.py` | Python 进阶知识点速查（可直接运行） |
| `run.py` | PyCharm 调试入口（直接用 Debug 按钮启动服务） |
| `douyin_downloader.py` | 抖音视频下载网页工具（页面在 `/douyin-download`） |
| `launcher.py` | EXE 启动入口：启动服务并自动打开浏览器 |
| `ai_chart.py` | AI 数据图表分析（页面在 `/ai-chart`，需 API Key） |
| `buy_helper.py` | 购物比价助手（页面在 `/buy-helper`，需 API Key；无 Key 也可直接跳平台搜索） |
| `bill_analysis.py` | 账单分析（上传月度账单 Excel/CSV 到 `/bill-analysis`，DeepSeek 分类汇总消费并给省钱建议） |
| `translator.py` | 翻译助手（页面在 `/translator`：输入中文 -> 免费接口返回英文，并转换小写/大写/驼峰命名格式） |
| `ocr.py` | OCR图片识别（页面在 `/ocr`：粘贴截图或上传图片 -> 免费云端接口 OCR.space 识别成文字，无需本地模型，部署体积小） |
| `templates/` 与 `static/` | 工具箱主页 / AI 图表 / 比价助手 页面模板与本地静态资源 |
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

然后在浏览器打开：

- `http://127.0.0.1:8000/` **工具箱主页**：集中放置 AI 生成图表、抖音下载器 两个入口
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

调用大模型需要 API Key，三种方式任选其一：

- 在页面「模型设置」里填写（Key 只保存在本机浏览器 localStorage，不会发送给第三方）；
- 设置环境变量 `DEEPSEEK_API_KEY`；
- 在程序目录放一个 `ai_chart_key.txt`，内容就是 Key。

默认接口为 DeepSeek 官方 OpenAI 兼容地址（模型 `deepseek-chat`）；若使用其它服务商，
在页面里修改 Base URL 与模型名称即可。

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
- 默认使用公共免费 Key，无需注册即可用，但高峰期会被限流；在页面「自定义 Key」里填入自己申请的免费 Key（https://ocr.space/ocrapi ，每月 25000 次）即可稳定使用，也可以在服务端配置环境变量 `OCR_SPACE_API_KEY`；
- 免费额度单张图片上限 1 MB，中文简体（同时兼顾英文与数字）识别效果最好。

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
