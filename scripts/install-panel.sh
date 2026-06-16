#!/usr/bin/env bash
# Установка UTMka+AWG на чистый VPS (Ubuntu/Debian) — одной командой.
#
# Публичный репозиторий, токен НЕ нужен:
#   curl -fsSL https://raw.githubusercontent.com/Saw28rus/utmka-awg/main/scripts/install-panel.sh | sudo bash
#
# Скрипт: ставит Docker (если нет), клонирует репозиторий в /opt/utmka-awg,
# генерирует случайные секреты в .env и поднимает прод-стек. По завершении
# печатает адрес панели и одноразовый пароль администратора.
#
# (Опционально) приватный форк: sudo GITHUB_TOKEN="ghp_..." bash install-panel.sh
set -euo pipefail

INSTALL_DIR="${INSTALL_DIR:-/opt/utmka-awg}"
REPO_OWNER="${REPO_OWNER:-Saw28rus}"
REPO_NAME="${REPO_NAME:-utmka-awg}"
REPO_URL="${REPO_URL:-https://github.com/${REPO_OWNER}/${REPO_NAME}.git}"
BRANCH="${BRANCH:-main}"
GITHUB_TOKEN="${GITHUB_TOKEN:-}"
ADMIN_EMAIL="${ADMIN_EMAIL:-admin@utmka.app}"
ADMIN_PASSWORD="${ADMIN_PASSWORD:-}"

need_root() {
  if [ "$(id -u)" -ne 0 ]; then
    echo "Запусти скрипт от root: sudo bash $0"
    exit 1
  fi
}

rand_hex() {
  if command -v openssl >/dev/null 2>&1; then
    openssl rand -hex 32
  else
    tr -dc 'a-f0-9' </dev/urandom | head -c 64
  fi
}

ensure_docker() {
  if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
    return
  fi
  echo "Устанавливаю Docker…"
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq
  apt-get install -y -qq ca-certificates curl git
  curl -fsSL https://get.docker.com | sh
  systemctl enable --now docker
}

repo_clone_url() {
  if [ -n "$GITHUB_TOKEN" ]; then
    printf 'https://x-access-token:%s@github.com/%s/%s.git' "$GITHUB_TOKEN" "$REPO_OWNER" "$REPO_NAME"
  else
    printf '%s' "$REPO_URL"
  fi
}

preflight() {
  # Свободное место: первая установка тянет образы + сборку (запас ~2.5 ГБ).
  local avail_kb
  avail_kb=$(df -Pk / 2>/dev/null | awk 'NR==2 {print $4}')
  if [ -n "${avail_kb:-}" ] && [ "$avail_kb" -lt 2621440 ]; then
    echo "Мало места на диске (свободно ~$((avail_kb / 1024)) МБ, нужно ~2.5 ГБ). Освободите место и повторите."
    exit 1
  fi
  # Порт 8080 занят чужим процессом (наш фронт ещё не поднят) — предупреждаем.
  if command -v ss >/dev/null 2>&1 && ss -ltnH 2>/dev/null | awk '{print $4}' | grep -qE '[:.]8080$'; then
    if ! docker ps --format '{{.Ports}}' 2>/dev/null | grep -q '8080'; then
      echo "ВНИМАНИЕ: порт 8080 уже занят другим процессом — панели он нужен. Освободите порт или будет конфликт."
    fi
  fi
}

public_ip() {
  curl -4 -s --max-time 6 ifconfig.me 2>/dev/null \
    || curl -4 -s --max-time 6 icanhazip.com 2>/dev/null \
    || hostname -I 2>/dev/null | awk '{print $1}'
}

clone_or_update() {
  local clone_url
  clone_url="$(repo_clone_url)"

  if [ -d "$INSTALL_DIR/.git" ]; then
    echo "Обновляю репозиторий в $INSTALL_DIR…"
    git -C "$INSTALL_DIR" fetch --depth 1 origin "$BRANCH"
    git -C "$INSTALL_DIR" checkout "$BRANCH"
    git -C "$INSTALL_DIR" pull --ff-only origin "$BRANCH" || true
  else
    echo "Клонирую ${REPO_OWNER}/${REPO_NAME} → $INSTALL_DIR…"
    rm -rf "$INSTALL_DIR"
    git clone --depth 1 --branch "$BRANCH" "$clone_url" "$INSTALL_DIR"
  fi
}

