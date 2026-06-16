import hashlib
import io
import json
import shutil
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.panel_settings import PanelSetting
from app.models.user import User
from app.services.persistence import DATA_DIR, write_json

BACKUP_VERSION = "1.0"


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _snapshot_dir(prefix: str) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    path = DATA_DIR / "backups" / f"{prefix}-{stamp}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _copy_json_data(target: Path) -> list[str]:
    copied: list[str] = []
    for src in DATA_DIR.glob("*.json"):
        dest = target / src.name
        shutil.copy2(src, dest)
        copied.append(src.name)
    return copied


async def create_backup_zip(session: AsyncSession, include_secrets: bool = False) -> bytes:
    users = (await session.execute(select(User))).scalars().all()
    settings = (await session.execute(select(PanelSetting))).scalars().all()

    users_payload = [
        {
            "id": str(u.id),
            "email": u.email,
            "password_hash": u.password_hash,
            "name": u.name,
            "role": u.role,
            "is_active": u.is_active,
            "theme": u.theme,
        }
        for u in users
    ]
    settings_payload = {s.key: s.value for s in settings}

    buffer = io.BytesIO()
    checksums: dict[str, str] = {}
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for json_file in DATA_DIR.glob("*.json"):
            data = json_file.read_bytes()
            zf.writestr(f"data/{json_file.name}", data)
            checksums[f"data/{json_file.name}"] = _sha256(data)

        users_bytes = json.dumps(users_payload, ensure_ascii=False, indent=2).encode()
        zf.writestr("postgres/users.json", users_bytes)
        checksums["postgres/users.json"] = _sha256(users_bytes)

        settings_bytes = json.dumps(settings_payload, ensure_ascii=False, indent=2).encode()
        zf.writestr("postgres/panel_settings.json", settings_bytes)
        checksums["postgres/panel_settings.json"] = _sha256(settings_bytes)

        if include_secrets:
            env_path = Path("/host/utmka-awg/.env")
            if env_path.exists():
                env_data = env_path.read_bytes()
                zf.writestr("secrets/.env", env_data)
                checksums["secrets/.env"] = _sha256(env_data)

        manifest = {
            "version": BACKUP_VERSION,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "checksums": checksums,
        }
        manifest_bytes = json.dumps(manifest, ensure_ascii=False, indent=2).encode()
        zf.writestr("manifest.json", manifest_bytes)

    return buffer.getvalue()


def _validate_manifest(zf: zipfile.ZipFile) -> dict[str, Any]:
    if "manifest.json" not in zf.namelist():
        raise ValueError("В архиве нет manifest.json.")
    manifest = json.loads(zf.read("manifest.json"))
    version = manifest.get("version", "")
    if not str(version).startswith("1."):
        raise ValueError(f"Несовместимая версия бэкапа: {version}")
    checksums = manifest.get("checksums", {})
    for name, expected in checksums.items():
        if name not in zf.namelist():
            raise ValueError(f"В архиве отсутствует файл: {name}")
        actual = _sha256(zf.read(name))
        if actual != expected:
            raise ValueError(f"Повреждённый файл в архиве: {name}")
    return manifest


async def restore_backup_zip(session: AsyncSession, payload: bytes) -> str:
    snapshot = _snapshot_dir("pre-restore")
    _copy_json_data(snapshot)

    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as zf:
            _validate_manifest(zf)
            for name in zf.namelist():
                if name.startswith("data/") and name.endswith(".json"):
                    dest_name = Path(name).name
                    write_json(dest_name, json.loads(zf.read(name)))

            if "postgres/users.json" in zf.namelist():
                users_data = json.loads(zf.read("postgres/users.json"))
                await session.execute(delete(User))
                for row in users_data:
                    session.add(
                        User(
                            id=uuid.UUID(row["id"]),
                            email=row["email"],
                            password_hash=row["password_hash"],
                            name=row["name"],
                            role=row["role"],
                            is_active=row["is_active"],
                            theme=row.get("theme", "dark"),
                        )
                    )

            if "postgres/panel_settings.json" in zf.namelist():
                settings_data = json.loads(zf.read("postgres/panel_settings.json"))
                await session.execute(delete(PanelSetting))
                for key, value in settings_data.items():
                    session.add(PanelSetting(key=key, value=value))

            await session.commit()
    except Exception:
        await session.rollback()
        for snap_file in snapshot.glob("*.json"):
            try:
                write_json(snap_file.name, json.loads(snap_file.read_text(encoding="utf-8")))
            except (json.JSONDecodeError, OSError):
                continue
        raise

    return str(snapshot)
