"""
RocketMQ消息消费者
监听文档转换任务并处理
"""
import json
import traceback
import time
from typing import Optional
from loguru import logger

from rocketmq.client import Producer, PushConsumer, Message, ConsumeStatus

from app.config import settings
from app.models import DocumentConvertMessage, DocumentConvertResultMessage
from app.services.document_parser import DocumentParser


class DocumentConsumer:
    """文档转换消息消费者"""
    
    def __init__(self):
        self.consumer: Optional[PushConsumer] = None
        self.producer: Optional[Producer] = None
        self.parser = DocumentParser()
    
    def connect(self):
        """连接到RocketMQ"""
        try:
            logger.info(f"Connecting to RocketMQ NameServer: {settings.rocketmq_name_server}")
            logger.info(f"Topic: {settings.rocketmq_topic}")
            logger.info(f"Consumer Group: {settings.rocketmq_consumer_group}")
            logger.info(f"Consume Tag: {settings.rocketmq_consume_tag}")
            
            # 创建消费者
            self.consumer = PushConsumer(settings.rocketmq_consumer_group)
            self.consumer.set_name_server_address(settings.rocketmq_name_server)
            
            # 创建生产者（用于发送结果）
            self.producer = Producer(settings.rocketmq_producer_group)
            self.producer.set_name_server_address(settings.rocketmq_name_server)
            self.producer.start()
            
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
        self.consumer.subscribe(
            settings.rocketmq_topic,
            self._handle_message
        )
        
        # 启动消费者
        self.consumer.start()
        
        logger.info("Waiting for messages. To exit press CTRL+C")
        
        try:
            # 保持运行
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            logger.info("Stopping consumer...")
            self.close()
    
    def _handle_message(self, msg) -> ConsumeStatus:
        """处理接收到的消息"""
        task_id = None
        try:
            # 解析消息
            body = msg.body.decode('utf-8') if isinstance(msg.body, bytes) else msg.body
            message_data = json.loads(body)
            message = DocumentConvertMessage(**message_data)
            task_id = message.task_id
            
            logger.info(f"Received conversion task: task_id={task_id}, file_url={message.file_url}")
            
            # 检查 Tag 是否匹配
            msg_tag = msg.get_tags() if hasattr(msg, 'get_tags') else None
            if msg_tag and msg_tag != settings.rocketmq_consume_tag:
                logger.debug(f"Message tag {msg_tag} does not match consume tag {settings.rocketmq_consume_tag}, skipping")
                return ConsumeStatus.CONSUME_SUCCESS
            
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
            
            # 解析文档
            questions = self.parser.parse_document(file_path)
            
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
