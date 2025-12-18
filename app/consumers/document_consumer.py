"""
RocketMQ消息消费者
监听文档转换任务并处理
"""
import json
import os
import traceback
import time
from typing import Optional
from loguru import logger

from rocketmq.client import Producer, PushConsumer, Message, ConsumeStatus

from app.config import settings
from app.models import DocumentConvertMessage, DocumentConvertResultMessage
from app.services.document_parser import DocumentParser
from app.services.markdown_converter import MarkdownConverter, MARKITDOWN_AVAILABLE
from app.services.markdown_parser import MarkdownParser
from app.services.image_processor import ImageProcessor


class DocumentConsumer:
    """文档转换消息消费者"""
    
    def __init__(self):
        self.consumer: Optional[PushConsumer] = None
        self.producer: Optional[Producer] = None
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
            asset_service_url=settings.asset_service_url,
            app_id=settings.asset_service_app_id,
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
            
            # 创建生产者（用于发送结果）
            logger.info("Creating Producer...")
            self.producer = Producer(settings.rocketmq_producer_group)
            self.producer.set_name_server_address(settings.rocketmq_name_server)
            self.producer.start()
            logger.info("Producer started successfully")
            
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
                logger.debug(f"Message tag '{msg_tag}' does not match consume tag '{settings.rocketmq_consume_tag}', skipping")
                return ConsumeStatus.CONSUME_SUCCESS
            
            logger.info(f"Message tag matched: '{msg_tag}' == '{settings.rocketmq_consume_tag}', processing...")
            
            # 解析消息
            body = msg.body.decode('utf-8') if isinstance(msg.body, bytes) else msg.body
            logger.debug(f"Message body: {body[:200]}...")  # 只记录前200个字符
            
            message_data = json.loads(body)
            message = DocumentConvertMessage(**message_data)
            task_id = message.task_id
            
            logger.info(f"Processing conversion task: task_id={task_id}, file_url={message.file_url}")
            
            # 处理文档转换
            questions = self._process_document(message)
            
            # 发送成功结果
            result_message = DocumentConvertResultMessage(
                task_id=task_id,
                status="completed",
                result=questions
            )
            self._send_result(result_message)
            
            logger.info(f"Task completed successfully: task_id={task_id}, questions={len(questions)}")
            
            return ConsumeStatus.CONSUME_SUCCESS
            
        except Exception as e:
            logger.error(f"Failed to process task {task_id}: {e}")
            logger.error(traceback.format_exc())
            
            # 发送失败结果
            if task_id:
                result_message = DocumentConvertResultMessage(
                    task_id=task_id,
                    status="failed",
                    error_msg=str(e)
                )
                try:
                    self._send_result(result_message)
                except Exception as send_err:
                    logger.error(f"Failed to send error result: {send_err}")
            
            # 返回失败，RocketMQ会自动重试
            return ConsumeStatus.RECONSUME_LATER
    
    def _process_document(self, message: DocumentConvertMessage) -> list:
        """处理文档转换"""
        file_path = None
        try:
            # 下载文件
            file_path = self.parser.download_file(message.file_url)
            
            # 判断文件格式
            file_ext = os.path.splitext(file_path)[1].lower()
            
            # Word文档：直接解析
            if file_ext in ['.doc', '.docx']:
                questions = self.parser.parse_document(file_path)
                return questions
            
            # 其他格式：使用MarkItDown转换为Markdown，然后解析
            if not self.markdown_converter:
                raise RuntimeError("MarkItDown is not available. Cannot process non-Word formats.")
            
            if not self.markdown_converter.is_supported_format(file_path):
                raise ValueError(f"Unsupported file format: {file_ext}")
            
            # 转换为Markdown
            markdown_content, metadata = self.markdown_converter.convert_to_markdown(file_path)
            
            # 处理图片：提取、上传、替换路径
            import asyncio
            try:
                # 尝试获取现有的事件循环
                try:
                    loop = asyncio.get_event_loop()
                    if loop.is_running():
                        # 如果事件循环正在运行，创建新的事件循环在另一个线程中运行
                        import concurrent.futures
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
                                        business_type="question_image"
                                    )
                                )
                                result_container['result'] = result
                                new_loop.close()
                            except Exception as e:
                                exception_container['exception'] = e
                        
                        thread = threading.Thread(target=run_in_thread)
                        thread.start()
                        thread.join()
                        
                        if 'exception' in exception_container:
                            raise exception_container['exception']
                        processed_markdown, image_urls = result_container['result']
                    else:
                        processed_markdown, image_urls = loop.run_until_complete(
                            self.image_processor.process_images_in_markdown(
                                markdown_content,
                                document_base_path=os.path.dirname(file_path),
                                business_type="question_image"
                            )
                        )
                except RuntimeError:
                    # 没有事件循环，创建新的
                    processed_markdown, image_urls = asyncio.run(
                        self.image_processor.process_images_in_markdown(
                            markdown_content,
                            document_base_path=os.path.dirname(file_path),
                            business_type="question_image"
                        )
                    )
            except Exception as e:
                logger.warning(f"Failed to process images, continuing without image processing: {e}")
                # 如果图片处理失败，继续使用原始Markdown
                processed_markdown = markdown_content
                image_urls = []
            
            # 从Markdown解析题目
            questions = self.markdown_parser.parse_markdown_to_questions(processed_markdown)
            
            # 将图片URL添加到题目中
            for question in questions:
                if not question.images:
                    question.images = []
                question.images.extend(image_urls)
            
            return questions
            
        finally:
            # 清理临时文件
            if file_path:
                self.parser.cleanup(file_path)
    
    def _send_result(self, result_message: DocumentConvertResultMessage):
        """发送转换结果到RocketMQ"""
        if not self.producer:
            raise RuntimeError("Producer not initialized")
        
        try:
            message_body = json.dumps(
                result_message.model_dump(by_alias=True),
                ensure_ascii=False
            )
            
            msg = Message(settings.rocketmq_topic)
            msg.set_tags(settings.rocketmq_publish_tag)
            msg.set_body(message_body.encode('utf-8'))
            
            result = self.producer.send_sync(msg)
            
            logger.info(f"Sent result message: task_id={result_message.task_id}, status={result_message.status}, msgId={result.msg_id}")
            
        except Exception as e:
            logger.error(f"Failed to send result message: {e}")
            logger.error(traceback.format_exc())
            raise
    
    def close(self):
        """关闭连接"""
        if self.consumer:
            self.consumer.shutdown()
        if self.producer:
            self.producer.shutdown()
        logger.info("RocketMQ connection closed")
