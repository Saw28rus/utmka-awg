import asyncio
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import client_ip, get_current_user, require_admin
from app.db.session import get_db
from app.schemas.auth import CurrentUser
from app.schemas.clients import EndpointUpdate, TransportReissueResult
from app.schemas.servers import (
    ChatDomainInstallResult,
    ChatDomainStatus,
    ChatDomainVerifyRequest,
    ChatDomainVerifyResult,
    ContainerActionRequest,
    DetectPreviewRequest,
    DetectResult,
    PanelHardenApplyRequest,
    PanelHardenResult,
    PanelHardenStatus,
    PanelSslAutoInstallRequest,
    PanelSslInstallRequest,
    PanelSslInstallResult,
    PanelSslStatus,
    PanelSslVerifyRequest,
    PanelSslVerifyResult,
    ProtocolActionRequest,
    ProtocolInstallRequest,
    ProtocolInstallResult,
    SecurityActionRequest,
    SecurityActionResult,
    ServerCreate,
    ServerListItem,
    ServerMetrics,
    ServerMinimal,
    ServerOverview,
    ServerRead,
    UfwPreviewResult,
)
from app.schemas.awg_masking import (
    MaskingApplyRequest,
    MaskingApplyResponse,
    MaskingPreset,
    MaskingPreviewRequest,
    MaskingPreviewResponse,
    MaskingResponse,
    MaskingRollbackRequest,
    MaskingSnapshotInfo,
    RotationPolicy,
    RotationPolicyUpdate,
)
from app.services.audit_service import AuditService
from app.services.awg_detect import run_awg_detect
from app.services.awg_import import run_awg_import
from app.services.awg_masking import read_masking
from app.services.awg_masking_apply import (
    apply_rotation,
    list_presets,
    list_snapshots,
    preview_rotation,
    rollback_rotation,
)
from app.services.awg_transport import apply_endpoint
from app.services.metrics import get_all_server_metrics, get_server_metrics
from app.services.server_overview import (
    get_container_logs,
    get_server_overview,
    run_container_action,
    run_protocol_action,
)
from app.services.chat_domain import (
    install_chat_domain_auto,
    ChatDomainError,
    disable_chat_domain,
    get_chat_domain_state,
    install_chat_domain,
    verify_chat_domain,
)
from app.services.panel_harden import (
    PanelHardenError,
    apply_harden,
    disable_harden,
    get_harden_state,
)
from app.services.panel_settings_service import PanelSettingsService
from app.services.server_hardening import (
    HardeningError,
    run_action as run_security_action,
    ufw_preview,
)
from app.services.panel_ssl import (
    PanelSslError,
    get_panel_ssl_status,
    install_panel_ssl,
    install_panel_ssl_auto,
    rollback_panel_ssl,
    verify_panel_domain,
)
from app.services.xray_install import XrayInstallError
from app.services.awg_install import AwgInstallError
from app.services.wireguard_install import WireguardInstallError, install_wireguard
from app.services.telemt_install import TelemtInstallError, install_telemt
from app.services.protocol_engine import get_engine
from app.services.protocol_versions import record_install, reconcile_node
from app.services.server_store import server_store


router = APIRouter()


@router.get("/minimal", response_model=list[ServerMinimal])
async def list_servers_minimal(
    _: CurrentUser = Depends(get_current_user),
) -> list[ServerMinimal]:
    return [
        ServerMinimal(
            id=s.id,
            name=s.name,
            host=s.host,
            status=s.status,
            protocols=s.protocols,
        )
        for s in server_store.list()
    ]


@router.get("", response_model=list[ServerListItem])
async def list_servers(_: CurrentUser = Depends(require_admin)) -> list[ServerListItem]:
    return server_store.list()


@router.get("/metrics", response_model=list[ServerMetrics])
async def all_server_metrics(
    refresh: bool = False,
    _: CurrentUser = Depends(require_admin),
) -> list[ServerMetrics]:
    """Метрики всех серверов одним запросом (параллельный SSH + кэш)."""
    return await asyncio.to_thread(get_all_server_metrics, refresh=refresh)


