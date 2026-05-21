You are Codex. Your task is to implement and run a controlled experiment comparing SINGLE-AGENT vs MULTI-AGENT architectures on exactly three Toolathlon scenarios.

Goal:
Measure whether a multi-agent architecture improves task success over a strong single-agent baseline on Toolathlon. The desired finding is that the multi-agent system solves at least one task that the single-agent system fails, but you must report results honestly even if this does not happen.

Benchmark:
Use the official Toolathlon benchmark repository: hkust-nlp/Toolathlon.

Use exactly these three Toolathlon tasks:
1. Travel Expense Reimbursement
   - Toolathlon docs path: docs/tasks/office/331
   - Nature: validate expense claims against invoices, send emails for incomplete/mismatched/over-cap claims, write valid reimbursement data to Snowflake.

2. Inventory Sync
   - Toolathlon docs path: docs/tasks/shopping/304
   - Nature: inspect multiple city warehouse SQLite databases and synchronize latest unupdated product inventory to WooCommerce.

3. K8S PR Preview Testing
   - Toolathlon docs path: docs/tasks/tech/245
   - Nature: deploy feature/pr-123 branch of SimpleShopping to Kubernetes, create ConfigMap from preview.yaml, expose page at localhost:30123, run tests, and write filled-test-results-report.md.

Do not add more tasks. Do not substitute tasks unless one of the above is impossible to run in the current Toolathlon checkout. If substitution is unavoidable, explain exactly why and choose the closest task with:
- long-horizon tool use,
- multiple tool domains,
- deterministic evaluation script,
- evidence that strong default agents often fail.

Experimental design:
Compare two architectures under the same model, same Toolathlon task initial state, same allowed benchmark tools, and same run budget.

Architecture A: strong single-agent baseline.
- Use Toolathlon’s default agent scaffold as much as possible.
- The single agent receives all tools allowed by the task_config.
- It may use Toolathlon’s existing context/history/overlong-output tools.
- Do not intentionally weaken the single-agent baseline.
- Keep the system prompt strong and explicit: plan, inspect, execute, verify, and call claim_done only after checking final state.

Architecture B: multi-agent orchestrator-worker system.
Implement a GENERAL-PURPOSE orchestrator-led multi-agent system using the same underlying LLM model.
Do not design task-specific sub-agent types such as TravelExpenseAgent, InventorySyncAgent, or K8SDeploymentAgent.
The same multi-agent structure must be used across all three Toolathlon tasks.

Required general-purpose multi-agent pattern:
- Orchestrator Agent:
  - owns the global task plan,
  - decomposes the task into subtasks,
  - assigns work to general-purpose specialist agents,
  - integrates findings,
  - decides final action sequence,
  - calls claim_done only after verifier approval.

- Research/Inspection Agent:
  - gathers facts from available read-only or low-risk tools,
  - inspects files, records, task state, policies, documents, configs, or existing system state,
  - returns structured findings with evidence and uncertainty.

- Planning Agent:
  - converts the task objective and inspection findings into an executable step-by-step plan,
  - identifies dependencies, required tools, risk points, and verification criteria,
  - updates the plan when new information appears.

- Action/Execution Agent:
  - performs mutating or state-changing actions only when authorized by the Orchestrator,
  - executes tool calls according to the approved plan,
  - records every performed action and its observed result.

- Verification Agent:
  - checks whether the current state likely satisfies the task objective,
  - checks for missing requirements, unsafe actions, inconsistent evidence, and premature completion,
  - can reject completion and request another investigation/action cycle.

- Memory/Summary Agent:
  - maintains compact Korean summaries of important facts, decisions, evidence, tool results, and unresolved issues,
  - reduces context bloat without dropping task-critical information,
  - provides handoff summaries between agents.

All three Toolathlon tasks must use this same agent set:
1. Orchestrator Agent
2. Research/Inspection Agent
3. Planning Agent
4. Action/Execution Agent
5. Verification Agent
6. Memory/Summary Agent

