"""Kiểm thử các điểm đã gia cố: đánh dấu đã đọc, giới hạn upload, dữ liệu đầu vào sai, race DM/join,
open redirect, giới hạn đăng nhập/đăng ký, lỗi storage và quyền tải file."""
import io

import pytest

from app.extensions import db
from app.models import Message, Room, RoomMember, SharedFile, User
from app.services import auth_service, room_service
from app.services.storage import StorageNotFound, StorageUnavailable
from conftest import login, register, tiny_png
from test_files import up
from test_rooms import mk_room


def uid(app, username):
    with app.app_context():
        return User.query.filter_by(username=username).one().id


def last_msg_id(c, room_id):
    return c.get(f"/api/rooms/{room_id}/messages").get_json()["messages"][-1]["id"]


# ---------------------------------------------------------------- 1. đánh dấu đã đọc
def test_mark_read_valid_and_never_goes_backwards(make_user, app):
    a, b = make_user("alice"), make_user("bob")
    room = mk_room(a)
    b.post("/api/rooms/join", json={"code": room["invite_code"]})
    from conftest import login as _l  # noqa
    # tạo 2 tin nhắn từ alice (qua DB service vì test này không dùng socket)
    with app.app_context():
        for t in ("m1", "m2"):
            db.session.add(Message(room_id=room["id"], sender_id=uid(app, "alice"), content=t, message_type="text"))
        db.session.commit()
    msgs = b.get(f"/api/rooms/{room['id']}/messages").get_json()["messages"]
    texts = [m for m in msgs if m["type"] == "text"]
    assert b.get("/api/rooms").get_json()["rooms"][0]["unread"] == 2
    r = b.post(f"/api/rooms/{room['id']}/read", json={"message_id": texts[1]["id"]})
    assert r.status_code == 200 and r.get_json()["last_read_message_id"] == texts[1]["id"]
    assert b.get("/api/rooms").get_json()["rooms"][0]["unread"] == 0
    r = b.post(f"/api/rooms/{room['id']}/read", json={"message_id": texts[0]["id"]})   # ID cũ hơn: không lùi
    assert r.status_code == 200 and r.get_json()["last_read_message_id"] == texts[1]["id"]
    assert b.post(f"/api/rooms/{room['id']}/read", json={"message_id": str(texts[1]["id"])}).status_code == 200


@pytest.mark.parametrize("payload,status", [
    ({"message_id": 999999}, 404),         # ID không tồn tại
    ({"message_id": "abc"}, 400), ({"message_id": True}, 400), ({"message_id": 1.5}, 400),
    ({"message_id": None}, 400), ({"message_id": -3}, 400), ({"message_id": 0}, 400),
    ({"message_id": [1]}, 400), ({"message_id": {"a": 1}}, 400), ({"message_id": 10 ** 30}, 400),
    ({}, 400),
])
def test_mark_read_invalid_ids(make_user, payload, status):
    a = make_user("alice")
    room = mk_room(a)
    assert a.post(f"/api/rooms/{room['id']}/read", json=payload).status_code == status


def test_mark_read_message_of_another_room_and_bad_bodies(make_user, app):
    a, b = make_user("alice"), make_user("bob")
    r1, r2 = mk_room(a, "Phòng R1"), mk_room(b, "Phòng R2")
    with app.app_context():
        m = Message(room_id=r2["id"], sender_id=uid(app, "bob"), content="x", message_type="text")
        db.session.add(m); db.session.commit(); foreign = m.id
    # alice là thành viên r1 nhưng gửi ID tin của phòng r2 → 404 và KHÔNG đổi last_read
    assert a.post(f"/api/rooms/{r1['id']}/read", json={"message_id": foreign}).status_code == 404
    with app.app_context():
        assert RoomMember.query.filter_by(room_id=r1["id"], user_id=uid(app, "alice")).one().last_read_message_id == 0
    assert a.post(f"/api/rooms/{r2['id']}/read", json={"message_id": foreign}).status_code == 403  # không phải thành viên
    for body in ("[]", "null", '"x"', "123", "{bad json"):
        r = a.post(f"/api/rooms/{r1['id']}/read", data=body, content_type="application/json")
        assert r.status_code == 400, body
    assert a.post(f"/api/rooms/{r1['id']}/read", data="x", content_type="text/plain").status_code == 400


