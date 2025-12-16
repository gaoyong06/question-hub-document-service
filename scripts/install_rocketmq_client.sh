#!/bin/bash

# RocketMQ Client C++ 安装脚本（适用于 macOS M1/M2）
# 参考：https://blog.csdn.net/u012655332/article/details/131440219

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

log_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# 检查架构
ARCH=$(uname -m)
log_info "检测到系统架构: $ARCH"

if [ "$ARCH" = "arm64" ]; then
    log_warn "检测到 ARM64 架构（M1/M2 芯片）"
    log_warn "官方只提供 x86_64 版本的 librocketmq.dylib"
    log_warn "建议使用 Rosetta 2 运行 x86_64 版本的 Python"
    log_warn ""
    log_warn "或者，我们可以尝试下载并安装 x86_64 版本（通过 Rosetta 2 运行）"
    read -p "是否继续安装 x86_64 版本？(y/n) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        log_info "已取消安装"
        exit 0
    fi
fi

# 检查依赖工具
log_info "检查依赖工具..."
if ! command -v wget &> /dev/null; then
    log_info "安装 wget..."
    brew install wget
fi

if ! command -v automake &> /dev/null; then
    log_info "安装 automake..."
    brew install automake
fi

if ! command -v cmake &> /dev/null; then
    log_info "安装 cmake..."
    brew install cmake
fi

# 创建临时目录
TMP_DIR=$(mktemp -d)
log_info "临时目录: $TMP_DIR"
cd "$TMP_DIR"

# 下载 rocketmq-client-cpp
VERSION="2.0.0"
log_info "下载 rocketmq-client-cpp $VERSION..."

if [ "$ARCH" = "arm64" ]; then
    # ARM64 架构：下载源码自行编译
    log_info "下载源码进行编译..."
    wget -q "https://github.com/apache/rocketmq-client-cpp/archive/refs/tags/${VERSION}.tar.gz" -O rocketmq-client-cpp-${VERSION}.tar.gz
    tar -xzf rocketmq-client-cpp-${VERSION}.tar.gz
    cd rocketmq-client-cpp-${VERSION}
    
    # 执行 build.sh（第一次会失败，但会生成 tmp_down_dir）
    log_info "执行 build.sh（第一次会失败，但会生成依赖目录）..."
    ./build.sh || true
    
    if [ ! -d "tmp_down_dir" ]; then
        log_error "tmp_down_dir 目录未生成，请检查 build.sh 执行情况"
        exit 1
    fi
    
    # 下载 boost 1.77.0（替换官方不兼容的 1.58）
    log_info "下载 boost 1.77.0..."
    cd tmp_down_dir
    if [ ! -f "boost_1_77_0.tar.gz" ]; then
        wget -q "https://boostorg.jfrog.io/artifactory/main/release/1.77.0/source/boost_1_77_0.tar.gz" || {
            log_error "下载 boost 失败，请检查网络连接"
            exit 1
        }
    fi
    
    # 下载 jsoncpp（如果需要）
    if [ ! -f "jsoncpp-0.10.6.zip" ]; then
        log_info "下载 jsoncpp-0.10.6..."
        wget -q "https://github.com/open-source-parsers/jsoncpp/archive/0.10.6.zip" -O jsoncpp-0.10.6.zip || {
            log_warn "jsoncpp 下载失败，build.sh 可能会自动下载"
        }
    fi
    
    cd ..
    
    # 修复 boost endian 问题（如果存在）
    if [ -f "tmp_down_dir/boost_1_77_0/boost/endian/endian.hpp" ]; then
        log_info "修复 boost endian 兼容性问题..."
        mkdir -p tmp_down_dir/boost_1_77_0/boost/detail
        cp tmp_down_dir/boost_1_77_0/boost/endian/endian.hpp tmp_down_dir/boost_1_77_0/boost/detail/ 2>/dev/null || true
    fi
    
    # 重新执行 build.sh
    log_info "重新执行 build.sh 进行编译（这可能需要较长时间）..."
    ./build.sh || {
        log_error "编译失败，请检查错误信息"
        log_error "可能需要手动修复编译问题"
        exit 1
    }
    
    BUILD_DIR="rocketmq-client-cpp-${VERSION}"
