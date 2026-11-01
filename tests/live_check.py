"""Kiểm thử đầu-cuối trên server ĐANG CHẠY thật (WebSocket thật, không dùng test client giả lập).

Cách dùng:
    python tests/live_check.py http://127.0.0.1:5000
    python tests/live_check.py https://<ten-dich-vu>.onrender.com     # kiểm tra bản đã deploy
Script tạo 2 tài khoản ngẫu nhiên (tiền tố 'livechk') và một phòng thử nghiệm.
"""
import io
import re
import sys
import threading
import time
import uuid

import requests
import socketio

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:5000").rstrip("/")
results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond)))
    print(("PASS " if cond else "FAIL ") + name + (f"  [{detail}]" if detail and not cond else ""))


class User:
    def __init__(self, tag):
        self.s = requests.Session()
        self.name = f"livechk{tag}{uuid.uuid4().hex[:6]}"
        self.pw = "Passw0rd123"
        self.csrf = None

    def token(self, path):
        html = self.s.get(BASE + path, timeout=15).text
        self.csrf = re.search(r'name="csrf-token" content="([^"]+)"', html).group(1)
        return self.csrf

    def signup_login(self):
        t = self.token("/register")
        r = self.s.post(BASE + "/register", data={"csrf_token": t, "full_name": self.name.title(), "username": self.name,
                        "email": f"{self.name}@example.com", "password": self.pw, "confirm": self.pw}, allow_redirects=False, timeout=15)
        ok1 = r.status_code == 302
        t = self.token("/login")
        r = self.s.post(BASE + "/login", data={"csrf_token": t, "identifier": self.name, "password": self.pw}, allow_redirects=False, timeout=15)
        self.token("/")  # lấy CSRF token của phiên đã đăng nhập
        return ok1 and r.status_code == 302

    def api(self, method, path, **kw):
        h = kw.pop("headers", {}); h["X-CSRFToken"] = self.csrf
        return self.s.request(method, BASE + path, headers=h, timeout=30, **kw)

    def cookie_header(self):
        return "; ".join(f"{c.name}={c.value}" for c in self.s.cookies)


def connect(user, inbox):
    sio = socketio.Client(reconnection=False)
    sio.on("message:new", lambda d: inbox.append(("msg", d)))
    sio.on("presence:update", lambda d: inbox.append(("presence", d)))
    sio.connect(BASE, headers={"Cookie": user.cookie_header()}, transports=["websocket"], wait_timeout=15)
    return sio


def wait_for(pred, timeout=5):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.05)
    return False


