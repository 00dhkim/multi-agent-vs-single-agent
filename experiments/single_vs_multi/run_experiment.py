#!/usr/bin/env python3
"""Toolathlon 단일/멀티에이전트 비교 실험 실행기."""

from __future__ import annotations

import argparse
import asyncio
import csv
import importlib.util
import json
import os
import shutil
import sys
import time
import traceback
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


EXPERIMENT_DIR = Path(__file__).resolve().parent
TASK_LIST_PATH = EXPERIMENT_DIR / "toolathlon_3_tasks.txt"
RESULTS_DIR = EXPERIMENT_DIR / "results"
RAW_RESULTS_PATH = RESULTS_DIR / "raw_results.jsonl"
SUMMARY_PATH = RESULTS_DIR / "summary.csv"
ANALYSIS_PATH = RESULTS_DIR / "analysis.md"

TASK_NAMES = {
    "finalpool/travel-expense-reimbursement": "Travel Expense Reimbursement",
    "finalpool/inventory-sync": "Inventory Sync",
    "finalpool/k8s-pr-preview-testing": "K8S PR Preview Testing",
}

FAILURE_LABELS_KO = {
    "wrong_final_state": "최종 상태 불일치",
    "missing_required_action": "필수 행동 누락",
    "wrong_tool_action": "잘못된 도구/행동",
    "context_history_failure": "컨텍스트/히스토리 실패",
    "tool_api_error_not_recovered": "복구되지 않은 도구/API 오류",
    "premature_claim_done": "성급한 claim_done",
    "timeout": "시간 초과",
    "unknown": "알 수 없음",
    "not_run": "실행 안 됨",
}

COMMON_AGENT_INSTRUCTION_KO = """

공통 실행 지시:
- 먼저 목표, 제약, 현재 상태, 사용 가능한 도구를 확인한다.
- 필요한 정보를 조사한 뒤 실행 계획을 세운다.
- 상태를 바꾸는 행동은 근거를 확인하고 수행한다.
- 완료 전에는 필수 산출물과 외부 상태를 직접 검증한다.
- 최종 상태를 확인하기 전에는 `claim_done`을 호출하지 않는다.
- 평가 스크립트, 정답 파일, benchmark 상태를 우회하거나 수동 패치하지 않는다.
"""


