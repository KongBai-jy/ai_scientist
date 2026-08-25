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

import logging
import re
import xml.etree.ElementTree as ET
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

_ARXIV_API = "http://export.arxiv.org/api/query"


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
        # 16s 超时：arXiv 偶尔慢；允许跟随重定向（arXiv http→https）
        self.http = httpx.Client(
            timeout=16.0,
            follow_redirects=True,
            headers={"User-Agent": "AI-Scientist/1.0"},
        )

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

            # 下载 PDF + 提取文本
            pdf_bytes = self._download_pdf(p.arxiv_id)
            text = ""
            if pdf_bytes:
                try:
                    text = self._extract_pdf_text(pdf_bytes)
                except Exception as e:
                    logger.warning(f"PDF 文本提取失败 {p.arxiv_id}: {e}")

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

        # 2. 提取全文
        try:
            text = self._extract_pdf_text(pdf_bytes)
        except Exception as e:
            return {
                "file": file_path,
                "source": source,
                "total_chars": 0,
                "ingested": 0,
                "skipped": False,
                "error": f"PDF 解析失败: {e}",
            }

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
            "skipped": False,
        }

    # ============================================================
    # Query 预处理（中文 → 英文关键词 / 字符清洗）
    # ============================================================

    @staticmethod
    def _prepare_query(query: str) -> str:
        """将 query 转为 arXiv 可识别的英文关键词

        处理策略：
        1. 纯 ASCII → 直接返回（去掉首尾空白）
        2. 含中文字符 → 尝试调用 DashScope 翻译为英文关键词；失败时用简单字符清洗降级
        """
        q = (query or "").strip()
        if not q:
            return q

        # 纯 ASCII / 英文 query 直接返回
        if all(ord(c) < 128 for c in q):
            return q

        # 含中文：调用 LLM 翻译
        translated = PaperSearchService._translate_to_english(q)
        if translated and translated != q:
            return translated

        # 降级：移除明显的中文/标点，保留英文数字部分
        fallback = re.sub(r"[^\w\s\-+:().,?]", " ", q)
        fallback = re.sub(r"\s+", " ", fallback).strip()
        return fallback or q

    @staticmethod
    def _translate_to_english(text: str) -> Optional[str]:
        """调用 DashScope（或系统 LLM）将中文 query 翻译为 3-6 个英文关键词"""
        try:
            import os
            from dotenv import load_dotenv
            from pathlib import Path

            # 加载项目根目录 .env（如果还没加载）
            _PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
            load_dotenv(_PROJECT_ROOT / ".env", override=False)

            api_key = (
                os.getenv("DASHSCOPE_API_KEY")
                or os.getenv("DASHSCOPE_API_KEY_EMBEDDING")
            )
            if not api_key:
                logger.warning("无 DASHSCOPE_API_KEY，跳过中文翻译")
                return None

            model = os.getenv("QWEN_MODEL", "qwen-plus")
            prompt = (
                "将下面的科学研究问题翻译成 3-6 个英文关键词短语，"
                "这些关键词将用于 arXiv 文献检索。\n"
                "要求：只输出关键词，用英文逗号分隔，不要其他文字。\n"
                f"问题：{text}"
            )
            resp = httpx.post(
                "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.1,
                    "max_tokens": 128,
                },
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
            content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
            content = content.strip()
            # 清理可能的引号、序号和末尾句号
            content = re.sub(r"^[\d\.\)\s]+", "", content)
            content = re.sub(r"[\"'`]", "", content)
            content = content.rstrip(".。")
            # 将逗号分隔符替换为空格，让 arXiv 把所有关键词作为一个联合 query
            # 例如 "YBCO, high-temperature superconductivity" → "YBCO high-temperature superconductivity"
            content = re.sub(r"[,，;；]", " ", content)
            content = re.sub(r"\s+", " ", content).strip()
            return content or None
        except Exception as e:
            logger.warning(f"中文翻译失败（将使用降级策略）: {e}")
            return None

    # ============================================================
    # arXiv 检索
    # ============================================================

    def _arxiv_search(self, query: str, max_results: int) -> List[Paper]:
        """调用 arXiv API（返回 Atom XML）"""
        params = {
            "search_query": f"all:{query}",
            "start": 0,
            "max_results": max_results,
            "sortBy": "relevance",
        }
        logger.info(f"arXiv 检索: {query!r} (max={max_results})")
        resp = self.http.get(_ARXIV_API, params=params)
        resp.raise_for_status()

        return self._parse_arxiv_atom(resp.text)

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
