# RTO/RPO Evidence — Lab 23

Các số liệu trong bảng lấy từ lần Drill 2 hợp lệ gần nhất. Mọi Evidence đều trỏ tới file và dòng log thật.

## 1. Drill 1 — không có DR (baseline)

| Chỉ số | Giá trị | Cách đo | Evidence |
|---|---|---|---|
| t_outage | baseline kill | chaos kill | `chaos/chaos-events.jsonl:1` |
| Request fail đầu tiên | Có request lỗi sau khi Region A bị kill | dòng `ok:false` đầu tiên | `reports/drill-1-nodr.jsonl:17` |
| Request thành công sau đó | Không có recovery tự động | measure_rto | `reports/drill-1-nodr.jsonl:46` |
| RTO | `NO_RECOVERY` | `tools/measure_rto.py` | `reports/drill-1-nodr.jsonl:46` |

## 2. Drill 2 — có DR

| Mốc | +giây từ t_outage | Cách đo | Evidence |
|---|---:|---|---|
| t_outage (mốc 0) | 0.0 | `action:kill` | `chaos/chaos-events.jsonl:3` |
| User thấy lỗi đầu tiên | +0.1s | dòng `ok:false` đầu tiên | `reports/drill-2-withdr.jsonl:1` |
| Health check phát hiện | +16.0s | `to:UNHEALTHY, region:a` | `reports/health-events.jsonl:1` |
| Snapshot restore xong | +27.5s | `step:2_restore_snapshot` | `reports/failover-events.jsonl:2` |
| Region phụ ready | +32.8s | `step:4_wait_ready` | `reports/failover-events.jsonl:4` |
| DNS cutover | +43.4s | `step:5_dns_cutover` | `reports/failover-events.jsonl:5` |
| **RTO đo được** | **+44.7s** | request `ok:true` đầu tiên từ B | `reports/drill-2-withdr.jsonl:16` |

| Chỉ số | Đo được | Mục tiêu | Verdict |
|---|---:|---:|---|
| RTO — Inference API | 44.7s | 300s (5 phút) | PASS |
| RPO — Vector DB | 6.0s / 3 docs | 300s (5 phút) | PASS |

## 3. RTO breakdown

| Thành phần | Giây | Nó đến từ đâu | Giảm được bằng cách nào |
|---|---:|---|---|
| Health-check detect floor | 15.0s (`5 × 3`) | `reports/health-events.jsonl:1` | Giảm interval, nhưng phải theo dõi flapping |
| Snapshot restore | 11.5s | `reports/failover-events.jsonl:2` | Snapshot nhỏ hơn hoặc restore song song |
| GPU pool warm-up | 5.3s | `reports/failover-events.jsonl:4` | Pre-warm pool hoặc provision trước |
| DNS/LB TTL cache và request convergence | Phần còn lại đến +44.7s | `reports/failover-events.jsonl:5` và `reports/drill-2-withdr.jsonl:16` | Giảm TTL có kiểm soát |

## 4. Kết luận

Drill 2 hợp lệ: traffic phục hồi từ Region A sang Region B, không có warning, RTO `44.7s` thấp hơn mục tiêu `300s`. RPO đo được là `6.0s`, tương ứng `3` document chưa có trong snapshot được restore.