# ---------------------------------------------------------------- 2. giới hạn upload
def test_upload_rate_limit_5_per_60s_per_user(make_user, app):
    app.config["UPLOAD_RATE_LIMIT"] = (5, 60)
    a, b = make_user("alice"), make_user("bob")
    ra, rb = mk_room(a, "Phòng A"), mk_room(b, "Phòng B")
    for i in range(5):
        assert up(a, ra["id"], f"p{i}.png", tiny_png()).status_code == 201
    r = up(a, ra["id"], "p6.png", tiny_png())
    assert r.status_code == 429 and int(r.headers["Retry-After"]) >= 1 and r.get_json()["retry_after"] >= 1
    assert "5 file" in r.get_json()["error"]
    assert up(b, rb["id"], "other.png", tiny_png()).status_code == 201          # người khác không bị ảnh hưởng
    with app.app_context():
        assert SharedFile.query.filter_by(room_id=ra["id"]).count() == 5        # lượt thứ 6 không được lưu


def test_upload_rate_limit_counts_rejected_files_and_window_expires(make_user, app, monkeypatch):
    app.config["UPLOAD_RATE_LIMIT"] = (3, 60)
    a = make_user("alice"); room = mk_room(a)
    for _ in range(3):
        assert up(a, room["id"], "x.exe", b"MZ").status_code == 400            # file sai vẫn tính lượt (chống spam file rác)
    assert up(a, room["id"], "ok.png", tiny_png()).status_code == 429
    import time as _t
    real = _t.monotonic
    monkeypatch.setattr("app.services.ratelimit.time.monotonic", lambda: real() + 61)   # hết cửa sổ 60 giây
    assert up(a, room["id"], "ok.png", tiny_png()).status_code == 201


# ---------------------------------------------------------------- 3. dữ liệu đầu vào sai không gây 500
BAD_VALUES = [None, 123, 1.5, True, [], ["a"], {}, {"a": 1}, "", "   ", "x" * 500, "a\x00b", "a\x07b"]


@pytest.mark.parametrize("value", BAD_VALUES)
def test_create_room_bad_name_never_500(make_user, value):
    a = make_user("alice")
    r = a.post("/api/rooms", json={"name": value, "description": "ok"})
    assert r.status_code == 400, (value, r.status_code)


@pytest.mark.parametrize("value", [123, 1.5, True, [], {}, "x" * 501, "a\x00b"])
def test_create_room_bad_description_never_500(make_user, value):
    a = make_user("alice")
    assert a.post("/api/rooms", json={"name": "Phòng hợp lệ", "description": value}).status_code == 400
    assert a.post("/api/rooms", json={"name": "Phòng hợp lệ", "description": None}).status_code == 201   # null = rỗng


@pytest.mark.parametrize("body", ["[]", "null", '"s"', "42", "true", "{bad", ""])
def test_non_object_json_bodies_rejected_everywhere(make_user, body):
    a = make_user("alice"); room = mk_room(a)
    ct = {"content_type": "application/json"}
    for method, url in (("post", "/api/rooms"), ("patch", f"/api/rooms/{room['id']}"), ("post", "/api/rooms/join"),
                        ("post", "/api/dm"), ("patch", "/api/me")):
        r = getattr(a, method)(url, data=body, **ct)
        assert r.status_code == 400, (url, body, r.status_code)


@pytest.mark.parametrize("value", [None, 123, [], {}, True, "", "  ", "a" * 200, "a\x00"])
def test_join_bad_code_never_500(make_user, value):
    a = make_user("alice")
    assert a.post("/api/rooms/join", json={"code": value}).status_code in (400, 404)


@pytest.mark.parametrize("value", [None, "abc", True, 1.5, -1, 0, [], {}, 10 ** 30, "1.5", "9" * 30])
def test_dm_bad_user_id_never_500(make_user, value):
    a = make_user("alice")
    assert a.post("/api/dm", json={"user_id": value}).status_code in (400, 404)


def test_dm_self_and_unknown_user_and_string_id(make_user, app):
    a, _ = make_user("alice"), make_user("bob")
    assert a.post("/api/dm", json={"user_id": uid(app, "alice")}).status_code == 400
    assert a.post("/api/dm", json={"user_id": 987654}).status_code == 404
    assert a.post("/api/dm", json={"user_id": str(uid(app, "bob"))}).status_code == 200


