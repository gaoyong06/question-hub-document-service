"""
文档解析服务
使用python-docx解析Word文档（.doc, .docx）
其他格式通过MarkItDown转换为Markdown后解析
"""
from __future__ import annotations

import os
import re
import tempfile
import zipfile
import uuid
from dataclasses import dataclass, field
from typing import List, Optional, Tuple
from pathlib import Path
from docx import Document
from docx.oxml.ns import qn
from loguru import logger

from app.models import QuestionResult
from app.config import settings
from app.services.exam_structure_utils import (
    QUESTION_TYPES,
    REFERENCE_ANSWER_HEADERS,
    SECTION_HEADER_PATTERN,
    QUESTION_START_PATTERN,
    SECTION_RELAXED_PATTERN,
    SECTION_TITLE_MAX_LEN,
    QUESTION_SPLIT_PATTERNS,
    GRADE_PATTERNS,
    SUBJECT_KEYWORDS,
    ParsedStructure,
    parse_structure as parse_structure_common,
    structure_to_questions as structure_to_questions_common,
    parse_reference_answers as parse_reference_answers_common,
    content_has_choice_options as content_has_choice_options_common,
    parse_stem_and_options_from_choice_content as parse_stem_and_options_from_choice_content_common,
    to_markdown_line_breaks as to_markdown_line_breaks_common,
    parse_grade_subject as parse_grade_subject_common,
    apply_grade_subject_to_questions as apply_grade_subject_to_questions_common,
    is_relaxed_section_heading as is_relaxed_section_heading_common,
)

# 答案/题干截断边界（fallback 正则用）
ANSWER_END_LOOKAHEAD = r"(?=\d+[\.．、]|\n\s*[一二三四五六七八九十]+[\.．、]|\Z)"


def _is_relaxed_section_heading(line: str) -> bool:
    return is_relaxed_section_heading_common(line)


# 格式辅助：正文默认字号（pt），用于判断「明显大于正文」的标题
DEFAULT_BODY_FONT_PT = 12.0
# 若段落字号 >= 此值且大于正文，可视为标题
SECTION_HEADING_FONT_PT_MIN = 14.0


def _format_suggests_section_heading(
    fmt: Optional[ParagraphFormatInfo],
    body_font_pt: float = DEFAULT_BODY_FONT_PT,
) -> bool:
    """根据段落格式判断是否像大题标题（辅助，与文字规则结合使用）。"""
    if fmt is None:
        return False
    style = (fmt.style_name or "").strip().lower()
    if "heading" in style or "标题" in style or style.startswith("heading") or style.startswith("标题"):
        return True
    if fmt.font_size_pt is not None and fmt.font_size_pt >= SECTION_HEADING_FONT_PT_MIN and fmt.font_size_pt > body_font_pt:
        return True
    if fmt.is_bold and fmt.left_indent_pt <= 0 and fmt.first_line_indent_pt <= 0:
        return True
    return False


@dataclass
class ParagraphFormatInfo:
    """Word 段落格式信息，用于辅助判断大题/小题（结构优先）。无格式时为 None。"""
    style_name: str = ""
    font_size_pt: Optional[float] = None
    left_indent_pt: float = 0.0
    first_line_indent_pt: float = 0.0
    is_bold: bool = False


