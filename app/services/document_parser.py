"""
Word文档解析服务
使用python-docx解析Word文档
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
    """Word文档解析器"""
    
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
        if not file_name:
            file_name = "document.docx"
        
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
                
                # 检查文件大小
                content_length = response.headers.get("content-length")
                if content_length and int(content_length) > settings.max_file_size:
                    raise ValueError(f"File size {content_length} exceeds maximum {settings.max_file_size}")
                
                # 保存文件
                with open(local_path, "wb") as f:
                    for chunk in response.iter_bytes(chunk_size=settings.download_chunk_size):
                        f.write(chunk)
        
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
        
        logger.info(f"File downloaded successfully: {local_path}")
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
        
        try:
            doc = Document(file_path)
            questions = []
            
            # 提取所有段落文本
            paragraphs = []
            for para in doc.paragraphs:
                text = para.text.strip()
                if text:
                    paragraphs.append(text)
            
            # 合并段落（处理跨段落的题目）
            full_text = "\n".join(paragraphs)
            
            # 识别题目
            questions = self._extract_questions(full_text, paragraphs)
            
            logger.info(f"Extracted {len(questions)} questions from document")
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
        
        # 尝试识别选择题（单选题和多选题）
        single_choice_questions = self._extract_single_choice(full_text, paragraphs)
        questions.extend(single_choice_questions)
        
        # 尝试识别多选题
        multiple_choice_questions = self._extract_multiple_choice(full_text, paragraphs)
        questions.extend(multiple_choice_questions)
        
        # 尝试识别填空题
        fill_blank_questions = self._extract_fill_blank(full_text, paragraphs)
        questions.extend(fill_blank_questions)
        
        # 尝试识别判断题
        judge_questions = self._extract_judge(full_text, paragraphs)
        questions.extend(judge_questions)
        
        # 尝试识别解答题
        essay_questions = self._extract_essay(full_text, paragraphs)
        questions.extend(essay_questions)
        
        return questions
    
    def _extract_single_choice(self, text: str, paragraphs: List[str]) -> List[QuestionResult]:
        """提取单选题"""
        questions = []
        
        # 匹配模式：题目 + A/B/C/D选项 + 答案：X
        pattern = r'(\d+[\.、]?\s*.+?)\s+(A[\.、]\s*.+?)\s+(B[\.、]\s*.+?)\s+(C[\.、]\s*.+?)\s+(D[\.、]\s*.+?)\s+答案[：:]\s*([ABCD])'
        
        matches = re.finditer(pattern, text, re.DOTALL | re.MULTILINE)
        
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
        
        # 匹配模式：题目（包含下划线或括号）+ 答案：...
        pattern = r'(\d+[\.、]?\s*.+?[（(].*?[）)]|.+?___.+?)\s+答案[：:]\s*(.+?)(?=\d+[\.、]|$)'
        
        matches = re.finditer(pattern, text, re.DOTALL | re.MULTILINE)
        
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

