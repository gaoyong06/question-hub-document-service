"""
配置管理
"""
import os
from typing import Optional
from pydantic_settings import BaseSettings
from pydantic import Field


class Settings(BaseSettings):
    """应用配置"""
    
    # 服务配置
    service_name: str = Field(default="question-hub-document-service", alias="SERVICE_NAME")
    service_version: str = Field(default="1.0.0", alias="SERVICE_VERSION")
    
    # RocketMQ配置
    rocketmq_name_server: str = Field(
        default="localhost:9876",
        alias="ROCKETMQ_NAME_SERVER"
    )
    rocketmq_topic: str = Field(
        default="question_hub",
        alias="ROCKETMQ_TOPIC"
    )
    rocketmq_consumer_group: str = Field(
        default="question_hub_document_consumer",
        alias="ROCKETMQ_CONSUMER_GROUP"
    )
    rocketmq_producer_group: str = Field(
        default="question_hub_document_producer",
        alias="ROCKETMQ_PRODUCER_GROUP"
    )
    rocketmq_consume_tag: str = Field(
        default="document.convert",
        alias="ROCKETMQ_CONSUME_TAG"
    )
    rocketmq_publish_tag: str = Field(
        default="document.convert.result",
        alias="ROCKETMQ_PUBLISH_TAG"
    )
    
    # 文件下载配置
    download_timeout: int = Field(default=300, alias="DOWNLOAD_TIMEOUT")  # 5分钟
    download_chunk_size: int = Field(default=8192, alias="DOWNLOAD_CHUNK_SIZE")
    temp_file_dir: str = Field(default="/tmp/question-hub-documents", alias="TEMP_FILE_DIR")
    
    # 日志配置
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    log_format: str = Field(default="json", alias="LOG_FORMAT")  # json or text
    
    # 题目识别配置
    max_file_size: int = Field(default=50 * 1024 * 1024, alias="MAX_FILE_SIZE")  # 50MB
    supported_extensions: list[str] = Field(
        default_factory=lambda: [
            ".docx", ".doc",  # Word
            ".pdf",  # PDF
            ".ppt", ".pptx",  # PowerPoint
            ".xls", ".xlsx",  # Excel
            ".jpg", ".jpeg", ".png", ".gif", ".bmp",  # 图片
            ".txt", ".html", ".csv", ".json", ".xml",  # 文本
            ".epub"  # EPUB
        ],
        alias="SUPPORTED_EXTENSIONS"
    )
    
    # Asset Service配置
    asset_service_url: str = Field(
        default="http://localhost:8104",
        alias="ASSET_SERVICE_URL"
    )
    asset_service_app_id: str = Field(
        default="",
        alias="ASSET_SERVICE_APP_ID"
    )
    
    # OCR配置（可选）
    enable_ocr: bool = Field(
        default=True,
        alias="ENABLE_OCR"
    )
    azure_docintel_endpoint: Optional[str] = Field(
        default=None,
        alias="AZURE_DOCINTEL_ENDPOINT"
    )
    azure_docintel_key: Optional[str] = Field(
        default=None,
        alias="AZURE_DOCINTEL_KEY"
    )
    
    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = False


# 全局配置实例
settings = Settings()

