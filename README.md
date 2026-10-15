# VoiceClonerAppServer

API Gateway, Reverse Proxy & Quản trị Hệ thống Voice Cloner (Lấy cảm hứng từ kiến trúc [CLIProxyAPI](https://github.com/router-for-me/CLIProxyAPI)).

---

## 1. Tổng Quan Kiến Trúc

`VoiceClonerAppServer` đóng vai trò là gateway trung gian điều phối toàn bộ luồng tổng hợp âm thanh, phân quyền và giám sát:
- **Proxy điều phối tải**: Chuyển tiếp các yêu cầu tổng hợp âm thanh (TTS) đến các cụm worker inference [vienueTTS_server](https://github.com/NgoMinhToan/vienueTTS_server) bằng thuật toán Least-Active.
- **Tập trung cấu hình trong `config/settings.json`**: Mọi cài đặt cổng mạng, danh sách workers, giới hạn luồng (concurrency), bộ nhớ đệm âm thanh và danh sách Client API Keys đều được lưu trong file JSON và áp dụng ngay lập tức (Hot-Reload) mà không cần khởi động lại server.
- **Giao diện quản trị hiện đại (Dark Theme)**: Bảng điều khiển lấy cảm hứng từ CLIProxyAPI tích hợp biểu đồ Chart.js thống kê lưu lượng thời gian thực.
- **Tách biệt kết nối hạ tầng**: Cấu hình cơ sở dữ liệu (PostgreSQL) và Redis nằm trong biến môi trường `.env`.

---

## 2. Mô Hình Bảo Mật 3 Tầng API Key

Để tránh nhầm lẫn giữa các loại khóa trong hệ thống, `VoiceClonerAppServer` phân định rạch ròi 3 tầng API Key:

| Tầng API Key | Vị trí cấu hình | Header truyền tải | Mục đích sử dụng |
| :--- | :--- | :--- | :--- |
| **1. Management API Key** (Admin Key) | `settings.json` -> `security.management_key` | `X-Management-Key` | Truy cập Web Dashboard (`/dashboard`) và các API quản trị `/v1/management/*`. |
| **2. Client API Keys** (App / Web Key) | `settings.json` -> `client_api_keys` | `X-API-Key` | Định danh client (ứng dụng Android, Web Demo). Tiền tố `vc_live_...`. Dùng để kiểm soát hạn ngạch ký tự và vẽ biểu đồ thống kê. |
| **3. Model Worker API Keys** (Upstream Key) | `settings.json` -> `model_workers[].api_key` | `X-API-Key` | Gateway tự động gửi kèm khi gọi sang các node worker [vienueTTS_server](https://github.com/NgoMinhToan/vienueTTS_server). |

---

## 3. Cấu Trúc File `config/settings.json`

```json
{
  "server": {
    "host": "0.0.0.0",
    "port": 8000
  },
  "security": {
    "management_key": "vc_admin_secret_key_123",
    "require_client_api_key": true
  },
  "client_api_keys": [
    {
      "id": "key_android_app_default",
      "name": "Android Mobile App (Production)",
      "key": "vc_live_a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5p6",
      "is_active": true,
      "daily_char_limit": 50000,
      "created_at": "2026-10-15T00:00:00Z"
    }
  ],
  "model_workers": [
    {
      "id": "worker-primary",
      "url": "http://model-service:8001",
      "api_key": "vtts_worker_secret_key_123",
      "weight": 1,
      "is_active": true
    }
  ],
  "concurrency": {
    "max_concurrent_tts": 2,
    "max_queue_waiters": 8,
    "daily_tts_char_limit": 100000,
    "rate_limit_requests_per_minute": 60
  },
  "audio_cache": {
    "max_size_mb": 2048,
    "ttl_days": 7,
    "clean_interval_sec": 3600
  }
}
```

---

## 4. Hướng Dẫn Triển Khai & Khởi Chạy

### 4.1 Khởi chạy với Docker Compose (Khuyên dùng)
```bash
# 1. Sao chép biến môi trường
cp .env.example .env

# 2. Khởi chạy toàn bộ dịch vụ (PostgreSQL, Redis, AppServer)
docker compose up -d --build

# 3. Xem nhật ký hoạt động
docker compose logs -f appserver
```

### 4.2 Khởi chạy trực tiếp bằng Python
```bash
# 1. Cài đặt thư viện phụ thuộc
pip install -r requirements.txt

# 2. Khởi chạy server
python main.py
```
- Web Dashboard: `http://localhost:8000/dashboard`
- Tài liệu Swagger: `http://localhost:8000/docs`

---

## 5. Tài Liệu API Chính

### 5.1 Dành cho Client Ứng Dụng (Yêu cầu `X-API-Key`)
- `POST /v1/audio/speech`: Tổng hợp văn bản thành âm thanh 48kHz WAV tiêu chuẩn.
- `GET /v1/voices`: Lấy danh mục giọng đọc có sẵn.
- `POST /v1/voices/preview`: Nghe thử giọng đọc mẫu.

### 5.2 Dành cho Quản Trị (Yêu cầu `X-Management-Key`)
- `GET /v1/management/settings`: Đọc toàn bộ cấu hình hệ thống.
- `PUT /v1/management/settings`: Cập nhật cấu hình và Hot-Reload tức thì.
- `GET /v1/management/keys`: Liệt kê danh sách Client API Key.
- `POST /v1/management/keys`: Tạo mới một Client API Key.
- `DELETE /v1/management/keys/{key_id}`: Thu hồi Client API Key.
- `GET /v1/management/workers`: Trạng thái và độ trễ ping các worker inference.
- `GET /v1/management/metrics/summary`: Thống kê số lượng request, ký tự và biểu đồ.

---

## 6. Màn Hình Ứng Dụng Client (Android & Dashboard)

### Giao diện Quản Trị (CLIProxyAPI Style Web Dashboard)
Trang quản trị cho phép theo dõi biểu đồ thống kê lưu lượng theo từng Client API Key và tỷ trọng của từng Model Worker, quản lý danh sách khóa và thay đổi cấu hình trực tiếp từ trình duyệt:
```
+--------------------------------------------------------------------------+
|  [Voice Cloner Gateway]                     Management Key: [******]     |
|--------------------------------------------------------------------------|
|  [1. Metrics]    |  Total Requests: 1,420     Total Chars: 85,200        |
|  [2. API Keys]   |  [=== Line Chart: Requests by Client Key ===]         |
|  [3. Workers]    |  [=== Doughnut: Model Worker Distribution ===]        |
|  [4. Settings]   |  Recent Request Logs Table...                         |
+--------------------------------------------------------------------------+
```

### Màn Hình Android Client (`app/`)
Ứng dụng Android kết nối trực tiếp đến `VoiceClonerAppServer` thông qua Client API Key (`vc_live_...`):
- **Home Screen**: Giao diện nhận diện giọng nói, chọn giọng đọc Hero Card và phát tức thì.
- **Library Screen**: Danh mục 23 giọng đọc VieNeu-TTS v3 với bộ lọc ngôn ngữ, giới tính và nghe thử 3s.
- **Record Sample Screen**: Phòng thu âm chuẩn studio với thước đo âm lượng VU dB meter phục vụ huấn luyện Voice Clone.
- **Voice Settings Screen**: Tinh chỉnh cao độ, tốc độ, cảm xúc và thuật toán khử nhiễu.

---

## 7. Liên Kết Các Dự Án Liên Quan

- **[vienueTTS_server](https://github.com/NgoMinhToan/vienueTTS_server)**: Microservice AI xử lý inference VieNeu-TTS.
- **[VoiceClonerWebDemo](https://github.com/NgoMinhToan/VoiceClonerWebDemo)**: Giao diện Web tĩnh thử nghiệm độc lập trên GitHub Pages.
- **Android Client**: Thư mục `app/` tại repository chính [VoiceCloner](https://github.com/NgoMinhToan/voice-cloner-app).
