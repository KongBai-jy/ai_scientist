"""
在线文献检索服务
=================
基于 arXiv API 检索，结果可直接塞入 Chroma 向量库。

arXiv API 官方文档: https://info.arxiv.org/help/api/index.html

使用示例:
    from services.paper_search_service import PaperSearchService
    svc = PaperSearchService()
    papers = svc.search("causal inference", max_results=5)
    svc.ingest(papers)  # 写入 Chroma
"""

import base64
import logging
import os
import re
import time
import threading
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import List, Dict, Optional, TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    # 仅类型注解用，运行时不导入（避免 dry-run 时硬依赖 langchain_chroma）
    from services.chroma_service import ChromaService

logger = logging.getLogger(__name__)

# arXiv Atom 命名空间
_ATOM_NS = "{http://www.w3.org/2005/Atom}"
_ARXIV_NS = "{http://arxiv.org/schemas/atom}"

# ── 官方 ToU：单客户端 4 秒 1 次，串行请求 ──
# 这里用 4.2s 偏保守的间隔；连续两次研究触发时也不会撞 429
_ARXIV_API = "https://export.arxiv.org/api/query"
_ARXIV_MIN_INTERVAL_SEC = 4.2
# 429 / 5xx 重试：最多 3 次，退避 2s -> 4s -> 8s（上限 10s）
_ARXIV_MAX_RETRIES = 3
_ARXIV_BASE_BACKOFF_SEC = 2.0
_ARXIV_MAX_BACKOFF_SEC = 10.0
# 中文翻译关键词上限：避免过长 query 触发 arXiv 严格配额
_MAX_ARXIV_QUERY_TERMS = 4

# 全局限速锁（进程内统一节流，即便多 Pipeline 并发也不会击穿）
_rate_lock = threading.Lock()
_last_request_ts: float = 0.0


@dataclass
class Paper:
    """统一论文数据结构"""
    title: str
    abstract: str
    year: str = ""
    authors: List[str] = field(default_factory=list)
    source: str = ""           # 引用来源字符串（供 Explorer evidence.source 使用）
    doi: str = ""
    arxiv_id: str = ""
    url: str = ""
    field: str = ""

    def to_chroma_document(self) -> Dict:
        """转换为 Chroma 文档格式（content + metadata）"""
        content = f"{self.title}. {self.abstract}".strip()
        metadata = {
            "source": self.source or "未知",
            "year": self.year,
            "field": self.field,
            "doi": self.doi,
            "arxiv_id": self.arxiv_id,
            "url": self.url,
            "title": self.title,
        }
        # 移除空值，避免 Chroma metadata 出现空字符串
        metadata = {k: v for k, v in metadata.items() if v}
        return {"content": content, "metadata": metadata}


