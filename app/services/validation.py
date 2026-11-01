"""Kiểm tra kiểu dữ liệu đầu vào từ client (JSON/form) để không bao giờ gây AttributeError/HTTP 500."""
import re

from flask import request

from .errors import ServiceError

_INT_RE = re.compile(r"^\d{1,18}$")


def json_dict():
    """Trả về body JSON dạng dict. JSON hỏng, null, list, chuỗi, số... đều bị từ chối bằng HTTP 400."""
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise ServiceError("Dữ liệu gửi lên phải là một đối tượng JSON hợp lệ.", 400)
    return data


def as_int(value, field="Giá trị"):
    """Chấp nhận số nguyên dương (hoặc chuỗi toàn chữ số). Từ chối bool, float, None, list, dict, số âm."""
    if isinstance(value, bool):
        raise ServiceError(f"{field} không hợp lệ.")
    if isinstance(value, int):
        number = value
    elif isinstance(value, str) and _INT_RE.match(value.strip()):
        number = int(value.strip())
    else:
        raise ServiceError(f"{field} không hợp lệ.")
    if number <= 0 or number > 2**63 - 1:
        raise ServiceError(f"{field} không hợp lệ.")
    return number


def as_text(value, field, *, min_len=0, max_len=255, required=True, multiline=False):
    """Chuỗi đã strip. None → '' nếu không bắt buộc. Từ chối kiểu không phải str và ký tự điều khiển."""
    if value is None:
        if required:
            raise ServiceError(f"{field} không được để trống.")
        return ""
    if not isinstance(value, str):
        raise ServiceError(f"{field} phải là chuỗi ký tự.")
    text = value.replace("\r\n", "\n").strip()
    for ch in text:
        if ch == "\n" and multiline:
            continue
        if ord(ch) < 32 or ord(ch) == 127:
            raise ServiceError(f"{field} chứa ký tự không hợp lệ.")
    if required and not text:
        raise ServiceError(f"{field} không được để trống.")
    if len(text) < min_len:
        raise ServiceError(f"{field} phải có ít nhất {min_len} ký tự.")
    if len(text) > max_len:
        raise ServiceError(f"{field} tối đa {max_len} ký tự.")
    return text
