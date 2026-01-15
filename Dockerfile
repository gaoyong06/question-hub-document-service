# 多阶段构建 Dockerfile for Question Hub Document Service
# ⚠️ 注意：此 Dockerfile 需要从项目根目录构建
# 构建命令：docker build -f question-hub-document-service/Dockerfile -t image:tag .
FROM python:3.11-slim

WORKDIR /app

# 安装系统依赖
RUN apt-get update && apt-get install -y \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# 复制依赖文件（从项目根目录构建）
COPY question-hub-document-service/requirements.txt .

# 安装Python依赖
RUN pip install --no-cache-dir -r requirements.txt

# 复制应用代码（从项目根目录构建）
COPY question-hub-document-service/app/ ./app/

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

