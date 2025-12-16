# RocketMQ Client 安装指南（macOS M1/M2）

## 问题描述

在 macOS M1/M2 芯片上运行 `rocketmq-client-python` 时，可能会遇到以下错误：

```
ImportError: rocketmq dynamic library not found
```

## 原因分析

`rocketmq-client-python` 依赖于 `rocketmq-client-cpp` 的 C++ 动态库。官方只提供了 **x86_64 架构**的 Darwin 版本，没有提供 **ARM64 架构**的预编译版本。

## 解决方案

### 方案一：使用 Rosetta 2 运行 x86_64 版本（推荐）

这是最简单的方法，适合快速开发和测试。

#### 1. 创建 x86_64 的 Python 环境（使用 Conda）

```bash
# 创建 x86_64 的 conda 环境
CONDA_SUBDIR=osx-64 conda create -n rocketmq-x86 python=3.10
conda activate rocketmq-x86

# 验证架构
python -c "import platform; print(platform.machine())"
# 应该输出: x86_64
```

#### 2. 安装依赖

```bash
# 安装 rocketmq-client-python
pip install rocketmq-client-python

# 安装其他依赖
pip install -r requirements.txt
```

#### 3. 下载并安装 x86_64 版本的 librocketmq

```bash
# 下载预编译版本
wget https://github.com/apache/rocketmq-client-cpp/releases/download/2.0.0/rocketmq-client-cpp-2.0.0-bin-release.darwin.tar.gz
tar -xzf rocketmq-client-cpp-2.0.0-bin-release.darwin.tar.gz
cd rocketmq-client-cpp

# 安装到系统目录
sudo mkdir -p /usr/local/include/rocketmq
sudo mkdir -p /usr/local/lib
sudo cp include/* /usr/local/include/rocketmq
sudo cp lib/* /usr/local/lib

# 修复动态库 ID（macOS 必需）
sudo install_name_tool -id "@rpath/librocketmq.dylib" /usr/local/lib/librocketmq.dylib

# 设置环境变量
echo 'export DYLD_LIBRARY_PATH="/usr/local/lib:$DYLD_LIBRARY_PATH"' >> ~/.zshrc
source ~/.zshrc
```

#### 4. 验证安装

```bash
python -c "from rocketmq.client import Producer; print('RocketMQ 启动成功 ✅')"
```

### 方案二：使用安装脚本（ARM64 编译）

如果需要在原生 ARM64 Python 环境中运行，需要自行编译 `rocketmq-client-cpp`。

#### 1. 运行安装脚本

```bash
cd question-hub-document-service
./scripts/install_rocketmq_client.sh
```

脚本会自动：
- 检查系统架构
- 下载源码和依赖
- 编译 rocketmq-client-cpp
- 安装到系统目录
- 配置环境变量

**注意**：编译过程可能需要较长时间（10-30 分钟），并且可能会遇到编译错误需要手动修复。

#### 2. 安装 Python 客户端

```bash
pip install rocketmq-client-python
```

#### 3. 验证安装

```bash
python3 -c "from rocketmq.client import Producer; print('RocketMQ 启动成功 ✅')"
```

### 方案三：使用 Docker（推荐用于生产环境）

如果本地环境配置困难，可以使用 Docker 运行服务：

```bash
# 构建 Docker 镜像
docker build -t question-hub-document-service .

# 运行容器
docker run -d \
  --name document-service \
  -e ROCKETMQ_NAME_SERVER=your-nameserver:9876 \
  question-hub-document-service
```

## 常见问题

### 1. 架构不匹配错误

```
OSError: ... is an incompatible architecture (have 'x86_64', need 'arm64e' or 'arm64')
```

**解决方法**：确保 Python 环境和库文件的架构一致。如果使用 x86_64 库，Python 环境也必须是 x86_64。

### 2. 动态库未找到

```
ImportError: rocketmq dynamic library not found
```

**解决方法**：
1. 检查库文件是否存在：`ls -la /usr/local/lib/librocketmq*`
2. 检查环境变量：`echo $DYLD_LIBRARY_PATH`
3. 确保已设置：`export DYLD_LIBRARY_PATH="/usr/local/lib:$DYLD_LIBRARY_PATH"`

### 3. 编译错误

如果使用方案二编译时遇到错误，可能需要：
- 更新 CMake 版本（需要 >= 3.5）
- 修复 boost endian 兼容性问题
- 检查其他依赖是否完整

## 参考链接

- [MacOS M1/M2 安装使用rocketmq-client-python](https://blog.csdn.net/u012655332/article/details/131440219)
- [rocketmq 环境配置[python]](https://jacinli.github.io/2025/05/rocketmq-%E9%85%8D%E7%BD%AE%E7%8E%AF%E5%A2%83-python/)
- [rocketmq-client-cpp GitHub](https://github.com/apache/rocketmq-client-cpp)
- [rocketmq-client-python GitHub](https://github.com/apache/rocketmq-client-python)
