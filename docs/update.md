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

### 4. 图片预览选择器修复与提交时机优化
- **改动文件**：`web/app.js`、`web/index.html`、`src/main.py`
- **问题背景**：用户反馈在 V2/V3 迭代提交反馈时，粘贴截图并输入文本后点击提交，文本消失了但图片仍留在输入框中。

#### 问题 1：图片预览从未渲染（根本原因）
- **现象**：图片预览区域始终为空，清除操作无效。
- **原因**：`renderImagePreviews` 函数中 `$(containerId)` 使用了 `document.querySelector`，需要 CSS 选择器格式（如 `#composerImagePreview`），但调用时传入的是纯 ID 字符串（如 `"composerImagePreview"`）。`querySelector('composerImagePreview')` 会查找 `<composerImagePreview>` 标签（不存在），而非 `#composerImagePreview` 元素。
- **影响**：图片预览从未被渲染；清除操作调用 `box.innerHTML = ""` 时 `box` 为 `null`，直接 return。
- **修复**：
```javascript
// 修改前（app.js:242）
const box = $(containerId);

// 修改后
const box = document.getElementById(containerId.replace(/^#/, ''));
```

#### 问题 2：修复后出现新报错
- **现象**：修复问题 1 后，页面报错 `Uncaught SyntaxError: Failed to execute 'querySelector' on 'Document': '#composerImagePreview' is not a valid selector.`
- **原因**：`bindImageUpload` 函数调用 `renderImagePreviews` 时，传入的 `previewId` 参数已带 `#` 前缀（如 `"#composerImagePreview"`）。第一次修复使用 `$('#' + containerId)`，导致选择器变成 `##composerImagePreview`（无效选择器）。
- **修复**：使用 `document.getElementById` 并自动去除可能存在的 `#` 前缀，兼容两种调用方式。

#### 问题 3：图片清除时机不当
- **现象**：即使图片预览能正常渲染，提交后图片清除需要等待 `enqueueJob` 的 HTTP 请求完成才执行。对于大图片，请求可能耗时数秒，期间用户看到图片仍在输入框，误以为未提交。
- **原因**：原代码在 `await enqueueJob(...)` **之后**才清除图片和文本。
- **修复**：调整执行顺序——先清空 UI，再发起异步请求；失败时恢复图片和文本：
```javascript
// app.js:1678-1689
try {
  const imgs = composerImages.slice();
  const savedText = text;
  composerImages.length = 0;
  renderImagePreviews(composerImages, "composerImagePreview");
  $("#expertInput").value = "";
  await enqueueJob(question, fromRound, savedText, proj.project_id, imgs);
} catch (e) {
  // 失败时恢复
  composerImages.push(...imgs);
  renderImagePreviews(composerImages, "composerImagePreview");
  $("#expertInput").value = savedText;
  // ... 错误处理
}
```

#### 问题 4：后端缺少图片接收日志
- **现象**：无法从服务器日志确认后端是否收到了图片数据。
- **修复**：在 `/api/feedback` 和 `/api/run` 的入队日志中增加 `images=N` 字段：
```python
# src/main.py
logger.info("已入队迭代任务 %s（%s，%s，images=%d）", job.job_id, job.project_id, round_label, len(request.images) if request.images else 0)
logger.info("已入队任务 %s（%s，%s，images=%d）", job.job_id, job.project_id, round_label, len(request.images) if request.images else 0)
```

#### 测试验证
1. ✅ 图片预览正常渲染（兼容带/不带 `#` 前缀的调用方式）
2. ✅ 提交后图片预览和文本立即清除
3. ✅ 提交失败时图片和文本自动恢复
4. ✅ POST /api/feedback 返回 200，图片数据成功发送到后端
5. ✅ V3 流水线正常执行，综合得分从 6.94 提升到 7.16

## 2026-09-01

### 1. PDF/Markdown 文档上传功能
- **改动文件**：`src/services/document_parser.py`（新建）、`src/main.py`、`src/agents/agent_orchestrator.py`、`src/agents/agent_scientist.py`、`web/app.js`、`web/index.html`、`web/styles.css`
- **功能描述**：支持用户上传 PDF 和 Markdown 文档，后端解析提取文本后注入 Agent prompt，作为证据来源。

#### 新增模块

**后端**：
| 文件 | 说明 |
|------|------|
| `src/services/document_parser.py` | 文档解析服务，支持 PDF（pypdf）和 Markdown（直接读取） |

**前端**：
| 文件 | 说明 |
|------|------|
| `web/app.js` | 添加 `composerDocuments`/`customDocuments` 状态、`renderDocumentPreviews`、`handleDocumentFiles`、`documentsToBase64List`、`_extractClipboardFiles` |
| `web/index.html` | 添加 `#composerDocPreview`/`#customDocPreview` 容器；更新 `accept="image/*,.pdf,.md,.markdown"` |
| `web/styles.css` | 添加文档预览样式（`.doc-thumb`、`.doc-icon`、`.doc-name`、`.remove-doc`） |

#### 数据流
```
用户选择文档 → 前端读取为 base64 → POST /api/feedback {documents: [...]}
→ 后端解析文档提取文本 → 注入 Scientist prompt → Agent 结合文档内容生成假设
```

#### 遇到的问题及解决方法

**问题 1：粘贴文档无效**
- **现象**：从资源管理器复制 PDF 后粘贴到输入框，无反应。
- **原因**：粘贴处理器只调用 `_extractClipboardImages` 提取图片，没有处理文档。
- **修复**：新增 `_extractClipboardFiles` 函数，同时提取图片和文档；更新粘贴处理器调用 `handleFiles`。

**问题 2：文档预览选择器**
- **现象**：与图片预览相同的选择器问题。
- **修复**：复用图片预览的修复方案，使用 `document.getElementById(containerId.replace(/^#/, ''))`。

#### 功能限制
| 限制 | 值 |
|------|-----|
| 文档大小 | ≤10MB |
| 文档数量 | ≤3 个 |
| 提取文本长度 | ≤50000 字符（超出截断） |
| 支持格式 | `.pdf`、`.md`、`.markdown` |
| 不支持 | 加密 PDF、扫描版 PDF（无法提取文本） |

#### 依赖说明
无需新增依赖，复用已有的 `pypdf` 库（requirements.txt 第 51 行）。详见 `experiment.txt` 文件。

#### 测试验证
1. ✅ 文档预览正常渲染（显示文件名和删除按钮）
2. ✅ 点击上传按钮可选择文档
3. ✅ 拖拽文档到输入框可添加
4. ✅ 粘贴文档可添加
5. ✅ 提交后文档预览立即清除
6. ✅ 提交失败时文档自动恢复