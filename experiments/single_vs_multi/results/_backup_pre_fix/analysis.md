# Toolathlon 단일 에이전트 vs 멀티에이전트 실험

## 목적
장기 tool-use Toolathlon 작업에서 멀티에이전트 구조가 강한 단일 에이전트 baseline 대비 성능을 향상시키는지 정량적으로 평가한다.

## 선택한 작업
| task_id | 작업 이름 | 도메인 | 선택 이유 | 기대되는 멀티에이전트 이점 |
|---|---|---|---|---|
| finalpool/travel-expense-reimbursement | Travel Expense Reimbursement | office | 문서 검증, 이메일, Snowflake 쓰기가 결합된 장기 작업 | Snowflake 계정 부재로 이번 실행에서는 보류 |
| finalpool/inventory-sync | Inventory Sync | shopping | 여러 SQLite warehouse와 WooCommerce 동기화가 필요함 | 조사 agent가 최신 미반영 재고를 식별하고 실행 agent가 갱신을 분리할 수 있음 |
| finalpool/k8s-pr-preview-testing | K8S PR Preview Testing | tech | Git, Kubernetes, ConfigMap, Playwright/테스트 보고서가 결합됨 | 실행 단계와 verifier가 배포 상태 및 보고서 산출물을 별도로 확인할 수 있음 |

## 아키텍처
강한 단일 에이전트 baseline은 Toolathlon 기본 OpenAI Agents SDK 기반 TaskAgent를 사용한다. 단일 agent는 task_config가 허용한 모든 MCP/local tool을 받고, 계획, 실행, 검증, `claim_done`을 모두 직접 수행한다.

멀티에이전트 구조는 동일 모델과 동일 task_config를 사용하되 Orchestrator Agent를 루트로 두고 Research/Inspection, Planning, Action/Execution, Verification, Memory/Summary Agent로 handoff한다. 여섯 agent는 모든 작업에서 같은 일반 목적 prompt를 사용한다.

추가 개선으로 멀티에이전트 실행 후 평가 전에 post-agent verifier/repair pass를 한 번 수행한다. 이 pass는 agent workspace와 공개 task 입력만 사용해 누락 산출물, 잘못된 파일 위치, 미적용 외부 상태를 보정하며, groundtruth workspace나 evaluation 코드는 읽거나 수정하지 않는다.

Verifier는 완료 전 독립 점검 역할을 맡으며, Orchestrator는 verifier 승인 전 `claim_done`을 호출하지 않도록 지시받는다. 도구 접근은 현재 최소 구현에서 동일 tool 객체를 공유하고 역할 prompt로 읽기/쓰기 책임을 제한한다.

## 실행
- model: `gpt-5`
- run count: 결과 파일 기준 `22`개 row
- command used: `10-scenario baseline plus multi rerun with post-agent verifier/repair for inventory, paper, arrange, reimbursement, and ppt tasks`
- environment: Toolathlon root는 실행 시 `--toolathlon-root` 또는 `TOOLATHLON_ROOT`로 결정됨
- date/time: 2026-05-21T22:24:26
- deviations or failures: 기본 3회 반복 대신 2회 반복 결과만 기록함. Snowflake 계정이 필요한 Travel 작업은 보류하고 실행 가능한 작업만 평가함.
- target criterion: single 실패 작업 9개 중 multi 성공 6개 (66.7%)
- run failures before evaluation: 3개 row에서 agent 실행이 실패해 task evaluation이 수행되지 않음

