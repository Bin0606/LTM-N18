# Kiến trúc ConnectHub

## 1. Tổng quan Client – Server

```mermaid
flowchart LR
    subgraph Clients["Client: một website responsive"]
        D["Trình duyệt máy tính"]
        M["Trình duyệt điện thoại"]
    end
    D & M -- "HTTPS: trang, REST API, upload/download" --> P
    D & M -- "WSS (Socket.IO): tin nhắn, presence" --> P
    P["Render Free: TLS + reverse proxy (hỗ trợ WebSocket)"] --> F
    subgraph F["Flask server trên Render Free (gunicorn gthread, 1 worker)"]
        R["routes/: auth, room, message, file, user"]
        S["sockets/: chat, presence"]
        V["services/: auth, room, chat, file, storage"]
        R --> V
        S --> V
    end
    V --> DB[("Neon PostgreSQL: users, rooms, room_members, messages, shared_files")]
    V --> ST[("Cloudflare R2 (S3 API): file và ảnh đại diện")]
```

| Thành phần | Vai trò | Nơi đặt |
|---|---|---|
| GitHub | Lưu source code | github.com |
| Hosting | Chạy Flask + WebSocket | Render Web Service (Free) |
| Database | Lưu user, phòng, tin nhắn, metadata file | Neon PostgreSQL (Free) |
| File Storage | Lưu nội dung file/ảnh đại diện | Cloudflare R2 (free tier) |

## 2. Sơ đồ tuần tự

### Đăng nhập
```mermaid
sequenceDiagram
    participant B as Browser
    participant S as Flask
    participant DB as Database
    B->>S: GET /login
    S-->>B: HTML + CSRF token
    B->>S: POST /login (identifier, password, csrf_token)
    S->>DB: SELECT user theo username/email
    S->>S: check_password_hash, giới hạn đăng nhập sai
    S-->>B: 302 + Set-Cookie (session, HttpOnly, Secure, SameSite=Lax)
```

### Kết nối WebSocket
```mermaid
sequenceDiagram
    participant B as Browser
    participant S as Flask-SocketIO
    participant DB as Database
    B->>S: GET /socket.io/?transport=websocket (Upgrade, kèm cookie phiên)
    S->>S: kiểm tra Origin + current_user.is_authenticated
    S->>DB: lấy danh sách phòng của user
    S->>S: join room "user:ID" và "room:ID" cho từng phòng
    S->>S: presence.add(user, sid) → nếu là kết nối đầu tiên: Online
    S-->>B: 101 Switching Protocols
    S-->>B: presence:update (tới các phòng chung)
```

### Gửi tin nhắn
```mermaid
sequenceDiagram
    participant A as Client A
    participant S as Flask-SocketIO
    participant DB as Database
    participant O as Client khác trong phòng
    A->>S: message:send {room_id, content, client_msg_id}
    S->>S: xác thực, kiểm tra thành viên, nội dung, rate limit
    S->>DB: INSERT messages + COMMIT
    alt lưu thành công
        S-->>A: ack {ok:true, message}
        S-->>A: message:new (room:ID)
        S-->>O: message:new (room:ID)
    else lưu lỗi
        S->>DB: ROLLBACK
        S-->>A: ack {ok:false, error}  (KHÔNG phát cho ai)
    end
```

### Upload file
```mermaid
sequenceDiagram
    participant B as Browser
    participant S as Flask
    participant ST as Storage
    participant DB as Database
    participant W as Các client trong phòng
    B->>S: POST /api/rooms/ID/files (multipart, X-CSRFToken)
    S->>S: xác thực, giới hạn 5 file/60 giây/tài khoản (429), kiểm tra thành viên
    S->>S: đuôi file, MIME, nội dung (magic bytes), dung lượng
    S->>ST: lưu với tên ngẫu nhiên files/ROOM/uuid.ext
    S->>DB: INSERT messages(type=file) + shared_files
    S-->>B: 201 {message}
    S-->>W: message:new (WebSocket)
```

### Download file
```mermaid
sequenceDiagram
    participant B as Browser
    participant S as Flask
    participant DB as Database
    participant ST as Storage
    B->>S: GET /files/ID (cookie phiên)
    S->>DB: lấy shared_files + kiểm tra user thuộc phòng
    alt có quyền và storage trả file
        S->>ST: mở storage_key
        S-->>B: 200 stream, Content-Disposition, nosniff
    else không quyền / file không tồn tại trong DB
        S-->>B: 404 (không chạm tới storage)
    else storage không khả dụng (mạng, quá hạn mức, sai khóa)
        S-->>B: 503 + Retry-After
    end
```

## 3. Cơ sở dữ liệu

```mermaid
erDiagram
    USERS ||--o{ ROOMS : owner_id
    USERS ||--o{ ROOM_MEMBERS : user_id
    ROOMS ||--o{ ROOM_MEMBERS : room_id
    USERS ||--o{ MESSAGES : sender_id
    ROOMS ||--o{ MESSAGES : room_id
    MESSAGES ||--o| SHARED_FILES : message_id
    ROOMS ||--o{ SHARED_FILES : room_id
    USERS ||--o{ SHARED_FILES : uploader_id
```
Tất cả khóa ngoại dùng `ON DELETE CASCADE`. Index chính: `messages(room_id, id)`, `room_members(user_id)`, `shared_files(room_id, created_at)`, unique `invite_code`, `username`, `email`, `(room_id, user_id)`, `(sender_id, client_msg_id)`.