def main():
    check("healthz", requests.get(BASE + "/healthz", timeout=15).status_code == 200)
    a, b = User("a"), User("b")
    check("đăng ký + đăng nhập 2 tài khoản", a.signup_login() and b.signup_login())
    check("chưa đăng nhập bị chặn", requests.get(BASE + "/api/rooms", timeout=15).status_code == 401)

    room = a.api("POST", "/api/rooms", json={"name": "Live check", "description": "tự động"}).json()["room"]
    check("B không thuộc phòng → 403", b.api("GET", f"/api/rooms/{room['id']}/messages").status_code == 403)
    check("B tham gia bằng mã mời", b.api("POST", "/api/rooms/join", json={"code": room["invite_code"]}).status_code == 200)

    # Kết nối WebSocket thật
    inbox_a, inbox_b = [], []
    sa, sb = connect(a, inbox_a), connect(b, inbox_b)
    check("WebSocket kết nối (transport=websocket)", sa.transport() == "websocket" and sb.transport() == "websocket", sa.transport())
    try:
        bad = socketio.Client(reconnection=False)
        bad.connect(BASE, transports=["websocket"], wait_timeout=5)
        check("WebSocket không cookie bị từ chối", False)
    except Exception:
        check("WebSocket không cookie bị từ chối", True)

    ack = {}
    def send(sio, content, cid):
        done = threading.Event()
        sio.emit("message:send", {"room_id": room["id"], "content": content, "client_msg_id": cid},
                 callback=lambda r: (ack.update({cid: r}), done.set()))
        done.wait(10)
    send(sa, "Xin chào từ A <script>alert(1)</script> 🇻🇳", "c1")
    check("ack ok từ server", ack.get("c1", {}).get("ok") is True, ack.get("c1"))
    check("B nhận realtime", wait_for(lambda: any(k == "msg" and d["client_msg_id"] == "c1" for k, d in inbox_b)))
    check("A (người gửi) cũng nhận", wait_for(lambda: any(k == "msg" and d["client_msg_id"] == "c1" for k, d in inbox_a)))
    send(sa, "Xin chào từ A <script>alert(1)</script> 🇻🇳", "c1")
    time.sleep(0.5)
    check("gửi lại cùng client_msg_id không phát trùng", sum(1 for k, d in inbox_b if k == "msg" and d["client_msg_id"] == "c1") == 1)
    send(sb, "Chào A, mình là B", "c2")
    check("A nhận tin của B", wait_for(lambda: any(k == "msg" and d["client_msg_id"] == "c2" for k, d in inbox_a)))
    send(sa, "   ", "c3")
    check("tin rỗng bị từ chối", ack["c3"]["ok"] is False)

    hist = b.api("GET", f"/api/rooms/{room['id']}/messages").json()["messages"]
    texts = [m["content"] for m in hist if m["type"] == "text"]
    check("lịch sử lưu DB đúng thứ tự", texts == ["Xin chào từ A <script>alert(1)</script> 🇻🇳", "Chào A, mình là B"], texts)

    # Upload / download file thật qua HTTP
    png = b"\x89PNG\r\n\x1a\n" + b"x" * 2048
    r = a.api("POST", f"/api/rooms/{room['id']}/files", files={"file": ("ảnh test.png", io.BytesIO(png), "image/png")})
    check("upload PNG 201", r.status_code == 201, r.text[:200])
    fobj = r.json()["message"]["file"] if r.status_code == 201 else {}
    check("B nhận thông báo file qua WebSocket", wait_for(lambda: any(k == "msg" and d["type"] == "file" for k, d in inbox_b)))
    d = b.api("GET", fobj.get("url", "/files/0"))
    check("B tải file đúng nội dung", d.status_code == 200 and d.content == png)
    c = User("c"); c.signup_login()
    check("người ngoài phòng không tải được file", c.api("GET", fobj.get("url", "/files/0")).status_code == 404)
    check("upload .exe bị từ chối", a.api("POST", f"/api/rooms/{room['id']}/files", files={"file": ("x.exe", io.BytesIO(b"MZ"), "application/octet-stream")}).status_code == 400)
    big = a.api("POST", f"/api/rooms/{room['id']}/files", files={"file": ("big.txt", io.BytesIO(b"a" * (12 * 1024 * 1024)), "text/plain")})
    check("file vượt dung lượng bị từ chối", big.status_code in (400, 413), big.status_code)

    check("chưa đăng nhập không tải được file (401)", requests.get(BASE + fobj.get("url", "/files/0"), timeout=15).status_code == 401)

    # --- Đánh dấu đã đọc (ID thật / ID giả / ID của phòng khác) ---
    rooms_b = {r["id"]: r for r in b.api("GET", "/api/rooms").json()["rooms"]}
    check("B có tin chưa đọc", rooms_b[room["id"]]["unread"] >= 1, rooms_b[room["id"]]["unread"])
    last_id = b.api("GET", f"/api/rooms/{room['id']}/messages").json()["messages"][-1]["id"]
    rr = b.api("POST", f"/api/rooms/{room['id']}/read", json={"message_id": last_id})
    check("đánh dấu đã đọc bằng ID thật → 200", rr.status_code == 200 and rr.json()["last_read_message_id"] == last_id)
    unread_after = {r["id"]: r for r in b.api("GET", "/api/rooms").json()["rooms"]}[room["id"]]["unread"]
    check("số chưa đọc về 0", unread_after == 0, unread_after)
    check("ID không tồn tại → 404", b.api("POST", f"/api/rooms/{room['id']}/read", json={"message_id": 999999999}).status_code == 404)
    check("ID sai kiểu (chuỗi/bool/null/âm) → 400", all(
        b.api("POST", f"/api/rooms/{room['id']}/read", json={"message_id": v}).status_code == 400 for v in ("abc", True, None, -5, 1.5)))
    croom = c.api("POST", "/api/rooms", json={"name": "Phòng của C"}).json()["room"]
    cmsg = c.api("POST", f"/api/rooms/{croom['id']}/files", files={"file": ("c.png", io.BytesIO(png), "image/png")}).json()["message"]
    check("ID tin nhắn của phòng khác → 404", b.api("POST", f"/api/rooms/{room['id']}/read", json={"message_id": cmsg["id"]}).status_code == 404)

    # --- Dữ liệu sai không gây 500 ---
    bad_bodies = [b.api("POST", "/api/rooms", data="[]", headers={"Content-Type": "application/json"}),
                  b.api("POST", "/api/rooms", json={"name": 123}), b.api("POST", "/api/rooms/join", json={"code": ["x"]}),
                  b.api("POST", "/api/dm", json={"user_id": "abc"}), b.api("PATCH", "/api/me", data="null", headers={"Content-Type": "application/json"})]
    check("payload sai kiểu → 4xx (không 500)", all(400 <= r.status_code < 500 for r in bad_bodies), [r.status_code for r in bad_bodies])

    # --- Open redirect ---
    t = User("r"); t.signup_login()
    evil = ["//evil.com", "/\\evil.com", "/%5Cevil.com", "https://evil.com", "/\t/evil.com"]
    locs = []
    for n in evil:
        t.token("/login"); r = requests.post(BASE + "/login", params={"next": n}, data={"csrf_token": t.csrf, "identifier": t.name, "password": t.pw}, allow_redirects=False, timeout=15)
        locs.append(r.headers.get("Location"))
    check("open redirect bị chặn (luôn về '/' hoặc đã đăng nhập)", all(l in ("/", None) or "evil" not in (l or "") for l in locs), locs)

    # --- Chat riêng 1-1 realtime ---
    b_id = b.api("GET", "/api/me").json()["user"]["id"]
    dm = a.api("POST", "/api/dm", json={"user_id": b_id}).json()["room"]
    dm2 = b.api("POST", "/api/dm", json={"user_id": a.api("GET", "/api/me").json()["user"]["id"]}).json()["room"]
    check("DM 1-1 dùng chung một phòng, không tạo trùng", dm["id"] == dm2["id"] and dm["is_direct"])
    check("người thứ ba không đọc được DM", c.api("GET", f"/api/rooms/{dm['id']}/messages").status_code == 403)
    def send_to(sio, rid, content, cid):
        done = threading.Event()
        sio.emit("message:send", {"room_id": rid, "content": content, "client_msg_id": cid}, callback=lambda r: (ack.update({cid: r}), done.set()))
        done.wait(10)
    send_to(sa, dm["id"], "tin riêng từ A", "d1")
    check("B nhận tin DM realtime", wait_for(lambda: any(k == "msg" and d["client_msg_id"] == "d1" and d["room_id"] == dm["id"] for k, d in inbox_b)))

    # --- Kết nối lại WebSocket: B rớt mạng, A nhắn, B nối lại và đồng bộ ---
    seen = b.api("GET", f"/api/rooms/{room['id']}/messages").json()["messages"][-1]["id"]
    sb.disconnect(); time.sleep(0.4)
    send(sa, "tin B bị lỡ khi mất mạng", "m-miss")
    inbox_b2 = []
    sb = connect(b, inbox_b2)
    missed = b.api("GET", f"/api/rooms/{room['id']}/messages", params={"after_id": seen}).json()["messages"]
    check("kết nối lại → lấy lại được tin bị lỡ qua REST (after_id)", any(m["client_msg_id"] == "m-miss" for m in missed))
    send(sa, "tin sau khi B nối lại", "m-after")
    check("kết nối lại → nhận realtime trở lại", wait_for(lambda: any(k == "msg" and d["client_msg_id"] == "m-after" for k, d in inbox_b2)))
    send_to(sb, room["id"], "B gửi lại", "b-dup"); send_to(sb, room["id"], "B gửi lại", "b-dup")
    texts = [m["content"] for m in a.api("GET", f"/api/rooms/{room['id']}/messages").json()["messages"] if m["type"] == "text"]
    check("gửi lại cùng client_msg_id không tạo tin trùng", texts.count("B gửi lại") == 1)

    # --- Giới hạn upload: tối đa 5 file / 60 giây / tài khoản, người khác không bị ảnh hưởng ---
    codes = [c.api("POST", f"/api/rooms/{croom['id']}/files", files={"file": (f"r{i}.png", io.BytesIO(png), "image/png")}).status_code for i in range(6)]
    check("upload vượt 5 file/60s → HTTP 429", codes[-1] == 429 and all(x == 201 for x in codes[:4]), codes)
    rl = c.api("POST", f"/api/rooms/{croom['id']}/files", files={"file": ("again.png", io.BytesIO(png), "image/png")})
    check("429 kèm Retry-After", rl.status_code == 429 and int(rl.headers.get("Retry-After", 0)) >= 1)
    check("người dùng khác vẫn upload được", b.api("POST", f"/api/rooms/{room['id']}/files", files={"file": ("ok.png", io.BytesIO(png), "image/png")}).status_code == 201)

    # Presence nhiều thiết bị: A mở thêm kết nối thứ 2
    a_pres_before = len([1 for k, d in inbox_b if k == "presence"])
    sa2 = connect(a, [])
    sa.disconnect(); time.sleep(0.6)
    members = b.api("GET", f"/api/rooms/{room['id']}").json()["room"]["members"]
    check("A còn 1 kết nối → vẫn Online", any(m["username"] == a.name and m["online"] for m in members))
    sa2.disconnect(); time.sleep(0.8)
    members = b.api("GET", f"/api/rooms/{room['id']}").json()["room"]["members"]
    check("A hết kết nối → Offline", any(m["username"] == a.name and not m["online"] for m in members))
    check("B nhận sự kiện Offline", wait_for(lambda: any(k == "presence" and d["online"] is False for k, d in inbox_b2)))

    # Đăng xuất ngắt WebSocket
    sb_open = sb.connected
    r = b.api("POST", "/logout", allow_redirects=False)
    time.sleep(0.8)
    check("đăng xuất ngắt kết nối WebSocket", sb_open and not sb.connected)
    check("sau đăng xuất API trả 401", b.api("GET", "/api/rooms").status_code == 401)

    # Dọn dẹp
    a.api("DELETE", f"/api/rooms/{room['id']}")
    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} đạt" + (f" — LỖI: {failed}" if failed else ""))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
