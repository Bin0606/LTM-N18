from urllib.parse import quote

from flask import Blueprint, Response, abort, current_app, stream_with_context
from flask_login import current_user, login_required

from ..extensions import db
from ..models import SharedFile
from ..services.chat_service import is_member
from ..services.errors import ServiceError
from ..services.storage import StorageNotFound, StorageUnavailable

bp = Blueprint("files", __name__)


def open_storage(key):
    """Mở file trên storage và phân biệt đúng 2 loại lỗi: không tồn tại (404) và kho lưu trữ lỗi (503)."""
    try:
        return current_app.extensions["storage"].open(key)
    except StorageNotFound:
        raise ServiceError("File không còn tồn tại trên kho lưu trữ.", 404)
    except StorageUnavailable:
        current_app.logger.exception("Kho lưu trữ không khả dụng khi đọc file")
        raise ServiceError("Kho lưu trữ tạm thời không khả dụng. Vui lòng thử lại sau.", 503, retry_after=30)


def stream_storage(key, size, mimetype, disposition, filename=None):
    handle = open_storage(key)

    def generate():
        try:
            while True:
                chunk = handle.read(64 * 1024)
                if not chunk:
                    break
                yield chunk
        except Exception:  # header đã gửi nên không đổi được mã lỗi; ghi log để điều tra
            current_app.logger.exception("Lỗi giữa chừng khi stream file")
        finally:
            handle.close()

    headers = {
        "Content-Length": str(size),
        "X-Content-Type-Options": "nosniff",
        "Cache-Control": "private, max-age=60",
        # file người dùng không bao giờ được chạy script trong trang của ConnectHub
        "Content-Security-Policy": "default-src 'none'; sandbox",
    }
    if filename:
        headers["Content-Disposition"] = f"{disposition}; filename*=UTF-8''{quote(filename)}"
    else:
        headers["Content-Disposition"] = disposition
    return Response(stream_with_context(generate()), mimetype=mimetype, headers=headers)


@bp.get("/files/<int:file_id>")
@login_required
def download(file_id):
    record = db.session.get(SharedFile, file_id)
    # 404 cho cả "không tồn tại" lẫn "không có quyền" để không lộ sự tồn tại của file
    if not record or not is_member(current_user.id, record.room_id):
        abort(404)
    # Ảnh hiển thị inline (xem trước); mọi loại khác luôn tải xuống để trình duyệt không thực thi nội dung
    disposition = "inline" if record.is_image else "attachment"
    return stream_storage(record.storage_key, record.file_size, record.mime_type, disposition, record.original_filename)
