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

_ARXIV_API = "https://export.arxiv.org/api/query"


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
            query: 搜索关键词（英文效果最佳，中文 arXiv 支持有限）
            max_results: 最大返回数
        """
        try:
            return self._arxiv_search(query, max_results)
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
    ) -> Dict:
        """一站式：检索 + 入库"""
        papers = self.search(query, max_results=max_results)
        ingested = self.ingest(papers, dedupe=dedupe)
        return {
            "query": query,
            "retrieved": len(papers),
            "ingested": ingested,
            "skipped": len(papers) - ingested,
            "papers": [
                {
                    "title": p.title,
                    "year": p.year,
                    "source": p.source,
                    "arxiv_id": p.arxiv_id,
                    "doi": p.doi,
                    "url": p.url,
                }
                for p in papers
            ],
        }

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
