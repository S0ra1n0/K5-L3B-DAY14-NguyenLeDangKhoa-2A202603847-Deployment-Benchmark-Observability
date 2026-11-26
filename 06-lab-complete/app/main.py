"""
Production AI Agent — Part 6 Final Project

  GET  /health   liveness: luôn 200 khi process còn chạy (không phụ thuộc Redis)
  GET  /ready    readiness: 200 khi Redis kết nối được, 503 khi không (hoặc đang tắt)
  POST /ask      X-API-Key → rate limit (Redis) → cost guard (Redis) → mock LLM

Log: mỗi dòng log của app là 1 JSON object.
Shutdown: uvicorn tự bắt SIGTERM, ngừng nhận kết nối mới, chờ request đang chạy xong,
rồi chạy phần shutdown của lifespan. App KHÔNG tự đăng ký signal handler để khỏi đè
handler của uvicorn.

Kiểm tra: python check_production_ready.py
"""
import json
import logging
import sys
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import redis
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.auth import verify_api_key
from app.config import settings
from app.cost_guard import check_budget, estimate_tokens, record_cost
from app.rate_limiter import check_rate_limit
from app.redis_client import r, redis_healthy
from utils.mock_llm import ask as llm_ask


# ── Structured logging ────────────────────────────────────────────────────────
class JsonFormatter(logging.Formatter):
    """Mỗi record → 1 dòng JSON hợp lệ. Field thêm qua `extra={"fields": {...}}`."""

    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "ts": datetime.fromtimestamp(record.created, timezone.utc).isoformat(timespec="milliseconds"),
            "lvl": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
        }
        entry.update(getattr(record, "fields", {}))
        if record.exc_info:
            entry["exc_type"] = record.exc_info[0].__name__
            entry["exc"] = self.formatException(record.exc_info)
        return json.dumps(entry, ensure_ascii=False, default=str)


def _setup_logger() -> logging.Logger:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    log = logging.getLogger("agent")
    log.handlers = [handler]
    log.setLevel(settings.log_level)
    log.propagate = False  # không lặp lại qua root logger / handler của uvicorn
    return log


logger = _setup_logger()


def log_event(event: str, level: int = logging.INFO, **fields) -> None:
    logger.log(level, event, extra={"fields": fields})


# ── Lifecycle ─────────────────────────────────────────────────────────────────
START_TIME = time.time()
_state = {"shutting_down": False}


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: không bắt buộc Redis — container vẫn lên, /ready báo 503 tới khi Redis sẵn sàng
    log_event("startup", environment=settings.environment, version=settings.app_version,
              port=settings.port, redis_ok=redis_healthy(),
              rate_limit_per_minute=settings.rate_limit_per_minute,
              daily_budget_usd=settings.daily_budget_usd)
    yield
    # Shutdown: tới đây uvicorn đã ngừng nhận kết nối mới và chờ xong request đang chạy
    _state["shutting_down"] = True
    log_event("shutdown_started")
    try:
        r.close()
    except redis.RedisError:
        pass
    log_event("shutdown_complete", uptime_seconds=round(time.time() - START_TIME, 1))


app = FastAPI(title="Production AI Agent", version=settings.app_version, lifespan=lifespan)


# ── Middleware: request log + request id ─────────────────────────────────────
@app.middleware("http")
async def log_requests(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:12]
    started = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        log_event("request_failed", logging.ERROR, request_id=request_id,
                  method=request.method, path=request.url.path)
        raise
    ms = round((time.perf_counter() - started) * 1000, 1)
    response.headers["X-Request-ID"] = request_id
    if request.url.path not in ("/health", "/ready"):  # probe gọi liên tục, không cần log
        log_event("request", request_id=request_id, method=request.method,
                  path=request.url.path, status=response.status_code, ms=ms)
    return response


# ── Error handling: mã lỗi rõ ràng, không lộ stack trace ra client ────────────
@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError):
    errors = [{"field": ".".join(str(p) for p in e["loc"][1:]) or "body", "msg": e["msg"]}
              for e in exc.errors()]
    return JSONResponse(status_code=422, content={"error": "Invalid request", "details": errors})


@app.exception_handler(redis.RedisError)
async def redis_error_handler(request: Request, exc: redis.RedisError):
    log_event("redis_unavailable", logging.ERROR, path=request.url.path, error=type(exc).__name__)
    return JSONResponse(status_code=503, content={"error": "Service temporarily unavailable"},
                        headers={"Retry-After": "5"})


@app.exception_handler(Exception)
async def unhandled_error_handler(request: Request, exc: Exception):
    logger.error("unhandled_exception", exc_info=exc, extra={"fields": {"path": request.url.path}})
    return JSONResponse(status_code=500, content={"error": "Internal server error"})


# ── Endpoints ─────────────────────────────────────────────────────────────────
class AskRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=2000)
    user_id: str | None = Field(default=None, max_length=100)


@app.get("/")
def root():
    return {"app": "Production AI Agent", "version": settings.app_version,
            "endpoints": ["GET /health", "GET /ready", "POST /ask"]}


@app.get("/health")
def health():
    """Liveness — chỉ hỏi "process còn sống không?", KHÔNG kiểm tra Redis."""
    return {
        "status": "ok",
        "uptime_seconds": round(time.time() - START_TIME, 1),
        "version": settings.app_version,
        "environment": settings.environment,
    }


@app.get("/ready")
def ready():
    """Readiness — nhận traffic được chưa? Redis không kết nối được hoặc đang tắt → 503."""
    if _state["shutting_down"]:
        raise HTTPException(status_code=503, detail="Shutting down")
    if not redis_healthy():
        raise HTTPException(status_code=503, detail="Redis not reachable")
    return {"ready": True}


@app.post("/ask")
def ask(body: AskRequest, api_key: str = Depends(verify_api_key)):
    # def (không async): Redis client và mock LLM đều blocking → FastAPI chạy trong threadpool
    rate = check_rate_limit(api_key)          # 429 khi vượt giới hạn
    check_budget()                            # 402 khi hết budget — kiểm tra TRƯỚC khi gọi LLM

    started = time.perf_counter()
    answer = llm_ask(body.question)
    llm_ms = round((time.perf_counter() - started) * 1000, 1)

    input_tokens = estimate_tokens(body.question)
    output_tokens = estimate_tokens(answer)
    cost = record_cost(input_tokens, output_tokens)   # cộng chi phí SAU khi gọi LLM

    # Không log nội dung câu hỏi / câu trả lời (PII) và không log API key
    log_event("llm_call", question_chars=len(body.question), input_tokens=input_tokens,
              output_tokens=output_tokens, cost_usd=round(cost, 8), llm_ms=llm_ms)
    return {
        "question": body.question,
        "answer": answer,
        "usage": {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cost_usd": round(cost, 8),
            "requests_remaining": rate["remaining"],
        },
    }
