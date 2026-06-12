from fastapi import APIRouter

from app.api.routes import (
    audit,
    auth,
    cascade,
    chat_admin,
    chat_client,
    clients,
    dashboard,
    health,
    invoices,
    operations,
    servers,
    settings,
    users,
)


api_router = APIRouter()
api_router.include_router(health.router, tags=["health"])
api_router.include_router(auth.router, prefix="/auth", tags=["auth"])
api_router.include_router(dashboard.router, prefix="/dashboard", tags=["dashboard"])
api_router.include_router(servers.router, prefix="/servers", tags=["servers"])
api_router.include_router(cascade.router, prefix="/servers", tags=["cascade"])
api_router.include_router(clients.router, prefix="/clients", tags=["clients"])
api_router.include_router(invoices.router, prefix="/invoices", tags=["invoices"])
api_router.include_router(users.router, prefix="/users", tags=["users"])
api_router.include_router(settings.router, prefix="/settings", tags=["settings"])
api_router.include_router(audit.router, prefix="/audit", tags=["audit"])
api_router.include_router(operations.router, prefix="/operations", tags=["operations"])
api_router.include_router(chat_admin.router, prefix="/chat/admin", tags=["chat-admin"])
api_router.include_router(chat_client.router, prefix="/chat/client", tags=["chat-client"])
