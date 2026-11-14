#!/usr/bin/env bash
# Xuân Bắc AI Video Studio V4 — cài đặt & chạy bằng 1 lệnh (macOS / Linux / Windows WSL)
#   bash studio.sh            # lần đầu: tự cài mọi thứ rồi chạy; các lần sau: chỉ chạy
#   bash studio.sh stop       # dừng backend + dashboard
#   bash studio.sh status     # kiểm tra tình trạng
#   bash studio.sh logs       # xem log backend
set -euo pipefail
cd "$(dirname "$0")"
ROOT="$PWD"
RUN="$ROOT/.studio-run"; mkdir -p "$RUN"
OS="$(uname -s)"

say()  { printf "\033[1;36m▶ %s\033[0m\n" "$*"; }
ok()   { printf "\033[1;32m✔ %s\033[0m\n" "$*"; }
warn() { printf "\033[1;33m! %s\033[0m\n" "$*"; }
die()  { printf "\033[1;31m✘ %s\033[0m\n" "$*"; exit 1; }
has()  { command -v "$1" >/dev/null 2>&1; }

# ───────────────────────── cài phụ thuộc hệ thống ─────────────────────────
install_sys() {
  local need=()
  has python3 || need+=(python)
  has ffmpeg  || need+=(ffmpeg)
  has node    || need+=(node)
  has git     || need+=(git)
  [ ${#need[@]} -eq 0 ] && { ok "Python, FFmpeg, Node, Git đã có"; return; }
  say "Cần cài: ${need[*]}"
  if [ "$OS" = "Darwin" ]; then
    if ! has brew; then
      say "Cài Homebrew (sẽ hỏi mật khẩu máy Mac)…"
      /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
      eval "$(/opt/homebrew/bin/brew shellenv 2>/dev/null || /usr/local/bin/brew shellenv)"
    fi
    local pk=(); for n in "${need[@]}"; do case $n in python) pk+=(python@3.12);; *) pk+=("$n");; esac; done
    brew install "${pk[@]}"
  elif has apt-get; then
    sudo apt-get update -y
    local pk=(); for n in "${need[@]}"; do case $n in python) pk+=(python3 python3-venv python3-pip);; node) pk+=(nodejs npm);; *) pk+=("$n");; esac; done
    sudo apt-get install -y "${pk[@]}"
    if has node && [ "$(node -p 'process.versions.node.split(".")[0]')" -lt 18 ]; then
      say "Node quá cũ → cài Node 20"
      curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash - && sudo apt-get install -y nodejs
    fi
  else
    die "Không tự cài được trên hệ điều hành này. Cài tay: Python 3.10+, FFmpeg, Node 18+, Git."
  fi
}

check_versions() {
  python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)' \
    || die "Cần Python 3.10 trở lên (đang có $(python3 --version))."
  [ "$(node -p 'process.versions.node.split(".")[0]')" -ge 18 ] \
    || die "Cần Node 18 trở lên (đang có $(node --version))."
  has ffprobe || die "Thiếu ffprobe (đi kèm FFmpeg)."
}

# ───────────────────────── cài ứng dụng ─────────────────────────
install_app() {
  if [ ! -x .venv/bin/python ]; then
    say "Tạo môi trường Python…"; python3 -m venv .venv
  fi
  if [ ! -f "$RUN/py.ok" ] || [ requirements.txt -nt "$RUN/py.ok" ]; then
    say "Cài thư viện Python (vài phút lần đầu)…"
    .venv/bin/pip install -q --upgrade pip
    .venv/bin/pip install -q -r requirements.txt
    touch "$RUN/py.ok"
  fi
  if [ ! -f "$RUN/js.ok" ] || [ dashboard/package.json -nt "$RUN/js.ok" ]; then
    say "Cài Dashboard (vài phút lần đầu)…"
    (cd dashboard && npm install --no-package-lock --no-audit --no-fund --loglevel=error)
    touch "$RUN/js.ok"
  fi
  if ! grep -q '^STUDIO_SECRET_KEY=' .env 2>/dev/null; then
    say "Tạo khoá mã hoá token (lưu trong .env, không đưa lên GitHub)…"
    echo "STUDIO_SECRET_KEY=$(.venv/bin/python -m studio.publish.crypto)" >> .env
    chmod 600 .env
  fi
  grep -qx '.env' .gitignore 2>/dev/null || echo '.env' >> .gitignore
  grep -qx '.studio-run/' .gitignore 2>/dev/null || echo '.studio-run/' >> .gitignore
  ok "Cài đặt xong"
}

