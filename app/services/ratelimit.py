"""Bộ giới hạn tần suất theo cửa sổ trượt, lưu trong bộ nhớ tiến trình (đủ cho 1 worker).

Có dọn dẹp khóa cũ để bộ nhớ không phình ra khi bị spam bằng nhiều khóa ngẫu nhiên.
Khi chạy nhiều worker/instance cần chuyển sang Redis (xem README).
"""
import math
import threading
import time
from collections import deque


class SlidingWindowLimiter:
    def __init__(self, max_keys=20000):
        self._lock = threading.Lock()
        self._data = {}  # key -> (deque[timestamps], window)
        self._max_keys = max_keys

    def _prune_locked(self, now):
        if len(self._data) <= self._max_keys:
            return
        for key in [k for k, (q, w) in self._data.items() if not q or now - q[-1] > w]:
            self._data.pop(key, None)
        if len(self._data) > self._max_keys:  # vẫn quá lớn: bỏ bớt các khóa cũ nhất
            for key in sorted(self._data, key=lambda k: self._data[k][0][-1] if self._data[k][0] else 0)[: len(self._data) // 2]:
                self._data.pop(key, None)

    def _queue_locked(self, key, window, now):
        q, _ = self._data.get(key, (deque(), window))
        while q and now - q[0] > window:
            q.popleft()
        self._data[key] = (q, window)
        return q

    def hit(self, key, limit, window):
        """Ghi nhận 1 lượt nếu chưa vượt giới hạn. Trả về (cho_phép, số_giây_phải_chờ)."""
        now = time.monotonic()
        with self._lock:
            self._prune_locked(now)
            q = self._queue_locked(key, window, now)
            if len(q) >= limit:
                return False, max(1, math.ceil(window - (now - q[0])))
            q.append(now)
            return True, 0

    def add(self, key, window):
        """Ghi nhận 1 lượt vô điều kiện (dùng cho đếm lần đăng nhập sai)."""
        now = time.monotonic()
        with self._lock:
            self._prune_locked(now)
            self._queue_locked(key, window, now).append(now)

    def blocked(self, key, limit, window):
        """Đã đạt giới hạn chưa? Trả về (bị_chặn, số_giây_phải_chờ). Không ghi nhận lượt mới."""
        now = time.monotonic()
        with self._lock:
            if key not in self._data:
                return False, 0
            q = self._queue_locked(key, window, now)
            if len(q) >= limit:
                return True, max(1, math.ceil(window - (now - q[0])))
            return False, 0

    def clear(self, key):
        with self._lock:
            self._data.pop(key, None)

    def reset(self):
        with self._lock:
            self._data.clear()
