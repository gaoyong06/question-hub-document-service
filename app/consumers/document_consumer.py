"""
RocketMQ消息消费者
监听文档转换任务并处理
"""
import json
import os
import traceback
import time
from typing import Optional, Tuple, List
from loguru import logger
import httpx

from rocketmq.client import PushConsumer, ConsumeStatus

from app.config import settings
from app.models import DocumentConvertMessage, QuestionResult
from app.services.document_parser import DocumentParser
from app.services.markdown_converter import MarkdownConverter, MARKITDOWN_AVAILABLE
from app.services.markdown_parser import MarkdownParser
from app.services.image_processor import ImageProcessor


class DocumentConsumer:
    """文档转换消息消费者"""
    
    def __init__(self):
        self.consumer: Optional[PushConsumer] = None
        self.parser = DocumentParser()
        self.markdown_converter = (
            MarkdownConverter(
                enable_ocr=settings.enable_ocr,
                azure_docintel_endpoint=settings.azure_docintel_endpoint,
                azure_docintel_key=settings.azure_docintel_key
            ) if MARKITDOWN_AVAILABLE else None
        )
        self.markdown_parser = MarkdownParser()
        self.image_processor = ImageProcessor(
            asset_service_url=settings.asset_service_api_base_url,
            app_id=settings.app_id,
            user_id=""  # 可以从消息中获取
        )
    
    def connect(self):
        """连接到RocketMQ"""
        try:
            logger.info(f"Connecting to RocketMQ NameServer: {settings.rocketmq_name_server}")
            logger.info(f"Topic: {settings.rocketmq_topic}")
            logger.info(f"Consumer Group: {settings.rocketmq_consumer_group}")
            logger.info(f"Consume Tag: {settings.rocketmq_consume_tag}")
            
            # 创建消费者
            logger.info("Creating PushConsumer...")
            self.consumer = PushConsumer(settings.rocketmq_consumer_group)
            logger.info(f"PushConsumer created: {self.consumer}")
            
            logger.info(f"Setting NameServer address: {settings.rocketmq_name_server}")
            self.consumer.set_name_server_address(settings.rocketmq_name_server)
            logger.info("NameServer address set successfully")
            logger.info("Connected to RocketMQ successfully")
            
        except Exception as e:
            logger.error(f"Failed to connect to RocketMQ: {e}")
            logger.error(traceback.format_exc())
            raise
    
    def start_consuming(self):
        """开始消费消息"""
        if not self.consumer:
            raise RuntimeError("Not connected to RocketMQ")
        
        logger.info("Starting to consume messages...")
        
        # 订阅消息，使用回调函数处理
        # Python RocketMQ 客户端支持在 subscribe 时指定 tag
        # 格式：subscribe(topic, tag, callback)
        # 如果不指定 tag，则接收所有消息
        logger.info(f"Subscribing to topic={settings.rocketmq_topic}, tag={settings.rocketmq_consume_tag}")
        
        # Python RocketMQ 客户端的 subscribe 方法只接受 (topic, callback) 参数
        # tag 过滤需要在 handler 中手动处理
        # 参考：https://github.com/apache/rocketmq-client-python
        logger.info(f"Subscribing to topic={settings.rocketmq_topic} (tag filtering will be done in handler)")
        self.consumer.subscribe(
            settings.rocketmq_topic,
            self._handle_message
        )
        logger.info(f"Successfully subscribed to topic={settings.rocketmq_topic} (will filter tag '{settings.rocketmq_consume_tag}' in handler)")
        
        # 启动消费者（必须在 subscribe 之后）
        logger.info("Starting consumer...")
        self.consumer.start()
        logger.info("Consumer started successfully")
        
        logger.info("Waiting for messages. To exit press CTRL+C")
        
        # 添加心跳日志，确认消费者正常运行
        last_heartbeat = time.time()
        heartbeat_interval = 30  # 每30秒输出一次心跳日志
        
        try:
            # 保持运行
            while True:
                time.sleep(1)
                # 每30秒输出一次心跳日志
                current_time = time.time()
                if current_time - last_heartbeat >= heartbeat_interval:
                    logger.debug(f"Consumer is alive, waiting for messages... (elapsed: {int(current_time - last_heartbeat)}s)")
                    last_heartbeat = current_time
        except KeyboardInterrupt:
            logger.info("Stopping consumer...")
            self.close()
    
    def _handle_message(self, msg) -> ConsumeStatus:
        """处理接收到的消息"""
        task_id = None
        try:
            # 记录收到的所有消息（用于调试）
            logger.info("=" * 60)
            logger.info("MESSAGE RECEIVED - Starting to process")
            
            # 尝试多种方式获取 tag
            msg_tag = None
            if hasattr(msg, 'get_tags'):
                msg_tag = msg.get_tags()
            elif hasattr(msg, 'tags'):
                msg_tag = msg.tags
            elif hasattr(msg, 'get_property'):
                msg_tag = msg.get_property('TAGS')
            
            # 如果 tag 是字节串，转换为字符串
            if isinstance(msg_tag, bytes):
                msg_tag = msg_tag.decode('utf-8')
            
            # 获取消息的所有属性（用于调试）
            msg_attrs = {}
            if hasattr(msg, '__dict__'):
                msg_attrs = {k: str(v)[:100] for k, v in msg.__dict__.items() if not k.startswith('_')}
            
            logger.info(f"Message details: topic={getattr(msg, 'topic', 'unknown')}, tag={msg_tag} (type: {type(msg_tag).__name__}), msgId={getattr(msg, 'msg_id', getattr(msg, 'msgId', 'unknown'))}")
            logger.debug(f"Message attributes: {msg_attrs}")
            
            # 检查 Tag 是否匹配（只处理 document.convert 消息）
            # 注意：Python RocketMQ 客户端可能不支持 tag 过滤，所以在这里严格过滤
            if not msg_tag:
                logger.warning(f"Message has no tag, skipping. Expected tag: {settings.rocketmq_consume_tag}")
                return ConsumeStatus.CONSUME_SUCCESS
            
            if msg_tag != settings.rocketmq_consume_tag:
                logger.info(f"Message tag '{msg_tag}' does not match consume tag '{settings.rocketmq_consume_tag}', skipping")
                return ConsumeStatus.CONSUME_SUCCESS
            
            logger.info(f"Message tag matched: '{msg_tag}' == '{settings.rocketmq_consume_tag}', processing...")
            
            # 解析消息
            body = msg.body.decode('utf-8') if isinstance(msg.body, bytes) else msg.body
            logger.debug(f"Message body: {body[:200]}...")  # 只记录前200个字符
            
            message_data = json.loads(body)
            message = DocumentConvertMessage(**message_data)
            task_id = message.task_id
            
            logger.info(f"Processing conversion task: task_id={task_id}, file_url={message.file_url}")
            
            # 处理文档转换（含识别出的年级、学科；markdown_content 为 MarkItDown 转换结果，供持久化与对比）
            questions, document_title, document_description, document_grade, document_subject, markdown_content = self._process_document(message)

            # 仅通过 HTTP API 提交结果，不发 MQ
            paper_id = self._submit_result_via_api(
                task_id=task_id,
                status="completed",
                questions=questions,
                document_title=document_title or "",
                document_description=(document_description or "").strip(),
                document_grade=document_grade if document_grade and 1 <= document_grade <= 9 else 0,
                document_subject=(document_subject or "").strip(),
                markdown_content=(markdown_content or "").strip(),
            )
            logger.info(f"Task completed successfully: task_id={task_id}, questions={len(questions)}, paper_id={paper_id or ''}")
            
            return ConsumeStatus.CONSUME_SUCCESS
            
        except Exception as e:
            logger.error(f"Failed to process task {task_id}: {e}")
            logger.error(traceback.format_exc())
            
            # 失败时也通过 API 上报（更新 conversion_task 状态与 error_msg），不发 MQ
            if task_id:
                try:
                    self._submit_result_via_api(
                        task_id=task_id,
                        status="failed",
                        questions=[],
                        document_title="",
                        document_description="",
                        document_grade=0,
                        document_subject="",
                        markdown_content="",
                        error_msg=str(e),
                    )
                except Exception as api_err:
                    logger.error("Failed to submit failed result via API: %s", api_err)
            return ConsumeStatus.RECONSUME_LATER
    
    async def _upload_docx_images_and_replace_placeholders(
        self,
        questions: list,
        image_paths: list,
        business_type: str = "question_image",
    ) -> list:
        """上传 docx 中提取的图片，将题目 content 中的 {{IMAGE_N}} 替换为 Markdown 图片并写入 question.images。"""
        import re
        replacements = {}  # placeholder -> url
        for i, path in enumerate(image_paths):
            placeholder = "{{IMAGE_%d}}" % i
            try:
                url = await self.image_processor.upload_image_to_asset_service(path, business_type)
                replacements[placeholder] = url
            except Exception as e:
                logger.warning("Upload image %s failed: %s", path, e)
        markdown_by_ph = {ph: "![image](%s)" % url for ph, url in replacements.items()}
        for q in questions:
            # 题干、参考答案、解析中均可能含图片占位符，统一替换
            for ph, url in replacements.items():
                markdown_img = markdown_by_ph[ph]
                if q.content and ph in q.content:
                    q.content = q.content.replace(ph, markdown_img)
                    if q.images is None:
                        q.images = []
                    q.images.append(url)
                if q.answer and ph in q.answer:
                    q.answer = q.answer.replace(ph, markdown_img)
                if q.explanation and ph in q.explanation:
                    q.explanation = q.explanation.replace(ph, markdown_img)
        return questions

    def _process_document(self, message: DocumentConvertMessage) -> tuple:
        """处理文档转换。返回 (题目列表, document_title, document_description, document_grade, document_subject, markdown_content)。
        markdown_content 为 MarkItDown 转换后的原文（仅当已转换时非空），用于与 python-docx 效果对比与持久化。"""
        file_path = None
        document_title = ""
        document_description = ""
        document_grade = 0
        document_subject = ""
        markdown_content = ""
        try:
            # 下载文件
            file_path = self.parser.download_file(message.file_url)
            
            # 统一流程：所有格式 → MarkItDown → 图片上传替换 → Markdown 流式解析
            file_ext = os.path.splitext(file_path)[1].lower()
            if not self.markdown_converter:
                raise RuntimeError("MarkItDown is not available.")
            if not self.markdown_converter.is_supported_format(file_path):
                raise ValueError(f"Unsupported file format: {file_ext}")

            # 1. 转为 Markdown（含 .doc/.docx）
            markdown_content, metadata = self.markdown_converter.convert_to_markdown(file_path)

            # 2. 图片：data URL / 本地 / 网络 → 上传 asset-service，在 markdown 中替换为 URL
            import asyncio
            try:
                try:
                    loop = asyncio.get_event_loop()
                    if loop.is_running():
                        import threading
                        result_container = {}
                        exception_container = {}

                        def run_in_thread():
                            try:
                                new_loop = asyncio.new_event_loop()
                                asyncio.set_event_loop(new_loop)
                                result = new_loop.run_until_complete(
                                    self.image_processor.process_images_in_markdown(
                                        markdown_content,
                                        document_base_path=os.path.dirname(file_path),
                                        business_type="question_image",
                                    )
                                )
                                result_container["result"] = result
                                new_loop.close()
                            except Exception as e:
                                exception_container["exception"] = e

                        t = threading.Thread(target=run_in_thread)
                        t.start()
                        t.join()
                        if "exception" in exception_container:
                            raise exception_container["exception"]
                        processed_markdown, _ = result_container["result"]
                    else:
                        processed_markdown, _ = loop.run_until_complete(
                            self.image_processor.process_images_in_markdown(
                                markdown_content,
                                document_base_path=os.path.dirname(file_path),
                                business_type="question_image",
                            )
                        )
                except RuntimeError:
                    processed_markdown, _ = asyncio.run(
                        self.image_processor.process_images_in_markdown(
                            markdown_content,
                            document_base_path=os.path.dirname(file_path),
                            business_type="question_image",
                        )
                    )
            except Exception as e:
                logger.warning("Failed to process images, continuing with raw markdown: %s", e)
                processed_markdown = markdown_content

            # 3. Markdown 流式解析为试卷（标题、注意事项、大题、小题、参考答案任意位置）
            questions, document_title, document_description, document_grade, document_subject = (
                self.markdown_parser.parse_markdown_to_exam(processed_markdown)
            )
            if not document_title and metadata:
                document_title = (metadata.get("title", "") or "").strip()
            return (
                questions,
                document_title or "",
                document_description or "",
                document_grade,
                document_subject or "",
                markdown_content or "",
            )

        finally:
            # 清理临时文件
            if file_path:
                self.parser.cleanup(file_path)
    
    def _submit_result_via_api(
        self,
        task_id: str,
        status: str,
        questions: List[QuestionResult],
        document_title: str,
        document_description: str,
        document_grade: int,
        document_subject: str,
        markdown_content: str,
        error_msg: str = "",
    ) -> Optional[str]:
        """通过 HTTP API 将转换结果提交到 question-hub-service（结果落库），成功返回 paper_id，失败无返回值。"""
        base = (settings.question_hub_api_base_url or "").rstrip("/")
        if not base:
            raise RuntimeError("QUESTION_HUB_SERVICE_API_BASE_URL is not set")
        url = f"{base}/question-hub/v1/questions/convert/{task_id}/result"
        # 与 question-hub-service API 的 JSON 字段一致（camelCase）
        result_list = []
        for q in questions:
            result_list.append({
                "type": q.type,
                "content": q.content,
                "options": q.options or [],
                "answer": q.answer,
                "explanation": q.explanation or "",
                "images": q.images or [],
                "difficulty": getattr(q, "difficulty", "medium") or "medium",
                "grade": getattr(q, "grade", 1) or 1,
                "subject": getattr(q, "subject", "") or "",
                "tags": q.tags or [],
                "sectionTitle": q.section_title or "",
                "sectionOrder": q.section_order or 0,
            })
        body = {
            "status": status,
            "result": result_list,
            "errorMsg": error_msg,
            "documentTitle": document_title,
            "documentDescription": document_description,
            "documentGrade": document_grade,
            "documentSubject": document_subject,
            "markdownContent": markdown_content,
        }
        try:
            with httpx.Client(timeout=60.0) as client:
                resp = client.put(url, json=body)
                resp.raise_for_status()
                data = resp.json()
                return (data.get("paperId") or "").strip() or None
        except Exception as e:
            logger.error("Submit result via API failed: %s", e)
            raise

    def close(self):
        """关闭连接"""
        if self.consumer:
            self.consumer.shutdown()
        logger.info("RocketMQ connection closed")
