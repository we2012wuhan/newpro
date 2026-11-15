# RAG 实验台

在自己的文档上做**可重复、可度量**的 RAG 检索实验。只用标准库完成切块、
BM25 和评估；生成部分复用项目现有的 `model_config`。

核心心智模型：

> **RAG = 检索（找对材料）→ 增强（组织进上下文）→ 生成（基于材料回答并引用）。**
> 大多数“答得不好”其实是检索问题，不是模型问题。

## 快速开始

在项目根目录执行：

```powershell
# 看语料和切块统计
.\.venv\Scripts\python.exe -m rag_lab build

# 检索一个问题，观察 top-5
.\.venv\Scripts\python.exe -m rag_lab ask "长期记忆最多保留多少条？"

# 检索 + 调模型生成带引用的回答
.\.venv\Scripts\python.exe -m rag_lab ask "长期记忆最多保留多少条？" --generate

# 在 golden set 上评估
.\.venv\Scripts\python.exe -m rag_lab eval

# 一张表对比两种切法 × 两种检索器
.\.venv\Scripts\python.exe -m rag_lab compare
```

默认语料是 `README.md` + `docs/`，默认切法是 `heading`，默认检索器是 `bm25`。

## 网页版

启动项目后访问 **http://127.0.0.1:8000/rag-lab**（首页「RAG 实验台」卡片也能进）。
页面上可以直接：

- 输入问题，看 top-k 资料、每段的分数和出处
- 勾选「让模型基于资料回答」，看带 `[编号]` 引用的回答
- 点「跑评估」看 Recall / MRR / 失败归因，点失败项会提示下一步该改什么
- 点「对比 4 种组合」看切法 × 检索算法谁更好
- 改切块方式、检索算法、k、块大小和重叠，再重新跑

网页和命令行调用的是同一个 `rag_lab` 包，参数一样时结果一致。

## 命令参数

| 参数 | 说明 |
| --- | --- |
| `--sources README.md docs` | 语料文件或目录 |
| `--strategy heading\|fixed` | 按标题切 / 固定滑窗切 |
| `--max-chars 700` | 单块最大字数 |
| `--overlap 120` | 相邻块重叠字数 |
| `--retriever bm25\|bigram` | BM25 / 中文二元组重合度 |
| `--k 5` | 取前 k 个块 |
| `--golden path` | golden set 路径 |
| `--limit N` | 只跑前 N 条，方便快速试 |

## 指标怎么读

- **Recall@k**：至少一个 gold 块出现在前 k 个结果里的比例。回答“能不能找到”。
- **MRR**：第一个 gold 块排名的倒数。回答“找到得有多靠前”。
- **Context@k**：前 k 个结果里有多少是 gold。回答“上下文有多干净”。
- **Recall@20**：gold 是否进入候选池。用来区分召回失败和排序失败。
- **不可答样本最高分均值**：负样本的检索分数。后面做拒答阈值时可以参考。

## 四层失败归因

每个问题跑完，按顺序判断：

1. gold 块在 top-20 里吗？→ 不在 = **召回失败**
2. 在 top-k 里吗？→ 不在 = **排序失败**
3. 答案引用了它吗？→ 没有 = **上下文 / 生成失败**
4. 答案本身对吗？→ 不对 = **理解或推理失败**

`eval` 默认只打印失败项，并给出每条的 gold 首次命中排名和 top-1 结果。
用 `--verbose` 可以看全部。

## 建议的实验顺序

每次只改一个变量，记录前后指标：

1. **切块**：`--max-chars 300 / 700 / 1000`，看 Recall@5
2. **检索器**：`--retriever bigram` 对比 `bm25`
3. **切法**：`--strategy fixed` 对比 `heading`
4. **查询改写**：在 `ask` 前用模型改写问题，再对比 Recall@5
5. **重排**：先取 top-20，再用模型重排到 top-5
6. **上下文组织**：把 gold 块放在开头 / 中间 / 结尾，观察答案命中率
7. **引用校验**：要求模型返回 quote，后端验证 quote 是否在来源正文中
8. **拒答**：用不可答问题测误答率，调整阈值和提示词

## 添加 golden 问题

`golden.jsonl` 每行一个 JSON。可答问题需要标注 `file` 和 `contains`
（`contains` 是原文里的一段短文本），评估时按这两个字段解析出 gold 块：

```json
{"id":"arch-13","question":"...","answerable":true,
 "gold":[{"file":"docs/architecture.md","contains":"原文里的一句短句"}],
 "points":["标准答案要点"]}
```

不可答问题：

```json
{"id":"neg-09","question":"...","answerable":false,"gold":[]}
```

如果 `eval` 提示某个标注的 gold 没找到，先检查 `contains` 是否和原文完全一致；
切块时如果这句刚好跨块，把 `contains` 改短一点。

## 下一步

这份实验台刻意没有引入向量检索和外部依赖。等 BM25 基线稳定后，再加：

- **向量检索**：`fastembed` + `bge-small-zh`，或一个 OpenAI 兼容的 embedding 接口
- **混合检索**：BM25 和向量分数归一化后加权
- **重排**：top-20 交给模型或 cross-encoder 重排
- **生成评估**：LLM-as-judge 打分，加上人工抽查

这些找到的结论，可以直接迁回 `web_search.py`：查询改写、多路检索合并、
上下文组织、引用校验和拒答策略，都是联网搜索能用的；切块和本地向量检索
因为是动态网页，不能直接迁移。