else
    # x86_64 架构：直接下载预编译版本
    log_info "下载预编译版本..."
    wget -q "https://github.com/apache/rocketmq-client-cpp/releases/download/${VERSION}/rocketmq-client-cpp-${VERSION}-bin-release.darwin.tar.gz" -O rocketmq-client-cpp-${VERSION}-bin-release.darwin.tar.gz
    tar -xzf rocketmq-client-cpp-${VERSION}-bin-release.darwin.tar.gz
    BUILD_DIR="rocketmq-client-cpp"
fi

# 安装库文件
log_info "安装库文件到系统目录..."

if [ "$ARCH" = "arm64" ]; then
    # ARM64 编译后的文件位置
    LIB_DIR="${BUILD_DIR}/bin"
    INCLUDE_DIR="${BUILD_DIR}/include"
else
    # x86_64 预编译版本的文件位置
    LIB_DIR="${BUILD_DIR}/lib"
    INCLUDE_DIR="${BUILD_DIR}/include"
fi

# 创建目录
sudo mkdir -p /usr/local/include/rocketmq
sudo mkdir -p /usr/local/lib

# 复制头文件
log_info "复制头文件..."
sudo cp -r "${INCLUDE_DIR}"/* /usr/local/include/rocketmq/ 2>/dev/null || sudo cp "${INCLUDE_DIR}"/* /usr/local/include/rocketmq/

# 复制库文件
log_info "复制库文件..."
sudo cp "${LIB_DIR}"/librocketmq* /usr/local/lib/ 2>/dev/null || {
    log_error "未找到 librocketmq 库文件，请检查编译是否成功"
    exit 1
}

# 修复动态库 ID（macOS 必需）
log_info "修复动态库 ID..."
if [ -f "/usr/local/lib/librocketmq.dylib" ]; then
    sudo install_name_tool -id "@rpath/librocketmq.dylib" /usr/local/lib/librocketmq.dylib
    log_info "动态库 ID 修复完成"
else
    log_warn "未找到 librocketmq.dylib，跳过 ID 修复"
fi

# 设置环境变量
log_info "配置环境变量..."
SHELL_CONFIG=""
if [ -f "$HOME/.zshrc" ]; then
    SHELL_CONFIG="$HOME/.zshrc"
elif [ -f "$HOME/.bash_profile" ]; then
    SHELL_CONFIG="$HOME/.bash_profile"
elif [ -f "$HOME/.bashrc" ]; then
    SHELL_CONFIG="$HOME/.bashrc"
fi

if [ -n "$SHELL_CONFIG" ]; then
    if ! grep -q "DYLD_LIBRARY_PATH.*/usr/local/lib" "$SHELL_CONFIG"; then
        echo 'export DYLD_LIBRARY_PATH="/usr/local/lib:$DYLD_LIBRARY_PATH"' >> "$SHELL_CONFIG"
        log_info "已添加 DYLD_LIBRARY_PATH 到 $SHELL_CONFIG"
        log_info "请运行: source $SHELL_CONFIG"
    else
        log_info "DYLD_LIBRARY_PATH 已存在于 $SHELL_CONFIG"
    fi
else
    log_warn "未找到 shell 配置文件，请手动添加："
    log_warn 'export DYLD_LIBRARY_PATH="/usr/local/lib:$DYLD_LIBRARY_PATH"'
fi

# 验证安装
log_info "验证安装..."
if [ -f "/usr/local/lib/librocketmq.dylib" ]; then
    log_info "✓ librocketmq.dylib 已安装"
    ls -lh /usr/local/lib/librocketmq*
else
    log_error "✗ librocketmq.dylib 未找到"
    exit 1
fi

# 清理临时目录
log_info "清理临时目录..."
rm -rf "$TMP_DIR"

log_info ""
log_info "安装完成！"
log_info ""
log_info "下一步："
log_info "1. 如果修改了 shell 配置，请运行: source $SHELL_CONFIG"
log_info "2. 安装 Python 客户端: pip install rocketmq-client-python"
log_info "3. 验证安装: python3 -c 'from rocketmq.client import Producer; print(\"OK\")'"
log_info ""
