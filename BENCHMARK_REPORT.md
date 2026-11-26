# Benchmark & Observability Report — Day 14

> Mọi con số lấy từ report JSON trong `reports/` (load test, eval) và CSV trong `benchmark-results/` (Locust), không ước lượng.

**Họ tên:** Nguyen Le Dang Khoa
**Ngày chạy:** 2026-11-26
**Môi trường:** Laptop Windows 11, Intel Core i7-11800H (8 nhân / 16 luồng), 32 GB RAM. Bước 1 chạy agent `develop/` bằng Python 3.12 trên Windows (1 process uvicorn mỗi agent; load generator chạy cùng máy). Bước 2: Docker Desktop 29.6.1 (WSL2).

---

## 1. SLO của agent

| SLI | SLO | Lý do chọn ngưỡng này |
|---|---|---|
| p95 latency `/ask` | < 500 ms | Mock LLM mất ~150–250 ms / câu (p50 ≈ 185–200 ms ở mọi mức tải nhẹ). 500 ms chừa khoảng 2× cho retrieval, mạng và hàng đợi, mà người dùng chat vẫn thấy phản hồi gần như tức thì. Đặt theo **p95** chứ không theo trung bình, vì trung bình che mất các request chậm (mục 2.2). Đây cũng là ngưỡng mặc định của `load_test.py` (`--slo-p95-ms 500`). |
| Error rate `/ask` | < 2 % | Mock LLM tự lỗi ~1 % (`MOCK_ERROR_RATE=0.01`, giống provider thật thỉnh thoảng trả 429/503); các lần chạy đo được 0.67–1.67 %. 2 % chừa error budget cho lỗi upstream nhưng vẫn bắt được sự cố thật (vd provider sập → hàng chục %). |
| Accuracy trên golden dataset | ≥ 80 % (merge), mục tiêu 100 % | 80 % là **quality gate** mặc định của `run_eval.py`: v1 (4.3 %) bị chặn, v2 (91.3 %) qua. Khi đã sửa KB thì gate cuối đặt `--min-accuracy 1.0` và **không cho phép regression** câu nào so với baseline. |

---

## 2. Benchmark hiệu năng (develop, `load_test.py`)

### 2.1 Concurrency vs latency (agent v2)

`python load_test.py --url http://localhost:8002 --requests 300 --concurrency <c> --out reports/load_v2_c<c>.json`

| Concurrency | RPS | p50 (ms) | p95 (ms) | p99 (ms) | Error rate | SLO |
|---|---|---|---|---|---|---|
| 1 | 4.25 | 200.9 | 233.6 | 1696.8 | 1.33 % | ✅ PASS |
| 10 | 38.66 | 197.0 | 474.2 | 1719.4 | 0.67 % | ✅ PASS |
| 50 | 96.35 | 200.3 | 537.7 | 1756.7 | 1.67 % | ❌ FAIL (p95) |
| 100 | 48.93 | 638.0 | 3771.9 | 4139.0 | 1.33 % | ❌ FAIL (p95) |

**Nhận xét:** RPS **không tăng tuyến tính** theo concurrency.

- **1 → 10:** gần tuyến tính (4.25 → 38.66, ×9.1). Theo **Little's Law** `RPS ≈ concurrency / latency`: 1 / 0.237 s ≈ 4.2 và 10 / 0.251 s ≈ 40, khớp số đo. Agent là async (mock LLM `await asyncio.sleep`), thời gian chủ yếu là *chờ* upstream, nên một event loop xử lý song song nhiều request được mà p50 gần như không đổi (~200 ms).
- **10 → 50:** RPS vẫn tăng (96.35) nhưng kém xa mức lý tưởng 50 / 0.31 ≈ 160. p50 vẫn ~200 ms nhưng **p95 vượt SLO** (537.7 ms). Một phần do bài test ngắn (300 request, mỗi "user" chỉ ~6 request): vài request chậm 1.5 s ở cuối kéo dài tổng thời gian chạy (3.11 s), phần khác do agent bắt đầu xếp hàng.
- **50 → 100:** RPS **giảm** còn 48.93, p50 tăng gấp 3 (638 ms), p95 ≈ 3.8 s. Đã qua **điểm bão hoà**: agent chỉ có 1 process uvicorn = 1 event loop = 1 nhân CPU. Phần việc tốn CPU của mỗi request (parse JSON, pydantic, middleware Prometheus, retrieval, log) bị xếp hàng nối tiếp nhau trên event loop đó; thêm nữa load generator chạy *cùng máy* và mở 100 kết nối đồng thời, tranh CPU với agent. Từ đây thêm concurrency chỉ làm hàng đợi dài ra (latency tăng) chứ không tăng throughput. Muốn tăng tiếp phải **scale ngang** (nhiều worker / instance sau load balancer, mục 5).
- Error rate ổn định 0.67–1.67 % ở mọi mức: lỗi đến từ mock upstream (`MOCK_ERROR_RATE`), không phải do quá tải. Nghĩa là quá tải ở đây biểu hiện bằng **latency**, không bằng lỗi; chỉ nhìn error rate thì sẽ không thấy.

