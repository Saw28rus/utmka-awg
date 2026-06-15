#!/bin/sh
# Запускается в отдельном sibling-контейнере (docker:cli), который НЕ пересоздаётся
# во время сборки backend. Alpine — только /bin/sh, без bash.
set -eu

INSTALL_DIR="${INSTALL_DIR:-/host/utmka-awg}"
COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.yml}"
DATA_DIR="${DATA_DIR:-/app/data}"
LOCK_FILE="${DATA_DIR}/update.lock"
STATE_FILE="${DATA_DIR}/update_state.json"
LOG_FILE="${DATA_DIR}/update.log"
HEALTH_URL="${PANEL_HEALTH_URL:-http://127.0.0.1:8080/api/v1/health}"
BACKUP_ROOT="${DATA_DIR}/backups"
PANEL_REPO="${PANEL_REPO:-Saw28rus/utmka-awg}"
PANEL_BRANCH="${PANEL_BRANCH:-main}"
PG_USER="${PG_USER:-utmka}"
PG_DB="${PG_DB:-utmka_awg}"
# Целевой релиз-тег (передаёт backend). Пусто → возьмём высший semver-тег локально.
PANEL_TARGET_TAG="${PANEL_TARGET_TAG:-}"

mkdir -p "$DATA_DIR"
: >"$LOG_FILE"
exec >>"$LOG_FILE" 2>&1

log() { printf '[update] %s\n' "$*"; }

short_sha() {
  printf '%s' "$1" | cut -c1-12
}

dc() {
  docker compose -f "$COMPOSE_FILE" "$@"
}

pg_password() {
  # Пароль БД для pg_dump/psql берём из .env (single source of truth).
  # Локальный сокет в контейнере обычно trust, но PGPASSWORD не помешает.
  grep '^POSTGRES_PASSWORD=' "$INSTALL_DIR/.env" 2>/dev/null | head -n1 | cut -d= -f2- || true
}

state() {
  # state <status> <progress> <message>
  cat >"$STATE_FILE" <<EOF
{"status":"$1","progress":$2,"message":"$3","previous_commit":"${PREV_COMMIT:-}","target_commit":"${TARGET_COMMIT:-}","updated_at":"$(date -u +%Y-%m-%dT%H:%M:%SZ)"}
EOF
}

fail() {
  log "$1"
  state "failed_manual" 100 "$1"
  exit 1
}

on_exit() {
  code=$?
  if [ "$code" -ne 0 ]; then
    if [ ! -f "$STATE_FILE" ] || ! grep -qE '"status":"(success|rolled_back|failed_manual)"' "$STATE_FILE" 2>/dev/null; then
      state "failed_manual" 100 "Скрипт обновления завершился с ошибкой (код $code). См. лог."
    fi
  fi
}

cleanup_lock() {
  rm -f "$LOCK_FILE"
}

trap 'on_exit; cleanup_lock' EXIT

if [ ! -d "$INSTALL_DIR" ]; then
  fail "Каталог установки не найден: $INSTALL_DIR"
fi

if [ ! -f "$INSTALL_DIR/scripts/update-panel.sh" ]; then
  fail "Скрипт update-panel.sh не найден в $INSTALL_DIR/scripts/"
fi

if [ -f "$LOCK_FILE" ]; then
  lock_mtime=$(stat -c %Y "$LOCK_FILE" 2>/dev/null || echo 0)
  age=$(( $(date +%s) - lock_mtime ))
  if [ "$age" -lt 1800 ]; then
    fail "Обновление уже выполняется (lock)."
  fi
  rm -f "$LOCK_FILE"
fi

touch "$LOCK_FILE"

cd "$INSTALL_DIR"
state "running" 5 "Подготовка окружения"

if [ ! -d ".git" ]; then
  fail "В $INSTALL_DIR нет git-репозитория."
fi

state "running" 8 "Проверка места и настроек"

# Проверка свободного места ДО любых действий: git fetch + docker build
# на полном диске роняют обновление на середине.
avail_kb=$(df -Pk "$INSTALL_DIR" 2>/dev/null | awk 'NR==2 {print $4}')
if [ -n "${avail_kb:-}" ] && [ "$avail_kb" -lt 1572864 ]; then
  avail_h=$((avail_kb / 1024))
  fail "Мало места на диске (свободно ~${avail_h} МБ, нужно минимум 1.5 ГБ). Освободите место: docker image prune -af; docker builder prune -af; rm -rf /app/data/backups/pre-update-* (внутри backend-контейнера)."
fi

# Pre-flight: инструменты и конфиг должны быть на месте ДО изменений.
if ! docker compose version >/dev/null 2>&1; then
  fail "docker compose недоступен в окружении обновления. Нужен SSH."
fi
if [ ! -f "$INSTALL_DIR/$COMPOSE_FILE" ]; then
  fail "Не найден $COMPOSE_FILE в $INSTALL_DIR. Нужен SSH."
