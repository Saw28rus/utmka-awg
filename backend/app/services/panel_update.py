import json
import os
import re
import shutil
import socket
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.panel_job import PanelJob
from app.services.panel_settings_service import PanelSettingsService

INSTALL_DIR = Path(os.getenv("PANEL_INSTALL_DIR", "/host/utmka-awg"))
HOST_INSTALL_DIR = Path(os.getenv("PANEL_HOST_DIR", "/opt/utmka-awg"))
UPDATE_SCRIPT = INSTALL_DIR / "scripts" / "update-panel.sh"
DATA_DIR = Path("/app/data")
LOCK_FILE = DATA_DIR / "update.lock"
UPDATE_STATE = DATA_DIR / "update_state.json"
UPDATE_LOG = DATA_DIR / "update.log"
PANEL_VERSION_FILE = Path(__file__).resolve().parents[1] / "VERSION"
HEALTH_URL = os.getenv("PANEL_HEALTH_URL", "http://127.0.0.1:8080/api/v1/health")
UPDATER_IMAGE = os.getenv("PANEL_UPDATER_IMAGE", "docker:27-cli")
UPDATER_NAME = "utmka-updater"
DOCKER_SOCK = Path("/var/run/docker.sock")
DATA_VOLUME = os.getenv("PANEL_DATA_VOLUME", "utmka-awg_panel_data")
PANEL_REPO = os.getenv("PANEL_REPO", "Saw28rus/utmka-awg")
PANEL_BRANCH = os.getenv("PANEL_BRANCH", "main")

TERMINAL_STATUSES = {"success", "rolled_back", "failed_manual"}

DOCKER_BIN_CANDIDATES = (
    os.getenv("DOCKER_BIN"),
    shutil.which("docker"),
    "/usr/bin/docker",
    "/usr/local/bin/docker",
)


def resolve_docker_bin() -> Optional[str]:
    for candidate in DOCKER_BIN_CANDIDATES:
        if not candidate:
            continue
        path = Path(candidate)
        if path.is_file() and os.access(path, os.X_OK):
            return str(path)
    return None


def panel_update_capable() -> bool:
    return (
        INSTALL_DIR.exists()
        and UPDATE_SCRIPT.exists()
        and DOCKER_SOCK.exists()
        and resolve_docker_bin() is not None
    )


def _read_git_commit(base: Path) -> Optional[str]:
    git_head = base / ".git" / "HEAD"
    if not git_head.exists():
        return None
    ref = git_head.read_text(encoding="utf-8").strip()
    if ref.startswith("ref:"):
        ref_path = base / ".git" / ref.split(" ", 1)[1].strip()
        if ref_path.exists():
            return ref_path.read_text(encoding="utf-8").strip()[:12]
        return None
    return ref[:12]


def read_local_version() -> str:
    if PANEL_VERSION_FILE.exists():
        return PANEL_VERSION_FILE.read_text(encoding="utf-8").strip()
    deployed = _read_git_commit(INSTALL_DIR)
    return deployed or "unknown"


def read_deployed_commit() -> str:
    """Commit кода на хосте — для сравнения с GitHub при проверке обновлений."""
    deployed = _read_git_commit(INSTALL_DIR)
    if deployed:
        return deployed
    version = read_local_version()
    return version[:12] if version else "unknown"


def _norm_version(v: str) -> str:
    return (v or "").strip().lstrip("vV")


def _parse_semver(v: str) -> Optional[tuple[int, int, int]]:
    m = re.match(r"^(\d+)\.(\d+)\.(\d+)", _norm_version(v))
    if not m:
        return None
    return (int(m.group(1)), int(m.group(2)), int(m.group(3)))


def _pick_latest_tag(tags: list[str]) -> Optional[str]:
    best: Optional[str] = None
    best_t: Optional[tuple[int, int, int]] = None
    for tag in tags:
        t = _parse_semver(tag)
        if t is None:
            continue
        if best_t is None or t > best_t:
            best_t, best = t, tag
    return best


