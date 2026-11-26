# Day 14 Lab — Mission Answers

> Mọi con số (MB, mã HTTP, thời gian…) lấy từ lệnh đã chạy thật. Che token / API key trước khi dán output.

---

## Part 1: Localhost vs Production

### Exercise 1.1: Anti-patterns trong `01-localhost-vs-production/develop/app.py` (≥ 5)

| # | Anti-pattern | Dòng code | Vì sao nguy hiểm |
|---|---|---|---|
| 1 | **Hardcode secret** (secret viết thẳng trong source code) | `OPENAI_API_KEY = "sk-..."` (dòng 17), `DATABASE_URL = "postgresql://admin:password123@..."` (dòng 18) | Lộ API key và mật khẩu DB: push lên GitHub là secret nằm vĩnh viễn trong **git history** (xoá file sau đó cũng không hết, phải **revoke** key). Đổi key phải sửa code rồi deploy lại. Vi phạm 12-Factor (Factor III: Config): secret phải đọc từ **environment variable**. |
| 2 | **Config hardcode, không tách theo môi trường** (thiếu config management) | `DEBUG = True`, `MAX_TOKENS = 500` (dòng 21–22) | Không đổi được hành vi giữa dev / staging / production nếu không sửa code. Quên tắt `DEBUG` ở production thì dễ lộ thông tin nội bộ (stack trace, dữ liệu debug). Cùng một **build artifact** (image) phải chạy được ở mọi môi trường, chỉ khác nhau ở env var. |
| 3 | **Logging bằng `print`, log ra secret và dữ liệu user** (thiếu structured logging) | `print(f"[DEBUG] Using key: {OPENAI_API_KEY}")` (dòng 34); `print` câu hỏi / câu trả lời (dòng 33, 38) | Secret bị ghi vào **log của server**: ai đọc được log hoặc hệ thống thu log (**log aggregator**) là thấy key. Log nguyên câu hỏi của user có thể lộ **PII** (dữ liệu cá nhân). `print` không có **log level**, timestamp, không phải JSON nên khó lọc, tìm kiếm, đặt cảnh báo. |
| 4 | **Thiếu health check endpoint** (không có liveness / readiness probe) | Dòng 42–43: không có route `/health` / `/ready` | Agent treo hoặc crash thì nền tảng (Docker, Railway, Render, Kubernetes) không phát hiện được để **tự restart**, cũng không biết ngừng chuyển traffic tới instance lỗi → load balancer vẫn gửi request vào instance chết, user nhận lỗi. |
| 5 | **Host và port cố định**, không đọc `PORT` từ env | `host="localhost", port=8000` (dòng 51–52) | Railway / Render cấp cổng động qua biến `PORT`; app bỏ qua và luôn nghe 8000 → nền tảng không kết nối được, health check fail, deploy hỏng. `localhost` (127.0.0.1) là **bind address** chỉ nhận kết nối từ chính máy đó: chạy trong container thì bên ngoài không gọi vào được, phải bind `0.0.0.0`. |
| 6 | **Bật auto-reload ở production** | `reload=True` (dòng 53) | Chế độ dev: theo dõi file và tự restart process khi code đổi. Ở production tốn CPU, restart bất ngờ làm rớt request, và không chạy được nhiều **worker**. |
| 7 | **Không có graceful shutdown** | Không có `lifespan` / xử lý `SIGTERM` | Khi nền tảng tắt app hoặc **rolling deploy** (gửi `SIGTERM`), request đang xử lý bị cắt ngang, user nhận lỗi; không có bước dọn dẹp (đóng kết nối DB, flush log). |

### Exercise 1.2: Chạy bản develop

**Terminal client** (chạy lệnh):

```text
PS> curl.exe -i -X POST "http://localhost:8000/ask?question=Hello"
HTTP/1.1 200 OK
date: Thu, 26 Nov 2026 07:56:49 GMT
server: uvicorn
content-length: 95
content-type: application/json

{"answer":"Agent đang hoạt động tốt! (mock response) Hỏi thêm câu hỏi đi nhé."}
```

**Terminal server** (`python app.py`):

```text
Starting agent on localhost:8000...
INFO:     Will watch for changes in these directories: ['...\\01-localhost-vs-production\\develop']
INFO:     Uvicorn running on http://localhost:8000 (Press CTRL+C to quit)
INFO:     Started reloader process [93216] using WatchFiles
INFO:     Started server process [35988]
INFO:     Waiting for application startup.
INFO:     Application startup complete.
[DEBUG] Got question: Hello
[DEBUG] Using key: sk-hardcoded-****
[DEBUG] Response: Agent đang hoạt động tốt! (mock response) Hỏi thêm câu hỏi đi nhé.
INFO:     127.0.0.1:63954 - "POST /ask?question=Hello HTTP/1.1" 200 OK
```

**Nhận xét:** App **chạy được** (trả `200 OK`, có câu trả lời), nhưng **chưa production-ready**. Chính output trên đã cho thấy các anti-pattern ở bảng 1.1:

- **Lộ secret trong log** (anti-pattern 3): dòng `[DEBUG] Using key: sk-hardcoded-...` ghi nguyên API key ra log của server. Ở production, log được gom về log aggregator, ai đọc được log là lấy được key.
- **Log nội dung user:** `Got question` / `Response` ghi nguyên câu hỏi và câu trả lời, có nguy cơ lộ PII. Log dạng text tự do, không phải JSON, không có timestamp, khó lọc và đặt cảnh báo.
- **Bind `localhost`, cổng cứng 8000** (anti-pattern 5): `Uvicorn running on http://localhost:8000` nghĩa là chỉ nhận kết nối từ chính máy này; chạy trong container hoặc trên Railway / Render (cấp cổng qua `PORT`) sẽ không gọi vào được.
- **Chạy chế độ dev** (anti-pattern 6): `Started reloader process ... using WatchFiles` cho thấy `reload=True` đang bật.
- **Không có health check** (anti-pattern 4): app không có route `/health`, nên nền tảng không có cách nào hỏi "app còn sống không?".

### Exercise 1.3: So sánh develop vs production

