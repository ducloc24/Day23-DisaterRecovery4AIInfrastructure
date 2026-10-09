# Postmortem — DR Drill Lab 23

Đây là postmortem blameless của Drill 2. Mục tiêu là xác định gap của hệ thống và quy trình, không quy trách nhiệm cho cá nhân.

## 1. Tóm tắt sự cố

Region A bị mô phỏng network block trong khi hệ thống đang phục vụ traffic. Health checker phát hiện outage, failover restore snapshot vào Region B, chờ Region B ready, sau đó mới cutover Edge. Traffic được phục hồi từ Region B.

Kết quả: RTO `44.7s`, RPO `6.0s`, mất `3 docs`, verdict `PASS`.

## 2. Timeline

| ISO time | Sự kiện | Evidence |
|---|---|---|
| 2026-10-09 07:12:26Z | Chaos kill Region A bằng `netblock` | `chaos/chaos-events.jsonl:3` |
| 2026-10-09 07:12:26Z | User bắt đầu thấy request lỗi | `reports/drill-2-withdr.jsonl:1` |
| 2026-10-09 07:12:42Z | Health checker đánh dấu Region A `UNHEALTHY` | `reports/health-events.jsonl:1` |
| 2026-10-09 07:12:53Z | Snapshot restore hoàn tất | `reports/failover-events.jsonl:2` |
| 2026-10-09 07:13:00Z | Region B ready | `reports/failover-events.jsonl:4` |
| 2026-10-09 07:13:09Z | DNS/LB cutover sang B | `reports/failover-events.jsonl:5` |
| 2026-10-09 07:13:11Z | Request đầu tiên thành công từ B | `reports/drill-2-withdr.jsonl:16` |

## 3. RTO/RPO so với mục tiêu

- RTO mục tiêu: `300s`; đo được: **44.7s**; gap: **255.3s tốt hơn mục tiêu**.
- RPO mục tiêu: `300s`; đo được: **6.0s**; mất **3 docs**; gap: **294.0s tốt hơn mục tiêu**.
- Health-check detect floor: `interval × threshold = 5 × 3 = 15s`; thực tế phát hiện ở `16.0s`.
- Bước tốn nhiều thời gian nhất: tổng thời gian từ detection đến request thành công đầu tiên, gồm restore, warm-up, cutover và edge convergence.

## 4. Root cause — 5 whys

1. Vì sao request lỗi sau khi Region A mất kết nối? Vì Edge vẫn route request tới upstream A cho đến khi cutover.
2. Vì sao Edge không tự chuyển sang B ngay? Vì Edge chỉ đọc file `active_region`; health checker không tự thực hiện DNS cutover.
3. Vì sao B không thể nhận traffic ngay? Vì B ban đầu thiếu vector DB và model weights.
4. Vì sao B thiếu state mới nhất? Vì replication chạy theo chu kỳ, tạo ra replication lag.
5. Vì sao cần runbook có thứ tự nghiêm ngặt? Vì cutover trước khi B ready sẽ tạo lỗi từ cả hai region và làm RTO dài hơn.

## 5. Gap analysis

Điểm mạnh là hệ thống đạt RTO mục tiêu và có bằng chứng timestamp từ traffic, chaos, health và failover logs. Gap còn lại là RPO phụ thuộc chu kỳ replication; nếu chu kỳ tăng, số document mất có thể tăng. Ngoài ra, full-auto failover không có circuit breaker có thể gây flapping trong outage ngắn hoặc lỗi mạng tạm thời.

## 6. Action items

| # | Action | Owner | Deadline | Tác động dự kiến |
|---|---|---|---|---|
| 1 | Thêm cảnh báo khi replication lag vượt 30s | Data platform | 2026-10-16 | Giữ RPO lý thuyết dưới khoảng 30s |
| 2 | Thêm circuit breaker và cooldown cho failover | Platform | 2026-10-23 | Giảm nguy cơ flapping |
| 3 | Chạy DR drill định kỳ, lưu log và evidence tự động | SRE | 2026-10-30 | Phát hiện regression RTO/RPO |
| 4 | Kiểm tra encoding UTF-8 trong CI trên Windows và WSL | Developer experience | 2026-11-06 | Tránh report hiển thị sai tiếng Việt |

## 7. Câu hỏi bắt buộc

1. `interval × threshold = 5 × 3 = 15s`. Nó là detection floor và chiếm khoảng `15 / 44.7 = 33.6%` RTO đo được.
2. Nếu hạ interval xuống 1s, detection floor lý thuyết giảm khoảng 12s, nhưng đổi lại tăng false positive, request overhead và nguy cơ flapping.
3. Nếu outage kéo dài 6 giờ, `docs_lost` là số document có ở primary nhưng snapshot của secondary chưa có. Với khách hàng, đó là dữ liệu phải ingest lại hoặc khôi phục từ nguồn khác.
