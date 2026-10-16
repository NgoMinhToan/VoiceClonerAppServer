# Kế Hoạch Chuẩn Hóa & Kiểm Thử VoiceClonerAppServer

**Mục tiêu:** Xây dựng `VoiceClonerAppServer` thành một API Gateway, Reverse Proxy và Quản trị tập trung độc lập hoàn toàn với bất kỳ model hay voice server cụ thể nào:
1. Đảm bảo hỗ trợ 100% các hợp đồng API với ứng dụng Android Native Client và Web Client (`/v1/auth/*`, `/v1/voices/*`, `/v1/audio/speech`, `/v1/system/status`).
2. **Dynamic Voice Discovery:** Tự động khám phá danh mục giọng từ các worker voice server thông qua `GET /internal/v1/voices`, tổng hợp danh mục động thay vì hardcode danh sách giọng tĩnh.
3. **Capability / Voice-Aware Least-Active Routing:** Giải thuật điều phối lọc chính xác các worker có hỗ trợ `voice_id` được yêu cầu (hoặc hỗ trợ clone) trước khi tính toán worker ít việc nhất (Least-Active) kèm round-robin tiebreaker.
4. Tích hợp mô hình bảo mật kết hợp: 3-tier API Key + User Bearer Token (JWT).
5. Quản lý hạn mức ký tự hàng ngày (Daily Character Quota) và số lượng slot clone (tối đa 3 slots/user).
6. Bảng điều khiển quản trị thời gian thực phong cách CLIProxyAPI (`/dashboard`).

---

## 1. Kiến Trúc Khám Phá Giọng Động & Điều Phối Nhận Biết Năng Lực (Capability-Aware)

```
                            ┌────────────────────────────────────────┐
                            │    Android App / Web Studio Client     │
                            └───────────────────┬────────────────────┘
                                                │ HTTP / REST
                                                ▼
┌────────────────────────────────────────────────────────────────────────────────────────┐
│ VoiceClonerAppServer (Gateway & Orchestrator - Port 8000)                              │
│                                                                                        │
│  ┌─────────────────────────────┐         ┌──────────────────────────────────────────┐  │
│  │ VoiceService                │         │ ModelDispatcher                          │  │
│  │ - Discovered Catalog Map    │◄───────►│ - Worker Capability Registry             │  │
│  │ - Cold-boot Fallback Catalog│         │   (supported_voices, supports_cloning)   │  │
│  │ - Deduplicated Aggregator   │         │ - Capability Filtering (by voice_id)     │  │
│  │ - Periodic Discovery Loop   │         │ - Least-Active + Round-Robin Tiebreaker  │  │
│  └──────────────┬──────────────┘         └────────────────────┬─────────────────────┘  │
└─────────────────┼─────────────────────────────────────────────┼────────────────────────┘
                  │ GET /internal/v1/voices                     │ POST /internal/v1/synthesize
                  ▼                                             ▼
┌──────────────────────────────────────┐     ┌────────────────────────────────────────┐
│ Upstream Voice Worker A (Port 8001)  │     │ Upstream Voice Worker B (Port 8002)    │
│ - Supported voices: [v1, v2, ...]    │     │ - Supported voices: [v2, v3, ...]      │
│ - Supports cloning: Yes              │     │ - Supports cloning: Yes/No             │
└──────────────────────────────────────┘     └────────────────────────────────────────┘
```

### Nguyên lý hoạt động:
1. **Dynamic Voice Discovery (`discover_all_workers`)**:
   - Khi khởi động server hoặc khi danh sách worker thay đổi trong `settings.json`, Gateway tự động gửi request `GET /internal/v1/voices` tới tất cả các worker đang kích hoạt (`is_active=True`).
   - Tổng hợp và loại bỏ trùng lặp danh sách giọng, lưu trữ vào bộ nhớ đệm `_discovered_presets`.
   - Giữ lại một danh mục `FALLBACK_PRESET_VOICES` ban đầu chỉ dành cho trường hợp khởi động nguội (cold boot) khi chưa có worker nào kịp phản hồi.
