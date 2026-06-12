"""Split-routing слой каскада: «РФ напрямую, остальное через exit».

Транзит и policy routing каскада живут в network namespace контейнера
`amnezia-awg2`. Внутри Alpine-контейнера нет `ipset`/`nft`, поэтому split-слой
ставится с ХОСТА через `nsenter -t <pid> -n`, используя бинарники хоста
(`ipset`, `iptables`, `ip`) в netns контейнера. Проверено: хост и контейнер на
одном `iptables nf_tables`, set-match и fwmark в netns работают.

Механизм:
  1. ipset `utmka_direct` (hash:net) ← РФ GeoIP + RFC1918 + свои CIDR;
  2. mangle PREROUTING: -s <client_subnet> -m set --match-set utmka_direct dst
     -j MARK --set-mark 0x1;
  3. ip rule fwmark 0x1 lookup main priority 250 (ВЫШЕ каскадного 300) →
     помеченный (российский) трафик уходит в main = напрямую через entry;
  4. остальной трафик клиента остаётся на priority 300 → каскад → exit.

Fail-closed сохраняется: только не-RU трафик зависит от таблицы каскада/blackhole;
российский трафик всегда жив через main.
"""

from __future__ import annotations

import base64
import shlex
from typing import Optional

from app.services.amnezia_ssh import run_script
from app.ssh import exec as ssh_exec

SET_NAME = "utmka_direct"
SET_TMP = "utmka_direct_tmp"
MARK = "0x1"
SPLIT_RULE_PRIORITY = "250"  # < 300 (каскад) → перехват раньше
SPLIT_TABLE = "main"
HOST_IPSET_FILE = "/var/lib/utmka-cascade/direct.ipset"
HOST_DIR = "/var/lib/utmka-cascade"
HASHSIZE = "8192"
MAXELEM = "300000"


class SplitError(Exception):
    pass


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def netns_pid(ssh, container: str) -> Optional[int]:
    res = run_script(
        ssh,
        "docker inspect -f '{{.State.Pid}}' " + shlex.quote(container) + " 2>/dev/null",
        timeout=20,
    )
    raw = res.stdout.strip()
    try:
        pid = int(raw)
        return pid if pid > 0 else None
    except ValueError:
        return None


def _render_ipset_restore(cidrs: list[str]) -> str:
    """ipset restore-формат со swap для атомарной замены содержимого."""
    lines = [
        f"create {SET_NAME} hash:net family inet hashsize {HASHSIZE} maxelem {MAXELEM} -exist",
        f"create {SET_TMP} hash:net family inet hashsize {HASHSIZE} maxelem {MAXELEM} -exist",
        f"flush {SET_TMP}",
    ]
    for cidr in cidrs:
        lines.append(f"add {SET_TMP} {cidr}")
    lines.append(f"swap {SET_TMP} {SET_NAME}")
    lines.append(f"destroy {SET_TMP}")
    return "\n".join(lines) + "\n"


def _write_host_file_chunked(ssh, path: str, content: str, *, chunk: int = 60000) -> None:
    """Заливает большой файл на хост по частям (append) — безопасно для ARG_MAX."""
    quoted = shlex.quote(path)
    dir_q = shlex.quote(HOST_DIR)
    init = run_script(
        ssh,
        f"mkdir -p {dir_q} && : > {quoted} && chmod 600 {quoted} && echo OK",
        timeout=20,
    )
    if "OK" not in init.stdout:
        raise SplitError(f"Не удалось создать {path}: {init.stderr.strip()}")
    data = content.encode("utf-8")
    for i in range(0, len(data), chunk):
        b64 = base64.b64encode(data[i : i + chunk]).decode("ascii")
        res = run_script(
            ssh,
            f"printf '%s' {shlex.quote(b64)} | base64 -d >> {quoted}",
            timeout=40,
        )
        if res.exit_code != 0:
            raise SplitError(f"Ошибка записи {path}: {res.stderr.strip()}")


def _nse(pid: int) -> str:
    return f"nsenter -t {int(pid)} -n"


# ---------------------------------------------------------------------------
# apply / teardown
# ---------------------------------------------------------------------------


