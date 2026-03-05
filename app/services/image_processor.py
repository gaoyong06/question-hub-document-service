"""
图片处理服务
提取Markdown中的图片，上传到asset-service，并替换路径
支持：本地路径、http(s) URL、data URL（data:image/...;base64,...）
"""
import json
import os
import re
import base64
import uuid
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
            asset_service_url: 调用 asset-service 接口的 base URL（内网地址，如 http://asset-service:8104）
            app_id: 应用ID（可选）
            user_id: 用户ID（可选）
        """
        self.asset_service_url = asset_service_url.rstrip("/")
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

    def _save_data_url_to_temp_file(self, data_url: str) -> str:
        """
        将 data:image/...;base64,XXX 解码并保存为临时文件，返回本地路径。
        """
        if not data_url.startswith("data:image/"):
            raise ValueError("Not a data URL or unsupported image type")
        # data:image/png;base64,iVBORw0KG...
        comma = data_url.find(",")
        if comma == -1:
            raise ValueError("Invalid data URL: no comma")
        header = data_url[:comma].lower()
        ext = ".png"
        if "jpeg" in header or "jpg" in header:
            ext = ".jpg"
        elif "gif" in header:
            ext = ".gif"
        elif "webp" in header:
            ext = ".webp"
        b64 = data_url[comma + 1 :]
        raw = base64.b64decode(b64)
        path = self.temp_dir / f"{uuid.uuid4().hex}{ext}"
        path.write_bytes(raw)
        return str(path)

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
            
            # asset-service 要求请求必须带 X-App-Id，否则返回 400（与 question-hub-web 一致）
            # 使用 service.app_id（或环境变量 SERVICE_APP_ID），默认 question_hub
            effective_app_id = (self.app_id or os.environ.get("SERVICE_APP_ID") or "question_hub").strip()
            if not effective_app_id:
                raise ValueError(
                    "app_id is required for upload (set service.app_id or SERVICE_APP_ID in config, same as question-hub APP_ID)"
                )

            # 调用 asset-service 上传接口（正确路径为 /asset/v1/files/upload，multipart）
            # 表单字段与 question-hub-web 一致：file + metadata(business_type, source)
            upload_url = f"{self.asset_service_url}/asset/v1/files/upload"
            logger.info(
                "Uploading image to asset-service: image_path={}, app_id={}, url={}",
                image_path,
                effective_app_id,
                upload_url,
            )
            async with httpx.AsyncClient(timeout=60.0) as client:
                headers = {"X-App-Id": effective_app_id}
                if self.user_id:
                    headers["X-User-ID"] = self.user_id

                # 打印请求是否带 X-App-Id 及其值，便于排查 400
                has_app_id = "X-App-Id" in headers
                app_id_value = headers.get("X-App-Id", "")
                logger.info(
                    "asset-service upload request: X-App-Id present={}, X-App-Id value={}",
                    has_app_id,
                    app_id_value if app_id_value else "(empty)",
                )

                response = await client.post(
                    upload_url,
                    files=files,
                    data=data,
                    headers=headers,
                )

                # 非 2xx 时记录响应体，便于排查 400 等原因；突出 errorCode/errorMessage/traceId 便于与 asset-service 日志对照
                if not response.is_success:
                    try:
                        body = response.text
                    except Exception:
                        body = "(unable to read response body)"
                    try:
                        err_json = json.loads(body)
                        code = err_json.get("errorCode") or ""
                        msg = err_json.get("errorMessage") or err_json.get("message") or ""
                        trace_id = err_json.get("traceId") or ""
                        logger.error(
                            "asset-service upload failed: image_path={}, status_code={}, errorCode={}, errorMessage={}, traceId={} (check asset-service logs for this traceId); full_response={}",
                            image_path,
                            response.status_code,
                            code,
                            msg,
                            trace_id,
                            body,
                        )
                    except Exception:
                        logger.error(
                            "asset-service upload failed: image_path={}, status_code={}, response_body={}",
                            image_path,
                            response.status_code,
                            body,
                        )
                    response.raise_for_status()

                result = response.json()

                # 解析响应格式: { success: true, data: { fileId, url, ... } }，直接使用 asset-service 返回的 url（由 asset-service 负责生成可访问的公开 URL）
                if result.get("success") and result.get("data"):
                    data = result["data"]
                    file_url = data.get("url") or ""
                    if file_url:
                        logger.info("Image uploaded successfully: image_path={}, file_id={}", image_path, data.get("fileId"))
                        return file_url
                    raise ValueError("asset-service response does not contain url (check asset-service storage.local.base_url config)")
                error_msg = result.get("errorMessage") or result.get("message") or "Unknown error"
                raise ValueError(f"Upload failed: {error_msg}")

        except httpx.HTTPStatusError as e:
            # 已在上面记录过 response_body，这里只补充 image_path 避免歧义
            logger.error(
                "Failed to upload image to asset-service: image_path={}, status={}, error={}",
                image_path,
                e.response.status_code if e.response else None,
                str(e),
            )
            raise
        except Exception as e:
            logger.error(
                "Failed to upload image to asset-service: image_path={}, error={}",
                image_path,
                str(e),
            )
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
            # data URL 或超长路径用简单字符串替换，避免正则性能问题
            if old_path.startswith("data:") or len(old_path) > 2000:
                result = result.replace("](" + old_path + ")", "](" + new_url + ")")
            else:
                escaped_old_path = re.escape(old_path)
                pattern = rf'!\[([^\]]*)\]\({escaped_old_path}\)'
                replacement = rf'![\1]({new_url})'
                result = re.sub(pattern, replacement, result)

        logger.info("Replaced %s image paths in markdown", len(image_replacements))
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
            image_to_upload = None  # 仅 data URL 或 http 下载时会生成临时文件，需后续删除
            try:
                # data URL：MarkItDown 等转换常输出 ![](data:image/png;base64,...)
                if image_path.startswith("data:image/"):
                    image_to_upload = self._save_data_url_to_temp_file(image_path)
                # 相对路径：结合文档目录
                elif document_base_path and not image_path.startswith(('http://', 'https://')):
                    image_to_upload = os.path.join(document_base_path, image_path)
                else:
                    image_to_upload = image_path

                # 下载图片（仅 http(s) 需要下载）
                if image_path.startswith(('http://', 'https://')):
                    local_image_path = self.temp_dir / (os.path.basename(image_path) or f"{uuid.uuid4().hex}.png")
                    await self.download_image(image_path, str(local_image_path))
                    image_to_upload = str(local_image_path)

                # 上传到 asset-service
                uploaded_url = await self.upload_image_to_asset_service(
                    image_to_upload,
                    business_type
                )

                # 记录替换映射（key 为 markdown 中的原串，便于精确替换）
                image_replacements[image_path] = uploaded_url
                uploaded_urls.append(uploaded_url)

                # 清理临时文件（data URL 或 http 下载产生的）
                if image_path.startswith(("data:image/", "http://", "https://")) and image_to_upload and os.path.isfile(image_to_upload):
                    try:
                        os.remove(image_to_upload)
                    except OSError:
                        pass

            except Exception as e:
                logger.error("Failed to process image (path_len=%s): %s", len(image_path), e)
                # 继续处理其他图片，不中断整个流程
                continue
        
        # 替换Markdown中的图片路径
        processed_markdown = self.replace_images_in_markdown(markdown_content, image_replacements)
        
        return processed_markdown, uploaded_urls