@router.post("", response_model=ServerRead)
async def create_server(
    payload: ServerCreate,
    _: CurrentUser = Depends(require_admin),
) -> ServerRead:
    server = server_store.create(payload, message="Сервер добавлен после detect.")
    if payload.detect_branch == "import":
        imported_count, vpn_port = await asyncio.to_thread(
            run_awg_import, server.id, server.name, payload
        )
        if imported_count:
            return server_store.update_after_import(
                server.id,
                imported_count,
                f"Импортировано клиентов: {imported_count}. Ключи на VPS не менялись.",
                vpn_port=vpn_port,
            )
        return server_store.update_after_import(
            server.id,
            payload.active_peers,
            "Сервер подключен. Клиенты в конфиге не найдены — проверь awg0.conf.",
            vpn_port=vpn_port,
        )
    return server


@router.get("/{server_id}", response_model=ServerRead)
async def get_server(server_id: str, _: CurrentUser = Depends(require_admin)) -> ServerRead:
    server = server_store.get(server_id)
    if not server:
        raise HTTPException(status_code=404, detail="Сервер не найден.")
    return server


@router.delete("/{server_id}")
async def delete_server(server_id: str, _: CurrentUser = Depends(require_admin)) -> dict:
    if not server_store.get_record(server_id):
        raise HTTPException(status_code=404, detail="Сервер не найден.")

    from app.services.channel_store import describe_blocking_cascades

    blocking = describe_blocking_cascades(server_id)
    if blocking:
        raise HTTPException(
            status_code=409,
            detail=(
                "Узел занят в активном канале: "
                + "; ".join(blocking)
                + ". Сначала выключите каскад(ы), затем удаляйте сервер."
            ),
        )

    if not server_store.delete(server_id):
        raise HTTPException(status_code=404, detail="Сервер не найден.")
    from app.services.metrics_cache import metrics_cache
    from app.services.protocol_backup import forget_node as forget_snapshots
    from app.services.protocol_versions import forget_node

    from app.services.cascade_store import cascade_store
    from app.services.xray_cascade_store import xray_cascade_store
    from app.services.health_store import health_store

    metrics_cache.invalidate(server_id)
    forget_node(server_id)
    forget_snapshots(server_id)
    cascade_store.forget_server(server_id)
    xray_cascade_store.forget_server(server_id)
    health_store.forget(server_id)
    from app.services.dpi_store import dpi_store
    from app.services.notification_store import notification_store
    from app.services.rotation_policy_store import rotation_policy_store

    notification_store.forget_server(server_id)
    dpi_store.forget_server(server_id)
    rotation_policy_store.forget_server(server_id)
    return {"status": "ok"}


@router.get("/{server_id}/metrics", response_model=ServerMetrics)
async def server_metrics(
    server_id: str,
    refresh: bool = False,
    _: CurrentUser = Depends(require_admin),
) -> ServerMetrics:
    if not server_store.get_record(server_id):
        raise HTTPException(status_code=404, detail="Сервер не найден.")
    return await asyncio.to_thread(get_server_metrics, server_id, refresh=refresh)


@router.get("/{server_id}/overview", response_model=ServerOverview)
async def server_overview(
    server_id: str, _: CurrentUser = Depends(require_admin)
) -> ServerOverview:
    if not server_store.get_record(server_id):
        raise HTTPException(status_code=404, detail="Сервер не найден.")
    return await asyncio.to_thread(get_server_overview, server_id)


@router.post("/{server_id}/containers/{container}/action")
async def container_action(
    server_id: str,
    container: str,
    payload: ContainerActionRequest,
    _: CurrentUser = Depends(require_admin),
) -> dict:
    try:
        message = await asyncio.to_thread(run_container_action, server_id, container, payload.action)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    return {"status": "ok", "message": message}


@router.get("/{server_id}/containers/{container}/logs")
async def container_logs(
    server_id: str,
    container: str,
    tail: int = 200,
    _: CurrentUser = Depends(require_admin),
) -> dict:
    try:
        logs = await asyncio.to_thread(get_container_logs, server_id, container, tail)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"container": container, "logs": logs}


@router.post("/{server_id}/protocols/{protocol_id}/action")
async def protocol_action(
    server_id: str,
    protocol_id: str,
    payload: ProtocolActionRequest,
    _: CurrentUser = Depends(require_admin),
) -> dict:
    try:
        message = await asyncio.to_thread(run_protocol_action, server_id, protocol_id, payload.action)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    return {"status": "ok", "message": message}