fi
if [ ! -f "$INSTALL_DIR/.env" ]; then
  fail "Нет файла .env в $INSTALL_DIR — нечем подключиться к БД. Нужен SSH."
fi
for key in POSTGRES_PASSWORD PANEL_SECRET_KEY; do
  val=$(grep "^${key}=" "$INSTALL_DIR/.env" 2>/dev/null | head -n1 | cut -d= -f2-)
  if [ -z "${val:-}" ]; then
    fail "В .env отсутствует или пуст ${key}. Обновление остановлено (защита от поломки). Нужен SSH."
  fi
done

PREV_COMMIT="$(git rev-parse HEAD)"
TARGET_COMMIT=""
STAMP="$(date -u +%Y%m%d-%H%M%S)"
BACKUP_DIR="${BACKUP_ROOT}/pre-update-${STAMP}"
mkdir -p "$BACKUP_DIR"

# Ротация: держим только 2 последних pre-update бэкапа (+ текущий).
ls -1d "$BACKUP_ROOT"/pre-update-* 2>/dev/null | sort -r | tail -n +3 | while read -r old_backup; do
  log "Удаляю старый бэкап $old_backup"
  rm -rf "$old_backup"
done

if [ -n "${GITHUB_TOKEN:-}" ]; then
  log "Настраиваю git remote с токеном (приватный репозиторий/форк)"
  git remote set-url origin "https://x-access-token:${GITHUB_TOKEN}@github.com/${PANEL_REPO}.git"
else
  # Публичный репозиторий — токен не нужен, ставим обычный https-remote.
  git remote set-url origin "https://github.com/${PANEL_REPO}.git" 2>/dev/null || true
  log "Токен не задан — обновление из публичного репозитория."
fi

