import pytest

from app.extensions import socketio
from app.sockets import presence
from conftest import login, register
from test_rooms import mk_room


def sock(app, http):
    return socketio.test_client(app, flask_test_client=http)


def setup_users(make_user, app, names=("alice", "bob")):
    return [make_user(n) for n in names]


def test_unauthenticated_socket_rejected(app):
    c = socketio.test_client(app)
    assert not c.is_connected()


def test_two_clients_chat_and_isolation(make_user, app):
    a, b, c = setup_users(make_user, app, ("alice", "bob", "carol"))
    room, other = mk_room(a, "R1"), mk_room(c, "R2")
    b.post("/api/rooms/join", json={"code": room["invite_code"]})
    sa, sb, sc = sock(app, a), sock(app, b), sock(app, c)
    for s in (sa, sb, sc):
        s.get_received()
    ack = sa.emit("message:send", {"room_id": room["id"], "content": "Xin chào <b>Việt Nam</b>",
                                  "client_msg_id": "m1"}, callback=True)
    assert ack["ok"] and ack["message"]["content"] == "Xin chào <b>Việt Nam</b>"  # lưu thô, client sẽ render textContent
    for s in (sa, sb):  # người gửi và người nhận cùng phòng đều nhận
        got = [e for e in s.get_received() if e["name"] == "message:new"]
        assert len(got) == 1 and got[0]["args"][0]["id"] == ack["message"]["id"]
    assert [e for e in sc.get_received() if e["name"] == "message:new"] == []     # phòng khác không nhận
    # gửi lại cùng client_msg_id (reconnect) → không phát/lưu trùng
    ack2 = sa.emit("message:send", {"room_id": room["id"], "content": "Xin chào <b>Việt Nam</b>",
                                   "client_msg_id": "m1"}, callback=True)
    assert ack2["ok"] and ack2["message"]["id"] == ack["message"]["id"]
    assert [e for e in sb.get_received() if e["name"] == "message:new"] == []
    hist = b.get(f"/api/rooms/{room['id']}/messages").get_json()["messages"]
    assert [m["content"] for m in hist if m["type"] == "text"] == ["Xin chào <b>Việt Nam</b>"]


def test_message_validation_and_permission(make_user, app):
    a, b = setup_users(make_user, app)
    room = mk_room(a)
    sa, sb = sock(app, a), sock(app, b)
    send = lambda s, d: s.emit("message:send", d, callback=True)
    assert send(sa, {"room_id": room["id"], "content": "   "})["ok"] is False
    assert send(sa, {"room_id": room["id"], "content": "x" * 2001})["ok"] is False
    assert send(sa, "không phải dict")["ok"] is False
    assert send(sa, {"room_id": "abc", "content": "hi"})["ok"] is False
    r = send(sb, {"room_id": room["id"], "content": "chen ngang"})                # không phải thành viên
    assert r["ok"] is False and r["code"] == 403
    sa.get_received()
    assert [e for e in sb.get_received() if e["name"] == "message:new"] == []


def test_rate_limit(make_user, app):
    (a,) = setup_users(make_user, app, ("alice",))
    room = mk_room(a)
    s = sock(app, a)
    results = [s.emit("message:send", {"room_id": room["id"], "content": f"m{i}"}, callback=True)["ok"]
               for i in range(7)]
    assert results[:5] == [True] * 5 and results[5:] == [False, False]            # cấu hình test: 5 tin / 10s


def test_db_failure_does_not_broadcast(make_user, app, monkeypatch):
    a, b = setup_users(make_user, app)
    room = mk_room(a); b.post("/api/rooms/join", json={"code": room["invite_code"]})
    sa, sb = sock(app, a), sock(app, b); sa.get_received(); sb.get_received()
    from app.extensions import db
    def boom(): raise RuntimeError("db down")
    monkeypatch.setattr(db.session, "commit", boom)
    ack = sa.emit("message:send", {"room_id": room["id"], "content": "hi"}, callback=True)
    monkeypatch.undo()
    assert ack["ok"] is False and "db down" not in ack["error"]                   # không lộ lỗi nội bộ
    assert [e for e in sb.get_received() if e["name"] == "message:new"] == []     # không phát khi lưu lỗi


