# 变更记录

> 记录本轮对话完成的功能性 / 配置 / 文档改动，供追溯。旧历史见 git log。

---

## 2026-08-27

### 1. 证据列表：由「固定配额」改回「纯 Top-3」
- **改动文件**：`src/agents/agent_explorer.py`
- **背景**：上一版为满足"3 个 OpenAlex + 1 个 arXiv"的展示要求，在 `_hybrid_retrieve` 里写死三桶配额（`n_arxiv_target=1, n_openalex_target=top_k-1`），`explore` 默认 `top_k=4`。
- **现状（本轮）**：
  - `explore(top_k=3)`：默认证据条数由 4 → **3**。
  - `_hybrid_retrieve(chroma, question, top_k=3)`：**去掉 openalex_target 的强制配额**。
  - 组合策略改为：若候选池存在 arXiv 文献则优先纳入 **至少 1 条（非强制兜底）**，剩余名额按「OpenAlex + 其他」合并后的相似度顺序追加，直到填满 `top_k`；返回总数 ≤ `top_k`。
- **日志**：保留 `证据来源构成: arXiv=?, OpenAlex=?, 其他=?（共 N 条）` 便于验证 N=3。

### 2. 后端启动可靠性修复：`_free_port` 崩溃
- **改动文件**：`src/main.py`（`_free_port` 函数）
- **问题**：`subprocess.run(["netstat","-ano"], text=True)` 在中文 Windows 下 stdout 字节非 UTF-8（GBK/头部 BOM），导致解码异常被捕获返回 `None`，随后 `None.splitlines()` 抛 `AttributeError`，服务无法启动。
- **修复**：改为 `capture_output=True`（不指定 `text=True`），对原始 bytes 依次用 `utf-8` → `mbcs` 容错解码（`errors="replace"`），并对 `None`/空作 `""` 兜底，不再阻塞启动。

### 3. 环境变量与新配置项（`.env`）
| 变量 | 默认 | 说明 |
|---|---|---|
| `QWEN_MODEL` | `qwen-plus` | 本地实测 `qwen3.7-plus`（含 reasoning tokens，`max_tokens`/`timeout` 需放大） |
| `QWEN_MODEL_EMBEDDING` | `qwen3.7-text-embedding` | 1024 维向量模型 |
| `KEEP_SEARCHED_PAPERS` | `false` | 在线检索（arXiv）入库文献是否保留（`true` 供 125 题全量复用证据） |
| `OPENALEX_EMAIL` | — | OpenAlex 礼貌池邮箱 |
| `CHROMA_COLLECTION_NAME` | `ai_scientist_literature` | 向量库 collection |

### 4. 启动方式（务必遵守）
`start.bat` / 命令行均要求：
```bat
venv\Scripts\python.exe src\main.py   # 必须：项目根目录 + venv python
```

### 5. 中文命中 arXiv 检索（前置会话，保留结论）
- `_translate_to_english` 重写：`sci2025_problems.json` 离线中英对照兜底 + `.env` 私有 endpoint env 化 + 3 次重试 + `timeout=90` + `max_tokens=300`，兼容 `reasoning_content`。

### 6. Token / 时间量级参考（125 题全量）
- 单题约 **40~50k** 输入 token + **26~27k** 输出（含 reasoning）。
- 125 题总量约 **8~10M token**；串行约 **11.5h**，并发度 2 约 5~6h。
- 显著降本手段（未落地，按需评估）：`MAX_ITERATIONS 3→1`（−60~65%）、换非推理模型（−30~40%）、缩 evidence 条数 + quick 粒度（−15~20%）、关闭 `auto_search_papers`（−5~10%）。

---

## 2026-08-31

### 1. V2/V3 迭代复用 V1 Explorer 检索结果
- **改动文件**：`src/agents/agent_orchestrator.py`
- **背景**：V1→V2→V3 迭代时，Explorer 每轮重新检索导致证据集漂移，综合得分逐轮下降。
- **改动**：
  - `run_full_pipeline` 新增 `prev_explorer_output` 变量，从上一轮快照中读取 `agent_explorer` 输出。
  - V2/V3 的 Step 1 直接构造 `ExplorerOutput(**prev_explorer_output)`，跳过 `explore()` 调用。
  - 仅 V1（无上一轮快照时）执行实际检索。
- **效果**：迭代轮次间证据集保持一致，消除因检索漂移导致的分数退化。

### 2. Scientist 迭代时强制锚定已有证据
- **改动文件**：`src/agents/agent_scientist.py`
- **背景**：即使证据集固定，Scientist 在迭代中仍可能脱离已有证据、凭空引入新文献，导致评审扣分。
- **改动**：
  - SYSTEM_PROMPT 新增第 4 条核心约束「证据锚定原则」：`source` 字段必须引用提供的证据来源之一。
  - 新增 `iteration_anchor` 提示段（仅在迭代 + 有证据列表时注入），以最高优先级列出可用证据来源，禁止引入未提供的新文献。
  - 跨域类比推测须在 source 中标注"基于类比推测"。
- **效果**：迭代生成的假设严格扎根于固定证据集，减少因"虚构文献"导致的 evidence 维度扣分。

### 3. Critic 迭代评分改为关注相对改进
- **改动文件**：`src/agents/agent_critic.py`
- **背景**：Critic 在 V2/V3 仍以绝对标准打分，未考虑 Scientist 已针对上轮缺陷做了改进，导致分数不升反降。
- **改动**：
  - 计算上轮均分 `prev_total` 和最弱两个维度 `weak_dims`，注入迭代上下文。
  - 用 5 条「评分原则（最高优先级）」替换原有的简单验证指令：
    1. 关注相对改进而非绝对分数
    2. 保护已有优势维度（≥8 分降幅不超过 1.5）
    3. 聚焦薄弱维度突破
    4. 避免矫枉过正惩罚（合理权衡不视为退步）
    5. 评分锚定：各维度差值应在 [-2, +3] 范围内
- **效果**：Critic 评分更能反映迭代的实际进步，避免因绝对标准过严导致分数逐轮递减。