def apply_split(ssh, pid: int, client_subnet: str, cidrs: list[str]) -> int:
    """Накатывает split-слой в netns контейнера. Возвращает число CIDR в set."""
    if not cidrs:
        raise SplitError("Пустой список direct-CIDR — split не применяется.")
    cs = shlex.quote(client_subnet)
    nse = _nse(pid)

    _write_host_file_chunked(ssh, HOST_IPSET_FILE, _render_ipset_restore(cidrs))

    script = f"""
set -e
# 1. Загрузка/замена ipset в netns контейнера (бинарник хоста, namespace контейнера)
{nse} ipset restore -! < {shlex.quote(HOST_IPSET_FILE)}
# 2. mangle MARK для российского/локального трафика клиента
{nse} iptables -t mangle -C PREROUTING -s {cs} -m set --match-set {SET_NAME} dst -j MARK --set-mark {MARK} 2>/dev/null \
  || {nse} iptables -t mangle -A PREROUTING -s {cs} -m set --match-set {SET_NAME} dst -j MARK --set-mark {MARK}
# 3. policy rule: помеченный трафик → main (direct через entry), ВЫШЕ каскада
{nse} ip rule del fwmark {MARK} lookup {SPLIT_TABLE} priority {SPLIT_RULE_PRIORITY} 2>/dev/null || true
{nse} ip rule add fwmark {MARK} lookup {SPLIT_TABLE} priority {SPLIT_RULE_PRIORITY}
# сброс кеша маршрутов
{nse} ip route flush cache 2>/dev/null || true
COUNT=$({nse} ipset list {SET_NAME} 2>/dev/null | grep -c '^[0-9]')
echo "SPLIT_OK count=$COUNT"
"""
    res = run_script(ssh, script, timeout=120)
    if "SPLIT_OK" not in res.stdout:
        raise SplitError(res.stderr.strip() or res.stdout.strip() or "split apply failed")
    count = 0
    for tok in res.stdout.split():
        if tok.startswith("count="):
            try:
                count = int(tok.split("=", 1)[1])
            except ValueError:
                count = 0
    return count


def teardown_split(ssh, pid: int, client_subnet: str) -> None:
    """Снимает split-слой. Порядок: rule → mangle (ссылка на set) → destroy set."""
    cs = shlex.quote(client_subnet or "10.8.1.0/24")
    nse = _nse(pid)
    script = f"""
{nse} ip rule del fwmark {MARK} lookup {SPLIT_TABLE} priority {SPLIT_RULE_PRIORITY} 2>/dev/null || true
{nse} iptables -t mangle -D PREROUTING -s {cs} -m set --match-set {SET_NAME} dst -j MARK --set-mark {MARK} 2>/dev/null || true
{nse} ipset destroy {SET_NAME} 2>/dev/null || true
{nse} ipset destroy {SET_TMP} 2>/dev/null || true
{nse} ip route flush cache 2>/dev/null || true
echo SPLIT_DOWN
"""
    run_script(ssh, script, timeout=60)


def split_health(ssh, pid: int) -> dict:
    """Структурная проверка: RU-IP в set (direct), зарубежный — нет (каскад)."""
    nse = _nse(pid)
    script = f"""
RU=$({nse} ipset test {SET_NAME} 77.88.8.8 2>&1 | grep -c 'is in set' || true)
RU2=$({nse} ipset test {SET_NAME} 87.240.190.78 2>&1 | grep -c 'is in set' || true)
FOREIGN=$({nse} ipset test {SET_NAME} 8.8.8.8 2>&1 | grep -c 'is in set' || true)
RULE=$({nse} ip rule show 2>/dev/null | grep -c 'fwmark {MARK} ' || true)
MANGLE=$({nse} iptables -t mangle -C PREROUTING -m set --match-set {SET_NAME} dst -j MARK --set-mark {MARK} 2>/dev/null && echo 1 || echo 0)
echo "ru=$RU ru2=$RU2 foreign=$FOREIGN rule=$RULE"
"""
    res = run_script(ssh, script, timeout=30)
    vals: dict[str, int] = {}
    for tok in res.stdout.split():
        if "=" in tok:
            k, v = tok.split("=", 1)
            try:
                vals[k] = int(v)
            except ValueError:
                vals[k] = 0
    ru_ok = bool(vals.get("ru") or vals.get("ru2"))
    foreign_excluded = not vals.get("foreign")
    rule_ok = bool(vals.get("rule"))
    return {
        "ru_in_set": ru_ok,
        "foreign_excluded": foreign_excluded,
        "rule_present": rule_ok,
        "ok": ru_ok and foreign_excluded and rule_ok,
    }


def egress_probe(ssh, pid: int, client_addr: str) -> dict:
    """Реальная проверка выхода: RU-сервис → entry IP, зарубежный → exit IP."""
    src = shlex.quote(client_addr)
    nse = _nse(pid)
    script = f"""
RU_IP=$({nse} curl -4 -s --max-time 8 --interface {src} https://api.2ip.ru 2>/dev/null \
  || {nse} curl -4 -s --max-time 8 --interface {src} https://ip.2ip.io 2>/dev/null)
FOREIGN_IP=$({nse} curl -4 -s --max-time 8 --interface {src} https://api.ipify.org 2>/dev/null \
  || {nse} curl -4 -s --max-time 8 --interface {src} http://ifconfig.me 2>/dev/null)
echo "ru_egress=$RU_IP"
echo "foreign_egress=$FOREIGN_IP"
"""
    res = run_script(ssh, script, timeout=40)
    out: dict[str, Optional[str]] = {"ru_egress": None, "foreign_egress": None}
    for line in res.stdout.splitlines():
        if line.startswith("ru_egress="):
            out["ru_egress"] = line.split("=", 1)[1].strip() or None
        elif line.startswith("foreign_egress="):
            out["foreign_egress"] = line.split("=", 1)[1].strip() or None
    return out