@router.get("/protocols/capabilities")
async def protocols_capabilities(
    _: CurrentUser = Depends(require_admin),
) -> dict:
    """Возможности движков по протоколам (UI прячет неподдержанные кнопки).

    Аддитивный эндпоинт: поведение существующих операций не меняет.
    """
    from dataclasses import asdict

    from app.services.protocol_engine import EngineNotSupported

    result: dict[str, dict] = {}
    for pid in ("awg2", "awg_legacy", "xray"):
        try:
            result[pid] = asdict(get_engine(pid).capabilities())
        except EngineNotSupported:
            continue
    return {"protocols": result}


@router.get("/{server_id}/protocols/versions")
async def protocol_versions(
    server_id: str,
    _: CurrentUser = Depends(require_admin),
) -> dict:
    """Сверка версий протоколов узла: pinned (что шипит панель) vs installed.

    Делает live-reconcile (SSH к узлу): какие протоколы есть, что запущено,
    актуальна ли версия. Не меняет конфиги — только читает статус.
    """
    if not server_store.get_record(server_id):
        raise HTTPException(status_code=404, detail="Сервер не найден.")
    items = await asyncio.to_thread(reconcile_node, server_id)
    return {"protocols": items}


@router.get("/{server_id}/xray/masking")
async def xray_masking(
    server_id: str,
    _: CurrentUser = Depends(require_admin),
) -> dict:
    """Инспектор Reality-маскировки Xray (read-only): dest/SNI/shortId/порт + scoring."""
    if not server_store.get_record(server_id):
        raise HTTPException(status_code=404, detail="Сервер не найден.")
    from app.services.xray_masking import inspect_xray_masking

    return await asyncio.to_thread(inspect_xray_masking, server_id)


@router.post("/{server_id}/xray/masking/domain")
async def xray_masking_set_domain(
    server_id: str,
    payload: dict,
    _: CurrentUser = Depends(require_admin),
) -> dict:
    """Сменить домен маскировки Reality (dest+SNI) с переизданием клиентов."""
    if not server_store.get_record(server_id):
        raise HTTPException(status_code=404, detail="Сервер не найден.")
    from app.services.xray_masking_apply import MaskingApplyError, set_masking_domain

    site = (payload or {}).get("site") or ""
    try:
        return await asyncio.to_thread(set_masking_domain, server_id, site)
    except MaskingApplyError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/{server_id}/xray/masking/short-id")
async def xray_masking_add_short_id(
    server_id: str,
    _: CurrentUser = Depends(require_admin),
) -> dict:
    """Добавить новый shortId в Reality (без переиздания клиентов)."""
    if not server_store.get_record(server_id):
        raise HTTPException(status_code=404, detail="Сервер не найден.")
    from app.services.xray_masking_apply import MaskingApplyError, add_short_id

    try:
        return await asyncio.to_thread(add_short_id, server_id)
    except MaskingApplyError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/{server_id}/protocols/{protocol_id}/update-plan")
async def protocol_update_plan(
    server_id: str,
    protocol_id: str,
    _: CurrentUser = Depends(require_admin),
) -> dict:
    """План обновления протокола (installed→pinned, статус снапшота, шаги). Read-only."""
    if not server_store.get_record(server_id):
        raise HTTPException(status_code=404, detail="Сервер не найден.")
    from app.services.protocol_backup import update_plan

    return await asyncio.to_thread(update_plan, server_id, protocol_id)


@router.post("/{server_id}/protocols/{protocol_id}/snapshot")
async def protocol_snapshot(
    server_id: str,
    protocol_id: str,
    _: CurrentUser = Depends(require_admin),
) -> dict:
    """Снять зашифрованный снапшот конфига/ключей протокола (read-only на узле)."""
    if not server_store.get_record(server_id):
        raise HTTPException(status_code=404, detail="Сервер не найден.")
    from app.services.protocol_backup import SnapshotError, snapshot_protocol

    try:
        snapshot = await asyncio.to_thread(snapshot_protocol, server_id, protocol_id)
    except SnapshotError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"status": "ok", "snapshot": snapshot}


