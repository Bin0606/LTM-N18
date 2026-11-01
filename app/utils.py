"""Hàm tiện ích dùng chung."""
from datetime import datetime, timezone


def utcnow():
    """Thời gian UTC dạng naive (không tzinfo) để lưu thống nhất ở SQLite và PostgreSQL."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def iso(dt):
    """Chuyển datetime UTC naive thành chuỗi ISO 8601 có hậu tố Z để JavaScript hiểu đúng múi giờ."""
    return dt.isoformat(timespec="seconds") + "Z" if dt else None