### 2.2 Tail latency

v2 khởi động lại với từng giá trị `MOCK_SLOW_RATE`, cùng `--requests 300 --concurrency 20` (`reports/load_v2_slow_<rate>.json`).

| `MOCK_SLOW_RATE` | avg (ms) | p95 (ms) | p99 (ms) |
|---|---|---|---|
| 0 | 206.1 | 461.0 | 493.3 |
| 0.03 (mặc định) | 261.2 | 473.3 | 1723.0 |
| 0.1 | 387.5 | 1694.4 | 1744.1 |

**Nhận xét:**

- Một request "slow" bị cộng thêm 1500 ms (`MOCK_SLOW_MS`). Từ 0 → 3 % slow: **avg chỉ tăng 27 %** (206 → 261 ms) và p95 gần như không đổi (461 → 473 ms), nhưng **p99 tăng 3.5×** (493 → 1723 ms). 3 % request chậm nằm hết ở phần đuôi trên p97, nên p99 "bắt" được ngay trong khi trung bình bị 97 % request nhanh pha loãng.
- Từ 3 % → 10 %: số request chậm vượt quá 5 % nên rơi vào cả **p95** (473 → 1694 ms, SLO FAIL), còn avg vẫn chỉ ~388 ms, nhìn qua tưởng "vẫn dưới 500 ms, ổn".
- **Chỉ số nhạy nhất với request chậm là p99** (rồi tới p95 khi tỉ lệ chậm vượt 5 %); average kém nhạy nhất.
- **Vì sao SLO không nên đặt theo average:** average là một con số "trung hoà", không mô tả trải nghiệm của ai cả. Với `MOCK_SLOW_RATE=0.1`, avg 388 ms (đạt nếu SLO là avg < 500 ms) trong khi cứ 10 người thì 1 người chờ gần 1.7 s. Một user gửi nhiều câu trong một phiên sẽ gần như chắc chắn gặp request chậm. SLO theo **p95 / p99** gắn trực tiếp với "bao nhiêu % người dùng được phục vụ đủ nhanh", và báo động sớm khi phần đuôi xấu đi.

---

## 3. Benchmark chất lượng (develop, `eval/run_eval.py`)

| Chỉ số | Agent v1 | Agent v2 | Agent v2 (sau khi sửa KB) |
|---|---|---|---|
| Accuracy | 4.3 % (1/23) | 91.3 % (21/23) | 100.0 % (23/23) |
| Avg tokens / câu | 56.9 | 109.1 | 117.2 |
| Tổng cost (USD) | 0.000402 | 0.000762 | 0.000821 |
| Cost / câu đúng (USD) | 0.00040155 | 0.00003627 | 0.00003570 |
| Quality gate | FAIL (accuracy < 80 %) | PASS (≥ 80 %, 0 regression so với v1) | PASS (≥ 100 %, 0 regression so với v2) |

Lệnh: `eval_v1` → `--url :8001`; `eval_v2` → `--url :8002 --baseline reports/eval_v1.json`; `eval_v2_fixed` → `--url :8002 --min-accuracy 1.0 --baseline reports/eval_v2.json`.

**Những câu v2 trả lời sai ban đầu và cách bạn sửa:**