@pytest.mark.parametrize("payload", [{"name": 5}, {"name": None}, {"name": []}, {"description": 5}, {}, {"name": " "}])
def test_edit_room_bad_payload_never_500(make_user, payload):
    a = make_user("alice"); room = mk_room(a)
    assert a.patch(f"/api/rooms/{room['id']}", json=payload).status_code == 400


@pytest.mark.parametrize("payload", [{"full_name": None}, {"full_name": 5}, {"full_name": "x"}, {"status_text": 5},
                                     {"status_text": "y" * 200}, {}, {"full_name": ["a"]}])
def test_profile_bad_payload_never_500(make_user, payload):
    a = make_user("alice")
    assert a.patch("/api/me", json=payload).status_code == 400


def test_socket_room_id_strict(make_user, app):
    from app.extensions import socketio
    a = make_user("alice"); room = mk_room(a)
    s = socketio.test_client(app, flask_test_client=a)
    for bad in (True, 1.5, None, "abc", [], -1):
        r = s.emit("message:send", {"room_id": bad, "content": "hi"}, callback=True)
        assert r["ok"] is False and r["code"] == 400, bad
    assert s.emit("message:send", {"room_id": room["id"], "content": "hi"}, callback=True)["ok"] is True


# ---------------------------------------------------------------- 3b. race: hai người cùng mở DM / cùng join
def test_dm_race_returns_existing_room(make_user, app, monkeypatch):
    a, b = make_user("alice"), make_user("bob")
    first = a.post("/api/dm", json={"user_id": uid(app, "bob")}).get_json()["room"]["id"]
    real, calls = room_service._find_dm, {"n": 0}

    def flaky(key):  # lần tra cứu đầu "không thấy" (bên kia vừa tạo xong) → buộc đi vào nhánh tạo → IntegrityError
        calls["n"] += 1
        return None if calls["n"] == 1 else real(key)
    monkeypatch.setattr(room_service, "_find_dm", flaky)
    r = b.post("/api/dm", json={"user_id": uid(app, "alice")})
    assert r.status_code == 200 and r.get_json()["room"]["id"] == first and calls["n"] == 2
    with app.app_context():
        assert Room.query.filter_by(is_direct=True).count() == 1
        assert RoomMember.query.filter_by(room_id=first).count() == 2            # không có thành viên trùng/rác
    assert a.post("/api/rooms", json={"name": "vẫn dùng được DB"}).status_code == 201   # session đã rollback sạch


def test_join_race_is_idempotent(make_user, app, monkeypatch):
    a, b = make_user("alice"), make_user("bob")
    room = mk_room(a)
    assert b.post("/api/rooms/join", json={"code": room["invite_code"]}).status_code == 200
    real, calls = room_service.get_membership, {"n": 0}

    def flaky(user_id, room_id):
        calls["n"] += 1
        return None if calls["n"] == 1 else real(user_id, room_id)
    monkeypatch.setattr(room_service, "get_membership", flaky)
    r = b.post("/api/rooms/join", json={"code": room["invite_code"]})           # bản ghi thành viên đã có → IntegrityError
    assert r.status_code == 200 and r.get_json()["joined"] is False
    with app.app_context():
        assert RoomMember.query.filter_by(room_id=room["id"]).count() == 2


# ---------------------------------------------------------------- 4. open redirect
BAD_NEXT = ["//evil.com", "///evil.com", "/\\evil.com", "/\\\\evil.com", "\\evil.com", "\\\\evil.com", "/%5Cevil.com",
            "/%5cevil.com", "/%2F/evil.com", "/%252F/evil.com", "/%255Cevil.com", "https://evil.com", "http:evil.com",
            "javascript:alert(1)", "data:text/html,x", "/\t/evil.com", "/\n/evil.com", "/\r/evil.com", "/%09/evil.com",
            "/%0d%0a/evil.com", " //evil.com", "/ /evil.com", "/é", "evil.com", "", "/" + "a" * 600, "/../\\evil.com"]


@pytest.mark.parametrize("target", BAD_NEXT)
def test_safe_next_blocks_external(client, target):
    register(client, "alice")
    r = client.post("/login", query_string={"next": target}, data={"identifier": "alice", "password": "Passw0rd!"})
    assert r.status_code == 302 and r.headers["Location"] == "/", target