class DocumentParser:
    """文档解析器（支持Word格式，其他格式通过MarkItDown转换）"""
    
    def __init__(self):
        self.temp_dir = Path(settings.temp_file_dir)
        self.temp_dir.mkdir(parents=True, exist_ok=True)
    
    def download_file(self, file_url: str) -> str:
        """
        下载文件到临时目录
        
        支持多种协议：
        - http://, https://: 通过HTTP下载
        - file://: 直接访问本地文件系统
        - 相对路径或绝对路径: 作为本地文件路径处理
        
        Args:
            file_url: 文件URL或路径
            
        Returns:
            本地文件路径
        """
        import httpx
        from urllib.parse import urlparse
        
        # 解析URL
        parsed = urlparse(file_url)
        scheme = parsed.scheme.lower()
        
        # 从URL提取文件名
        file_name = os.path.basename(parsed.path or file_url.split("?")[0])
        if not file_name or file_name == "content":
            # 如果文件名为空或是 "content"，使用 UUID 生成唯一文件名
            import uuid
            file_name = f"{uuid.uuid4().hex}.docx"
        else:
            # 确保文件扩展名
            if not any(file_name.lower().endswith(ext) for ext in settings.supported_extensions):
                file_name += ".docx"
        
        local_path = self.temp_dir / file_name
        
        logger.info(f"Downloading file from {file_url} to {local_path} (scheme: {scheme})")
        
        # 根据协议类型处理
        if scheme in ('http', 'https'):
            # 若 file_url 为对外网关 URL，走网关可能 401，后端应使用内网 asset-service 直连。
            # asset-service 主资源为 GET /asset/v1/files/{fileId}。
            download_url = file_url
            try:
                internal_parsed = urlparse(settings.asset_service_url)
                internal_host = (internal_parsed.netloc or "").strip()
                if internal_host and parsed.netloc != internal_host:
                    path = (parsed.path or "").strip("/")
                    if "files" in path and "asset/v1/files" in path:
                        parts = path.split("/")
                        if "files" in parts:
                            idx = parts.index("files")
                            if idx + 1 < len(parts):
                                file_id = parts[idx + 1]
                                if len(file_id) == 36 and file_id.count("-") == 4:
                                    base = settings.asset_service_url.rstrip("/")
                                    download_url = f"{base}/asset/v1/files/{file_id}"
                                    logger.info(f"Using internal asset URL for download: {download_url}")
            except Exception as e:
                logger.warning(f"Could not resolve internal asset URL, using original: {e}")
            # HTTP/HTTPS: 通过HTTP下载
            with httpx.Client(timeout=settings.download_timeout) as client:
                response = client.get(download_url)
                response.raise_for_status()
                
                # 检查响应类型
                content_type = response.headers.get("content-type", "").lower()
                logger.info(f"HTTP response Content-Type: {content_type}")
                
                # 如果是 JSON 响应（asset-service 的 DownloadFile 返回 JSON）
                if "application/json" in content_type:
                    import json
                    import base64
                    logger.info(f"Detected JSON response, parsing...")
                    data = response.json()
                    
                    # 提取文件数据（可能是 base64 编码的）
                    if isinstance(data, dict):
                        # 统一响应格式：{"success": true, "data": {...}}
                        if "data" in data:
                            file_data = data["data"]
                        else:
                            file_data = data
                        
                        # 如果 data 字段是字典，提取其中的 data 字段（base64 编码）
                        if isinstance(file_data, dict) and "data" in file_data:
                            # base64 解码
                            base64_str = file_data["data"]
                            logger.info(f"Decoding base64 data, length: {len(base64_str)}")
                            file_bytes = base64.b64decode(base64_str)
                            logger.info(f"Decoded file size: {len(file_bytes)} bytes")
                        elif isinstance(file_data, str):
                            # 直接是 base64 字符串
                            logger.info(f"Decoding base64 string, length: {len(file_data)}")
                            file_bytes = base64.b64decode(file_data)
                            logger.info(f"Decoded file size: {len(file_bytes)} bytes")
                        else:
                            raise ValueError(f"Unexpected JSON response format: {data}")
                    else:
                        raise ValueError(f"Unexpected JSON response format: {data}")
                    
                    # 检查文件大小
                    if len(file_bytes) > settings.max_file_size:
                        raise ValueError(f"File size {len(file_bytes)} exceeds maximum {settings.max_file_size}")
                    
                    # 验证文件签名（docx 是 ZIP 格式，以 PK 开头）
                    if len(file_bytes) < 2 or file_bytes[:2] != b'PK':
                        logger.warning(f"File does not have valid ZIP/DOCX signature, first 50 bytes: {file_bytes[:50]}")
                    else:
                        logger.info(f"File has valid ZIP/DOCX signature (PK)")
                    
                    # 保存文件
                    with open(local_path, "wb") as f:
                        f.write(file_bytes)
                    
                    # 验证文件已保存
                    if not os.path.exists(local_path):
                        raise FileNotFoundError(f"File was not saved: {local_path}")
                    
                    saved_size = os.path.getsize(local_path)
                    logger.info(f"File saved to {local_path}, size: {saved_size} bytes")
                    
                    # 再次验证文件签名
                    with open(local_path, 'rb') as f:
                        first_bytes = f.read(2)
                        if first_bytes != b'PK':
                            raise ValueError(f"Saved file is not a valid ZIP/DOCX file (signature: {first_bytes})")
                else:
                    # 直接是文件流
                    # 检查文件大小
                    content_length = response.headers.get("content-length")
                    if content_length and int(content_length) > settings.max_file_size:
                        raise ValueError(f"File size {content_length} exceeds maximum {settings.max_file_size}")
                    
                    # 保存文件
                    with open(local_path, "wb") as f:
                        for chunk in response.iter_bytes(chunk_size=settings.download_chunk_size):
                            f.write(chunk)
                    
                    # 验证文件已保存
                    if not os.path.exists(local_path):
                        raise FileNotFoundError(f"File was not saved: {local_path}")
                    
                    saved_size = os.path.getsize(local_path)
                    logger.info(f"File saved to {local_path}, size: {saved_size} bytes")
                    
                    # 验证文件签名（确保是有效的 ZIP/DOCX 文件）
                    with open(local_path, 'rb') as f:
                        first_bytes = f.read(2)
                        if first_bytes != b'PK':
                            raise ValueError(f"Saved file is not a valid ZIP/DOCX file (signature: {first_bytes})")
        
        elif scheme == 'file':
            # file:// 协议: 直接访问本地文件
            # file:///path/to/file 或 file://localhost/path/to/file
            file_path = parsed.path
            # 处理 Windows 路径 (file:///C:/path/to/file)
            if os.name == 'nt' and len(file_path) > 2 and file_path[0] == '/' and file_path[2] == ':':
                file_path = file_path[1:]  # 移除开头的 /
            
            if not os.path.isabs(file_path):
                raise ValueError(f"file:// URL must be an absolute path: {file_path}")
            
            if not os.path.exists(file_path):
                raise FileNotFoundError(f"File not found: {file_path}")
            
            # 检查文件大小
            file_size = os.path.getsize(file_path)
            if file_size > settings.max_file_size:
                raise ValueError(f"File size {file_size} exceeds maximum {settings.max_file_size}")
            
            # 复制文件到临时目录
            import shutil
            shutil.copy2(file_path, local_path)
            logger.info(f"Copied file from {file_path} to {local_path}")
        
        else:
            # 无协议或未知协议: 作为本地文件路径处理
            # 可能是相对路径或绝对路径
            file_path = file_url
            
            # 如果是相对路径，尝试从常见位置查找
            if not os.path.isabs(file_path):
                # 尝试从 asset-service 的默认存储路径查找
                possible_paths = [
                    file_path,  # 原始路径
                    os.path.join("./uploads", file_path),  # 相对 uploads 目录
                    os.path.join("/uploads", file_path),  # 绝对 uploads 目录
                ]
                
                found = False
                for path in possible_paths:
                    abs_path = os.path.abspath(path)
                    if os.path.exists(abs_path):
                        file_path = abs_path
                        found = True
                        break
                
                if not found:
                    raise FileNotFoundError(f"File not found: {file_url} (tried: {possible_paths})")
            else:
                if not os.path.exists(file_path):
                    raise FileNotFoundError(f"File not found: {file_path}")
            
            # 检查文件大小
            file_size = os.path.getsize(file_path)
            if file_size > settings.max_file_size:
                raise ValueError(f"File size {file_size} exceeds maximum {settings.max_file_size}")
            
            # 复制文件到临时目录
            import shutil
            shutil.copy2(file_path, local_path)
            logger.info(f"Copied file from {file_path} to {local_path}")
        
        # 最终验证：确保文件存在且有效
        if not os.path.exists(local_path):
            raise FileNotFoundError(f"File was not saved: {local_path}")
        
        file_size = os.path.getsize(local_path)
        if file_size == 0:
            raise ValueError(f"Downloaded file is empty: {local_path}")
        
        # 验证文件签名
        with open(local_path, 'rb') as f:
            first_bytes = f.read(2)
            if first_bytes != b'PK':
                raise ValueError(f"Downloaded file is not a valid ZIP/DOCX file (signature: {first_bytes}, size: {file_size})")
        
        logger.info(f"File downloaded successfully: {local_path}, size: {file_size} bytes, signature: PK")
        return str(local_path)
    
    def parse_document(self, file_path: str) -> Tuple[List[QuestionResult], List[str], str, str, int, str]:
        """
        解析Word文档，提取题目、图片路径、文档标题、注意事项、年级与学科。

        Args:
            file_path: 本地文件路径

        Returns:
            (题目列表, 图片本地路径列表, document_title, document_description, document_grade, document_subject)。
            题目 content 中可能含 {{IMAGE_0}}、{{IMAGE_1}} 等占位符；document_grade 为 1-9，未识别为 0；
            document_subject 为学科名（如数学、语文），未识别为空字符串。
        """
        logger.info(f"Parsing document: {file_path}")

        if not os.path.exists(file_path):
            logger.error("File does not exist at path: %s", file_path)
            raise FileNotFoundError(f"File does not exist: {file_path}")

        file_size = os.path.getsize(file_path)
        logger.info("File exists. Path: %s, Size: %s bytes.", file_path, file_size)

        if file_size == 0:
            raise ValueError(f"File is empty: {file_path}")

        with open(file_path, "rb") as f:
            if f.read(2) != b"PK":
                raise ValueError("File is not a valid ZIP/DOCX file")

        try:
            if not os.path.exists(file_path):
                raise FileNotFoundError(f"File was deleted before parsing: {file_path}")

            doc = Document(file_path)
            # 文档标题：优先 core_properties.title，否则取首段非空文本（供下游作为试卷名称）
            document_title = (getattr(doc.core_properties, "title", None) or "").strip()
            # 带图片占位符的段落文本、图片路径、每段格式信息（供结构解析辅助大题/小题判断）
            paragraphs, image_paths, format_infos = self._extract_paragraphs_with_images(doc, file_path)
            if not document_title and paragraphs:
                for p in paragraphs:
                    t = (p or "").strip()
                    if t and not t.startswith("{{IMAGE_"):
                        document_title = t[:200].strip()
                        break

            full_text = "\n".join(paragraphs)
            if full_text:
                preview = full_text[:1000] if len(full_text) > 1000 else full_text
                logger.info("Extracted text preview (first 1000 chars):\n%s", preview)
                logger.info("Full text length: %s characters", len(full_text))
            else:
                logger.warning("No text content extracted from document")

            # 优先：流式结构解析（先解析结构，再映射为业务数据；有格式时用格式辅助大题判断）
            structure = self._parse_structure(paragraphs, format_infos)
            total_from_structure = sum(len(contents) for _, _, contents in structure.sections)
            if total_from_structure > 0:
                document_description = ""
                answer_block_text = "\n".join(structure.answer_block_texts) if structure.answer_block_texts else ""
                reference_answers = self._parse_reference_answers(answer_block_text) if answer_block_text else []
                logger.info("Structure path: sections=%s, questions=%s, reference_answers=%s",
                            len(structure.sections), total_from_structure, len(reference_answers))
                questions = structure_to_questions_common(structure, reference_answers)
                if structure.document_title:
                    document_title = structure.document_title
                if structure.document_description:
                    document_description = structure.document_description
                logger.info("Extracted %s questions from document (structure path)", len(questions))
                document_grade, document_subject = parse_grade_subject_common(document_title, full_text)
                apply_grade_subject_to_questions_common(questions, document_grade, document_subject)
                if document_grade or document_subject:
                    logger.info("Recognized document_grade=%s, document_subject=%s", document_grade, document_subject)
                return questions, image_paths, document_title, document_description, document_grade, document_subject

            # 回退：原正则流程
            reference_answers = self._parse_reference_answers(full_text)
            logger.info("Parsed reference answers count: %s", len(reference_answers))
            sections = self._detect_sections(full_text)
            logger.info("Detected sections count: %s", len(sections))
            questions_with_pos = self._extract_questions_with_positions(full_text, paragraphs)
            questions = self._sort_and_fill_answers(questions_with_pos, reference_answers, sections)
            document_description = self._parse_document_description(full_text)
            if document_description:
                logger.info("Extracted document_description (notes) length: %s chars", len(document_description))
            logger.info("Extracted %s questions from document (fallback path)", len(questions))
            if len(questions) == 0 and paragraphs:
                logger.warning("No questions extracted, but document has content. Check regex patterns.")
                logger.info("First 5 paragraphs:\n%s", "\n".join(paragraphs[:5]))
            document_grade, document_subject = parse_grade_subject_common(document_title, full_text)
            apply_grade_subject_to_questions_common(questions, document_grade, document_subject)
            if document_grade or document_subject:
                logger.info("Recognized document_grade=%s, document_subject=%s", document_grade, document_subject)
            return questions, image_paths, document_title, document_description, document_grade, document_subject

        except Exception as e:
            logger.error("Failed to parse document: %s", e)
            raise

    def _get_paragraph_format(self, para) -> ParagraphFormatInfo:
        """从 python-docx 段落对象提取格式信息，用于格式辅助结构判断。"""
        fmt = ParagraphFormatInfo()
        try:
            fmt.style_name = getattr(para.style, "name", None) or ""
            pf = getattr(para, "paragraph_format", None)
            if pf is not None:
                li = getattr(pf, "left_indent", None)
                fmt.left_indent_pt = float(getattr(li, "pt", 0) or 0)
                fl = getattr(pf, "first_line_indent", None)
                fmt.first_line_indent_pt = float(getattr(fl, "pt", 0) or 0)
            font_sizes: List[float] = []
            bold_count = 0
            text_run_count = 0
            for run in getattr(para, "runs", []):
                if getattr(run, "text", "").strip():
                    text_run_count += 1
                    if getattr(run, "bold", None) is True:
                        bold_count += 1
                    sz = getattr(run.font, "size", None)
                    if sz is not None and hasattr(sz, "pt"):
                        font_sizes.append(float(sz.pt))
            if font_sizes:
                fmt.font_size_pt = max(font_sizes)
            fmt.is_bold = text_run_count > 0 and bold_count >= (text_run_count + 1) // 2
        except Exception as e:
            logger.debug("Failed to get paragraph format: %s", e)
        return fmt

    def _extract_paragraphs_with_images(self, doc: Document, file_path: str) -> Tuple[List[str], List[str], List[ParagraphFormatInfo]]:
        """
        提取段落文本，并将文档中的内嵌图片导出为临时文件，在文本中用 {{IMAGE_N}} 占位。
        同时提取每段格式信息（样式、字号、缩进、加粗），供结构解析辅助判断大题/小题。
        返回 (段落列表, 图片路径列表, 每段对应的格式信息列表，与段落一一对应)。
        """
        image_paths: List[str] = []
        image_dir = self.temp_dir / "docx_images"
        image_dir.mkdir(parents=True, exist_ok=True)

        def save_image_blob(blob: bytes, ext: str = ".png") -> str:
            path = image_dir / f"{uuid.uuid4().hex}{ext}"
            path.write_bytes(blob)
            return str(path)

        w_br = qn("w:br")
        w_r = qn("w:r")
        w_t = qn("w:t")

        paragraphs: List[str] = []
        format_infos: List[ParagraphFormatInfo] = []
        for para in doc.paragraphs:
            fmt = self._get_paragraph_format(para)
            parts: List[str] = []
            run_idx = 0
            for child in para._element:
                if child.tag == w_br:
                    parts.append("\n")
                    continue
                if child.tag != w_r:
                    continue
                run = para.runs[run_idx] if run_idx < len(para.runs) else None
                run_idx += 1
                if run is None:
                    for t in child.iter():
                        if t.tag == w_t and t.text:
                            parts.append(t.text)
                        if t.tail:
                            parts.append(t.tail)
                    continue
                if run._element is None:
                    if run.text:
                        parts.append(run.text)
                    continue
                r_id = None
                drawing = run._element.find(qn("w:drawing"))
                if drawing is not None:
                    blip = None
                    for elem in drawing.iter():
                        if elem.tag is not None and "blip" in elem.tag.lower():
                            blip = elem
                            break
                    if blip is None:
                        blip = drawing.find(qn("a:blip"))
                    if blip is not None:
                        r_id = blip.get(qn("r:embed"))
                if r_id is None:
                    pict = run._element.find(qn("w:pict"))
                    if pict is not None:
                        for elem in pict.iter():
                            if elem.tag is not None and "imagedata" in elem.tag.lower():
                                r_id = elem.get(qn("r:id"))
                                if r_id:
                                    break
                if r_id and hasattr(doc.part, "related_parts") and r_id in doc.part.related_parts:
                    part = doc.part.related_parts[r_id]
                    blob = getattr(part, "blob", None) or getattr(part, "_blob", None)
                    if blob is not None:
                        ext = ".png"
                        if getattr(part, "content_type", "").startswith("image/jpeg") or getattr(
                            part, "partname", ""
                        ).lower().endswith(".emf"):
                            ext = ".jpg"
                        local_path = save_image_blob(blob, ext)
                        image_paths.append(local_path)
                        parts.append("{{IMAGE_%d}}" % (len(image_paths) - 1))
                        continue
                if run.text:
                    parts.append(run.text)
            line = "".join(parts).strip()
            if line:
                paragraphs.append(line)
                format_infos.append(fmt)

        if not image_paths and doc.paragraphs:
            try:
                with zipfile.ZipFile(file_path, "r") as zf:
                    names = [n for n in zf.namelist() if n.startswith("word/media/")]
                    for i, name in enumerate(sorted(names)):
                        data = zf.read(name)
                        ext = os.path.splitext(name)[1] or ".png"
                        local_path = str(image_dir / f"img_{i}{ext}")
                        Path(local_path).write_bytes(data)
                        image_paths.append(local_path)
            except Exception as e:
                logger.debug("Fallback zip media extraction failed: %s", e)

        return paragraphs, image_paths, format_infos

    def _parse_reference_answers(self, full_text: str) -> List[str]:
        """从「参考答案」区块解析出按题号顺序的答案列表（委托公共实现）。"""
        return parse_reference_answers_common(full_text)

    def _parse_document_description(self, full_text: str) -> str:
        """
        从全文中解析「注意事项」区块，作为试卷的 description（注意事项）字段。
        若未找到「注意事项」或内容为空，返回空字符串；下游未解析到时该字段留空展示。
        """
        # 定位「注意事项」或「注意事项：」（允许后跟冒号、空格等）
        idx = full_text.find("注意事项")
        if idx == -1:
            return ""
        # 跳过标题本身，取标题后的内容（允许「注意事项」或「注意事项：」）
        block_start = idx + len("注意事项")
        if block_start < len(full_text) and full_text[block_start] in "：:":
            block_start += 1
        block = full_text[block_start:].lstrip("\n\r\t \u3000")
        if not block:
            return ""
        # 截断到下一个区块：大题标题（一. 二. …）、参考答案/答案/标准答案、或文末
        end_match = SECTION_HEADER_PATTERN.search(block)
        end_pos = len(block)
        if end_match:
            end_pos = min(end_pos, end_match.start())
        for header in REFERENCE_ANSWER_HEADERS:
            i = block.find(header)
            if i != -1 and i < end_pos:
                end_pos = i
        description = block[:end_pos].strip()
        return to_markdown_line_breaks_common(description) if description else ""

    def _parse_structure(
        self,
        paragraphs: List[str],
        paragraph_formats: Optional[List[ParagraphFormatInfo]] = None,
    ) -> ParsedStructure:
        """
        流式解析试卷结构（委托公共实现，传入 Word 段落格式辅助大题判断）。
        """
        body_font_pt = DEFAULT_BODY_FONT_PT
        if paragraph_formats and len(paragraph_formats) == len(paragraphs):
            sizes = [f.font_size_pt for f in paragraph_formats if f and getattr(f, "font_size_pt", None) is not None]
            if sizes:
                sorted_sizes = sorted(sizes)
                body_font_pt = sorted_sizes[len(sorted_sizes) // 2]

        def format_suggests_heading(i: int) -> bool:
            fmt = paragraph_formats[i] if paragraph_formats and i < len(paragraph_formats) else None
            return _format_suggests_section_heading(fmt, body_font_pt)

        return parse_structure_common(paragraphs, format_suggests_heading)

    def _structure_to_questions(
        self,
        structure: ParsedStructure,
        paragraphs: List[str],
        reference_answers: List[str],
    ) -> List[QuestionResult]:
        """将解析出的结构映射为业务题目列表（委托公共实现）。"""
        return structure_to_questions_common(structure, reference_answers)

    def _extract_questions_with_positions(
        self, full_text: str, paragraphs: List[str]
    ) -> List[Tuple[QuestionResult, int]]:
        """提取题目并返回 (题目, 在 full_text 中的起始位置)，用于按文档顺序排序。"""
        with_pos: List[Tuple[QuestionResult, int]] = []

        for extractor, name in [
            (self._extract_single_choice_with_pos, "single-choice"),
            (self._extract_multiple_choice_with_pos, "multiple-choice"),
            (self._extract_fill_blank_with_pos, "fill-blank"),
            (self._extract_judge_with_pos, "judge"),
            (self._extract_essay_with_pos, "essay"),
        ]:
            items = extractor(full_text, paragraphs)
            with_pos.extend(items)
            logger.info("Extracted %s %s questions", len(items), name)

        return with_pos

    def _detect_sections(self, full_text: str) -> List[Tuple[int, str, int]]:
        """
        检测大题标题及其在全文中的起始位置，得到试卷结构。
        同一大题（同一 section_order）在文档中可能多次出现（如正文「四.计算题(共3题，共31分)」
        与参考答案中「四.计算题」），只保留每种 order 的第一次出现，避免同一大题被拆成多个分组。
        返回 [(section_order, section_title, start_pos), ...]，按 start_pos 递增。
        """
        raw: List[Tuple[int, int, str]] = []  # (pos, order, title)
        order_map = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
        next_order = 11  # 十一、十二等未在 order_map 中时递增加
        for m in SECTION_HEADER_PATTERN.finditer(full_text):
            cn_num, rest = m.group(1), m.group(2).strip()
            order = order_map.get(cn_num)
            if order is None:
                order = next_order
                next_order += 1
            title = (cn_num + "." + rest) if not rest.startswith(".") else (cn_num + rest)
            raw.append((m.start(), order, title))
        raw.sort(key=lambda x: x[0])
        # 每个 section_order 只保留第一次出现（最小 pos），对应试卷正文中的大题标题
        seen_order: set = set()
        sections: List[Tuple[int, int, str]] = []
        for pos, order, title in raw:
            if order in seen_order:
                continue
            seen_order.add(order)
            sections.append((pos, order, title))
        return [(order, title, pos) for pos, order, title in sections]

    def _sort_and_fill_answers(
        self,
        questions_with_pos: List[Tuple[QuestionResult, int]],
        reference_answers: List[str],
        sections: Optional[List[Tuple[int, str, int]]] = None,
    ) -> List[QuestionResult]:
        """按文档位置排序，回填参考答案，并依位置归属大题（section_title / section_order）。"""
        sorted_list = sorted(questions_with_pos, key=lambda x: x[1])
        questions = [q for q, _ in sorted_list]
        sections = sections or []

        for i, (q, pos) in enumerate(sorted_list):
            # 回填答案
            if not (q.answer and q.answer.strip()) and i < len(reference_answers):
                raw = reference_answers[i].strip()
                if q.type == QUESTION_TYPES[3]:  # judge
                    if raw in ("对", "正确", "√", "T", "t"):
                        q.answer = "true"
                    elif raw in ("错", "错误", "×", "F", "f"):
                        q.answer = "false"
                    else:
                        q.answer = raw
                else:
                    q.answer = raw
            # 归属大题：找到 start_pos <= pos 的最后一个 section
            if sections:
                section_order, section_title = 1, "题目"
                for so, st, sp in sections:
                    if sp <= pos:
                        section_order, section_title = so, st
                q.section_order = section_order
                q.section_title = section_title
        # 题型与大题标题一致：若小节是「选择题」但被填空提取器误判为 fill-blank（因题干含（ ）），
        # 且内容含 A. B. 选项，则改为 single-choice 并从 content 拆出题干与 options
        for q in questions:
            if q.type != QUESTION_TYPES[2] or not q.section_title or not q.content:  # fill-blank
                continue
            if "选择题" not in q.section_title and "单选题" not in q.section_title:
                continue
            if not content_has_choice_options_common(q.content):
                continue
            stem, options = parse_stem_and_options_from_choice_content_common(q.content)
            if len(options) < 2:
                continue
            q.type = QUESTION_TYPES[0]  # single-choice
            q.content = stem
            q.options = options
        # 将题干/选项/答案/解析中的单换行转为 Markdown 换行，便于前端正确展示
        for q in questions:
            if q.content:
                q.content = to_markdown_line_breaks_common(q.content)
            if q.answer:
                q.answer = to_markdown_line_breaks_common(q.answer)
            if q.explanation:
                q.explanation = to_markdown_line_breaks_common(q.explanation)
            if q.options:
                q.options = [to_markdown_line_breaks_common(o) for o in q.options]
        # 保证题型与系统定义一致，避免写入 question 表时出现非法 type
        for q in questions:
            if q.type not in QUESTION_TYPES:
                logger.warning("Question type %r not in QUESTION_TYPES, fallback to fill-blank", q.type)
                q.type = QUESTION_TYPES[2]  # fill-blank
        return questions
    
    def _extract_single_choice_with_pos(self, text: str, paragraphs: List[str]) -> List[Tuple[QuestionResult, int]]:
        """提取单选题，返回 (题目, 起始位置)。题干与选项在 docx 中为不同段落，content 末尾保留段落断行以反映该格式。"""
        result: List[Tuple[QuestionResult, int]] = []
        pattern = r'(\d+[\.、]?\s*.+?)\s+(A[\.、\s]\s*.+?)\s+(B[\.、\s]\s*.+?)\s+(C[\.、\s]\s*.+?)\s+(D[\.、\s]\s*.+?)\s+答案[：:]\s*([ABCD])'
        for m in re.finditer(pattern, text, re.DOTALL | re.MULTILINE):
            # 题干后在本段与 A. 之间在 docx 中有段落边界，在 markdown 中用 \n\n 反映
            content = m.group(1).strip() + "\n\n"
            oa, ob, oc, od = (re.sub(r'^[A-D][\.、]\s*', '', m.group(i).strip()) for i in range(2, 6))
            ans = m.group(6).strip()
            result.append((
                QuestionResult(type="single-choice", content=content, options=[oa, ob, oc, od], answer=ans, difficulty="medium", grade=1, subject=""),
                m.start(),
            ))
        if not result:
            alt = r'(\d+[\.、]?\s*.+?)\s+A[\.、\s]\s*(.+?)\s+B[\.、\s]\s*(.+?)\s+C[\.、\s]\s*(.+?)\s+D[\.、\s]\s*(.+?)\s+答案[：:]\s*([ABCD])'
            for m in re.finditer(alt, text, re.DOTALL | re.MULTILINE):
                oa, ob, oc, od = (re.sub(r'^[A-D][\.、]\s*', '', m.group(i).strip()) for i in range(2, 6))
                result.append((
                    QuestionResult(type="single-choice", content=m.group(1).strip() + "\n\n", options=[oa, ob, oc, od], answer=m.group(6).strip(), difficulty="medium", grade=1, subject=""),
                    m.start(),
                ))
        return result

    def _extract_multiple_choice_with_pos(self, text: str, paragraphs: List[str]) -> List[Tuple[QuestionResult, int]]:
        """题干与选项在 docx 中为不同段落，content 末尾保留段落断行以反映该格式。"""
        result: List[Tuple[QuestionResult, int]] = []
        pattern = r'(\d+[\.、]?\s*.+?)\s+(A[\.、]\s*.+?)\s+(B[\.、]\s*.+?)\s+(C[\.、]\s*.+?)\s+(D[\.、]\s*.+?)\s+答案[：:]\s*([ABCD]+)'
        for m in re.finditer(pattern, text, re.DOTALL | re.MULTILINE):
            if len(m.group(6)) <= 1:
                continue
            content = m.group(1).strip() + "\n\n"
            oa, ob, oc, od = (re.sub(r'^[A-D][\.、]\s*', '', m.group(i).strip()) for i in range(2, 6))
            result.append((
                QuestionResult(type="multiple-choice", content=content, options=[oa, ob, oc, od], answer=m.group(6).strip(), difficulty="medium", grade=1, subject=""),
                m.start(),
            ))
        return result

    def _extract_fill_blank_with_pos(self, text: str, paragraphs: List[str]) -> List[Tuple[QuestionResult, int]]:
        result: List[Tuple[QuestionResult, int]] = []
        # 答案截断边界：下一小题（\d+[\.、]）或换行+大题标题（一. 二. …）或文末，按格式区分不把下一大题标题吃进答案
        pat = r"(\d+[\.、]?\s*.+?[（(].*?[）)]|.+?___.+?)\s+答案[：:]\s*(.+?)" + ANSWER_END_LOOKAHEAD
        for m in re.finditer(pat, text, re.DOTALL | re.MULTILINE):
            result.append((
                QuestionResult(type="fill-blank", content=m.group(1).strip(), answer=m.group(2).strip(), difficulty="medium", grade=1, subject=""),
                m.start(),
            ))
        current_question, current_content = None, []
        for para in paragraphs:
            para = para.strip()
            if not para:
                continue
            # 大题标题行（如「二.判断题(共6题，共12分)）」不参与题干拼接，避免混入上一题
            if SECTION_HEADER_PATTERN.match(para) or re.match(r"^[一二三四五六七八九十]+[\.．、]\s*[选判填计作解].*$", para):
                if current_question and current_content:
                    content = "\n".join(current_content).strip()
                    if ("（" in content or "(" in content or "）" in content or ")" in content or "___" in content):
                        if not any(q.content == content or content in q.content for q, _ in result):
                            pos = text.find(content) if content in text else len(text)
                            result.append((QuestionResult(type="fill-blank", content=content, answer="", difficulty="medium", grade=1, subject=""), pos))
                current_question, current_content = None, []
                continue
            # 题号支持半角点、全角点、顿号：1. 1． 1、
            if re.match(r'^\d+[\.．、]', para):
                if current_question and current_content:
                    content = "\n".join(current_content).strip()
                    if ("（" in content or "(" in content or "）" in content or ")" in content or "___" in content):
                        if not any(q.content == content or content in q.content for q, _ in result):
                            pos = text.find(content) if content in text else len(text)
                            result.append((QuestionResult(type="fill-blank", content=content, answer="", difficulty="medium", grade=1, subject=""), pos))
                current_question, current_content = para, [para]
            elif current_question:
                if re.match(r'^\d+[\.．、]', para) or "答案" in para:
                    if current_content:
                        content = "\n".join(current_content).strip()
                        if ("（" in content or "(" in content or "）" in content or ")" in content or "___" in content):
                            if not any(q.content == content or content in q.content for q, _ in result):
                                pos = text.find(content) if content in text else len(text)
                                result.append((QuestionResult(type="fill-blank", content=content, answer="", difficulty="medium", grade=1, subject=""), pos))
                    current_question, current_content = (para, [para]) if re.match(r'^\d+[\.．、]', para) else (None, [])
                else:
                    current_content.append(para)
        if current_question and current_content:
            content = "\n".join(current_content).strip()
            if ("（" in content or "(" in content or "）" in content or ")" in content or "___" in content):
                if not any(q.content == content or content in q.content for q, _ in result):
                    pos = text.find(content) if content in text else len(text)
                    result.append((QuestionResult(type="fill-blank", content=content, answer="", difficulty="medium", grade=1, subject=""), pos))
        return result

    def _extract_judge_with_pos(self, text: str, paragraphs: List[str]) -> List[Tuple[QuestionResult, int]]:
        result: List[Tuple[QuestionResult, int]] = []
        pattern = r'(\d+[\.、]?\s*.+?)\s+答案[：:]\s*([对错正确错误√×])'
        for m in re.finditer(pattern, text, re.DOTALL | re.MULTILINE):
            at = m.group(2).strip()
            ans = "true" if at in ("对", "正确", "√") else ("false" if at in ("错", "错误", "×") else "")
            if not ans:
                continue
            result.append((QuestionResult(type="judge", content=m.group(1).strip(), answer=ans, difficulty="medium", grade=1, subject=""), m.start()))
        return result

    def _extract_essay_with_pos(self, text: str, paragraphs: List[str]) -> List[Tuple[QuestionResult, int]]:
        result: List[Tuple[QuestionResult, int]] = []
        pattern = r'(\d+[\.、]?\s*.+?)\s+解析[：:]\s*(.+?)(?=\d+[\.、]|$)'
        for m in re.finditer(pattern, text, re.DOTALL | re.MULTILINE):
            result.append((
                QuestionResult(type="essay", content=m.group(1).strip(), answer="", explanation=m.group(2).strip(), difficulty="medium", grade=1, subject=""),
                m.start(),
            ))
        return result

    def _extract_single_choice(self, text: str, paragraphs: List[str]) -> List[QuestionResult]:
        """提取单选题"""
        questions = []
        
        # 匹配模式：题目 + A/B/C/D选项 + 答案：X
        # 更灵活的模式：允许选项之间有空行，允许不同的分隔符
        pattern = r'(\d+[\.、]?\s*.+?)\s+(A[\.、\s]\s*.+?)\s+(B[\.、\s]\s*.+?)\s+(C[\.、\s]\s*.+?)\s+(D[\.、\s]\s*.+?)\s+答案[：:]\s*([ABCD])'
        
        matches = list(re.finditer(pattern, text, re.DOTALL | re.MULTILINE))
        logger.debug(f"Pattern matched {len(matches)} times for single-choice questions")
        
        if len(matches) == 0:
            logger.debug("No single-choice questions found with primary pattern. Trying alternative patterns...")
            # 尝试更宽松的模式：不要求严格的格式，允许换行
            alt_pattern = r'(\d+[\.、]?\s*.+?)\s+A[\.、\s]\s*(.+?)\s+B[\.、\s]\s*(.+?)\s+C[\.、\s]\s*(.+?)\s+D[\.、\s]\s*(.+?)\s+答案[：:]\s*([ABCD])'
            matches = list(re.finditer(alt_pattern, text, re.DOTALL | re.MULTILINE))
            logger.debug(f"Alternative pattern found {len(matches)} matches")
        
        for match in matches:
            content = match.group(1).strip()
            option_a = match.group(2).strip()
            option_b = match.group(3).strip()
            option_c = match.group(4).strip()
            option_d = match.group(5).strip()
            answer = match.group(6).strip()
            
            # 清理选项格式
            option_a = re.sub(r'^A[\.、]\s*', '', option_a)
            option_b = re.sub(r'^B[\.、]\s*', '', option_b)
            option_c = re.sub(r'^C[\.、]\s*', '', option_c)
            option_d = re.sub(r'^D[\.、]\s*', '', option_d)
            
            questions.append(QuestionResult(
                type="single-choice",
                content=content,
                options=[option_a, option_b, option_c, option_d],
                answer=answer,
                difficulty="medium",
                grade=1,
                subject=""
            ))
        
        return questions
    
    def _extract_multiple_choice(self, text: str, paragraphs: List[str]) -> List[QuestionResult]:
        """提取多选题"""
        questions = []
        
        # 匹配模式：题目 + A/B/C/D选项 + 答案：多个选项（如：AB、ABC等）
        pattern = r'(\d+[\.、]?\s*.+?)\s+(A[\.、]\s*.+?)\s+(B[\.、]\s*.+?)\s+(C[\.、]\s*.+?)\s+(D[\.、]\s*.+?)\s+答案[：:]\s*([ABCD]+)'
        
        matches = re.finditer(pattern, text, re.DOTALL | re.MULTILINE)
        
        for match in matches:
            content = match.group(1).strip()
            option_a = match.group(2).strip()
            option_b = match.group(3).strip()
            option_c = match.group(4).strip()
            option_d = match.group(5).strip()
            answer = match.group(6).strip()
            
            # 多选题答案长度应该大于1
            if len(answer) <= 1:
                continue
            
            # 清理选项格式
            option_a = re.sub(r'^A[\.、]\s*', '', option_a)
            option_b = re.sub(r'^B[\.、]\s*', '', option_b)
            option_c = re.sub(r'^C[\.、]\s*', '', option_c)
            option_d = re.sub(r'^D[\.、]\s*', '', option_d)
            
            questions.append(QuestionResult(
                type="multiple-choice",
                content=content,
                options=[option_a, option_b, option_c, option_d],
                answer=answer,
                difficulty="medium",
                grade=1,
                subject=""
            ))
        
        return questions
    
    def _extract_fill_blank(self, text: str, paragraphs: List[str]) -> List[QuestionResult]:
        """提取填空题"""
        questions = []
        
        # 模式1：有答案的填空题：题目（包含括号或下划线）+ 答案：...
        pattern_with_answer = r'(\d+[\.、]?\s*.+?[（(].*?[）)]|.+?___.+?)\s+答案[：:]\s*(.+?)(?=\d+[\.、]|$)'
        matches = re.finditer(pattern_with_answer, text, re.DOTALL | re.MULTILINE)
        for match in matches:
            content = match.group(1).strip()
            answer = match.group(2).strip()
            questions.append(QuestionResult(
                type="fill-blank",
                content=content,
                answer=answer,
                difficulty="medium",
                grade=1,
                subject=""
            ))
        
        # 模式2：没有答案的填空题
        # 使用段落来帮助识别题目边界
        # 题目通常以数字开头（1、2、3等），包含括号，可能跨多行
        current_question = None
        current_content = []
        
        for para in paragraphs:
            para = para.strip()
            if not para:
                continue
            
            # 检查是否是题目开头（以数字开头）
            if re.match(r'^\d+[\.、]', para):
                # 如果之前有题目，先保存
                if current_question and current_content:
                    content = '\n'.join(current_content).strip()
                    # 检查是否包含括号（填空题特征）
                    if '（' in content or '(' in content or '）' in content or ')' in content or '___' in content:
                        # 检查是否已经被模式1匹配过
                        already_matched = any(q.content == content or content in q.content for q in questions)
                        if not already_matched:
                            questions.append(QuestionResult(
                                type="fill-blank",
                                content=content,
                                answer="",  # 没有答案
                                difficulty="medium",
                                grade=1,
                                subject=""
                            ))
                
                # 开始新题目
                current_question = para
                current_content = [para]
            elif current_question:
                # 继续当前题目
                # 检查是否是下一个题目（以数字开头）或答案字段
                if re.match(r'^\d+[\.、]', para) or '答案' in para:
                    # 保存当前题目
                    if current_content:
                        content = '\n'.join(current_content).strip()
                        if '（' in content or '(' in content or '）' in content or ')' in content or '___' in content:
                            already_matched = any(q.content == content or content in q.content for q in questions)
                            if not already_matched:
                                questions.append(QuestionResult(
                                    type="fill-blank",
                                    content=content,
                                    answer="",
                                    difficulty="medium",
                                    grade=1,
                                    subject=""
                                ))
                    
                    # 如果是新题目，开始新的
                    if re.match(r'^\d+[\.、]', para):
                        current_question = para
                        current_content = [para]
                    else:
                        current_question = None
                        current_content = []
                else:
                    # 继续添加到当前题目
                    current_content.append(para)
        
        # 处理最后一个题目
        if current_question and current_content:
            content = '\n'.join(current_content).strip()
            if '（' in content or '(' in content or '）' in content or ')' in content or '___' in content:
                already_matched = any(q.content == content or content in q.content for q in questions)
                if not already_matched:
                    questions.append(QuestionResult(
                        type="fill-blank",
                        content=content,
                        answer="",
                        difficulty="medium",
                        grade=1,
                        subject=""
                    ))
        
        logger.debug(f"Extracted {len(questions)} fill-blank questions (with answer: {len([q for q in questions if q.answer])}, without answer: {len([q for q in questions if not q.answer])})")
        return questions
    
    def _extract_judge(self, text: str, paragraphs: List[str]) -> List[QuestionResult]:
        """提取判断题"""
        questions = []
        
        # 匹配模式：题目 + 答案：对/错 或 正确/错误
        pattern = r'(\d+[\.、]?\s*.+?)\s+答案[：:]\s*([对错正确错误√×])'
        
        matches = re.finditer(pattern, text, re.DOTALL | re.MULTILINE)
        
        for match in matches:
            content = match.group(1).strip()
            answer_text = match.group(2).strip()
            
            # 转换为标准答案格式
            if answer_text in ["对", "正确", "√"]:
                answer = "true"
            elif answer_text in ["错", "错误", "×"]:
                answer = "false"
            else:
                continue
            
            questions.append(QuestionResult(
                type="judge",
                content=content,
                answer=answer,
                difficulty="medium",
                grade=1,
                subject=""
            ))
        
        return questions
    
    def _extract_essay(self, text: str, paragraphs: List[str]) -> List[QuestionResult]:
        """提取解答题"""
        questions = []
        
        # 匹配模式：题目 + 解析：...
        pattern = r'(\d+[\.、]?\s*.+?)\s+解析[：:]\s*(.+?)(?=\d+[\.、]|$)'
        
        matches = re.finditer(pattern, text, re.DOTALL | re.MULTILINE)
        
        for match in matches:
            content = match.group(1).strip()
            explanation = match.group(2).strip()
            
            questions.append(QuestionResult(
                type="essay",
                content=content,
                answer="",  # 解答题没有标准答案
                explanation=explanation,
                difficulty="medium",
                grade=1,
                subject=""
            ))
        
        return questions
    
    def cleanup(self, file_path: str):
        """清理临时文件"""
        try:
            if os.path.exists(file_path):
                os.remove(file_path)
                logger.info(f"Cleaned up temporary file: {file_path}")
        except Exception as e:
            logger.warning(f"Failed to cleanup file {file_path}: {e}")

