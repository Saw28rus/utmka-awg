#!/bin/sh
# Ставит Python-зависимости с запасными зеркалами.
# С RU-VPS files.pythonhosted.org часто рвётся по таймауту — тогда Tsinghua / Aliyun / Tencent.
# Кэш pip сохраняется между попытками (BuildKit cache mount), уже скачанные wheel не качаются заново.
set -u

REQ="${1:-/app/requirements.txt}"

log() { echo "pip-install: $*"; }

pip_from() {
  index="$1"
  host="$2"
  pip install \
    --disable-pip-version-check \
    --default-timeout=120 \
    --retries 10 \
    -i "$index" \
    --trusted-host "$host" \
    --trusted-host files.pythonhosted.org \
    --trusted-host pypi.org \
    -r "$REQ"
}

try_index() {
  index="$1"
  host="$2"
  n=1
  while [ "$n" -le 3 ]; do
    log "попытка ${n}/3 через ${host}"
    if pip_from "$index" "$host"; then
      log "готово через ${host}"
      return 0
    fi
    n=$((n + 1))
    log "обрыв, повтор через 3 с (кэш pip сохраняется)"
    sleep 3
  done
  return 1
}

pypi_reachable() {
  curl -fsS -o /dev/null --max-time 8 -I "https://pypi.org/simple/pip/" \
    || curl -fsS -o /dev/null --max-time 8 -I "https://files.pythonhosted.org/"
}

if pypi_reachable; then
  if try_index "https://pypi.org/simple" "pypi.org"; then
    exit 0
  fi
  log "официальный PyPI не докачался — переключаюсь на зеркала"
else
  log "официальный PyPI недоступен — сразу зеркала"
fi

if try_index "https://pypi.tuna.tsinghua.edu.cn/simple" "pypi.tuna.tsinghua.edu.cn"; then
  exit 0
fi
if try_index "https://mirrors.aliyun.com/pypi/simple" "mirrors.aliyun.com"; then
  exit 0
fi
if try_index "https://mirrors.cloud.tencent.com/pypi/simple" "mirrors.cloud.tencent.com"; then
  exit 0
fi
if try_index "https://pypi.org/simple" "pypi.org"; then
  exit 0
fi

log "ни одно зеркало не смогло поставить зависимости"
exit 1