| ID | Keyword bị thiếu | Bạn đã sửa gì |
|---|---|---|
| `security-02` ("JWT khác gì API key trong xác thực?") | `token`, `hết hạn`, `chữ ký` | Retrieval không tìm thấy tài liệu nào (KB không có keyword `jwt`), v2 trả câu chung chung. Thêm entry keyword `["jwt", "api key", "xác thực"]`: API key là chuỗi bí mật cố định, không tự hết hạn; JWT là token có chữ ký số, chứa thông tin user và thời điểm hết hạn, server chỉ kiểm tra chữ ký. |
| `scaling-03` ("Kubernetes HPA scale dựa trên những chỉ số nào?") | `cpu`, `memory`, `custom metrics` | KB không có gì về Kubernetes. Thêm entry keyword `["hpa", "kubernetes", "autoscal"]`: HPA tăng giảm số pod dựa trên CPU, memory hoặc custom metrics (RPS, độ dài hàng đợi). |

Sửa trong `KNOWLEDGE_BASE` của `develop/utils/mock_llm.py` (không sửa `golden_dataset.jsonl`), chép sang `production/utils/mock_llm.py` cho stack Docker. Trước khi thêm, kiểm tra không câu nào khác trong dataset chứa `jwt` / `api key` / `kubernetes` / `hpa`, để entry mới không "cướp" retrieval của câu đang đúng. Kết quả eval cuối: 2 câu được cải thiện, **0 regression**.

**Nhận xét về trade-off chất lượng / chi phí / latency giữa v1 và v2:**

- **Chất lượng:** v2 (RAG: retrieval → đưa context vào prompt) tăng accuracy từ 4.3 % lên 91.3 %, rồi 100 % chỉ bằng cách bổ sung tài liệu, không đổi model hay code. Chất lượng của RAG bị giới hạn bởi **độ phủ của knowledge base**: câu nào retrieval không tìm được tài liệu thì v2 tệ như v1.
- **Chi phí:** v2 tốn gần **gấp đôi token mỗi câu** (56.9 → 109.1) vì prompt có thêm context, nên tổng cost tăng 1.9× (0.000402 → 0.000762 USD). Nhưng tính trên **mỗi câu trả lời đúng** thì v2 rẻ hơn **~11 lần** (0.000402 → 0.0000363 USD), vì v1 tiêu tiền cho câu trả lời sai. Sửa KB tăng thêm ~7 % token nhưng cost / câu đúng còn giảm nhẹ (0.0000357). Vì vậy so sánh 2 phiên bản phải dùng **cost / câu đúng**, không dùng cost / câu.
- **Latency:** v2 có thêm bước retrieval (~20 ms) và prompt dài hơn (latency mock tăng theo số token), p50 eval 167 ms (v1) → 202 ms (v2), tức chậm hơn ~35 ms, vẫn xa dưới SLO 500 ms. (p95 của từng lần eval chỉ trên 23 câu nên nhảy theo việc có trúng request "slow" 1.5 s hay không: 1555.8 ms ở `eval_v2` nhưng 237.6 ms ở `eval_v2_fixed`; số này không dùng để kết luận v2 nhanh hay chậm hơn.)
- Kết luận: đổi ~35 ms latency và ~2× token lấy accuracy tăng từ 4 % lên 100 % là **đáng**.

**Quality gate trong CI:** `run_eval.py` trả **exit code 0** khi gate PASS và **exit code 1** khi FAIL (accuracy dưới `--min-accuracy` hoặc có regression so với `--baseline`). Đã thử: eval v1 với `--baseline reports/eval_v2.json` → `exit=1`; eval v2 sau khi sửa KB → `exit=0`. CI chỉ cần chạy lệnh này như một bước của job: exit code khác 0 làm step fail → job fail → nếu job được đặt là **required status check** trong branch protection của `main` thì nút Merge bị khoá cho tới khi sửa xong. File job: [`.github/workflows/eval.yml`](.github/workflows/eval.yml).

---

## 4. Observability stack (production)

Stack chạy bằng `docker compose -p k5-day14-observability up -d --build` trong `production/`: agent (1–3 instance) + Nginx + Prometheus + Grafana + Jaeger + Locust. Unit test alert rules: `promtool test rules prometheus/alert_rules_test.yml` → `SUCCESS`.

