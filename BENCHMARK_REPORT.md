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

### 4.1 Screenshot

- [ ] Dashboard Grafana khi đang có tải (thấy rõ Golden Signals)
- [ ] Một trace chậm trong Jaeger (thấy span nào chiếm thời gian)
- [ ] Một trace lỗi trong Jaeger (có event `exception`)
- [ ] Trang Alerts của Prometheus với ít nhất 1 alert đang **firing**

### 4.2 Thí nghiệm "phá hệ thống"

| # | Thay đổi | Alert nào firing | Thời gian từ lúc đổi → firing | Panel nào phản ánh rõ nhất |
|---|---|---|---|---|
| A | `MOCK_ERROR_RATE=0.2` | | | |
| B | `MOCK_SLOW_RATE=0.3` | | | |
| C | `LLM_MODEL=mock-llm-large` | | | |
| D | `docker pause` 1 instance | | | |
| E | `AGENT_VERSION=v1` | | | |

**Thí nghiệm E không làm alert nào firing. Bạn sẽ phát hiện loại sự cố này trong production bằng cách nào?**

...

### 4.3 Debug bằng trace

Chọn 1 trace chậm nhất bạn tìm được:

- `trace_id`: ...
- Tổng thời gian: ... ms
- Span chiếm nhiều thời gian nhất: ... (... ms, ...%)
- Dòng log tương ứng (grep theo `trace_id`):

```json

```

---

## 5. Benchmark scaling (production, Locust headless, 100 users, 2 phút)

| Số instance | RPS | p50 (ms) | p95 (ms) | p99 (ms) | Failures |
|---|---|---|---|---|---|
| 1 | | | | | |
| 3 | | | | | |

**Giải thích kết quả:** Scale có cải thiện p95 không? Nút thắt nằm ở đâu?

...

---

## 6. Kết luận

- 3 điều bạn học được về benchmark & observability:
  1. ...
  2. ...
  3. ...
- Nếu đưa agent này lên production thật, bạn sẽ thêm metric / alert nào nữa?

...