@router.post("/{server_id}/protocols/{protocol_id}/update")
async def protocol_update(
    server_id: str,
    protocol_id: str,
    _: CurrentUser = Depends(require_admin),
) -> dict:
    """Управляемое обновление протокола до pinned-версии (snapshot→build→recreate→health→rollback)."""
    if not server_store.get_record(server_id):
        raise HTTPException(status_code=404, detail="Сервер не найден.")
    from app.services.protocol_engine import EngineNotSupported
    from app.services.protocol_update import UpdateError

    try:
        result = await asyncio.to_thread(get_engine(protocol_id).update, server_id)
    except EngineNotSupported as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except UpdateError as exc:
        raise HTTPException(
            status_code=400,
            detail={"message": str(exc), "rolled_back": exc.rolled_back},
        )
    return {
        "status": "ok",
        "message": result.message,
        "container": result.container,
        "protocol": result.protocol,
        "from_version": result.from_version,
        "to_version": result.to_version,
        "rolled_back": result.rolled_back,
    }


@router.post("/{server_id}/protocols/{protocol_id}/install", response_model=ProtocolInstallResult)
async def protocol_install(
    server_id: str,
    protocol_id: str,
    payload: ProtocolInstallRequest,
    _: CurrentUser = Depends(require_admin),
) -> ProtocolInstallResult:
    if not server_store.get_record(server_id):
        raise HTTPException(status_code=404, detail="Сервер не найден.")
    try:
        if protocol_id == "xray":
            result = await asyncio.to_thread(
                get_engine("xray").install,
                server_id,
                port=payload.port,
                site_name=payload.site_name,
                transport=payload.transport,
            )
            record_install(server_id, "xray")
            return ProtocolInstallResult(
                message=result.message,
                container=result.container,
                port=result.port,
                site_name=result.site_name,
                client_uuid=result.client_uuid,
                public_key=result.public_key,
                short_id=result.short_id,
                transport=result.transport,
            )
        if protocol_id in ("awg2", "awg_legacy"):
            result = await asyncio.to_thread(
                get_engine(protocol_id).install,
                server_id,
                port=payload.port,
            )
            record_install(server_id, protocol_id)
            return ProtocolInstallResult(
                message=result.message,
                container=result.container,
                port=result.port,
                public_key=result.public_key,
            )
        if protocol_id == "wireguard":
            result = await asyncio.to_thread(
                install_wireguard,
                server_id,
                port=payload.port,
            )
            return ProtocolInstallResult(
                message=result.message,
                container=result.container,
                port=result.port,
                public_key=result.public_key,
            )
        if protocol_id == "telemt":
            result = await asyncio.to_thread(
                install_telemt,
                server_id,
                port=payload.port,
                tls_domain=payload.site_name,
            )
            record_install(server_id, "telemt")
            return ProtocolInstallResult(
                message=result.message,
                container=result.container,
                port=result.port,
                site_name=result.tls_domain,
                secret=result.secret,
                tg_link=result.tg_link,
            )
    except (XrayInstallError, AwgInstallError, WireguardInstallError, TelemtInstallError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    raise HTTPException(status_code=400, detail="Установка этого протокола пока не поддерживается.")


@router.post("/detect-preview", response_model=DetectResult)
async def detect_preview(
    payload: DetectPreviewRequest,
    _: CurrentUser = Depends(require_admin),
) -> DetectResult:
    return await asyncio.to_thread(run_awg_detect, payload)


@router.get("/{server_id}/panel-ssl/status", response_model=PanelSslStatus)
async def panel_ssl_status(
    server_id: str, _: CurrentUser = Depends(require_admin)
) -> PanelSslStatus:
    if not server_store.get_record(server_id):
        raise HTTPException(status_code=404, detail="Сервер не найден.")
    try:
        status = await asyncio.to_thread(get_panel_ssl_status, server_id)
    except PanelSslError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return PanelSslStatus(**status.__dict__)


@router.post("/{server_id}/panel-ssl/verify", response_model=PanelSslVerifyResult)
async def panel_ssl_verify(
    server_id: str,
    payload: PanelSslVerifyRequest,
    _: CurrentUser = Depends(require_admin),
) -> PanelSslVerifyResult:
    if not server_store.get_record(server_id):
        raise HTTPException(status_code=404, detail="Сервер не найден.")
    try:
        result = await asyncio.to_thread(verify_panel_domain, server_id, payload.domain)
    except PanelSslError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return PanelSslVerifyResult(**result.__dict__)


@router.post("/{server_id}/panel-ssl/install", response_model=PanelSslInstallResult)
async def panel_ssl_install(
    server_id: str,
    payload: PanelSslInstallRequest,
    _: CurrentUser = Depends(require_admin),
) -> PanelSslInstallResult:
    if not server_store.get_record(server_id):
        raise HTTPException(status_code=404, detail="Сервер не найден.")
    try:
        result = await asyncio.to_thread(
            install_panel_ssl,
            server_id,
            payload.domain,
            email=payload.email,
        )
    except PanelSslError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return PanelSslInstallResult(**result.__dict__)


@router.post("/{server_id}/panel-ssl/install-auto", response_model=PanelSslInstallResult)
async def panel_ssl_install_auto(
    server_id: str,
    payload: PanelSslAutoInstallRequest,
    _: CurrentUser = Depends(require_admin),
) -> PanelSslInstallResult:
    if not server_store.get_record(server_id):
        raise HTTPException(status_code=404, detail="Сервер не найден.")
    try:
        result = await asyncio.to_thread(
            install_panel_ssl_auto,
            server_id,
            email=payload.email,
        )
    except PanelSslError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return PanelSslInstallResult(**result.__dict__)


@router.post("/{server_id}/panel-ssl/rollback")
async def panel_ssl_rollback(
    server_id: str, _: CurrentUser = Depends(require_admin)
) -> dict:
    if not server_store.get_record(server_id):
        raise HTTPException(status_code=404, detail="Сервер не найден.")
    try:
        message = await asyncio.to_thread(rollback_panel_ssl, server_id)
    except PanelSslError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"status": "ok", "message": message}