| Feature | Develop | Production | Tại sao quan trọng? |
|---|---|---|---|
| Config | Hardcode trong code: `DEBUG = True`, `MAX_TOKENS = 500` (`app.py` dòng 21–22) | Tập trung trong `config.py`, mọi field đọc bằng `os.getenv(...)` có default; `settings.validate()` kiểm tra lúc khởi động (**fail fast**) | Cùng một image chạy được ở dev / staging / production, chỉ đổi env var, không phải sửa code và build lại (12-Factor, Factor III). Thiếu config bắt buộc thì app dừng ngay lúc start, thay vì lỗi ngầm khi đang phục vụ user. |
| Secrets | `OPENAI_API_KEY`, mật khẩu DB viết thẳng trong code (dòng 17–18) và còn in ra log (dòng 34) | Đọc từ env (`OPENAI_API_KEY`, `AGENT_API_KEY`, `config.py` dòng 26, 31); `.env.example` để trống giá trị; thiếu `AGENT_API_KEY` khi `ENVIRONMENT=production` → `ValueError`, app không chạy | Secret không nằm trong git history nên không lộ khi push code; đổi hoặc **revoke** key chỉ cần đổi env var trên nền tảng. |
| Health check | Không có | `GET /health` (liveness: trả `status`, `uptime_seconds`) và `GET /ready` (readiness: `503` khi chưa sẵn sàng, `is_ready` bật sau startup, tắt khi shutdown) | Nền tảng dùng `/health` để biết khi nào phải **restart** container; load balancer dùng `/ready` để chỉ chuyển traffic vào instance đã sẵn sàng, không gửi vào instance đang khởi động hoặc đang tắt. |
| Logging | `print()` text tự do, log cả API key và nguyên nội dung câu hỏi / câu trả lời | `logging` với **log level** (DEBUG chỉ khi `DEBUG=true`), mỗi dòng là JSON (`event`, `question_length`, `client_ip`, `response_length`); không log secret, không log nội dung | Log JSON thì **log aggregator** (Datadog, Loki…) parse, lọc, đếm và đặt cảnh báo được; không log secret và PII nên đọc log không làm lộ dữ liệu. |
| Shutdown | Đột ngột: không có `lifespan`, không xử lý `SIGTERM` | **Graceful**: `lifespan` có phần startup (đặt `is_ready = True`) và shutdown (đặt `is_ready = False`, chờ request đang chạy, log `Shutdown complete`); có đăng ký handler `SIGTERM` | Khi **rolling deploy** hoặc scale xuống, nền tảng gửi `SIGTERM`: request đang xử lý vẫn hoàn thành, `/ready` báo 503 để ngừng nhận traffic mới, kết nối được đóng gọn → user không gặp lỗi giữa chừng. |
| Host / Port | `host="localhost"`, `port=8000` cứng, `reload=True` luôn bật | `HOST` mặc định `0.0.0.0`, `PORT` từ env (`config.py` dòng 216–217); `reload` chỉ bật khi `DEBUG=true` | Bind `0.0.0.0` để nhận kết nối từ ngoài container; đọc `PORT` vì Railway / Render cấp cổng động; tắt reload ở production để không tốn tài nguyên và không restart bất ngờ. |

---

## Part 2: Docker

### Exercise 2.1: Câu hỏi về `02-docker/develop/Dockerfile`

1. **Base image là gì?**
   Base image là image có sẵn mà image của mình được xây lên trên (dòng `FROM`): nó cung cấp hệ điều hành tối thiểu và runtime. Ở đây là **`python:3.11`**: bản Python *đầy đủ* (Debian + compiler, header, nhiều thư viện hệ thống), nặng khoảng 1 GB. Đây là lý do image develop to (`my-agent:develop` đo được 1.67 GB). Production nên dùng `python:3.11-slim` (bỏ phần không cần khi chạy) để image nhỏ hơn, build/pull nhanh hơn, ít lỗ hổng bảo mật hơn (**attack surface** nhỏ hơn).

2. **Working directory là gì?**
   `WORKDIR /app` đặt thư mục làm việc *bên trong container* là `/app` (tự tạo nếu chưa có). Mọi lệnh sau đó (`COPY`, `RUN`, `CMD`) dùng đường dẫn tương đối tính từ `/app`, ví dụ `COPY ... .` chép vào `/app/`, `CMD ["python", "app.py"]` chạy `/app/app.py`. Không đặt `WORKDIR` thì file rơi vào `/` (thư mục gốc), lộn xộn và dễ đè file hệ thống.

3. **Tại sao `COPY requirements.txt` trước `COPY` code?**
   Để tận dụng **layer cache** của Docker. Mỗi lệnh trong Dockerfile tạo ra một layer; khi build lại, Docker dùng lại layer cũ nếu lệnh và file đầu vào không đổi, nhưng **một layer thay đổi thì mọi layer phía sau phải build lại**. `requirements.txt` hiếm khi đổi, còn code đổi liên tục. Đặt `COPY requirements.txt` + `RUN pip install` trước thì sửa code chỉ làm build lại từ bước `COPY app.py`, bước `pip install` (chậm nhất) lấy từ cache → build lại mất vài giây thay vì vài phút. Nếu `COPY . .` trước thì sửa một dòng code cũng phải cài lại toàn bộ dependencies.

4. **`CMD` và `ENTRYPOINT` khác nhau thế nào?**
   Cả hai đều khai báo lệnh chạy khi container start, khác nhau ở chỗ **có bị ghi đè dễ dàng hay không**:
   - `CMD`: lệnh *mặc định*, bị **thay thế hoàn toàn** khi truyền lệnh khác lúc `docker run`. Ví dụ Dockerfile có `CMD ["python", "app.py"]`, chạy `docker run my-agent:develop bash` thì container chạy `bash` thay cho `python app.py`.
   - `ENTRYPOINT`: lệnh *cố định* của container, tham số ở `docker run` **được nối thêm** vào sau chứ không thay thế (muốn đổi phải dùng `--entrypoint`).
   - Hay dùng kết hợp: `ENTRYPOINT` là chương trình, `CMD` là tham số mặc định. Ví dụ `ENTRYPOINT ["uvicorn", "app:app"]` + `CMD ["--port", "8000"]`; chạy `docker run img --port 9000` thì thành `uvicorn app:app --port 9000`.
   - File này chỉ dùng `CMD ["python", "app.py"]` ở dạng **exec form** (mảng JSON): process chạy trực tiếp là **PID 1** nên nhận được `SIGTERM` khi `docker stop`. Dạng **shell form** (`CMD python app.py`) chạy qua `/bin/sh -c`, shell là PID 1 và không chuyển tiếp `SIGTERM` → app bị `SIGKILL` sau 10 giây. (Điều này quan trọng ở Part 6: uvicorn phải là PID 1.)

### Exercise 2.2: Build và chạy container

Build (từ gốc repo): `docker build -f 02-docker/develop/Dockerfile -t my-agent:develop .`
Chạy: `docker run --rm -p 8000:8000 my-agent:develop`

```text
PS> curl.exe -i -X POST "http://localhost:8000/ask?question=What%20is%20Docker"
HTTP/1.1 200 OK
date: Thu, 26 Nov 2026 08:24:42 GMT
server: uvicorn
content-length: 103
content-type: application/json

{"answer":"Container là cách đóng gói app để chạy ở mọi nơi. Build once, run anywhere!"}
```

