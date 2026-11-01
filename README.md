# ConnectHub: nền tảng chat nhóm realtime và chia sẻ tài liệu

Ứng dụng web Client–Server: nhiều người dùng đăng nhập, tạo/tham gia phòng chat bằng link hoặc mã mời, nhắn tin realtime qua **WebSocket (Flask-SocketIO)**, gửi/tải ảnh và tài liệu, xem lịch sử, theo dõi Online/Offline. Một website responsive duy nhất dùng cho máy tính và điện thoại.

**Stack:** Python · Flask · Flask-SocketIO · Flask-SQLAlchemy · Flask-Login · Flask-WTF (CSRF) · Flask-Migrate · SQLite (dev) / PostgreSQL (production) · HTML/CSS/JavaScript thuần · gunicorn. Triển khai miễn phí: **Render Free + Neon PostgreSQL + Cloudflare R2**.

## Cấu trúc

```
connecthub/
├── app/
│   ├── __init__.py        # application factory, bảo mật header, xử lý lỗi
│   ├── config.py          # cấu hình đọc từ biến môi trường
│   ├── extensions.py
│   ├── models/            # user, room (+RoomMember), message, shared_file
│   ├── routes/            # auth, main, room (REST), file (download), user (hồ sơ, avatar)
│   ├── sockets/           # chat.py (Socket.IO), presence.py, events.py
│   ├── services/          # auth, room, chat, file, storage (local / S3)
│   ├── templates/         # login, register, chat
│   └── static/            # css/, js/ (+ vendor/socket.io), images/
├── migrations/            # Alembic (Flask-Migrate)
├── tests/                 # pytest + live_check.py (server thật) + ui/ui_smoke.js
├── docs/                  # ARCHITECTURE.md (Mermaid), GIAI_THICH_MANG.md, TEST_REPORT.md
├── render.yaml            # Render Blueprint
├── run.py  wsgi.py  requirements.txt  .env.example  .gitignore
```

## 1. Chạy trên máy cá nhân

```bash
python -m venv .venv
# Windows:  .venv\Scripts\activate        macOS/Linux:  source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # Windows: copy .env.example .env  → sửa SECRET_KEY
python run.py                 # http://127.0.0.1:5000
```
Lần đầu với SQLite, bảng được tạo tự động. Mở 2 trình duyệt (hoặc 1 thường + 1 ẩn danh), đăng ký 2 tài khoản, tạo phòng ở tài khoản 1, tham gia bằng link/mã ở tài khoản 2.

**Thử trên điện thoại trong cùng Wi-Fi:** chạy `python run.py`, tìm IP máy tính (`ipconfig`/`ifconfig`), mở `http://<IP>:5000` trên điện thoại (cho phép Python qua tường lửa). Lưu ý: cookie `Secure` chỉ bật ở production nên HTTP nội bộ vẫn đăng nhập được.

**Dùng PostgreSQL ở local:** đặt `DATABASE_URL=postgresql://user:pass@localhost:5432/connecthub`, rồi `export FLASK_APP=wsgi.py && flask db upgrade`.

## 2. Kiểm thử

```bash
pip install -r requirements-dev.txt
cd tests && PYTHONPATH=.. python -m pytest -q          # 151 test tự động
# Kiểm thử trên server ĐANG CHẠY (WebSocket thật), dùng được cả với bản đã deploy:
python tests/live_check.py http://127.0.0.1:5000
python tests/live_check.py https://<ten-dich-vu>.onrender.com
# Smoke test giao diện (jsdom, chạy chat.js thật):
cd tests/ui && npm install && node ui_smoke.js http://127.0.0.1:5000
```
Kết quả đã chạy và những gì CHƯA kiểm thử (Neon thật, R2 thật, trình duyệt/điện thoại thật, Render thật): xem [docs/TEST_REPORT.md](docs/TEST_REPORT.md).

## 3. Triển khai miễn phí (Render Free + Neon + Cloudflare R2)

Hướng dẫn từng bước, hạn mức và các bẫy phát sinh phí: **[docs/DEPLOY_FREE.md](docs/DEPLOY_FREE.md)**. Tóm tắt:

| Thành phần | Dịch vụ | Biến môi trường |
|---|---|---|
| Backend Flask + WebSocket | Render Web Service **Free** | `APP_ENV=production`, `SECRET_KEY` |
| Database | Neon PostgreSQL Free | `DATABASE_URL`, `DIRECT_DATABASE_URL` (dùng cho migration) |
| File upload | Cloudflare R2 (S3 API) | `STORAGE_BACKEND=s3`, `S3_BUCKET`, `S3_ENDPOINT_URL`, `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY` |

`render.yaml` đã cấu hình gói Free, không dùng Persistent Disk và không dùng database của Render; các secret là biến `sync: false` nhập trong Dashboard, không nằm trong repo. Mẫu biến môi trường ở `.env.example`.

