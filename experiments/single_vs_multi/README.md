# Toolathlon 단일 에이전트 vs 멀티에이전트 실험

## 목적

이 실험은 공식 Toolathlon benchmark 작업에서 강한 단일 에이전트 baseline과 일반 목적 orchestrator-worker 멀티에이전트 구조를 비교한다. 현재 주 실험은 10개 시나리오를 대상으로 하며, 목표는 역할 분리와 handoff가 단일 에이전트 대비 성공률, 비용, 도구 호출 패턴을 개선하는지 확인하는 것이다. 결과는 Toolathlon 평가 결과 그대로 보고한다.

## 선택한 작업

기본 작업 목록은 [toolathlon_10_scenarios.txt](/home/primi/workspace/multi-agent-vs-single-agent/experiments/single_vs_multi/toolathlon_10_scenarios.txt)에 기록한다. Snowflake처럼 외부 계정이 필요한 작업은 제외했고, WooCommerce, 파일 시스템, 문서 편집, Excel, Kubernetes처럼 로컬 Toolathlon 환경에서 즉시 준비 가능한 시나리오를 우선했다.

초기 3-task 목록은 [toolathlon_3_tasks.txt](/home/primi/workspace/multi-agent-vs-single-agent/experiments/single_vs_multi/toolathlon_3_tasks.txt)에 보존되어 있다. 세부 선정 기준과 제외 이유는 [scenario_plan.md](/home/primi/workspace/multi-agent-vs-single-agent/experiments/single_vs_multi/scenario_plan.md)를 본다.

## 조사한 Toolathlon 구조

- 공식 단일 작업 runner: `main.py`
- 병렬/컨테이너 runner: `run_parallel.py`, `scripts/run_single_containerized.sh`, `scripts/run_single_decoupled.sh`
- task 선택 방식: `TaskConfig.build(task_dir, ...)`가 `tasks/{task_dir}/task_config.json`과 `docs/task.md`를 읽는다.
- 평가 결과: 각 run의 `traj_log.json`을 기준으로 `utils/evaluation/evaluator.py`가 `eval_res.json`을 작성한다.
- task_config 도구 노출: 각 작업의 `task_config.json` 안에 `needed_mcp_servers`, `needed_local_tools`가 있다.
- 기본 OpenAI Agents SDK scaffold: `utils/roles/task_agent.py`의 `TaskAgent.setup_agent()`가 `Agent(...)`를 생성하고 MCP/local tool을 붙인다.

## 아키텍처

단일 에이전트 baseline은 Toolathlon 기본 `TaskAgent`를 그대로 사용한다. 단일 agent는 task_config가 허용한 모든 도구를 받고, 계획, 조사, 실행, 검증, `claim_done`을 한 agent가 수행한다.

멀티에이전트 구조는 [multi_agent_scaffold.py](/home/primi/workspace/multi-agent-vs-single-agent/experiments/single_vs_multi/multi_agent_scaffold.py)를 사용한다. 공식 `TaskAgent`를 상속해 workspace 초기화, MCP 연결, evaluation, log 저장은 유지하고 `setup_agent()`만 6-agent handoff 구조로 교체한다.

현재 정정판은 `run_interaction_loop()` 종료 뒤 별도 post-agent repair를 실행하지 않는다. 이전 repair layer는 task별 정답에 해당하는 경로, 셀 좌표, 문서 본문, 참조 매핑을 코드에 포함해 공정성 제약을 위반했으므로 제거했다.

실행 시 [run_experiment.py](/home/primi/workspace/multi-agent-vs-single-agent/experiments/single_vs_multi/run_experiment.py)는 single과 multi 모두에 같은 공통 실행 지시를 추가한다. 이 지시는 목표/제약 확인, 조사, 계획, 근거 기반 실행, 완료 전 검증, 검증 전 `claim_done` 금지를 요구한다. single에는 “강한 단일 에이전트 baseline”이라는 설명만 덧붙이고, multi에는 동일 task_config와 benchmark 도구만 사용한다는 설명을 덧붙인다.

