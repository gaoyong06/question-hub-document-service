"""
主程序入口
启动RocketMQ消费者
"""
import signal
import sys
from loguru import logger

from app.config import settings
from app.consumers.document_consumer import DocumentConsumer


def setup_logging():
    """配置日志"""
    logger.remove()  # 移除默认handler
    
    if settings.log_format == "json":
        # JSON格式日志
        logger.add(
            sys.stdout,
            format="{time} | {level} | {message}",
            level=settings.log_level,
            serialize=True
        )
    else:
        # 文本格式日志
        logger.add(
            sys.stdout,
            format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>",
            level=settings.log_level,
            colorize=True
        )


def main():
    """主函数"""
    setup_logging()
    
    logger.info(f"Starting {settings.service_name} v{settings.service_version}")
    logger.info(f"RocketMQ NameServer: {settings.rocketmq_name_server}")
    logger.info(f"Topic: {settings.rocketmq_topic}")
    logger.info(f"Consumer Group: {settings.rocketmq_consumer_group}")
    logger.info(f"Consume Tag: {settings.rocketmq_consume_tag}")
    
    consumer = DocumentConsumer()
    
    # 注册信号处理
    def signal_handler(sig, frame):
        logger.info("Received interrupt signal, shutting down...")
        consumer.close()
        sys.exit(0)
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    try:
        # 连接RocketMQ
        consumer.connect()
        
        # 开始消费消息
        consumer.start_consuming()
        
    except Exception as e:
        logger.error(f"Failed to start consumer: {e}")
        import traceback
        logger.error(traceback.format_exc())
        consumer.close()
        sys.exit(1)


if __name__ == "__main__":
    main()

