import argparse
import json
import pathlib
import statistics
import sys
import time

import httpx

sys.path.insert(0, ".")
from dr import failover as fo  # noqa: E402
from dr import health_checker as hc  # noqa: E402

LOG = pathlib.Path("reports/runbook-run.jsonl")
URL = {"a": "http://127.0.0.1:8001", "b": "http://127.0.0.1:8002"}


def step(n, name, **kw):
    LOG.parent.mkdir(parents=True, exist_ok=True)
    event = {"ts": time.time(), "iso": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()),
             "step": n, "name": name}
    event.update(kw)
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(event) + "\n")
    print(json.dumps(event))
    return event


def confirm(auto: bool, msg: str) -> bool:
    if auto:
        return True
    answer = input(f"{msg} [y/N] ").strip().lower()
    return answer in {"y", "yes"}


def _golden_signals(target: str, count: int = 10) -> dict:
    latencies, errors = [], 0
    for i in range(count):
        started = time.time()
        try:
            response = httpx.get(f"{URL[target]}/v1/infer", params={"q": f"runbook-check-{i}"}, timeout=5)
            latency_ms = (time.time() - started) * 1000
            latencies.append(latency_ms)
            if response.status_code != 200 or not response.json().get("answer"):
                errors += 1
        except Exception:
            latencies.append((time.time() - started) * 1000)
            errors += 1
    ordered = sorted(latencies)
    p95 = ordered[min(len(ordered) - 1, max(0, int(len(ordered) * 0.95) - 1))] if ordered else None
    return {"requests": count, "errors": errors,
            "error_rate": errors / count if count else 1.0,
            "p95_latency_ms": round(p95, 2) if p95 is not None else None}


def _alive(region: str) -> tuple[bool, str]:
    try:
        response = httpx.get(f"{URL[region]}/healthz", timeout=2.0)
        return response.status_code == 200, f"http_{response.status_code}"
    except Exception as exc:
        return False, type(exc).__name__


def run(primary: str, target: str, backend: str, auto: bool) -> dict:
    if primary not in URL or target not in URL or primary == target:
        raise ValueError("primary and target must be different valid regions")
    started = time.time()

    primary_probe = hc.probe(primary, 2.0)
    target_probe = hc.probe(target, 2.0)
    target_alive, target_alive_reason = _alive(target)
    # The target is allowed to be unready: failover will restore its state
    # and warm the pool. It must, however, be alive so that /readyz can be
    # polled after restoration.
    confirmed = (not primary_probe[0]) and target_alive
    step(1, "xac_nhan_outage", primary=primary, target=target,
         primary_ready=primary_probe[0], primary_reason=primary_probe[1],
         target_ready=target_probe[0], target_reason=target_probe[1], confirmed=confirmed)
    if not confirmed:
        return {"ok": False, "error": "outage not confirmed", "elapsed_s": time.time() - started}

    outage_ts = time.time()
    step(2, "thong_bao_incident", primary=primary, target=target,
         outage_ts=outage_ts, message="incident announced")
    if not confirm(auto, f"Fail over traffic from Region {primary} to Region {target}?"):
        step(3, "scale_gpu_pool", ok=False, cancelled=True)
        return {"ok": False, "cancelled": True, "elapsed_s": time.time() - started}

    result = fo.failover(target, backend, wait=60)
    step(3, "scale_gpu_pool", ok=result.get("ok", False), failover_result=result)
    if not result.get("ok"):
        step(7, "post_incident", ok=False, elapsed_s=time.time() - started,
             error=result.get("error", "failover failed"))
        return {"ok": False, "failover": result, "elapsed_s": time.time() - started}

    state = {"region": target, "docs_lost": result.get("docs_lost"),
             "rpo_seconds": result.get("rpo_seconds"),
             "embed_model_version": result.get("embed_model_version")}
    step(4, "verify_state_replica", ok=True, **state)
    cutover_ok = "5_dns_cutover" in result.get("completed_steps", [])
    step(5, "dns_cutover", ok=cutover_ok, active_region=target)
    signals = _golden_signals(target)
    step(6, "verify_golden_signals", ok=signals["errors"] == 0, **signals)
    final = {"ok": cutover_ok and signals["errors"] == 0,
             "failover": result, "golden_signals": signals,
             "elapsed_s": round(time.time() - started, 3)}
    step(7, "post_incident", **final)
    return final


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--primary", default="a", choices=["a", "b"])
    parser.add_argument("--target", default="b", choices=["a", "b"])
    parser.add_argument("--backend", default="fs", choices=["fs", "minio"])
    parser.add_argument("--auto", action="store_true")
    args = parser.parse_args()
    print(json.dumps(run(args.primary, args.target, args.backend, args.auto), indent=2))
