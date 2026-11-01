class ServiceError(Exception):
    """Lỗi nghiệp vụ có thông báo an toàn để hiển thị cho người dùng.

    retry_after (giây): nếu có, API trả thêm header Retry-After (dùng cho HTTP 429/503).
    """

    def __init__(self, message, status=400, retry_after=None):
        super().__init__(message)
        self.message = message
        self.status = status
        self.retry_after = retry_after