Giới hạn bạn phải chấp nhận khi dùng gói miễn phí (đối chiếu tài liệu chính thức ngày 31/10/2026, hãy kiểm tra lại):
- Render Free tự ngủ sau 15 phút không có traffic, thức dậy mất khoảng 1 phút, có 750 giờ chạy/tháng, ổ đĩa tạm thời (vì vậy file phải ở R2).
- Neon Free: 0,5 GB, 100 CU-giờ/tháng, compute tạm dừng sau 5 phút nhàn rỗi.
- R2: 10 GB, 1 triệu thao tác ghi, 10 triệu thao tác đọc mỗi tháng; vượt thì tính phí. Có thể cần phương thức thanh toán để bật R2 (chưa xác minh): hãy đặt cảnh báo chi phí.

### Vì sao 1 worker?
Danh sách kết nối Online, giới hạn tần suất (tin nhắn, upload, đăng nhập) nằm trong bộ nhớ tiến trình, và Render Free chỉ chạy 1 instance. Chạy 1 worker nhiều thread xử lý được nhiều kết nối đồng thời. Muốn mở rộng nhiều worker/instance cần Redis làm `message_queue` cho Flask-SocketIO, chuyển presence/rate-limit sang Redis và bật sticky session. Phần này **chưa được cài đặt/kiểm thử**.

## 4. GitHub và làm việc nhóm

```bash
git init && git add . && git commit -m "feat: khởi tạo ConnectHub"
git branch -M main
git remote add origin https://github.com/<tai-khoan>/connecthub.git
git push -u origin main
git checkout -b develop && git push -u origin develop
```
Quy trình: `main` (ổn định, deploy) ← Pull Request từ `develop` ← Pull Request từ `feature/<ten>`. Commit rõ ràng (`feat:`, `fix:`, `docs:`, `test:`). **Không commit** `.env`, database, thư mục `uploads/` (đã có trong `.gitignore`); kiểm tra bằng `git status` trước mỗi lần commit.

| Thành viên | Phụ trách | File chính |
|---|---|---|
| 1 | Backend, WebSocket | `app/sockets/`, `app/services/chat_service.py`, `app/routes/room.py` |
| 2 | Frontend | `app/templates/`, `app/static/` |
| 3 | Database, upload/download, kiểm thử, GitHub, deploy | `app/models/`, `app/services/file_service.py`, `storage.py`, `migrations/`, `tests/`, `render.yaml` |

## 5. Cấu hình chính (biến môi trường)
Xem `.env.example`. Quan trọng nhất: `SECRET_KEY`, `DATABASE_URL`, `STORAGE_BACKEND`/`UPLOAD_DIR`/`S3_*`, `CORS_ORIGINS`, `MAX_UPLOAD_MB`.

## 6. Bảo mật đã cài đặt
Hash mật khẩu (werkzeug) · CSRF cho mọi POST/PATCH/DELETE · cookie HttpOnly/SameSite, Secure ở production · kiểm tra quyền thành viên ở mọi API và sự kiện WebSocket · chống XSS (client dùng `textContent`, CSP chỉ cho script `'self'`) · kiểm tra chặt kiểu dữ liệu đầu vào (JSON sai/kiểu sai trả 400, không 500) · chống open redirect (`next`) kể cả `\`, tab/xuống dòng và mã hóa `%5C`/`%2F` · chống dò mật khẩu 3 lớp (theo IP+tài khoản, theo IP, theo tài khoản), không lộ tài khoản có tồn tại, chống spam đăng ký theo IP · upload: whitelist đuôi, kiểm tra MIME và nội dung thật, giới hạn dung lượng, **tối đa 5 file/60 giây/tài khoản (HTTP 429 + Retry-After)**, tên lưu ngẫu nhiên, không lộ đường dẫn · tải về luôn qua kiểm tra thành viên phòng (người không có quyền nhận 404 và storage không bị chạm tới), header `nosniff` + CSP `sandbox` · lỗi storage được phân loại: không tồn tại = 404, storage lỗi = 503 · đánh dấu đã đọc chỉ nhận ID tin nhắn thật và thuộc đúng phòng · race điều kiện (2 người cùng mở chat 1-1, 2 yêu cầu cùng tham gia phòng) được xử lý bằng ràng buộc UNIQUE + rollback · không lộ lỗi nội bộ · kiểm tra Origin cho WebSocket · mã mời ngẫu nhiên 72 bit.

## 7. Hạn chế hiện tại
- Presence, giới hạn tần suất, bộ đếm đăng nhập sai nằm trong RAM → chỉ đúng với 1 worker và mất khi restart. Giới hạn theo IP dựa vào `X-Forwarded-For` do proxy của Render đặt (ProxyFix); mạng dùng chung IP (trường học) chia sẻ chung bộ đếm theo IP.
- Giới hạn upload chỉ chặn được sau khi nhận request; Werkzeug đã giới hạn dung lượng thân yêu cầu (`MAX_UPLOAD_MB` + 1).
- Đăng ký vẫn báo "username/email đã tồn tại" (đề bài yêu cầu kiểm tra trùng), nên có thể dò xem một email đã đăng ký hay chưa; chỉ đăng nhập mới được chống lộ thông tin.
- Ảnh đại diện đọc nguyên file vào RAM (≤ 2 MB); mọi người dùng đã đăng nhập xem được ảnh đại diện của nhau.
- Chưa có quên mật khẩu/xác minh email, chưa quét virus, chưa thu nhỏ ảnh, chưa có "đang gõ".
- Giao diện chưa được thử trên trình duyệt thật/điện thoại thật (xem TEST_REPORT).