**Quan sát:** cùng app như Part 1 nhưng giờ chạy trong container, kết quả giống hệt khi chạy trực tiếp trên máy (mock LLM nhận ra từ khoá "docker" trong câu hỏi). Image size: `my-agent:develop` = **1.67 GB** (`docker images my-agent`), chủ yếu do base image `python:3.11` bản đầy đủ.

### Exercise 2.3: Multi-stage build

Build (từ gốc repo): `docker build -f 02-docker/production/Dockerfile -t my-agent:advanced .`

- **Stage 1 (`builder`, dòng 16–29) làm gì:** Từ `python:3.11-slim`, cài **build tools** bằng `apt-get` (`gcc`, `libpq-dev`, cần để compile một số package có phần C), rồi `pip install --no-cache-dir --user -r requirements.txt` để cài dependencies vào `/root/.local`. Stage này chỉ dùng để *build*, **không được deploy**.
- **Stage 2 (`runtime`, dòng 36–72) làm gì:** Bắt đầu lại từ một `python:3.11-slim` sạch, chỉ lấy những gì cần để *chạy*:
  - `COPY --from=builder /root/.local /home/appuser/.local`: chép các package đã cài từ stage 1 sang, không chép `gcc` hay cache của apt.
  - Chép source (`main.py`, `utils/mock_llm.py`).
  - Tạo và chuyển sang **user non-root** `appuser` (dòng 40, 58): app bị chiếm quyền cũng không có quyền root trong container.
  - `HEALTHCHECK` gọi `/health` (dòng 68–69): Docker tự đánh dấu container `unhealthy` khi app treo.
  - `CMD` ở **exec form**, chạy `uvicorn ... --workers 2` (dòng 72): uvicorn là PID 1 nên nhận được `SIGTERM` khi `docker stop`.
- **Vì sao image nhỏ hơn:**
  1. Base **`python:3.11-slim`** thay cho `python:3.11` bản đầy đủ: bỏ compiler, header, tài liệu và nhiều thư viện hệ thống không cần khi chạy.
  2. **Image cuối chỉ gồm stage cuối.** Các layer của stage `builder` (gồm `gcc`, `libpq-dev`, apt lists) bị bỏ lại, không nằm trong image deploy.
  3. `pip install --no-cache-dir` và `rm -rf /var/lib/apt/lists/*` không để lại cache trong layer.

  Image nhỏ thì build, push, pull và khởi động nhanh hơn (quan trọng khi autoscale), tốn ít dung lượng registry hơn, và **attack surface** nhỏ hơn (ít package nên ít lỗ hổng CVE hơn).

| Image | Size (từ `docker images`) |
|---|---|
| `my-agent:develop` | 1670 MB |
| `my-agent:advanced` | 240 MB |
| Chênh lệch |  85.6% |

```text
PS> docker images my-agent
IMAGE               ID             DISK USAGE   CONTENT SIZE   EXTRA
my-agent:advanced   42f1866fdc8b        240MB         57.4MB
my-agent:develop    c6ccb1716cd3       1.67GB          426MB
```

Cách tính: (1670 − 240) / 1670 × 100 ≈ **85.6%** nhỏ hơn. Bảng dùng cột `DISK USAGE` (dung lượng image đã giải nén trên máy). `CONTENT SIZE` là dung lượng nén khi tải về từ registry, và cũng giảm tương tự (426 MB → 57.4 MB).

### Exercise 2.4: Docker Compose stack

Chạy (từ gốc repo, `.env.local` để trống):
`docker compose -p k5-day14-docker -f 02-docker/production/docker-compose.yml up -d --build`

**Sơ đồ các service:**

```text
                 máy host
  client ──HTTP :80──►┌─────────────────────────── network "internal" (bridge) ──────────────┐
  (curl)              │                                                                       │
                      │  nginx (nginx:alpine)  ──proxy_pass──►  agent (build từ Dockerfile)   │
                      │  cổng 80/443 mở ra host     agent:8000   cổng 8000 chỉ trong network  │
                      │  rate limit 10 r/s,                        │              │           │
                      │  security headers                          ▼              ▼           │
                      │                              redis:6379 (redis:7-alpine)  qdrant:6333 │
                      │                              cache / session,             (vector DB) │
                      │                              volume redis_data            volume      │
                      │                                                           qdrant_data │
                      └───────────────────────────────────────────────────────────────────────┘
  Thứ tự khởi động (depends_on): redis + qdrant healthy → agent → nginx
```

- **Các service được start (4):** `redis`, `qdrant`, `agent`, `nginx`. Thứ tự do `depends_on`: agent chờ redis và qdrant ở trạng thái `service_healthy` (healthcheck `redis-cli ping` và kiểm tra cổng 6333), nginx chờ agent.

  ```text
  SERVICE   IMAGE                   STATUS                    PORTS
  agent     k5-day14-docker-agent   Up 10 seconds (healthy)   8000/tcp
  nginx     nginx:alpine            Up 10 seconds             0.0.0.0:80->80/tcp, 0.0.0.0:443->443/tcp
  qdrant    qdrant/qdrant:v1.9.0    Up 21 seconds (healthy)   6333-6334/tcp
  redis     redis:7-alpine          Up 21 seconds (healthy)   6379/tcp
  ```

- **Chúng giao tiếp với nhau thế nào:**
  - Cả 4 service nằm chung network `internal` (driver `bridge`). Docker có **DNS nội bộ**: service gọi nhau bằng **tên service**, ví dụ `redis://redis:6379/0`, `http://qdrant:6333`, `server agent:8000` trong `nginx.conf`.
  - **Chỉ Nginx mở cổng ra host** (`80:80`). Agent, Redis, Qdrant chỉ `EXPOSE` trong network, bên ngoài không gọi thẳng được → giảm bề mặt tấn công.
  - Nginx là **reverse proxy**: nhận request ở cổng 80, `proxy_pass` tới `agent_backend` (upstream `agent:8000`), thêm header `X-Real-IP` / `X-Forwarded-For`, áp **rate limit** 10 request/giây mỗi IP (vượt → 429), thêm security header (`X-Frame-Options`, `X-Content-Type-Options`…).
  - Agent đọc địa chỉ Redis / Qdrant từ env var (`REDIS_URL`, `QDRANT_URL`) do Compose đặt, không hardcode.
  - Dữ liệu Redis và Qdrant nằm trong **named volume** (`redis_data`, `qdrant_data`) nên không mất khi container bị tạo lại.

**Test qua Nginx:**

