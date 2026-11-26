# Deployment Information — Day 14

## Public URL

https://day14-agent-gtyd.onrender.com

## Platform

**Render**, deploy bằng Blueprint [`06-lab-complete/render.yaml`](06-lab-complete/render.yaml) (Dashboard → New → Blueprint, Blueprint Path `06-lab-complete/render.yaml`):

| Tài nguyên | Loại | Plan / region | Ghi chú |
|---|---|---|---|
| `day14-agent` | Web Service, runtime Docker | free / singapore | Build từ `06-lab-complete/Dockerfile` (multi-stage, user non-root), health check `/health` |
| `day14-redis` | Key Value (Redis) | free / singapore | Chỉ nhận kết nối nội bộ (`ipAllowList: []`); lưu state rate limit + cost guard |

Gói free của Render tắt service sau một thời gian không có request; request đầu tiên sau đó mất khoảng 30–60 s để khởi động lại.

## Test Commands

Đặt biến trước (key lấy ở Render → `day14-agent` → Environment, không ghi vào file):

```bash
BASE="https://day14-agent-gtyd.onrender.com"
read -rsp "AGENT_API_KEY: " AGENT_API_KEY; echo
```

### Health check (liveness): mong đợi `200`

```bash
curl -sS "$BASE/health"
# {"status":"ok","uptime_seconds":...,"version":"1.0.0","environment":"production"}
```

### Readiness (Redis kết nối được): mong đợi `200`

```bash
curl -sS "$BASE/ready"
# {"ready":true}
```

### Không có API key: mong đợi `401`

```bash
curl -sS -o /dev/null -w '%{http_code}\n' -X POST "$BASE/ask" \
  -H 'Content-Type: application/json' -d '{"question":"Hello"}'
```

### Có API key: mong đợi `200`

```bash
curl -sS -X POST "$BASE/ask" \
  -H 'Content-Type: application/json' -H "X-API-Key: $AGENT_API_KEY" \
  -d '{"user_id": "test", "question": "Hello"}'
```

### Kết quả tự kiểm (4 lệnh ở CP6)

```text
200
200
401
200
```

## Environment Variables Set

Đặt trên Render (khai báo trong `render.yaml`), **không có giá trị secret nào trong repo**:

| Biến | Giá trị / nguồn |
|---|---|
| `AGENT_API_KEY` | Render tự sinh (`generateValue: true`), chỉ xem được trên dashboard |
| `REDIS_URL` | Tham chiếu `day14-redis` (`fromService` → `connectionString`), URL nội bộ do Render điền |
| `ENVIRONMENT` | `production` (app không khởi động nếu thiếu `AGENT_API_KEY`) |
| `RATE_LIMIT_PER_MINUTE` | `10` |
| `DAILY_BUDGET_USD` | `1.0` |
| `MONTHLY_BUDGET_USD` | `10.0` |
| `WEB_CONCURRENCY` | `1` (số worker uvicorn, gói free 512 MB RAM) |
| `PORT` | Render tự cấp; `CMD` của Dockerfile đọc `${PORT:-8000}` |

## Screenshots

- [Deployment dashboard](screenshots/dashboard.png)
- [Service running](screenshots/running.png)
- [Test results](screenshots/test.png)
