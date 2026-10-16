# Kế Hoạch Chuẩn Hóa & Kiểm Thử VoiceClonerAppServer

**Mục tiêu:** Xây dựng `VoiceClonerAppServer` thành một API Gateway, Reverse Proxy và Quản trị tập trung chuẩn mực:
1. Đảm bảo hỗ trợ 100% các hợp đồng API với ứng dụng Android Native Client và Web Client (`/v1/auth/*`, `/v1/voices/*`, `/v1/audio/speech`, `/v1/system/status`).
2. Tích hợp mô hình bảo mật 3-tier API Key kết hợp User Bearer Token (JWT).
3. Hỗ trợ điều phối (Reverse Proxy/Load Balancer) tới cụm worker `vienueTTS_server` theo giải thuật Least-Active.
4. Quản lý hạn mức ký tự hàng ngày (Daily Character Quota) và số lượng slot clone (tối đa 3 slots/user).
5. Bảng điều khiển quản trị thời gian thực phong cách CLIProxyAPI (`/dashboard`).

---

## 1. So Sánh Với Baseline Gốc (`baseline-stable`) & Khắc Phục
- **Vấn đề phát hiện:** Khi chuyển đổi sang microservices và mô hình CLIProxyAPI, toàn bộ hệ thống xác thực người dùng `/v1/auth/*` và chức năng nhân bản giọng đọc người dùng `/v1/voices/clone` đã bị thiếu sót.
- **Hành động khắc phục:**
  - Khôi phục trọn vẹn router `api/auth.py`: `/register`, `/login`, `/google`, `/refresh`, `/logout`, `/me`.
  - Khôi phục trọn vẹn router `api/voices.py`: `/v1/voices` (kết hợp 23 giọng preset + giọng clone của user), `/v1/voices/{id}`, `/v1/voices/{id}/audio`, `/v1/voices/clone`, `PUT`, `DELETE`.
  - Bổ sung trích xuất đặc trưng giọng clone qua phương thức `model_dispatcher.extract_embedding` (chuyển tiếp tới `vienueTTS_server /embed`).
  - Cập nhật `api/speech.py` để hỗ trợ cả Client API Key và User JWT Auth, hỗ trợ nạp `.npz` tự động cho giọng clone và gửi `ref_npz_base64` sang Voice Server.
  - Cập nhật `api/system.py` trả về schema `SystemStatusResponse` chuẩn cho ứng dụng di động Android.

---

## 2. Danh Sách API Contract

### Nhóm Xác Thực Người Dùng (`/v1/auth`)
| Phương thức | Endpoint | Mô tả |
| :--- | :--- | :--- |
| `POST` | `/v1/auth/register` | Đăng ký tài khoản mới (email, password, full_name) |
| `POST` | `/v1/auth/login` | Đăng nhập nhận `access_token` và `refresh_token` |
| `POST` | `/v1/auth/google` | Đăng nhập bằng Google ID Token |
| `POST` | `/v1/auth/refresh` | Cấp mới `access_token` từ `refresh_token` |
| `POST` | `/v1/auth/logout` | Đăng xuất và đưa token vào danh sách đen (Redis blacklist) |
| `GET` | `/v1/auth/me` | Lấy thông tin user, số slot clone đã dùng, số ký tự đã dùng trong ngày |

### Nhóm Giọng Nói (`/v1/voices`)
| Phương thức | Endpoint | Mô tả |
| :--- | :--- | :--- |
| `GET` | `/v1/voices` | Lấy 23 giọng preset + danh sách giọng clone của user đang đăng nhập |
| `GET` | `/v1/voices/{voice_id}` | Chi tiết profile của một giọng nói |
| `GET` | `/v1/voices/{voice_id}/audio` | Tải file âm thanh mẫu của giọng clone (IDOR protected) |
| `POST` | `/v1/voices/clone` | Upload file âm thanh mẫu (3-15s), trích xuất `.npz` và lưu DB (tối đa 3 slots) |
| `PUT` | `/v1/voices/{voice_id}` | Cập nhật cấu hình giọng clone |
| `DELETE` | `/v1/voices/{voice_id}` | Xóa giọng clone và giải phóng slot quota |

### Nhóm Tổng Hợp & Nghe Thử (`/v1/audio`, `/v1/voices/preview`)
| Phương thức | Endpoint | Mô tả |
| :--- | :--- | :--- |
| `POST` | `/v1/audio/speech` | Tổng hợp âm thanh 48kHz WAV từ văn bản (kiểm tra quota ký tự hàng ngày, rate limit, điều phối tới worker) |
| `POST` | `/v1/voices/preview` | Kết xuất mẫu nghe thử nhanh |

### Nhóm Quản Trị Hệ Thống (`/v1/management`, `/dashboard`)
| Phương thức | Endpoint | Mô tả |
| :--- | :--- | :--- |
| `GET` | `/dashboard` | Giao diện điều khiển Web CLIProxyAPI (Metrics, Keys, Workers, Users, Settings) |
| `GET/POST/DELETE/PATCH` | `/v1/management/keys` | Quản lý Client API Keys |
| `GET/POST` | `/v1/management/workers` | Quản lý và kiểm tra kết nối (ping test) Model Worker Nodes |
| `GET/PUT` | `/v1/management/settings` | Đọc và lưu cấu hình hệ thống với khả năng Hot-Reload không cần restart server |
| `GET/PATCH/DELETE` | `/v1/management/users` | Quản trị tài khoản người dùng và điều chỉnh slot clone |

---

## 3. Kế Hoạch Kiểm Thử
- [x] Test bộ định tuyến người dùng (`/v1/auth/register`, `/v1/auth/login`, `/v1/auth/refresh`, `/v1/auth/me`, `/v1/auth/logout`).
- [x] Test danh mục giọng nói kết hợp 23 preset và giọng clone của user (`/v1/voices`).
- [x] Test nhân bản giọng nói `/v1/voices/clone` và kiểm soát hạn mức 3 slot.
- [x] Test tổng hợp âm thanh `/v1/audio/speech` với quota ký tự hàng ngày.
- [x] Test trạng thái hệ thống `/v1/system/status` khớp với Android client model.
- [x] Test quản lý Client API Key và Hot-reload settings.
- [x] Test giao diện Dashboard và static assets.