# ───────────────────────── chạy / dừng ─────────────────────────
killtree() {  # npm → sh → vite: dừng cả cây tiến trình
  local c; for c in $(pgrep -P "$1" 2>/dev/null); do killtree "$c" "$2"; done
  kill -"$2" "$1" 2>/dev/null || true
}
alive() { [ -f "$RUN/$1.pid" ] && kill -0 "$(cat "$RUN/$1.pid")" 2>/dev/null; }

start() {
  set -a; [ -f .env ] && . ./.env; set +a
  if alive api; then ok "Backend đang chạy"; else
    say "Chạy backend :8100…"
    nohup .venv/bin/uvicorn agent.main:app --host 127.0.0.1 --port 8100 >"$RUN/api.log" 2>&1 </dev/null &
    echo $! >"$RUN/api.pid"
  fi
  if alive web; then ok "Dashboard đang chạy"; else
    say "Chạy Dashboard :5173…"
    nohup npm --prefix "$ROOT/dashboard" run dev -- --host 127.0.0.1 >"$RUN/web.log" 2>&1 </dev/null &
    echo $! >"$RUN/web.pid"
  fi
  for _ in $(seq 1 40); do curl -fs http://127.0.0.1:8100/health >/dev/null 2>&1 && break; sleep 1; done
  curl -fs http://127.0.0.1:8100/health >/dev/null 2>&1 || { tail -20 "$RUN/api.log"; die "Backend không lên — xem log ở trên."; }
  for _ in $(seq 1 30); do curl -fs http://127.0.0.1:5173 >/dev/null 2>&1 && break; sleep 1; done
  status
  local url=http://localhost:5173/studio
  if [ "$OS" = "Darwin" ]; then open "$url"; elif has wslview; then wslview "$url"; elif has xdg-open; then xdg-open "$url" >/dev/null 2>&1 || true; fi
  echo; ok "Mở Studio: $url"
  echo "  Dừng: bash studio.sh stop   ·   Log: bash studio.sh logs"
}

stop() {
  for s in web api; do
    if alive $s; then
      local pid; pid=$(cat "$RUN/$s.pid")
      killtree "$pid" TERM; sleep 1
      killtree "$pid" KILL   # FlowKit gốc không thoát khi nhận SIGTERM
      ok "Đã dừng $s"
    fi
    rm -f "$RUN/$s.pid"
  done
}

status() {
  alive api && ok "Backend: đang chạy" || warn "Backend: đã dừng"
  alive web && ok "Dashboard: đang chạy" || warn "Dashboard: đã dừng"
  local h; h=$(curl -fs http://127.0.0.1:8100/health 2>/dev/null || true)
  if echo "$h" | grep -q '"extension_connected": *true'; then
    ok "Extension Google Flow: đã kết nối"
  elif [ -n "$h" ]; then
    warn "Extension Google Flow: CHƯA kết nối — làm 2 việc dưới đây:"
    echo "   1) Chrome → chrome://extensions → bật Developer mode → Load unpacked → chọn: $ROOT/extension"
    echo "   2) Mở và đăng nhập https://flow.google.com (để tab mở). Dựng/duyệt/xuất video vẫn dùng được khi chưa kết nối."
  fi
}

case "${1:-up}" in
  up|start)  if [ ! -f "$RUN/py.ok" ] || [ ! -f "$RUN/js.ok" ]; then install_sys; check_versions; fi; install_app; start ;;
  install)   install_sys; check_versions; install_app ;;
  stop)      stop ;;
  restart)   stop; start ;;
  status)    status ;;
  logs)      tail -n 100 -f "$RUN/api.log" ;;
  *)         echo "Dùng: bash studio.sh [up|stop|restart|status|logs|install]" ;;
esac
