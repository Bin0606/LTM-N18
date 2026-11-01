"""Theo dõi trạng thái Online/Offline theo SỐ KẾT NỐI của mỗi tài khoản.

Một tài khoản có thể mở nhiều kết nối (máy tính + điện thoại + nhiều tab). Chỉ khi kết nối cuối cùng
đóng thì mới chuyển Offline. Dữ liệu lưu trong bộ nhớ tiến trình nên chỉ đúng khi chạy 1 worker
(xem README: nhiều worker cần Redis message queue + lưu presence ngoài tiến trình).
"""
import threading

_lock = threading.Lock()
_user_sids = {}   # user_id -> {sid: session_key}
_sid_user = {}    # sid -> user_id


def add(user_id, sid, session_key):
    """Thêm kết nối. Trả về True nếu đây là kết nối ĐẦU TIÊN của user (vừa chuyển sang Online)."""
    with _lock:
        sids = _user_sids.setdefault(user_id, {})
        first = len(sids) == 0
        sids[sid] = session_key
        _sid_user[sid] = user_id
        return first


def remove(sid):
    """Bỏ kết nối. Trả về (user_id, is_last) hoặc None nếu sid không biết."""
    with _lock:
        user_id = _sid_user.pop(sid, None)
        if user_id is None:
            return None
        sids = _user_sids.get(user_id, {})
        sids.pop(sid, None)
        if not sids:
            _user_sids.pop(user_id, None)
            return user_id, True
        return user_id, False


def is_online(user_id):
    with _lock:
        return bool(_user_sids.get(user_id))


def online_ids(user_ids):
    with _lock:
        return {uid for uid in user_ids if _user_sids.get(uid)}


def sids_for_user(user_id):
    with _lock:
        return list(_user_sids.get(user_id, {}).keys())


def sids_for_session(session_key):
    with _lock:
        return [sid for sids in _user_sids.values() for sid, key in sids.items() if key == session_key]


def reset():
    with _lock:
        _user_sids.clear()
        _sid_user.clear()