2. **Capability / Voice-Aware Least-Active Load Balancer**:
   - Khi client gọi `POST /v1/audio/speech` với `voice="quynh_anh"`:
     - Gateway lọc danh sách các worker đang online và mạch `closed`/`half-open`.
     - Lọc tiếp các worker có khai báo hỗ trợ giọng `quynh_anh` trong `supported_voices`.
     - Trong số các worker thỏa mãn năng lực trên, chọn worker có số lượng `active_jobs` thấp nhất. Nếu có nhiều worker hòa điểm, áp dụng xoay vòng round-robin.
   - Khi client gọi nhân bản giọng (`/v1/voices/clone` hoặc tổng hợp với `ref_npz_base64`):
     - Lọc các worker có cờ `supports_cloning=True`.

---

## 2. Danh Sách API Contract Chuẩn

### 2.1 Nhóm Xác Thực Người Dùng (`/v1/auth`)
| Phương thức | Endpoint | Header / Auth | Mô tả |
| :--- | :--- | :--- | :--- |
| `POST` | `/v1/auth/register` | Mở / Client Key | Đăng ký tài khoản (email, password, full_name) |
| `POST` | `/v1/auth/login` | Mở / Client Key | Đăng nhập nhận `access_token` và `refresh_token` |
| `POST` | `/v1/auth/google` | Mở / Client Key | Đăng nhập bằng Google ID Token |
| `POST` | `/v1/auth/refresh` | Mở / Client Key | Cấp mới `access_token` từ `refresh_token` |
| `POST` | `/v1/auth/logout` | `Bearer <JWT>` | Đăng xuất và đưa token vào danh sách đen (Redis) |
| `GET` | `/v1/auth/me` | `Bearer <JWT>` | Lấy thông tin user, số slot clone đã dùng, ký tự đã dùng hôm nay |

### 2.2 Nhóm Giọng Nói (`/v1/voices`)
| Phương thức | Endpoint | Header / Auth | Mô tả |
| :--- | :--- | :--- | :--- |
| `GET` | `/v1/voices` | Optional Bearer | Lấy toàn bộ giọng preset động được tổng hợp từ các workers + giọng clone của user |
| `GET` | `/v1/voices/{voice_id}` | Optional Bearer | Chi tiết profile của một giọng nói (preset hoặc clone) |
| `GET` | `/v1/voices/{voice_id}/audio` | `Bearer <JWT>` | Tải file âm thanh mẫu của giọng clone (IDOR protected) |
| `POST` | `/v1/voices/clone` | `Bearer <JWT>` | Upload file audio mẫu, điều phối trích xuất `.npz` và lưu DB (tối đa 3 slots) |
| `PUT` | `/v1/voices/{voice_id}` | `Bearer <JWT>` | Cập nhật cấu hình giọng clone |
| `DELETE` | `/v1/voices/{voice_id}` | `Bearer <JWT>` | Xóa giọng clone và giải phóng slot quota |

### 2.3 Nhóm Tổng Hợp & Nghe Thử (`/v1/audio`, `/v1/voices/preview`)
| Phương thức | Endpoint | Header / Auth | Mô tả |
| :--- | :--- | :--- | :--- |
| `POST` | `/v1/audio/speech` | Bearer hoặc Client Key | Tổng hợp âm thanh 48kHz WAV từ văn bản (tích hợp Audio Cache SHA-256 < 5ms & miễn trừ quota, kiểm tra quota ký tự, lọc worker theo voice, điều phối Least-Active) |
| `POST` | `/v1/voices/preview` | Bearer hoặc Client Key | Kết xuất mẫu nghe thử nhanh (hỗ trợ Audio Cache) |

