"""
Cost guard (tiêu chí 8).

  • Cộng dồn chi phí LLM (tính theo token) vào Redis, theo ngày và theo tháng (UTC)
  • Tổng chi phí đã đạt DAILY_BUDGET_USD hoặc MONTHLY_BUDGET_USD → HTTP 402

Key Redis:
  cost:daily:YYYY-MM-DD   (hết hạn sau 2 ngày)
  cost:monthly:YYYY-MM    (hết hạn sau 32 ngày)
Sang ngày / tháng mới là key mới, nên budget tự reset mà không cần job dọn dẹp.
"""
from datetime import datetime, timezone

from fastapi import HTTPException

from app.config import settings
from app.redis_client import r

# Giá USD / 1K tokens (tham khảo gpt-4o-mini)
PRICE_INPUT_PER_1K = 0.00015
PRICE_OUTPUT_PER_1K = 0.0006

DAILY_TTL_SECONDS = 2 * 24 * 3600
MONTHLY_TTL_SECONDS = 32 * 24 * 3600


def _keys() -> tuple[str, str]:
    now = datetime.now(timezone.utc)
    return f"cost:daily:{now:%Y-%m-%d}", f"cost:monthly:{now:%Y-%m}"


def estimate_tokens(text: str) -> int:
    """Mock LLM không trả usage → ước lượng ~4 ký tự / token (LLM thật: lấy từ response.usage)."""
    return max(1, len(text) // 4)


def get_spend() -> dict:
    daily_key, monthly_key = _keys()
    daily, monthly = r.mget(daily_key, monthly_key)
    return {"daily_usd": float(daily or 0), "monthly_usd": float(monthly or 0)}


def check_budget() -> None:
    """Gọi TRƯỚC khi gọi LLM. Hết budget → raise HTTPException(status_code=402)."""
    spend = get_spend()
    if spend["daily_usd"] >= settings.daily_budget_usd:
        raise HTTPException(
            status_code=402,
            detail={
                "error": "Daily budget exceeded",
                "spent_usd": round(spend["daily_usd"], 6),
                "budget_usd": settings.daily_budget_usd,
                "resets_at": "00:00 UTC",
            },
        )
    if spend["monthly_usd"] >= settings.monthly_budget_usd:
        raise HTTPException(
            status_code=402,
            detail={
                "error": "Monthly budget exceeded",
                "spent_usd": round(spend["monthly_usd"], 6),
                "budget_usd": settings.monthly_budget_usd,
                "resets_at": "first day of next month, 00:00 UTC",
            },
        )


def record_cost(input_tokens: int, output_tokens: int) -> float:
    """Gọi SAU khi gọi LLM. Cộng chi phí của request vào tổng, trả về chi phí request."""
    cost = (input_tokens / 1000) * PRICE_INPUT_PER_1K + (output_tokens / 1000) * PRICE_OUTPUT_PER_1K
    daily_key, monthly_key = _keys()
    pipe = r.pipeline(transaction=True)
    pipe.incrbyfloat(daily_key, cost)
    pipe.expire(daily_key, DAILY_TTL_SECONDS)
    pipe.incrbyfloat(monthly_key, cost)
    pipe.expire(monthly_key, MONTHLY_TTL_SECONDS)
    pipe.execute()
    return cost