@pytest.mark.parametrize("target", ["/join/abc123", "/", "/api/rooms?x=1", "/join/a-b_c"])
def test_safe_next_allows_internal(client, target):
    register(client, "alice")
    r = client.post("/login", query_string={"next": target}, data={"identifier": "alice", "password": "Passw0rd!"})
    assert r.headers["Location"] == target


# ---------------------------------------------------------------- 5. chống spam đăng nhập / đăng ký
def _login_flash(r):
    import re
    m = re.search(r'class="alert alert-error"[^>]*>([^<]+)<', r.get_data(as_text=True))
    return m.group(1) if m else None


def test_login_does_not_reveal_whether_account_exists(client):
    register(client, "alice")
    known = login(client, "alice", "sai-mat-khau")
    unknown = login(client, "nguoi-la-khong-ton-tai", "sai-mat-khau")
    assert known.status_code == unknown.status_code == 401
    assert _login_flash(known) == _login_flash(unknown) == "Email/username hoặc mật khẩu không đúng."


def test_lockout_message_identical_for_existing_and_unknown(client, monkeypatch):
    monkeypatch.setattr(auth_service, "LIMIT_IP_ID", 3)
    register(client, "alice")
    for who in ("alice", "ghost"):
        for _ in range(3):
            assert login(client, who, "bad").status_code == 401
    a, g = login(client, "alice", "bad"), login(client, "ghost", "bad")
    assert a.status_code == g.status_code == 429 and _login_flash(a) == _login_flash(g)
    assert int(a.headers.get("Retry-After", 0)) >= 0
    assert login(client, "alice", "Passw0rd!").status_code == 429              # đang khóa: đúng mật khẩu cũng bị chặn


def test_rotating_usernames_from_one_ip_is_blocked(client, monkeypatch):
    monkeypatch.setattr(auth_service, "LIMIT_IP", 6)
    register(client, "alice")
    for i in range(6):
        assert login(client, f"user{i}", "bad").status_code == 401
    assert login(client, "user99", "bad").status_code == 429                    # đổi username liên tục vẫn bị chặn theo IP
    assert login(client, "alice", "Passw0rd!").status_code == 429


def test_distributed_attack_on_one_account_is_blocked(app, monkeypatch):
    monkeypatch.setattr(auth_service, "LIMIT_ID", 4)
    c = app.test_client(); register(c, "alice")
    for i in range(4):
        r = c.post("/login", data={"identifier": "alice", "password": "bad"}, environ_overrides={"REMOTE_ADDR": f"10.1.0.{i}"})
        assert r.status_code == 401
    r = c.post("/login", data={"identifier": "alice", "password": "Passw0rd!"}, environ_overrides={"REMOTE_ADDR": "10.1.0.99"})
    assert r.status_code == 429


def test_successful_login_resets_counter_and_non_string_input(client):
    register(client, "alice")
    for _ in range(3):
        login(client, "alice", "bad")
    assert login(client, "alice").status_code == 302
    assert auth_service.authenticate(None, "x") is None and auth_service.authenticate("a", ["x"]) is None


def test_register_rate_limit_per_ip(app):
    app.config["REGISTER_RATE_LIMIT"] = 2
    c = app.test_client()
    assert register(c, "user1").status_code == 302 and register(c, "user2").status_code == 302
    r = register(c, "user3")
    assert r.status_code == 429
    r2 = c.post("/register", data={"full_name": "A B", "username": "user4", "email": "user4@example.com", "password": "Passw0rd!",
                                   "confirm": "Passw0rd!"}, environ_overrides={"REMOTE_ADDR": "10.9.9.9"})
    assert r2.status_code == 302                                                 # IP khác không bị ảnh hưởng


def test_limiter_memory_is_bounded():
    from app.services.ratelimit import SlidingWindowLimiter
    lim = SlidingWindowLimiter(max_keys=100)
    for i in range(1000):
        lim.add(f"k{i}", 60)
    assert len(lim._data) <= 101


# ---------------------------------------------------------------- 6. lỗi storage & quyền tải file
def upload_png(c, room_id):
    r = up(c, room_id, "a.png", tiny_png(), "image/png")
    assert r.status_code == 201
    return r.get_json()["message"]["file"]