```text
PS> curl.exe -i http://localhost/health
HTTP/1.1 200 OK
Server: nginx
Content-Type: application/json
Content-Length: 95
Connection: keep-alive
X-Frame-Options: DENY
X-Content-Type-Options: nosniff
X-XSS-Protection: 1; mode=block

{"status":"ok","uptime_seconds":9.5,"version":"2.0.0","timestamp":"2026-11-26T08:42:22.061996"}

PS> curl.exe -i -X POST http://localhost/ask -H "Content-Type: application/json" -d '{\"question\": \"Explain microservices\"}'
HTTP/1.1 200 OK
Server: nginx
Content-Type: application/json
Content-Length: 103
Connection: keep-alive
X-Frame-Options: DENY
X-Content-Type-Options: nosniff
X-XSS-Protection: 1; mode=block

{"answer":"Tôi là AI agent được deploy lên cloud. Câu hỏi của bạn đã được nhận."}
```

Header `Server: nginx` và các security header chứng minh request đi **qua Nginx** chứ không vào thẳng agent. Log của agent (`docker compose -p k5-day14-docker ... logs agent`) thấy request đến từ IP nội bộ của Nginx (`172.18.0.5`), không phải từ máy host:

```text
agent-1  | {"time":"2026-11-26 08:42:22,103","level":"INFO","msg":"{"event": "request", "q_len": 21}"}
agent-1  | INFO:     172.18.0.5:60656 - "POST /ask HTTP/1.1" 200 OK
```

---

## Part 3: Cloud Deployment

> K5: deploy chính agent Part 6 (CP6). Chi tiết ở `DEPLOYMENT.md`.

### Exercise 3.1 / 3.2: Nền tảng chính

- Nền tảng: Render / Railway
- URL public:
- Ảnh: `screenshots/dashboard.png`, `screenshots/running.png`, `screenshots/test.png`

### Nền tảng còn lại: so sánh `railway.toml` vs `render.yaml`

| Điểm | `railway.toml` | `render.yaml` | Vì sao khác |
|---|---|---|---|
| Builder / runtime | | | |
| Health check | | | |
| Secret / env var | | | |
| Redis / service phụ | | | |

### Exercise 3.3: Pipeline Cloud Run (`cloudbuild.yaml` + `service.yaml`)

(3–5 dòng mô tả các bước CI/CD)

---

## Part 4: API Security

### Exercise 4.1: API key (bản develop)

```text
curl.exe -i -X POST http://localhost:8000/ask -H "Content-Type: application/json" -d '{\"question\": \"Hello\"}'
HTTP/1.1 401 Unauthorized
date: Thu, 26 Nov 2026 08:53:51 GMT
server: uvicorn
content-length: 67
content-type: application/json

{"detail":"Missing API key. Include header: X-API-Key: <your-key>"} 
```

```text
curl.exe -i -X POST http://localhost:8000/ask -H "X-API-Key: secret-key-123" -H "Content-Type: application/json" -d '{\"question\": \"Hello\"}'
HTTP/1.1 200 OK
date: Thu, 26 Nov 2026 08:54:21 GMT
server: uvicorn
content-length: 114
content-type: application/json

{"question":"Hello","answer":"Agent đang hoạt động tốt! (mock response) Hỏi thêm câu hỏi đi nhé."}
```

### Exercise 4.2: JWT (bản production, `POST /auth/token`, tài khoản student)

```text
$TOKEN = (Invoke-RestMethod -Method Post -Uri http://localhost:8000/auth/token -ContentType "application/json" -Body '{"username":"student","password":"demo123"}').access_token; $TOKEN.Substring(0,20) + "..."
ey[the rest of token]...
```

**Luồng JWT:** client gửi `username` / `password` tới `POST /auth/token` → server kiểm tra rồi trả **access token** (JWT) đã được **ký** bằng secret của server, bên trong chứa `sub` (username), `role` và thời hạn `exp` (60 phút) → mỗi request sau gửi token trong header `Authorization: Bearer <token>` → server chỉ cần **kiểm tra chữ ký và hạn dùng** (`jwt.decode`), không phải tra DB hay lưu session (**stateless auth**). Token gồm 3 phần `header.payload.signature` mã hoá base64url, nên luôn bắt đầu bằng `eyJ` (là `{"` sau khi mã hoá); payload *đọc được* nhưng không *sửa được*, vì sửa thì chữ ký sai.

Đăng nhập (`curl -i`, token đã che):

```text
PS> curl.exe -i -X POST http://localhost:8000/auth/token -H "Content-Type: application/json" -d '{\"username\":\"student\",\"password\":\"demo123\"}'
HTTP/1.1 200 OK
content-type: application/json

{"access_token":"eyJ***masked***","token_type":"bearer","expires_in_minutes":60,"hint":"Include in header: Authorization: Bearer eyJ***masked***"}
```

Gọi `/ask` **không có token** → `401`:

```text
PS> curl.exe -i -X POST http://localhost:8000/ask -H "Content-Type: application/json" -d '{\"question\": \"Hello\"}'
HTTP/1.1 401 Unauthorized
www-authenticate: Bearer
content-type: application/json

{"detail":"Authentication required. Include: Authorization: Bearer <token>"}
```

Gọi `/ask` với **token giả** → `403`:

```text
PS> curl.exe -i -X POST http://localhost:8000/ask -H "Authorization: Bearer abc.def.ghi" -H "Content-Type: application/json" -d '{\"question\": \"Hello\"}'
HTTP/1.1 403 Forbidden
content-type: application/json

{"detail":"Invalid token."}
```

Gọi `/ask` **có token hợp lệ** → `200`:

```text
PS> curl.exe -i -X POST http://localhost:8000/ask -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{\"question\": \"Hello\"}'
HTTP/1.1 200 OK
content-type: application/json

{"question":"Hello","answer":"Đây là câu trả lời từ AI agent (mock). Trong production, đây sẽ là response từ OpenAI/Anthropic.","usage":{"requests_remaining":9,"budget_remaining_usd":0.000204}}
```

**401 vs 403:** `401 Unauthorized` = *chưa xác thực* (không có token, hoặc token hết hạn), client cần đăng nhập; `403 Forbidden` = có gửi thông tin nhưng *không được phép* (ở đây là token không hợp lệ). Response cũng có các **security header** (`x-frame-options: DENY`, `x-content-type-options: nosniff`…) do middleware `security_headers` thêm vào.

### Exercise 4.3: Rate limiting

```text
1..15 | ForEach-Object { curl.exe -s -o NUL -w "$_ -> %{http_code}`n" -X POST http://localhost:8000/ask -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{\"question\": \"Hello\"}' }
1 -> 200
2 -> 200
3 -> 200
4 -> 200
5 -> 200
6 -> 200
7 -> 200
8 -> 200
9 -> 200
10 -> 200
11 -> 429
12 -> 429
13 -> 429
14 -> 429
15 -> 429
```

Chi tiết một response `429` (`curl -i` gọi ngay sau khi hết lượt):

```text
PS> curl.exe -i -X POST http://localhost:8000/ask -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{\"question\": \"Hello\"}'
HTTP/1.1 429 Too Many Requests
x-ratelimit-limit: 10
x-ratelimit-remaining: 0
x-ratelimit-reset: 1795683686
retry-after: 56
content-type: application/json

