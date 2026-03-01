"""
文档解析服务
使用python-docx解析Word文档（.doc, .docx）
其他格式通过MarkItDown转换为Markdown后解析
"""
import os
import re
import tempfile
import zipfile
import uuid
from typing import List, Optional, Tuple
from pathlib import Path
from docx import Document
from docx.oxml.ns import qn
from loguru import logger

from app.models import QuestionResult
from app.config import settings

# 参考答案区块标题的多种写法
REFERENCE_ANSWER_HEADERS = ("参考答案", "答案", "标准答案")

# 大题标题模式：一.选择题(共6题，共12分)、二.判断题、六.解答题 等（整行）
SECTION_HEADER_PATTERN = re.compile(
    r"^([一二三四五六七八九十]+)[\.．、]\s*(.+)$",
    re.MULTILINE,
)


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
            # HTTP/HTTPS: 通过HTTP下载
            with httpx.Client(timeout=settings.download_timeout) as client:
                response = client.get(file_url)
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
    
    def parse_document(self, file_path: str) -> Tuple[List[QuestionResult], List[str]]:
        """
        解析Word文档，提取题目与图片路径（图片需由调用方上传并替换占位符）。

        Args:
            file_path: 本地文件路径

        Returns:
            (题目列表, 图片本地路径列表)。题目 content 中可能含 {{IMAGE_0}}、{{IMAGE_1}} 等占位符，
            与 image_paths 下标对应；调用方上传后替换为实际 URL。
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
            # 带图片占位符的段落文本、以及本 doc 中出现的图片本地路径（按出现顺序）
            paragraphs, image_paths = self._extract_paragraphs_with_images(doc, file_path)

            full_text = "\n".join(paragraphs)
            if full_text:
                preview = full_text[:1000] if len(full_text) > 1000 else full_text
                logger.info("Extracted text preview (first 1000 chars):\n%s", preview)
                logger.info("Full text length: %s characters", len(full_text))
            else:
                logger.warning("No text content extracted from document")

            # 解析卷末「参考答案」区块，得到按题号顺序的答案列表
            reference_answers = self._parse_reference_answers(full_text)
            logger.info("Parsed reference answers count: %s", len(reference_answers))

            # 识别大题标题与位置（用于给题目打上 section_title / section_order）
            sections = self._detect_sections(full_text)
            logger.info("Detected sections count: %s", len(sections))

            # 识别题目（返回带文档内位置的题目，便于按顺序回填答案与归属大题）
            questions_with_pos = self._extract_questions_with_positions(full_text, paragraphs)
            # 按文档顺序排序、回填参考答案并归属大题
            questions = self._sort_and_fill_answers(questions_with_pos, reference_answers, sections)

            logger.info("Extracted %s questions from document", len(questions))
            if len(questions) == 0 and paragraphs:
                logger.warning("No questions extracted, but document has content. Check regex patterns.")
                logger.info("First 5 paragraphs:\n%s", "\n".join(paragraphs[:5]))

            return questions, image_paths

        except Exception as e:
            logger.error("Failed to parse document: %s", e)
            raise

    def _extract_paragraphs_with_images(self, doc: Document, file_path: str) -> Tuple[List[str], List[str]]:
        """
        提取段落文本，并将文档中的内嵌图片导出为临时文件，在文本中用 {{IMAGE_N}} 占位。
        返回 (段落列表（含占位符）, 图片本地路径列表，与占位符下标对应)。
        """
        image_paths: List[str] = []
        image_dir = self.temp_dir / "docx_images"
        image_dir.mkdir(parents=True, exist_ok=True)

        def save_image_blob(blob: bytes, ext: str = ".png") -> str:
            path = image_dir / f"{uuid.uuid4().hex}{ext}"
            path.write_bytes(blob)
            return str(path)

        paragraphs: List[str] = []
        for para in doc.paragraphs:
            parts: List[str] = []
            for run in para.runs:
                if run._element is None:
                    if run.text:
                        parts.append(run.text)
                    continue
                r_id = None
                # DrawingML 图片：w:drawing -> a:blip @r:embed
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
                # VML 图片（WPS/Word 旧格式）：w:pict -> v:imagedata @r:id
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

        # 若 python-docx 未暴露 related_parts/blob，则退化为从 zip 解压 word/media
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
                # 按文档中引用顺序：遍历 document.xml 找 r:embed 顺序（与 media 名对应需通过 rels）
                # 此处简化：若从 zip 解压，则占位符按 media 顺序插入到全文末尾会错位，故仅当上面 drawing 路径已取到图时才插占位符；zip 路径仅作补充提取，不插占位符
                # 若需 zip 路径也插占位符，需解析 document.xml 与 rels 确定顺序，这里先不实现
            except Exception as e:
                logger.debug("Fallback zip media extraction failed: %s", e)

        return paragraphs, image_paths

    def _parse_reference_answers(self, full_text: str) -> List[str]:
        """
        从全文中的「参考答案」区块解析出按题号顺序的答案列表。
        支持格式：参考答案 1. A  2. B  3. 对  … 或 1．A  2．B 等。
        """
        out: List[str] = []
        # 定位「参考答案」区块：优先「参考答案」，其次「标准答案」，最后「答案」（避免误匹配题干中的「答案」）
        idx = full_text.find("参考答案")
        if idx == -1:
            idx = full_text.find("标准答案")
        if idx == -1:
            idx = full_text.find("答案")
        if idx == -1:
            return out
        header = "参考答案" if full_text.find("参考答案") == idx else ("标准答案" if full_text.find("标准答案") == idx else "答案")

        block = full_text[idx:]
        # 去掉首行仅含标题的情况（若首行还有题号答案则保留）
        first_nl = block.find("\n")
        if first_nl != -1:
            first_line = block[:first_nl]
            # 首行去掉「参考答案」等字样后若只剩空白，则从下一行开始解析
            rest_first = first_line.replace(header, "").strip()
            if not rest_first or not re.search(r"\d+[\.．、]", rest_first):
                block = block[first_nl + 1:]
            # 否则 block 保持从 idx 开始（含首行「参考答案 1.A 2.B」）
        else:
            block = re.sub(r"^[\s\S]*?" + re.escape(header), "", block, count=1).strip()

        # 按「数字.」或「数字．」分割出每题答案
        pattern = re.compile(r"\d+[\.．、]\s*")
        parts = pattern.split(block)
        for i, seg in enumerate(parts):
            ans = re.sub(r"^[\s\u3000]+|[\s\u3000]+$", "", seg)
            if not ans:
                continue
            # 跳过明显是大题标题的行（如「一.选择题」「二.判断题」），避免混入答案列表
            if SECTION_HEADER_PATTERN.match(ans) or re.match(r"^[一二三四五六七八九十]+[\.．、]\s*[选判填计作解].*$", ans):
                continue
            out.append(ans)
        return out

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
        检测大题标题及其在全文中的起始位置。
        返回 [(section_order, section_title, start_pos), ...]，按 start_pos 递增。
        """
        sections: List[Tuple[int, int, str]] = []  # (pos, order, title)
        order_map = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
        for m in SECTION_HEADER_PATTERN.finditer(full_text):
            cn_num, rest = m.group(1), m.group(2).strip()
            order = order_map.get(cn_num, len(sections) + 1)
            title = (cn_num + "." + rest) if not rest.startswith(".") else (cn_num + rest)
            sections.append((m.start(), order, title))
        sections.sort(key=lambda x: x[0])
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
                if q.type == "judge":
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
        return questions
    
    def _extract_single_choice_with_pos(self, text: str, paragraphs: List[str]) -> List[Tuple[QuestionResult, int]]:
        """提取单选题，返回 (题目, 起始位置)。"""
        result: List[Tuple[QuestionResult, int]] = []
        pattern = r'(\d+[\.、]?\s*.+?)\s+(A[\.、\s]\s*.+?)\s+(B[\.、\s]\s*.+?)\s+(C[\.、\s]\s*.+?)\s+(D[\.、\s]\s*.+?)\s+答案[：:]\s*([ABCD])'
        for m in re.finditer(pattern, text, re.DOTALL | re.MULTILINE):
            content = m.group(1).strip()
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
                    QuestionResult(type="single-choice", content=m.group(1).strip(), options=[oa, ob, oc, od], answer=m.group(6).strip(), difficulty="medium", grade=1, subject=""),
                    m.start(),
                ))
        return result

    def _extract_multiple_choice_with_pos(self, text: str, paragraphs: List[str]) -> List[Tuple[QuestionResult, int]]:
        result: List[Tuple[QuestionResult, int]] = []
        pattern = r'(\d+[\.、]?\s*.+?)\s+(A[\.、]\s*.+?)\s+(B[\.、]\s*.+?)\s+(C[\.、]\s*.+?)\s+(D[\.、]\s*.+?)\s+答案[：:]\s*([ABCD]+)'
        for m in re.finditer(pattern, text, re.DOTALL | re.MULTILINE):
            if len(m.group(6)) <= 1:
                continue
            oa, ob, oc, od = (re.sub(r'^[A-D][\.、]\s*', '', m.group(i).strip()) for i in range(2, 6))
            result.append((
                QuestionResult(type="multiple-choice", content=m.group(1).strip(), options=[oa, ob, oc, od], answer=m.group(6).strip(), difficulty="medium", grade=1, subject=""),
                m.start(),
            ))
        return result

    def _extract_fill_blank_with_pos(self, text: str, paragraphs: List[str]) -> List[Tuple[QuestionResult, int]]:
        result: List[Tuple[QuestionResult, int]] = []
        pat = r'(\d+[\.、]?\s*.+?[（(].*?[）)]|.+?___.+?)\s+答案[：:]\s*(.+?)(?=\d+[\.、]|$)'
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

