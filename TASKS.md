# TASKS — Studio V4 backlog

## P0 — Functional MVP ✅
- [x] Audit FlowKit, tái sử dụng: FastAPI app, event bus WebSocket, `file_url_to_path`, `resolve_4k_file`, `slugify`, dashboard shell + theme
- [x] `studio.db` + migrations, asset manager, FlowKit bridge
- [x] Timeline/EDL, FFmpeg engine, 12 skill có schema + test
- [x] Render queue, preview/export, QA, approval, audit
- [x] Dashboard Studio (5 trang), responsive
- [x] CI: chạy `tests/studio`, build + lint dashboard

## P2 — Publishing Automation ✅ (mock-tested)
- [x] 4 connector, mã hoá credential, refresh token (TikTok/Zalo/Google), queue + backoff + idempotency + lịch + manual override + webhook thông báo
- [ ] **Chạy thử với tài khoản sandbox/test thật** (cần credential — xem PROJECT_STATE)
- [ ] OAuth flow trong dashboard (hiện nhập token JSON sau khi lấy từ OAuth của app)
- [ ] Đăng theo lịch native của nền tảng (YouTube `publishAt`, FB `SCHEDULED` đã hỗ trợ qua `options`; UI chưa có tuỳ chọn)

## P1 — AI Production + Advanced Editing
- [ ] Agent Director/Character/Storyboard: bọc skill `fk-create-project`, entity reference FlowKit vào schema JSON Studio (Character Bible, Storyboard per clip, prompt EN/thoại VI)
- [ ] Nối `edit-continuity.ai_findings` với provider review của FlowKit (`agent/services/video_reviewer.py`) dùng contact sheet
- [ ] Render lại tự động riêng clip lỗi khi QA gắn `clip_id` (đã có cache + `force_clips`, thiếu vòng lặp tự động có giới hạn)
- [ ] Waveform + scrub trong timeline editor; kéo thả sắp xếp clip
- [ ] Cài `faster-whisper` trên máy render, bật `use_asr`

## P3 — SaaS & Analytics
- [ ] Analytics connectors (Graph insights, YouTube Analytics, TikTok video query) ghi `metric_snapshot`
- [ ] Multi-user, roles, team approval, Postgres (schema đã tương thích)
- [ ] Theo dõi credit Google Flow / chi phí, backup `studio.db` + assets
