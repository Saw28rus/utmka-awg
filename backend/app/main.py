from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.router import api_router
from app.core.config import settings
from app.db.bootstrap import bootstrap_database
from app.db.session import AsyncSessionLocal, engine
from app.services.panel_update import PANEL_VERSION_FILE
from app.workers.scheduler import shutdown_scheduler, start_scheduler


@asynccontextmanager
async def lifespan(_app: FastAPI):
    async with AsyncSessionLocal() as session:
        await bootstrap_database(session)
    start_scheduler()
    yield
    shutdown_scheduler()
    await engine.dispose()


def create_app() -> FastAPI:
    version = "0.1.0"
    if PANEL_VERSION_FILE.exists():
        version = PANEL_VERSION_FILE.read_text(encoding="utf-8").strip()

    app = FastAPI(
        title="UTMka+AWG API",
        description="Admin panel API for AmneziaWG 2.0 servers.",
        version=version,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url=f"{settings.api_v1_prefix}/openapi.json",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(api_router, prefix=settings.api_v1_prefix)

    @app.get("/")
    async def root() -> dict[str, str]:
        return {"name": settings.app_name, "status": "ok", "docs": "/docs", "version": version}

    return app


app = create_app()