{"detail":{"error":"Rate limit exceeded","limit":10,"window_seconds":60,"retry_after_seconds":56}}
```

- **Sau bao nhiêu request thì bị 429:** 10 request thành công trong 60 giây (tài khoản `student`: 10 req/phút), request thứ 11 bị `429 Too Many Requests`. Header `Retry-After: 56` cho client biết phải chờ 56 giây (đến khi timestamp cũ nhất trôi ra khỏi cửa sổ 60 giây); `X-RateLimit-Limit` / `X-RateLimit-Remaining` cho biết hạn mức và số lượt còn lại.
- **Thuật toán rate limit dùng trong code:** docstring ghi "Sliding Window Counter", nhưng code thật là **Sliding Window Log**: mỗi user có một `deque` lưu **timestamp của từng request**; mỗi lần gọi, xoá các timestamp cũ hơn 60 giây, nếu còn ≥ 10 thì trả 429, không thì thêm timestamp mới. Chính xác (không bị "dồn" request ở ranh giới phút như **fixed window**), đổi lại tốn bộ nhớ theo số request. (Sliding Window *Counter* chỉ lưu bộ đếm của từng ô thời gian rồi nội suy, ít tốn bộ nhớ hơn nhưng xấp xỉ.)
- **Giới hạn theo role:** `student` dùng `rate_limiter_user` (10 req/phút), `teacher` / admin dùng `rate_limiter_admin` (100 req/phút). Key đếm là `username` lấy từ JWT.
- **Hạn chế:** state nằm **trong RAM** của process: restart là mất, chạy nhiều worker / instance thì mỗi cái đếm riêng nên giới hạn thực tế bị nhân lên. Part 6 phải chuyển sang **Redis** (sorted set: `ZREMRANGEBYSCORE` → `ZCARD` → `ZADD` → `EXPIRE`) để mọi instance dùng chung.

### Exercise 4.4: Cost guard

Đọc `04-api-gateway/production/cost_guard.py` và đoạn gọi trong `app.py` (`/ask`):

- **Tính chi phí thế nào:** chi phí tính theo **token**, theo bảng giá gpt-4o-mini: input `$0.00015 / 1K token`, output `$0.0006 / 1K token` (dòng 20–21). `cost = input_tokens/1000 × 0.00015 + output_tokens/1000 × 0.0006`. Vì là mock LLM nên số token được *ước lượng* bằng `số từ × 2` cho câu hỏi và câu trả lời (`app.py`); với LLM thật thì lấy số token chính xác từ trường `usage` trong response của API.
- **Luồng xử lý trong `/ask`:** xác thực JWT → rate limit → **`check_budget(username)` trước khi gọi LLM** (hết tiền thì chặn luôn, không tốn thêm) → gọi LLM → **`record_usage(...)` sau khi gọi** để cộng chi phí thật của request vào tổng.
- **Hai tầng budget:**
  - **Per-user:** mỗi user `$1/ngày` (`daily_budget_usd`). Vượt → **HTTP 402 Payment Required**, kèm `used_usd`, `budget_usd`, `resets_at`.
  - **Global:** toàn hệ thống `$10/ngày`. Vượt → **HTTP 503**, chặn mọi user (tránh nhiều user cộng lại làm cháy bill), log mức `CRITICAL`.
  - **Cảnh báo sớm:** dùng tới 80% budget (`warn_at_pct = 0.8`) thì log `WARNING` để biết trước khi bị chặn.
- **Lưu ở đâu:** **trong RAM** của process (`self._records: dict[str, UsageRecord]`, `self._global_cost`). Đây là điểm yếu: restart là mất hết số đã tiêu; chạy nhiều worker / instance thì mỗi cái đếm riêng, budget thực tế bị nhân lên. Production phải lưu trong **Redis** (hoặc DB) để mọi instance dùng chung và còn sau restart, ví dụ key `budget:<user>:<YYYY-MM-DD>` tăng bằng `INCRBYFLOAT`, đặt `EXPIRE` để tự xoá (cách làm ở Part 6).
- **Reset khi nào:** **mỗi ngày**. Mỗi `UsageRecord` gắn với chuỗi ngày `YYYY-MM-DD`; `_get_record()` thấy ngày đã đổi thì tạo record mới về 0. (Lưu ý: code dùng `time.strftime` theo giờ máy chủ, trong khi thông báo ghi "midnight UTC"; `_global_cost` cũng không được reset theo ngày như per-user. Đây là 2 lỗi nhỏ của code mẫu.)
- **Hạn chế khác của code mẫu:** `check_budget` chỉ so tổng *đã tiêu*, không ước lượng chi phí request sắp tới, nên request cuối cùng có thể làm vượt budget một chút; và field `budget_remaining_usd` trong response của `/ask` thực ra trả về số *đã dùng* (`usage.total_cost_usd`), không phải số còn lại.

**Rate limit vs cost guard: vì sao cần cả hai:**

| | Rate limit | Cost guard |
|---|---|---|
| Bảo vệ cái gì | **Tốc độ / tài nguyên server** (chống spam, abuse, DoS nhẹ, một user chiếm hết server) | **Tiền** (bill LLM API) |
| Đơn vị đo | Số request trong một khoảng thời gian (10 req / 60 s) | USD tính theo token, cộng dồn theo ngày |
| Vượt thì trả | `429 Too Many Requests` + `Retry-After` | `402 Payment Required` (user), `503` (global) |

Hai cái không thay thế nhau được: user gửi **ít request nhưng mỗi request rất dài** (prompt vài nghìn token) thì không bao giờ chạm rate limit nhưng vẫn đốt hết tiền → cần cost guard. Ngược lại, user gửi **rất nhiều request ngắn** thì tốn ít tiền nhưng làm quá tải server, ảnh hưởng user khác → cần rate limit.

---

## Part 5: Scaling & Reliability

### Exercise 5.1: Health checks

Code: `05-scaling-reliability/develop/app.py`, `/health` (dòng 104), `/ready` (dòng 147).

- **`/health` (liveness probe) kiểm tra gì:** câu hỏi "**process còn sống không?**". Endpoint trả `200` miễn là process còn chạy và trả lời được, kèm thông tin để debug: `status` (`ok` / `degraded`), `uptime_seconds`, `version`, `environment`, `timestamp`, và một `checks` nhẹ về bộ nhớ (dùng `psutil`: RAM dùng ≥ 90% thì `degraded`; máy chưa cài `psutil` nên chỉ ghi note). Liveness **không** kiểm tra dependency bên ngoài (DB, Redis): Redis chết không phải lỗi của process, restart agent cũng không chữa được.
- **`/ready` (readiness probe) kiểm tra gì:** câu hỏi "**đã sẵn sàng nhận traffic chưa?**". Dựa vào cờ `_is_ready`: cờ chỉ bật `True` sau khi `lifespan` startup xong (load model, kiểm tra dependency, giả lập 0.2 s) và tắt về `False` ngay khi bắt đầu shutdown. Cờ `False` → **`503 Service Unavailable`**; `True` → `200` kèm `in_flight_requests` (số request đang xử lý, đếm bằng middleware `track_requests`; kết quả `1` dưới đây chính là request `/ready` đang chạy). Bản production thật nên kiểm tra thêm kết nối Redis / DB ở đây (Part 6: mất Redis → `/ready` trả 503).
- **Khác nhau ở đâu, nền tảng dùng mỗi cái để làm gì:**

  | | Liveness (`/health`) | Readiness (`/ready`) |
  |---|---|---|
  | Câu hỏi | Process còn sống không? | Nhận traffic được chưa? |
  | Fail khi | Process treo, deadlock, không trả lời | Đang khởi động, đang shutdown, mất dependency (Redis/DB) |
  | Nền tảng làm gì khi fail | **Restart / thay container** | **Ngừng chuyển traffic** vào instance đó (bỏ khỏi load balancer), **không restart**; khi `/ready` lại 200 thì chuyển traffic vào lại |
  | Ai dùng | Docker `HEALTHCHECK`, Railway / Render health check, K8s `livenessProbe` | Load balancer, K8s `readinessProbe`, rolling deploy (chỉ chuyển traffic sang bản mới khi bản mới ready) |

  Nếu dùng một endpoint cho cả hai: Redis chết một lúc thì liveness fail → nền tảng restart hàng loạt container vô ích (**restart loop**), trong khi đúng ra chỉ cần tạm ngừng nhận traffic.

**Kết quả chạy** (`PORT=8005 python app.py`, dùng cổng 8005 vì 8000 đang bận):

```text
PS> curl.exe -i http://localhost:8005/health
HTTP/1.1 200 OK
content-type: application/json