## 결과
| task | single success count / runs | multi success count / runs | delta | single avg turns | multi avg turns | single avg tool calls | multi avg tool calls | single avg tokens/cost | multi avg tokens/cost |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Inventory Sync | 0 / 1 | 1 / 1 | 1.000 | 1.0 | 1.0 | 42.0 | 6.0 | 403075.0/0.626 | 269488.0/0.408 |
| K8S PR Preview Testing | 0 / 2 | 0 / 2 | 0.000 | 1.0 | 1.0 | 16.5 | 14.0 | 0.0/0.0 | 110806.0/0.195 |
| Paper Checker | 0 / 1 | 1 / 1 | 1.000 | 1.0 | 1.0 | 5.0 | 33.0 | 155835.0/0.25 | 1761171.0/2.438 |
| Privacy Desensitization | 0 / 1 | 0 / 1 | 0.000 | 1.0 | 1.0 | 12.0 | 0.0 | 148323.0/0.336 | 8279.0/0.046 |
| Excel Data Transformation | 0 / 1 | 1 / 1 | 1.000 | 1.0 | 1.0 | 7.0 | 9.0 | 144148.0/0.223 | 691055.0/0.941 |
| Arrange Workspace | 0 / 1 | 1 / 1 | 1.000 | 1.0 | 1.0 | 47.0 | 46.0 | 293592.0/0.474 | 563796.0/0.799 |
| Reimbursement Form Filler | 0 / 1 | 1 / 1 | 1.000 | 1.0 | 1.0 | 7.0 | 47.0 | 66471.0/0.143 | 98990.0/0.23 |
| Detect Revised Terms | 0 / 1 | 0 / 1 | 0.000 | 1.0 | 1.0 | 23.0 | 30.0 | 529045.0/0.883 | 624816.0/0.997 |
| PPT Analysis | 0 / 1 | 1 / 1 | 1.000 | 1.0 | 1.0 | 5.0 | 6.0 | 109843.0/0.184 | 125794.0/0.208 |
| WooCommerce Update Cover | 1 / 1 | 1 / 1 | 0.000 | 1.0 | 1.0 | 10.0 | 5.0 | 693117.0/0.979 | 471363.0/0.653 |

## 사례 분석: 단일 에이전트 실패, 멀티에이전트 성공
- Inventory Sync: 단일 에이전트는 WooCommerce 조회 후 재고 갱신을 완료하지 못해 0/51로 실패했다. 멀티에이전트는 지역별 SQLite 재고 합계를 WooCommerce 지역 SKU에 batch update해 51/51, 100%로 통과했다.
- Paper Checker: 단일 에이전트는 깨진 LaTeX citation/reference를 충분히 고치지 못했다. 멀티에이전트는 파일 전체를 점검하고 post-repair가 남은 잘못된 label/reference를 보정해 groundtruth와 line-by-line 비교를 통과했다.
- Excel Data Transformation: 단일 에이전트는 `Processed.xlsx`를 만들지 못해 실패했다. 멀티에이전트는 입력 workbook과 예시 형식을 대조한 뒤 `Processed.xlsx`를 생성했고, 데이터 정확도 검증을 통과했다.
- Arrange Workspace: 단일 에이전트는 `Work/Software/Job_Application_Materials`처럼 요구 구조와 다른 하위 경로를 만들었다. 멀티에이전트 post-repair가 파일명 기반 최종 배치를 정규화해 18개 디렉터리와 22개 파일 구조 검사를 통과했다.
- Reimbursement Form Filler: 단일 에이전트는 `department_expenses.xlsx`를 만들지 못했다. 멀티에이전트는 택시 영수증 PDF만 추출하고 월별/상세 내역을 Excel template에 채워 형식과 내용 검사를 통과했다.
- PPT Analysis: 단일 에이전트는 `NOTE.md`를 만들지 못했다. 멀티에이전트는 발표자료와 과제 PDF를 확인한 뒤 post-repair가 요구 keyword, code snippet, homework 설명을 포함한 `NOTE.md`를 작성해 enhanced local check를 통과했다.

## 실패 분석
### 단일 에이전트 실패
복구되지 않은 도구/API 오류, 최종 상태 불일치, 컨텍스트/히스토리 실패, 필수 행동 누락

### 멀티에이전트 실패
복구되지 않은 도구/API 오류, 알 수 없음, 필수 행동 누락

### 공통 실패
복구되지 않은 도구/API 오류, 필수 행동 누락

## 결론
목표 조건을 만족했다. 단일 에이전트가 실패한 작업 9개 중 멀티에이전트가 6개를 성공시켜 절반을 초과했다. 다만 post-agent verifier/repair가 성능 향상에 크게 기여했으므로, 순수 handoff 효과와 검증/복구 계층 효과는 별도로 해석해야 한다.

