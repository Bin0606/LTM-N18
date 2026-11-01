"""Kiểm tra, lưu và truy xuất file chia sẻ."""
import codecs
import os
import uuid
import zipfile

from flask import current_app

from ..extensions import db
from ..models import Message, SharedFile
from .chat_service import is_member
from .errors import ServiceError
from .ratelimit import SlidingWindowLimiter
from .storage import StorageUnavailable

# phần mở rộng -> (MIME chuẩn do server gán, các MIME mà trình duyệt có thể khai báo)
ALLOWED = {
    "jpg": ("image/jpeg", {"image/jpeg", "image/pjpeg"}),
    "jpeg": ("image/jpeg", {"image/jpeg", "image/pjpeg"}),
    "png": ("image/png", {"image/png"}),
    "webp": ("image/webp", {"image/webp"}),
    "pdf": ("application/pdf", {"application/pdf"}),
    "docx": ("application/vnd.openxmlformats-officedocument.wordprocessingml.document",
             {"application/vnd.openxmlformats-officedocument.wordprocessingml.document", "application/zip"}),
    "xlsx": ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
             {"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "application/zip"}),
    "txt": ("text/plain", {"text/plain"}),
}
GENERIC_MIMES = {"", "application/octet-stream"}  # một số trình duyệt di động khai báo chung chung
MAX_UNZIPPED = 200 * 1024 * 1024  # chặn zip bomb

_upload_limiter = SlidingWindowLimiter()


def check_upload_rate_limit(user_id):
    """Giới hạn upload theo TÀI KHOẢN (mặc định 5 file / 60 giây). Vượt → HTTP 429 + Retry-After.

    Tính cả lần upload bị từ chối vì sai định dạng/quá dung lượng để chặn spam file rác. Mỗi user có bộ đếm riêng
    nên không ảnh hưởng người khác. Phải gọi TRƯỚC khi đọc request.files để không tốn công phân tích thân yêu cầu.
    """
    limit, window = current_app.config["UPLOAD_RATE_LIMIT"]
    allowed, wait = _upload_limiter.hit(f"upload:{user_id}", limit, window)
    if not allowed:
        raise ServiceError(f"Bạn upload quá nhanh (tối đa {limit} file trong {int(window)} giây). "
                           f"Vui lòng thử lại sau {wait} giây.", 429, retry_after=wait)


def reset_upload_limits():
    _upload_limiter.reset()


def _ext(filename):
    return os.path.splitext(filename or "")[1].lower().lstrip(".")


def _sniff_ok(ext, stream):
    """Kiểm tra NỘI DUNG thật của file khớp với phần mở rộng (không tin tên file/MIME từ client)."""
    stream.seek(0)
    head = stream.read(16)
    stream.seek(0)
    if ext in ("jpg", "jpeg"):
        return head.startswith(b"\xff\xd8\xff")
    if ext == "png":
        return head.startswith(b"\x89PNG\r\n\x1a\n")
    if ext == "webp":
        return head[:4] == b"RIFF" and head[8:12] == b"WEBP"
    if ext == "pdf":
        return head.startswith(b"%PDF-")
    if ext in ("docx", "xlsx"):
        if not zipfile.is_zipfile(stream):
            return False
        stream.seek(0)
        try:
            with zipfile.ZipFile(stream) as zf:
                names = zf.namelist()
                if sum(i.file_size for i in zf.infolist()) > MAX_UNZIPPED:
                    return False
        except zipfile.BadZipFile:
            return False
        finally:
            stream.seek(0)
        prefix = "word/" if ext == "docx" else "xl/"
        return "[Content_Types].xml" in names and any(n.startswith(prefix) for n in names)
    if ext == "txt":
        decoder = codecs.getincrementaldecoder("utf-8")()
        try:
            while True:
                chunk = stream.read(65536)
                if not chunk:
                    decoder.decode(b"", final=True)
                    break
                if b"\x00" in chunk:
                    return False
                decoder.decode(chunk)
        except UnicodeDecodeError:
            return False
        finally:
            stream.seek(0)
        return True
    return False


def _size(stream):
    stream.seek(0, os.SEEK_END)
    size = stream.tell()
    stream.seek(0)
    return size


def validate_upload(file_storage):
    if file_storage is None or not file_storage.filename:
        raise ServiceError("Chưa chọn file.")
    ext = _ext(file_storage.filename)
    if ext not in ALLOWED:
        raise ServiceError("Định dạng không được hỗ trợ. Cho phép: JPG, JPEG, PNG, WEBP, PDF, DOCX, XLSX, TXT.")
    canonical, accepted = ALLOWED[ext]
    declared = (file_storage.mimetype or "").lower()
    if declared not in GENERIC_MIMES and declared not in accepted:
        raise ServiceError("Loại file (MIME) không khớp với phần mở rộng.")
    stream = file_storage.stream
    size = _size(stream)
    if size == 0:
        raise ServiceError("File rỗng.")
    max_bytes = current_app.config["MAX_UPLOAD_MB"] * 1024 * 1024
    if size > max_bytes:
        raise ServiceError(f"File vượt quá giới hạn {current_app.config['MAX_UPLOAD_MB']} MB.", 413)
    if not _sniff_ok(ext, stream):
        msg = "File TXT phải mã hóa UTF-8." if ext == "txt" else "Nội dung file không khớp với định dạng khai báo."
        raise ServiceError(msg)
    return ext, canonical, size


def display_name(filename):
    """Tên chỉ dùng để HIỂN THỊ: bỏ đường dẫn và ký tự điều khiển, giới hạn độ dài."""
    name = os.path.basename((filename or "").replace("\\", "/"))
    name = "".join(ch for ch in name if ch.isprintable()).strip() or "file"
    return name[-200:]


def save_shared_file(user, room_id, file_storage):
    """Luồng upload: kiểm quyền → kiểm file → ghi storage → ghi DB (tin nhắn + shared_files)."""
    if not is_member(user.id, room_id):
        raise ServiceError("Bạn không thuộc phòng này.", 403)
    ext, mime, size = validate_upload(file_storage)
    stored_name = f"{uuid.uuid4().hex}.{ext}"  # tên ngẫu nhiên, không dùng tên người dùng
    key = f"files/{room_id}/{stored_name}"
    storage = current_app.extensions["storage"]
    try:
        storage.save(key, file_storage.stream, mime)
    except StorageUnavailable:
        current_app.logger.exception("Kho lưu trữ không khả dụng khi ghi file")
        raise ServiceError("Kho lưu trữ tạm thời không khả dụng. Vui lòng thử lại sau.", 503, retry_after=30)
    except Exception:
        current_app.logger.exception("Lỗi ghi file lên storage")
        raise ServiceError("Không lưu được file. Vui lòng thử lại.", 500)
    name = display_name(file_storage.filename)
    try:
        msg = Message(room_id=room_id, sender_id=user.id, content=name, message_type="file")
        db.session.add(msg)
        db.session.flush()
        db.session.add(SharedFile(room_id=room_id, message_id=msg.id, uploader_id=user.id,
                                  original_filename=name, stored_filename=stored_name, storage_key=key,
                                  file_size=size, mime_type=mime))
        db.session.commit()
    except Exception:
        db.session.rollback()
        try:
            storage.delete(key)  # dọn file mồ côi nếu DB thất bại
        except Exception:
            current_app.logger.exception("Không dọn được file mồ côi")
        current_app.logger.exception("Lỗi lưu thông tin file vào DB")
        raise ServiceError("Không lưu được thông tin file. Vui lòng thử lại.", 500)
    return msg


def validate_avatar(file_storage):
    ext, mime, size = validate_upload(file_storage)
    if ext not in ("jpg", "jpeg", "png", "webp"):
        raise ServiceError("Ảnh đại diện phải là JPG, PNG hoặc WEBP.")
    if size > 2 * 1024 * 1024:
        raise ServiceError("Ảnh đại diện tối đa 2 MB.", 413)
    return ext, mime


def delete_keys(keys):
    storage = current_app.extensions["storage"]
    for key in keys:
        try:
            storage.delete(key)
        except Exception:
            current_app.logger.exception("Không xóa được file trên storage")
