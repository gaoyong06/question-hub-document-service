# Question Hub Document Service

文档识别服务 - 从多种文档格式中提取题目信息

## 功能特性

- ✅ 监听RocketMQ消息队列，接收文档转换任务
- ✅ 支持多种文档格式：Word (.doc, .docx)、PDF (.pdf)、PowerPoint (.ppt, .pptx)、Excel (.xls, .xlsx)、图片 (.jpg, .jpeg, .png, .gif, .bmp)、文本 (.txt, .html, .csv, .json, .xml)、EPUB (.epub)
- ✅ 自动转换文档为Markdown格式（非Word格式）
- ✅ 解析文档内容，提取文本和图片
- ✅ 识别题目类型（单选题、多选题、填空题、判断题、解答题）
- ✅ 提取题目内容、选项、答案、解析
- ✅ 发送转换结果到RocketMQ队列

## 技术栈

- **Python 3.9+**
- **FastAPI** - Web框架（可选，用于健康检查）
- **rocketmq-client-python** - RocketMQ Python客户端（需要先安装 librocketmq C++ 库）
- **python-docx** - Word文档解析（.doc, .docx）
- **markitdown** - 多格式文档转换（PDF、PowerPoint、Excel、图片、文本等）
- **loguru** - 日志管理
- **pydantic** - 数据验证

## 项目结构

```
question-hub-document-service/
├── app/
│   ├── __init__.py
│   ├── main.py              # 主程序入口
│   ├── config.py            # 配置管理
│   ├── models.py            # 数据模型
│   ├── consumers/           # 消息消费者
│   │   └── document_consumer.py
│   └── services/            # 业务逻辑
│       └── document_parser.py
├── requirements.txt         # 依赖列表
├── .env.example            # 环境变量示例
├── Dockerfile              # Docker配置
└── README.md
```

## 安装和运行

### 1. 安装 librocketmq C++ 库（必需）

RocketMQ Python 客户端依赖于 C++ 客户端库。在 macOS M1/M2 上安装：

参考下面的安装方法
https://github.com/apache/rocketmq-client-python/issues/156


### 2. 安装 Python 依赖

```bash
# 创建虚拟环境
# 激活Python 3.10虚拟环境
source venv310/bin/activate # Windows: venv310\Scripts\activate

# 安装依赖
pip install -r requirements.txt
```

### 2. 配置环境变量

```bash
# 复制环境变量示例文件
cp .env.example .env

# 编辑.env文件，配置RabbitMQ连接信息
```

### 3. 运行服务

```bash
# 直接运行
python -m app.main

# 或使用uvicorn（如果添加了HTTP接口）
uvicorn app.main:app --host 0.0.0.0 --port 8122
```

## 配置说明

### 环境变量

| 变量名 | 说明 | 默认值 |
|--------|------|--------|
| `ROCKETMQ_NAME_SERVER` | RocketMQ NameServer 地址 | `localhost:9876` |
| `ROCKETMQ_TOPIC` | Topic名称 | `question_hub` |
| `ROCKETMQ_CONSUMER_GROUP` | 消费者组名 | `question_hub_document_consumer` |
| `ROCKETMQ_PRODUCER_GROUP` | 生产者组名 | `question_hub_document_producer` |
| `ROCKETMQ_CONSUME_TAG` | 消费Tag（接收） | `document.convert` |
| `ROCKETMQ_PUBLISH_TAG` | 发布Tag（发送） | `document.convert.result` |
| `DOWNLOAD_TIMEOUT` | 下载超时时间（秒） | `300` |
| `TEMP_FILE_DIR` | 临时文件目录 | `/tmp/question-hub-documents` |
| `LOG_LEVEL` | 日志级别 | `INFO` |
| `LOG_FORMAT` | 日志格式（json/text） | `json` |

## 消息格式

### 接收消息（DocumentConvertMessage）

```json
{
  "task_id": "uuid-string",
  "merchant_id": "uuid-string",
  "file_id": "uuid-string",
  "file_url": "https://example.com/file.docx"
}
```

### 发送消息（DocumentConvertResultMessage）

成功：
```json
{
  "task_id": "uuid-string",
  "status": "completed",
  "result": [
    {
      "type": "single-choice",
      "content": "题目内容",
      "options": ["选项A", "选项B", "选项C", "选项D"],
      "answer": "A",
      "explanation": "解析内容",
      "difficulty": "medium",
      "grade": 1,
      "subject": "数学"
    }
  ]
}
```

失败：
```json
{
  "task_id": "uuid-string",
  "status": "failed",
  "error_msg": "错误信息"
}
```

## 题目识别规则

### 单选题
- 匹配模式：`题目 + A/B/C/D选项 + 答案：X`
- 示例：`1. 题目内容 A. 选项1 B. 选项2 C. 选项3 D. 选项4 答案：A`

### 多选题
- 匹配模式：`题目 + A/B/C/D选项 + 答案：多个选项（如AB、ABC）`
- 示例：`1. 题目内容 A. 选项1 B. 选项2 C. 选项3 D. 选项4 答案：AB`

### 填空题
- 匹配模式：`题目（包含下划线或括号）+ 答案：...`
- 示例：`1. 题目内容（    ）答案：答案内容`

### 判断题
- 匹配模式：`题目 + 答案：对/错 或 正确/错误`
- 示例：`1. 题目内容 答案：对`

### 解答题
- 匹配模式：`题目 + 解析：...`
- 示例：`1. 题目内容 解析：解析内容`

## Docker部署

```bash
# 构建镜像
docker build -t question-hub-document-service .

# 运行容器
docker run -d \
  --name document-service \
  -e RABBITMQ_URL=amqp://guest:guest@rabbitmq:5672/ \
  question-hub-document-service
```

## 开发

### 添加新的题目类型识别

1. 在 `app/services/document_parser.py` 中添加新的提取方法
2. 在 `_extract_questions` 方法中调用新方法
3. 更新正则表达式匹配模式

### 测试

```bash
# 运行测试（需要先编写测试用例）
pytest tests/
```

## 日志

日志输出到标准输出，支持JSON和文本两种格式。

JSON格式示例：
```json
{"time": "2024-12-14T10:00:00", "level": "INFO", "message": "Received conversion task"}
```

文本格式示例：
```
2024-12-14 10:00:00 | INFO     | app.consumers.document_consumer - Received conversion task
```

## 故障排查

### 无法连接到RocketMQ
- 检查RocketMQ NameServer是否运行（默认端口 9876）
- 检查 NameServer 地址是否正确
- 检查网络连接
- 检查是否已安装 librocketmq C++ 库

### rocketmq dynamic library not found
- **这是 macOS M1/M2 的常见问题**，详见 [RocketMQ 安装指南](./README_ROCKETMQ_INSTALL.md)
- 需要先安装 librocketmq C++ 库（见安装步骤1）
- 检查库文件是否在 `/usr/local/lib` 目录：`ls -la /usr/local/lib/librocketmq*`
- 检查环境变量 `DYLD_LIBRARY_PATH` 是否包含库路径：`echo $DYLD_LIBRARY_PATH`
- **如果使用 M1/M2 芯片**，建议使用 Rosetta 2 运行 x86_64 版本的 Python 和库

### 无法下载文件
- 检查文件URL是否可访问
- 检查网络连接
- 检查文件大小是否超过限制

### 无法识别题目
- 检查文档格式是否支持（支持 Word、PDF、PowerPoint、Excel、图片、文本、EPUB 等）
- 检查题目格式是否符合识别规则
- 查看日志了解详细错误信息

## 许可证

MIT License

