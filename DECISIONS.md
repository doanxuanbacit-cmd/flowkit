# DECISIONS — Studio V4

| # | Quyết định | Lý do |
|---|---|---|
| D1 | Studio là package `studio/` cắm vào FlowKit, không fork/viết lại `agent/` | Giữ FlowKit cập nhật được từ upstream; diff file gốc < 40 dòng |
| D2 | DB riêng `studio.db` (sqlite3 sync, WAL, migrations đánh số), đọc `flow_agent.db` ở chế độ read-only | Không đụng schema FlowKit; dễ chuyển Postgres; worker thread-safe |
| D3 | Mọi xử lý xác định được dùng FFmpeg/numpy, không gọi AI | Tiết kiệm token, tái lập được, test được |
| D4 | Mỗi skill sở hữu đúng 1 slot; engine có thứ tự stage cố định | Không áp một hiệu ứng hai lần (màu, loudness, chuyển cảnh) |
| D5 | J/L-cut làm bằng đặt audio tuyệt đối (adelay+amix) với handle từ nguồn | Một cơ chế cho mọi loại chuyển cảnh âm thanh; báo rõ khi nguồn thiếu handle |
| D6 | Loudness 2-pass linear ở cuối pipeline, mục tiêu theo thể loại (-14 mặc định, drama/podcast -16) | Chuẩn social, tránh pumping |
| D7 | Cache clip trung gian theo nội dung (path+size+mtime+tham số) | Render lại một phần khi QA/editor đổi một clip |
| D8 | Continuity AI chỉ nhận contact sheet JPEG | Giảm token so với gửi video |
| D9 | Gắn cờ upscale trung thực trong metadata/QA | Theo yêu cầu không tuyên bố 1080p/4K khi nguồn thấp hơn |
| D10 | Publisher dùng HTTP thô (httpx) + state checkpoint, không dùng client discovery | Resumable, test bằng MockTransport, không đăng trùng khi retry |
| D11 | Approval gate mặc định bật; tắt thì vẫn chỉ đăng bản QA đạt; duyệt bản QA lỗi phải có ghi chú | Không đăng nội dung chưa đạt QA/chưa duyệt |
| D12 | Credential mã hoá Fernet, khoá chỉ từ env `STUDIO_SECRET_KEY` | Không lưu khoá cạnh dữ liệu, không log token |
| D13 | Zalo OA → bài viết video; thiếu quyền → MANUAL_REQUIRED + gói file | Không giả định OA hỗ trợ đăng trực tiếp kiểu Reels |
| D14 | TikTok thiếu `video.publish` → endpoint inbox (nháp) → MANUAL_REQUIRED | Tuân thủ audit TikTok |
| D15 | Không dùng n8n; scheduler là worker nội bộ | Theo master prompt |
| D16 | Không push được GitHub → bàn giao git bundle + patch | Thiếu quyền ghi repo/fork |
