# 多阶段构建 Dockerfile for Question Hub Document Service
# 构建命令：docker build -f Dockerfile -t image:tag .
FROM python:3.11-slim

WORKDIR /app

# 安装系统依赖（含 LibreOffice 用于 .doc → .docx 转换，供 python-docx 路径使用）
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    wget \
    libreoffice-writer \
    && rm -rf /var/lib/apt/lists/*

# 安装 RocketMQ C++ 客户端库
RUN wget https://github.com/apache/rocketmq-client-cpp/releases/download/2.2.0/rocketmq-client-cpp-2.2.0.amd64.deb -O rocketmq-client-cpp.deb \
    && apt-get install -y ./rocketmq-client-cpp.deb \
    && rm rocketmq-client-cpp.deb

# 复制依赖文件
COPY requirements.txt .

# 安装 Python 依赖（--timeout 避免下载大包如 magika/onnxruntime 时 Read timed out）
RUN pip install --no-cache-dir --timeout 300 -r requirements.txt

# 复制应用代码
COPY app/ ./app/

# 创建临时目录
RUN mkdir -p /tmp/question-hub-documents

# 设置环境变量
ENV PYTHONUNBUFFERED=1
ENV LOG_FORMAT=json
ENV LOG_LEVEL=INFO

# 暴露端口
EXPOSE 8122

# 运行服务
CMD ["python", "-m", "app.main"]