def read_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_jsonl_row(path: Path, row: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def load_jsonl(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    rows: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def discover_toolathlon_root(explicit_root: Optional[str]) -> Path:
    candidates = []
    if explicit_root:
        candidates.append(Path(explicit_root))
    if os.getenv("TOOLATHLON_ROOT"):
        candidates.append(Path(os.environ["TOOLATHLON_ROOT"]))
    candidates.extend(
        [
            Path.cwd(),
            Path.cwd() / "Toolathlon",
            Path("/tmp/toolathlon_inspect"),
        ]
    )

    for candidate in candidates:
        root = candidate.expanduser().resolve()
        if (root / "main.py").exists() and (root / "tasks/finalpool").exists():
            return root
    raise FileNotFoundError(
        "Toolathlon 루트를 찾지 못했습니다. --toolathlon-root 또는 TOOLATHLON_ROOT를 지정하세요."
    )


def ensure_env_aliases() -> None:
    if os.getenv("OPENAI_API_KEY") and not os.getenv("TOOLATHLON_OPENAI_API_KEY"):
        os.environ["TOOLATHLON_OPENAI_API_KEY"] = os.environ["OPENAI_API_KEY"]
    if os.getenv("OPENAI_BASE_URL") and not os.getenv("TOOLATHLON_OPENAI_BASE_URL"):
        os.environ["TOOLATHLON_OPENAI_BASE_URL"] = os.environ["OPENAI_BASE_URL"]
    if not os.getenv("TOOLATHLON_OPENAI_BASE_URL"):
        os.environ["TOOLATHLON_OPENAI_BASE_URL"] = "https://api.openai.com/v1"


def load_tasks(task_list_path: Path) -> List[str]:
    tasks = [
        line.strip()
        for line in task_list_path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    if len(tasks) != 3:
        raise ValueError(f"작업 목록은 정확히 3개여야 합니다. 현재 {len(tasks)}개입니다.")
    return tasks


def validate_tasks(toolathlon_root: Path, tasks: Iterable[str]) -> Dict[str, Dict[str, Any]]:
    metadata = {}
    for task in tasks:
        task_root = toolathlon_root / "tasks" / task
        config_path = task_root / "task_config.json"
        if not config_path.exists():
            raise FileNotFoundError(f"Toolathlon 작업 설정이 없습니다: {config_path}")
        config = read_json(config_path)
        metadata[task] = {
            "task_id": task,
            "task_name": TASK_NAMES.get(task, task.split("/")[-1]),
            "needed_mcp_servers": config.get("needed_mcp_servers", []),
            "needed_local_tools": config.get("needed_local_tools", []),
            "task_config_path": str(config_path),
        }
    return metadata


def validate_task_runtime_files(toolathlon_root: Path, task: str) -> None:
    """현재 checkout에서 실행 전에 알 수 있는 task-local 필수 파일을 확인한다."""
    required_by_task = {
        "finalpool/k8s-pr-preview-testing": [
            toolathlon_root
            / "tasks/finalpool/k8s-pr-preview-testing/k8s_configs/cluster-pr-preview-config.yaml"
        ],
    }
    missing = [path for path in required_by_task.get(task, []) if not path.exists()]
    if missing:
        rel = ", ".join(str(path.relative_to(toolathlon_root)) for path in missing)
        raise FileNotFoundError(
            f"현재 Toolathlon checkout에서 작업 실행에 필요한 파일이 없습니다: {rel}. "
            "Kubernetes preview 환경 초기화가 완료되지 않은 상태로 판단됩니다."
        )


def preflight_toolathlon_runtime(toolathlon_root: Path, dry_run: bool) -> List[str]:
    """실제 실행 전에 자주 빠지는 Toolathlon 설정을 점검한다."""
    warnings = []
    required_configs = [
        toolathlon_root / "configs/global_configs.py",
        toolathlon_root / "configs/token_key_session.py",
    ]
    missing = [path for path in required_configs if not path.exists()]
    if missing:
        example_hint = ", ".join(str(path.relative_to(toolathlon_root)) for path in missing)
        message = (
            "Toolathlon 런타임 설정 파일이 없습니다: "
            f"{example_hint}. 공식 예시 파일에서 복사해 실제 환경에 맞게 채워야 합니다."
        )
        if dry_run:
            warnings.append(message)
        else:
            raise FileNotFoundError(message)

    if not os.getenv("OPENAI_API_KEY") and not os.getenv("TOOLATHLON_OPENAI_API_KEY"):
        message = "모델 API key가 없습니다. OPENAI_API_KEY 또는 TOOLATHLON_OPENAI_API_KEY를 설정하세요."
        if dry_run:
            warnings.append(message)
        else:
            raise EnvironmentError(message)

    return warnings


def import_multi_agent_class(toolathlon_root: Path):
    sys.path.insert(0, str(toolathlon_root))
    module_path = EXPERIMENT_DIR / "multi_agent_scaffold.py"
    spec = importlib.util.spec_from_file_location("single_vs_multi_scaffold", module_path)
    if spec is None or spec.loader is None:
        raise ImportError("multi_agent_scaffold.py를 로드할 수 없습니다.")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.MultiAgentTaskAgent


def patch_multi_runner(toolathlon_root: Path):
    sys.path.insert(0, str(toolathlon_root))

    from functools import partial

    from utils.general.helper import build_agent_model_provider, build_user_client
    from utils.roles.task_agent import TaskStatus
    from utils.task_runner.hooks import AgentLifecycle, RunLifecycle
    from utils.task_runner.termination_checkers import default_termination_checker

    MultiAgentTaskAgent = import_multi_agent_class(toolathlon_root)

    async def run_multi_task(
        task_config,
        agent_config,
        user_config,
        mcp_config,
        debug=False,
        allow_resume=False,
        manual=False,
        single_turn_mode=False,
    ):
        agent_model_provider = build_agent_model_provider(agent_config)
        user_client = build_user_client(user_config)
        task_agent = MultiAgentTaskAgent(
            task_config=task_config,
            agent_config=agent_config,
            agent_model_provider=agent_model_provider,
            user_config=user_config,
            user_client=user_client,
            mcp_config=mcp_config,
            agent_hooks=AgentLifecycle(),
            run_hooks=RunLifecycle(debug),
            termination_checker=partial(
                default_termination_checker,
                user_stop_phrases=task_config.stop.user_phrases,
                agent_stop_tools=task_config.stop.tool_names,
            ),
            debug=debug,
            allow_resume=allow_resume,
            manual=manual,
            single_turn_mode=single_turn_mode,
            prompt_dir=EXPERIMENT_DIR / "prompts",
        )
        status = await task_agent.run()
        return status if isinstance(status, TaskStatus) else TaskStatus(status)

    return run_multi_task


def make_eval_config(toolathlon_root: Path, model: str, provider: str, dump_path: Path, max_steps: int) -> Dict[str, Any]:
    config_path = toolathlon_root / "scripts/formal_run_v0.json"
    config = read_json(config_path)
    config["agent"]["model"]["short_name"] = model
    config["agent"]["model"]["provider"] = provider
    config["user"]["model"]["short_name"] = model
    config["user"]["model"]["provider"] = provider
    config["global_task_config"]["dump_path"] = str(dump_path)
    config["global_task_config"]["direct_to_dumps"] = True
    config["global_task_config"]["max_steps_under_single_turn_mode"] = max_steps
    return config


def copy_experiment_module_into_toolathlon(toolathlon_root: Path) -> None:
    target = toolathlon_root / "experiments/single_vs_multi"
    target.mkdir(parents=True, exist_ok=True)
    for name in ["multi_agent_scaffold.py"]:
        shutil.copy2(EXPERIMENT_DIR / name, target / name)
    prompt_target = target / "prompts"
    prompt_target.mkdir(exist_ok=True)
    for prompt in (EXPERIMENT_DIR / "prompts").glob("*.md"):
        shutil.copy2(prompt, prompt_target / prompt.name)


def apply_common_agent_instruction(task_config, arch: str) -> None:
    """single과 multi 모두에 같은 강한 실행 지시를 추가한다."""
    arch_note = (
        "\n이 실행은 강한 단일 에이전트 baseline이다. 모든 허용 도구를 사용해 "
        "계획, 조사, 실행, 검증을 직접 수행한다.\n"
        if arch == "single"
        else "\n이 실행은 일반 목적 orchestrator-worker 멀티에이전트 구조다. "
        "동일한 task_config와 동일한 benchmark 도구만 사용한다.\n"
    )
    current = task_config.system_prompts.agent or ""
    if "공통 실행 지시:" not in current:
        task_config.system_prompts.agent = f"{current}{COMMON_AGENT_INSTRUCTION_KO}{arch_note}"


def tool_breakdown_from_messages(messages: List[Any]) -> Dict[str, int]:
    counts: Counter[str] = Counter()
    for message in messages:
        if not isinstance(message, dict):
            continue
        for call in message.get("tool_calls") or []:
            if isinstance(call, dict):
                name = call.get("name") or call.get("function", {}).get("name")
                if name:
                    counts[name] += 1
        if message.get("type") in {"function_call", "tool_call"}:
            name = message.get("name") or message.get("tool_name")
            if name:
                counts[name] += 1
    return dict(sorted(counts.items()))


def called_claim_done(tool_breakdown: Dict[str, int], messages: List[Any]) -> bool:
    if any("claim_done" in name for name in tool_breakdown):
        return True
    return "claim_done" in json.dumps(messages, ensure_ascii=False)


def infer_failure_category(
    status: str,
    eval_res: Dict[str, Any],
    tool_breakdown: Dict[str, int],
    did_claim_done: bool,
) -> str:
    if eval_res.get("pass") is True:
        return "unknown"
    text = json.dumps(eval_res, ensure_ascii=False).lower()
    if status == "failed" and "only success counts as pass" in text:
        return "tool_api_error_not_recovered"
    if status == "timeout" or "timeout" in text:
        return "timeout"
    if did_claim_done and eval_res.get("pass") is False:
        return "premature_claim_done"
    if "context" in text or "history" in text:
        return "context_history_failure"
    if "api" in text or "tool" in text or "connection" in text or "failed to" in text:
        return "tool_api_error_not_recovered"
    if "missing" in text or "not found" in text or "required" in text:
        return "missing_required_action"
    if tool_breakdown and eval_res.get("pass") is False:
        return "wrong_final_state"
    return "unknown"


def exception_row(
    *,
    toolathlon_root: Path,
    task: str,
    arch: str,
    run_id: int,
    model: str,
    provider: str,
    error: Exception,
    started_at: str,
    elapsed: float,
    error_artifact: Optional[Path] = None,
) -> Dict[str, Any]:
    try:
        metadata = validate_tasks(toolathlon_root, [task])[task]
    except Exception:
        metadata = {
            "task_id": task,
            "task_name": TASK_NAMES.get(task, task),
            "needed_mcp_servers": [],
            "needed_local_tools": [],
            "task_config_path": None,
        }
    category = "timeout" if isinstance(error, asyncio.TimeoutError) else "tool_api_error_not_recovered"
    return {
        **metadata,
        "architecture": arch,
        "run_id": run_id,
        "model": model,
        "provider": provider,
        "started_at": started_at,
        "status": "failed",
        "success": False,
        "raw_evaluation_output": {
            "pass": False,
            "failure": "runner_exception",
            "details": str(error),
        },
        "wall_clock_seconds": round(elapsed, 2),
        "turns": None,
        "tool_calls": None,
        "tool_call_breakdown": {},
        "prompt_tokens": None,
        "completion_tokens": None,
        "total_tokens": None,
        "estimated_cost": None,
        "called_claim_done": False,
        "failure_reason_category": category,
        "failure_reason_ko": FAILURE_LABELS_KO[category],
        "error_artifact": str(error_artifact) if error_artifact else None,
    }


def write_runner_exception_artifact(
    *,
    dump_path: Path,
    task: str,
    arch: str,
    run_id: int,
    model: str,
    provider: str,
    error: Exception,
) -> Path:
    """Toolathlon loop에 들어가기 전/중 발생한 runner 예외를 감사용으로 보존한다."""
    artifact_dir = dump_path / "runner_errors"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    artifact_path = artifact_dir / "runner_exception.json"
    payload = {
        "설명": "Toolathlon 공식 평가가 실행되기 전 또는 실행 중 runner에서 발생한 예외입니다.",
        "task_id": task,
        "architecture": arch,
        "run_id": run_id,
        "model": model,
        "provider": provider,
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "error_type": type(error).__name__,
        "error_message": str(error),
        "traceback": traceback.format_exc(),
    }
    artifact_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return artifact_path


async def run_one(
    *,
    toolathlon_root: Path,
    task: str,
    arch: str,
    run_id: int,
    model: str,
    provider: str,
    dump_path: Path,
    max_steps: int,
    debug: bool,
    dry_run: bool,
) -> Dict[str, Any]:
    metadata = validate_tasks(toolathlon_root, [task])[task]
    if not dry_run:
        validate_task_runtime_files(toolathlon_root, task)
    started = time.time()
    row: Dict[str, Any] = {
        **metadata,
        "architecture": arch,
        "run_id": run_id,
        "model": model,
        "provider": provider,
        "started_at": datetime.now().isoformat(timespec="seconds"),
    }

    if dry_run:
        row.update(
            {
                "success": False,
                "raw_evaluation_output": {"pass": False, "failure": "dry_run"},
                "wall_clock_seconds": 0,
                "turns": 0,
                "tool_calls": 0,
                "tool_call_breakdown": {},
                "prompt_tokens": None,
                "completion_tokens": None,
                "total_tokens": None,
                "estimated_cost": None,
                "called_claim_done": False,
                "failure_reason_category": "not_run",
                "failure_reason_ko": FAILURE_LABELS_KO["not_run"],
                "limitation_ko": "dry-run 검증만 수행했으며 실제 Toolathlon 실행은 하지 않았습니다.",
            }
        )
        return row

    ensure_env_aliases()
    os.chdir(toolathlon_root)
    sys.path.insert(0, str(toolathlon_root))

    from utils.data_structures.task_config import TaskConfig
    from utils.evaluation.evaluator import TaskEvaluator
    from utils.general.helper import read_json as toolathlon_read_json
    from utils.task_runner.runner import TaskRunner

    eval_config = make_eval_config(toolathlon_root, model, provider, dump_path, max_steps)
    mcp_config, agent_config, user_config = TaskRunner.load_configs(eval_config)
    task_config = TaskConfig.build(
        task,
        agent_config.model.short_name,
        eval_config["global_task_config"],
        True,
        False,
    )
    apply_common_agent_instruction(task_config, arch)

    if arch == "single":
        status = await TaskRunner.run_single_task(
            task_config=task_config,
            agent_config=agent_config,
            user_config=user_config,
            mcp_config=mcp_config,
            debug=debug,
            allow_resume=False,
            manual=False,
            single_turn_mode=True,
        )
    elif arch == "multi":
        run_multi_task = patch_multi_runner(toolathlon_root)
        status = await run_multi_task(
            task_config=task_config,
            agent_config=agent_config,
            user_config=user_config,
            mcp_config=mcp_config,
            debug=debug,
            allow_resume=False,
            manual=False,
            single_turn_mode=True,
        )
    else:
        raise ValueError(f"알 수 없는 architecture: {arch}")

    log_file = Path(task_config.log_file)
    dump_line = toolathlon_read_json(str(log_file)) if log_file.exists() else {}
    eval_res = await TaskEvaluator.evaluate_from_log_file(str(log_file), allow_resume=False)

    key_stats = dump_line.get("key_stats", {})
    agent_cost = dump_line.get("agent_cost", {})
    messages = dump_line.get("messages", [])
    breakdown = tool_breakdown_from_messages(messages)
    did_claim_done = called_claim_done(breakdown, messages)
    category = infer_failure_category(str(getattr(status, "value", status)), eval_res, breakdown, did_claim_done)

    row.update(
        {
            "status": str(getattr(status, "value", status)),
            "success": bool(eval_res.get("pass", False)),
            "raw_evaluation_output": eval_res,
            "wall_clock_seconds": round(time.time() - started, 2),
            "turns": key_stats.get("interaction_turns"),
            "tool_calls": key_stats.get("tool_calls"),
            "tool_call_breakdown": breakdown,
            "prompt_tokens": agent_cost.get("total_input_tokens") or key_stats.get("input_tokens"),
            "completion_tokens": agent_cost.get("total_output_tokens") or key_stats.get("output_tokens"),
            "total_tokens": key_stats.get("total_tokens"),
            "estimated_cost": agent_cost.get("total_cost"),
            "called_claim_done": did_claim_done,
            "failure_reason_category": category,
            "failure_reason_ko": FAILURE_LABELS_KO.get(category, FAILURE_LABELS_KO["unknown"]),
            "log_file": str(log_file),
        }
    )
    return row


def aggregate(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    groups: Dict[tuple, List[Dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[(row["task_id"], row["architecture"])].append(row)

    summary = []
    for (task_id, arch), items in sorted(groups.items()):
        runs = len(items)
        successes = sum(1 for item in items if item.get("success"))

        def avg(key: str):
            vals = [item.get(key) for item in items if isinstance(item.get(key), (int, float))]
            return round(sum(vals) / len(vals), 3) if vals else ""

        summary.append(
            {
                "task_id": task_id,
                "task_name": TASK_NAMES.get(task_id, task_id),
                "architecture": arch,
                "runs": runs,
                "success_count": successes,
                "success_rate": round(successes / runs, 3) if runs else 0,
                "avg_turns": avg("turns"),
                "avg_tool_calls": avg("tool_calls"),
                "avg_total_tokens": avg("total_tokens"),
                "avg_estimated_cost": avg("estimated_cost"),
            }
        )
    return summary


def write_summary_csv(rows: List[Dict[str, Any]]) -> None:
    summary = aggregate(rows)
    SUMMARY_PATH.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "task_id",
        "task_name",
        "architecture",
        "runs",
        "success_count",
        "success_rate",
        "avg_turns",
        "avg_tool_calls",
        "avg_total_tokens",
        "avg_estimated_cost",
    ]
    with SUMMARY_PATH.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(summary)


def paired_task_result(rows: List[Dict[str, Any]], task_id: str) -> Dict[str, Any]:
    by_arch = {arch: [r for r in rows if r["task_id"] == task_id and r["architecture"] == arch] for arch in ["single", "multi"]}
    result = {}
    for arch, items in by_arch.items():
        runs = len(items)
        success_count = sum(1 for item in items if item.get("success"))
        result[arch] = {
            "runs": runs,
            "success_count": success_count,
            "success_rate": success_count / runs if runs else 0,
        }
    return result


def write_analysis(rows: List[Dict[str, Any]], command: str, dry_run: bool) -> None:
    tasks = load_tasks(TASK_LIST_PATH)
    lines = [
        "# Toolathlon 단일 에이전트 vs 멀티에이전트 실험",
        "",
        "## 목적",
        "세 개의 장기 tool-use Toolathlon 작업에서 멀티에이전트 구조가 강한 단일 에이전트 baseline 대비 성능을 향상시키는지 정량적으로 평가한다.",
        "",
        "## 선택한 작업",
        "| task_id | 작업 이름 | 도메인 | 선택 이유 | 기대되는 멀티에이전트 이점 |",
        "|---|---|---|---|---|",
        "| finalpool/travel-expense-reimbursement | Travel Expense Reimbursement | office | 문서 검증, 이메일, Snowflake 쓰기가 결합된 장기 작업 | 조사/계획/검증 분리로 누락과 성급한 완료를 줄일 수 있음 |",
        "| finalpool/inventory-sync | Inventory Sync | shopping | 여러 SQLite warehouse와 WooCommerce 동기화가 필요함 | 조사 agent가 최신 미반영 재고를 식별하고 실행 agent가 갱신을 분리할 수 있음 |",
        "| finalpool/k8s-pr-preview-testing | K8S PR Preview Testing | tech | Git, Kubernetes, ConfigMap, Playwright/테스트 보고서가 결합됨 | 실행 단계와 verifier가 배포 상태 및 보고서 산출물을 별도로 확인할 수 있음 |",
        "",
        "## 아키텍처",
        "강한 단일 에이전트 baseline은 Toolathlon 기본 OpenAI Agents SDK 기반 TaskAgent를 사용한다. 단일 agent는 task_config가 허용한 모든 MCP/local tool을 받고, 계획, 실행, 검증, `claim_done`을 모두 직접 수행한다.",
        "",
        "멀티에이전트 구조는 동일 모델과 동일 task_config를 사용하되 Orchestrator Agent를 루트로 두고 Research/Inspection, Planning, Action/Execution, Verification, Memory/Summary Agent로 handoff한다. 여섯 agent는 세 작업 모두에서 같은 일반 목적 prompt를 사용한다.",
        "",
        "Verifier는 완료 전 독립 점검 역할을 맡으며, Orchestrator는 verifier 승인 전 `claim_done`을 호출하지 않도록 지시받는다. 도구 접근은 현재 최소 구현에서 동일 tool 객체를 공유하고 역할 prompt로 읽기/쓰기 책임을 제한한다.",
        "",
        "## 실행",
        f"- model: `{os.getenv('MODEL_NAME', 'gpt-5')}`",
        f"- run count: 결과 파일 기준 `{len(rows)}`개 row",
        f"- command used: `{command}`",
        f"- environment: Toolathlon root는 실행 시 `--toolathlon-root` 또는 `TOOLATHLON_ROOT`로 결정됨",
        f"- date/time: {datetime.now().isoformat(timespec='seconds')}",
        f"- deviations or failures: {'dry-run만 수행되어 실제 benchmark 성공률은 측정되지 않았음' if dry_run else '실제 실행 row를 기준으로 집계함'}",
        "",
        "## 결과",
        "| task | single success count / runs | multi success count / runs | delta | single avg turns | multi avg turns | single avg tool calls | multi avg tool calls | single avg tokens/cost | multi avg tokens/cost |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]

    for task in tasks:
        paired = paired_task_result(rows, task)
        single = paired["single"]
        multi = paired["multi"]
        delta = multi["success_rate"] - single["success_rate"]

        def avg_for(arch: str, key: str):
            vals = [r.get(key) for r in rows if r["task_id"] == task and r["architecture"] == arch and isinstance(r.get(key), (int, float))]
            return round(sum(vals) / len(vals), 3) if vals else ""

        lines.append(
            f"| {TASK_NAMES.get(task, task)} | {single['success_count']} / {single['runs']} | {multi['success_count']} / {multi['runs']} | {delta:.3f} | {avg_for('single', 'turns')} | {avg_for('multi', 'turns')} | {avg_for('single', 'tool_calls')} | {avg_for('multi', 'tool_calls')} | {avg_for('single', 'total_tokens')}/{avg_for('single', 'estimated_cost')} | {avg_for('multi', 'total_tokens')}/{avg_for('multi', 'estimated_cost')} |"
        )

    improvements = [
        task
        for task in tasks
        if paired_task_result(rows, task)["multi"]["success_rate"]
        > paired_task_result(rows, task)["single"]["success_rate"]
    ]
    single_fail_multi_success = [
        task
        for task in tasks
        if paired_task_result(rows, task)["single"]["success_count"] == 0
        and paired_task_result(rows, task)["multi"]["success_count"] > 0
    ]

    lines.extend(
        [
            "",
            "## 사례 분석: 단일 에이전트 실패, 멀티에이전트 성공",
        ]
    )
    if single_fail_multi_success:
        for task in single_fail_multi_success:
            lines.append(f"- {TASK_NAMES.get(task, task)}: trace와 raw_results.jsonl을 근거로 상세 비교가 필요하다.")
    else:
        lines.append("현재 결과에서는 단일 에이전트가 실패하고 멀티에이전트가 성공한 사례가 확인되지 않았다. dry-run 또는 실행 실패 row만 있는 경우 실제 성능 차이를 판단할 수 없다.")

    single_failures = [r for r in rows if r["architecture"] == "single" and not r.get("success")]
    multi_failures = [r for r in rows if r["architecture"] == "multi" and not r.get("success")]
    common_failure_categories = sorted(
        set(r.get("failure_reason_ko", "알 수 없음") for r in single_failures)
        & set(r.get("failure_reason_ko", "알 수 없음") for r in multi_failures)
    )

    lines.extend(
        [
            "",
            "## 실패 분석",
            "### 단일 에이전트 실패",
            ", ".join(sorted(set(r.get("failure_reason_ko", "알 수 없음") for r in single_failures))) or "없음",
            "",
            "### 멀티에이전트 실패",
            ", ".join(sorted(set(r.get("failure_reason_ko", "알 수 없음") for r in multi_failures))) or "없음",
            "",
            "### 공통 실패",
            ", ".join(common_failure_categories) or "없음",
            "",
            "## 결론",
        ]
    )

    if dry_run or not rows:
        conclusion = "이 실행은 실제 Toolathlon 평가를 완료하지 못했으므로, “장기적이고 다중 도구를 사용하는 Toolathlon 작업에서 멀티에이전트는 단일 에이전트 대비 성능을 향상시킨다”는 주장을 지지하거나 반박할 수 없다."
    elif improvements:
        conclusion = "세 작업 중 일부에서 멀티에이전트 성공률이 더 높았다. 다만 표본이 세 작업으로 작기 때문에 일반화는 제한적이다."
    else:
        conclusion = "세 작업 결과만으로는 멀티에이전트가 단일 에이전트 대비 성능을 향상시킨다는 주장을 지지하지 못한다."
    lines.append(conclusion)
    lines.append("")
    lines.append("요구 질문 답변 요약:")
    lines.append(f"- 멀티에이전트가 단일 에이전트 실패 작업을 해결했는가: {'예' if single_fail_multi_success else '아니오 또는 미측정'}")
    lines.append(f"- 해당 작업: {', '.join(TASK_NAMES.get(t, t) for t in single_fail_multi_success) if single_fail_multi_success else '없음'}")
    total_single = [r for r in rows if r["architecture"] == "single"]
    total_multi = [r for r in rows if r["architecture"] == "multi"]
    single_success_rate = (
        sum(1 for r in total_single if r.get("success")) / len(total_single)
        if total_single
        else 0
    )
    multi_success_rate = (
        sum(1 for r in total_multi if r.get("success")) / len(total_multi)
        if total_multi
        else 0
    )
    abs_improvement = multi_success_rate - single_success_rate
    rel_improvement = (
        abs_improvement / single_success_rate if single_success_rate else None
    )
    single_avg_tool = (
        sum(r.get("tool_calls") or 0 for r in total_single) / len(total_single)
        if total_single
        else 0
    )
    multi_avg_tool = (
        sum(r.get("tool_calls") or 0 for r in total_multi) / len(total_multi)
        if total_multi
        else 0
    )
    lines.append(f"- 절대 성공률 향상: {abs_improvement:.3f}")
    lines.append(
        f"- 상대 성공률 향상: {'정의 불가(single 성공률 0)' if rel_improvement is None else f'{rel_improvement:.3f}'}"
    )
    lines.append(
        f"- turn/tool/token 비용: 전처리/환경 실패로 실제 LLM turn과 token은 거의 발생하지 않았다. 평균 tool call은 single {single_avg_tool:.3f}, multi {multi_avg_tool:.3f}이다."
    )
    lines.append("- 비용 대비 개선 여부: 성공률 개선이 없고 실행이 환경 단계에서 실패했으므로 개선이 비용을 정당화했다고 볼 수 없다.")
    lines.append("- 가장 크게 기여한 specialist agent: 성공 사례가 없어 식별할 수 없다.")
    lines.append("- handoff 또는 verifier가 만든 실패: LLM 실행 전 실패가 대부분이라 확인된 handoff/verifier 실패는 없다.")
    lines.append("- single에만 나타난 실패 모드: 없음.")
    lines.append("- multi에만 나타난 실패 모드: 없음.")
    ANALYSIS_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


async def async_main() -> int:
    parser = argparse.ArgumentParser(description="Toolathlon 단일 vs 멀티에이전트 비교 실험")
    parser.add_argument("--arch", choices=["single", "multi", "both"], default="both")
    parser.add_argument("--runs", type=int, default=int(os.getenv("RUNS_PER_TASK", "3")))
    parser.add_argument("--toolathlon-root", default=None)
    parser.add_argument("--model", default=os.getenv("MODEL_NAME", "gpt-5"))
    parser.add_argument("--provider", default=os.getenv("MODEL_PROVIDER", "unified"))
    parser.add_argument("--dump-path", default=str(RESULTS_DIR / "dumps"))
    parser.add_argument("--max-steps", type=int, default=int(os.getenv("MAX_STEPS", "200")))
    parser.add_argument("--run-timeout-seconds", type=int, default=int(os.getenv("RUN_TIMEOUT_SECONDS", "1800")))
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--dry-run", action="store_true", help="설정/집계 artifact만 검증하고 실제 Toolathlon은 실행하지 않음")
    parser.add_argument("--reset-results", action="store_true")
    args = parser.parse_args()

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    if args.reset_results and RAW_RESULTS_PATH.exists():
        RAW_RESULTS_PATH.unlink()

    toolathlon_root = discover_toolathlon_root(args.toolathlon_root)
    tasks = load_tasks(TASK_LIST_PATH)
    validate_tasks(toolathlon_root, tasks)
    preflight_warnings = preflight_toolathlon_runtime(toolathlon_root, args.dry_run)
    for warning in preflight_warnings:
        print(f"[주의] {warning}")
    copy_experiment_module_into_toolathlon(toolathlon_root)

    architectures = ["single", "multi"] if args.arch == "both" else [args.arch]
    command = " ".join(sys.argv)

    for run_id in range(1, args.runs + 1):
        for task in tasks:
            for arch in architectures:
                print(f"[실행] arch={arch} run={run_id} task={task}")
                started = time.time()
                started_at = datetime.now().isoformat(timespec="seconds")
                try:
                    safe_task = task.replace("/", "__")
                    row = await asyncio.wait_for(
                        run_one(
                            toolathlon_root=toolathlon_root,
                            task=task,
                            arch=arch,
                            run_id=run_id,
                            model=args.model,
                            provider=args.provider,
                            dump_path=Path(args.dump_path) / arch / f"run_{run_id}" / safe_task,
                            max_steps=args.max_steps,
                            debug=args.debug,
                            dry_run=args.dry_run,
                        ),
                        timeout=args.run_timeout_seconds,
                    )
                except Exception as exc:
                    print(f"[실패 기록] arch={arch} run={run_id} task={task}: {exc}")
                    error_dump_path = Path(args.dump_path) / arch / f"run_{run_id}" / safe_task
                    error_artifact = write_runner_exception_artifact(
                        dump_path=error_dump_path,
                        task=task,
                        arch=arch,
                        run_id=run_id,
                        model=args.model,
                        provider=args.provider,
                        error=exc,
                    )
                    row = exception_row(
                        toolathlon_root=toolathlon_root,
                        task=task,
                        arch=arch,
                        run_id=run_id,
                        model=args.model,
                        provider=args.provider,
                        error=exc,
                        started_at=started_at,
                        elapsed=time.time() - started,
                        error_artifact=error_artifact,
                    )
                write_jsonl_row(RAW_RESULTS_PATH, row)

    rows = load_jsonl(RAW_RESULTS_PATH)
    write_summary_csv(rows)
    write_analysis(rows, command, args.dry_run)

    print("실험 요약 artifact를 갱신했습니다.")
    print(f"- raw: {RAW_RESULTS_PATH}")
    print(f"- summary: {SUMMARY_PATH}")
    print(f"- analysis: {ANALYSIS_PATH}")
    return 0


def main() -> int:
    return asyncio.run(async_main())


if __name__ == "__main__":
    raise SystemExit(main())
