#!/bin/bash

# Python文档识别服务启动脚本

set -e  # 遇到错误立即退出

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# 日志函数
log_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

log_step() {
    echo -e "\n${BLUE}========================================${NC}"
    echo -e "${BLUE}$1${NC}"
    echo -e "${BLUE}========================================${NC}\n"
}

# 获取脚本所在目录
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR" || exit 1

# 解析命令行参数
VERBOSE=0
if [ "$1" = "--verbose" ] || [ "$1" = "-v" ]; then
    VERBOSE=1
    log_step "Question Hub Document Service 启动脚本（详细模式）"
    log_info "工作目录: $SCRIPT_DIR"
elif [ "$1" = "--help" ] || [ "$1" = "-h" ]; then
    echo "用法: $0 [选项]"
    echo ""
    echo "选项:"
    echo "  -v, --verbose    显示详细的启动信息"
    echo "  -h, --help       显示此帮助信息"
    echo ""
    echo "快速启动（默认）: 跳过不必要的检查，快速启动服务"
    echo "详细模式: 显示所有检查步骤和配置信息"
    exit 0
else
    # 快速启动模式，只显示关键信息
    log_info "Question Hub Document Service 启动中..."
fi

# 检查 Python 版本（快速检查）
if ! command -v python3 &> /dev/null; then
    log_error "Python3 未安装，请先安装 Python 3.9+"
    exit 1
fi

# 仅在详细模式下显示版本信息
if [ "$1" = "--verbose" ] || [ "$1" = "-v" ]; then
    log_step "检查 Python 环境"
    PYTHON_VERSION=$(python3 --version 2>&1)
    log_info "Python 版本: $PYTHON_VERSION"
fi

# 检查虚拟环境（优先使用 venv310，如果不存在则使用 venv）
VENV_DIR="venv"
if [ -d "venv310" ]; then
    VENV_DIR="venv310"
    if [ "$VERBOSE" = "1" ]; then
        log_info "使用 Python 3.10 虚拟环境: venv310/"
    fi
elif [ ! -d "venv" ]; then
    log_warn "虚拟环境不存在，正在创建..."
    python3 -m venv venv
    log_info "虚拟环境创建成功: venv/"
else
    if [ "$VERBOSE" = "1" ]; then
        log_info "虚拟环境已存在: venv/"
    fi
fi

# 激活虚拟环境
source "$VENV_DIR/bin/activate"
if [ "$1" = "--verbose" ] || [ "$1" = "-v" ]; then
    log_step "激活虚拟环境"
    log_info "虚拟环境已激活"
fi

# 检查 pip（仅在需要时显示）
if [ "$1" = "--verbose" ] || [ "$1" = "-v" ]; then
    log_step "检查 pip"
    PIP_VERSION=$(pip --version 2>&1)
    log_info "pip 版本: $PIP_VERSION"
fi

# 检查依赖是否需要安装
if [ "$VERBOSE" = "1" ]; then
    log_step "检查依赖"
fi
if [ ! -f "requirements.txt" ]; then
    log_error "requirements.txt 文件不存在"
    exit 1
fi

# 检查依赖是否已安装（通过检查关键包是否存在）
INSTALLED_MARKER="$VENV_DIR/.installed"
NEED_INSTALL=0
if [ ! -f "$INSTALLED_MARKER" ]; then
    NEED_INSTALL=1
    log_info "首次运行，需要安装依赖"
else
    # 检查 requirements.txt 是否更新（通过比较文件修改时间）
    if [ "requirements.txt" -nt "$INSTALLED_MARKER" ]; then
        NEED_INSTALL=1
        log_info "requirements.txt 已更新，需要重新安装依赖"
    else
        # 快速检查关键包是否已安装
        if ! python -c "import rocketmq_client_python" 2>/dev/null; then
            NEED_INSTALL=1
            log_info "检测到依赖缺失，需要安装"
        fi
    fi
fi

if [ "$NEED_INSTALL" = "1" ]; then
    # 升级 pip（仅在需要安装依赖时）
    log_info "升级 pip..."
    pip install --upgrade pip > /dev/null 2>&1 || log_warn "pip 升级失败，继续使用当前版本"
    
    log_info "正在安装依赖包（这可能需要几分钟）..."
    
    # 尝试使用新的解析器安装
    if pip install -r requirements.txt > /dev/null 2>&1; then
        log_info "依赖安装成功"
        touch "$INSTALLED_MARKER"
    else
        log_warn "标准解析器安装失败，尝试使用 legacy 解析器..."
        # 如果标准解析器失败，使用 legacy 解析器
        if pip install --use-deprecated=legacy-resolver -r requirements.txt; then
            log_info "依赖安装成功（使用 legacy 解析器）"
            touch "$INSTALLED_MARKER"
        else
            log_error "依赖安装失败，请检查 requirements.txt 或 Python 版本兼容性"
            log_error "提示：如果使用 Python 3.14，某些包可能还不完全兼容"
            log_error "建议：使用 Python 3.10-3.12 版本（如 venv310）"
            exit 1
        fi
    fi
else
    log_info "依赖已安装，跳过安装步骤"
fi

# 检查.env文件（静默检查）
if [ ! -f ".env" ]; then
    log_warn ".env 文件不存在，正在创建默认配置..."
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
    log_info ".env 文件已创建"
fi

# 创建临时目录（静默创建）
TEMP_DIR="/tmp/question-hub-documents"
mkdir -p "$TEMP_DIR" 2>/dev/null || true

# 检查 RocketMQ 连接（仅在详细模式下显示）
if [ "$VERBOSE" = "1" ]; then
    log_step "检查 RocketMQ 连接"
    log_info "检查 NameServer 连接..."
    if command -v nc &> /dev/null; then
        if nc -z localhost 9876 2>/dev/null; then
            log_info "RocketMQ NameServer (localhost:9876) 连接正常"
        else
            log_warn "RocketMQ NameServer (localhost:9876) 无法连接，请确保 RocketMQ 已启动"
        fi
    else
        log_warn "nc 命令不可用，跳过连接检查"
    fi
    
    # 显示配置信息
    log_step "服务配置信息"
    log_info "Topic: question_hub"
    log_info "Consumer Group: question_hub_document_consumer"
    log_info "Consume Tag: document.convert"
    log_info "Publish Tag: document.convert.result"
fi

# 启动服务
if [ "$VERBOSE" = "1" ]; then
    log_step "启动 Document Service"
    log_info "正在启动服务..."
    log_info "按 Ctrl+C 停止服务"
    echo ""
else
    log_info "服务启动中... (按 Ctrl+C 停止)"
fi

# 运行服务
python -m app.main

