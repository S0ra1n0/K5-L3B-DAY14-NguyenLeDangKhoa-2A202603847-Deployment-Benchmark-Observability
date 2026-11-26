"""
Rate limiting (tiêu chí 7) — Sliding Window Log trên Redis sorted set.

  • Mỗi API key tối đa RATE_LIMIT_PER_MINUTE request trong 60 giây gần nhất
  • Vượt giới hạn → HTTP 429 kèm header Retry-After
  • State nằm trong Redis → mọi worker / instance dùng chung, còn nguyên sau khi restart agent

Mỗi request là 1 phần tử trong sorted set, score = thời điểm (ms):
  ZREMRANGEBYSCORE  xoá các request cũ hơn 60 giây
  ZADD              ghi request hiện tại
  ZCARD             đếm số request trong cửa sổ
  EXPIRE            key tự xoá khi key API ngừng gọi
Bốn lệnh chạy trong 1 transaction (MULTI/EXEC) nên 2 worker gọi cùng lúc vẫn đếm đúng.
Vượt giới hạn thì xoá lại phần tử vừa thêm, để request bị chặn không chiếm chỗ trong cửa sổ.
"""
import hashlib
import math
import time
import uuid

from fastapi import HTTPException

from app.config import settings
from app.redis_client import r

WINDOW_SECONDS = 60


def _bucket_key(api_key: str) -> str:
    # Không lưu nguyên API key trong Redis: dùng hash
    digest = hashlib.sha256(api_key.encode()).hexdigest()[:16]
    return f"ratelimit:{digest}"


def check_rate_limit(key: str) -> dict:
    """Ghi nhận 1 request của `key`. Vượt giới hạn → raise HTTPException(429)."""
    limit = settings.rate_limit_per_minute
    bucket = _bucket_key(key)
    now_ms = int(time.time() * 1000)
    window_start = now_ms - WINDOW_SECONDS * 1000
    member = f"{now_ms}-{uuid.uuid4().hex[:8]}"

    pipe = r.pipeline(transaction=True)
    pipe.zremrangebyscore(bucket, 0, window_start)
    pipe.zadd(bucket, {member: now_ms})
    pipe.zcard(bucket)
    pipe.expire(bucket, WINDOW_SECONDS + 1)
    _, _, count, _ = pipe.execute()

    if count > limit:
        r.zrem(bucket, member)
        oldest = r.zrange(bucket, 0, 0, withscores=True)
        oldest_ms = oldest[0][1] if oldest else now_ms
        retry_after = max(1, math.ceil((oldest_ms + WINDOW_SECONDS * 1000 - now_ms) / 1000))
        raise HTTPException(
            status_code=429,
            detail={
                "error": "Rate limit exceeded",
                "limit": limit,
                "window_seconds": WINDOW_SECONDS,
                "retry_after_seconds": retry_after,
            },
            headers={
                "Retry-After": str(retry_after),
                "X-RateLimit-Limit": str(limit),
                "X-RateLimit-Remaining": "0",
            },
        )

    return {"limit": limit, "remaining": max(0, limit - count)}