Hai lỗi của bản mẫu đã gặp và cách xử lý:

- **Agent crash loop `ModuleNotFoundError: No module named 'uvicorn'`:** `Dockerfile` copy package (`pip install --user`) vào `/home/agent/.local`, nhưng user `agent` được tạo với home `/app` (`useradd -d /app`), nên Python tìm user site-packages ở `/app/.local`. Sửa: thêm `ENV PYTHONUSERBASE=/home/agent/.local` trong `production/Dockerfile`.
- **`docker compose run locust` tự đổi số agent:** `run` khởi động lại service phụ thuộc (`nginx` → `agent`) theo `deploy.replicas: 2`, nên lần chạy "1 instance" thành 2 và "3 instance" bị giảm về 2 (thấy rõ trong `count(up{job="agent"}==1)` của Prometheus). Sửa: chạy Locust với `--no-deps`, và kiểm tra số target `up` trước / sau mỗi lần đo (mục 5).

### 4.1 Screenshot

- [x] Dashboard Grafana khi đang có tải (thấy rõ Golden Signals): `screenshots/grafana-dashboard.png`
- [x] Một trace chậm trong Jaeger (thấy span nào chiếm thời gian): `screenshots/jaeger-slow-trace.png`
- [x] Một trace lỗi trong Jaeger (có event `exception`): `screenshots/jaeger-error-trace.png`
- [x] Trang Alerts của Prometheus với **2 alert cùng firing** (`AgentHighErrorRate` + `AgentHighLatencyP95`, lúc chạy A + B): `screenshots/prometheus-alert-firing.png`

### 4.2 Thí nghiệm "phá hệ thống"

Điều kiện chung: 2 agent, Locust headless **10 user** chạy nền suốt bài; đổi cấu hình bằng file `.env` rồi `docker compose up -d --no-deps --scale agent=2 agent` (D dùng `docker pause`). **t = 0** là lúc lệnh áp dụng xong; thời gian đọc từ API `/api/v1/alerts` của Prometheus, poll mỗi 2 s.

Vì sao 10 user chứ không phải 30 như README: với tải lớn, `LLMCostBurnRateHigh` **đã firing ngay ở cấu hình bình thường**. Khi chạy 100 user (~56 req/s), cost đo được là $6.35/giờ, vượt ngưỡng $2/giờ, vì 56 req/s × ~$0.00004 / request × 3600 ≈ $8/giờ. Ở 10 user, baseline chỉ **$0.59/giờ**, nên thí nghiệm C mới đo được ý nghĩa. Nghĩa là ngưỡng cost alert phải gắn với lưu lượng dự kiến, không thể là con số cố định.

| # | Thay đổi | Alert nào firing | Thời gian từ lúc đổi → firing | Panel nào phản ánh rõ nhất |
|---|---|---|---|---|
| A | `MOCK_ERROR_RATE=0.2` | `AgentHighErrorRate` (critical) | pending t+22 s → **firing t+80 s** | 🚦 *Error rate* (đỏ) và 🤖 *LLM calls ok / error*; Jaeger có nhiều trace `error=true` |
| B | `MOCK_SLOW_RATE=0.3` (A vẫn bật) | `AgentHighLatencyP95` (warning) | pending t+26 s → **firing t+149 s** | ⏱️ *Latency p50 / p95 / p99 / avg*: p95, p99 tách hẳn lên ~1.7 s trong khi p50 gần như không đổi |
| C | `LLM_MODEL=mock-llm-large` | `LLMCostBurnRateHigh` (warning) | pending t+58 s → **firing t+175 s** | 🤖 *Cost USD / giờ*: $0.59 → **$6.91/giờ** (×11.7 khi cửa sổ `rate[5m]` chưa đầy; về lý thuyết ×16); p95 `/ask` lên 0.70 s (latency factor 2.5) |
| D | `docker pause` agent-1 | `AgentInstanceDown` (critical) | pending t+12 s → **firing t+44 s** | 🚦 *Instances up* giảm 2 → 1. Nginx log ghi **10 lỗi 504** (`upstream timed out`) trong lúc pause, nhưng error rate *của agent* chỉ 1.5 % (vẫn xanh) |
| E | `AGENT_VERSION=v1` | **Không có** (sau 150 s, `/api/v1/alerts` rỗng) | — | 🤖 *Retrieval hit ratio* mất hẳn (hit 5.57/s → 0), *tokens/s* giảm một nửa (input 439 → 216, output 234 → 118), 🧩 bảng version đổi sang `v1`. p95 0.197 s, error 0.93 %: mọi Golden Signal vẫn xanh |