### 2.4 Nhóm Quản Trị Hệ Thống (`/v1/management`, `/dashboard`)
| Phương thức | Endpoint | Header / Auth | Mô tả |
| :--- | :--- | :--- | :--- |
| `GET` | `/dashboard` | `X-Management-Key` | Giao diện điều khiển Web CLIProxyAPI (Metrics, Keys, Workers, Users, Settings) |
| `GET/POST/DELETE/PATCH` | `/v1/management/keys` | `X-Management-Key` | Quản lý Client API Keys |
| `GET/POST` | `/v1/management/workers` | `X-Management-Key` | Quản lý và ping test năng lực các Model Worker Nodes |
| `GET/PUT` | `/v1/management/settings` | `X-Management-Key` | Đọc và lưu cấu hình hệ thống với khả năng Hot-Reload tức thì |
| `GET/PATCH/DELETE` | `/v1/management/users` | `X-Management-Key` | Quản trị tài khoản người dùng và điều chỉnh slot clone |

---

## 3. Kiến Trúc Bộ Nhớ Đệm Âm Thanh (Audio Caching Architecture)
- **Tạo Cache Key Tất Định (`cache_service.get_cache_key`)**:
  - Key được mã hóa bằng SHA-256: `sha256(voice_id + ":" + text.strip() + ":" + smart_pause + ":48000hz")`.
  - Không bao gồm tốc độ (speed) và cao độ (pitch) vì các thông số này được điều khiển thời gian thực phía trình phát client cho độ trễ bằng 0.
- **Quy trình Xử Lý Cache:**
  - **Cache HIT:** Trả về file WAV đã cache trực tiếp từ disk trong **< 10ms**, trả kèm headers `X-Cache-Status: HIT`, `X-Sample-Rate: 48000`, `X-Model-Worker: cache`. Hoàn toàn không gọi sang AI worker và miễn trừ tính quota ký tự cho người dùng.
  - **Cache MISS:** Trừ quota ký tự, chuyển tiếp tới worker phù hợp, sau đó lưu nguyên tử (atomic write qua temp file) vào `storage/audio_cache/{key}.wav` và trả về `X-Cache-Status: MISS`.

---

## 4. Kết Quả Kiểm Thử (Verification Suite)
Toàn bộ **31/31 test cases** trong `VoiceClonerAppServer/tests/` đã được chạy và vượt qua 100%:
- `test_exceptions_and_security.py`:
  - `test_register_invalid_email_and_short_password`: Xác thực email sai chuẩn và mật khẩu < 6 ký tự bị từ chối với 422.
  - `test_login_wrong_credentials`: Đăng nhập sai mật khẩu hoặc tài khoản không tồn tại bị từ chối 401.
  - `test_tampered_jwt_token_rejected`: Token JWT giả mạo hoặc sai chữ ký bị từ chối 401.
  - `test_blacklisted_token_rejected_after_logout`: Token sau khi đăng xuất được đưa vào danh sách đen (Redis/InMemory blacklist) và bị từ chối 401.
  - `test_idor_protection_between_users`: User B tuyệt đối không thể đọc thông tin, tải file audio, sửa, xóa hoặc dùng giọng clone của User A (toàn bộ trả về 404 để chống dò quét IDOR).
  - `test_modify_or_delete_builtin_voice_rejected`: Cấm sửa hoặc xóa giọng đọc có sẵn 400.
  - `test_cloned_slots_quota_exhaustion_and_release`: Kiểm soát chặt chẽ hạn mức 3/3 slot clone, vượt quá nhận 403, xóa giọng cũ giải phóng slot.
  - `test_quota_rollback_on_worker_synthesis_failure`: Khi downstream worker gặp sự cố, hệ thống trả về 503 và hoàn lại 100% số ký tự đã trừ tạm thời.
  - `test_clone_voice_empty_file_rejected`: Tải file audio rỗng bị từ chối 400.
  - `test_management_unauthorized_access`: Truy cập quản trị không có key hoặc sai key bị từ chối 401, xóa user không tồn tại trả về 404.
- `test_audio_cache.py`:
  - `test_audio_cache_hit_and_miss_flow`: Xác nhận lần gọi thứ nhất là `MISS` (tính quota, ghi cache), lần gọi thứ hai cùng câu thoại là `HIT` (trả về tức thì < 50ms, không gọi worker, không trừ quota).
  - `test_preview_audio_cache_flow`: Xác nhận nghe thử mẫu giọng cũng áp dụng cache tăng tốc.