class PaperSearchService:
    """在线文献检索 + 入库服务（仅基于 arXiv API）"""

    def __init__(self, chroma: Optional["ChromaService"] = None):
        # ChromaService 懒加载：dry-run（仅 search 不 ingest）不需要 langchain_chroma
        self._chroma = chroma
        # 20s 超时：arXiv 偶尔慢；已经用 https 直连，不再依赖 301 跳转
        # HTTPTransport(1) 内置 1 次网络层重试；应用层再对 429/5xx 做退避
        transport = httpx.HTTPTransport(retries=1)
        self.http = httpx.Client(
            timeout=20.0,
            follow_redirects=False,
            transport=transport,
            headers={
                "User-Agent": "AI-Scientist/1.0 (arXiv API client; please throttle)",
                "Accept": "application/atom+xml, application/xml;q=0.9, */*;q=0.8",
            },
        )

        # 多模态（视觉）图注开关：默认关，需在 .env 配置 QWEN_VL_MODEL 且 PDF_VISION=true
        self._enable_vision = os.getenv("PDF_VISION", "").strip().lower() == "true"
        self._vision = None
        if self._enable_vision:
            self._vision = self._vision_service()

    @property
    def chroma(self) -> "ChromaService":
        """ChromaService 懒加载：首次访问时才实例化（仅 ingest 时需要）"""
        if self._chroma is None:
            from services.chroma_service import ChromaService  # 运行时导入
            self._chroma = ChromaService()
        return self._chroma

    # ============================================================
    # 公共接口
    # ============================================================

    def search(self, query: str, max_results: int = 5) -> List[Paper]:
        """
        在线检索论文（基于 arXiv API）

        Args:
            query: 搜索关键词（英文效果最佳；若为中文，将自动翻译为英文关键词）
            max_results: 最大返回数
        """
        prepared = self._prepare_query(query)
        if prepared != query:
            logger.info(f"query 预处理: {query!r} → {prepared!r}")
        try:
            return self._arxiv_search(prepared, max_results)
        except Exception as e:
            logger.warning(f"arXiv 检索失败: {e}")
            return []

    def ingest(self, papers: List[Paper], dedupe: bool = True) -> int:
        """
        将检索结果写入 Chroma 向量库

        Args:
            papers: 检索到的论文列表
            dedupe: 是否基于 arxiv_id/doi 去重（避免重复塞入相同论文）

        Returns:
            实际写入的文档数
        """
        if not papers:
            return 0

        if dedupe:
            existing = self._fetch_existing_ids()
            papers = [
                p for p in papers
                if not (p.arxiv_id and p.arxiv_id in existing.get("arxiv", set()))
                and not (p.doi and p.doi in existing.get("doi", set()))
            ]
            if not papers:
                logger.info("所有论文已存在，跳过写入")
                return 0

        docs = [p.to_chroma_document() for p in papers]
        self.chroma.add_documents(
            texts=[d["content"] for d in docs],
            metadatas=[d["metadata"] for d in docs],
        )
        logger.info(f"✅ 成功写入 {len(papers)} 篇论文到 Chroma")
        return len(papers)

    def search_and_ingest(
        self,
        query: str,
        max_results: int = 5,
        dedupe: bool = True,
        full_text: bool = False,
    ) -> Dict:
        """
        一站式：检索 + 入库

        Args:
            query: 搜索关键词
            max_results: 最大返回数
            dedupe: 是否基于 arxiv_id/doi 去重
            full_text: 是否下载 PDF 全文并切分入库（True 时每篇分多个 chunk，
                       False 时只入库 title + abstract）
        """
        papers = self.search(query, max_results=max_results)
        if full_text:
            ingested, ingested_papers = self.ingest_pdf(papers, dedupe=dedupe)
            # PDF 模式：ingested_papers 用 mode="skipped" 标记去重的论文
            processed = sum(1 for p in ingested_papers if p.get("mode") != "skipped")
            skipped = len(papers) - processed
            mode = "pdf"
        else:
            ingested = self.ingest(papers, dedupe=dedupe)
            # abstract 模式：ingest 内部去重，ingested 是实际入库数
            skipped = len(papers) - ingested
            ingested_papers = [
                {
                    "title": p.title, "year": p.year, "source": p.source,
                    "arxiv_id": p.arxiv_id, "doi": p.doi, "url": p.url,
                    "mode": "abstract",
                }
                for p in papers
            ]
            mode = "abstract"

        return {
            "query": query,
            "retrieved": len(papers),
            "ingested": ingested,  # PDF 模式下是 chunk 总数，abstract 模式下是论文数
            "skipped": skipped,
            "mode": mode,
            "papers": ingested_papers,
        }

    # ============================================================
    # PDF 全文入库（扩展能力）
    # ============================================================

    def _download_pdf(self, arxiv_id: str) -> Optional[bytes]:
        """下载 arXiv PDF 字节流（自动剥离版本号 vN 后缀）"""
        if not arxiv_id:
            return None
        # arxiv_id 形如 2302.08893v4，PDF URL 不带版本号
        clean_id = arxiv_id.split("v")[0] if re.match(r".*v\d+$", arxiv_id) else arxiv_id
        url = f"https://arxiv.org/pdf/{clean_id}.pdf"
        try:
            resp = self.http.get(url, timeout=30)
            resp.raise_for_status()
            return resp.content
        except Exception as e:
            logger.warning(f"下载 PDF 失败 {arxiv_id}: {e}")
            return None

    def _extract_pdf_text(self, content: bytes) -> str:
        """从 PDF 字节流提取全文（需 pypdf）"""
        try:
            from pypdf import PdfReader
        except ImportError as e:
            raise ImportError("PDF 全文入库需要 pypdf 库: pip install pypdf") from e

        from io import BytesIO
        reader = PdfReader(BytesIO(content))
        text = "\n\n".join(page.extract_text() or "" for page in reader.pages)
        return text.strip()

    # ============================================================
    # 多模态（视觉图注）：按页抽取 → 图描述并入该页文本
    # ============================================================

    @staticmethod
    def _vision_service():
        """懒构建 qwen-vl 视觉描述器。优先使用 VL 专用 key，缺省回退主 key。

        返回带 caption_images(bytes列表)->List[str] 的对象；未配置 key 时返回 None。
        """
        try:
            from langchain_openai import ChatOpenAI
        except ImportError as e:
            logger.warning(f"视觉图注不可用（langchain_openai 未安装）: {e}")
            return None
        model = os.getenv("QWEN_VL_MODEL", "qwen-vl-max")
        api_key = os.getenv("DASHSCOPE_API_KEY_VL") or os.getenv("DASHSCOPE_API_KEY")
        if not model or not api_key:
            logger.warning("视觉图注未启用：缺少 QWEN_VL_MODEL 或 DASHSCOPE_API_KEY(_VL)")
            return None
        base_url = os.getenv("DASHSCOPE_API_BASE_VL") or os.getenv("DASHSCOPE_API_BASE", "https://dashscope.aliyuncs.com/compatible-mode/v1")
        llm = ChatOpenAI(
            model=model,
            api_key=api_key,
            base_url=base_url,
            temperature=0.2,
            max_tokens=512,
            timeout=45.0,
        )

        def caption_images(images: List[bytes], max_workers: int = 3) -> List[str]:
            """并发把每张 PNG 转成描述文本；单张失败返回空串，互不影响。"""
            if not images:
                return []

            def _one(img: bytes) -> str:
                try:
                    b64 = base64.b64encode(img).decode("ascii")
                    msg = [
                        {
                            "role": "user",
                            "content": [
                                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
                                {"type": "text", "text": (
                                    "这是论文中的一张图。请用中文简要描述：1) 图中内容与类型；"
                                    "2) 坐标轴/标注文字/图例等关键文字；3) 反映的主要结论或关系。300字内。"
                                )},
                            ],
                        }
                    ]
                    resp = llm.invoke(msg)
                    return (resp.content or "").strip()
                except Exception as e:
                    logger.warning(f"单图描述失败: {e}")
                    return ""

            with ThreadPoolExecutor(max_workers=max_workers) as ex:
                return list(ex.map(_one, images))

        return type("_VisionService", (), {"caption_images": staticmethod(caption_images)})()

    @staticmethod
    def _pil_to_png_bytes(pil_img) -> Optional[bytes]:
        """PIL.Image → PNG 字节（需 Pillow）"""
        try:
            from io import BytesIO
            buf = BytesIO()
            pil_img = pil_img.convert("RGB")
            pil_img.save(buf, format="PNG")
            return buf.getvalue()
        except Exception as e:
            logger.warning(f"图片转 PNG 失败: {e}")
            return None

    def _extract_pages(self, content: bytes) -> List[Dict]:
        """按页抽取。返回 [{"text", "images"[png bytes], "page_no"}, ...]。

        过滤面积过大的图（常是扫描整页），避免把整页当"论文插图"送视觉。
        """
        try:
            from pypdf import PdfReader
        except ImportError as e:
            raise ImportError("PDF 全文入库需要 pypdf 库: pip install pypdf") from e

        from io import BytesIO
        reader = PdfReader(BytesIO(content))
        pages: List[Dict] = []
        for i, page in enumerate(reader.pages):
            text = page.extract_text() or ""
            imgs: List[bytes] = []
            for img in getattr(page, "images", []):
                try:
                    pil = img.image  # pypdf → PIL.Image（需 Pillow）
                    # 面积阈值：约 2000x1000 以上视为整页/大图，跳过
                    if pil is None or (pil.size[0] * pil.size[1]) > 2_000_000:
                        continue
                    png = self._pil_to_png_bytes(pil)
                    if png:
                        imgs.append(png)
                except Exception:
                    continue
            pages.append({"text": text.strip(), "images": imgs, "page_no": i + 1})
        return pages

    def _merge_figure_descriptions(self, pages: List[Dict], vision=None) -> tuple:
        """把每页图描述并入该页正文末尾，返回 (合并全文, 图注条数)。

        视觉关闭或无图时退化为纯文本合并（等价原 _extract_pdf_text）。
        """
        if not self._enable_vision or not vision:
            return "\n\n".join(p["text"] for p in pages if p["text"]), 0

        merged: List[str] = []
        fig_count = 0
        for p in pages:
            block = p["text"]
            if p["images"]:
                descs = vision.caption_images(p["images"])
                valid = [d for d in descs if d]
                if valid:
                    fig_note = "\n\n[本页图注] " + "；".join(valid)
                    block = (block + fig_note) if block else fig_note
                    fig_count += len(valid)
            if block:
                merged.append(block)
        return "\n\n".join(merged), fig_count

    def _chunk_text(self, text: str, chunk_size: int = 1000, overlap: int = 100) -> List[str]:
        """长文本切分（优先 langchain_text_splitters，降级为简单切分）"""
        if not text:
            return []
        try:
            from langchain_text_splitters import RecursiveCharacterTextSplitter
            splitter = RecursiveCharacterTextSplitter(
                chunk_size=chunk_size,
                chunk_overlap=overlap,
            )
            return splitter.split_text(text)
        except ImportError:
            # 降级：简单滑动窗口切分
            chunks = []
            for i in range(0, len(text), chunk_size - overlap):
                chunks.append(text[i:i + chunk_size])
            return chunks

    def ingest_pdf(self, papers: List[Paper], dedupe: bool = True) -> tuple:
        """
        对每篇论文下载 PDF 全文 + 切分 + 入库

        Args:
            papers: 检索到的论文列表
            dedupe: 是否按 arxiv_id 去重（已存在则跳过 PDF 下载）

        Returns:
            (总入库 chunk 数, 论文信息列表)
            论文信息: {title, arxiv_id, year, source, chunks, mode, url}
                      mode ∈ {"pdf", "abstract", "skipped"}
        """
        if not papers:
            return 0, []

        existing = self._fetch_existing_ids() if dedupe else {"arxiv": set(), "doi": set()}

        all_chunks: List[Dict] = []
        paper_infos: List[Dict] = []
        total_chunks = 0

        for p in papers:
            # 去重
            if dedupe and (
                (p.arxiv_id and p.arxiv_id in existing.get("arxiv", set()))
                or (p.doi and p.doi in existing.get("doi", set()))
            ):
                paper_infos.append({
                    "title": p.title, "arxiv_id": p.arxiv_id, "year": p.year,
                    "source": p.source, "doi": p.doi, "url": p.url,
                    "chunks": 0, "mode": "skipped",
                })
                continue

            # 下载 PDF + 提取文本（含多模态图注并入邻近文本）
            pdf_bytes = self._download_pdf(p.arxiv_id)
            text = ""
            fig_count = 0
            if pdf_bytes:
                try:
                    pages = self._extract_pages(pdf_bytes)
                    text, fig_count = self._merge_figure_descriptions(pages, self._vision)
                except Exception as e:
                    logger.warning(f"PDF 文本提取失败 {p.arxiv_id}: {e}")
            if fig_count:
                logger.info(f"🧠 {p.arxiv_id} 并入 {fig_count} 条图注描述")

            # PDF 提取失败 → 降级为 abstract
            if not text:
                logger.info(f"使用 abstract 降级入库: {p.arxiv_id}")
                doc = p.to_chroma_document()
                doc["metadata"]["ingest_mode"] = "abstract"
                all_chunks.append(doc)
                total_chunks += 1
                paper_infos.append({
                    "title": p.title, "arxiv_id": p.arxiv_id, "year": p.year,
                    "source": p.source, "doi": p.doi, "url": p.url,
                    "chunks": 1, "mode": "abstract",
                })
                continue

            # 切分 + 入库
            chunks = self._chunk_text(text)
            if not chunks:
                # 切分失败也降级
                doc = p.to_chroma_document()
                doc["metadata"]["ingest_mode"] = "abstract"
                all_chunks.append(doc)
                total_chunks += 1
                paper_infos.append({
                    "title": p.title, "arxiv_id": p.arxiv_id, "year": p.year,
                    "source": p.source, "doi": p.doi, "url": p.url,
                    "chunks": 1, "mode": "abstract",
                })
                continue

            for i, chunk in enumerate(chunks):
                all_chunks.append({
                    "content": chunk,
                    "metadata": {
                        "arxiv_id": p.arxiv_id,
                        "title": p.title,
                        "source": p.source,
                        "year": p.year,
                        "field": "",
                        "doi": p.doi,
                        "chunk_index": i,
                        "total_chunks": len(chunks),
                        "ingest_mode": "pdf",
                    },
                })
            total_chunks += len(chunks)
            paper_infos.append({
                "title": p.title, "arxiv_id": p.arxiv_id, "year": p.year,
                "source": p.source, "doi": p.doi, "url": p.url,
                "chunks": len(chunks), "mode": "pdf",
            })
            logger.info(f"✅ {p.arxiv_id} PDF 入库 {len(chunks)} 个 chunks")

        # 批量写入
        if all_chunks:
            self.chroma.add_documents(
                texts=[d["content"] for d in all_chunks],
                metadatas=[d["metadata"] for d in all_chunks],
            )
            logger.info(f"✅ PDF 模式共入库 {total_chunks} 个 chunks（来自 {len(papers)} 篇论文）")

        return total_chunks, paper_infos

    # ============================================================
    # 本地 PDF 文件入库（不依赖 arXiv API）
    # ============================================================

    def ingest_local_pdf(
        self,
        file_path: str,
        source: Optional[str] = None,
        dedupe: bool = True,
        chunk_size: int = 1200,
        overlap: int = 100,
    ) -> Dict:
        """
        解析本地 PDF 文件并塞入 Chroma 向量库

        Args:
            file_path: 本地 PDF 文件绝对/相对路径
            source: 来源标识（默认用文件名）。用于去重和 Explorer 引用
            dedupe: 是否基于 source 字段去重（已存在则跳过整个文件）
            chunk_size: 切分大小（默认 1200 字符）
            overlap: 切分重叠（默认 100）

        Returns:
            {file, source, total_chars, ingested, skipped, error?}
        """
        import os

        if not os.path.exists(file_path):
            raise FileNotFoundError(f"PDF 文件不存在: {file_path}")

        source = source or os.path.basename(file_path)

        # 去重检查：按 source 字段查现有 metadata
        if dedupe:
            try:
                store = self.chroma.load_or_create()
                existing = store._collection.get(
                    where={"source": source},
                    include=["metadatas"],
                )
                if existing.get("metadatas"):
                    logger.info(f"本地 PDF 已入库（source={source!r}），跳过")
                    return {
                        "file": file_path,
                        "source": source,
                        "total_chars": 0,
                        "ingested": 0,
                        "skipped": True,
                    }
            except Exception as e:
                logger.warning(f"去重检查失败，继续执行: {e}")

        # 1. 读文件 → 字节流
        with open(file_path, "rb") as f:
            pdf_bytes = f.read()

        # 2. 提取全文（含多模态图注并入邻近文本）
        fig_count = 0
        try:
            pages = self._extract_pages(pdf_bytes)
            text, fig_count = self._merge_figure_descriptions(pages, self._vision)
        except Exception as e:
            return {
                "file": file_path,
                "source": source,
                "total_chars": 0,
                "ingested": 0,
                "skipped": False,
                "error": f"PDF 解析失败: {e}",
            }
        if fig_count:
            logger.info(f"🧠 本地 PDF 并入 {fig_count} 条图注描述: {source}")

        if not text:
            return {
                "file": file_path,
                "source": source,
                "total_chars": 0,
                "ingested": 0,
                "skipped": False,
                "error": "PDF 解析为空（可能是扫描版/加密 PDF）",
            }

        # 3. 切分（用 chunk_size=1200）
        chunks = self._chunk_text(text, chunk_size=chunk_size, overlap=overlap)
        if not chunks:
            return {
                "file": file_path,
                "source": source,
                "total_chars": len(text),
                "ingested": 0,
                "skipped": False,
                "error": "切分为空",
            }

        # 4. 构造 docs
        docs = [
            {
                "content": chunk,
                "metadata": {
                    "source": source,
                    "file_path": file_path,
                    "chunk_index": i,
                    "total_chunks": len(chunks),
                    "ingest_mode": "local_pdf",
                },
            }
            for i, chunk in enumerate(chunks)
        ]

        # 5. 批量入库
        self.chroma.add_documents(
            texts=[d["content"] for d in docs],
            metadatas=[d["metadata"] for d in docs],
        )

        logger.info(
            f"✅ 本地 PDF 入库: {source} → {len(chunks)} chunks ({len(text)} 字符)"
        )

        return {
            "file": file_path,
            "source": source,
            "total_chars": len(text),
            "ingested": len(chunks),
            "figures": fig_count,
            "skipped": False,
        }

    # ============================================================
    # 离线采集 JSON 入库（OpenAlex 元数据 → Chroma 检索索引）
    # ============================================================

    def ingest_openalex_json(
        self,
        file_path: str,
        dedupe: bool = True,
        chunk_size: int = 1200,
        overlap: int = 100,
    ) -> Dict:
        """
        读取离线采集脚本（scripts/collect_openalex.py）生成的 JSON，
        把每篇论文的「标题 + 摘要」切块后塞入 Chroma，作为 Explorer 的检索索引。

        注意：JSON 本身才是永久存储（保留在 papers/），Chroma 只是从它派生的
        检索缓存，可随时从 JSON 无损重建，因此不把 Chroma 当作唯一持久层。

        Args:
            file_path: 单个 JSON 文件，或包含多个 JSON 的目录（递归扫描 *.json）
            dedupe: 是否按 openalex_id 去重（已存在则跳过该篇）
            chunk_size / overlap: 切分参数（摘要通常较短，多为 1 个 chunk）

        Returns:
            {files, papers_processed, papers_skipped, chunks_ingested, skipped?, error?}
        """
        import os
        import glob
        import json

        path = os.path.abspath(file_path)
        if os.path.isdir(path):
            json_files = sorted(glob.glob(os.path.join(path, "**", "*.json"), recursive=True))
        elif os.path.exists(path):
            json_files = [path]
        else:
            raise FileNotFoundError(f"JSON 路径不存在: {file_path}")

        # 去重索引：一次取出现有全部 openalex_id
        existing_ids = set()
        if dedupe:
            try:
                store = self.chroma.load_or_create()
                got = store._collection.get(include=["metadatas"])
                for m in (got.get("metadatas") or []):
                    oid = m.get("openalex_id")
                    if oid:
                        existing_ids.add(oid)
            except Exception as e:
                logger.warning(f"去重索引读取失败，如需去重请重试: {e}")

        all_chunks: List[Dict] = []
        papers_processed = 0
        papers_skipped = 0

        for jf in json_files:
            with open(jf, encoding="utf-8") as f:
                try:
                    data = json.load(f)
                except Exception as e:
                    logger.warning(f"JSON 解析失败，跳过 {jf}: {e}")
                    continue

            category = data.get("category", "")
            problem_id = data.get("problem_id")
            for paper in data.get("papers", []):
                oid = paper.get("openalex_id") or paper.get("doi")
                if not oid:
                    continue
                if dedupe and oid in existing_ids:
                    papers_skipped += 1
                    continue

                title = (paper.get("title") or "").strip()
                abstract = (paper.get("abstract") or "").strip()
                text = "\n\n".join(t for t in (title, abstract) if t)
                if not text:
                    continue

                chunks = self._chunk_text(text, chunk_size=chunk_size, overlap=overlap)
                if not chunks:
                    continue

                for i, chunk in enumerate(chunks):
                    meta = {
                        "source": "openalex",
                        "ingest_mode": "openalex_json",
                        "openalex_id": paper.get("openalex_id"),
                        "doi": paper.get("doi"),
                        "title": title,
                        "year": paper.get("year"),
                        "cited_by_count": paper.get("cited_by_count"),
                        "problem_id": problem_id,
                        "category": category,
                        "file_path": jf,
                        "chunk_index": i,
                        "total_chunks": len(chunks),
                    }
                    # Chroma metadata 不接受 None，过滤掉空值字段
                    meta = {k: v for k, v in meta.items() if v is not None}
                    all_chunks.append({"content": chunk, "metadata": meta})

                existing_ids.add(oid)
                papers_processed += 1

        if all_chunks:
            self.chroma.add_documents(
                texts=[d["content"] for d in all_chunks],
                metadatas=[d["metadata"] for d in all_chunks],
            )
            logger.info(
                f"✅ OpenAlex JSON 入库 {len(all_chunks)} chunks "
                f"（{papers_processed} 篇，跳过 {papers_skipped} 篇）"
            )

        return {
            "files": len(json_files),
            "papers_processed": papers_processed,
            "papers_skipped": papers_skipped,
            "chunks_ingested": len(all_chunks),
            "skipped": bool(papers_skipped > 0 and papers_processed == 0),
        }

    # ============================================================
    # Query 预处理（中文 → 英文关键词 / 字符清洗）
    # ============================================================

    @staticmethod
    def _prepare_query(query: str) -> str:
        """将 query 转为 arXiv 可识别的英文关键词

        处理策略：
          1) 纯 ASCII 长 query → 清洗 + 统一限词（避免过长触发 429）
          2) 含中文 → 优先用 DashScope 翻译为英文关键词；失败时降级清洗
          3) 出口统一限词（_MAX_ARXIV_QUERY_TERMS 个核心 term）
        """
        q = (query or "").strip()
        if not q:
            return q

        if all(ord(c) < 128 for c in q):
            # 纯 ASCII：只做轻清洗 + 出口限词（不再直接放行长 query）
            cleaned = re.sub(r"[^\w\s\-+:().,?]", " ", q)
            cleaned = re.sub(r"\s+", " ", cleaned).strip()
            out = PaperSearchService._truncate_query_terms(cleaned or q, caller="ascii_clean")
            return out

        # 含中文：翻译 → 出口限词
        translated = PaperSearchService._translate_to_english(q)
        if translated and translated != q:
            # 翻译内部已经做过截断，但这里再兜一层以防变体
            return PaperSearchService._truncate_query_terms(translated, caller="translate_out")

        # 降级：清掉非英文字符，保留英文数字部分
        fallback = re.sub(r"[^\w\s\-+:().,?]", " ", q)
        fallback = re.sub(r"\s+", " ", fallback).strip()
        return PaperSearchService._truncate_query_terms(fallback or q, caller="fallback")

    @staticmethod
    def _truncate_query_terms(content: str, caller: str = "prepare") -> str:
        """统一出口：把英文关键词串截断到 ≤_MAX_ARXIV_QUERY_TERMS。

        term 的定义：`[A-Za-z0-9]+(?:['-][A-Za-z0-9]+)*`，
        这样 "high-temperature"、"state-of-the-art"、"don't" 这类都视为 1 个 term，
        不会被拆成两半导致语义丢失。
        """
        if not content:
            return content
        tokens = re.findall(r"[A-Za-z0-9]+(?:[\-'][A-Za-z0-9]+)*", content)
        if not tokens:
            return content
        if len(tokens) > _MAX_ARXIV_QUERY_TERMS:
            logger.info(
                f"[arxiv query @{caller}] 关键词过多 ({len(tokens)} terms)，"
                f"截断为前 {_MAX_ARXIV_QUERY_TERMS} 条: "
                f"{' '.join(tokens[:_MAX_ARXIV_QUERY_TERMS])!r}"
            )
            tokens = tokens[:_MAX_ARXIV_QUERY_TERMS]
        return " ".join(tokens)

    @staticmethod
    def _translate_to_english(text: str) -> Optional[str]:
        """调用 DashScope（或系统 LLM）将中文 query 翻译为 3-6 个英文关键词。

        多层兜底保证中文仍能产出英文 query：
          1) 先用 sci2025_problems.json 的「中文-英文」离线对照做精确匹配（快、稳）；
          2) 匹配不到才调用 DashScope 翻译（qwen3.5-plus 等推理模型响应偶发超时，
             故对本次调用做最多 MAX_ATTEMPTS 次重试，并兼容 reasoning_content）；
          3) 仍失败则返回 None，由上层走原有降级。
        """
        import os
        from dotenv import load_dotenv
        from pathlib import Path

        # 加载项目根目录 .env（如果还没加载）
        _PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
        load_dotenv(_PROJECT_ROOT / ".env", override=False)

        def _normalize(s: str) -> str:
            return re.sub(r"[^\w\u4e00-\u9fff]+", "", (s or "")).lower()

        # ===== 1) 离线中英对照兜底 =====
        try:
            problems_path = _PROJECT_ROOT / "data" / "sci2025_problems.json"
            if problems_path.exists():
                import json as _json
                with open(problems_path, "r", encoding="utf-8") as _f:
                    problems = _json.load(_f)
                needle = _normalize(text)
                for p in problems:
                    if _normalize(p.get("cn", "")) == needle and p.get("en"):
                        en = p["en"]
                        # 用英文问题提取关键词 term 做 arXiv query
                        terms = re.findall(r"[a-zA-Z0-9]+(?:[\-'][a-zA-Z0-9]+)*", en)
                        if terms:
                            logger.info(f"离线对照翻译命中: {en!r}")
                            return PaperSearchService._truncate_query_terms(" ".join(terms), caller="offline_match")
        except Exception as _off:
            logger.debug(f"离线对照翻译异常（忽略）: {_off}")

        # ===== 2) LLM 翻译（带重试） =====
        api_key = (
            os.getenv("DASHSCOPE_API_KEY")
            or os.getenv("DASHSCOPE_API_KEY_EMBEDDING")
        )
        if not api_key:
            logger.warning("无 DASHSCOPE_API_KEY，跳过中文翻译")
            return None

        model = os.getenv("QWEN_MODEL", "qwen-plus")
        # 优先使用 .env 配置的 DashScope 私有化/专属 endpoint；
        # 兜底用公网 compatible-mode 域名（部分账号需在专属 workspace 内调用）。
        base = os.getenv("DASHSCOPE_API_BASE", "") or "https://dashscope.aliyuncs.com/compatible-mode/v1"
        base = base.rstrip("/")
        prompt = (
            "将下面的科学研究问题翻译成 3-6 个英文关键词短语，"
            "这些关键词将用于 arXiv 文献检索。\n"
            "要求：只输出关键词，用英文逗号分隔，不要其他文字、不要解释。\n"
            f"问题：{text}"
        )

        MAX_ATTEMPTS = 3
        last_err: Optional[Exception] = None
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                resp = httpx.post(
                    f"{base}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": model,
                        "messages": [{"role": "user", "content": prompt}],
                        "temperature": 0.1,
                        # qwen3.5-plus 等推理模型生成 final 前会先输出大量
                        # reasoning tokens，故放大 max_tokens 与 timeout，否则
                        # content 全被思考占满且 15s 读超时等不到首个输出字节。
                        "max_tokens": 300,
                    },
                    timeout=90,
                )
                resp.raise_for_status()
                data = resp.json()
                message = data.get("choices", [{}])[0].get("message", {}) or {}
                # 兼容推理模型：可从 reasoning_content / content 取文本
                content = (message.get("content") or message.get("reasoning_content") or "").strip()
                if not content:
                    last_err = RuntimeError("翻译返回空内容")
                    continue
                # 清理可能的引号、序号和末尾句号
                content = re.sub(r"^[\d\.\)\s]+", "", content)
                content = re.sub(r"[\"'`]", "", content)
                content = content.rstrip(".。")
                # 将逗号分隔符替换为空格，让 arXiv 把所有关键词作为一个联合 query
                content = re.sub(r"[,，;；]", " ", content)
                content = re.sub(r"\s+", " ", content).strip()
                # 出口限词（避免过长 query 触发 arXiv 严格配额）
                return PaperSearchService._truncate_query_terms(content, caller="translate") or None
            except httpx.TransportError as e:
                last_err = e
                logger.warning(f"中文翻译调用失败（第 {attempt}/{MAX_ATTEMPTS} 次）: {e}")
                continue
            except Exception as e:
                last_err = e
                logger.warning(f"中文翻译异常（第 {attempt}/{MAX_ATTEMPTS} 次）: {e}")
                continue

        logger.warning(f"中文翻译失败（将使用降级策略）: {last_err}")
        return None

    # ============================================================
    # arXiv 检索
    # ============================================================

    def _arxiv_search(self, query: str, max_results: int) -> List[Paper]:
        """调用 arXiv API（返回 Atom XML）

        附带 ToU 合规机制：
          1) 全局 4.2s 最小请求间隔（跨所有 PaperSearchService 实例共享）
          2) 遇到 429 / 5xx 时指数退避重试最多 3 次
        """
        params = {
            "search_query": f"all:{query}",
            "start": 0,
            "max_results": max_results,
            "sortBy": "relevance",
        }
        logger.info(f"arXiv 检索: {query!r} (max={max_results})")

        last_exc: Optional[Exception] = None
        for attempt in range(1, _ARXIV_MAX_RETRIES + 1):
            # ── 节流：满足 arXiv ToU "单客户端 ≥4 秒 / 次" ──
            PaperSearchService._throttle()
            try:
                resp = self.http.get(_ARXIV_API, params=params)
                if resp.status_code == 429 or resp.status_code >= 500:
                    # 429 / 服务端错误：构造一次 status error 以便打印真实异常文本
                    import httpx as _httpx
                    status_err = _httpx.HTTPStatusError(
                        f"{resp.status_code} {resp.reason_phrase}",
                        request=resp.request,
                        response=resp,
                    )
                    # 最后一次：不再 sleep，直接退出
                    if attempt >= _ARXIV_MAX_RETRIES:
                        last_exc = status_err
                        logger.warning(
                            f"arXiv 返回 {resp.status_code}（第 {attempt}/{_ARXIV_MAX_RETRIES} 次，最后一次），不再重试"
                        )
                        break
                    # 否则指数退避 + 遵守 Retry-After
                    backoff = PaperSearchService._backoff_for(attempt)
                    ra = resp.headers.get("Retry-After")
                    try:
                        if ra and ra.isdigit():
                            backoff = min(float(ra), _ARXIV_MAX_BACKOFF_SEC)
                    except Exception:
                        pass
                    logger.warning(
                        f"arXiv 返回 {resp.status_code}（第 {attempt}/{_ARXIV_MAX_RETRIES} 次），"
                        f"{backoff:.1f}s 后重试: {resp.url}"
                    )
                    last_exc = status_err
                    time.sleep(backoff)
                    continue
                resp.raise_for_status()
                return self._parse_arxiv_atom(resp.text)
            except httpx.HTTPStatusError as e:
                last_exc = e
                if attempt < _ARXIV_MAX_RETRIES and (
                    e.response.status_code == 429 or e.response.status_code >= 500
                ):
                    backoff = PaperSearchService._backoff_for(attempt)
                    logger.warning(
                        f"arXiv HTTP {e.response.status_code}（第 {attempt}/{_ARXIV_MAX_RETRIES} 次），"
                        f"{backoff:.1f}s 后重试: {e}"
                    )
                    time.sleep(backoff)
                    continue
                # 其它 HTTP 错误 / 最后一次：直接跳出
                break
            except httpx.HTTPError as e:
                last_exc = e
                if attempt < _ARXIV_MAX_RETRIES:
                    backoff = PaperSearchService._backoff_for(attempt)
                    logger.warning(
                        f"arXiv 网络错误（第 {attempt}/{_ARXIV_MAX_RETRIES} 次），"
                        f"{backoff:.1f}s 后重试: {e}"
                    )
                    time.sleep(backoff)
                    continue
                break
            except Exception as e:
                last_exc = e
                break

        # 所有尝试耗尽
        err_msg = f"{type(last_exc).__name__}: {last_exc}" if last_exc else "unknown"
        logger.warning(f"arXiv 检索失败（{_ARXIV_MAX_RETRIES} 次重试后仍失败）: {err_msg}")
        return []

    # ============================================================
    # arXiv ToU 合规：全局限速 + 指数退避
    # ============================================================

    @staticmethod
    def _throttle() -> None:
        """在本次请求前等待，确保与上一次 arXiv API 请求至少间隔 _ARXIV_MIN_INTERVAL_SEC 秒。"""
        global _last_request_ts
        with _rate_lock:
            now = time.monotonic()
            wait = _ARXIV_MIN_INTERVAL_SEC - (now - _last_request_ts)
            if wait > 0:
                time.sleep(wait)
                now = time.monotonic()
            _last_request_ts = now

    @staticmethod
    def _backoff_for(attempt: int) -> float:
        """第 attempt (1-based) 次失败后的退避秒数（指数增长，不超上限）。"""
        backoff = _ARXIV_BASE_BACKOFF_SEC * (2 ** (attempt - 1))
        return min(backoff, _ARXIV_MAX_BACKOFF_SEC)

    def _parse_arxiv_atom(self, xml_text: str) -> List[Paper]:
        """解析 arXiv 返回的 Atom XML"""
        root = ET.fromstring(xml_text)
        papers: List[Paper] = []

        for entry in root.findall(f"{_ATOM_NS}entry"):
            title_el = entry.find(f"{_ATOM_NS}title")
            summary_el = entry.find(f"{_ATOM_NS}summary")
            published_el = entry.find(f"{_ATOM_NS}published")
            doi_el = entry.find(f"{_ARXIV_NS}doi")
            primary_category_el = entry.find(f"{_ARXIV_NS}primary_category")

            title = (title_el.text or "").strip() if title_el is not None else ""
            abstract = (summary_el.text or "").strip() if summary_el is not None else ""
            year = ""
            if published_el is not None and published_el.text:
                year = published_el.text[:4]

            arxiv_id = ""
            url = ""
            # arXiv 的 <id> 形如 http://arxiv.org/abs/2401.12345v1
            id_el = entry.find(f"{_ATOM_NS}id")
            if id_el is not None and id_el.text:
                raw_id = id_el.text.strip()
                # 提取 2401.12345 部分
                if "/abs/" in raw_id:
                    arxiv_id = raw_id.split("/abs/")[-1]
                url = raw_id

            doi = (doi_el.text or "").strip() if doi_el is not None else ""
            field = ""
            if primary_category_el is not None:
                field = primary_category_el.get("term", "")

            authors: List[str] = []
            for author_el in entry.findall(f"{_ATOM_NS}author"):
                name_el = author_el.find(f"{_ATOM_NS}name")
                if name_el is not None and name_el.text:
                    authors.append(name_el.text.strip())

            if not title or not abstract:
                continue  # 跳过无效条目

            source_str = self._format_arxiv_source(arxiv_id, year, authors)

            papers.append(Paper(
                title=title,
                abstract=abstract,
                year=year,
                authors=authors,
                source=source_str,
                doi=doi,
                arxiv_id=arxiv_id,
                url=url,
                field=field,
            ))

        logger.info(f"arXiv 解析完成: {len(papers)} 篇")
        return papers

    @staticmethod
    def _format_arxiv_source(arxiv_id: str, year: str, authors: List[str]) -> str:
        """格式化为 Explorer 可读的 source 字符串（≥3 字符校验）"""
        first_author = authors[0].split()[-1] if authors else "et al"
        return f"arXiv:{arxiv_id} ({first_author}, {year or 'n.d.'})"

    # ============================================================
    # 去重辅助
    # ============================================================

    def _fetch_existing_ids(self) -> Dict[str, set]:
        """拉取当前向量库中所有 arxiv_id / doi，用于去重"""
        store = self.chroma.load_or_create()
        result = {"arxiv": set(), "doi": set()}
        try:
            data = store._collection.get(include=["metadatas"])
            for meta in data.get("metadatas", []):
                if not isinstance(meta, dict):
                    continue
                if meta.get("arxiv_id"):
                    result["arxiv"].add(meta["arxiv_id"])
                if meta.get("doi"):
                    result["doi"].add(meta["doi"])
        except Exception as e:
            logger.warning(f"读取现有 metadata 失败，跳过去重: {e}")
        return result

    # ============================================================
    # 临时文献清理（策略 B：跑完 pipeline 后精确清理本次塞入的文献）
    # ============================================================

    def get_existing_arxiv_ids(self) -> set:
        """获取当前向量库中所有 arxiv_id 集合（pipeline 开始前调用做快照）"""
        return self._fetch_existing_ids().get("arxiv", set())

    def cleanup_by_arxiv_ids(self, arxiv_ids: List[str]) -> int:
        """
        根据 arxiv_id 列表从 Chroma 精确删除对应文档

        Args:
            arxiv_ids: 要删除的 arxiv_id 列表

        Returns:
            实际删除的文档数
        """
        if not arxiv_ids:
            return 0

        store = self.chroma.load_or_create()
        collection = store._collection
        deleted = 0

        for aid in arxiv_ids:
            try:
                # Chroma 支持 where 子句按 metadata 删除
                collection.delete(where={"arxiv_id": aid})
                deleted += 1
            except Exception as e:
                logger.warning(f"删除 arxiv_id={aid} 失败: {e}")

        logger.info(f"✅ 清理 {deleted}/{len(arxiv_ids)} 篇临时文献")
        return deleted