{"status":"ok","uptime_seconds":0.9,"version":"1.0.0","environment":"development","timestamp":"2026-11-26T09:23:42.088081+00:00","checks":{"memory":{"status":"ok","note":"psutil not installed"}}}

PS> curl.exe -i http://localhost:8005/ready
HTTP/1.1 200 OK
content-type: application/json

{"ready":true,"in_flight_requests":1}
```

Log khởi động cho thấy `/ready` chỉ thành `200` *sau* bước startup của `lifespan`:

```text
2026-11-26 16:23:41,308 INFO Agent starting up...
2026-11-26 16:23:41,308 INFO Loading model and checking dependencies...
2026-11-26 16:23:41,508 INFO ✅ Agent is ready!
INFO:     Application startup complete.
INFO:     Uvicorn running on http://0.0.0.0:8005 (Press CTRL+C to quit)
INFO:     127.0.0.1:50273 - "GET /health HTTP/1.1" 200 OK
INFO:     127.0.0.1:50275 - "GET /ready HTTP/1.1" 200 OK
```

### Exercise 5.2: Graceful shutdown

```text
/mnt/d/AI_Thuc_Chien/LAB/K5-L3B-DAY14-NguyenLeDangKhoa-2A202603847-Deployment-Benchmark-Observability$ cd 05-scaling-reliability/develop
python app.py & PID=$!; sleep 3
curl -s -X POST "http://localhost:8000/ask?question=Long%20task" & sleep 0.05; kill -TERM $PID; wait
[1] 2693
2026-11-26 09:32:28,988 INFO Starting agent on port 8000
INFO:     Started server process [2693]
INFO:     Waiting for application startup.
2026-11-26 09:32:29,004 INFO Agent starting up...
2026-11-26 09:32:29,005 INFO Loading model and checking dependencies...
2026-11-26 09:32:29,205 INFO ✅ Agent is ready!
INFO:     Application startup complete.
INFO:     Uvicorn running on http://0.0.0.0:8000 (Press CTRL+C to quit)
[2] 2699
INFO:     Shutting down
INFO:     127.0.0.1:37484 - "POST /ask?question=Long%20task HTTP/1.1" 200 OK
{"answer":"Đây là câu trả lời từ AI agent (mock). Trong production, đây sẽ là response từ OpenAI/Anthropic."}INFO:     Waiting for application shutdown.
2026-11-26 09:32:34,867 INFO 🔄 Graceful shutdown initiated...
2026-11-26 09:32:34,867 INFO ✅ Shutdown complete
INFO:     Application shutdown complete.
INFO:     Finished server process [2693]
2026-11-26 09:32:34,867 INFO Received signal 15 — uvicorn will handle graceful shutdown
[1]-  Done                    python app.py
[2]+  Done                    curl -s -X POST "http://localhost:8000/ask?question=Long%20task"
(venv-day14) 
```

(Chạy trong Ubuntu WSL2, vì Windows không gửi được `SIGTERM`.)

**Nhận xét: request đang chạy vẫn hoàn thành, app tắt êm.** Thứ tự trong log:

1. `kill -TERM` gửi `SIGTERM` (signal 15) trong lúc request `/ask` đang xử lý → uvicorn in `Shutting down`: **ngừng nhận kết nối mới** nhưng chưa thoát.
2. Request đang chạy (**in-flight request**) vẫn được xử lý xong: `POST /ask ... 200 OK` và curl nhận đủ câu trả lời JSON. Request không bị cắt ngang.
3. Sau đó uvicorn mới chạy phần shutdown của `lifespan`: `Graceful shutdown initiated...` (đặt `_is_ready = False` để `/ready` trả 503; chờ `_in_flight_requests` về 0, tối đa 30 giây) → `Shutdown complete`.
4. `Finished server process`, job `[1]` báo `Done` (process thoát bình thường, không bị kill).
5. Dòng `Received signal 15 — uvicorn will handle graceful shutdown` in **cuối cùng**: khi chạy, uvicorn tạm thay handler `SIGTERM` của app bằng handler của chính nó; lúc thoát thì trả lại handler cũ và chuyển lại signal cho nó. Handler của app chỉ **log**, không tự `sys.exit()`, nên không cản việc tắt. (Nếu handler tự viết nuốt signal mà không để uvicorn xử lý thì process không bao giờ tắt; README Part 6 cảnh báo đúng điểm này.)

So với Windows (`taskkill /F` ở 5.1): process bị giết ngay, log dừng ngang, không có dòng shutdown nào. Đó là khác biệt giữa `SIGTERM` (xin tắt, app được dọn dẹp) và `SIGKILL` (giết ngay, không bắt được). `docker stop` gửi `SIGTERM`, chờ 10 giây rồi mới `SIGKILL`.

### Exercise 5.3: Stateless design

Code: `05-scaling-reliability/production/app.py`.

- **Vì sao state trong RAM hỏng khi chạy nhiều instance:** mỗi instance là một process riêng với **bộ nhớ riêng**. Load balancer (Nginx) chia request theo vòng (**round-robin**), nên các request liên tiếp của cùng một user rơi vào các instance khác nhau. Request 1 vào instance A lưu history trong RAM của A; request 2 vào instance B thì B không thấy gì → user bị "quên" giữa chừng cuộc hội thoại. Ngoài ra state trong RAM **mất khi restart / deploy / scale xuống**, và không chia sẻ được giữa các worker. Có thể dùng **sticky session** (luôn gửi một user về cùng instance) để chữa cháy, nhưng instance đó chết là mất session và tải không chia đều được.
- **Code đã chuyển state sang đâu:** sang **Redis**, một kho dữ liệu dùng chung mà mọi instance cùng đọc / ghi:
  - Mỗi session lưu thành một key `session:<session_id>`, value là JSON chứa `history` (`save_session` dùng `SETEX` với **TTL 3600 s**, hết hạn tự xoá, không phình Redis).
  - `/chat` nhận `session_id` từ client (không có thì tạo `uuid4` mới), `append_to_history` đọc session từ Redis → thêm message → ghi lại; giữ tối đa 20 message (10 lượt).
  - Instance **không giữ gì trong RAM** ngoài `INSTANCE_ID` (chỉ để hiển thị `served_by`), nên **bất kỳ instance nào cũng phục vụ được bất kỳ request nào**, và có thể thêm / bớt / restart instance thoải mái → đây mới là **horizontal scaling**.
- **Hạn chế của code mẫu:**
  - Nếu không kết nối được Redis lúc import, app **lặng lẽ fallback** về dict trong RAM (`USE_REDIS = False`), tức là quay lại đúng anti-pattern mà vẫn chạy như không có gì. Production nên để `/ready` trả 503 thay vì fallback.
  - `append_to_history` là **read-modify-write** không atomic (`GET` → sửa → `SETEX`): hai request cùng session tới hai instance cùng lúc có thể ghi đè nhau, mất message (**race condition**). Dùng Redis list (`RPUSH` + `LTRIM`) hoặc transaction thì an toàn hơn.

### Exercise 5.4: Load balancing (`docker compose -p k5-day14-scaling up -d --build --scale agent=3`)

Chạy trong `05-scaling-reliability/production/` (tạo `.env.local` trống trước). Stack: 3 agent + Redis + Nginx, Nginx mở cổng **8080**, endpoint `POST /chat`.

```text
NAME                       STATUS                    PORTS
k5-day14-scaling-agent-1   Up 8 seconds (healthy)    8000/tcp
k5-day14-scaling-agent-2   Up 8 seconds (healthy)    8000/tcp
k5-day14-scaling-agent-3   Up 8 seconds (healthy)    8000/tcp
k5-day14-scaling-nginx-1   Up 7 seconds              0.0.0.0:8080->80/tcp
k5-day14-scaling-redis-1   Up 19 seconds (healthy)   6379/tcp
```

**Gửi 9 request liên tiếp** (mỗi request in trường `served_by` của response):

```text
Request 1 -> instance-d22888
Request 2 -> instance-fc6543
Request 3 -> instance-d44654
Request 4 -> instance-d22888
Request 5 -> instance-fc6543
Request 6 -> instance-d44654
Request 7 -> instance-d22888
Request 8 -> instance-fc6543
Request 9 -> instance-d44654
```

**Log của các agent** (`docker compose -p k5-day14-scaling logs agent`): cả 3 instance cùng nhận `POST /chat` và cùng dùng Redis:

```text
agent-1  | INFO:app:Starting instance instance-d22888
agent-1  | INFO:app:Storage: Redis ✅
agent-2  | INFO:app:Starting instance instance-fc6543
agent-2  | INFO:app:Storage: Redis ✅
agent-3  | INFO:app:Starting instance instance-d44654
agent-3  | INFO:app:Storage: Redis ✅
agent-1  | INFO:     172.18.0.6:47518 - "POST /chat HTTP/1.1" 200 OK
agent-2  | INFO:     172.18.0.6:52948 - "POST /chat HTTP/1.1" 200 OK
agent-3  | INFO:     172.18.0.6:58402 - "POST /chat HTTP/1.1" 200 OK
...
```

**Tắt 1 instance** (`docker stop k5-day14-scaling-agent-1`) rồi gửi tiếp:

```text
After stop 1 -> 200 instance-fc6543
After stop 2 -> 200 instance-d44654
After stop 3 -> 200 instance-fc6543
After stop 4 -> 200 instance-d44654
After stop 5 -> 200 instance-fc6543
After stop 6 -> 200 instance-d44654
```

**Giải thích:**

- Nginx chia request theo **round-robin**: thứ tự `d22888 → fc6543 → d44654` lặp lại đều, mỗi instance nhận 1/3 tải. `docker compose --scale agent=3` tạo 3 container cùng tên service `agent`; DNS nội bộ của Docker (resolver `127.0.0.11` trong `nginx.conf`) trả về cả 3 IP cho tên `agent`, và upstream `agent_cluster` của Nginx xoay vòng giữa chúng.
- Mọi request trong log đến từ `172.18.0.6`, là IP của Nginx trong network nội bộ: client không gọi thẳng agent được, chỉ đi qua **load balancer**.
- Khi tắt `agent-1`, **mọi request vẫn trả 200**, tải tự chuyển sang 2 instance còn lại (`fc6543` / `d44654` luân phiên). `proxy_next_upstream error timeout http_503` (tối đa 3 lần) giúp Nginx thử instance khác khi một instance lỗi hoặc trả 503 → user không thấy lỗi. Đó là **high availability**: không có **single point of failure** ở tầng agent (Redis và Nginx thì vẫn là điểm đơn lẻ trong demo này).

### Exercise 5.5: Test stateless (`python test_stateless.py`)

```text
============================================================
Stateless Scaling Demo
============================================================

