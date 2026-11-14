# PROJECT_STATE — Xuân Bắc AI Video Studio V4

Cập nhật: 2026-11-14 · Branch: `feat/studio-v4-p0` (từ `crisng95/flowkit@bef59ca`)

## Tóm tắt
Studio V4 được xây dưới dạng package `studio/` cắm vào FlowKit (router `/api/studio`, worker chạy trong lifespan, dashboard thêm nhóm "Studio V4"). Thay đổi file gốc FlowKit tối thiểu: `agent/main.py` (+14 dòng), `agent/config.py` (+3), `dashboard/src/App.tsx` (nav/route), `requirements.txt` (+numpy, cryptography), CI.

## Trạng thái

| Hạng mục | Trạng thái | Ghi chú |
|---|---|---|
| Audit FlowKit, kiến trúc, backlog | IMPLEMENTED | `docs/studio/README.md`, `TASKS.md` |
| DB studio + migrations đánh số | IMPLEMENTED · TESTED | `studio/db.py` (v1 core, v2 publishing) |
| Media Asset Manager (SHA-256 dedupe, probe) | IMPLEMENTED · TESTED | |
| FlowKit bridge (import scene clip, timeline nháp) | IMPLEMENTED · TESTED (live) | Đã chạy thật với FlowKit API + DB; tải URL ký tên chỉ cho host Google |
| Timeline/EDL (JSON versioned + CMX3600) | IMPLEMENTED · TESTED | |
| 12 editing skills + engine | IMPLEMENTED · TESTED | 19 test skill + 3 test engine end-to-end |
| Render queue + worker + cache theo clip | IMPLEMENTED · TESTED | Re-render 1 clip: 2 hit / 1 miss |
| QA + Approval gate + audit log | IMPLEMENTED · TESTED | QA lỗi phải có ghi chú override |
| Dashboard: Overview, Editor, Assets, Render·QA·Duyệt, Auto Publisher, Calendar | IMPLEMENTED · TESTED (build, lint, headless screenshot desktop+mobile) | |
| Publisher Facebook Reels / YouTube Shorts / TikTok / Zalo OA | IMPLEMENTED · TESTED (mock API) | Chưa test với tài khoản thật — BLOCKED bởi credential/App Review |
| Queue đăng: idempotency, backoff, per-platform, lịch, manual package, mã hoá credential | IMPLEMENTED · TESTED | |
| Character Bible / Storyboard agent (P1) | NOT STARTED | FlowKit đã có entity/reference + skill fk-*; Studio chưa bọc lại |
| Analytics connectors (P3) | NOT STARTED | Bảng `metric_snapshot` + endpoint chỉ trả số liệu thật |
| Multi-user / roles / Postgres (P3) | NOT STARTED | Schema đã viết tương thích Postgres |

## Test
- `pytest tests/unit tests/studio`: **550 passed, 5 skipped** (507 test gốc FlowKit vẫn xanh + 42 test Studio).
- Dashboard: `npm run build` OK; `eslint src/studio src/App.tsx` sạch (6 lỗi lint còn lại là file gốc upstream).
- Live: FlowKit API + Studio chạy thật — tạo project/video/scene → import → sửa timeline → render (3 rendition, QA đạt, -16 LUFS drama) → duyệt → tạo job đăng TikTok → connector gọi mạng bị chặn trong sandbox → job tự lùi lịch thử lại (đúng thiết kế). Token không xuất hiện trong log, mã hoá trong DB.

## Giới hạn / BLOCKED
1. **Không push được lên GitHub**: không có quyền ghi `crisng95/flowkit`, tài khoản `doanxuanbacit-cmd` chưa có fork, `gh` token trong môi trường không hợp lệ. Bàn giao bằng git bundle + patch.
2. Publisher chưa chạy với API thật: cần app Meta (App Review), Google OAuth client, TikTok app đã audit (`video.publish`), Zalo OA có quyền bài viết. Mã lỗi Zalo trong `zalo.py` cần đối chiếu tài liệu OA hiện hành khi có token.
3. Zalo OA API là **bài viết video** (≤50 MB), không phải Reels; không hỗ trợ thì rơi về gói đăng thủ công.
4. ASR tiếng Việt cần `faster-whisper` (tuỳ chọn, chưa cài trong test).
5. Kiểm tra mặt/trang phục cần AI vision: engine đã xuất contact sheet + nhận `ai_findings`, chưa nối provider tự động.
6. Upstream: SIGTERM bị FlowKit chiếm (`loop.add_signal_handler`) nên uvicorn không thoát bằng SIGTERM — dùng SIGINT/Ctrl+C. Có từ trước, không do Studio.
7. `dashboard/package-lock.json` upstream lệch với `package.json` → dùng `npm install --no-package-lock`.

## Tiếp theo
Xem `TASKS.md`.
