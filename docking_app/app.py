from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import state
from .config import BASE, DOCK_DIR, LIGAND_DIR, RECEPTOR_DIR, STATIC_DIR, TEMPLATES_DIR
from .ligand_3d.app import app as ligand3d_app
from .routes import configure_templates, router
from .services import _existing_files, _load_receptor_meta, _start_run
from .state import RUN_STATE


def _render_main_index() -> str:
    html = (TEMPLATES_DIR / "index.html").read_text(encoding="utf-8")
    return html.replace("{{ title }}", "DockUP")


def create_app() -> FastAPI:
    app = FastAPI(title="DockUP")
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
    configure_templates(templates)

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    def homepage() -> HTMLResponse:
        return HTMLResponse(_render_main_index())

    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    app.mount("/ligand-3d", ligand3d_app)
    app.include_router(router)
    return app


app = create_app()


def __getattr__(name: str):
    if name == "RUN_PROC":
        return state.RUN_PROC
    raise AttributeError(name)


__all__ = [
    "app",
    "create_app",
    "_start_run",
    "_existing_files",
    "_load_receptor_meta",
    "BASE",
    "DOCK_DIR",
    "LIGAND_DIR",
    "RECEPTOR_DIR",
    "RUN_STATE",
]
