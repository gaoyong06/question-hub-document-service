#!/usr/bin/env python3
"""
本地验证：MarkItDown 转换 docx → markdown，并验证图片（含 data URL）处理正常。
用法（在项目根目录，先激活 venv）:
  source .venv/bin/activate   # 或 venv/bin/activate
  python scripts/test_markdown_images_local.py [docx路径]
默认使用: /Users/gaoyong/Downloads/题库/一年级-数学/一年级上册数学期末测试卷（达标题）.docx
"""
import asyncio
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

def main():
    try:
        from loguru import logger
    except ImportError:
        import logging
        logging.basicConfig(level=logging.INFO)
        logger = logging.getLogger(__name__)
        logger.warning("Run from project venv for full deps (pip install -r requirements.txt)")

    docx_path = sys.argv[1] if len(sys.argv) > 1 else "/Users/gaoyong/Downloads/题库/一年级-数学/一年级上册数学期末测试卷（达标题）.docx"
    if not os.path.isfile(docx_path):
        logger.error("File not found: %s", docx_path)
        sys.exit(1)

    # 1. MarkItDown 转 markdown（keep_data_uris=True 保留完整 base64，否则图片会被截断为占位）
    try:
        from markitdown import MarkItDown
        md = MarkItDown()
        result = md.convert(docx_path, keep_data_uris=True)
        markdown_content = result.text_content
        logger.info("MarkItDown converted, markdown length: %s", len(markdown_content))
    except Exception as e:
        logger.exception("MarkItDown failed: %s", e)
        sys.exit(2)

    # 2. 提取图片（含 data URL）
    from app.config import settings
    from app.services.image_processor import ImageProcessor

    processor = ImageProcessor(
        asset_service_url=settings.asset_service_url,
        app_id=settings.app_id or "test",
        user_id="",
    )
    images = processor.extract_images_from_markdown(markdown_content)
    data_url_count = sum(1 for path, _ in images if path.startswith("data:"))
    logger.info("Extracted %s images, %s are data URL", len(images), data_url_count)

    # 3. 验证 data URL 解码并保存为临时文件（不依赖 asset-service）
    for i, (image_path, alt_text) in enumerate(images):
        if image_path.startswith("data:image/"):
            try:
                local = processor._save_data_url_to_temp_file(image_path)
                size = os.path.getsize(local)
                logger.info("Data URL #%s decoded to %s, size=%s bytes", i + 1, local, size)
                try:
                    os.remove(local)
                except OSError:
                    pass
            except Exception as e:
                logger.exception("Data URL #%s decode failed: %s", i + 1, e)
                sys.exit(3)

    # 4. 若配置了 asset_service_url 且非占位，则执行完整上传并替换
    if images and settings.asset_service_url and "localhost" not in settings.asset_service_url:
        async def run_upload():
            processed, urls = await processor.process_images_in_markdown(
                markdown_content,
                document_base_path=os.path.dirname(docx_path),
                business_type="question_image",
            )
            return processed, urls

        try:
            processed, urls = asyncio.run(run_upload())
            logger.info("Uploaded %s images, got %s URLs", len(images), len(urls))
            # 检查替换后是否还有 data: 残留
            if "data:image/" in processed:
                logger.warning("Processed markdown still contains data:image/ (replace may have failed)")
            else:
                logger.info("All data URLs replaced with URLs in markdown")
            print("\n--- Processed markdown (first 800 chars) ---\n")
            print(processed[:800])
        except Exception as e:
            logger.warning("Full upload/replace skipped or failed (asset-service): %s", e)
    else:
        logger.info("Skipping full upload (no asset_service_url or localhost). Data URL decode OK.")

if __name__ == "__main__":
    main()
