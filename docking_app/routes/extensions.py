from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse, StreamingResponse

from ..extensions import gemini_agent, ollama_agent, vina_gpu_21
from ..helpers import normalize_docking_config
from ..state import STATE, save_state_cache

router = APIRouter(prefix="/api/extensions", tags=["extensions"])


@router.get("/vina-gpu-21/status")
def vina_gpu_21_status() -> JSONResponse:
    return JSONResponse(vina_gpu_21.status())


@router.post("/vina-gpu-21/install")
def vina_gpu_21_install() -> JSONResponse:
    return JSONResponse(vina_gpu_21.start_install())


@router.post("/vina-gpu-21/test")
def vina_gpu_21_test() -> JSONResponse:
    return JSONResponse(vina_gpu_21.start_test())


@router.post("/vina-gpu-21/uninstall")
def vina_gpu_21_uninstall() -> JSONResponse:
    if normalize_docking_config(STATE.get("docking_config") or {}).get("docking_engine") == "vina_gpu_21":
        STATE["docking_config"] = normalize_docking_config({**(STATE.get("docking_config") or {}), "docking_engine": "vina"})
        save_state_cache()
    return JSONResponse(vina_gpu_21.start_uninstall())


@router.post("/vina-gpu-21/use-default")
def vina_gpu_21_use_default() -> JSONResponse:
    cfg = normalize_docking_config({**(STATE.get("docking_config") or {}), "docking_engine": "vina_gpu_21"})
    STATE["docking_config"] = cfg
    save_state_cache()
    return JSONResponse({"ok": True, "docking_config": cfg})


@router.post("/vina/use-default")
def vina_use_default() -> JSONResponse:
    cfg = normalize_docking_config({**(STATE.get("docking_config") or {}), "docking_engine": "vina"})
    STATE["docking_config"] = cfg
    save_state_cache()
    return JSONResponse({"ok": True, "docking_config": cfg})


@router.get("/ollama/status")
def ollama_status() -> JSONResponse:
    return JSONResponse(ollama_agent.status())


@router.post("/ollama/connect")
def ollama_connect(payload: dict[str, object]) -> JSONResponse:
    return JSONResponse(ollama_agent.connect(payload))


@router.post("/ollama/models")
def ollama_models(payload: dict[str, object]) -> JSONResponse:
    return JSONResponse(ollama_agent.update_selected_models(payload))


@router.post("/ollama/offload")
def ollama_offload(payload: dict[str, object]) -> JSONResponse:
    return JSONResponse(ollama_agent.offload(payload))


@router.post("/ollama/shutdown")
def ollama_shutdown(payload: dict[str, object]) -> JSONResponse:
    return JSONResponse(ollama_agent.shutdown(payload))


@router.post("/ollama/chat")
def ollama_chat(payload: dict[str, object]) -> JSONResponse:
    return JSONResponse(ollama_agent.ask(payload))


@router.post("/ollama/request-usage")
def ollama_request_usage(payload: dict[str, object]) -> JSONResponse:
    return JSONResponse(ollama_agent.request_usage(payload))


@router.post("/ollama/autonomous-docking")
def ollama_autonomous_docking(payload: dict[str, object]) -> JSONResponse:
    return JSONResponse(ollama_agent.autonomous_docking(payload))


@router.post("/ollama/chat/stream")
def ollama_chat_stream(payload: dict[str, object]) -> StreamingResponse:
    return StreamingResponse(
        ollama_agent.stream_ask(payload),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/gemini/status")
def gemini_status() -> JSONResponse:
    return JSONResponse(gemini_agent.status())


@router.post("/gemini/save")
def gemini_save(payload: dict[str, object]) -> JSONResponse:
    return JSONResponse(gemini_agent.save(payload))


@router.post("/gemini/models")
def gemini_models(payload: dict[str, object]) -> JSONResponse:
    return JSONResponse(gemini_agent.save(payload))


@router.post("/gemini/cli")
def gemini_cli(payload: dict[str, object]) -> JSONResponse:
    result = gemini_agent.activate_cli(payload)
    return JSONResponse(result, status_code=200 if result.get("ok") else 404)


@router.post("/gemini/cli/mcp")
def gemini_cli_mcp(payload: dict[str, object]) -> JSONResponse:
    return JSONResponse(gemini_agent.configure_cli_mcp(payload))


@router.post("/gemini/chat/stream")
def gemini_chat_stream(payload: dict[str, object]) -> StreamingResponse:
    return StreamingResponse(
        gemini_agent.stream_ask(payload),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/gemini-cli/chat/stream")
def gemini_cli_chat_stream(payload: dict[str, object]) -> StreamingResponse:
    return StreamingResponse(
        gemini_agent.stream_cli_ask(payload),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