async def get_latest_tag(token: str = "") -> Optional[str]:
    """Высший semver-тег из GitHub. Работает с обычными git-тегами (без Release-объекта).

    Токен опционален: для публичного репозитория проверка идёт анонимно,
    для приватного — с токеном из настроек.
    """
    url = f"https://api.github.com/repos/{PANEL_REPO}/tags?per_page=100"
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.get(url, headers=headers)
            resp.raise_for_status()
            tags = [item.get("name", "") for item in resp.json()]
    except Exception:  # noqa: BLE001
        return None
    return _pick_latest_tag(tags)


async def check_for_updates(session: AsyncSession) -> dict[str, Any]:
    current = read_local_version()
    settings = PanelSettingsService(session)
    # Токен опционален: публичный репозиторий проверяется анонимно.
    token = await settings.get_github_token() or os.getenv("GITHUB_TOKEN", "")

    latest = await get_latest_tag(token)
    if not latest:
        return {
            "current": current,
            "latest": None,
            "available": None,
            "message": "Не удалось получить релизы (нет тегов или ошибка доступа к GitHub).",
            "capable": panel_update_capable(),
        }

    cur_t = _parse_semver(current)
    lat_t = _parse_semver(latest)
    if cur_t and lat_t:
        available = lat_t > cur_t
    else:
        # У сервера ещё нет версии-релиза (старый коммит) — предлагаем обновиться.
        available = _norm_version(latest) != _norm_version(current)

    return {
        "current": current,
        "latest": latest,
        "available": available,
        "message": "Доступно обновление." if available else "Панель актуальна.",
        "capable": panel_update_capable(),
        "changelog_url": f"https://github.com/{PANEL_REPO}/releases/tag/{latest}",
    }


