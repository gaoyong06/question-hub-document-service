"""
数据模型定义
与Golang服务保持一致
"""
from typing import Optional, List
from pydantic import BaseModel, Field


class DocumentConvertMessage(BaseModel):
    """文档转换消息（从RabbitMQ接收）"""
    task_id: str = Field(..., alias="task_id")
    merchant_id: str = Field(..., alias="merchant_id")
    file_id: str = Field(..., alias="file_id")
    file_url: str = Field(..., alias="file_url")
    
    class Config:
        populate_by_name = True


class QuestionResult(BaseModel):
    """题目转换结果"""
    type: str  # single-choice, multiple-choice, fill-blank, judge, essay
    content: str
    options: Optional[List[str]] = None
    answer: str
    explanation: Optional[str] = None
    images: Optional[List[str]] = None
    difficulty: str = "medium"  # easy, medium, hard
    grade: int = 1  # 1-9
    subject: str = ""  # 数学、语文、英语等
    tags: Optional[List[str]] = None


class DocumentConvertResultMessage(BaseModel):
    """文档转换结果消息（发送到RabbitMQ）"""
    task_id: str = Field(..., alias="task_id")
    status: str = Field(..., alias="status")  # completed, failed
    result: Optional[List[QuestionResult]] = Field(default=None, alias="result")
    error_msg: Optional[str] = Field(default=None, alias="error_msg")
    
    class Config:
        populate_by_name = True

