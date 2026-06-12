import shlex
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

from app.schemas.servers import ServerMetrics
from app.services.metrics_cache import metrics_cache
from app.services.awg_config import parse_dump
from app.services.client_store import client_store
from app.services.server_store import server_store
from app.ssh import exec as ssh_exec

METRICS_SCRIPT = r"""
read _ a b c d _ < /proc/stat
pt=$((a+b+c+d)); pi=$d
sleep 0.2
read _ a b c d _ < /proc/stat
t=$((a+b+c+d)); i=$d
dt=$((t-pt)); di=$((i-pi))
cpu=0
[ "$dt" -gt 0 ] && cpu=$(( (100*(dt-di))/dt ))
echo "cpu=$cpu"
awk '/MemTotal/{mt=$2}/MemAvailable/{ma=$2}END{print "mem_total="mt*1024; print "mem_used="(mt-ma)*1024}' /proc/meminfo
df -B1 / 2>/dev/null | awk 'NR==2{print "disk_total="$2; print "disk_used="$3}'
up=$(cut -d. -f1 /proc/uptime 2>/dev/null)
echo "uptime=$up"
"""


def get_server_metrics(server_id: str, *, refresh: bool = False) -> ServerMetrics:
    if not refresh:
        cached = metrics_cache.get(server_id)
        if cached:
            return cached

    record = server_store.get_record(server_id)
    target = server_store.ssh_target(server_id)
    if not record or not target:
        return ServerMetrics(
            server_id=server_id,
            status="unknown",
            online=False,
            message="Сервер не найден.",
        )

    try:
        ssh = ssh_exec.connect(
            host=target.host,
            port=target.port,
            username=target.username,
            password=target.password,
            key=target.key,
        )
    except Exception as exc:  # noqa: BLE001
        server_store.update_runtime(server_id, status="offline")
        return ServerMetrics(
            server_id=server_id,
            status="offline",
            online=False,
            protocols=["AmneziaWG 2.0"] if record.get("awg2_imported") else [],
            message=f"SSH не отвечает: {exc}",
        )

    try:
        values = _parse_kv(ssh_exec.run(ssh, METRICS_SCRIPT, timeout=20).stdout)

        container, stats = _resolve_awg(ssh, record)
        awg2_running = False
        active_peers = record.get("active_peers", 0)
        total_traffic = 0
        if container:
            status_out = ssh_exec.run(
                ssh,
                f"docker inspect -f '{{{{.State.Status}}}}' {shlex.quote(container)} 2>/dev/null || true",
            ).stdout.strip()
            awg2_running = status_out == "running"

            if stats:
                active_peers = len(stats)
                total_traffic = sum(t.rx_bytes + t.tx_bytes for t in stats.values())
                client_store.update_traffic(server_id, stats)

            if server_store.has_xray(record):
                from app.services.xray_stats import fetch_xray_stats

                xray_stats = fetch_xray_stats(ssh, server_id)
                if xray_stats:
                    client_store.update_traffic(server_id, xray_stats)
                    active_peers = max(active_peers, len(xray_stats))
                    total_traffic += sum(t.rx_bytes + t.tx_bytes for t in xray_stats.values())

            # блокировка/разблокировка клиентов по лимиту и сроку
            try:
                from app.services.awg_enforce import enforce_server

                enforce_server(ssh, record, server_id)
            except Exception:  # noqa: BLE001
                pass

        metrics = ServerMetrics(
            server_id=server_id,
            status="online",
            online=True,
            cpu_percent=_to_float(values.get("cpu")),
            mem_used_bytes=_to_int(values.get("mem_used")),
            mem_total_bytes=_to_int(values.get("mem_total")),
            disk_used_bytes=_to_int(values.get("disk_used")),
            disk_total_bytes=_to_int(values.get("disk_total")),
            uptime_seconds=_to_int(values.get("uptime")),
            awg2_container=container,
            awg2_running=awg2_running,
            active_peers=active_peers,
            total_traffic_bytes=total_traffic,
            protocols=["AmneziaWG 2.0"] if record.get("awg2_imported") or record.get("awg2_detected") else [],
        )
        server_store.update_runtime(server_id, status="online", active_peers=active_peers)
        metrics_cache.set(server_id, metrics)
        return metrics
    finally:
        ssh.close()


def get_all_server_metrics(*, refresh: bool = False) -> list[ServerMetrics]:
    """Метрики всех серверов параллельно (для списка /servers)."""
    server_ids = [item.id for item in server_store.list()]
    if not server_ids:
        return []

    if not refresh:
        cached: list[ServerMetrics] = []
        missing: list[str] = []
        for server_id in server_ids:
            hit = metrics_cache.get(server_id)
            if hit:
                cached.append(hit)
            else:
                missing.append(server_id)
        if not missing:
            return cached

    results: list[ServerMetrics] = []
    to_fetch = server_ids if refresh else missing

    with ThreadPoolExecutor(max_workers=min(8, len(to_fetch))) as pool:
        futures = {pool.submit(get_server_metrics, sid, refresh=True): sid for sid in to_fetch}
        for future in as_completed(futures):
            try:
                results.append(future.result())
            except Exception:  # noqa: BLE001
                sid = futures[future]
                results.append(
                    ServerMetrics(
                        server_id=sid,
                        status="offline",
                        online=False,
                        message="Не удалось получить метрики.",
                    )
                )

    if not refresh:
        results.extend(cached)
    results.sort(key=lambda m: server_ids.index(m.server_id) if m.server_id in server_ids else 999)
    return results


def _resolve_awg(ssh, record: dict):
    """Находит контейнер с активным AWG/WG и возвращает (имя, статистику peers).

    Среди контейнеров несколько могут содержать 'amnezia' в имени (панель, БД и т.п.),
    поэтому проверяем именно тот, где `awg show` отдаёт данные.
    """
    containers = record.get("container_names") or []
    ordered = sorted(
        containers,
        key=lambda n: (0 if "awg" in n.lower() else (1 if "amnezia" in n.lower() else 2)),
    )
    fallback: Optional[str] = None
    for name in ordered:
        stats = _peer_stats(ssh, name)
        if stats:
            return name, stats
        if fallback is None and "awg" in name.lower():
            fallback = name
    return (fallback or (ordered[0] if ordered else None)), {}


def _peer_stats(ssh, container: str):
    inner = "awg show all dump 2>/dev/null || wg show all dump 2>/dev/null || true"
    cmd = f"docker exec {shlex.quote(container)} sh -c {shlex.quote(inner)} 2>/dev/null || true"
    out = ssh_exec.run(ssh, cmd).stdout
    return parse_dump(out)


def _parse_kv(text: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in text.splitlines():
        if "=" in line:
            key, _, value = line.partition("=")
            result[key.strip()] = value.strip()
    return result


def _to_int(value: Optional[str]) -> Optional[int]:
    try:
        return int(value) if value not in (None, "") else None
    except ValueError:
        return None


def _to_float(value: Optional[str]) -> Optional[float]:
    try:
        return float(value) if value not in (None, "") else None
    except ValueError:
        return None
