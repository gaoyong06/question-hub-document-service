"""
Markdown转换服务
使用MarkItDown将各种格式文档转换为Markdown
"""
import os
from pathlib import Path
from typing import Optional
from loguru import logger

try:
    from markitdown import MarkItDown
    MARKITDOWN_AVAILABLE = True
except ImportError:
    MARKITDOWN_AVAILABLE = False
    logger.warning("MarkItDown not installed, PDF/PPT/Excel conversion will not be available")

from app.config import settings


class MarkdownConverter:
    """Markdown转换器，使用MarkItDown"""
    
    def __init__(self, enable_ocr: bool = True, azure_docintel_endpoint: Optional[str] = None, azure_docintel_key: Optional[str] = None):
        """
        初始化Markdown转换器
        
        Args:
            enable_ocr: 是否启用OCR（默认True，MarkItDown会自动检测并使用）
            azure_docintel_endpoint: Azure Document Intelligence端点（可选，提升OCR准确率）
            azure_docintel_key: Azure Document Intelligence密钥（可选）
        """
        if not MARKITDOWN_AVAILABLE:
            raise ImportError("MarkItDown is not installed. Please install it with: pip install 'markitdown[all]>=0.1.4'")
        
        # 配置MarkItDown
        # MarkItDown会自动启用OCR（如果安装了相关依赖）
        # 如果提供了Azure Document Intelligence配置，会使用Azure OCR提升准确率
        md_kwargs = {}
        if azure_docintel_endpoint and azure_docintel_key:
            md_kwargs['docintel_endpoint'] = azure_docintel_endpoint
            md_kwargs['docintel_key'] = azure_docintel_key
            logger.info("Using Azure Document Intelligence for enhanced OCR")
        elif enable_ocr:
            logger.info("OCR will be automatically enabled by MarkItDown if dependencies are available")
        
        self.md = MarkItDown(**md_kwargs)
        self.temp_dir = Path(settings.temp_file_dir)
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        self.enable_ocr = enable_ocr
    
    def convert_to_markdown(self, file_path: str) -> tuple[str, dict]:
        """
        将文档转换为Markdown
        
        Args:
            file_path: 本地文件路径
            
        Returns:
            (markdown_content, metadata) 元组
            - markdown_content: Markdown格式的内容
            - metadata: 文档元数据（包含图片路径等信息）
        """
        logger.info(f"Converting document to markdown: {file_path}")
        
        try:
            result = self.md.convert(file_path)
            
            markdown_content = result.text_content
            metadata = result.metadata if hasattr(result, 'metadata') else {}
            
            logger.info(f"Conversion completed: {len(markdown_content)} characters")
            return markdown_content, metadata
            
        except Exception as e:
            logger.error(f"Failed to convert document to markdown: {e}")
            raise
    
    def is_supported_format(self, file_path: str) -> bool:
        """
        检查文件格式是否支持
        
        Args:
            file_path: 文件路径
            
        Returns:
            是否支持
        """
        if not MARKITDOWN_AVAILABLE:
            return False
        
        ext = Path(file_path).suffix.lower()
        supported_extensions = [
            '.pdf', '.doc', '.docx',
            '.ppt', '.pptx',
            '.xls', '.xlsx',
            '.jpg', '.jpeg', '.png', '.gif', '.bmp',
            '.txt', '.html', '.csv', '.json', '.xml',
            '.epub'
        ]
        return ext in supported_extensions
    
    def get_file_format(self, file_path: str) -> str:
        """
        获取文件格式类型
        
        Args:
            file_path: 文件路径
            
        Returns:
            格式类型: 'word', 'pdf', 'ppt', 'excel', 'image', 'text', 'other'
        """
        ext = Path(file_path).suffix.lower()
        
        if ext in ['.doc', '.docx']:
            return 'word'
        elif ext == '.pdf':
            return 'pdf'
        elif ext in ['.ppt', '.pptx']:
            return 'ppt'
        elif ext in ['.xls', '.xlsx']:
            return 'excel'
        elif ext in ['.jpg', '.jpeg', '.png', '.gif', '.bmp']:
            return 'image'
        elif ext in ['.txt', '.html', '.csv', '.json', '.xml']:
            return 'text'
        else:
            return 'other'

