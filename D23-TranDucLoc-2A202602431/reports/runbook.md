# Runbook 1 trang — Region chính down

Runbook này dành cho on-call xử lý lúc 3h sáng. Không cutover khi Region phụ chưa restore state và chưa trả `/readyz` HTTP 200.

## Quy trình failover

| # | Bước | Lệnh | Biết là xong khi | Owner |
|---|---|---|---|---|
| 1 | Xác nhận outage | `python chaos/kill_region.py status` | Region A không ready; Region B còn alive; xác nhận qua nhiều probe | on-call |
| 2 | Mở incident và bấm giờ RTO | `python dr/runbook.py --primary a --target b --backend fs --auto` | Có `thong_bao_incident` trong `reports/runbook-run.jsonl` | incident commander |
| 3 | Restore state ở Region phụ | Do `dr/failover.py` thực hiện; backend `fs` dùng `state/_replica/dr-artifacts` | Có `2_restore_snapshot`, `rpo_seconds`, `docs_lost`, `embed_model_version` | DR operator |
| 4 | Scale pool và chờ ready | Do `dr/failover.py` thực hiện | Có `4_wait_ready` với `ok:true`; `/readyz` Region B trả HTTP 200 | platform operator |
| 5 | DNS/LB cutover | Do `dr/failover.py` thực hiện | Có `5_dns_cutover`; `curl http://localhost:8080/edge/state` trả `active_region=b` | platform operator |
| 6 | Verify golden signals | Do `dr/runbook.py` thực hiện | 10 request thật, error rate 0%, p95 được ghi vào log | application owner |
| 7 | Đo RTO và đóng incident | `python tools/measure_rto.py --loadgen reports/drill-2-withdr.jsonl --target-rto 300` | `valid:true`, `warnings:[]`, `recovered_by_region:b`, `rto_verdict:PASS` | incident commander |

## Lệnh vận hành đầy đủ

```bash
python chaos/kill_region.py status
python dr/runbook.py --primary a --target b --backend fs --auto
curl http://localhost:8080/edge/state
curl "http://localhost:8080/v1/infer?q=health-check"
python tools/measure_rto.py --loadgen reports/drill-2-withdr.jsonl --target-rto 300
```

## Rollback condition

Không rollback chỉ vì Region A đã sống lại. Chỉ rollback khi:

- Region A trả `/readyz` HTTP 200 liên tiếp ít nhất 3 lần.
- Region B đang phục vụ ổn định, error rate bằng 0%.
- Snapshot/state của B đã được bảo toàn.
- Incident commander phê duyệt rollback.

Rollback về A:

```bash
python dr/failover.py --target a --backend fs --wait 60
```

Người có quyền quyết định rollback là incident commander. Platform operator thực hiện; application owner xác nhận golden signals sau rollback.

## Evidence lần chạy gần nhất

- Chaos: `chaos/chaos-events.jsonl:3`
- Health detection: `reports/health-events.jsonl:1`
- DNS cutover: `reports/failover-events.jsonl:5`
- Golden signals: `reports/runbook-run.jsonl:6`
- RTO/RPO: `reports/rto-evidence.md`
