#!/bin/bash

# Python文档识别服务启动脚本

# 获取脚本所在目录
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR" || exit 1

# 检查虚拟环境
if [ ! -d "venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv venv
fi

# 激活虚拟环境
source venv/bin/activate

# 升级 pip
pip install --upgrade pip -q

# 安装依赖
echo "Installing dependencies..."
pip install -r requirements.txt -q

# 检查.env文件
if [ ! -f ".env" ]; then
    echo "Creating .env file..."
    cat > .env << EOF
# RocketMQ配置
ROCKETMQ_NAME_SERVER=localhost:9876
ROCKETMQ_TOPIC=question_hub
ROCKETMQ_CONSUMER_GROUP=question_hub_document_consumer
ROCKETMQ_PRODUCER_GROUP=question_hub_document_producer

# 消息路由
ROCKETMQ_CONSUME_TAG=document.convert
ROCKETMQ_PUBLISH_TAG=document.convert.result

# 服务配置
SERVICE_NAME=question-hub-document-service
SERVICE_VERSION=1.0.0
LOG_LEVEL=INFO
LOG_FORMAT=text

# 临时文件目录
TEMP_FILE_DIR=/tmp/question-hub-documents
EOF
    echo ".env file created. Please edit if needed."
fi

# 创建临时目录
mkdir -p /tmp/question-hub-documents

# 启动服务
echo "Starting document service..."
python -m app.main