Nhận xét:

- **Thời gian tới firing ≈ thời gian `rate()` "nhìn thấy" vấn đề + `for`.** A: `rate[1m]` cần ~20 s để error rate vượt 5 % (pending), cộng `for: 1m` → firing ở 80 s. B: `for: 2m` nên ~150 s. C chậm nhất vì `rate[5m]` của cost tăng dần (58 s mới vượt $2/giờ) rồi thêm `for: 2m`. D nhanh nhất (44 s): `up == 0` đúng ngay lần scrape đầu tiên bị timeout, `for: 30s`. `for` là đánh đổi giữa phát hiện nhanh và tránh báo động giả do một đợt nhiễu ngắn.
- **D, vì sao 504 không lên dashboard của agent:** lỗi 504 xảy ra ở Nginx (chờ quá `proxy_read_timeout`); request chưa bao giờ được agent xử lý xong nên metric của agent không đếm nó. Metric chỉ phản ánh những gì *chính service đó* nhìn thấy, nên production cần thêm metric ở tầng load balancer và **synthetic check** từ bên ngoài (gọi URL public như người dùng). Dùng `pause` chứ không `stop`: container bị stop biến mất khỏi Docker DNS nên Prometheus (`dns_sd_configs`) coi như target không còn tồn tại, không phải "down", và không có alert.

**Thí nghiệm E không làm alert nào firing. Bạn sẽ phát hiện loại sự cố này trong production bằng cách nào?**

Đây là lỗi **chất lượng** (trả lời sai), không phải lỗi hạ tầng: request vẫn nhanh, vẫn 200, thậm chí rẻ hơn. Cách phát hiện:

1. **Chặn trước khi deploy:** quality gate eval trên golden dataset trong CI (Exercise 7.3, `.github/workflows/eval.yml`). v1 chỉ đạt 4.3 % nên `run_eval.py` trả exit code 1 và PR bị chặn.
2. **Alert trên metric "proxy" của chất lượng:** đặt alert khi *retrieval hit ratio* giảm mạnh, hoặc khi `agent_info{version}` / tokens mỗi request đổi bất thường sau một lần deploy. E làm hit ratio về 0, rất dễ bắt.
3. **Eval online / giám sát chất lượng khi chạy:** lấy mẫu một phần traffic thật để chấm bằng LLM-as-judge hoặc so keyword; thu feedback người dùng (thumbs up / down) thành metric và alert khi tỉ lệ tệ đi.
4. **Canary deploy:** đưa bản mới cho một ít traffic, so các chỉ số chất lượng với bản cũ trước khi rollout hết.

### 4.3 Debug bằng trace

Chọn 1 trace chậm nhất bạn tìm được:

- `trace_id`: `5106d45d01e9e475b3d4fbc48d74fa08`
- Tổng thời gian: **1720.9 ms** (`POST /ask`)
- Span chiếm nhiều thời gian nhất: `chat mock-llm-small` (**1692.2 ms, 98.3 %**). Các span còn lại: `agent.run` 1718.1 ms (span cha), `agent.retrieve_context` 25.7 ms (1.5 %). Đây là request rơi vào nhóm "slow" của mock LLM (+1500 ms); nút thắt nằm ở **upstream LLM**, không phải code của agent.
- Dòng log tương ứng (`docker compose -p k5-day14-observability logs agent | grep 5106d45d...`):

