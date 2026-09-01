"""
文档解析服务：提取 PDF 和 Markdown 文件的文本内容

支持格式：
- PDF (.pdf): 使用 pypdf 提取文本
- Markdown (.md): 直接读取文本内容
"""

import base64
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# 最大提取文本长度（字符数），避免超出 LLM 上下文
MAX_EXTRACTED_TEXT_LENGTH = 50000


def parse_document(name: str, data_url: str) -> Optional[str]:
    """
    根据文件类型提取文本内容
    
    Args:
        name: 文件名
        data_url: base64 data URL (格式: data:<mime>;base64,<data>)
        
    Returns:
        提取的文本内容，失败返回 None
    """
    try:
        # 解析 data URL
        if not data_url.startswith("data:"):
            logger.warning(f"文档 {name}: 无效的 data URL 格式")
            return None
        
        # 分离 mime type 和 base64 数据
        header, _, base64_data = data_url.partition(",")
        mime_type = header.split(":")[1].split(";")[0] if ":" in header else ""
        
        # 解码 base64
        file_bytes = base64.b64decode(base64_data)
        
        # 根据文件类型选择解析器
        lower_name = name.lower()
        if lower_name.endswith(".pdf") or mime_type == "application/pdf":
            return _extract_pdf_text(file_bytes, name)
        elif lower_name.endswith(".md") or lower_name.endswith(".markdown") or mime_type == "text/markdown":
            return _extract_markdown_text(file_bytes, name)
        else:
            logger.warning(f"文档 {name}: 不支持的文件类型 (mime={mime_type})")
            return None
            
    except Exception as e:
        logger.error(f"文档 {name} 解析失败: {e}")
        return None


def _extract_pdf_text(pdf_bytes: bytes, name: str) -> Optional[str]:
    """使用 pypdf 提取 PDF 文本"""
    try:
        from pypdf import PdfReader
        from io import BytesIO
        
        reader = PdfReader(BytesIO(pdf_bytes))
        text_parts = []
        
        for i, page in enumerate(reader.pages):
            try:
                page_text = page.extract_text()
                if page_text and page_text.strip():
                    text_parts.append(page_text.strip())
            except Exception as e:
                logger.warning(f"PDF {name} 第 {i+1} 页提取失败: {e}")
                continue
        
        if not text_parts:
            logger.warning(f"PDF {name}: 未能提取到任何文本（可能是扫描版）")
            return None
        
        full_text = "\n\n".join(text_parts)
        
        # 截断过长文本
        if len(full_text) > MAX_EXTRACTED_TEXT_LENGTH:
            full_text = full_text[:MAX_EXTRACTED_TEXT_LENGTH] + "\n\n[... 文本过长，已截断 ...]"
            logger.info(f"PDF {name}: 文本已截断至 {MAX_EXTRACTED_TEXT_LENGTH} 字符")
        
        logger.info(f"PDF {name}: 成功提取 {len(full_text)} 字符")
        return full_text
        
    except Exception as e:
        logger.error(f"PDF {name} 解析失败: {e}")
        return None


def _extract_markdown_text(md_bytes: bytes, name: str) -> Optional[str]:
    """读取 Markdown 文本内容"""
    try:
        # 尝试 UTF-8 解码
        text = md_bytes.decode("utf-8")
        
        # 截断过长文本
        if len(text) > MAX_EXTRACTED_TEXT_LENGTH:
            text = text[:MAX_EXTRACTED_TEXT_LENGTH] + "\n\n[... 文本过长，已截断 ...]"
            logger.info(f"Markdown {name}: 文本已截断至 {MAX_EXTRACTED_TEXT_LENGTH} 字符")
        
        logger.info(f"Markdown {name}: 成功读取 {len(text)} 字符")
        return text
        
    except UnicodeDecodeError:
        # 尝试其他编码
        try:
            text = md_bytes.decode("gbk")
            logger.info(f"Markdown {name}: 使用 GBK 编码读取 {len(text)} 字符")
            return text
        except Exception as e:
            logger.error(f"Markdown {name} 编码解析失败: {e}")
            return None
    except Exception as e:
        logger.error(f"Markdown {name} 读取失败: {e}")
        return None


def parse_documents(documents: list) -> list:
    """
    批量解析文档列表
    
    Args:
        documents: [{name, data, type}] 格式的文档列表
        
    Returns:
        解析成功的文档文本列表 [{name, content}]
    """
    results = []
    for doc in documents:
        name = doc.get("name", "unknown")
        data = doc.get("data", "")
        
        content = parse_document(name, data)
        if content:
            results.append({
                "name": name,
                "content": content
            })
    
    logger.info(f"文档解析完成: {len(results)}/{len(documents)} 成功")
    return results
