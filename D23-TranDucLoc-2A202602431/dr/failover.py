import argparse
import json
import pathlib
import sys
import time

import httpx

sys.path.insert(0, ".")
from state import snapshot  # noqa: E402

URL = {"a": "http://127.0.0.1:8001", "b": "http://127.0.0.1:8002"}
LOG = pathlib.Path("reports/failover-events.jsonl")


def emit(**kw):
    LOG.parent.mkdir(parents=True, exist_ok=True)
    event = {"ts": time.time(), "iso": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())}
    event.update(kw)
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(event) + "\n")
    print(json.dumps(event))
    return event


def state_of(region: str) -> dict:
    response = httpx.get(f"{URL[region]}/v1/state", timeout=2.0)
    response.raise_for_status()
    return response.json()


def failover(target: str, backend: str, wait: float) -> dict:
    if target not in URL or wait <= 0:
        raise ValueError("invalid target or wait")
    completed = []
    try:
        before = state_of(target)
    except Exception as exc:
        emit(step="1_verify_target", ok=False, target=target, reason=type(exc).__name__)
        return {"ok": False, "target": target, "completed_steps": completed, "error": str(exc)}
    emit(step="1_verify_target", ok=True, target=target, state=before)
    completed.append("1_verify_target")

    try:
        meta = snapshot.get(target, backend)
        rpo = snapshot.rpo(pathlib.Path("state/region-a/vectors.sqlite"),
                           pathlib.Path(f"state/region-{target}/vectors.sqlite"))
        rpo_seconds, docs_lost = rpo.get("rpo_seconds"), rpo.get("docs_lost")
        version = meta.get("embed_model_version")
        emit(step="2_restore_snapshot", ok=True, target=target,
             rpo_seconds=rpo_seconds, docs_lost=docs_lost,
             embed_model_version=version)
    except Exception as exc:
        emit(step="2_restore_snapshot", ok=False, target=target, reason=type(exc).__name__)
        return {"ok": False, "target": target, "completed_steps": completed, "error": str(exc)}
    completed.append("2_restore_snapshot")

    pathlib.Path(f"state/region-{target}/pool_state").write_text("full", encoding="utf-8")
    emit(step="3_scale_pool", ok=True, target=target, pool_state="full")
    completed.append("3_scale_pool")

    deadline, ready, reason = time.time() + wait, False, "timeout"
    while time.time() < deadline:
        try:
            response = httpx.get(f"{URL[target]}/readyz", timeout=min(2.0, max(0.1, deadline - time.time())))
            if response.status_code == 200:
                ready = True
                break
            try:
                reason = ";".join(response.json().get("reasons", [])) or f"http_{response.status_code}"
            except (ValueError, TypeError):
                reason = f"http_{response.status_code}"
        except Exception as exc:
            reason = type(exc).__name__
        time.sleep(min(0.2, max(0.0, deadline - time.time())))

    if not ready:
        emit(step="4_wait_ready", ok=False, target=target, reason=reason)
        return {"ok": False, "target": target, "completed_steps": completed,
                "rpo_seconds": rpo_seconds, "docs_lost": docs_lost,
                "embed_model_version": version, "error": f"target not ready: {reason}"}
    emit(step="4_wait_ready", ok=True, target=target)
    completed.append("4_wait_ready")

    pathlib.Path("edge/active_region").write_text(target, encoding="utf-8")
    emit(step="5_dns_cutover", ok=True, target=target, active_region=target)
    completed.append("5_dns_cutover")
    return {"ok": True, "target": target, "completed_steps": completed,
            "rpo_seconds": rpo_seconds, "docs_lost": docs_lost,
            "embed_model_version": version}


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--target", default="b", choices=["a", "b"])
    p.add_argument("--backend", default="fs", choices=["fs", "minio"])
    p.add_argument("--wait", type=float, default=60)
    a = p.parse_args()
    print(json.dumps(failover(a.target, a.backend, a.wait), indent=2))
