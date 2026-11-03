from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.answering.router import router as questions_router
from app.config import get_settings
from app.documents.router import router as documents_router
from app.errors import register_error_handlers
from app.logging import configure_logging
from app.telemetry import setup_telemetry


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)

    # Tracing comes from opentelemetry-instrumentation-fastapi (excludes /health); FastAPI's native
    # telemetry would trace every app once a provider exists, and its logs carry exception text.
    app = FastAPI(title="Tramontana RAG", telemetry={"tracing": False, "logs": False})
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    register_error_handlers(app)
    app.include_router(documents_router)
    app.include_router(questions_router)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    setup_telemetry("tramontana-api", app=app)
    return app