@router.get("/{server_id}/chat-domain/status", response_model=ChatDomainStatus)
async def chat_domain_status(
    server_id: str, _: CurrentUser = Depends(require_admin)
) -> ChatDomainStatus:
    if not server_store.get_record(server_id):
        raise HTTPException(status_code=404, detail="Сервер не найден.")
    try:
        state = await asyncio.to_thread(get_chat_domain_state, server_id)
    except ChatDomainError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return ChatDomainStatus(**state.__dict__)


@router.post("/{server_id}/chat-domain/verify", response_model=ChatDomainVerifyResult)
async def chat_domain_verify(
    server_id: str,
    payload: ChatDomainVerifyRequest,
    _: CurrentUser = Depends(require_admin),
) -> ChatDomainVerifyResult:
    if not server_store.get_record(server_id):
        raise HTTPException(status_code=404, detail="Сервер не найден.")
    try:
        result = await asyncio.to_thread(verify_chat_domain, server_id, payload.domain)
    except ChatDomainError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return ChatDomainVerifyResult(**result.__dict__)


@router.post("/{server_id}/chat-domain/install", response_model=ChatDomainInstallResult)
async def chat_domain_install(
    server_id: str,
    payload: ChatDomainVerifyRequest,
    request: Request,
    admin: CurrentUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> ChatDomainInstallResult:
    if not server_store.get_record(server_id):
        raise HTTPException(status_code=404, detail="Сервер не найден.")
    try:
        result = await asyncio.to_thread(install_chat_domain, server_id, payload.domain)
    except ChatDomainError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    await _post_chat_install(db, server_id, result, admin, request)
    return ChatDomainInstallResult(**result.__dict__)


@router.post("/{server_id}/chat-domain/install-auto", response_model=ChatDomainInstallResult)
async def chat_domain_install_auto(
    server_id: str,
    request: Request,
    admin: CurrentUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> ChatDomainInstallResult:
    if not server_store.get_record(server_id):
        raise HTTPException(status_code=404, detail="Сервер не найден.")
    try:
        result = await asyncio.to_thread(install_chat_domain_auto, server_id)
    except ChatDomainError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    await _post_chat_install(db, server_id, result, admin, request)
    return ChatDomainInstallResult(**result.__dict__)


async def _post_chat_install(db, server_id, result, admin, request) -> None:
    settings = PanelSettingsService(db)
    await settings.set_many(
        {
            "chat_domain": result.domain,
            "chat_enabled": "true",
            "chat_ssl_status": "cert_active",
            "chat_public_url": result.public_url,
        }
    )
    await AuditService(db).log(
        "chat_enabled",
        user_id=uuid.UUID(admin.id),
        user_email=admin.email,
        target_type="server",
        target_id=server_id,
        detail={"domain": result.domain, "public_url": result.public_url},
        ip=client_ip(request),
    )


@router.post("/{server_id}/chat-domain/disable")
async def chat_domain_disable(
    server_id: str,
    request: Request,
    admin: CurrentUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    if not server_store.get_record(server_id):
        raise HTTPException(status_code=404, detail="Сервер не найден.")
    try:
        message = await asyncio.to_thread(disable_chat_domain, server_id)
    except ChatDomainError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    settings = PanelSettingsService(db)
    await settings.set_many({"chat_enabled": "false", "chat_ssl_status": "disabled"})
    audit = AuditService(db)
    await audit.log(
        "chat_disabled",
        user_id=uuid.UUID(admin.id),
        user_email=admin.email,
        target_type="server",
        target_id=server_id,
        detail={},
        ip=client_ip(request),
    )
    return {"status": "ok", "message": message}


@router.get("/{server_id}/panel-harden/status", response_model=PanelHardenStatus)
async def panel_harden_status(
    server_id: str,
    request: Request,
    _: CurrentUser = Depends(require_admin),
) -> PanelHardenStatus:
    if not server_store.get_record(server_id):
        raise HTTPException(status_code=404, detail="Сервер не найден.")
    try:
        state = await asyncio.to_thread(get_harden_state, server_id)
    except PanelHardenError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return PanelHardenStatus(**state.__dict__, your_ip=client_ip(request))


@router.post("/{server_id}/panel-harden/apply", response_model=PanelHardenResult)
async def panel_harden_apply(
    server_id: str,
    payload: PanelHardenApplyRequest,
    request: Request,
    admin: CurrentUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> PanelHardenResult:
    if not server_store.get_record(server_id):
        raise HTTPException(status_code=404, detail="Сервер не найден.")
    caller = client_ip(request)
    try:
        result = await asyncio.to_thread(
            apply_harden,
            server_id,
            payload.allowed_ips,
            caller_ip=caller,
            force=payload.force,
        )
    except PanelHardenError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    audit = AuditService(db)
    await audit.log(
        "panel_harden_apply",
        user_id=uuid.UUID(admin.id),
        user_email=admin.email,
        target_type="server",
        target_id=server_id,
        detail={"allowed_ips": result.allowed_ips, "force": payload.force},
        ip=caller,
    )
    return PanelHardenResult(**result.__dict__)


@router.post("/{server_id}/panel-harden/disable", response_model=PanelHardenResult)
async def panel_harden_disable(
    server_id: str,
    request: Request,
    admin: CurrentUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> PanelHardenResult:
    if not server_store.get_record(server_id):
        raise HTTPException(status_code=404, detail="Сервер не найден.")
    try:
        result = await asyncio.to_thread(disable_harden, server_id)
    except PanelHardenError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    audit = AuditService(db)
    await audit.log(
        "panel_harden_disable",
        user_id=uuid.UUID(admin.id),
        user_email=admin.email,
        target_type="server",
        target_id=server_id,
        detail={},
        ip=client_ip(request),
    )
    return PanelHardenResult(**result.__dict__)


@router.get("/{server_id}/security/ufw-preview", response_model=UfwPreviewResult)
async def security_ufw_preview(
    server_id: str, _: CurrentUser = Depends(require_admin)
) -> UfwPreviewResult:
    if not server_store.get_record(server_id):
        raise HTTPException(status_code=404, detail="Сервер не найден.")
    try:
        preview = await asyncio.to_thread(ufw_preview, server_id)
    except HardeningError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return UfwPreviewResult(**preview.__dict__)


@router.post("/{server_id}/security/action", response_model=SecurityActionResult)
async def security_action(
    server_id: str,
    payload: SecurityActionRequest,
    request: Request,
    admin: CurrentUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> SecurityActionResult:
    if not server_store.get_record(server_id):
        raise HTTPException(status_code=404, detail="Сервер не найден.")
    caller = client_ip(request)
    try:
        result = await asyncio.to_thread(
            run_security_action,
            server_id,
            payload.control,
            payload.action,
            caller_ip=caller,
        )
    except HardeningError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    audit = AuditService(db)
    await audit.log(
        "security_action",
        user_id=uuid.UUID(admin.id),
        user_email=admin.email,
        target_type="server",
        target_id=server_id,
        detail={"control": payload.control, "action": payload.action, "ok": result.ok},
        ip=caller,
    )
    return SecurityActionResult(**result.__dict__)


@router.post("/{server_id}/test-ssh")
async def test_ssh(server_id: str, _: CurrentUser = Depends(require_admin)) -> dict:
    metrics = await asyncio.to_thread(get_server_metrics, server_id)
    return {
        "server_id": server_id,
        "status": metrics.status,
        "online": metrics.online,
        "message": metrics.message or "Сервер отвечает.",
    }


@router.get("/{server_id}/awg/masking", response_model=MaskingResponse)
async def awg_masking_state(
    server_id: str, _: CurrentUser = Depends(require_admin)
) -> MaskingResponse:
    if not server_store.get_record(server_id):
        raise HTTPException(status_code=404, detail="Сервер не найден.")
    return await asyncio.to_thread(read_masking, server_id)


@router.post("/{server_id}/awg/masking/check", response_model=MaskingResponse)
async def awg_masking_check(
    server_id: str, _: CurrentUser = Depends(require_admin)
) -> MaskingResponse:
    if not server_store.get_record(server_id):
        raise HTTPException(status_code=404, detail="Сервер не найден.")
    return await asyncio.to_thread(read_masking, server_id)


@router.get("/{server_id}/awg/masking/presets", response_model=list[MaskingPreset])
async def awg_masking_presets(
    server_id: str, _: CurrentUser = Depends(require_admin)
) -> list[MaskingPreset]:
    if not server_store.get_record(server_id):
        raise HTTPException(status_code=404, detail="Сервер не найден.")
    return list_presets()


@router.get("/{server_id}/awg/masking/snapshots", response_model=list[MaskingSnapshotInfo])
async def awg_masking_snapshots(
    server_id: str, _: CurrentUser = Depends(require_admin)
) -> list[MaskingSnapshotInfo]:
    if not server_store.get_record(server_id):
        raise HTTPException(status_code=404, detail="Сервер не найден.")
    return [MaskingSnapshotInfo(**s) for s in list_snapshots(server_id)]


@router.post("/{server_id}/awg/masking/preview", response_model=MaskingPreviewResponse)
async def awg_masking_preview(
    server_id: str,
    payload: MaskingPreviewRequest,
    _: CurrentUser = Depends(require_admin),
) -> MaskingPreviewResponse:
    if not server_store.get_record(server_id):
        raise HTTPException(status_code=404, detail="Сервер не найден.")
    return await asyncio.to_thread(preview_rotation, server_id, payload.preset)


@router.post("/{server_id}/awg/masking/apply", response_model=MaskingApplyResponse)
async def awg_masking_apply(
    server_id: str,
    payload: MaskingApplyRequest,
    admin: CurrentUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> MaskingApplyResponse:
    if not server_store.get_record(server_id):
        raise HTTPException(status_code=404, detail="Сервер не найден.")
    result = await asyncio.to_thread(apply_rotation, server_id, payload.preset, payload.params)
    audit = AuditService(db)
    await audit.log(
        "masking_apply",
        user_id=uuid.UUID(admin.id),
        user_email=admin.email,
        target_type="server",
        target_id=server_id,
        detail={
            "preset": payload.preset,
            "ok": result.ok,
            "rolled_back": result.rolled_back,
            "snapshot_id": result.snapshot_id,
            "reissued": result.reissued,
            "error": result.error,
        },
    )
    return result


@router.post("/{server_id}/awg/endpoint", response_model=TransportReissueResult)
async def awg_endpoint_apply(
    server_id: str,
    payload: EndpointUpdate,
    admin: CurrentUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> TransportReissueResult:
    if not server_store.get_record(server_id):
        raise HTTPException(status_code=404, detail="Сервер не найден.")
    result = await asyncio.to_thread(apply_endpoint, server_id, payload.endpoint_host)
    if not result.ok:
        raise HTTPException(status_code=400, detail=result.error or "Не удалось применить endpoint.")
    audit = AuditService(db)
    await audit.log(
        "endpoint_change",
        user_id=uuid.UUID(admin.id),
        user_email=admin.email,
        target_type="server",
        target_id=server_id,
        detail={"endpoint_host": (payload.endpoint_host or "").strip() or None, "reissued": result.reissued},
    )
    return result


@router.get("/{server_id}/awg/masking/rotation", response_model=RotationPolicy)
async def awg_rotation_get(
    server_id: str, _: CurrentUser = Depends(require_admin)
) -> RotationPolicy:
    if not server_store.get_record(server_id):
        raise HTTPException(status_code=404, detail="Сервер не найден.")
    from app.services.rotation_policy_store import rotation_policy_store

    return RotationPolicy(**rotation_policy_store.get(server_id))


@router.put("/{server_id}/awg/masking/rotation", response_model=RotationPolicy)
async def awg_rotation_set(
    server_id: str,
    payload: RotationPolicyUpdate,
    admin: CurrentUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> RotationPolicy:
    if not server_store.get_record(server_id):
        raise HTTPException(status_code=404, detail="Сервер не найден.")
    from app.services.awg_masking_apply import PRESETS
    from app.services.rotation_policy_store import rotation_policy_store

    changes = payload.model_dump(exclude_unset=True)
    if "preset" in changes and changes["preset"] not in PRESETS:
        raise HTTPException(status_code=400, detail="Неизвестный пресет.")
    if "interval_days" in changes and not (1 <= int(changes["interval_days"]) <= 90):
        raise HTTPException(status_code=400, detail="Интервал должен быть 1–90 дней.")
    for hk in ("window_start", "window_end"):
        if hk in changes and not (0 <= int(changes[hk]) <= 23):
            raise HTTPException(status_code=400, detail="Час окна — 0–23.")
    policy = rotation_policy_store.set(server_id, **changes)
    await AuditService(db).log(
        "masking_rotation_policy",
        user_id=uuid.UUID(admin.id),
        user_email=admin.email,
        target_type="server",
        target_id=server_id,
        detail=changes,
    )
    return RotationPolicy(**policy)


@router.post("/{server_id}/awg/masking/rotation/run", response_model=MaskingApplyResponse)
async def awg_rotation_run(
    server_id: str,
    admin: CurrentUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> MaskingApplyResponse:
    if not server_store.get_record(server_id):
        raise HTTPException(status_code=404, detail="Сервер не найден.")
    from datetime import datetime, timezone

    from app.services.awg_masking_apply import PRESETS, generate_params
    from app.services.rotation_policy_store import rotation_policy_store

    preset = rotation_policy_store.get(server_id).get("preset") or "balance"
    if preset not in PRESETS:
        preset = "balance"
    params = generate_params(preset)
    result = await asyncio.to_thread(apply_rotation, server_id, preset, params)
    now_iso = datetime.now(timezone.utc).isoformat()
    rotation_policy_store.mark_rotated(
        server_id,
        when=now_iso,
        status="ok" if result.ok else "failed",
        error=None if result.ok else result.error,
    )
    await AuditService(db).log(
        "masking_rotation_manual",
        user_id=uuid.UUID(admin.id),
        user_email=admin.email,
        target_type="server",
        target_id=server_id,
        detail={"preset": preset, "ok": result.ok, "reissued": result.reissued, "error": result.error},
    )
    return result


@router.post("/{server_id}/awg/masking/rollback", response_model=MaskingApplyResponse)
async def awg_masking_rollback(
    server_id: str,
    payload: MaskingRollbackRequest,
    admin: CurrentUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> MaskingApplyResponse:
    if not server_store.get_record(server_id):
        raise HTTPException(status_code=404, detail="Сервер не найден.")
    result = await asyncio.to_thread(rollback_rotation, server_id, payload.snapshot_id)
    audit = AuditService(db)
    await audit.log(
        "masking_rollback",
        user_id=uuid.UUID(admin.id),
        user_email=admin.email,
        target_type="server",
        target_id=server_id,
        detail={
            "ok": result.ok,
            "snapshot_id": result.snapshot_id,
            "reissued": result.reissued,
            "error": result.error,
        },
    )
    return result