def test_presence_multi_device(make_user, app):
    a, b = setup_users(make_user, app)
    room = mk_room(a); b.post("/api/rooms/join", json={"code": room["invite_code"]})
    sb = sock(app, b); sb.get_received()
    a2 = app.test_client(); login(a2, "alice")                                    # alice đăng nhập thêm thiết bị thứ 2
    sa1, sa2 = sock(app, a), sock(app, a2)
    ev = [e["args"][0] for e in sb.get_received() if e["name"] == "presence:update"]
    assert ev == [{"user_id": 1, "online": True}]                                  # chỉ 1 lần Online dù có 2 kết nối
    sa1.disconnect()
    assert [e for e in sb.get_received() if e["name"] == "presence:update"] == []   # còn thiết bị 2 → vẫn Online
    assert presence.is_online(1)
    sa2.disconnect()
    ev = [e["args"][0] for e in sb.get_received() if e["name"] == "presence:update"]
    assert ev == [{"user_id": 1, "online": False}]
    members = b.get(f"/api/rooms/{room['id']}").get_json()["room"]["members"]
    assert {m["username"]: m["online"] for m in members} == {"alice": False, "bob": True}


def test_upload_broadcasts_and_kick_removes_from_room(make_user, app):
    import io
    from conftest import tiny_png
    a, b = setup_users(make_user, app)
    room = mk_room(a); b.post("/api/rooms/join", json={"code": room["invite_code"]})
    sa, sb = sock(app, a), sock(app, b); sa.get_received(); sb.get_received()
    a.post(f"/api/rooms/{room['id']}/files", data={"file": (io.BytesIO(tiny_png()), "p.png")},
           content_type="multipart/form-data")
    assert any(e["name"] == "message:new" and e["args"][0]["type"] == "file" for e in sb.get_received())
    from app.models import User
    with app.app_context():
        bob_id = User.query.filter_by(username="bob").one().id
    a.delete(f"/api/rooms/{room['id']}/members/{bob_id}")
    sb.get_received()
    sa.emit("message:send", {"room_id": room["id"], "content": "sau khi kick"}, callback=True)
    assert [e for e in sb.get_received() if e["name"] == "message:new"] == []


def test_reconnect_resync_and_no_duplicates(make_user, app):
    """Mất kết nối → tin lỡ được lấy lại qua REST after_id; gửi lại cùng client_msg_id không tạo tin trùng."""
    a, b = setup_users(make_user, app)
    room = mk_room(a); b.post("/api/rooms/join", json={"code": room["invite_code"]})
    sa, sb = sock(app, a), sock(app, b)
    first = sa.emit("message:send", {"room_id": room["id"], "content": "trước khi mất mạng", "client_msg_id": "r1"}, callback=True)
    last_seen = first["message"]["id"]
    sb.disconnect()                                                                # B rớt mạng
    sa.emit("message:send", {"room_id": room["id"], "content": "tin B bị lỡ", "client_msg_id": "r2"}, callback=True)
    sb2 = sock(app, b); sb2.get_received()                                         # B kết nối lại (server tự cho vào lại các phòng)
    missed = b.get(f"/api/rooms/{room['id']}/messages?after_id={last_seen}").get_json()["messages"]
    assert [m["content"] for m in missed] == ["tin B bị lỡ"]                       # đồng bộ phần bị lỡ từ server
    sa.emit("message:send", {"room_id": room["id"], "content": "sau khi nối lại", "client_msg_id": "r3"}, callback=True)
    assert [e["args"][0]["content"] for e in sb2.get_received() if e["name"] == "message:new"] == ["sau khi nối lại"]
    # B gửi tin, client không nhận được ack (rớt mạng) nên gửi lại cùng client_msg_id sau khi kết nối lại
    r1 = sb2.emit("message:send", {"room_id": room["id"], "content": "gửi từ B", "client_msg_id": "b1"}, callback=True)
    sb2.disconnect(); sb3 = sock(app, b)
    r2 = sb3.emit("message:send", {"room_id": room["id"], "content": "gửi từ B", "client_msg_id": "b1"}, callback=True)
    assert r1["message"]["id"] == r2["message"]["id"]
    texts = [m["content"] for m in b.get(f"/api/rooms/{room['id']}/messages").get_json()["messages"] if m["type"] == "text"]
    assert texts.count("gửi từ B") == 1