```json
{"ts": "2026-11-26T11:30:10.102624+00:00", "level": "INFO", "service": "observable-agent", "instance": "c4c21ada9a00", "event": "llm_call", "trace_id": "5106d45d01e9e475b3d4fbc48d74fa08", "span_id": "b2c6fc5b23a5e265", "request_id": "e0c714bd3edb", "model": "mock-llm-small", "agent_version": "v2", "input_tokens": 74, "output_tokens": 37, "cost_usd": 3.3e-05, "llm_ms": 1692.2}
{"ts": "2026-11-26T11:30:10.102945+00:00", "level": "INFO", "service": "observable-agent", "instance": "c4c21ada9a00", "event": "http_request", "trace_id": "5106d45d01e9e475b3d4fbc48d74fa08", "span_id": "b2c6fc5b23a5e265", "request_id": "e0c714bd3edb", "method": "POST", "route": "/ask", "status": 200, "ms": 1720.4}
```

`llm_ms` (1692.2) khớp span `chat` (1692.2 ms) và `ms` (1720.4) khớp span gốc (1720.9 ms).

Trace lỗi trong ảnh `jaeger-error-trace.png`: `trace_id` `5a9a5f251966ed9d1b8944ce36b4fe49`; span `chat mock-llm-small` có event `exception` `Upstream LLM error: 503 Service Unavailable (mock)`; log tương ứng:

```json
{"ts": "2026-11-26T11:28:18.368373+00:00", "level": "INFO", "service": "observable-agent", "instance": "c4c21ada9a00", "event": "llm_error", "trace_id": "5a9a5f251966ed9d1b8944ce36b4fe49", "span_id": "77986c34a132f0fd", "request_id": "76e140ea31aa", "error": "Upstream LLM error: 503 Service Unavailable (mock)"}
{"ts": "2026-11-26T11:28:18.369656+00:00", "level": "INFO", "service": "observable-agent", "instance": "c4c21ada9a00", "event": "http_request", "trace_id": "5a9a5f251966ed9d1b8944ce36b4fe49", "span_id": "77986c34a132f0fd", "request_id": "76e140ea31aa", "method": "POST", "route": "/ask", "status": 503, "ms": 138.0}
```

Ghi chú: sắp xếp "Longest First" trong Jaeger, trace dài nhất là `e2477d48…` (3567 ms, span `chat` 3539 ms), nhưng log của *chính request đó* ghi `"ms": 1720.1`, `"llm_ms": 1692.3`. Mọi trace bị "phóng to" kiểu này đều rơi vào 11:30:00–11:30:03, đúng lúc scale lên 3 container. Span đo bằng **wall clock** (`time_ns`), còn middleware đo bằng `perf_counter` (đồng hồ đơn điệu), nên nhiều khả năng đồng hồ của VM Docker bị chỉnh nhảy lúc đó. Vì vậy mình không chọn trace đó để phân tích, và **đối chiếu trace với log** trước khi kết luận.

---

## 5. Benchmark scaling (production, Locust headless, 100 users, 2 phút)

`docker compose -p k5-day14-observability up -d --scale agent=<n> --no-recreate agent`, chờ Prometheus thấy đúng `n` target `up`, rồi `docker compose -p k5-day14-observability run --rm --no-deps locust ... --headless -u 100 -r 10 -t 2m --csv /mnt/results/scale_<n>`. Số target `up` kiểm tra lại sau khi chạy: 1 → 1 và 3 → 3. Số trong bảng là dòng `POST /ask` của `benchmark-results/scale_<n>_stats.csv`.

| Số instance | RPS | p50 (ms) | p95 (ms) | p99 (ms) | Failures |
|---|---|---|---|---|---|
| 1 | 56.33 | 190 | 270 | 1700 | 78 / 7127 (1.09 %) |
| 3 | 56.59 | 190 | 230 | 1700 | 69 / 7167 (0.96 %) |

**Giải thích kết quả:** Scale có cải thiện p95 không? Nút thắt nằm ở đâu?