- `test_capability_load_balancing.py`:
  - `test_capability_routing_matches_supported_voice`: Khẳng định request voice A chỉ đến worker hỗ trợ voice A, request voice C chỉ đến worker hỗ trợ voice C.
  - `test_capability_routing_least_active_among_shared_voice`: Khẳng định với voice dùng chung giữa nhiều worker, thuật toán chọn đúng worker ít việc nhất.
  - `test_capability_routing_for_cloning`: Khẳng định các tác vụ clone chỉ điều phối tới worker có năng lực clone.
- `test_dynamic_voice_discovery.py`:
  - Khẳng định khả năng tự động truy vấn và tổng hợp danh mục giọng từ nhiều worker bất kỳ, tự động loại bỏ trùng lặp ID.
- `test_auth_and_voices_e2e.py`:
  - Vòng đời tài khoản người dùng, token refresh, clone voice lưu DB và tổng hợp âm thanh.
- `test_settings_and_3tier_auth.py`:
  - Quản lý cấu hình 3-tier, cập nhật runtime, từ chối client key sai/vô hiệu hóa, tự động hot-reload khi file `settings.json` bị chỉnh sửa trực tiếp trên đĩa (disk mtime check).
- `test_management_users_and_pna.py`:
  - Private Network Access headers, luồng quản lý người dùng, hiển thị chính xác số slot clone đã dùng / tối đa và số ký tự TTS hôm nay.
- `test_dashboard_ui.py`, `test_docker_packaging.py`.

---

## 5. Phase 16: Tự Động Hot-Reload File Cấu Hình & Siết Chặt Xác Thực 3-Tier Client API Key Cho Toàn Bộ Gateway
- **Tự động hot-reload cấu hình đĩa (`settings.json`)**: `SettingsManager` theo dõi `st_mtime` của `config/settings.json`, tự động nạp lại cấu hình và kích hoạt change hooks ngay khi phát hiện chỉnh sửa từ editor mà không cần restart server.
- **Siết chặt xác thực Client API Key & User Token toàn diện**:
  - Bắt buộc mọi request từ client (Android, Web Demo) gửi tới `/v1/*` phải có `X-API-Key` hợp lệ khi `require_client_api_key = True`.
  - Nếu thiếu hoặc rỗng `X-API-Key`: Lập tức từ chối với HTTP 401 (`Client API key required. Please provide a valid X-API-Key header.`).
  - Nếu `X-API-Key` sai, không tồn tại hoặc bị tạm khóa: Lập tức từ chối với HTTP 401 (`Invalid or deactivated client API key.`).
  - **Bắt buộc định danh người dùng đối với chuyển đổi giọng & nhân bản giọng**:
    - Không phục vụ khách vãng lai (unauthenticated guest). Cả `/v1/audio/speech`, `/v1/voices/preview` và `/v1/voices/clone` bắt buộc phải có `current_user: User = Depends(get_current_user)` để kiểm soát rate limit và trừ chính xác hạn mức ký tự (quota).
    - Request thiếu User Token lập tức bị từ chối với HTTP 401 (`Yêu cầu xác thực`).
- **Đồng bộ chuẩn xác Ký tự TTS & Slot người dùng trên Web Dashboard**:
  - API `/v1/management/users` truy vấn đồng thời số lượng giọng clone thực tế (`cloned_voices_count`) cùng `max_cloned_slots`.
  - Chỉ số `tts_chars_today` được lấy theo thời gian thực từ Redis và đồng bộ lưu trữ bền vững vào bảng SQL `daily_usages`.
  - Bảng điều khiển Web Dashboard hiển thị rõ ràng: `X / Y slots` và số lượng ký tự TTS đã sử dụng hôm nay.
- **Đạt 38/38 test cases tự động** trong `VoiceClonerAppServer` vượt qua 100%.