write_env() {
  local env_file="$INSTALL_DIR/.env"
  local secret_key
  local pg_pass
  local admin_pass

  secret_key="$(rand_hex)"
  pg_pass="$(rand_hex | head -c 24)"
  admin_pass="${ADMIN_PASSWORD:-$(rand_hex | head -c 16)}"

  if [ -f "$env_file" ] && grep -q '^PANEL_SECRET_KEY=' "$env_file"; then
    echo ".env уже существует — не перезаписываю секреты."
    FRESH_ENV=0
    return
  fi

  cat >"$env_file" <<EOF
APP_NAME=UTMka+AWG
ENVIRONMENT=production
API_V1_PREFIX=/api/v1

DATABASE_URL=postgresql+asyncpg://utmka:${pg_pass}@postgres:5432/utmka_awg
POSTGRES_PASSWORD=${pg_pass}
PANEL_SECRET_KEY=${secret_key}
JWT_ALGORITHM=HS256
ACCESS_TOKEN_MINUTES=15
REFRESH_TOKEN_DAYS=7

ADMIN_EMAIL=${ADMIN_EMAIL}
ADMIN_PASSWORD=${admin_pass}

DEFAULT_DNS=1.1.1.1
DEFAULT_SUBNET=10.8.1.0/24
DEFAULT_UDP_PORT_MIN=1024
DEFAULT_UDP_PORT_MAX=9999

PANEL_HOST_DIR=${INSTALL_DIR}
PANEL_INSTALL_DIR=/host/utmka-awg
EOF
  chmod 600 "$env_file"
  # Свежий .env сгенерирован → запомним пароль, чтобы после старта ПРИНУДИТЕЛЬНО
  # выставить его в БД (иначе уцелевший том Postgres от прошлой установки оставит
  # старого админа, и напечатанный пароль не подойдёт).
  FRESH_ENV=1
  GENERATED_ADMIN_PASS="$admin_pass"
  local ip
  ip="$(public_ip)"
  echo ""
  echo "╔══════════════════════════════════════════╗"
  echo "║       UTMka+AWG — данные для входа       ║"
  echo "╠══════════════════════════════════════════╣"
  echo "║  Адрес:  http://${ip}:8080"
  echo "║  Email:  ${ADMIN_EMAIL}"
  echo "║  Пароль: ${admin_pass}"
  echo "╚══════════════════════════════════════════╝"
  echo "Сохрани пароль — повторно не покажется."
}

start_stack() {
  cd "$INSTALL_DIR"
  docker compose up -d --build
  local ip
  ip="$(public_ip)"
  echo ""
  echo "Готово. Открой в браузере: http://${ip}:8080"
  echo "Если не открывается — открой порт 8080 в firewall облака/VPS."
  echo "Позже: домен + HTTPS во вкладке «Безопасность» у сервера в панели."
}

wait_for_health() {
  # Ждём, пока backend ответит на /health (миграции + старт) — до ~90 сек.
  local i
  for i in $(seq 1 30); do
    if curl -s -o /dev/null -w '%{http_code}' --max-time 5 \
        http://127.0.0.1:8080/api/v1/health 2>/dev/null | grep -q '200'; then
      return 0
    fi
    sleep 3
  done
  return 1
}

ensure_admin_password() {
  # Только при свежем .env: гарантируем, что напечатанный пароль реально работает,
  # даже если уцелел том Postgres от прошлой установки (стейл-админ).
  if [ "${FRESH_ENV:-0}" != "1" ] || [ -z "${GENERATED_ADMIN_PASS:-}" ]; then
    return 0
  fi
  cd "$INSTALL_DIR"
  if ! wait_for_health; then
    echo "ВНИМАНИЕ: панель не ответила вовремя — пароль админа не синхронизирован."
    echo "Если вход не работает: docker compose exec -T backend python /host/utmka-awg/scripts/reset-admin.py 'НОВЫЙ_ПАРОЛЬ'"
    return 0
  fi
  if docker compose exec -T backend python /host/utmka-awg/scripts/reset-admin.py "$GENERATED_ADMIN_PASS" >/dev/null 2>&1; then
    echo "Пароль администратора синхронизирован с напечатанным выше."
  else
    echo "ВНИМАНИЕ: не удалось синхронизировать пароль админа автоматически."
    echo "Сбросьте вручную: docker compose exec -T backend python /host/utmka-awg/scripts/reset-admin.py 'НОВЫЙ_ПАРОЛЬ'"
  fi
}

main() {
  need_root
  ensure_docker
  preflight
  clone_or_update
  write_env
  start_stack
  ensure_admin_password
}

main "$@"
