from __future__ import annotations

from fastapi import APIRouter
from fastapi.templating import Jinja2Templates

from .core import router as core_router
from .results import router as results_router
from .config_routes import router as config_router
from .report import router as report_router

router = APIRouter()
router.include_router(core_router)
router.include_router(results_router)
router.include_router(config_router)
router.include_router(report_router)


def configure_templates(templates: Jinja2Templates) -> None:
    from . import core
    core.configure_templates(templates)