Task-specific behavior should be expressed only through task input, tool availability, and temporary instructions from the Orchestrator, not by creating task-specific sub-agent classes or prompts.

Implementation requirements:
1. First, inspect the Toolathlon repository structure.
   - Find the official runner entrypoints.
   - Find how tasks are selected.
   - Find where evaluation results are written.
   - Find how task_config exposes needed_mcp_servers and needed_local_tools.
   - Find how the default OpenAI Agents SDK scaffold is implemented.

2. Prefer minimal invasive changes.
   - Add a new experiment module, for example:
     experiments/single_vs_multi/
   - Do not break existing Toolathlon runner behavior.
   - Reuse Toolathlon’s agent loop, tool wrappers, container setup, evaluation scripts, and result aggregation where possible.

3. Implement two runnable modes:
   - --arch single
   - --arch multi

4. Implement a task list file containing exactly the three selected tasks.
   - Name it experiments/single_vs_multi/toolathlon_3_tasks.txt.
   - Use the exact task IDs/slugs expected by the repository runner.
   - If the IDs are not obvious, discover them from the repository’s tasks directory or configs.

5. Run plan:
   - Default to 3 repeated runs per architecture per task if cost/time allows.
   - If full 3x3x2 is too expensive or technically blocked, run at least 1 run per architecture per task and document the limitation.
   - Use the same model for both architectures.
   - The model name should be configurable via env var MODEL_NAME.
   - API base URL and key should be configurable via OPENAI_BASE_URL and OPENAI_API_KEY or the repository’s existing convention.
   - Do not hardcode secrets.

6. Metrics to collect:
   - task_id
   - task_name
   - architecture: single or multi
   - run_id
   - model
   - success / failure from Toolathlon evaluation
   - raw evaluation output
   - wall-clock time
   - number of turns
   - number of tool calls
   - tool call breakdown by tool name if available
   - prompt tokens, completion tokens, total tokens if available
   - estimated cost if available
   - whether the agent called claim_done
   - failure reason category, inferred from logs:
     a. wrong final state
     b. missing required action
     c. wrong tool/action
     d. context/history failure
     e. tool/API error not recovered
     f. premature claim_done
     g. timeout
     h. unknown

7. Result artifacts:
Create the following files:
- experiments/single_vs_multi/README.md
  - Korean document explaining the purpose, selected tasks, architectures, setup, commands, and interpretation.
- experiments/single_vs_multi/run_experiment.py
  - main runnable script or wrapper.
- experiments/single_vs_multi/multi_agent_scaffold.py
  - general-purpose multi-agent implementation shared across all tasks.
- experiments/single_vs_multi/prompts/
  - Korean system prompts for the Orchestrator, Research/Inspection, Planning, Action/Execution, Verification, and Memory/Summary agents.
- experiments/single_vs_multi/results/raw_results.jsonl
  - one JSON per run. Human-readable fields should be Korean where practical.
- experiments/single_vs_multi/results/summary.csv
  - aggregated table. Column names may remain English if easier for downstream processing, but include Korean descriptions in README.md.
- experiments/single_vs_multi/results/analysis.md
  - Korean result analysis.

Language requirements:
All experiment artifacts must be written in Korean unless source code syntax requires English identifiers.