- **RPS gần như không đổi (56.3 → 56.6)** vì throughput bị giới hạn bởi **chính Locust**, không phải agent. Đây là tải **closed-loop**: 100 user, mỗi user gửi request rồi chờ trả lời và nghỉ `between(0.5, 2)` s mới gửi tiếp. Mỗi vòng ≈ 0.24 s xử lý + 1.25 s nghỉ ≈ 1.5 s, nên trần là ~100 / 1.5 ≈ 67 req/s tổng (đo được 62 req/s gồm cả `/health`). Agent nhanh hơn cũng không làm user gửi nhiều hơn.
- **p95 cải thiện nhẹ (270 → 230 ms), p50 và p99 không đổi.** Với 1 instance (giới hạn 0.5 CPU), agent dùng ~0.16–0.19 CPU dưới tải này, chưa bão hoà, chỉ có chút xếp hàng ở phần đuôi; chia cho 3 instance thì hàng đợi đó biến mất. p99 = 1700 ms ở cả hai là các request "slow" của mock LLM (+1500 ms), không liên quan tới số instance.
- **Nút thắt là latency của upstream LLM**: panel *LLM p95* đứng yên ~0.198 s suốt cả hai lần chạy. Mỗi request phải chờ LLM ~190 ms dù có bao nhiêu instance. Scale ngang chỉ giúp khi agent **thiếu CPU / bị bão hoà** (như concurrency 100 ở mục 2.1 với 1 process). Muốn giảm latency thật thì phải tác động vào LLM: cache câu trả lời, dùng model nhanh hơn, streaming, hoặc giảm số token của prompt.
- Bài học đo lường: lần chạy đầu (không `--no-deps`) cho kết quả "1 instance" và "3 instance" gần như trùng nhau vì thực chất cả hai đều là 2 instance. Luôn kiểm tra điều kiện thí nghiệm bằng chính hệ thống monitoring (`count(up{job="agent"}==1)`) trước khi tin vào con số.

---

## 6. Kết luận

- 3 điều bạn học được về benchmark & observability:
  1. **Đặt SLO theo percentile, không theo trung bình.** Chỉ 3 % request chậm đã làm p99 tăng 3.5× trong khi avg tăng 27 % (mục 2.2); 10 % request chậm làm vỡ SLO p95 mà avg vẫn "đẹp". Phần đuôi là thứ người dùng cảm nhận được.
  2. **Mỗi loại sự cố cần một loại tín hiệu riêng.** Metric hạ tầng (Golden Signals) bắt được lỗi, chậm, tốn tiền, instance chết (A–D) nhưng hoàn toàn mù với lỗi chất lượng (E). Lỗi chất lượng phải bắt bằng eval trên golden dataset và quality gate trong CI. Ngược lại, metric của service chỉ thấy những gì service đó thấy: 504 ở Nginx không hiện trên dashboard của agent (D).
  3. **Metrics, logs, traces phải nối với nhau bằng `trace_id`.** Metric cho biết *có* vấn đề (p95 tăng), trace cho biết *ở đâu* (span LLM chiếm 98 %), log cho biết *chi tiết* (model, token, lỗi). Và phải đối chiếu chéo: một trace "3.5 s" hoá ra là do đồng hồ nhảy, còn một benchmark "3 instance" hoá ra là 2 instance. Công cụ đo cũng có thể sai.
- Nếu đưa agent này lên production thật, bạn sẽ thêm metric / alert nào nữa?
  - **Synthetic check** từ bên ngoài gọi URL public (`/health` và một câu `/ask` mẫu) và alert khi fail; thêm metric 5xx / 504 ở tầng load balancer (nginx exporter) để bắt lỗi mà agent không thấy (D).
  - **Chất lượng:** alert khi *retrieval hit ratio* giảm dưới ngưỡng, hoặc khi phiên bản đang chạy đổi mà eval chưa chạy; eval online trên mẫu traffic thật (LLM-as-judge) và metric feedback người dùng (E).
  - **Cost theo budget:** dùng chính `DAILY_BUDGET_USD` của Part 6 làm ngưỡng (alert khi đã dùng 80 % budget ngày, hoặc burn rate dự báo vượt budget), thay vì ngưỡng cố định $2/giờ vốn firing ngay khi traffic cao.
  - **Error budget burn rate** nhiều cửa sổ (1 h và 6 h) cho SLO 99.5 %, thay cho ngưỡng error rate 5 % cố định, để alert nhanh khi lỗi tăng mạnh mà không ồn khi lỗi nhẹ.
  - **Saturation:** CPU / memory theo container và độ trễ event loop, để biết khi nào cần scale (bài concurrency 100 ở mục 2.1); kèm alert **rate limit 429** và **cost guard 402** tăng bất thường (dấu hiệu bị lạm dụng hoặc cấu hình sai).
