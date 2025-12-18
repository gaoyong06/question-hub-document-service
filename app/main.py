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
    """配置日志 - 同时输出到终端和文件"""
    import os
    from pathlib import Path
    
    logger.remove()  # 移除默认handler
    
    # 确保日志目录存在
    log_file_path = Path(settings.log_file_path)
    log_file_path.parent.mkdir(parents=True, exist_ok=True)
    
    # 日志格式
    if settings.log_format == "json":
        # JSON格式日志
        console_format = "{time} | {level} | {message}"
        file_format = "{time} | {level} | {message}"
        serialize = True
    else:
        # 文本格式日志
        console_format = "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>"
        file_format = "{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} - {message}"
        serialize = False
    
    # 输出到终端（stdout）
    logger.add(
        sys.stdout,
        format=console_format,
        level=settings.log_level,
        serialize=serialize,
        colorize=(settings.log_format != "json")
    )
    
    # 同时输出到文件（如果启用）
    if settings.log_to_file:
        logger.add(
            str(log_file_path),
            format=file_format,
            level=settings.log_level,
            serialize=serialize,
            colorize=False,  # 文件不需要颜色
            rotation="100 MB",  # 日志轮转：100MB
            retention="30 days",  # 保留30天
            compression="zip",  # 压缩旧日志
            encoding="utf-8"
        )
        logger.info(f"Logging to file: {log_file_path}")


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