def test_download_404_vs_503_are_distinguished(make_user, app, monkeypatch):
    a = make_user("alice"); room = mk_room(a)
    f = upload_png(a, room["id"])
    storage = app.extensions["storage"]

    def missing(key): raise StorageNotFound("x")
    def down(key): raise StorageUnavailable("x")
    monkeypatch.setattr(storage, "open", missing)
    r = a.get(f["url"])
    assert r.status_code == 404 and "Retry-After" not in r.headers
    monkeypatch.setattr(storage, "open", down)
    r = a.get(f["url"])
    assert r.status_code == 503 and r.headers["Retry-After"] and "không khả dụng" in r.get_json()["error"]


def test_real_missing_local_file_is_404_not_500(make_user, app):
    import os
    a = make_user("alice"); room = mk_room(a)
    f = upload_png(a, room["id"])
    root = os.path.join(app.config["UPLOAD_DIR"], "files", str(room["id"]))
    for name in os.listdir(root):
        os.remove(os.path.join(root, name))
    assert a.get(f["url"]).status_code == 404


def test_unauthorized_users_never_reach_storage(make_user, app, monkeypatch):
    a, b, eve = make_user("alice"), make_user("bob"), make_user("eve")
    room = mk_room(a)
    b.post("/api/rooms/join", json={"code": room["invite_code"]})
    f = upload_png(a, room["id"])
    assert b.get(f["url"]).status_code == 200
    calls = []
    real_open = app.extensions["storage"].open
    monkeypatch.setattr(app.extensions["storage"], "open", lambda k: calls.append(k) or real_open(k))
    assert eve.get(f["url"]).status_code == 404                                  # người ngoài phòng
    assert app.test_client().get(f["url"]).status_code == 401                    # chưa đăng nhập
    assert calls == []                                                           # storage không bị chạm tới khi không có quyền
    b.post(f"/api/rooms/{room['id']}/leave")
    assert b.get(f["url"]).status_code == 404                                    # đã rời phòng
    assert calls == []
    b.post("/api/rooms/join", json={"code": room["invite_code"]})
    a.delete(f"/api/rooms/{room['id']}/members/{uid(app, 'bob')}")
    assert b.get(f["url"]).status_code == 404                                    # bị kick
    assert eve.get("/files/abc").status_code == 404 and eve.get("/files/-1").status_code == 404


def test_download_headers_safe_and_delete_room_removes_files(make_user, app):
    a = make_user("alice"); room = mk_room(a)
    r = up(a, room["id"], "bao cao.pdf", b"%PDF-1.4 x")
    f = r.get_json()["message"]["file"]
    d = a.get(f["url"])
    assert d.headers["X-Content-Type-Options"] == "nosniff" and "sandbox" in d.headers["Content-Security-Policy"]
    assert d.headers["Content-Disposition"].startswith("attachment")
    assert a.delete(f"/api/rooms/{room['id']}").status_code == 200
    with app.app_context():
        assert SharedFile.query.count() == 0
    import os
    assert not os.path.exists(os.path.join(app.config["UPLOAD_DIR"], "files", str(room["id"]))) or not os.listdir(
        os.path.join(app.config["UPLOAD_DIR"], "files", str(room["id"])))


def test_upload_storage_down_returns_503_and_saves_nothing(make_user, app, monkeypatch):
    a = make_user("alice"); room = mk_room(a)
    def down(*args, **kw): raise StorageUnavailable("x")
    monkeypatch.setattr(app.extensions["storage"], "save", down)
    r = up(a, room["id"], "a.png", tiny_png())
    assert r.status_code == 503 and r.headers["Retry-After"]
    with app.app_context():
        assert SharedFile.query.count() == 0 and Message.query.filter_by(message_type="file").count() == 0


def test_avatar_upload_download_and_storage_errors(make_user, app, monkeypatch):
    a = make_user("alice")
    r = a.post("/api/me/avatar", data={"file": (io.BytesIO(tiny_png()), "me.png", "image/png")}, content_type="multipart/form-data")
    assert r.status_code == 200
    url = r.get_json()["user"]["avatar_url"]
    assert a.get(url).status_code == 200 and app.test_client().get(url).status_code == 401
    monkeypatch.setattr(app.extensions["storage"], "open", lambda k: (_ for _ in ()).throw(StorageUnavailable("x")))
    assert a.get(url).status_code == 503
    monkeypatch.setattr(app.extensions["storage"], "open", lambda k: (_ for _ in ()).throw(StorageNotFound("x")))
    assert a.get(url).status_code == 404
