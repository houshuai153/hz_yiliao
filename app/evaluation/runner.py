"""评测运行器：读取配置中的阈值，运行三个套件，写出 JSON 结果；并校验评测集 / 阈值未被改动。"""
import hashlib
import json
import operator
from datetime import datetime, timezone
from pathlib import Path

import yaml

from app.evaluation.netguard import block_external_network
from app.evaluation.suites import SUITES, load_cases
from app.settings import ROOT

CONFIG_PATH = ROOT / "config" / "eval.yaml"
LOCK_PATH = ROOT / "eval" / "cases.lock.json"
DEFAULT_OUT = ROOT / "eval" / "results" / "latest.json"
OPS = {">=": operator.ge, "<=": operator.le, "==": operator.eq}
EPS = 1e-9


class EvalError(Exception):
    pass


def load_eval_config(path: Path = CONFIG_PATH) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))["evaluation"]


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def current_hashes(cfg: dict) -> dict:
    """评测集文件与“evaluation 配置段”（指标定义 + 阈值）的哈希。"""
    files = {s["cases_file"]: _sha((ROOT / s["cases_file"]).read_bytes().replace(b"\r\n", b"\n"))
             for s in cfg["suites"].values()}
    canon = json.dumps(cfg, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return {"case_files": files, "evaluation_config_sha256": _sha(canon)}


def check_lock(cfg: dict) -> list[str]:
    """返回问题列表；为空表示评测集与阈值和锁定版本一致。"""
    if not LOCK_PATH.exists():
        return [f"缺少 {LOCK_PATH.relative_to(ROOT)}；首次请用 --update-lock --reason 建立"]
    lock = json.loads(LOCK_PATH.read_text(encoding="utf-8"))["current"]
    now, problems = current_hashes(cfg), []
    for f, h in now["case_files"].items():
        if lock["case_files"].get(f) != h:
            problems.append(f"评测集已变更：{f}")
    if lock["evaluation_config_sha256"] != now["evaluation_config_sha256"]:
        problems.append("config/eval.yaml 的 evaluation 段（指标定义或阈值）已变更")
    return problems


def update_lock(cfg: dict, reason: str) -> dict:
    if not reason.strip():
        raise EvalError("更新锁必须说明原因（--reason）")
    old = json.loads(LOCK_PATH.read_text(encoding="utf-8")) if LOCK_PATH.exists() else {"history": []}
    now = current_hashes(cfg)
    entry = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "reason": reason.strip(), **now}
    lock = {"note": "评测集与阈值的锁定记录。变更必须经 --update-lock --reason 显式更新，历史保留在 history。",
            "current": now, "history": old.get("history", []) + [entry]}
    LOCK_PATH.write_text(json.dumps(lock, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return lock


def judge(name: str, spec: dict, measured: tuple) -> dict:
    value, num, den = measured
    ok = value is not None and OPS[spec["op"]](value, spec["threshold"] + (EPS if spec["op"] == "<=" else -EPS if spec["op"] == ">=" else 0))
    return {"name": name, "value": None if value is None else round(value, 6), "numerator": num, "denominator": den,
            "op": spec["op"], "threshold": spec["threshold"], "gate": spec["gate"],
            "passed": bool(ok), "formula": spec["formula"],
            **({"note": "分母为 0，无法计算，按未通过处理"} if value is None else {})}


def run_all(out_path: Path = DEFAULT_OUT, only: list[str] | None = None) -> dict:
    cfg = load_eval_config()
    results: dict = {"offline": True, "suites": {}}
    with block_external_network():
        for name, spec in cfg["suites"].items():
            if only and name not in only:
                continue
            values, details = SUITES[name](load_cases(ROOT / spec["cases_file"]))
            if set(values) != set(spec["metrics"]):
                raise EvalError(f"套件 {name} 计算的指标 {sorted(values)} 与配置 {sorted(spec['metrics'])} 不一致")
            metrics = [judge(m, spec["metrics"][m], values[m]) for m in spec["metrics"]]
            results["suites"][name] = {
                "passed": all(m["passed"] for m in metrics if m["gate"]),
                "n_cases": len(details), "metrics": metrics, "cases": details}
    results["overall_passed"] = all(s["passed"] for s in results["suites"].values())
    results["generated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    results["locked"] = current_hashes(cfg)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return results
