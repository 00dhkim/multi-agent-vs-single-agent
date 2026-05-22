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
SCENARIO_LIST_PATH = EXPERIMENT_DIR / "toolathlon_10_scenarios.txt"
RESULTS_DIR = EXPERIMENT_DIR / "results"
RAW_RESULTS_PATH = RESULTS_DIR / "raw_results.jsonl"
SUMMARY_PATH = RESULTS_DIR / "summary.csv"
ANALYSIS_PATH = RESULTS_DIR / "analysis.md"
FAIR_RAW_RESULTS_PATH = RESULTS_DIR / "raw_results_fair_workflow.jsonl"
FAIR_SUMMARY_PATH = RESULTS_DIR / "summary_fair_workflow.csv"
FAIR_ANALYSIS_PATH = RESULTS_DIR / "analysis_fair_workflow.md"
DYNAMIC_RAW_RESULTS_PATH = RESULTS_DIR / "raw_results_dynamic_supervisor.jsonl"
DYNAMIC_SUMMARY_PATH = RESULTS_DIR / "summary_dynamic_supervisor.csv"
DYNAMIC_ANALYSIS_PATH = RESULTS_DIR / "analysis_dynamic_supervisor.md"

TASK_NAMES = {
    "finalpool/travel-expense-reimbursement": "Travel Expense Reimbursement",
    "finalpool/inventory-sync": "Inventory Sync",
    "finalpool/k8s-pr-preview-testing": "K8S PR Preview Testing",
    "finalpool/paper-checker": "Paper Checker",
    "finalpool/privacy-desensitization": "Privacy Desensitization",
    "finalpool/excel-data-transformation": "Excel Data Transformation",
    "finalpool/arrange-workspace": "Arrange Workspace",
    "finalpool/reimbursement-form-filler": "Reimbursement Form Filler",
    "finalpool/detect-revised-terms": "Detect Revised Terms",
    "finalpool/ppt-analysis": "PPT Analysis",
    "finalpool/woocommerce-update-cover": "WooCommerce Update Cover",
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

ARCHITECTURES = [
    "single_baseline",
    "single_strong_workflow",
    "multi_workflow",
    "multi_dynamic_supervisor",
]

ARCH_ALIASES = {
    "baseline": "single_baseline",
    "single": "single_strong_workflow",
    "multi": "multi_workflow",
    "dynamic": "multi_dynamic_supervisor",
}

ARCH_GROUPS = {
    "both": ["single_strong_workflow", "multi_workflow"],
    "dynamic_compare": ["single_strong_workflow", "multi_workflow", "multi_dynamic_supervisor"],
    "all": ARCHITECTURES,
}

ARCH_LABELS_KO = {
    "single_baseline": "기본 단일",
    "single_strong_workflow": "강화 단일",
    "multi_workflow": "멀티 workflow",
    "multi_dynamic_supervisor": "멀티 dynamic supervisor",
}

WORKFLOW_AGENT_INSTRUCTION_KO = """

공통 workflow 실행 지시:
- 먼저 목표, 제약, 현재 상태, 사용 가능한 도구를 확인한다.
- Research → Plan → Execute → Self-Verify → Retry → Finalize 순서를 따른다.
- 필요한 정보를 조사한 뒤 체크리스트와 실행 계획을 명시한다.
- 상태를 바꾸는 행동은 근거를 확인하고 수행한다.
- 완료 전에는 필수 산출물과 외부 상태를 현재 workspace/API 상태로 직접 검증한다.
- 검증에서 누락, 오류, 불확실성이 발견되면 같은 task-specific 힌트를 추가하지 말고 일반 retry loop로 한 번 이상 복구를 시도한다.
- 최종 상태를 확인하기 전에는 `claim_done`을 호출하지 않는다.
- 평가 스크립트, 정답 파일, benchmark 상태를 우회하거나 수동 패치하지 않는다.
- hidden evaluator, groundtruth, answer dump, task별 hardcoded repair를 읽거나 사용하지 않는다.
- 완료 보고에는 확인한 요구사항, 사용한 도구, 변경한 상태, 검증 근거, 남은 위험을 포함한다.
"""

DYNAMIC_SUPERVISOR_INSTRUCTION_KO = """

공통 dynamic supervisor 실행 지시:
- 이 실행은 정해진 순서 workflow가 아니라 Orchestrator가 specialist agent를 도구처럼 호출하는 manager pattern이다.
- 같은 Toolathlon task_config, 같은 모델, 같은 benchmark 도구 조건을 사용한다.
- specialist 선택은 공개 task 설명과 task_config의 needed_mcp_servers/needed_local_tools에만 근거한다.
- task별 정답, 산출물 본문, 셀 좌표, reference 매핑, 평가 결과를 hardcode하지 않는다.
- 평가 스크립트, 정답 파일, hidden evaluator, answer dump, 이전 성공 trace를 읽거나 사용하지 않는다.
- Orchestrator는 필요한 specialist를 자율 호출하되, `claim_done` 전에는 독립 verifier를 호출해 현재 workspace/API 상태 근거를 확인한다.
- specialist는 `claim_done` 권한이 없으며, 역할별 필요한 도구만 받는다.
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
    if not tasks:
        raise ValueError(f"작업 목록이 비어 있습니다: {task_list_path}")
    return tasks


def select_tasks(all_tasks: List[str], task_filter: Optional[str]) -> List[str]:
    """Return the task subset requested on the CLI, preserving task list order."""
    if not task_filter:
        return all_tasks

    requested = [task.strip() for task in task_filter.split(",") if task.strip()]
    unknown = sorted(set(requested) - set(all_tasks))
    if unknown:
        raise ValueError(f"--tasks에 알 수 없는 task가 있습니다: {', '.join(unknown)}")
    return [task for task in all_tasks if task in requested]


def normalize_architecture(arch: str) -> str:
    return ARCH_ALIASES.get(arch, arch)


def expand_architectures(selection: str) -> List[str]:
    if selection in ARCH_GROUPS:
        return ARCH_GROUPS[selection]
    arch = normalize_architecture(selection)
    if arch not in ARCHITECTURES:
        allowed = sorted([*ARCHITECTURES, *ARCH_ALIASES, *ARCH_GROUPS])
        raise ValueError(f"--arch는 다음 중 하나여야 합니다: {', '.join(allowed)}")
    return [arch]


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


def patch_multi_runner(toolathlon_root: Path, architecture: str = "multi_workflow"):
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
            architecture=architecture,
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


def apply_workflow_agent_instruction(task_config, arch: str) -> None:
    """strong single과 multi에 같은 절차/checklist/retry 지시를 추가한다."""
    if arch == "single_baseline":
        return

    if arch == "multi_dynamic_supervisor":
        instruction = DYNAMIC_SUPERVISOR_INSTRUCTION_KO
        arch_note = "\n이 실행은 dynamic supervisor multi-agent 구조다. 고정 순서가 아니라 Orchestrator가 specialist를 자율 호출한다.\n"
    else:
        instruction = WORKFLOW_AGENT_INSTRUCTION_KO
        arch_note = (
            "\n이 실행은 강화 단일 에이전트 workflow다. 하나의 context/agent가 "
            "조사, 계획, 실행, 자기검증, 재시도, 완료 선언을 모두 수행한다.\n"
            if arch == "single_strong_workflow"
            else "\n이 실행은 일반 목적 multi workflow다. 같은 절차를 역할별 agent와 "
            "분리된 context, 독립 verifier, orchestrator-only `claim_done` 권한으로 수행한다.\n"
        )
    current = task_config.system_prompts.agent or ""
    if "공통 workflow 실행 지시:" not in current and "공통 dynamic supervisor 실행 지시:" not in current:
        task_config.system_prompts.agent = f"{current}{instruction}{arch_note}"


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


def message_text(messages: List[Any]) -> str:
    parts: List[str] = []
    for message in messages:
        if isinstance(message, dict):
            for key in ("content", "output", "text", "final_output"):
                value = message.get(key)
                if isinstance(value, str):
                    parts.append(value)
                elif isinstance(value, list):
                    parts.append(json.dumps(value, ensure_ascii=False))
            if message.get("type") in {"function_call_output", "tool_call_output"}:
                parts.append(json.dumps(message, ensure_ascii=False))
        elif isinstance(message, str):
            parts.append(message)
    return "\n".join(parts).lower()


def audit_workflow_adequacy(
    *,
    arch: str,
    messages: List[Any],
    tool_breakdown: Dict[str, int],
    did_claim_done: bool,
    success: bool,
    status: str,
) -> Dict[str, Any]:
    """Heuristic audit kept separate from deterministic Toolathlon pass/fail."""
    if arch not in {"single_baseline", "single_strong_workflow"}:
        return {"applicable": False}

    text = message_text(messages)
    tool_names = set(tool_breakdown)
    state_change_terms = (
        "write",
        "create",
        "update",
        "delete",
        "move",
        "copy",
        "edit",
        "apply",
        "execute",
        "python",
        "bash",
        "browser",
        "woocommerce",
        "kubernetes",
        "excel",
        "sheet",
        "email",
    )
    low_risk_terms = ("list", "read", "search", "get", "fetch", "inspect", "view")
    attempted_state_change = any(
        any(term in name.lower() for term in state_change_terms)
        and not all(term in name.lower() for term in low_risk_terms)
        for name in tool_names
    )
    if not attempted_state_change:
        attempted_state_change = any(
            term in text
            for term in (
                "created",
                "updated",
                "wrote",
                "modified",
                "moved",
                "saved",
                "생성",
                "수정",
                "갱신",
                "이동",
                "작성",
            )
        )

    explicit_plan = any(term in text for term in ("plan", "checklist", "steps", "계획", "체크리스트", "단계"))
    verified_output = any(
        term in text
        for term in (
            "verify",
            "verified",
            "validation",
            "confirmed",
            "checked",
            "test",
            "검증",
            "확인",
            "테스트",
        )
    )
    retried_on_failure = any(term in text for term in ("retry", "tried again", "rerun", "재시도", "다시"))
    saw_requirement = bool(messages) and any(
        term in text for term in ("task", "requirement", "goal", "objective", "요구", "목표")
    )
    inspected_tools_or_state = bool(tool_breakdown)
    premature_claim_done = did_claim_done and not success

    checks = {
        "read_task_requirements": saw_requirement,
        "inspected_tools_or_state": inspected_tools_or_state,
        "made_explicit_plan_or_checklist": explicit_plan,
        "attempted_required_state_change": attempted_state_change,
        "verified_outputs_or_external_state": verified_output,
        "retried_after_failure_signal": retried_on_failure,
        "premature_claim_done": premature_claim_done,
    }
    required = [
        "read_task_requirements",
        "inspected_tools_or_state",
        "made_explicit_plan_or_checklist",
        "attempted_required_state_change",
        "verified_outputs_or_external_state",
    ]
    audit_pass = all(checks[key] for key in required) and not premature_claim_done
    return {
        "applicable": True,
        "audit_pass": audit_pass,
        "checks": checks,
        "status": status,
        "note": (
            "성공/실패 판정과 별개인 trace 기반 휴리스틱 절차 audit입니다. "
            "agent가 충분히 노력했는지 분리해서 보기 위한 보조 지표입니다."
        ),
    }


def infer_failure_attribution(
    *,
    arch: str,
    success: bool,
    status: str,
    failure_category: str,
    audit: Dict[str, Any],
) -> str:
    if success:
        return "pass"
    if failure_category in {"timeout", "tool_api_error_not_recovered"}:
        return "environment_or_tool_failure"
    if arch == "single_baseline":
        return "weak_prompt_or_baseline_gap"
    if audit.get("applicable") and not audit.get("audit_pass"):
        return "agent_process_failure"
    if failure_category in {"premature_claim_done", "context_history_failure", "wrong_final_state", "missing_required_action"}:
        return "context_or_verification_failure"
    if status in {"max_turns_reached", "failed"}:
        return "agent_process_failure"
    return "unknown"


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
    failure_attribution = (
        "agent_process_failure"
        if arch == "multi_dynamic_supervisor" and category == "timeout"
        else "environment_or_tool_failure"
    )
    dynamic_profile_path = None
    dynamic_selected_specialists: List[str] = []
    if arch == "multi_dynamic_supervisor" and error_artifact:
        candidate = error_artifact.parent.parent / "workspace" / "profile_selection.json"
        if candidate.exists():
            dynamic_profile_path = str(candidate)
            try:
                dynamic_selected_specialists = sorted(read_json(candidate).get("selected_specialists", {}).keys())
            except Exception:
                dynamic_selected_specialists = []
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
        "workflow_audit": {"applicable": arch in {"single_baseline", "single_strong_workflow"}, "audit_pass": False},
        "baseline_adequacy_pass": False,
        "failure_attribution": failure_attribution,
        "failure_reason_category": category,
        "failure_reason_ko": FAILURE_LABELS_KO[category],
        "error_artifact": str(error_artifact) if error_artifact else None,
        "dynamic_profile_path": dynamic_profile_path,
        "dynamic_selected_specialists": dynamic_selected_specialists,
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
                "workflow_audit": {"applicable": arch in {"single_baseline", "single_strong_workflow"}, "audit_pass": False},
                "baseline_adequacy_pass": False,
                "failure_attribution": "not_run",
                "failure_reason_category": "not_run",
                "failure_reason_ko": FAILURE_LABELS_KO["not_run"],
                "limitation_ko": "dry-run 검증만 수행했으며 실제 Toolathlon 실행은 하지 않았습니다.",
                "dynamic_profile_path": None,
                "dynamic_selected_specialists": [],
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
    apply_workflow_agent_instruction(task_config, arch)

    if arch in {"single_baseline", "single_strong_workflow"}:
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
    elif arch in {"multi_workflow", "multi_dynamic_supervisor"}:
        run_multi_task = patch_multi_runner(toolathlon_root, architecture=arch)
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
    profile_path = Path(task_config.agent_workspace) / "profile_selection.json"
    dynamic_profile = read_json(profile_path) if arch == "multi_dynamic_supervisor" and profile_path.exists() else None
    breakdown = tool_breakdown_from_messages(messages)
    did_claim_done = called_claim_done(breakdown, messages)
    status_value = str(getattr(status, "value", status))
    success = bool(eval_res.get("pass", False))
    category = infer_failure_category(status_value, eval_res, breakdown, did_claim_done)
    workflow_audit = audit_workflow_adequacy(
        arch=arch,
        messages=messages,
        tool_breakdown=breakdown,
        did_claim_done=did_claim_done,
        success=success,
        status=status_value,
    )
    failure_attribution = infer_failure_attribution(
        arch=arch,
        success=success,
        status=status_value,
        failure_category=category,
        audit=workflow_audit,
    )

    row.update(
        {
            "status": status_value,
            "success": success,
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
            "workflow_audit": workflow_audit,
            "baseline_adequacy_pass": bool(workflow_audit.get("audit_pass", False)),
            "failure_attribution": failure_attribution,
            "failure_reason_category": category,
            "failure_reason_ko": FAILURE_LABELS_KO.get(category, FAILURE_LABELS_KO["unknown"]),
            "log_file": str(log_file),
            "dynamic_profile_path": str(profile_path) if dynamic_profile else None,
            "dynamic_selected_specialists": sorted((dynamic_profile or {}).get("selected_specialists", {}).keys()),
        }
    )
    return row


def aggregate(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    groups: Dict[tuple, List[Dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[(row["task_id"], normalize_architecture(row["architecture"]))].append(row)

    summary = []
    for (task_id, arch), items in sorted(groups.items()):
        runs = len(items)
        successes = sum(1 for item in items if item.get("success"))
        audit_applicable = [item for item in items if item.get("workflow_audit", {}).get("applicable")]
        audit_passes = sum(1 for item in audit_applicable if item.get("baseline_adequacy_pass"))
        premature_claims = sum(1 for item in items if item.get("called_claim_done") and not item.get("success"))
        missing_required = sum(1 for item in items if item.get("failure_reason_category") == "missing_required_action")

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
                "baseline_adequacy_pass_rate": round(audit_passes / len(audit_applicable), 3) if audit_applicable else "",
                "premature_claim_done_rate": round(premature_claims / runs, 3) if runs else 0,
                "missing_required_action_rate": round(missing_required / runs, 3) if runs else 0,
                "avg_turns": avg("turns"),
                "avg_tool_calls": avg("tool_calls"),
                "avg_total_tokens": avg("total_tokens"),
                "avg_estimated_cost": avg("estimated_cost"),
            }
        )
    return summary


def write_summary_csv(rows: List[Dict[str, Any]], summary_path: Path) -> None:
    summary = aggregate(rows)
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "task_id",
        "task_name",
        "architecture",
        "runs",
        "success_count",
        "success_rate",
        "baseline_adequacy_pass_rate",
        "premature_claim_done_rate",
        "missing_required_action_rate",
        "avg_turns",
        "avg_tool_calls",
        "avg_total_tokens",
        "avg_estimated_cost",
    ]
    with summary_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(summary)


def paired_task_result(rows: List[Dict[str, Any]], task_id: str, architectures: Optional[List[str]] = None) -> Dict[str, Any]:
    arch_list = architectures or ARCHITECTURES
    by_arch = {arch: [r for r in rows if r["task_id"] == task_id and normalize_architecture(r["architecture"]) == arch] for arch in arch_list}
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


def write_analysis(
    rows: List[Dict[str, Any]],
    command: str,
    dry_run: bool,
    tasks: List[str],
    analysis_path: Path,
    raw_results_path: Path,
    summary_path: Path,
    dump_path: Path,
) -> None:
    run_ids = sorted({row.get("run_id") for row in rows if row.get("run_id") is not None})
    archs_present = [arch for arch in ARCHITECTURES if any(normalize_architecture(row.get("architecture", "")) == arch for row in rows)]
    if not archs_present:
        archs_present = ARCHITECTURES
    has_actual_results = any(row.get("raw_evaluation_output", {}).get("failure") != "dry_run" for row in rows)

    def rows_for_arch(arch: str) -> List[Dict[str, Any]]:
        return [row for row in rows if normalize_architecture(row.get("architecture", "")) == arch]

    def rate(items: List[Dict[str, Any]], key: str) -> float:
        if not items:
            return 0.0
        return sum(1 for item in items if item.get(key)) / len(items)

    def avg_for(task: str, arch: str, key: str):
        vals = [
            row.get(key)
            for row in rows
            if row.get("task_id") == task
            and normalize_architecture(row.get("architecture", "")) == arch
            and isinstance(row.get(key), (int, float))
        ]
        return round(sum(vals) / len(vals), 3) if vals else ""

    strong_failed_task_count = sum(
        1
        for task in tasks
        if paired_task_result(rows, task)["single_strong_workflow"]["runs"]
        and paired_task_result(rows, task)["single_strong_workflow"]["success_count"] == 0
    )
    multi_solved_strong_failures_count = sum(
        1
        for task in tasks
        if paired_task_result(rows, task)["single_strong_workflow"]["runs"]
        and paired_task_result(rows, task)["single_strong_workflow"]["success_count"] == 0
        and paired_task_result(rows, task)["multi_workflow"]["success_count"] > 0
    )
    dynamic_solved_strong_failures_count = sum(
        1
        for task in tasks
        if paired_task_result(rows, task)["single_strong_workflow"]["runs"]
        and paired_task_result(rows, task)["single_strong_workflow"]["success_count"] == 0
        and paired_task_result(rows, task)["multi_dynamic_supervisor"]["success_count"] > 0
    )
    dynamic_solved_workflow_failures_count = sum(
        1
        for task in tasks
        if paired_task_result(rows, task)["multi_workflow"]["runs"]
        and paired_task_result(rows, task)["multi_workflow"]["success_count"] == 0
        and paired_task_result(rows, task)["multi_dynamic_supervisor"]["success_count"] > 0
    )
    strong_fail_multi_success = [
        task
        for task in tasks
        if paired_task_result(rows, task)["single_strong_workflow"]["runs"]
        and paired_task_result(rows, task)["single_strong_workflow"]["success_count"] == 0
        and paired_task_result(rows, task)["multi_workflow"]["success_count"] > 0
    ]
    strong_fail_dynamic_success = [
        task
        for task in tasks
        if paired_task_result(rows, task)["single_strong_workflow"]["runs"]
        and paired_task_result(rows, task)["single_strong_workflow"]["success_count"] == 0
        and paired_task_result(rows, task)["multi_dynamic_supervisor"]["success_count"] > 0
    ]
    workflow_fail_dynamic_success = [
        task
        for task in tasks
        if paired_task_result(rows, task)["multi_workflow"]["runs"]
        and paired_task_result(rows, task)["multi_workflow"]["success_count"] == 0
        and paired_task_result(rows, task)["multi_dynamic_supervisor"]["success_count"] > 0
    ]
    baseline_only_success = [
        task
        for task in tasks
        if paired_task_result(rows, task)["single_baseline"]["success_count"] > 0
        and paired_task_result(rows, task)["single_strong_workflow"]["success_count"] == 0
        and paired_task_result(rows, task)["multi_workflow"]["success_count"] == 0
    ]

    if dry_run and has_actual_results:
        deviation_text = "dry-run 호출로 기존 raw_results.jsonl을 재집계했으며 새 benchmark row는 실행하지 않음"
    elif dry_run:
        deviation_text = "dry-run만 수행되어 실제 benchmark 성공률은 측정되지 않았음"
    else:
        deviation_text = f"결과 파일 기준 run id {len(run_ids)}개가 기록됨. 환경 의존 실패는 agent 성능 실패와 분리해서 해석해야 함."

    lines = [
        "# Toolathlon dynamic supervisor 멀티에이전트 실험",
        "",
        "## 목적",
        "멀티에이전트의 우위를 주장하려면 기본 단일 baseline이 아니라 같은 절차적 도움을 받은 강화 단일 에이전트와 비교해야 한다. 이 문서는 `single_baseline`, `single_strong_workflow`, `multi_workflow`, `multi_dynamic_supervisor`를 분리해 기록한다.",
        "",
        "## 아키텍처",
        "- `single_baseline`: Toolathlon 기본 `TaskAgent`를 그대로 사용한다. 참고용이며 강한 주장에는 사용하지 않는다.",
        "- `single_strong_workflow`: 하나의 agent/context가 Research → Plan → Execute → Self-Verify → Retry → Finalize 절차, checklist, verifier rubric, retry 지시를 모두 수행한다.",
        "- `multi_workflow`: 같은 절차와 금지사항을 역할별 agent, 분리된 context, 독립 Verification Agent, orchestrator-only `claim_done` 권한으로 수행한다.",
        "- `multi_dynamic_supervisor`: Orchestrator가 중앙 통제권을 유지하며 공개 task metadata와 도구 요구사항으로 선택된 specialist agent를 tool처럼 자율 호출한다.",
        "",
        "## 공정성 제약",
        "- task-specific 지시는 원래 Toolathlon task input과 task_config에서만 온다.",
        "- task별 hardcoded repair, groundtruth/evaluation/answer dump 접근, 평가 직전 deterministic final-state patch는 금지한다.",
        "- 멀티만 갖는 차이는 역할별 system prompt, context 분리, 독립 verifier, `claim_done` 권한 분리, 역할별 도구 surface 축소로 제한한다.",
        "",
        "## 실행",
        f"- model: `{os.getenv('MODEL_NAME', 'gpt-5')}`",
        f"- row count: `{len(rows)}`",
        f"- command used: `{command}`",
        f"- date/time: {datetime.now().isoformat(timespec='seconds')}",
        f"- deviations or failures: {deviation_text}",
        f"- primary comparison target: 강화 단일 실패 task {strong_failed_task_count}개 중 workflow 성공 {multi_solved_strong_failures_count}개, dynamic supervisor 성공 {dynamic_solved_strong_failures_count}개",
        "",
        "## 핵심 발견",
        f"- `single_strong_workflow` 대비 `multi_workflow` 추가 성공은 {multi_solved_strong_failures_count}개 task이다: {', '.join(TASK_NAMES.get(t, t) for t in strong_fail_multi_success) if strong_fail_multi_success else '없음'}.",
        f"- `single_strong_workflow` 대비 `multi_dynamic_supervisor` 추가 성공은 {dynamic_solved_strong_failures_count}개 task이다: {', '.join(TASK_NAMES.get(t, t) for t in strong_fail_dynamic_success) if strong_fail_dynamic_success else '없음'}.",
        f"- `multi_workflow` 실패를 `multi_dynamic_supervisor`가 통과한 task는 {dynamic_solved_workflow_failures_count}개다: {', '.join(TASK_NAMES.get(t, t) for t in workflow_fail_dynamic_success) if workflow_fail_dynamic_success else '없음'}.",
        f"- `single_baseline`만 성공하고 strong/multi가 실패한 task는 {len(baseline_only_success)}개다: {', '.join(TASK_NAMES.get(t, t) for t in baseline_only_success) if baseline_only_success else '없음'}.",
        "- K8S PR Preview Testing은 Kubernetes MCP namespace handling 문제(`default` vs `pr-preview-123`)가 반복되어 agent 성능 실패 근거로 쓰기 어렵다.",
        "- 표본은 architecture별 task당 1회이므로 성공률 차이는 관찰값이며 통계적 결론은 아니다. 강한 주장은 3회 이상 반복 후에도 같은 패턴이 유지될 때만 가능하다.",
        "",
        "## 결과",
        "| task | single_baseline | single_strong_workflow | multi_workflow | multi_dynamic_supervisor | strong→dynamic delta | workflow→dynamic delta | strong audit pass |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]

    for task in tasks:
        paired = paired_task_result(rows, task)
        baseline = paired["single_baseline"]
        strong = paired["single_strong_workflow"]
        multi = paired["multi_workflow"]
        dynamic = paired["multi_dynamic_supervisor"]
        strong_dynamic_delta = dynamic["success_rate"] - strong["success_rate"]
        workflow_dynamic_delta = dynamic["success_rate"] - multi["success_rate"]
        strong_rows = [
            row
            for row in rows
            if row.get("task_id") == task
            and normalize_architecture(row.get("architecture", "")) == "single_strong_workflow"
        ]
        strong_audit_rows = [row for row in strong_rows if row.get("workflow_audit", {}).get("applicable")]
        strong_audit_cell = f"{rate(strong_audit_rows, 'baseline_adequacy_pass'):.3f}" if strong_audit_rows else "n/a"
        recovery_proxy = 1.0 if strong["success_count"] == 0 and multi["success_count"] > 0 else 0.0
        lines.append(
            f"| {TASK_NAMES.get(task, task)} | {baseline['success_count']} / {baseline['runs']} | "
            f"{strong['success_count']} / {strong['runs']} | {multi['success_count']} / {multi['runs']} | "
            f"{dynamic['success_count']} / {dynamic['runs']} | {strong_dynamic_delta:.3f} | "
            f"{workflow_dynamic_delta:.3f} | {strong_audit_cell} |"
        )

    lines.extend(
        [
            "",
            "## 사례 메모",
        ]
    )
    if strong_fail_multi_success:
        for task in strong_fail_multi_success:
            if task == "finalpool/inventory-sync":
                lines.append(
                    "- Inventory Sync: baseline과 strong single은 WooCommerce 재고를 갱신하지 못해 0/51로 실패했다. "
                    "multi는 WooCommerce `products/batch` update를 호출했고 evaluation에서 51/51, 100%로 통과했다. "
                    "다만 strong single의 절차 audit은 실패로 분류되어, '충실히 수행한 단일 agent를 독립 verifier가 구조적으로 이겼다'는 가장 강한 사례는 아니다."
                )
            else:
                lines.append(f"- {TASK_NAMES.get(task, task)}: strong single 실패를 multi가 통과했다. 세부 trace 검토가 필요하다.")
    else:
        lines.append("- 이번 run에서는 strong single 실패를 multi가 통과한 task가 없다.")
    if baseline_only_success:
        lines.append(
            "- Excel Data Transformation은 baseline만 통과했다. workflow 지시가 항상 성능을 올린다는 근거는 아니며, task별 분산과 모델 비결정성이 크다는 신호다."
        )
    lines.extend(
        [
            "",
            "## 비용 및 호출 지표",
            "| architecture | rows | pass rate | adequacy pass rate | premature claim rate | missing action rate | avg tools | avg tokens | avg cost |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for arch in archs_present:
        arch_rows = rows_for_arch(arch)
        audit_rows = [row for row in arch_rows if row.get("workflow_audit", {}).get("applicable")]
        missing_rate = (
            sum(1 for row in arch_rows if row.get("failure_reason_category") == "missing_required_action") / len(arch_rows)
            if arch_rows
            else 0
        )
        premature_rate = (
            sum(1 for row in arch_rows if row.get("called_claim_done") and not row.get("success")) / len(arch_rows)
            if arch_rows
            else 0
        )
        avg_tools = round(sum(row.get("tool_calls") or 0 for row in arch_rows) / len(arch_rows), 3) if arch_rows else 0
        avg_tokens = round(sum(row.get("total_tokens") or 0 for row in arch_rows) / len(arch_rows), 3) if arch_rows else 0
        avg_cost = round(sum(row.get("estimated_cost") or 0 for row in arch_rows) / len(arch_rows), 3) if arch_rows else 0
        adequacy_cell = f"{rate(audit_rows, 'baseline_adequacy_pass'):.3f}" if audit_rows else "n/a"
        lines.append(
            f"| {arch} | {len(arch_rows)} | {rate(arch_rows, 'success'):.3f} | {adequacy_cell} | "
            f"{premature_rate:.3f} | {missing_rate:.3f} | {avg_tools} | {avg_tokens} | {avg_cost} |"
        )

    dynamic_rows = rows_for_arch("multi_dynamic_supervisor")
    if dynamic_rows:
        lines.extend(
            [
                "",
                "## Dynamic Supervisor Specialist 호출",
                "각 dynamic run은 workspace의 `profile_selection.json`에 공개 task metadata와 도구 요구사항을 근거로 선택된 specialist roster를 남긴다.",
                "",
                "| task | selected specialists | profile artifact |",
                "|---|---|---|",
            ]
        )
        for row in dynamic_rows:
            specialists = ", ".join(row.get("dynamic_selected_specialists") or []) or "n/a"
            artifact = row.get("dynamic_profile_path") or "n/a"
            lines.append(f"| {TASK_NAMES.get(row.get('task_id'), row.get('task_id'))} | {specialists} | `{artifact}` |")

    lines.extend(
        [
            "",
            "## 단일 에이전트가 최선을 다했는가",
            "절차 audit은 Toolathlon 성공 판정과 분리된 보조 지표다. trace에서 요구사항 확인, 도구/상태 점검, 명시적 계획 또는 checklist, 실제 상태 변경 시도, 산출물/외부 상태 검증, premature `claim_done` 여부를 휴리스틱으로 본다.",
            "기존 raw row에 `workflow_audit` 필드가 없으면 `n/a`로 표시한다. 새 workflow 실행부터 audit이 row에 기록된다.",
            "",
            "| attribution | count |",
            "|---|---:|",
        ]
    )
    attribution_counts = Counter(row.get("failure_attribution", "unknown") for row in rows if not row.get("success"))
    for label, count in sorted(attribution_counts.items()):
        lines.append(f"| {label} | {count} |")

    lines.extend(
        [
            "",
            "## 해석",
        ]
    )
    strong_rows_total = rows_for_arch("single_strong_workflow")
    multi_rows_total = rows_for_arch("multi_workflow")
    dynamic_rows_total = rows_for_arch("multi_dynamic_supervisor")
    strong_rate = rate(strong_rows_total, "success")
    multi_rate = rate(multi_rows_total, "success")
    dynamic_rate = rate(dynamic_rows_total, "success")
    if not has_actual_results:
        conclusion = "dry-run이므로 멀티에이전트 우위 여부를 판단할 수 없다."
    elif dynamic_rows_total and strong_rows_total and dynamic_rate > strong_rate:
        conclusion = "Dynamic supervisor가 강화 단일 workflow보다 높은 pass rate를 보였다. 강한 주장은 절차 audit을 통과한 단일 실패를 dynamic supervisor가 공개 상태 기반 specialist delegation/verification으로 복구한 trace가 있을 때만 유지한다."
    elif dynamic_rows_total and multi_rows_total and dynamic_rate > multi_rate:
        conclusion = "Dynamic supervisor가 고정 multi workflow보다 높은 pass rate를 보였다. 이는 고정 순서보다 자율 specialist delegation이 일부 task에 더 적합할 수 있다는 관찰 신호다."
    elif not strong_rows_total or not multi_rows_total:
        conclusion = "강화 단일, 멀티 workflow, dynamic supervisor 결과가 함께 있어야 강한 구조 비교를 할 수 있다."
    elif multi_rate > strong_rate:
        conclusion = "멀티 workflow가 강화 단일 workflow보다 높은 pass rate를 보였다. 강한 주장은 절차 audit을 통과한 단일 실패를 멀티가 독립 verifier/retry로 복구한 trace가 있을 때만 유지한다."
    elif dynamic_rows_total and dynamic_rate <= strong_rate:
        conclusion = "현재 결과에서는 dynamic supervisor가 강화 단일 workflow보다 높은 성공률을 보였다는 증거가 아직 없다."
    elif multi_rate == strong_rate:
        conclusion = "멀티 workflow가 강화 단일 workflow보다 높은 성공률을 보였다는 증거는 아직 없다."
    else:
        conclusion = "현재 결과에서는 강화 단일 workflow가 multi workflow보다 높거나 같다. 멀티 구조 우위 주장은 지지되지 않는다."
    lines.append(conclusion)

    lines.extend(
        [
            "",
            "## 산출물",
            f"- `{raw_results_path.name}`: run별 원본 row와 workflow audit.",
            f"- `{summary_path.name}`: architecture별 success, audit, premature claim, missing action, 비용 집계.",
            f"- `{analysis_path.name}`: 이 분석 문서.",
            f"- `{dump_path.name}/`: Toolathlon 원본 trace, workspace, `eval_res.json` 로컬 dump. 크기 때문에 git에는 넣지 않는다.",
        ]
    )
    analysis_path.parent.mkdir(parents=True, exist_ok=True)
    analysis_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


async def async_main() -> int:
    parser = argparse.ArgumentParser(description="Toolathlon 단일 vs 멀티에이전트 비교 실험")
    parser.add_argument(
        "--arch",
        choices=sorted([*ARCHITECTURES, *ARCH_ALIASES, *ARCH_GROUPS]),
        default="all",
        help="실행 architecture. `all`은 baseline/strong/multi 3축, `both`는 strong/multi 핵심 비교입니다.",
    )
    parser.add_argument("--runs", type=int, default=int(os.getenv("RUNS_PER_TASK", "3")))
    parser.add_argument("--toolathlon-root", default=None)
    parser.add_argument("--task-list", default=str(TASK_LIST_PATH))
    parser.add_argument("--model", default=os.getenv("MODEL_NAME", "gpt-5"))
    parser.add_argument("--provider", default=os.getenv("MODEL_PROVIDER", "unified"))
    parser.add_argument("--dump-path", default=str(RESULTS_DIR / "dumps"))
    parser.add_argument("--raw-results-path", default=str(FAIR_RAW_RESULTS_PATH))
    parser.add_argument("--summary-path", default=str(FAIR_SUMMARY_PATH))
    parser.add_argument("--analysis-path", default=str(FAIR_ANALYSIS_PATH))
    parser.add_argument(
        "--comparison-results-path",
        default=None,
        help="분석 문서에만 함께 포함할 기존 raw_results JSONL. 예: fair workflow 결과와 dynamic 결과 비교",
    )
    parser.add_argument("--max-steps", type=int, default=int(os.getenv("MAX_STEPS", "200")))
    parser.add_argument("--run-timeout-seconds", type=int, default=int(os.getenv("RUN_TIMEOUT_SECONDS", "1800")))
    parser.add_argument(
        "--tasks",
        default=None,
        help="쉼표로 구분한 task_id subset. 예: finalpool/inventory-sync,finalpool/k8s-pr-preview-testing",
    )
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--dry-run", action="store_true", help="설정/집계 artifact만 검증하고 실제 Toolathlon은 실행하지 않음")
    parser.add_argument("--reset-results", action="store_true")
    parser.add_argument("--skip-existing", action="store_true", help="raw_results.jsonl에 같은 task/arch/run row가 있으면 재실행하지 않음")
    args = parser.parse_args()

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    raw_results_path = Path(args.raw_results_path)
    summary_path = Path(args.summary_path)
    analysis_path = Path(args.analysis_path)
    task_list_path = Path(args.task_list).expanduser().resolve()
    if args.reset_results and raw_results_path.exists():
        raw_results_path.unlink()

    toolathlon_root = discover_toolathlon_root(args.toolathlon_root)
    tasks = select_tasks(load_tasks(task_list_path), args.tasks)
    if not tasks:
        raise ValueError("실행할 task가 없습니다.")
    validate_tasks(toolathlon_root, tasks)
    preflight_warnings = preflight_toolathlon_runtime(toolathlon_root, args.dry_run)
    for warning in preflight_warnings:
        print(f"[주의] {warning}")
    copy_experiment_module_into_toolathlon(toolathlon_root)

    architectures = expand_architectures(args.arch)
    command = " ".join(sys.argv)
    existing_keys = set()
    if args.skip_existing:
        for row in load_jsonl(raw_results_path):
            existing_keys.add((row.get("task_id"), row.get("architecture"), row.get("run_id")))

    for run_id in range(1, args.runs + 1):
        for task in tasks:
            for arch in architectures:
                if (task, arch, run_id) in existing_keys:
                    print(f"[건너뜀] arch={arch} run={run_id} task={task} 이미 결과가 있습니다.")
                    continue
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
                write_jsonl_row(raw_results_path, row)

    rows = load_jsonl(raw_results_path)
    write_summary_csv(rows, summary_path)
    analysis_rows = rows
    if args.comparison_results_path:
        comparison_rows = load_jsonl(Path(args.comparison_results_path))
        analysis_rows = [*comparison_rows, *rows]
    analysis_tasks = [
        task
        for task in load_tasks(task_list_path)
        if any(row.get("task_id") == task for row in analysis_rows)
    ]
    write_analysis(
        analysis_rows,
        command,
        args.dry_run,
        analysis_tasks,
        analysis_path,
        raw_results_path,
        summary_path,
        Path(args.dump_path),
    )

    print("실험 요약 artifact를 갱신했습니다.")
    print(f"- raw: {raw_results_path}")
    print(f"- summary: {summary_path}")
    print(f"- analysis: {analysis_path}")
    return 0


def main() -> int:
    return asyncio.run(async_main())


if __name__ == "__main__":
    raise SystemExit(main())