def read_update_state() -> dict[str, Any]:
    if UPDATE_STATE.exists():
        try:
            return json.loads(UPDATE_STATE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _docker_inspect(field: str, target: str) -> str:
    docker_bin = resolve_docker_bin()
    if not docker_bin:
        return ""
    res = subprocess.run(
        [docker_bin, "inspect", "-f", field, target],
        capture_output=True,
        text=True,
    )
    if res.returncode != 0:
        return ""
    return res.stdout.strip()


def _updater_container_status() -> str:
    status = _docker_inspect("{{.State.Status}}", UPDATER_NAME)
    if status:
        return status
    return _docker_inspect("{{.State.Status}}", socket.gethostname())


def _updater_container_logs(tail: int = 80) -> str:
    docker_bin = resolve_docker_bin()
    if not docker_bin:
        return ""
    for target in (UPDATER_NAME, socket.gethostname()):
        res = subprocess.run(
            [docker_bin, "logs", "--tail", str(tail), target],
            capture_output=True,
            text=True,
        )
        if res.returncode == 0 and (res.stdout or res.stderr):
            return (res.stdout + res.stderr).strip()
    return ""


def _own_mounts() -> dict[str, str]:
    """Реальные источники монтирований backend-контейнера: Destination -> Source.

    Запрашиваем у демона хоста, потому что updater-контейнер создаётся тем же
    демоном и пути/имена volume должны быть в системе координат ХОСТА, а не
    внутри backend-контейнера.
    """
    docker_bin = resolve_docker_bin()
    if not docker_bin:
        return {}
    res = subprocess.run(
        [docker_bin, "inspect", "-f", "{{json .Mounts}}", socket.gethostname()],
        capture_output=True,
        text=True,
    )
    if res.returncode != 0 or not res.stdout.strip():
        return {}
    try:
        mounts = json.loads(res.stdout)
    except json.JSONDecodeError:
        return {}
    result: dict[str, str] = {}
    for mount in mounts:
        dest = mount.get("Destination")
        if not dest:
            continue
        if mount.get("Type") == "volume":
            result[dest] = mount.get("Name", "")
        else:
            result[dest] = mount.get("Source", "")
    return result


def _launch_updater_container(github_token: str = "", target_tag: str = "") -> str:
    """Запускает отдельный sibling-контейнер, который переживёт пересборку backend."""
    docker_bin = resolve_docker_bin()
    if not docker_bin:
        raise RuntimeError(
            "docker CLI не найден в контейнере backend. "
            "На VPS: cd /opt/utmka-awg && docker compose up -d"
        )

    # Берём РЕАЛЬНЫЕ источники монтирований у демона хоста — без догадок о путях.
    mounts = _own_mounts()
    repo_src = mounts.get(str(INSTALL_DIR)) or str(HOST_INSTALL_DIR)
    data_src = mounts.get("/app/data") or DATA_VOLUME
    sock_src = mounts.get("/var/run/docker.sock") or "/var/run/docker.sock"

    if not repo_src:
        raise RuntimeError(
            f"Не удалось определить путь к репозиторию на хосте (mount {INSTALL_DIR}). "
            "Проверьте docker-compose.yml: каталог установки должен быть смонтирован в backend."
        )

    subprocess.run([docker_bin, "rm", "-f", UPDATER_NAME], capture_output=True, text=True)
    # docker:27-cli — Alpine, только /bin/sh; git/curl ставим через apk.
    inner = (
        "apk add --no-cache git curl >/dev/null 2>&1; "
        f"sh {UPDATE_SCRIPT}"
    )
    cmd = [
        docker_bin,
        "run",
        "-d",
        "--name",
        UPDATER_NAME,
        "--network",
        "host",
        "-v",
        f"{repo_src}:{INSTALL_DIR}",
        "-v",
        f"{data_src}:/app/data",
        "-v",
        f"{sock_src}:/var/run/docker.sock",
        "-w",
        str(INSTALL_DIR),
        "-e",
        f"INSTALL_DIR={INSTALL_DIR}",
        "-e",
        "DATA_DIR=/app/data",
        "-e",
        f"PANEL_HEALTH_URL={HEALTH_URL}",
        "-e",
        f"PANEL_REPO={PANEL_REPO}",
        "-e",
        f"PANEL_BRANCH={PANEL_BRANCH}",
        # Чтобы пересобранный backend получил тот же bind-mount каталога установки.
        "-e",
        f"PANEL_HOST_DIR={repo_src}",
    ]
    if target_tag:
        cmd.extend(["-e", f"PANEL_TARGET_TAG={target_tag}"])
    if github_token:
        cmd.extend(["-e", f"GITHUB_TOKEN={github_token}"])
    cmd.extend([UPDATER_IMAGE, "sh", "-c", inner])

    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(res.stderr.strip() or res.stdout.strip() or "docker run не удался")

    container_id = res.stdout.strip()
    # Быстрая проверка: контейнер не упал сразу (например, образ не скачался).
    if not container_id:
        raise RuntimeError("docker run не вернул id контейнера обновления.")
    return container_id


class PanelUpdateService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_running_job(self) -> Optional[PanelJob]:
        result = await self.session.execute(
            select(PanelJob).where(PanelJob.type == "update", PanelJob.status == "running").limit(1)
        )
        job = result.scalar_one_or_none()
        if not job:
            return None
        return await self._sync_from_files(job)

    async def start_update(self) -> PanelJob:
        if not panel_update_capable():
            raise ValueError("Обновление из панели доступно только на VPS с Docker.")
        running = await self.get_running_job()
        if running:
            raise ValueError("Обновление уже выполняется.")

        settings = PanelSettingsService(self.session)
        # Токен опционален: для публичного репозитория обновление идёт анонимно.
        github_token = await settings.get_github_token() or os.getenv("GITHUB_TOKEN", "")
        target_tag = await get_latest_tag(github_token) or ""

        if LOCK_FILE.exists():
            try:
                LOCK_FILE.unlink()
            except OSError:
                raise ValueError("Обновление уже выполняется (lock).") from None

        for path in (UPDATE_STATE, UPDATE_LOG):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass

        job = PanelJob(
            type="update",
            status="running",
            progress=5,
            started_at=datetime.now(timezone.utc),
            log="Запускаю обновление в отдельном контейнере…",
        )
        self.session.add(job)
        await self.session.commit()
        await self.session.refresh(job)

        try:
            container_id = _launch_updater_container(github_token=github_token, target_tag=target_tag)
            job.rollback_ref = container_id[:255]
            await self.session.commit()
        except Exception as exc:  # noqa: BLE001
            job.status = "failed_manual"
            job.progress = 100
            job.log = f"Не удалось запустить обновление: {exc}"
            job.finished_at = datetime.now(timezone.utc)
            await self.session.commit()
            raise ValueError(f"Не удалось запустить обновление: {exc}") from exc

        return job

    async def cancel_running_job(self, reason: str = "Обновление отменено.") -> Optional[PanelJob]:
        job = await self.get_running_job()
        if not job:
            return None

        docker_bin = resolve_docker_bin()
        if docker_bin:
            subprocess.run([docker_bin, "rm", "-f", UPDATER_NAME], capture_output=True, text=True)

        try:
            LOCK_FILE.unlink(missing_ok=True)
        except OSError:
            pass

        job.status = "failed_manual"
        job.progress = 100
        job.finished_at = datetime.now(timezone.utc)
        job.log = f"{job.log or ''}\n{reason}".strip()
        await self.session.commit()
        return job

    async def get_job(self, job_id: uuid.UUID) -> Optional[PanelJob]:
        job = await self.session.get(PanelJob, job_id)
        if not job:
            return None
        return await self._sync_from_files(job)

    async def _mark_job_failed(self, job: PanelJob, message: str, logs: str = "") -> PanelJob:
        job.status = "failed_manual"
        job.progress = 100
        job.finished_at = datetime.now(timezone.utc)
        parts = [job.log or "", message, logs]
        job.log = "\n".join(part for part in parts if part).strip()[-8000:]
        try:
            LOCK_FILE.unlink(missing_ok=True)
        except OSError:
            pass
        await self.session.commit()
        return job

    async def _sync_from_files(self, job: PanelJob) -> PanelJob:
        if job.status in TERMINAL_STATUSES:
            return job

        changed = False
        state = read_update_state()
        if state:
            status = state.get("status")
            if status and status != job.status:
                job.status = status
                changed = True
            progress = state.get("progress")
            if isinstance(progress, int) and progress != job.progress:
                job.progress = progress
                changed = True
            if status in TERMINAL_STATUSES and not job.finished_at:
                job.finished_at = datetime.now(timezone.utc)
                changed = True

        if UPDATE_LOG.exists():
            try:
                log_text = UPDATE_LOG.read_text(encoding="utf-8", errors="replace")[-8000:]
                if log_text and log_text != job.log:
                    job.log = log_text
                    changed = True
            except OSError:
                pass

        if job.status == "running":
            container_status = _updater_container_status()
            if container_status in {"exited", "dead"}:
                logs = _updater_container_logs()
                message = state.get("message") if state else ""
                if not message:
                    exit_code = _docker_inspect("{{.State.ExitCode}}", UPDATER_NAME) or "?"
                    message = f"Контейнер обновления завершился с кодом {exit_code}."
                return await self._mark_job_failed(job, message, logs)

            if job.started_at:
                age_sec = (datetime.now(timezone.utc) - job.started_at).total_seconds()
                if age_sec > 1800 and not state and not UPDATE_LOG.exists():
                    logs = _updater_container_logs()
                    return await self._mark_job_failed(
                        job,
                        "Обновление зависло: нет прогресса более 30 минут.",
                        logs,
                    )

        if changed:
            await self.session.commit()
        return job
