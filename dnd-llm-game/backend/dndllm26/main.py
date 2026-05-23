import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from dndllm26.api.routes import router
from dndllm26.core.settings import get_settings
from dndllm26.db.session import init_db


def create_app() -> FastAPI:
    settings = get_settings()
    init_db()
    app = FastAPI(title=settings.app_name)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(router, prefix="/api")
    return app


app = create_app()


def run() -> None:
    settings = get_settings()
    uvicorn.run("dndllm26.main:app", host=settings.api_host, port=settings.api_port, reload=True)


if __name__ == "__main__":
    run()