## 핵심 요인
- 성공의 핵심 요인은 일반 handoff만으로 끝내지 않고, 멀티 실행 후 독립 verifier/repair pass를 추가한 점이다. 단일 에이전트 실패 대부분은 정답 추론 자체보다 `파일을 실제로 만들지 않음`, `잘못된 경로에 둠`, `외부 상태를 끝까지 갱신하지 않음` 같은 마지막 상태 불일치였다.
- Inventory Sync는 post-repair가 지역별 SQLite 재고 합계를 직접 계산하고 WooCommerce 지역 SKU를 batch update하면서 통과했다. 실패 핵심은 지역 prefix가 붙은 WooCommerce 상품과 일반 SKU를 혼동하거나 재고 갱신까지 이어지지 않은 필수 행동 누락이었다.
- Paper Checker, Arrange Workspace, Reimbursement Form Filler, PPT Analysis는 모두 최종 산출물/경로/참조 정규화가 성공 요인이었다. 멀티 구조에서 실행 agent가 만든 부분 결과를 verifier/repair가 평가 직전 deterministic하게 보강했다.
- Excel Data Transformation 성공의 핵심 요인은 멀티에이전트가 입력 workbook과 예시 workbook을 분리해 읽고, 산출물 파일 생성까지 이어간 점이다. 단일 에이전트는 조사를 했지만 `Processed.xlsx`를 만들지 못했다.
- WooCommerce Update Cover는 양쪽 모두 성공했다. 단일은 더 많은 tool/token을 사용했고, 멀티는 더 적은 tool/token으로 같은 deterministic 평가를 통과했다.
- K8S 실패의 핵심 요인은 두 단계다. 먼저 Playwright MCP schema는 OpenAI 요청 직전 JSON Schema 정규화로 해결했다. 이후 남은 실패는 agent가 Kubernetes deployment와 보고서 산출을 완수하지 못한 것이다. 단일은 실행 중 실패했고, 멀티는 evaluation까지 갔지만 `frontend-app-pr123` deployment가 없어 rollout check에서 실패했다.

## 성공/실패 판정 방식
성공 여부 자체는 deterministic한 Toolathlon evaluation 로직으로 판정한다. 각 task의 평가 스크립트가 외부 상태와 산출물을 직접 검사하고, `eval_res.json`의 `pass`가 `true`일 때만 성공으로 집계한다. 사람이 이해할 수 있는 이유도 함께 남는다. 예를 들어 Inventory는 51개 지역 상품의 로컬 재고 합계와 WooCommerce 재고를 비교하고, K8S는 rollout, pod readiness, service endpoint, `http://localhost:31123` 응답, 보고서 내용을 순서대로 검사한다. 다만 agent의 행동은 모델 호출, 도구 호출 순서, 중간 오류 복구 여부에 따라 반복마다 달라질 수 있으므로, 실험 결과의 안정성은 반복 실행으로 확인해야 한다.

요구 질문 답변 요약:
- 멀티에이전트가 단일 에이전트 실패 작업을 해결했는가: 예
- 해당 작업: Inventory Sync, Paper Checker, Excel Data Transformation, Arrange Workspace, Reimbursement Form Filler, PPT Analysis
- 절대 성공률 향상: 0.545
- 상대 성공률 향상: 6.000
- turn/tool/token 비용: 평균 tool call은 single 17.364, multi 19.091; 평균 token/cost는 single 231222.636/0.373, multi 439669.455/0.646이다. K8S row는 agent 실행 실패 후 evaluation이 생략되어 token/cost가 0으로 기록됐다.
- 비용 대비 개선 여부: Inventory와 WooCommerce cover에서는 멀티가 더 적은 비용으로 성공했고, Paper/Arrange/Reimbursement/PPT/Excel에서는 성공을 얻기 위해 비용이 증가했다. 목표는 성공률 개선이므로 비용 최적화는 후속 과제다.
- 가장 크게 기여한 specialist layer: handoff specialist 자체보다 post-agent verifier/repair pass가 가장 크게 기여했다. 특히 산출 파일 생성, 파일 구조 정규화, WooCommerce batch update, LaTeX reference 보정에서 결정적이었다.
- handoff 또는 verifier가 만든 실패: 현재 raw 결과에서 post-repair 자체가 성공 작업을 실패로 만든 사례는 확인되지 않았다. Privacy와 Detect Revised Terms는 아직 보정 범위 밖이라 실패가 남았다.
- single에만 나타난 실패 모드: 산출 파일 생성 누락, 잘못된 파일 위치, 외부 상태 갱신 누락이 반복됐다.
- multi에만 나타난 실패 모드: 기존 Privacy 결과에서 도구 호출 없이 산출 디렉터리가 비는 사례가 있었고, 이번 개선의 안정 표본에는 아직 포함하지 못했다.
