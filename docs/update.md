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