Session ID: 278468c2-4f8a-42e0-9b12-4c5eddb4ef49

Request 1: [instance-d22888]
  Q: What is Docker?
  A: Container là cách đóng gói app để chạy ở mọi nơi. Build once, run anywhere!...

Request 2: [instance-fc6543]
  Q: Why do we need containers?
  A: Agent đang hoạt động tốt! (mock response) Hỏi thêm câu hỏi đi nhé....

Request 3: [instance-d44654]
  Q: What is Kubernetes?
  A: Đây là câu trả lời từ AI agent (mock). Trong production, đây sẽ là response từ O...

Request 4: [instance-d22888]
  Q: How does load balancing work?
  A: Agent đang hoạt động tốt! (mock response) Hỏi thêm câu hỏi đi nhé....

Request 5: [instance-fc6543]
  Q: What is Redis used for?
  A: Tôi là AI agent được deploy lên cloud. Câu hỏi của bạn đã được nhận....

------------------------------------------------------------
Total requests: 5
Instances used: {'instance-d22888', 'instance-fc6543', 'instance-d44654'}
✅ All requests served despite different instances!

--- Conversation History ---
Total messages: 10
  [user]: What is Docker?...
  [assistant]: Container là cách đóng gói app để chạy ở mọi nơi. Build once...
  [user]: Why do we need containers?...
  [assistant]: Agent đang hoạt động tốt! (mock response) Hỏi thêm câu hỏi đ...
  [user]: What is Kubernetes?...
  [assistant]: Đây là câu trả lời từ AI agent (mock). Trong production, đây...
  [user]: How does load balancing work?...
  [assistant]: Agent đang hoạt động tốt! (mock response) Hỏi thêm câu hỏi đ...
  [user]: What is Redis used for?...
  [assistant]: Tôi là AI agent được deploy lên cloud. Câu hỏi của bạn đã đư...

