"""
自定义异常类型
"""


class DocumentServiceException(Exception):
    """文档服务基础异常"""
    pass


class DownloadException(DocumentServiceException):
    """文件下载异常"""
    pass


class ParseException(DocumentServiceException):
    """文档解析异常"""
    pass


class ConversionException(DocumentServiceException):
    """格式转换异常"""
    pass


class ValidationException(DocumentServiceException):
    """数据验证异常"""
    pass


class ResourceException(DocumentServiceException):
    """资源管理异常（文件、内存等）"""
    pass


class RocketMQException(DocumentServiceException):
    """RocketMQ相关异常"""
    pass