공통 sub-agent 구성:

- Orchestrator Agent
- Research/Inspection Agent
- Planning Agent
- Action/Execution Agent
- Verification Agent
- Memory/Summary Agent

모든 작업은 같은 prompt 파일을 사용한다. task-specific behavior는 Toolathlon task input, task_config 도구, Orchestrator의 임시 지시에서만 나온다.

## 도구 접근 전략

최소 침습 구현을 우선했기 때문에 현재 scaffold는 모든 agent가 같은 MCP/local tool 객체에 접근한다. 대신 prompt에서 Research/Inspection은 읽기/낮은 위험 도구 우선, Action/Execution은 승인된 상태 변경만 수행, Verification은 완료 승인/거절을 담당하도록 제한한다. 엄격한 per-agent tool ACL은 후속 개선 항목이다.

## 실행 준비

공식 Toolathlon checkout이 필요하다. 이 스크립트는 다음 순서로 루트를 찾는다.

1. `--toolathlon-root`
2. `TOOLATHLON_ROOT`
3. 현재 디렉터리
4. `./Toolathlon`
5. `/tmp/toolathlon_inspect`

모델 설정은 환경 변수로 바꿀 수 있다.

```bash
export MODEL_NAME=gpt-5
export OPENAI_API_KEY=...
export OPENAI_BASE_URL=https://api.openai.com/v1
```

Toolathlon의 `unified` provider convention에 맞춰 `OPENAI_*` 값은 실행 중 `TOOLATHLON_OPENAI_*`로 alias된다.

## 실행 명령

설정과 artifact 생성을 먼저 확인한다.

```bash
python3 experiments/single_vs_multi/run_experiment.py --dry-run --runs 1 --reset-results
```

실제 최소 실행은 architecture별 task별 1회다.

```bash
uv run python experiments/single_vs_multi/run_experiment.py \
  --toolathlon-root /path/to/Toolathlon \
  --arch both \
  --runs 1 \
  --model "${MODEL_NAME:-gpt-5}"
```

비용과 시간이 허용되면 기본 계획인 3회 반복을 실행한다.

```bash
uv run python experiments/single_vs_multi/run_experiment.py \
  --toolathlon-root /path/to/Toolathlon \
  --arch both \
  --runs 3 \
  --model "${MODEL_NAME:-gpt-5}"
```

## 결과 artifact

- `results/raw_results.jsonl`: run별 JSON row
- `results/summary.csv`: 작업/architecture별 집계
- `results/analysis.md`: 한국어 분석 문서
- `results/dumps/`: Toolathlon run dump와 원본 `traj_log.json`, `eval_res.json`
- `results/dumps/.../runner_errors/runner_exception.json`: Toolathlon loop 진입 전 또는 실행 중 발생한 runner 예외 trace

`summary.csv`의 column 이름은 downstream 처리를 위해 영어로 둔다. `success_rate`는 `success_count / runs`, `avg_turns`는 평균 interaction turn, `avg_tool_calls`는 평균 도구 호출 수, `avg_total_tokens`와 `avg_estimated_cost`는 Toolathlon log에서 얻은 값이다.

## 해석 원칙

- Toolathlon evaluation의 `pass`만 성공으로 센다.
- 부분 성공은 성공으로 세지 않는다.
- 단일 에이전트 실패를 본 뒤 멀티에이전트 prompt만 조정하지 않는다.
- 평가 스크립트나 정답 상태를 수정하지 않는다.
- `raw_results.jsonl`, Toolathlon `traj_log.json`, `eval_res.json`를 audit 근거로 보존한다.

## 현재 제한

이 작업트리 자체는 빈 실험 래퍼이며 공식 Toolathlon 전체 코드를 vendoring하지 않는다. 실제 benchmark 실행에는 Toolathlon 의존성 설치, 컨테이너 런타임, MCP 서비스 계정/로컬 서비스, 모델 API 설정이 필요하다. 환경이 준비되지 않았을 때는 `--dry-run`으로 파일 구조와 집계를 검증한다.
