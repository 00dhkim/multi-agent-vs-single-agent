# Toolathlon 단일 에이전트 vs 멀티에이전트 실험

## 목적
세 개의 장기 tool-use Toolathlon 작업에서 멀티에이전트 구조가 강한 단일 에이전트 baseline 대비 성능을 향상시키는지 정량적으로 평가한다.

## 선택한 작업
| task_id | 작업 이름 | 도메인 | 선택 이유 | 기대되는 멀티에이전트 이점 |
|---|---|---|---|---|
| finalpool/travel-expense-reimbursement | Travel Expense Reimbursement | office | 문서 검증, 이메일, Snowflake 쓰기가 결합된 장기 작업 | 조사/계획/검증 분리로 누락과 성급한 완료를 줄일 수 있음 |
| finalpool/inventory-sync | Inventory Sync | shopping | 여러 SQLite warehouse와 WooCommerce 동기화가 필요함 | 조사 agent가 최신 미반영 재고를 식별하고 실행 agent가 갱신을 분리할 수 있음 |
| finalpool/k8s-pr-preview-testing | K8S PR Preview Testing | tech | Git, Kubernetes, ConfigMap, Playwright/테스트 보고서가 결합됨 | 실행 단계와 verifier가 배포 상태 및 보고서 산출물을 별도로 확인할 수 있음 |

## 아키텍처
강한 단일 에이전트 baseline은 Toolathlon 기본 OpenAI Agents SDK 기반 TaskAgent를 사용한다. 단일 agent는 task_config가 허용한 모든 MCP/local tool을 받고, 계획, 실행, 검증, `claim_done`을 모두 직접 수행한다.

멀티에이전트 구조는 동일 모델과 동일 task_config를 사용하되 Orchestrator Agent를 루트로 두고 Research/Inspection, Planning, Action/Execution, Verification, Memory/Summary Agent로 handoff한다. 여섯 agent는 세 작업 모두에서 같은 일반 목적 prompt를 사용한다.

Verifier는 완료 전 독립 점검 역할을 맡으며, Orchestrator는 verifier 승인 전 `claim_done`을 호출하지 않도록 지시받는다. 도구 접근은 현재 최소 구현에서 동일 tool 객체를 공유하고 역할 prompt로 읽기/쓰기 책임을 제한한다.

## 실행
- model: `gpt-5`
- run count: 결과 파일 기준 `6`개 row
- command used: `/home/primi/workspace/multi-agent-vs-single-agent/experiments/single_vs_multi/run_experiment.py --toolathlon-root /tmp/toolathlon_inspect --arch both --runs 1 --reset-results --model gpt-5 --run-timeout-seconds 60`
- environment: Toolathlon root는 실행 시 `--toolathlon-root` 또는 `TOOLATHLON_ROOT`로 결정됨
- date/time: 2026-05-21T14:57:16
- deviations or failures: 기본 3회 반복 대신 1회 반복 결과만 기록함. 현재 checkout/서비스 환경에서 일부 작업이 agent loop 전 단계에서 실패했기 때문임.
- preprocess failures: 4개 row에서 Toolathlon preprocess 단계가 실패해 LLM turn이 발생하지 않음
- k8s runner failures: `tasks/finalpool/k8s-pr-preview-testing/k8s_configs/cluster-pr-preview-config.yaml` 누락으로 실행 전 실패

## 결과
| task | single success count / runs | multi success count / runs | delta | single avg turns | multi avg turns | single avg tool calls | multi avg tool calls | single avg tokens/cost | multi avg tokens/cost |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Travel Expense Reimbursement | 0 / 1 | 0 / 1 | 0.000 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0/0.0 | 0.0/0.0 |
| Inventory Sync | 0 / 1 | 0 / 1 | 0.000 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0/0.0 | 0.0/0.0 |
| K8S PR Preview Testing | 0 / 1 | 0 / 1 | 0.000 |  |  |  |  | / | / |

## 사례 분석: 단일 에이전트 실패, 멀티에이전트 성공
현재 결과에서는 단일 에이전트가 실패하고 멀티에이전트가 성공한 사례가 확인되지 않았다. 이번 실행은 모든 row가 preprocess 또는 runner 환경 단계에서 실패해 agent reasoning, handoff, verifier의 효과를 관찰할 수 없었다.

## 실패 분석
### 단일 에이전트 실패
복구되지 않은 도구/API 오류

### 멀티에이전트 실패
복구되지 않은 도구/API 오류

### 공통 실패
복구되지 않은 도구/API 오류

## 결론
세 작업 결과만으로는 멀티에이전트가 단일 에이전트 대비 성능을 향상시킨다는 주장을 지지하지 못한다.

요구 질문 답변 요약:
- 멀티에이전트가 단일 에이전트 실패 작업을 해결했는가: 아니오 또는 미측정
- 해당 작업: 없음
- 절대 성공률 향상: 0.000
- 상대 성공률 향상: 정의 불가(single 성공률 0)
- turn/tool/token 비용: 전처리/환경 실패로 실제 LLM turn과 token은 거의 발생하지 않았다. 평균 tool call은 single 0.000, multi 0.000이다.
- 비용 대비 개선 여부: 성공률 개선이 없고 실행이 환경 단계에서 실패했으므로 개선이 비용을 정당화했다고 볼 수 없다.
- 가장 크게 기여한 specialist agent: 성공 사례가 없어 식별할 수 없다.
- handoff 또는 verifier가 만든 실패: LLM 실행 전 실패가 대부분이라 확인된 handoff/verifier 실패는 없다.
- single에만 나타난 실패 모드: 없음.
- multi에만 나타난 실패 모드: 없음.
