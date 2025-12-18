# 微服务间文件访问最佳实践

## 问题分析

在微服务架构中，服务间文件共享是一个常见需求。当前系统面临以下问题：

1. **文件URL格式不统一**：可能是 HTTP URL、相对路径或本地文件路径
2. **协议支持不完整**：只支持 HTTP/HTTPS，不支持 `file://` 协议
3. **错误处理**：转换失败时需要通知调用方

## 行业最佳实践

### 1. 文件访问方式对比

| 方式 | 优点 | 缺点 | 适用场景 |
|------|------|------|----------|
| **HTTP/HTTPS** | ✅ 标准化<br>✅ 跨网络访问<br>✅ 支持认证授权<br>✅ 易于监控 | ⚠️ 需要网络连接<br>⚠️ 可能有延迟 | **推荐**：生产环境、分布式部署 |
| **file:// 协议** | ✅ 本地访问快<br>✅ 无需网络 | ❌ 要求服务在同一机器<br>❌ 路径依赖性强<br>❌ 难以扩展 | 仅适用于：开发环境、单机部署 |
| **共享存储（NFS）** | ✅ 多服务共享<br>✅ 统一管理 | ⚠️ 需要网络存储<br>⚠️ 性能可能受限 | 适用于：同机房部署 |
| **对象存储（OSS/S3）** | ✅ 高可用<br>✅ 可扩展<br>✅ CDN加速 | ⚠️ 需要云服务<br>⚠️ 可能有成本 | **推荐**：生产环境、大规模部署 |

### 2. 推荐方案

#### 方案一：统一通过 HTTP API 访问（推荐）

**架构**：
```
question-hub-service → asset-service (GetFileURL) → 返回 HTTP URL
document-service → HTTP URL → 下载文件
```

**优点**：
- ✅ 标准化，易于维护
- ✅ 支持多种存储后端（本地、OSS、S3）
- ✅ 支持访问控制和过期时间
- ✅ 易于监控和调试

**实现**：
- `asset-service` 的 `GetURL` 方法统一返回 HTTP URL
- 本地存储：`http://asset-service:8104/api/v1/files/{fileId}/content`
- 对象存储：返回预签名 URL

#### 方案二：支持多种协议（当前实现）

**架构**：
```
document-service 支持：
- http://, https:// → HTTP 下载
- file:// → 本地文件访问
- 相对路径 → 智能查找
```

**优点**：
- ✅ 灵活性高
- ✅ 支持本地开发环境

**缺点**：
- ⚠️ 代码复杂度增加
- ⚠️ 路径依赖性强

### 3. 错误处理最佳实践

#### 异步消息通知

**当前实现**：
```python
# document-service 处理失败时
result_message = DocumentConvertResultMessage(
    task_id=task_id,
    status="failed",
    error_msg=str(e)
)
self._send_result(result_message)  # 发送到 document.convert.result
```

**优点**：
- ✅ 解耦：调用方不需要等待
- ✅ 可靠性：消息队列保证送达
- ✅ 可重试：RocketMQ 支持消息重试

#### 错误信息设计

```json
{
  "task_id": "uuid",
  "status": "failed",
  "error_msg": "Request URL is missing an 'http://' or 'https://' protocol.",
  "error_code": "FILE_DOWNLOAD_FAILED",  // 可选：错误代码
  "retryable": true  // 可选：是否可重试
}
```

## 当前实现改进

### 1. 支持多种文件访问方式

已修改 `document_parser.py` 的 `download_file` 方法，支持：
- ✅ HTTP/HTTPS URL
- ✅ `file://` 协议
- ✅ 相对路径（智能查找）

### 2. 错误通知

已实现：转换失败时自动发送 `document.convert.result` 消息。

### 3. 建议的进一步优化

1. **统一使用 HTTP URL**：
   - 修改 `question-hub-service` 使用 `GetFileURL` 获取完整 URL（已完成）
   - 确保 `asset-service` 的 `GetURL` 始终返回可访问的 HTTP URL

2. **配置化文件访问方式**：
   ```python
   # config.py
   file_access_mode: str = "http"  # http, file, auto
   asset_service_base_url: str = "http://asset-service:8104"
   local_storage_base_path: str = "./uploads"
   ```

3. **错误分类和重试策略**：
   - 网络错误：可重试
   - 文件不存在：不可重试
   - 格式错误：不可重试

## 总结

**推荐做法**：
1. ✅ **生产环境**：统一使用 HTTP/HTTPS 通过 `asset-service` 访问文件
2. ✅ **开发环境**：可以支持 `file://` 协议，但应作为备选方案
3. ✅ **错误处理**：通过消息队列异步通知，保证可靠性
4. ✅ **监控**：记录文件访问日志，便于问题排查

**避免的做法**：
- ❌ 直接使用相对路径（路径依赖性强）
- ❌ 服务间直接共享文件系统（难以扩展）
- ❌ 同步等待文件处理完成（阻塞调用）

