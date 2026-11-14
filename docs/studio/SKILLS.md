# Professional Editing Engine — 12 skill

Mọi skill: `from studio.editing.skills import REGISTRY; REGISTRY["edit-…"](payload_dict)`.
Input/output là Pydantic model → JSON schema lấy qua `GET /api/studio/skills/{name}/schema`. Mỗi skill sở hữu đúng một *slot*; `check_no_conflict()` chặn hai skill cùng ghi một slot. Skill đánh dấu ⚙ chạy độc lập được qua `POST /api/studio/skills/{name}/run`; các skill còn lại chạy bên trong render.

| # | Skill | Slot | Làm gì | Dùng thế nào |
|---|---|---|---|---|
| 1 | `edit-director` ⚙ | style | Theo thể loại (phim ngắn, TVC, UGC, podcast, hài, drama, hành động): nhịp shot, ngân sách chuyển cảnh (vd. UGC ≤10% số mối nối), look màu, LUFS đích, cỡ phụ đề. Không ghi đè lựa chọn của editor | Tự chạy đầu render (`RenderOptions.apply_director`) |
| 2 | `edit-smart-cut` ⚙ | cut_points | `scdet` tìm đổi cảnh, `silencedetect` cắt im lặng đầu/cuối (giữ khoảng thở), gợi ý điểm cắt | `{"path": "...", "remove_internal_silence": false}` → `trim_in/out`, `keep`, `cut_points` |
| 3 | `edit-transition-pro` | transitions | Cut/match cut (concat), crossfade/fade đen/whip pan (xfade); J-cut/L-cut bằng đặt tiếng tuyệt đối trên timeline, có handle từ nguồn | Đặt `transition_in` trên clip; engine tự lấy handle âm thanh, báo nếu nguồn thiếu handle |
| 4 | `edit-continuity` ⚙ | continuity_check | So khung cuối/đầu tại mỗi mối nối (sau khi đã chỉnh màu): nhảy sáng, lệch tông, lệch histogram — nghiêm hơn khi 2 shot chung nhân vật. Xuất **1 ảnh contact sheet** cho AI/người duyệt kiểm tra mặt/trang phục (không gửi cả video vào AI) | Kết quả trong `qa_report.json → continuity` và `continuity_sheet.jpg` |
| 5 | `edit-color-grading` ⚙ | color | Shot matching gray-world + luma về shot tham chiếu (median), có giới hạn an toàn; eq/colorbalance; LUT `.cube` (kiểm tra hợp lệ, trộn theo `lut_strength`) | Tự match khi render (`auto_match_color`); chỉnh tay qua `ColorAdjust` |
| 6 | `edit-audio-master` | audio_master | Thoại: highpass, `afftdn`, EQ presence, compressor; nhạc ducking bằng sidechain dưới thoại; loudness EBU R128 **2-pass linear** (đo rồi mới chuẩn hoá, một lần duy nhất) | `Timeline.audio` (LUFS đích, true peak, denoise, mức duck) |
| 7 | `edit-dialogue-sync` ⚙ | av_sync | Tương quan chéo đường bao năng lượng giữa tiếng trong clip và file thoại tham chiếu → offset ms + độ tin cậy + filter sửa (`adelay`/`atrim`) | `{"clip_audio": "...", "reference": "..."}` |
| 8 | `edit-auto-subtitles` | subtitles | Nguồn: cue có sẵn → căn script theo vùng có tiếng → ASR faster-whisper `vi` (tuỳ chọn). Ngắt dòng, chia cue dài, SRT + ASS, vùng an toàn 9:16 (MarginV 18%) | `Timeline.subtitles` + `subtitle_style`; báo rõ nếu thiếu faster-whisper |
| 9 | `edit-motion-graphics` | graphics | Title, lower-third, CTA, logo, kinetic text (trượt + mờ dần), đặt trong safe area (trên 10%, dưới 22% với dọc) | `Timeline.overlays` |
| 10 | `edit-beat-sync` ⚙ | beat_grid | Spectral flux → tempo (autocorrelation 70–180 BPM) → lưới beat; snap điểm cắt vào beat chỉ trong dung sai (không kéo lệch cắt theo thoại) | `{"music": "...", "cut_points": [..]}` → `bpm`, `beats`, `snapped` |
| 11 | `edit-multi-format` ⚙ | format | Preset TikTok / Reels / Shorts / Zalo / YouTube 16:9; fit `blur_pad`/`crop`/`pad`; cảnh báo vượt giới hạn thời lượng; **gắn cờ upscale** khi nguồn nhỏ hơn khung | `Timeline.renditions` |
| 12 | `edit-master-qa` ⚙ | final_qa | Black frames, freeze, im lặng dài, clipping, LUFS/true peak, lệch độ dài A/V, tràn/chồng/nhanh phụ đề, kiểm tra codec/pix_fmt/kích thước/fps. Mỗi lỗi gắn `clip_id` để render lại đúng clip | Tự chạy cho master và mọi rendition |

## Nguyên tắc dựng đã mã hoá
- Storytelling hơn hiệu ứng: ngân sách chuyển cảnh theo thể loại, cảnh báo khi vượt.
- Ưu tiên âm thanh: loudness chuẩn hoá đúng một lần ở cuối; ducking nhạc dưới thoại.
- Không phóng đại chất lượng: nguồn 720p dựng khung 1080×1920 sẽ bị ghi rõ là upscale trong metadata/QA.
- Token: mọi việc xác định được đều chạy FFmpeg/numpy; AI chỉ nhận contact sheet nhỏ khi cần đánh giá thị giác.