This includes:
- experiments/single_vs_multi/README.md
- experiments/single_vs_multi/prompts/*
- experiments/single_vs_multi/results/analysis.md
- comments and documentation strings where practical
- terminal-facing experiment summaries generated by the scripts
- failure category descriptions in result files
- table headers in summary.csv where practical

Code variable names, function names, CLI flags, JSON keys, and integration points may remain in English for maintainability.
However, human-readable explanations, prompts, analysis, and documentation must be Korean.

8. The final analysis must answer:
   - Did multi-agent solve any task that single-agent failed?
   - Which task(s)?
   - What was the absolute success-rate improvement?
   - What was the relative success-rate improvement?
   - Did multi-agent require more turns/tool calls/tokens?
   - Was the improvement worth the cost?
   - Which specialist agent contributed most to success?
   - Did handoff or verifier introduce any failure?
   - Which failure modes appeared in single-agent but not multi-agent?
   - Which failure modes appeared in multi-agent but not single-agent?

9. Report format in analysis.md:
Write the entire analysis in Korean using this structure:

# Toolathlon 단일 에이전트 vs 멀티에이전트 실험

## 목적
세 개의 장기 tool-use Toolathlon 작업에서 멀티에이전트 구조가 강한 단일 에이전트 baseline 대비 성능을 향상시키는지 정량적으로 평가한다.

## 선택한 작업
Table with:
- task_id
- 작업 이름
- 도메인
- 선택 이유
- 기대되는 멀티에이전트 이점

## 아키텍처
Describe in Korean:
- 강한 단일 에이전트 baseline
- 일반화된 orchestrator-worker 멀티에이전트 구조
- 공통 sub-agent 구성:
  - Orchestrator Agent
  - Research/Inspection Agent
  - Planning Agent
  - Action/Execution Agent
  - Verification Agent
  - Memory/Summary Agent
- verifier 역할
- tool access strategy

## 실행
Include:
- model
- run count
- command used
- environment
- date/time
- any deviations or failures

## 결과
Table:
- task
- single success count / runs
- multi success count / runs
- delta
- single avg turns
- multi avg turns
- single avg tool calls
- multi avg tool calls
- single avg tokens/cost if available
- multi avg tokens/cost if available

## 사례 분석: 단일 에이전트 실패, 멀티에이전트 성공
If any such case exists, include in Korean:
- task
- single-agent가 무엇을 잘못했는지
- multi-agent가 무엇을 다르게 했는지
- 어떤 공통 sub-agent가 핵심 정보를 찾거나 오류를 방지했는지
- verifier 기여
- trace 근거

If no such case exists, say so clearly in Korean and analyze why.

## 실패 분석
Separate sections:
- 단일 에이전트 실패
- 멀티에이전트 실패
- 공통 실패

## 결론
State in Korean whether this experiment supports the claim:
“장기적이고 다중 도구를 사용하는 Toolathlon 작업에서 멀티에이전트는 단일 에이전트 대비 성능을 향상시킨다.”

Be precise. Do not overclaim from three tasks.

10. Fairness constraints:
- Do not tune the multi-agent prompts after seeing single-agent failures unless the same opportunity is given to the single-agent prompt.
- Do not give multi-agent extra hidden information.
- Do not change evaluation scripts.
- Do not manually patch final task state.
- Do not count partial success as success unless Toolathlon evaluation says success.
- Preserve all traces needed for audit.

11. If custom multi-agent integration with Toolathlon’s runner is too time-consuming:
Implement the smallest viable version:
- keep the official Toolathlon environment and evaluation,
- wrap the agent decision loop with a multi-agent orchestrator,
- share the same tool objects among agents but restrict access in code where feasible,
- if strict per-agent tool restriction is impossible, enforce restrictions in prompts and log this limitation.

12. If custom multi-agent integration with Toolathlon’s runner is too time-consuming:
Implement the smallest viable version:
- keep the official Toolathlon environment and evaluation,
- wrap the agent decision loop with a general-purpose multi-agent orchestrator,
- use the same shared sub-agent set for all tasks,
- share the same tool objects among agents but restrict access in code where feasible,
- if strict per-agent tool restriction is impossible, enforce restrictions in Korean prompts and log this limitation in Korean.

Deliverables:
At the end, produce:
1. a short Korean terminal summary,
2. committed code changes or a patch,
3. the result artifacts listed above,
4. a final written conclusion in Korean.

Begin by inspecting the repository and producing a short Korean implementation plan in experiments/single_vs_multi/README.md. Then implement and run the experiment.