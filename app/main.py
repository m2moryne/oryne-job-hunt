"""One server: CareerKit (the paid API, at /careerkit) and the try-it page for the job agent (at /)."""

from __future__ import annotations

import io
import json
import logging
import re
import zipfile
from pathlib import Path
from typing import Iterator

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import llm, sources
from .careerkit import SERVICES, router as careerkit
from .config import settings
from .runs import RunError, Runs

logging.basicConfig(level=logging.INFO)

STATIC = Path(__file__).parent / "static"
SAMPLE_CV = (Path(__file__).parent / "sample_cv.txt").read_text(encoding="utf-8")

app = FastAPI(title="Oryne job agent", docs_url="/careerkit/docs", redoc_url=None)
app.include_router(careerkit, prefix="/careerkit", tags=["CareerKit (paid)"])
app.mount("/static", StaticFiles(directory=STATIC), name="static")
runs = Runs()


@app.exception_handler(RunError)
def run_error(_: Request, error: RunError) -> JSONResponse:
    return JSONResponse({"error": str(error)}, status_code=error.status)


def _ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    return forwarded.split(",")[0].strip() or (request.client.host if request.client else "unknown")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.get("/api/config")
def config() -> dict:
    return {
        "services": [{"id": key, **value} for key, value in SERVICES.items()],
        "boards": [{"id": key, **value} for key, value in sources.SOURCES.items()],
        "demoReady": runs.owner is not None,
        "demoWallet": settings.demo_wallet,
        "careerkit": settings.careerkit_url,
        "careerkitAddress": settings.careerkit_address,
        "model": settings.llm_model if llm.enabled() else None,
        "sampleCv": SAMPLE_CV,
        "dashboard": "https://oryne.org/app",
    }


@app.post("/api/runs")
async def start(request: Request) -> dict:
    form = await request.json()
    run = runs.start(form if isinstance(form, dict) else {}, _ip(request))
    return {"id": run.id, "mode": run.mode}


@app.get("/api/runs/{run_id}/events")
def events(run_id: str, after: int = 0) -> StreamingResponse:
    run = runs.get(run_id)

    def stream() -> Iterator[str]:
        seen = after
        while True:
            fresh = run.wait(seen)
            for event in fresh:
                yield f"data: {json.dumps(event)}\n\n"
            seen += len(fresh)
            if run.done and seen >= len(run.events):
                return
            if not fresh:
                yield ": keep-alive\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream", headers={"cache-control": "no-cache", "x-accel-buffering": "no"})


@app.post("/api/runs/{run_id}/approvals/{approval_id}")
async def decide(run_id: str, approval_id: str, request: Request) -> dict:
    body = await request.json()
    return runs.decide(run_id, approval_id, bool(body.get("approve")))


@app.post("/api/runs/{run_id}/cancel")
def cancel(run_id: str) -> dict:
    runs.cancel(run_id)
    return {"ok": True}


@app.post("/api/cv-file")
async def cv_file(file: UploadFile = File(...)) -> dict:
    """A CV as PDF, Word (.docx) or text, to plain text. Nothing is kept."""
    data = await file.read(3_000_000)
    name = (file.filename or "").lower()
    try:
        if name.endswith(".pdf"):
            from pypdf import PdfReader

            text = "\n".join(page.extract_text() or "" for page in PdfReader(io.BytesIO(data)).pages[:8])
        elif name.endswith(".docx"):
            xml = zipfile.ZipFile(io.BytesIO(data)).read("word/document.xml").decode("utf-8", "ignore")
            text = re.sub(r"<[^>]+>", "", re.sub(r"</w:p>", "\n", xml))
        else:
            text = data.decode("utf-8", "ignore")
    except Exception as error:  # noqa: BLE001
        raise HTTPException(400, f"Couldn't read that file: {error}") from error
    text = re.sub(r"[ \t]+", " ", text).strip()
    if len(text) < 80:
        raise HTTPException(400, "Couldn't find much text in that file. Paste your CV instead.")
    return {"text": text[:15000]}
