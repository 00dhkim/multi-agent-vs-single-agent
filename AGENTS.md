작업을 시작하기 전에 요구사항이 모호하거나 확인이 필요한 사항이 있으면 진행 전에 사용자에게 질문한다.

## 이 레포의 목적

- 이 레포는 공식 Toolathlon checkout을 vendoring하지 않고, 단일 에이전트 baseline과 멀티에이전트 scaffold를 같은 Toolathlon task_config 위에서 비교하는 실험 래퍼다.
- 실제 benchmark 실행은 별도 Toolathlon checkout을 대상으로 한다. 현재 재현 기준 경로는 `/tmp/toolathlon_inspect`다.
- 결과 판정은 이 레포의 추론이 아니라 Toolathlon task별 evaluation script의 `pass` 값을 따른다.

## 재현성 메모

- 실제 실행은 이 레포의 uv 환경이 아니라 Toolathlon checkout의 uv 환경에서 시작해야 한다.
  - 권장 형태: `cd /tmp/toolathlon_inspect && uv run /home/primi/workspace/multi-agent-vs-single-agent/experiments/single_vs_multi/run_experiment.py ...`
  - 이 레포 cwd에서 `uv run python ...`을 실행하면 Toolathlon 의존성, 예를 들어 `termcolor`, 이 없어 runner가 agent loop 전에 실패할 수 있다.
- `OPENAI_API_KEY`가 있으면 runner가 `TOOLATHLON_OPENAI_API_KEY`로 alias한다. `OPENAI_BASE_URL`도 같은 방식으로 `TOOLATHLON_OPENAI_BASE_URL`에 alias된다.
- Snowflake 계정/private key가 필요한 task는 현재 보류한다. `travel-expense-reimbursement`, `payable-invoice-checker`, `sla-timeout-monitor`, `landing-task-reminder`는 Snowflake 없이는 재현 대상에서 제외한다.
- Toolathlon 전역 template warning 중 `token.snowflake_private_key_path`, `token.notion_allowed_page_ids`는 해당 task가 그 서비스를 쓰지 않으면 치명적이지 않다.
- 결과 디렉터리의 `results/dumps/`는 원본 trace와 workspace를 담아 커질 수 있다. 비교표와 분석은 `raw_results.jsonl`, `summary.csv`, `analysis.md`를 우선 확인한다.
- 현재 멀티에이전트 scaffold는 6-agent handoff 뒤에 post-agent verifier/repair pass를 수행한다. 이 pass는 agent workspace와 공개 task 입력만 사용하고, groundtruth workspace와 evaluation 코드는 읽거나 수정하지 않는다는 전제로 유지한다.

## 이미 겪은 실패 지점

- 공식 port mapping을 적용해야 한다. `global_preparation/apply_port_numbers.py -y`로 WooCommerce, Poste, Canvas, K8S PR preview port가 task 문서와 맞도록 바꾼 상태에서 실행한다.
- WooCommerce는 `http://localhost:11003` 기준으로 준비되어야 한다. Inventory Sync 성공/실패는 WooCommerce MCP가 실제 상품 재고를 읽고 쓸 수 있는지에 직접 의존한다.
- Canvas는 PostgreSQL stale pid/socket 때문에 초기화가 막힐 수 있다. Canvas HTTP가 `/login`으로 응답하고 user/admin 생성이 끝난 상태에서 Canvas task를 실행한다.
- K8S PR preview는 두 가지 별도 함정이 있었다.
  - Playwright MCP tool 중 일부가 `parameters.type = None` 형태로 변환되어 OpenAI Chat Completions API가 400을 냈다. `/tmp/toolathlon_inspect/utils/api_model/model_provider.py`에서 tool parameter schema를 object JSON Schema로 정규화하는 패치를 유지한다.
  - Kubernetes MCP가 기본 namespace `default`로 manifest를 적용하면 `preview.yaml`의 `pr-preview-123` namespace와 충돌한다. `/tmp/toolathlon_inspect/tasks/finalpool/k8s-pr-preview-testing/scripts/k8s_pr_preview_testing.sh`에서 current context namespace를 `pr-preview-123`로 설정하는 보정을 시도했다.
  - 위 보정 뒤에도 MCP가 `kubectl apply ... -n default`를 붙이는 사례가 남아 있었다. K8S 시나리오는 별도 MCP namespace handling 수정 전까지 진단용으로만 보고, 10개 비교의 안정 표본에서는 제외하는 편이 낫다.
- K8S task preprocess는 `cluster-pr-preview` kind cluster를 삭제 후 재생성한다. 같은 포트 `31123`을 쓰는 다른 프로세스가 있으면 먼저 정리한다.
- K8S 평가의 성공 판정은 deterministic하다. rollout, pod readiness, service endpoint, `http://localhost:31123` HTTP 응답, `filled-test-results-report.md` 내용을 모두 만족해야 한다.

## 실험 실행 원칙

- 외부 계정이 필요한 MCP 서버를 쓰는 task는 추가 시나리오에서 제외한다. 특히 Snowflake, Notion, Google Cloud/Sheet/Calendar/Forms/Map, HuggingFace, W&B, GitHub 쓰기 작업은 별도 계정 준비 전까지 제외한다.
- 추가 실험은 `experiments/single_vs_multi/toolathlon_10_scenarios.txt`를 기준으로 실행한다.
- 이미 실행된 row를 유지하고 나머지만 채울 때는 `--skip-existing`을 사용한다.
- 기본 명령:

```bash
cd /tmp/toolathlon_inspect
OPENAI_API_KEY="${OPENAI_API_KEY:-}" RUN_TIMEOUT_SECONDS=1800 \
uv run /home/primi/workspace/multi-agent-vs-single-agent/experiments/single_vs_multi/run_experiment.py \
  --toolathlon-root /tmp/toolathlon_inspect \
  --task-list /home/primi/workspace/multi-agent-vs-single-agent/experiments/single_vs_multi/toolathlon_10_scenarios.txt \
  --runs 1 \
  --skip-existing
```

## 해석 메모

- post-agent verifier/repair 개선 뒤 현재 10-scenario 결과에서는 단일 실패 9개 중 멀티가 6개를 성공했다. 성공한 단일 실패 작업은 Inventory Sync, Paper Checker, Excel Data Transformation, Arrange Workspace, Reimbursement Form Filler, PPT Analysis다.
- Inventory Sync의 핵심 실패 모드는 지역별 SKU와 일반 SKU를 혼동하거나 재고 갱신까지 이어지지 않은 필수 행동 누락이다. 개선판은 SQLite 지역 재고 합계를 계산한 뒤 WooCommerce 지역 SKU를 batch update한다.
- Excel Data Transformation에서는 단일이 `Processed.xlsx`를 만들지 못했고, 멀티가 workbook 형식을 맞춰 산출 파일을 생성해 통과했다.
- Paper Checker, Arrange Workspace, Reimbursement Form Filler, PPT Analysis는 최종 파일 생성, 참조 보정, 파일 위치 정규화 같은 마지막 상태 보정이 성공 요인이었다.
- 성공/실패는 모델의 자기평가가 아니라 task별 평가 스크립트의 deterministic 검사 결과다. 사람이 이해할 수 있도록 `eval_res.json`, `traj_log.json`, `raw_results.jsonl`을 함께 확인한다.
