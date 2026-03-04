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
    """题目转换结果，type 与 question-hub-service 表 question.type 一致"""
    type: str  # single-choice, multiple-choice, fill-blank, judge, essay（见 document_parser.QUESTION_TYPES）
    content: str
    options: Optional[List[str]] = None
    answer: str
    explanation: Optional[str] = None
    images: Optional[List[str]] = None
    difficulty: str = "medium"  # easy, medium, hard
    grade: int = 1  # 1-9
    subject: str = ""  # 数学、语文、英语等
    tags: Optional[List[str]] = None
    # 试卷层级：所属大题标题与顺序，供下游按 section 分组建卷
    section_title: Optional[str] = None
    section_order: Optional[int] = None


class DocumentConvertResultMessage(BaseModel):
    """文档转换结果消息（发送到RabbitMQ）"""
    task_id: str = Field(..., alias="task_id")
    status: str = Field(..., alias="status")  # completed, failed
    result: Optional[List[QuestionResult]] = Field(default=None, alias="result")
    error_msg: Optional[str] = Field(default=None, alias="error_msg")
    # 文档内标题，供下游作为试卷名称（优先于文件名）
    document_title: Optional[str] = Field(default=None, alias="document_title")
    # 文档内「注意事项」区块内容，供下游作为试卷 description；未解析到则为空/不传
    document_description: Optional[str] = Field(default=None, alias="document_description")
    # 识别出的年级（1-9），未识别为 0 或不传
    document_grade: Optional[int] = Field(default=None, alias="document_grade")
    # 识别出的学科（如数学、语文），未识别为空或不传
    document_subject: Optional[str] = Field(default=None, alias="document_subject")

    class Config:
        populate_by_name = True

