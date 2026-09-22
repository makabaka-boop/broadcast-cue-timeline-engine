"""FastAPI application: template registration, delay inference, querying."""

from __future__ import annotations

import json
import os
import uuid
from contextlib import asynccontextmanager
from typing import Any, Dict

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .db import Database
from .errors import APIError, register_exception_handlers
from .scheduling import run_inference
from .validation import validate_delay_overrides, validate_template

DEFAULT_DB_PATH = os.environ.get("CUE_DB_PATH", "/data/cue_control.db")


@asynccontextmanager
async def lifespan(app: FastAPI):
    db_path = app.state.db_path
    app.state.db = Database(db_path)
    try:
        yield
    finally:
        app.state.db.close()


def create_app(db_path: str | None = None) -> FastAPI:
    app = FastAPI(
        title="直播播控提示点排期服务",
        version="1.0.0",
        lifespan=lifespan,
    )
    app.state.db_path = db_path or DEFAULT_DB_PATH
    register_exception_handlers(app)

    async def parse_body(request: Request) -> Any:
        raw_bytes = await request.body()
        if not raw_bytes:
            return {}
        try:
            return json.loads(raw_bytes.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise APIError(
                status_code=400,
                code="invalid_json",
                detail="request body is not valid JSON",
            )

    @app.get("/health")
    async def health() -> Dict[str, str]:
        return {"status": "ok"}

    @app.post("/templates", status_code=201)
    async def register_template(request: Request) -> JSONResponse:
        payload = await parse_body(request)
        valid = validate_template(payload)
        template_id = uuid.uuid4().hex
        app.state.db.insert_template(template_id, valid["document"])
        return JSONResponse(
            status_code=201,
            content={
                "id": template_id,
                "template": valid["document"],
            },
        )

    @app.get("/templates/{template_id}")
    async def get_template(template_id: str) -> Dict[str, Any]:
        row = app.state.db.get_template(template_id)
        if row is None:
            raise APIError(
                status_code=404,
                code="not_found",
                detail=f"template {template_id!r} does not exist",
            )
        return {"id": row["id"], "template": row["document"],
                "created_at": row["created_at"]}

    @app.post("/templates/{template_id}/inferences", status_code=201)
    async def create_inference(template_id: str, request: Request) -> JSONResponse:
        row = app.state.db.get_template(template_id)
        if row is None:
            raise APIError(
                status_code=404,
                code="not_found",
                detail=f"template {template_id!r} does not exist",
            )
        payload = await parse_body(request)
        valid = validate_template(row["document"])
        delays = validate_delay_overrides(payload, valid)
        # Computation happens before any write: positive_cycle /
        # deadline_exceeded therefore leave storage untouched.
        results = run_inference(valid, delays)
        inference_id = uuid.uuid4().hex
        app.state.db.insert_inference(inference_id, template_id, delays, results)
        return JSONResponse(
            status_code=201,
            content={
                "id": inference_id,
                "template_id": template_id,
                "delays": delays,
                "status": "ok",
                "results": results,
            },
        )

    @app.get("/inferences/{inference_id}")
    async def get_inference(inference_id: str) -> Dict[str, Any]:
        row = app.state.db.get_inference(inference_id)
        if row is None:
            raise APIError(
                status_code=404,
                code="not_found",
                detail=f"inference {inference_id!r} does not exist",
            )
        template_id, delays, results, created_at = row
        return {
            "id": inference_id,
            "template_id": template_id,
            "delays": delays,
            "status": "ok",
            "results": results,
            "created_at": created_at,
        }

    return app


app = create_app()
