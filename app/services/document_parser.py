"""
文档解析服务
使用python-docx解析Word文档（.doc, .docx）
其他格式通过MarkItDown转换为Markdown后解析
"""
import os
import re
import tempfile
from typing import List, Optional
from pathlib import Path
from docx import Document
from docx.shared import Inches
from loguru import logger

from app.models import QuestionResult
from app.config import settings


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
    
    def parse_document(self, file_path: str) -> List[QuestionResult]:
        """
        解析Word文档，提取题目
        
        Args:
            file_path: 本地文件路径
            
        Returns:
            题目列表
        """
        logger.info(f"Parsing document: {file_path}")
        
        # 检查文件是否存在（在打开之前再次确认，避免并发问题）
        if not os.path.exists(file_path):
            logger.error(f"File does not exist at path: {file_path}")
            raise FileNotFoundError(f"File does not exist: {file_path}")
        
        # 检查文件大小
        file_size = os.path.getsize(file_path)
        logger.info(f"File exists. Path: {file_path}, Size: {file_size} bytes.")
        
        if file_size == 0:
            logger.error(f"File is empty: {file_path}")
            raise ValueError(f"File is empty: {file_path}")
        
        # 检查文件签名
        with open(file_path, 'rb') as f:
            first_bytes = f.read(4)
            if first_bytes[:2] != b'PK':
                logger.error(f"File signature mismatch. Expected PK, got {first_bytes!r}.")
                raise ValueError(f"File is not a valid ZIP/DOCX file (signature: {first_bytes})")
            logger.info(f"File signature (PK) confirmed before parsing.")
        
        try:
            # 在打开文件之前再次确认文件存在（避免并发删除问题）
            if not os.path.exists(file_path):
                logger.error(f"File was deleted before parsing: {file_path}")
                raise FileNotFoundError(f"File was deleted before parsing: {file_path}")
            
            # 使用文件路径打开 Document（python-docx 内部会再次检查文件）
            doc = Document(file_path)
            questions = []
            
            # 提取所有段落文本
            paragraphs = []
            for para in doc.paragraphs:
                text = para.text.strip()
                if text:
                    paragraphs.append(text)
            
            logger.info(f"Extracted {len(paragraphs)} paragraphs from document")
            
            # 合并段落（处理跨段落的题目）
            full_text = "\n".join(paragraphs)
            
            # 记录提取的文本内容（前1000个字符，用于调试）
            if full_text:
                preview = full_text[:1000] if len(full_text) > 1000 else full_text
                logger.info(f"Extracted text preview (first 1000 chars):\n{preview}")
                logger.info(f"Full text length: {len(full_text)} characters")
            else:
                logger.warning("No text content extracted from document!")
            
            # 识别题目
            questions = self._extract_questions(full_text, paragraphs)
            
            logger.info(f"Extracted {len(questions)} questions from document")
            if len(questions) == 0 and len(paragraphs) > 0:
                logger.warning("No questions extracted, but document has content. Check regex patterns.")
                # 显示前几个段落，帮助调试
                logger.info(f"First 5 paragraphs:\n" + "\n".join(paragraphs[:5]))
            return questions
            
        except Exception as e:
            logger.error(f"Failed to parse document: {e}")
            raise
    
    def _extract_questions(self, full_text: str, paragraphs: List[str]) -> List[QuestionResult]:
        """
        从文本中提取题目
        
        Args:
            full_text: 完整文本
            paragraphs: 段落列表
            
        Returns:
            题目列表
        """
        questions = []
        
        logger.info("Starting question extraction...")
        
        # 尝试识别选择题（单选题和多选题）
        single_choice_questions = self._extract_single_choice(full_text, paragraphs)
        logger.info(f"Extracted {len(single_choice_questions)} single-choice questions")
        questions.extend(single_choice_questions)
        
        # 尝试识别多选题
        multiple_choice_questions = self._extract_multiple_choice(full_text, paragraphs)
        logger.info(f"Extracted {len(multiple_choice_questions)} multiple-choice questions")
        questions.extend(multiple_choice_questions)
        
        # 尝试识别填空题
        fill_blank_questions = self._extract_fill_blank(full_text, paragraphs)
        logger.info(f"Extracted {len(fill_blank_questions)} fill-blank questions")
        questions.extend(fill_blank_questions)
        
        # 尝试识别判断题
        judge_questions = self._extract_judge(full_text, paragraphs)
        logger.info(f"Extracted {len(judge_questions)} judge questions")
        questions.extend(judge_questions)
        
        # 尝试识别解答题
        essay_questions = self._extract_essay(full_text, paragraphs)
        logger.info(f"Extracted {len(essay_questions)} essay questions")
        questions.extend(essay_questions)
        
        return questions
    
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

