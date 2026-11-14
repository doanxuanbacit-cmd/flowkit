#!/bin/bash
# ════════════════════════════════════════════════════════════════════
#  Xuân Bắc AI Video Studio V4 — bộ cài cho máy Mac (Intel & Apple chip)
#  Dán 1 dòng vào Terminal:
#   curl -fsSL https://raw.githubusercontent.com/doanxuanbacit-cmd/flowkit/feat/studio-v4-p0/install-mac.sh | bash
#  • Không cần mật khẩu, không cần Homebrew/Git/Node/Xcode.
#  • Mọi thứ nằm gọn trong ~/XuanBacStudio. Chạy lại = cập nhật (giữ nguyên dữ liệu).
#  • Tạo 3 biểu tượng trên Desktop: Mở / Tắt / Cập nhật Studio.
# ════════════════════════════════════════════════════════════════════
set -euo pipefail
main() {
REPO="doanxuanbacit-cmd/flowkit"
BRANCH="${STUDIO_BRANCH:-feat/studio-v4-p0}"
HOME_DIR="$HOME/XuanBacStudio"
APP="$HOME_DIR/app"; BIN="$HOME_DIR/bin"; DATA="$HOME_DIR/data"; VENV="$HOME_DIR/venv"
LOG="$HOME_DIR/install.log"
mkdir -p "$BIN" "$DATA" "$HOME_DIR/run"
: >"$LOG"

say()  { printf "\n\033[1;36m▶ %s\033[0m\n" "$*"; }
ok()   { printf "\033[1;32m  ✔ %s\033[0m\n" "$*"; }
die()  { printf "\n\033[1;31m✘ %s\033[0m\n  Chi tiết: %s\n  Gửi ảnh màn hình này cho Claude để được xử lý.\n" "$*" "$LOG"; exit 1; }
fetch(){ curl -fL --retry 3 --connect-timeout 20 -sS "$@" 2>>"$LOG"; }
trap 'die "Cài đặt dừng giữa chừng (dòng $LINENO)."' ERR

[ "$(uname -s)" = "Darwin" ] || die "Bộ cài này chỉ dành cho máy Mac."
ARCH="$(uname -m)"   # x86_64 | arm64
echo "Xuân Bắc AI Video Studio — cài đặt trên macOS $(sw_vers -productVersion) ($ARCH)"

# ── 1. uv: trình quản lý Python (tự tải Python, không cần mật khẩu) ──
say "1/6 Chuẩn bị Python"
if [ ! -x "$BIN/uv" ]; then
  UVA=$([ "$ARCH" = arm64 ] && echo aarch64 || echo x86_64)
  fetch "https://github.com/astral-sh/uv/releases/latest/download/uv-$UVA-apple-darwin.tar.gz" \
    | tar xz -C "$BIN" --strip-components=1 || die "Không tải được uv."
fi
export UV_PYTHON_INSTALL_DIR="$HOME_DIR/python" UV_CACHE_DIR="$HOME_DIR/.cache/uv"
"$BIN/uv" python install 3.12 >>"$LOG" 2>&1 || die "Không tải được Python 3.12."
ok "Python 3.12"

# ── 2. FFmpeg (bản dựng sẵn, chạy được trên macOS cũ) ──
say "2/6 Chuẩn bị FFmpeg"
ff_ok() { "$BIN/ffmpeg" -hide_banner -version >/dev/null 2>&1 && "$BIN/ffprobe" -hide_banner -version >/dev/null 2>&1; }
if ! ff_ok; then
  rm -f "$BIN/ffmpeg" "$BIN/ffprobe"
  # Bản tĩnh trên GitHub (FFmpeg 6, chạy từ macOS 10.9) — đã kiểm tra
  FA=$([ "$ARCH" = arm64 ] && echo arm64 || echo x64)
  for t in ffmpeg ffprobe; do
    fetch "https://github.com/eugeneware/ffmpeg-static/releases/download/b6.0/$t-darwin-$FA.gz" \
      | gunzip >"$BIN/$t" 2>>"$LOG" || true
  done
  chmod +x "$BIN/ffmpeg" "$BIN/ffprobe" 2>/dev/null || true
  if ! ff_ok && [ "$ARCH" = x86_64 ]; then   # dự phòng: evermeet.cx
    for t in ffmpeg ffprobe; do
      fetch -o "$HOME_DIR/run/$t.zip" "https://evermeet.cx/ffmpeg/getrelease/$t/zip" \
        && unzip -oq "$HOME_DIR/run/$t.zip" -d "$BIN" >>"$LOG" 2>&1 || true
    done
  fi
  chmod +x "$BIN/ffmpeg" "$BIN/ffprobe" 2>/dev/null || true
  xattr -dr com.apple.quarantine "$BIN" 2>/dev/null || true
  ff_ok || die "Không chạy được FFmpeg trên máy này."
fi
ok "$("$BIN/ffmpeg" -hide_banner -version | head -1 | cut -c1-40)"

# ── 3. Mã nguồn Studio (tải từ GitHub, giữ nguyên dữ liệu cũ) ──
say "3/6 Tải Xuân Bắc AI Video Studio"
TMP="$HOME_DIR/run/src"; rm -rf "$TMP"; mkdir -p "$TMP"
fetch "https://codeload.github.com/$REPO/tar.gz/refs/heads/$BRANCH" | tar xz -C "$TMP" --strip-components=1 \
  || die "Không tải được mã nguồn từ GitHub."
[ -f "$TMP/dashboard/dist/index.html" ] || die "Bản tải về thiếu Dashboard dựng sẵn."
if [ -d "$APP" ]; then
  # chống ghi đè dữ liệu nếu ai đó lỡ để dữ liệu trong thư mục app cũ
  for f in flow_agent.db flow_agent.db-wal flow_agent.db-shm output studio_data; do
    [ -e "$APP/$f" ] && [ ! -e "$DATA/$f" ] && mv "$APP/$f" "$DATA/" || true
  done
  rm -rf "$APP.old"; mv "$APP" "$APP.old"
fi
mv "$TMP" "$APP"; rm -rf "$APP.old"
ok "Mã nguồn: nhánh $BRANCH"

# ── 4. Thư viện Python ──
say "4/6 Cài thư viện (lần đầu mất vài phút)"
[ -x "$VENV/bin/python" ] || "$BIN/uv" venv --python 3.12 "$VENV" >>"$LOG" 2>&1
"$BIN/uv" pip install --python "$VENV/bin/python" -r "$APP/requirements.txt" >>"$LOG" 2>&1 \
  || die "Cài thư viện Python thất bại."
ok "Thư viện đã cài"

# ── 5. Cấu hình (khoá mã hoá token, font tiếng Việt) ──
say "5/6 Cấu hình"
ENVF="$DATA/.env"; touch "$ENVF"; chmod 600 "$ENVF"
if ! grep -q '^STUDIO_SECRET_KEY=' "$ENVF"; then
  echo "STUDIO_SECRET_KEY=$(cd "$APP" && "$VENV/bin/python" -m studio.publish.crypto)" >>"$ENVF"
fi
FONT="/System/Library/Fonts/Supplemental/Arial Bold.ttf"
[ -f "$FONT" ] || FONT="/Library/Fonts/Arial Bold.ttf"
grep -q '^STUDIO_FONT=' "$ENVF" || { [ -f "$FONT" ] && echo "STUDIO_FONT=\"$FONT\"" >>"$ENVF"; echo 'STUDIO_SUB_FONT="Arial"' >>"$ENVF"; }
ok "Khoá mã hoá + font tiếng Việt"

# ── 6. Biểu tượng trên Desktop ──
say "6/6 Tạo biểu tượng trên Desktop"
cat >"$HOME_DIR/start.sh" <<'EOS'
#!/bin/bash
# Mở Studio: backend + dashboard ở :8100, rồi mở Chrome riêng đã nạp extension FlowKit.
H="$HOME/XuanBacStudio"; cd "$H/app"
export PATH="$H/bin:$PATH" FLOW_AGENT_DIR="$H/data"
set -a; . "$H/data/.env"; set +a
up() { curl -fs -m 2 http://127.0.0.1:8100/health >/dev/null 2>&1; }
if ! up; then
  echo "Đang khởi động Studio…"
  nohup "$H/venv/bin/python" -m uvicorn agent.main:app --host 127.0.0.1 --port 8100 \
    >"$H/run/studio.log" 2>&1 </dev/null &
  echo $! >"$H/run/studio.pid"
  for _ in $(seq 1 60); do up && break; sleep 1; done
fi
up || { echo "Studio không khởi động được. Log:"; tail -30 "$H/run/studio.log"; read -r -p "Nhấn Enter để đóng…"; exit 1; }

CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
[ -x "$CHROME" ] || CHROME="$HOME/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
if [ -x "$CHROME" ]; then
  # Hồ sơ Chrome riêng cho Studio: extension tự nạp, đăng nhập Google 1 lần là nhớ.
  nohup "$CHROME" --user-data-dir="$H/chrome-profile" --load-extension="$H/app/extension" \
    --no-first-run --no-default-browser-check \
    "https://flow.google.com" "http://127.0.0.1:8100/studio" >/dev/null 2>&1 &
  MAJ=$("$CHROME" --version 2>/dev/null | grep -Eo '[0-9]+' | head -1)
  if [ "${MAJ:-0}" -ge 137 ]; then
    echo "Chrome $MAJ không còn tự nạp extension → kéo thư mục $H/app/extension vào chrome://extensions (bật Developer mode)."
    open "$H/app"
  fi
else
  open "http://127.0.0.1:8100/studio"
  echo "Chưa có Google Chrome → cài tại https://www.google.com/chrome/ rồi mở lại."
fi
echo "Studio đang chạy: http://127.0.0.1:8100/studio  (cửa sổ này đóng được)"
EOS
cat >"$HOME_DIR/stop.sh" <<'EOS'
#!/bin/bash
H="$HOME/XuanBacStudio"
PID=$(cat "$H/run/studio.pid" 2>/dev/null || true)
[ -n "$PID" ] && { kill "$PID" 2>/dev/null; sleep 1; kill -9 "$PID" 2>/dev/null; }
pkill -9 -f "uvicorn agent.main:app --host 127.0.0.1 --port 8100" 2>/dev/null || true
rm -f "$H/run/studio.pid"; echo "Đã tắt Studio."
EOS
chmod +x "$HOME_DIR/start.sh" "$HOME_DIR/stop.sh"

DESK="$HOME/Desktop"; mkdir -p "$DESK"
mk() { printf '#!/bin/bash\n%s\n' "$2" >"$DESK/$1.command"; chmod +x "$DESK/$1.command"; }
mk "Mở Xuân Bắc Studio"      "bash \"\$HOME/XuanBacStudio/start.sh\""
mk "Tắt Xuân Bắc Studio"     "bash \"\$HOME/XuanBacStudio/stop.sh\"; sleep 2"
mk "Cập nhật Xuân Bắc Studio" "bash \"\$HOME/XuanBacStudio/stop.sh\"; curl -fsSL https://raw.githubusercontent.com/$REPO/$BRANCH/install-mac.sh | bash; read -r -p 'Xong. Nhấn Enter để đóng…'"
ok "Desktop: Mở / Tắt / Cập nhật Xuân Bắc Studio"

trap - ERR
say "Cài đặt xong — đang mở Studio…"
bash "$HOME_DIR/start.sh" </dev/null
cat <<'EOF'

  ════════════════════════════════════════════════════════════
   LẦN ĐẦU: trong cửa sổ Chrome vừa mở, ĐĂNG NHẬP Google ở tab
   flow.google.com (chỉ 1 lần, các lần sau tự nhớ).
   Hằng ngày: bấm đúp "Mở Xuân Bắc Studio" trên Desktop.
  ════════════════════════════════════════════════════════════
EOF
}
# Bọc trong main() để bash đọc hết script trước khi chạy (an toàn khi dùng curl | bash).
main "$@"
