# Triển khai miễn phí: Render Free + Neon PostgreSQL + Cloudflare R2

Mục tiêu chi phí 0đ. Hạn mức dưới đây đối chiếu với tài liệu chính thức vào 31/10/2026 (render.com/docs/free, neon.com/pricing, developers.cloudflare.com/r2/pricing). **Hãy mở lại các trang này trước khi triển khai vì chính sách có thể đổi.** Bài làm này chưa được triển khai thử thật lên ba dịch vụ trên.

## 0. Hạn mức và rủi ro phát sinh phí

| Dịch vụ | Hạn mức miễn phí | Điều cần chú ý |
|---|---|---|
| Render Web Service Free | 750 giờ chạy/tháng cho cả workspace; tự ngủ sau 15 phút không có traffic vào (HTTP hoặc tin WebSocket); thức dậy mất khoảng 1 phút; ổ đĩa tạm thời; không có Persistent Disk | Chỉ chạy 1 instance. Render có thể restart bất cứ lúc nào và có thể tạm ngưng Free service tạo lưu lượng ra ngoài bất thường. Không bấm nâng cấp instance |
| Neon Free | 100 CU-giờ/tháng và 0,5 GB lưu trữ cho mỗi project; compute tự tạm dừng sau 5 phút nhàn rỗi (không tắt được), đánh thức trong khoảng vài trăm ms | Hết CU-giờ thì compute bị tạm dừng đến tháng sau (dữ liệu không mất). Đừng để thứ gì gọi vào database liên tục |
| Cloudflare R2 | 10 GB-tháng, 1 triệu thao tác Class A (ghi/liệt kê), 10 triệu thao tác Class B (đọc), egress miễn phí | Vượt hạn mức sẽ bị tính phí theo bảng giá. Cloudflare có thể yêu cầu phương thức thanh toán khi bật R2 (mình chưa xác minh); nếu có, hãy đặt cảnh báo chi phí/billing notification và theo dõi Usage |

Thiết kế đã tránh các cái bẫy phí: `/healthz` không chạm database; file được lưu ở R2 chứ không ở đĩa Render; giới hạn upload 10 MB/file, 5 file/60 giây/tài khoản; không dùng dịch vụ trả phí nào của Render.

Hệ quả cần chấp nhận khi dùng gói Free:
- Lần truy cập đầu sau khi ngủ sẽ chậm khoảng 1 phút. Khi service ngủ, mọi kết nối WebSocket bị ngắt; giao diện tự kết nối lại và đồng bộ tin nhắn bị lỡ.
- Trạng thái Online/Offline, bộ đếm giới hạn tần suất nằm trong RAM nên mất khi restart (không mất tin nhắn hay file).
- Trước buổi bảo vệ hãy mở trang trước 2 phút để "đánh thức" service.

## 1. Neon PostgreSQL

1. Tạo tài khoản tại neon.com → **Create project** (chọn region gần Render, ví dụ AWS Singapore). Chọn gói Free, không nâng cấp.
2. Vào **Connect** trên Project Dashboard. Có hai chuỗi kết nối:
   - **Direct** (hostname không có `-pooler`) → đặt vào `DIRECT_DATABASE_URL`. Dùng cho migration. Neon khuyến nghị không chạy migration qua pooler.
   - **Pooled** (hostname có `-pooler`) → đặt vào `DATABASE_URL` cho ứng dụng. (Nếu thấy phức tạp, có thể đặt cùng chuỗi Direct vào cả hai biến; ứng dụng chỉ dùng tối đa 10 kết nối.)
3. Giữ nguyên `?sslmode=require` trong chuỗi. Không commit chuỗi này lên GitHub.

## 2. Cloudflare R2

1. Đăng nhập dash.cloudflare.com → **R2 Object Storage** → tạo bucket (ví dụ `connecthub-files`). **Giữ bucket ở chế độ private**; không bật public access hay custom domain: mọi lượt tải đi qua ứng dụng để kiểm tra quyền.
2. **Manage R2 API Tokens → Create API token**: quyền **Object Read & Write**, chỉ áp dụng cho đúng bucket vừa tạo. Lưu `Access Key ID` và `Secret Access Key` (chỉ hiện một lần).
3. Endpoint S3: `https://<ACCOUNT_ID>.r2.cloudflarestorage.com` (Account ID ở trang tổng quan R2). Region luôn là `auto`.
4. Đặt biến: `S3_BUCKET`, `S3_ENDPOINT_URL`, `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY`, `S3_REGION=auto`.

Ghi chú kỹ thuật: boto3 bản mới mặc định thêm checksum mà R2 có thể từ chối; code đã đặt `request_checksum_calculation` và `response_checksum_validation` ở chế độ `when_required` (theo hướng dẫn của Cloudflare).

## 3. Render

1. Đẩy code lên GitHub (không có `.env`, database, `uploads/`).
2. Render Dashboard → **New → Blueprint** → chọn repo → Render đọc `render.yaml` (`plan: free`, không có `disk`, không có `databases`).
3. Render hỏi giá trị các biến `sync: false`: `DATABASE_URL`, `DIRECT_DATABASE_URL`, `S3_BUCKET`, `S3_ENDPOINT_URL`, `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY`. Dán giá trị từ bước 1 và 2. `SECRET_KEY` được Render tự sinh.
4. Bấm Apply. Build chạy `pip install`, khi khởi động chạy `flask db upgrade` (qua kết nối Direct) rồi mở gunicorn. Nếu Neon đang ngủ, lần khởi động đầu có thể chậm vài giây.
5. Kiểm tra: mở `https://<tên>.onrender.com/healthz` → `ok`. Chạy `python tests/live_check.py https://<tên>.onrender.com` (tạo vài tài khoản `livechk...` và một vài file thử trên R2; xóa đi sau khi thử).
6. Cập nhật phiên bản: `git push origin main` → Render tự deploy.

Nếu tạo dịch vụ thủ công (không dùng Blueprint): chọn instance type **Free**, Build `pip install -r requirements.txt`, Start `DATABASE_URL="${DIRECT_DATABASE_URL:-$DATABASE_URL}" flask db upgrade && gunicorn --worker-class gthread --threads 100 --workers 1 --timeout 120 --bind 0.0.0.0:$PORT wsgi:app`, Health Check Path `/healthz`, rồi thêm các biến môi trường như trên (cộng `APP_ENV=production`, `FLASK_APP=wsgi.py`, `STORAGE_BACKEND=s3`, `PYTHON_VERSION=3.12.3`).

## 4. Xử lý sự cố thường gặp

| Triệu chứng | Nguyên nhân thường gặp |
|---|---|
| Upload/tải file báo "Kho lưu trữ tạm thời không khả dụng" (HTTP 503) | Sai khóa/endpoint R2, bucket không tồn tại, token không có quyền trên bucket, hoặc vượt hạn mức. Xem log Render để biết chi tiết |
| Dịch vụ không khởi động, log báo "Thiếu cấu hình storage S3/R2" | Thiếu biến `S3_*` |
| Lỗi `SECRET_KEY` khi khởi động | Thiếu `SECRET_KEY` (Blueprint tự sinh; tạo thủ công thì phải đặt) |
| Migration lỗi khi khởi động | `DIRECT_DATABASE_URL`/`DATABASE_URL` sai, hoặc dùng chuỗi pooled cho migration |
| Đăng nhập được nhưng WebSocket không nối | `CORS_ORIGINS` đặt sai domain (để trống thì dùng `RENDER_EXTERNAL_URL`) |
| Đột nhiên chậm | Free service vừa ngủ dậy (~1 phút) hoặc Neon vừa được đánh thức |
