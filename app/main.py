"""
主程序入口
启动RocketMQ消费者
"""
import signal
import sys
from pathlib import Path
from loguru import logger
from fastapi import FastAPI
from fastapi.responses import JSONResponse
import uvicorn
import threading

from app.config import settings
from app.consumers.document_consumer import DocumentConsumer


# 创建FastAPI应用
app = FastAPI(
    title=settings.service_name,
    version=settings.service_version,
    description="文档识别服务 - 支持多种文档格式（Word、PDF、PowerPoint、Excel、图片、文本、EPUB等），自动提取题目信息"
)

# 全局消费者实例（用于健康检查）
_consumer_instance = None


@app.get("/")
async def root():
    """根路径 - 服务信息"""
    return {
        "service": settings.service_name,
        "version": settings.service_version,
        "status": "running"
    }


@app.get("/health")
async def health_check():
    """健康检查端点"""
    health_status = {
        "status": "healthy",
        "service": settings.service_name,
        "version": settings.service_version,
        "checks": {}
    }
    
    # 检查临时目录
    try:
        temp_dir = Path(settings.temp_file_dir)
        temp_dir.mkdir(parents=True, exist_ok=True)
        # 尝试写入测试文件
        test_file = temp_dir / ".health_check"
        test_file.write_text("ok")
        test_file.unlink()
        health_status["checks"]["temp_directory"] = {
            "status": "healthy",
            "path": str(temp_dir)
        }
    except Exception as e:
        health_status["status"] = "unhealthy"
        health_status["checks"]["temp_directory"] = {
            "status": "unhealthy",
            "error": str(e)
        }
    
    # 检查RocketMQ连接
    try:
        if _consumer_instance and _consumer_instance.consumer:
            health_status["checks"]["rocketmq"] = {
                "status": "healthy",
                "name_server": settings.rocketmq_name_server,
                "topic": settings.rocketmq_topic,
                "consumer_group": settings.rocketmq_consumer_group
            }
        else:
            health_status["status"] = "unhealthy"
            health_status["checks"]["rocketmq"] = {
                "status": "unhealthy",
                "error": "Consumer not initialized"
            }
    except Exception as e:
        health_status["status"] = "unhealthy"
        health_status["checks"]["rocketmq"] = {
            "status": "unhealthy",
            "error": str(e)
        }
    
    # 返回适当的HTTP状态码
    status_code = 200 if health_status["status"] == "healthy" else 503
    return JSONResponse(content=health_status, status_code=status_code)


@app.get("/ready")
async def readiness_check():
    """就绪检查端点（Kubernetes readiness probe）"""
    if _consumer_instance and _consumer_instance.consumer:
        return {"status": "ready"}
    return JSONResponse(
        content={"status": "not ready", "reason": "Consumer not initialized"},
        status_code=503
    )


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
    global _consumer_instance
    
    setup_logging()
    
    logger.info(f"Starting {settings.service_name} v{settings.service_version}")
    logger.info(f"RocketMQ NameServer: {settings.rocketmq_name_server}")
    logger.info(f"Topic: {settings.rocketmq_topic}")
    logger.info(f"Consumer Group: {settings.rocketmq_consumer_group}")
    logger.info(f"Consume Tag: {settings.rocketmq_consume_tag}")
    
    consumer = DocumentConsumer()
    _consumer_instance = consumer
    
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
        
        # 在单独的线程中启动FastAPI服务器
        def run_fastapi():
            uvicorn.run(
                app,
                host="0.0.0.0",
                port=8121,
                log_level="info"
            )
        
        fastapi_thread = threading.Thread(target=run_fastapi, daemon=True)
        fastapi_thread.start()
        logger.info("FastAPI server started on http://0.0.0.0:8121")
        logger.info("Health check: http://0.0.0.0:8121/health")
        
        # 开始消费消息（阻塞主线程）
        consumer.start_consuming()
        
    except Exception as e:
        logger.error(f"Failed to start consumer: {e}")
        import traceback
        logger.error(traceback.format_exc())
        consumer.close()
        sys.exit(1)


if __name__ == "__main__":
    main()

