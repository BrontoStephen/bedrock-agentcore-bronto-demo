"""FastAPI entrypoint for the Bedrock -> Bronto observability demo."""

from __future__ import annotations

import logging
from pathlib import Path

# Telemetry must be configured before instrumented libraries are used.
from telemetry import setup_telemetry

setup_telemetry()

from fastapi import FastAPI  # noqa: E402
from fastapi.responses import FileResponse, JSONResponse  # noqa: E402
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor  # noqa: E402
from pydantic import BaseModel  # noqa: E402

import bedrock  # noqa: E402

log = logging.getLogger(__name__)

app = FastAPI(title="Bedrock → Bronto demo")
FastAPIInstrumentor.instrument_app(app)

STATIC_DIR = Path(__file__).parent / "static"


class ChatRequest(BaseModel):
    prompt: str
    system: str | None = None


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok"}


@app.post("/chat")
def chat(req: ChatRequest) -> JSONResponse:
    log.info(
        "chat.request",
        extra={"event.name": "chat.request", "prompt.chars": len(req.prompt)},
    )
    try:
        result = bedrock.converse(req.prompt, system=req.system)
    except Exception as exc:  # noqa: BLE001
        log.exception("chat request failed")
        return JSONResponse(status_code=502, content={"error": str(exc)})
    return JSONResponse(content=result)
