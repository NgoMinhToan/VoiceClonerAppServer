# VoiceClonerAppServer

API Gateway, Reverse Proxy & Quản trị Hệ thống Voice Cloner (Tích hợp Dynamic Voice Discovery, Capability-Aware Least-Active Load Balancer, User Authentication JWT, và Bảng điều khiển lấy cảm hứng từ [CLIProxyAPI](https://github.com/router-for-me/CLIProxyAPI)).

---

## 1. Tổng Quan Kiến Trúc Độc Lập

`VoiceClonerAppServer` được thiết kế hoàn toàn tách rời, không gắn cứng với bất kỳ model AI hay nhà cung cấp giọng đọc cụ thể nào:
- **Chấp nhận mọi Voice Server tuân thủ chuẩn Voice API**: Gateway có thể kết nối tới [vienueTTS_server](https://github.com/NgoMinhToan/vienueTTS_server) hoặc bất kỳ cụm máy chủ giọng nói AI nào khác có cùng chuẩn API.
- **Dynamic Voice Discovery**: Khi khởi động hoặc khi cấu hình worker thay đổi, Gateway tự động gửi request `GET /internal/v1/voices` tới toàn bộ các worker đang online, tự động phân tích và gộp danh mục giọng đọc mà không cần khai báo danh sách giọng thủ công trong code hay file cấu hình.
- **Capability / Voice-Aware Least-Active Routing**: Thuật toán cân bằng tải thông minh không chỉ đếm số request đang xử lý (`active_jobs`) mà trước tiên lọc ra danh sách các worker **có hỗ trợ đúng `voice_id` được yêu cầu** (hoặc hỗ trợ clone). Sau đó, nó mới chọn worker ít việc nhất trong nhóm thỏa mãn năng lực (kèm cơ chế xoay vòng round-robin khi hòa điểm).
- **Hệ thống xác thực người dùng đầy đủ (`/v1/auth/*`)**: Cung cấp API đăng ký, đăng nhập email/mật khẩu, đăng nhập Google OAuth2, cấp mới JWT access/refresh token, và hồ sơ người dùng (`/v1/auth/me`).
- **Quản lý giọng nhân bản (Instant Voice Cloning `/v1/voices/clone`)**: Cho phép người dùng thu âm hoặc tải file mẫu để nhân bản giọng đọc, kiểm soát nghiêm ngặt hạn mức 3 slot clone/người dùng, lưu trữ tệp `.wav` và `.npz` speaker embedding.
- **Tập trung cấu hình trong `config/settings.json`**: Mọi cài đặt cổng mạng, danh sách workers, giới hạn luồng (concurrency), bộ nhớ đệm âm thanh và danh sách Client API Keys đều được lưu trong file JSON và áp dụng ngay lập tức (Hot-Reload) mà không cần khởi động lại server.
- **Giao diện quản trị hiện đại (KEEPER Dark Theme Console)**: Bảng điều khiển lấy cảm hứng từ CLIProxyAPI / KEEPER (`/dashboard`), tích hợp 6 tab chức năng tinh gọn (`Overview`, `Realtime`, `Request Events`, `Voice Workers`, `API Keys`, `Settings`), hỗ trợ rà chuột xem chỉ số chi tiết trên toàn bộ biểu đồ, sparklines và ma trận hoạt động (Activity Heatmaps & Request Health Timeline).


---

## 2. Mô Hình Bảo Mật Kết Hợp (Client API Key + User Bearer Token + Management Key)

Hệ thống kết hợp hài hòa giữa định danh ứng dụng và định danh người dùng:

| Tầng Xác Thực | Vị trí cấu hình | Header truyền tải | Mục đích sử dụng |
| :--- | :--- | :--- | :--- |
| **1. Client API Key** | `settings.json` -> `client_api_keys` | `X-API-Key` | Định danh nền tảng ứng dụng (Android Mobile App, Web Client). Tiền tố `vc_live_...`. Dùng để ghi log `api_usage_logs` và phân tích lưu lượng. |
| **2. User Bearer Token (JWT)** | Cấp động qua `/v1/auth/login` | `Authorization: Bearer <token>` | Xác thực danh tính người dùng cụ thể. Bắt buộc cho việc quản lý slot clone riêng tư (`/v1/voices/clone`), đọc hồ sơ cá nhân (`/v1/auth/me`), và tính hạn ngạch ký tự hàng ngày. |
| **3. Management Key** (Admin Key) | `settings.json` -> `security.management_key` | `X-Management-Key` | Cấp quyền quản trị tối cao cho Web Dashboard (`/dashboard`) và các API quản trị `/v1/management/*`. |
| **4. Upstream Worker Key** | `settings.json` -> `model_workers[].api_key` | `X-API-Key` | Gateway tự động gửi kèm khi gọi sang các node worker inference. |

---

## 3. Biến Môi Trường (Environment Variables)

| Biến | Mặc định | Mô tả |
| :--- | :--- | :--- |
| `DATABASE_URL` | `postgresql+asyncpg://voice_user:voice_pass@db:5432/voicecloner` | URL kết nối PostgreSQL chính (tự động fallback SQLite nếu lỗi) |
| `REDIS_URL` | `redis://redis:6379/0` | URL kết nối Redis cho quota và blacklist token (tự động fallback in-memory) |
| `CONFIG_PATH` | `/app/VoiceClonerAppServer/config/settings.json` | Đường dẫn file cấu hình động `settings.json` |
| `JWT_SECRET_KEY` | `dev_secret_key_...` | Khóa bí mật ký JWT tokens |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | `15` | Thời gian sống của Access Token (phút) |
| `REFRESH_TOKEN_EXPIRE_DAYS` | `7` | Thời gian sống của Refresh Token (ngày) |
| `GOOGLE_CLIENT_ID` | `""` | Google Web Client ID cho tính năng Google Sign-In |
| `BUSINESS_TIMEZONE` | `Asia/Ho_Chi_Minh` | Múi giờ đặt lại hạn ngạch ký tự hàng ngày (00:00 nửa đêm) |

---

## 4. Danh Sách API Endpoints Chi Tiết

### 4.1 Xác Thực & Tài Khoản Người Dùng (`/v1/auth`)
- `POST /v1/auth/register`: Đăng ký tài khoản (body: `{email, password, full_name}`).
- `POST /v1/auth/login`: Đăng nhập lấy access/refresh token (body: `{email, password}`).
- `POST /v1/auth/google`: Đăng nhập Google ID token (body: `{id_token}`).
- `POST /v1/auth/refresh`: Cấp mới token (body: `{refresh_token}`).
- `POST /v1/auth/logout`: Đăng xuất và đưa token vào Redis blacklist.
- `GET /v1/auth/me`: Lấy thông tin tài khoản, số ký tự đã dùng trong ngày, số slot clone.

### 4.2 Thư Viện Giọng Nói & Nhân Bản Giọng (`/v1/voices`)
- `GET /v1/voices`: Lấy danh mục giọng preset động được tổng hợp từ các upstream workers + các giọng clone riêng của user đăng nhập.
- `GET /v1/voices/{voice_id}`: Chi tiết profile của một giọng nói (preset hoặc clone).
- `GET /v1/voices/{voice_id}/audio`: Tải file âm thanh mẫu của giọng clone (bảo vệ IDOR).
- `POST /v1/voices/clone`: Upload file âm thanh mẫu (3-15s) để nhân bản giọng đọc mới (tối đa 3 slots).
- `PUT /v1/voices/{voice_id}`: Cập nhật tên, phong cách, cài đặt của giọng clone.
- `DELETE /v1/voices/{voice_id}`: Xóa giọng clone và giải phóng slot quota.

### 4.3 Tổng Hợp Âm Thanh (`/v1/audio`) & Nghe Thử
- `POST /v1/audio/speech`: Tổng hợp văn bản thành 48kHz WAV audio (kiểm tra giới hạn độ dài `max_speech_char_limit`, kiểm tra hạn mức còn lại, kiểm tra quota ký tự, lọc worker theo voice capability, điều phối Least-Active).
- `POST /v1/voices/preview`: Nghe thử nhanh mẫu giọng (hỗ trợ Audio Cache, giới hạn độ dài `max_preview_char_limit`, kiểm tra hạn mức còn lại, kiểm tra và trừ quota ký tự hàng ngày).

### 4.4 Trạng Thái Hệ Thống (`/v1/system`)
- `GET /health`: Probe kiểm tra container alive.
- `GET /v1/system/status`: Báo cáo phần cứng, backend, số lượng worker phục vụ Android Client.

### 4.5 Quản Trị Hệ Thống (`/v1/management` & `/dashboard`)
- `GET /dashboard`: Giao diện điều khiển Web CLIProxyAPI.
- `GET/PUT /v1/management/settings`: Đọc và cập nhật cấu hình hệ thống (Hot-Reload).
- `GET/POST/DELETE/PATCH /v1/management/keys`: Quản lý danh sách Client API Key.
- `GET/POST /v1/management/workers`: Danh sách và ping test năng lực các worker inference.
- `GET/PATCH/DELETE /v1/management/users`: Quản trị tài khoản người dùng và điều chỉnh slot clone.

---

## 5. Hướng Dẫn Khởi Chạy

### Chạy bằng Docker Compose:
```bash
docker compose up -d --build api
```

### Chạy trực tiếp bằng Python:
```bash
pip install -r requirements.txt
python main.py
```
- Dashboard Console: `http://localhost:8000/dashboard`
- Swagger UI Docs: `http://localhost:8000/docs`

---

## 6. Kiểm Thử Hệ Thống

Chạy bộ kiểm thử tự động pytest:
```bash
pytest tests/ -v
```
Toàn bộ 47 test cases kiểm tra capability load balancing, dynamic voice discovery, auth lifecycle, token blacklist, hot-reload settings on disk change, strict 3-tier client key verification across all endpoints, mandatory user auth on speech/preview/clone, audio cache hit/miss, speech & preview character limits, upfront remaining quota enforcement, user management (slots & real-time TTS chars) và dashboard delivery.

### Kích Hoạt Git Pre-Push Hook Cục Bộ:
Để git tự động chạy toàn bộ test và ngăn chặn lệnh push nếu có lỗi:
```bash
git config core.hooksPath .githooks
```