✅ Session history preserved across all instances via Redis!
```

**Giải thích kết quả:** 5 câu hỏi thuộc **cùng một session** (`278468c2-...`) nhưng được **cả 3 instance khác nhau** phục vụ (`d22888`, `fc6543`, `d44654`, xoay vòng). Dù vậy, khi đọc lại history thì đủ **10 message** (5 câu hỏi + 5 câu trả lời) theo đúng thứ tự. Nghĩa là mỗi instance ghi vào và đọc ra từ **cùng một Redis**, không instance nào giữ history trong RAM của riêng nó. Nếu state nằm trong RAM, instance thứ hai chỉ thấy 1 phần history và kết quả sẽ thiếu message. Đây là điều kiện để **scale ngang** (thêm / bớt / restart instance) mà user không mất cuộc hội thoại, đúng như phần tắt `agent-1` ở 5.4 đã chứng minh.

(Lưu ý: `test_stateless.py` chỉ gửi 5 câu rồi đọc history, không tự tắt instance; phần tắt instance là thí nghiệm thêm ở 5.4.)

**Giải thích kết quả:**

---

## Part 6: Final Project

### Output `check_production_ready.py` (bản đầy đủ, có Docker)

```text
(dán phần "📋 Checklist Deliverable" — phải là PASS 12/12)
```

---

## AI log

| # | Hỏi AI gì | Nhận được gì | Tôi tự sửa / kiểm tra thế nào |
|---|---|---|---|
| 1 | Claude Code: tạo khung file `MISSION_ANSWERS.md` | Khung các mục Part 1–6 + AI log, chưa có câu trả lời | Đối chiếu với `DAY14_DELIVERY_CHECKLIST.md`, `CHECKPOINTS.md`, `RUBRIC.md` |
| 2 | Claude Code: nhận xét rồi viết lại bảng Exercise 1.1 từ bản tôi tự điền (5 dòng) | Bảng 7 anti-pattern có tên, dòng code, giải thích kèm thuật ngữ (12-Factor, bind address, liveness probe, PII…) | Tự đọc lại từng dòng, đối chiếu số dòng với `01-localhost-vs-production/develop/app.py` |
| 3 | Claude Code: trình bày lại output Exercise 1.2 (client + server) và viết phần nhận xét | Hai khối output đọc từ terminal của tôi (đã che key), nhận xét nối từng dòng output với anti-pattern ở 1.1 | Tự chạy lại `curl.exe` và đối chiếu với terminal server |
| 4 | Claude Code: điền bảng so sánh Exercise 1.3 | Bảng 6 dòng (Config, Secrets, Health check, Logging, Shutdown, Host/Port) kèm vị trí code trong `production/app.py`, `config.py` | Đọc lại từng ô, mở 2 file `app.py` đặt cạnh nhau để đối chiếu |
| 5 | Claude Code: nhận xét và viết lại 4 câu trả lời Exercise 2.1 (tôi chưa hiểu câu 4) | Giải thích base image, WORKDIR, layer cache, CMD vs ENTRYPOINT (exec form / shell form, PID 1) | Đối chiếu với `02-docker/develop/Dockerfile`; sẽ thử `docker run --rm my-agent:develop python --version` để thấy CMD bị ghi đè |
| 6 | Claude Code: viết 3 câu Exercise 2.3 (bảng size tôi tự đo và điền), chạy stack 2.4 và viết sơ đồ + giải thích | Câu trả lời stage 1/2, lý do image nhỏ; Claude chạy `docker compose up`, `curl` qua Nginx, ghi output thật vào bài | Kiểm tra lại số trong bảng 2.3 với `docker images my-agent` (240 MB / 1.67 GB); đọc `docker-compose.yml` và `nginx.conf` để đối chiếu sơ đồ |
| 7 | Claude Code: nhận xét 4.1–4.3 tôi tự làm, viết phần giải thích Exercise 4.4 (cost guard) và so sánh rate limit / cost guard | Giải thích cách tính cost, 2 tầng budget (402 / 503), lưu in-memory và hạn chế, reset theo ngày; bảng so sánh | Đọc lại `cost_guard.py` và `/ask` trong `app.py` để đối chiếu số dòng và mã lỗi |
| 8 | Claude Code: chạy bổ sung các lệnh `curl -i` cho 4.2 (login, không token, token giả, có token) và 4.3 (chi tiết response 429), viết giải thích JWT và thuật toán rate limit | Output thật (token đã che), giải thích luồng JWT, 401 vs 403, Sliding Window Log | Đối chiếu `auth.py` (`verify_token`) và `rate_limiter.py`; vòng lặp 15 request ở 4.3 là tôi tự chạy |
| 9 | Claude Code: làm Exercise 5.1 (đọc `/health`, `/ready`, chạy app ở cổng 8005, gọi thử) | Câu trả lời liveness vs readiness kèm bảng so sánh, output `curl -i` và log khởi động thật | Đọc lại dòng 104–170 của `05-scaling-reliability/develop/app.py` |
| 10 | Claude Code: nhận xét output 5.2 (tôi tự chạy trong WSL), viết 5.3, chạy stack 5.4 (3 agent) và `test_stateless.py` cho 5.5 rồi viết giải thích | Phân tích thứ tự log graceful shutdown, giải thích stateless + hạn chế code mẫu, output thật của 5.4 / 5.5 | Đối chiếu log 5.2 với `lifespan` trong `develop/app.py`; đọc `production/app.py`, `nginx.conf` |
| 11 | | | |
