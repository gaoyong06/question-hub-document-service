"""
图片处理服务
提取Markdown中的图片，上传到asset-service，并替换路径
"""
import re
import os
import httpx
from typing import List, Tuple, Dict, Optional
from pathlib import Path
from loguru import logger

from app.config import settings


class ImageProcessor:
    """图片处理器"""
    
    def __init__(self, asset_service_url: str, app_id: str = "", user_id: str = ""):
        """
        初始化图片处理器
        
        Args:
            asset_service_url: asset-service的URL
            app_id: 应用ID（可选）
            user_id: 用户ID（可选）
        """
        self.asset_service_url = asset_service_url.rstrip('/')
        self.app_id = app_id
        self.user_id = user_id
        self.temp_dir = Path(settings.temp_file_dir) / "images"
        self.temp_dir.mkdir(parents=True, exist_ok=True)
    
    def extract_images_from_markdown(self, markdown_content: str) -> List[Tuple[str, str]]:
        """
        从Markdown中提取图片引用
        
        Args:
            markdown_content: Markdown内容
            
        Returns:
            List of (image_path, alt_text) tuples
        """
        # 匹配Markdown图片语法: ![alt text](path/to/image.png)
        pattern = r'!\[([^\]]*)\]\(([^)]+)\)'
        matches = re.findall(pattern, markdown_content)
        
        images = []
        for alt_text, image_path in matches:
            images.append((image_path, alt_text))
        
        logger.info(f"Extracted {len(images)} images from markdown")
        return images
    
    async def download_image(self, image_url: str, local_path: str) -> str:
        """
        下载图片到本地临时目录
        
        Args:
            image_url: 图片URL（可能是相对路径或绝对URL）
            local_path: 本地保存路径
            
        Returns:
            本地文件路径
        """
        try:
            # 如果是相对路径，需要结合文档所在目录
            if not image_url.startswith(('http://', 'https://')):
                # 相对路径，需要从文档目录解析
                logger.warning(f"Relative image path detected: {image_url}, may need document base path")
                return image_url
            
            logger.info(f"Downloading image from {image_url}")
            
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(image_url)
                response.raise_for_status()
                
                # 确保目录存在
                os.makedirs(os.path.dirname(local_path), exist_ok=True)
                
                # 保存图片
                with open(local_path, 'wb') as f:
                    f.write(response.content)
                
                logger.info(f"Image downloaded successfully: {local_path}")
                return local_path
                
        except Exception as e:
            logger.error(f"Failed to download image {image_url}: {e}")
            raise
    
    async def upload_image_to_asset_service(
        self,
        image_path: str,
        business_type: str = "question_image"
    ) -> str:
        """
        上传图片到asset-service
        
        Args:
            image_path: 本地图片路径
            business_type: 业务类型
            
        Returns:
            图片URL（asset-service返回的URL）
        """
        try:
            logger.info(f"Uploading image to asset-service: {image_path}")
            
            # 读取图片文件
            with open(image_path, 'rb') as f:
                image_data = f.read()
            
            # 准备FormData
            files = {
                'file': (os.path.basename(image_path), image_data, 'image/png')
            }
            data = {
                'business_type': business_type,
                'source': 'question_hub_document_service'
            }
            
            # 调用asset-service API
            async with httpx.AsyncClient(timeout=60.0) as client:
                headers = {}
                if self.app_id:
                    headers['X-App-ID'] = self.app_id
                if self.user_id:
                    headers['X-User-ID'] = self.user_id
                
                response = await client.post(
                    f"{self.asset_service_url}/asset/v1/files",
                    files=files,
                    data=data,
                    headers=headers
                )
                response.raise_for_status()
                
                result = response.json()
                
                # 解析响应格式: { success: true, data: { fileId, fileName, ... } }
                if result.get('success') and result.get('data'):
                    # 获取文件URL（可能需要调用getFileURL接口）
                    file_id = result['data'].get('fileId')
                    if file_id:
                        # 返回文件ID，实际URL可以通过getFileURL获取
                        # 或者直接返回fileId，让前端调用getFileURL
                        file_url = f"{self.asset_service_url}/asset/v1/files/{file_id}/url"
                        logger.info(f"Image uploaded successfully: file_id={file_id}")
                        return file_url
                    else:
                        raise ValueError("Response does not contain fileId")
                else:
                    error_msg = result.get('errorMessage') or result.get('message') or 'Unknown error'
                    raise ValueError(f"Upload failed: {error_msg}")
                    
        except Exception as e:
            logger.error(f"Failed to upload image to asset-service: {e}")
            raise
    
    def replace_images_in_markdown(
        self,
        markdown_content: str,
        image_replacements: Dict[str, str]
    ) -> str:
        """
        替换Markdown中的图片路径
        
        Args:
            markdown_content: 原始Markdown内容
            image_replacements: 图片路径映射 {old_path: new_url}
            
        Returns:
            替换后的Markdown内容
        """
        result = markdown_content
        
        for old_path, new_url in image_replacements.items():
            # 转义特殊字符用于正则表达式
            escaped_old_path = re.escape(old_path)
            # 匹配: ![alt](old_path) 并替换为 ![alt](new_url)
            pattern = rf'!\[([^\]]*)\]\({escaped_old_path}\)'
            replacement = rf'![\1]({new_url})'
            result = re.sub(pattern, replacement, result)
        
        logger.info(f"Replaced {len(image_replacements)} image paths in markdown")
        return result
    
    async def process_images_in_markdown(
        self,
        markdown_content: str,
        document_base_path: Optional[str] = None,
        business_type: str = "question_image"
    ) -> Tuple[str, List[str]]:
        """
        处理Markdown中的所有图片：提取、上传、替换
        
        Args:
            markdown_content: Markdown内容
            document_base_path: 文档所在目录（用于解析相对路径）
            business_type: 业务类型
            
        Returns:
            (processed_markdown, image_urls) 元组
            - processed_markdown: 处理后的Markdown（图片路径已替换）
            - image_urls: 上传后的图片URL列表
        """
        # 提取图片
        images = self.extract_images_from_markdown(markdown_content)
        
        if not images:
            logger.info("No images found in markdown")
            return markdown_content, []
        
        image_replacements = {}
        uploaded_urls = []
        
        for image_path, alt_text in images:
            try:
                # 如果是相对路径，需要结合文档目录
                if document_base_path and not image_path.startswith(('http://', 'https://')):
                    full_image_path = os.path.join(document_base_path, image_path)
                else:
                    full_image_path = image_path
                
                # 下载图片（如果是URL）
                if image_path.startswith(('http://', 'https://')):
                    local_image_path = self.temp_dir / os.path.basename(image_path)
                    await self.download_image(image_path, str(local_image_path))
                    image_to_upload = str(local_image_path)
                else:
                    # 本地路径
                    image_to_upload = full_image_path
                
                # 上传到asset-service
                uploaded_url = await self.upload_image_to_asset_service(
                    image_to_upload,
                    business_type
                )
                
                # 记录替换映射
                image_replacements[image_path] = uploaded_url
                uploaded_urls.append(uploaded_url)
                
                # 清理临时文件
                if image_path.startswith(('http://', 'https://')):
                    try:
                        os.remove(image_to_upload)
                    except:
                        pass
                
            except Exception as e:
                logger.error(f"Failed to process image {image_path}: {e}")
                # 继续处理其他图片，不中断整个流程
                continue
        
        # 替换Markdown中的图片路径
        processed_markdown = self.replace_images_in_markdown(markdown_content, image_replacements)
        
        return processed_markdown, uploaded_urls