state "running" 12 "Бэкап файлов панели"
log "Бэкап данных → $BACKUP_DIR"
# ВАЖНО: каталог backups исключаем, иначе каждый бэкап включает все предыдущие
# (экспоненциальный рост и переполнение диска).
if [ -d "$DATA_DIR" ]; then
  for item in "$DATA_DIR"/* "$DATA_DIR"/.[!.]*; do
    [ -e "$item" ] || continue
    case "$(basename "$item")" in
      backups|update.log|update_state.json|update.lock) continue ;;
    esac
    cp -a "$item" "$BACKUP_DIR/" 2>/dev/null || true
  done
fi
if [ -f "$INSTALL_DIR/.env" ]; then
  cp -a "$INSTALL_DIR/.env" "$BACKUP_DIR/dot-env.backup"
fi

# Дамп БД ДО любых изменений кода/миграций. Без него кривая миграция = потеря данных.
# pg_dump --clean --if-exists → восстановление само пересоздаёт объекты.
DB_DUMP="$BACKUP_DIR/db.sql.gz"
state "running" 22 "Дамп базы данных"
log "pg_dump → $DB_DUMP"
if dc exec -T -e PGPASSWORD="$(pg_password)" postgres \
     pg_dump -U "$PG_USER" --clean --if-exists "$PG_DB" > "$BACKUP_DIR/db.sql" 2>>"$LOG_FILE"; then
  gzip -f "$BACKUP_DIR/db.sql"
else
  rm -f "$BACKUP_DIR/db.sql"
  fail "Не удалось создать дамп БД перед миграциями. Обновление остановлено, изменений нет."
fi
if [ ! -s "$DB_DUMP" ]; then
  fail "Дамп БД пустой — обновление остановлено для безопасности данных."
fi
log "Дамп БД готов ($(du -h "$DB_DUMP" 2>/dev/null | cut -f1))"

# Откат возвращает СОГЛАСОВАННОЕ состояние: код + схема + данные.
rollback() {
  log "Откат на $PREV_COMMIT (код + база)"
  git checkout -f "$PREV_COMMIT"
  # Освобождаем соединения backend перед restore (DROP не пройдёт при активных сессиях).
  dc stop backend >/dev/null 2>&1 || true
  dc up -d postgres >/dev/null 2>&1 || true
  i=1
  while [ "$i" -le 30 ]; do
    if dc exec -T postgres pg_isready -U "$PG_USER" -d "$PG_DB" >/dev/null 2>&1; then
      break
    fi
    sleep 2
    i=$((i + 1))
  done
  if [ -s "$DB_DUMP" ]; then
    log "Восстановление БД из дампа"
    if gunzip -c "$DB_DUMP" | dc exec -T -e PGPASSWORD="$(pg_password)" postgres \
         psql -q -v ON_ERROR_STOP=1 -U "$PG_USER" -d "$PG_DB" >/dev/null 2>>"$LOG_FILE"; then
      log "БД восстановлена из дампа."
    else
      log "ВНИМАНИЕ: восстановление БД завершилось с ошибкой — проверьте данные вручную (дамп: $DB_DUMP)."
    fi
  else
    log "Дамп БД не найден/пуст — откат только кода."
  fi
  dc up -d --build
}

state "running" 32 "Подготовка git"
# Устойчивость к ручным правкам на сервере: не падаем молча на ff-merge,
# а аккуратно убираем локальные изменения в stash (сохранены, не потеряны).
if [ -n "$(git status --porcelain 2>/dev/null)" ]; then
  log "Найдены локальные правки в рабочем дереве — сохраняю в git stash перед обновлением."
  if git stash push -u -m "auto-stash-before-update-${STAMP}" >/dev/null 2>&1; then
    log "Локальные правки сохранены в stash (git stash list). После обновления при желании: git stash pop."
  else
    log "Не удалось сделать stash — пробую жёсткий сброс рабочего дерева к ${PREV_COMMIT}."
    git checkout -f "$PREV_COMMIT" >/dev/null 2>&1 || true
  fi
fi
# Обновляемся на ПОМЕЧЕННЫЙ релиз-тег (semver), а не на «сырой» main.
log "git fetch --tags"
state "running" 38 "Загрузка релизов с GitHub"
if ! git fetch --tags --force --prune origin; then
  fail "git fetch не удался. Проверьте GitHub token в настройках панели."
fi

TARGET_TAG="$PANEL_TARGET_TAG"
if [ -z "$TARGET_TAG" ]; then
  TARGET_TAG=$(git tag -l 'v*' | grep -E '^v[0-9]+\.[0-9]+\.[0-9]+$' | sort -V | tail -n1)
fi
if [ -z "$TARGET_TAG" ]; then
  log "Нет релиз-тегов — отмена без изменений."
  state "failed_manual" 100 "Не найден релиз для обновления (нет тегов). Нужен SSH."
  exit 2
fi
log "Целевой релиз: $TARGET_TAG"

if ! git rev-parse -q --verify "refs/tags/${TARGET_TAG}" >/dev/null 2>&1; then
  fail "Тег $TARGET_TAG не найден после fetch."
fi
if ! git checkout -f "tags/${TARGET_TAG}"; then
  log "Не удалось переключиться на тег — откат без изменений."
  git checkout -f "$PREV_COMMIT"
  state "failed_manual" 100 "Не удалось переключиться на релиз $TARGET_TAG. Нужен SSH."
  exit 2
fi

# Запекаем реальную версию в образ (VERSION-файл в репо не бампается вручную).
# Делаем ДО сборки, чтобы backend показывал корректную «текущую версию».
if [ -f "$INSTALL_DIR/backend/app/VERSION" ]; then
  printf '%s\n' "${TARGET_TAG#v}" > "$INSTALL_DIR/backend/app/VERSION" || true
  log "VERSION → ${TARGET_TAG#v}"
fi

state "running" 52 "Сборка контейнеров"
log "docker compose build"
if ! docker compose -f "$COMPOSE_FILE" build; then
  log "docker compose failed — выполняю rollback"
  state "running" 60 "Сбой сборки — откат"
  rollback || true
  fail "Сборка контейнеров не удалась. Выполнен откат на $PREV_COMMIT."
fi

state "running" 72 "Перезапуск контейнеров"
log "docker compose up"
if ! docker compose -f "$COMPOSE_FILE" up -d; then
  log "docker compose up failed — выполняю rollback"
  state "running" 75 "Сбой запуска — откат"
  rollback || true
  fail "Запуск контейнеров не удался. Выполнен откат на $PREV_COMMIT."
fi

state "running" 84 "Проверка работоспособности"
log "healthcheck $HEALTH_URL"
ok=0
i=1
while [ "$i" -le 30 ]; do
  if curl -sf --max-time 5 "$HEALTH_URL" >/dev/null 2>&1; then
    ok=1
    break
  fi
  sleep 3
  i=$((i + 1))
done

if [ "$ok" -ne 1 ]; then
  log "healthcheck failed — выполняю rollback"
  state "running" 85 "Сбой проверки — откат"
  rollback
  i=1
  while [ "$i" -le 20 ]; do
    if curl -sf --max-time 5 "$HEALTH_URL" >/dev/null 2>&1; then
      log "Откат успешен."
      state "rolled_back" 100 "Обновление откатилось: панель работает на прежней версии."
      exit 3
    fi
    sleep 3
    i=$((i + 1))
  done
  state "failed_manual" 100 "Откат не подтверждён healthcheck — нужен SSH. Данные сохранены."
  exit 4
fi

TARGET_COMMIT="$(git rev-parse HEAD)"

# Чистка после успешной сборки: dangling-слои копятся гигабайтами с каждым update.
log "Чистка старых docker-слоёв (image/builder prune)"
state "running" 94 "Чистка временных docker-слоёв"
docker image prune -f >/dev/null 2>&1 || true
docker builder prune -f >/dev/null 2>&1 || true

state "success" 100 "Готово: $(short_sha "$PREV_COMMIT") → $(short_sha "$TARGET_COMMIT")"
log "Готово: $PREV_COMMIT → $TARGET_COMMIT"
exit 0
