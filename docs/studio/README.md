# Xuân Bắc AI Video Studio V4 — Professional Edition

Studio V4 là lớp hậu kỳ + QA + phê duyệt + đăng tự động chạy **bên trong FlowKit** (cùng tiến trình FastAPI, cùng dashboard). FlowKit lo phần tạo ảnh/video trên Google Flow; Studio nhận clip đã render, dựng chuyên nghiệp bằng FFmpeg, kiểm tra chất lượng, chờ duyệt, rồi đăng lên Facebook Reels / YouTube Shorts / TikTok / Zalo OA bằng API chính thức.

## Kiến trúc

```mermaid
flowchart LR
  subgraph FlowKit Core (có sẵn)
    EXT[Chrome extension<br/>flow.google.com tab] <--WS--> AG[agent/ FastAPI :8100]
    AG --> FDB[(flow_agent.db)]
    AG --> OUT[output/ clip mp4]
  end
  subgraph Studio V4 (mới — studio/)
    BR[flowkit_bridge<br/>đọc DB read-only] --> AS[Asset Manager<br/>SHA-256, dedupe]
    AS --> TL[Timeline / EDL<br/>versioned]
    TL --> RQ[Render queue<br/>worker thread]
    RQ --> ENG[Editing Engine<br/>12 skills · FFmpeg]
    ENG --> DEL[master · renditions · thumbnail<br/>SRT/ASS · metadata · manifest · QA report]
    DEL --> AP{Approval gate}
    AP --> PQ[Publish queue<br/>idempotent · backoff · per-platform]
    PQ --> FB[Facebook Reels] & YT[YouTube Shorts] & TT[TikTok] & ZL[Zalo OA]
    PQ -.không đủ quyền.-> MAN[Gói đăng thủ công]
    SDB[(studio.db<br/>migrations)]
  end
  OUT --> BR
  FDB --> BR
  DASH[Dashboard React<br/>/studio/*] --> AG
```

Thứ tự stage trong engine cố định, mỗi stage do **đúng một skill** sở hữu (slot), nên không hiệu ứng nào bị áp hai lần:

`style → conform+màu (cache theo clip) → continuity → transitions/J-L → đồ hoạ → phụ đề → stems + loudness 2-pass → master → renditions → thumbnail → QA → manifest`

Clip trung gian được cache theo nội dung: sửa 1 clip chỉ render lại clip đó (đã kiểm chứng bằng test: 2 hit / 1 miss).

## Cài đặt & chạy

**Máy Mac — không cần biết kỹ thuật (khuyên dùng):** mở Terminal, dán 1 dòng:

```bash
curl -fsSL https://raw.githubusercontent.com/doanxuanbacit-cmd/flowkit/feat/studio-v4-p0/install-mac.sh | bash
```

Không cần mật khẩu hay Homebrew/Git/Node. Cài vào `~/XuanBacStudio`, tạo 3 biểu tượng Desktop (Mở / Tắt / Cập nhật). "Mở" chạy Studio tại http://127.0.0.1:8100/studio và mở một Chrome riêng đã nạp sẵn extension FlowKit — chỉ cần đăng nhập Google ở flow.google.com lần đầu.

**Dành cho lập trình viên — macOS / Linux / Windows WSL:**

```bash
git clone -b feat/studio-v4-p0 https://github.com/doanxuanbacit-cmd/flowkit.git
cd flowkit && bash studio.sh        # lần đầu tự cài Python/FFmpeg/Node + thư viện, tạo khoá .env, chạy và mở Studio
bash studio.sh stop | status | logs | restart
```

Cách thủ công:

```bash
python -m venv .venv && .venv/bin/pip install -r requirements.txt -r requirements-dev.txt
# tuỳ chọn: ASR tiếng Việt cho phụ đề tự động
.venv/bin/pip install -r requirements-optional.txt

export STUDIO_SECRET_KEY=$(.venv/bin/python -m studio.publish.crypto)   # lưu vào .env, KHÔNG commit
.venv/bin/uvicorn agent.main:app --host 127.0.0.1 --port 8100

cd dashboard && npm install --no-package-lock && npm run dev   # http://localhost:5173/studio
```

| Biến môi trường | Mặc định | Ý nghĩa |
|---|---|---|
| `STUDIO_ENABLED` | `1` | Bật router `/api/studio` + worker |
| `STUDIO_DIR` | `<repo>/studio_data` | Asset, render, cache, `studio.db` |
| `STUDIO_SECRET_KEY` | — | Khoá Fernet mã hoá credential (bắt buộc trước khi kết nối kênh) |
| `STUDIO_REQUIRE_APPROVAL` | `1` | Bắt buộc duyệt trước khi đăng. Tắt thì vẫn chỉ đăng bản **QA đạt** |
| `STUDIO_RENDER_WORKERS` | `1` | Số luồng render song song |
| `STUDIO_PUBLISH_MAX_ATTEMPTS` | `5` | Số lần thử lại khi lỗi tạm thời |
| `STUDIO_NOTIFY_WEBHOOK` | — | URL nhận thông báo lỗi/đã đăng (JSON POST) |
| `STUDIO_FONT` / `STUDIO_SUB_FONT` | DejaVu/Inter | Font có dấu tiếng Việt |
| `META_GRAPH_VERSION` | `v26.0` | Phiên bản Graph API |

> Không chạy FFmpeg dài hạn trong Vercel Serverless. Backend + worker cần máy có tiến trình dài (máy cá nhân, VPS, Fly/Render/Railway). Dashboard có thể build tĩnh và deploy Vercel, trỏ `/api` về backend.

## Quy trình 30 giây mẫu (9:16, 2 clip × 15s)

1. FlowKit tạo 2 scene (Omni Flash/Veo) → clip lưu `output/<project>/…` hoặc URL ký tên.
2. Dashboard → **Professional Editor → Từ FlowKit** → chọn video → Studio import clip (không tải tay) và tạo timeline nháp (nhân vật, narrator_text → phụ đề).
3. Chỉnh in/out, chuyển cảnh, màu, phụ đề, CTA, nhạc → **Render + QA**.
4. **Render · QA · Duyệt**: xem master/renditions, bảng lỗi kỹ thuật, ảnh continuity; duyệt (QA lỗi thì bắt buộc ghi chú override — ghi vào audit log).
5. **Auto Publisher**: chọn bản đã duyệt, chọn kênh, giờ đăng → hàng đợi tự đăng, thử lại, hoặc chuyển "đăng thủ công" kèm gói file.

## API chính (`/api/studio`)

| Method | Path | |
|---|---|---|
| GET | `/health`, `/overview`, `/skills`, `/skills/{name}/schema` | Trạng thái, danh mục skill + JSON schema |
| POST | `/skills/{name}/run` | Chạy skill phân tích (smart-cut, beat-sync, dialogue-sync, QA…) trên file trong thư mục cho phép |
| GET/POST/DELETE | `/assets`, `/assets/upload`, `/assets/import-path`, `/assets/{id}/file` | Thư viện asset |
| GET/POST | `/flowkit/videos`, `/flowkit/import` | Cầu nối FlowKit |
| GET/PUT/DELETE | `/timelines/{id}`, `/timelines/{id}/edl` | Timeline (EDL JSON, xuất CMX3600) |
| POST | `/timelines/{id}/render` | Đưa vào hàng đợi render |
| GET | `/renders`, `/renders/{id}`, `/renders/{id}/qa`, `/renders/{id}/files/{name}` | Kết quả |
| POST | `/renders/{id}/review` | Duyệt / từ chối |
| GET/POST | `/accounts`, `/accounts/{id}/disable` | Kênh mạng xã hội |
| GET/POST | `/publish`, `/publish/calendar?month=`, `/publish/{id}/retry|cancel|mark-published` | Hàng đợi đăng |
| GET | `/audit`, `/analytics` | Nhật ký, số liệu (chỉ số liệu thật đã thu) |

Sự kiện realtime đi qua WebSocket `/ws/dashboard` có sẵn của FlowKit: `studio_render`, `studio_published`, `studio_publish_failed`, `studio_publish_manual`, `studio_account_reauth`.

## Kết nối nền tảng (quyền phải do nền tảng cấp)

| Nền tảng | Credential JSON | Quyền cần | Ghi chú |
|---|---|---|---|
| Facebook Reels | `page_id`, `page_access_token` | `pages_manage_posts`, `pages_read_engagement`, `pages_show_list` (App Review) | Luồng `video_reels` start → `rupload.facebook.com` → finish; poll `status`. Lỗi 190 → cần đăng nhập lại |
| YouTube Shorts | `client_id`, `client_secret`, `refresh_token` | `youtube.upload` | Upload resumable, `publishAt` để hẹn giờ; hết quota → tự thử lại sau mốc reset (nửa đêm giờ Pacific) |
| TikTok | `access_token`, `refresh_token`, `client_key`, `client_secret`, `scope` | `video.publish` (Direct Post, app đã audit) hoặc `video.upload` (đẩy nháp vào inbox) | Bắt buộc gọi `creator_info` trước; app chưa audit chỉ đăng riêng tư → chuyển thủ công. Chỉ có `video.upload` → đẩy nháp, người dùng hoàn tất trong app |
| Zalo OA | `access_token`, `refresh_token`, `app_id`, `secret_key` | Quyền bài viết (article) của OA | API OA hỗ trợ **bài viết video** (≤ 50 MB), không phải feed kiểu Reels. Khi kết nối hệ thống thử gọi API bài viết; không có quyền → mọi job Zalo thành "đăng thủ công" kèm gói file cho OA Manager |

Các connector đã được kiểm thử với API **giả lập** (đúng trình tự gọi, resume, ánh xạ lỗi). Chưa kiểm thử với tài khoản thật/sandbox — xem `PROJECT_STATE.md`.
