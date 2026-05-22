# Toolathlon dynamic supervisor 멀티에이전트 실험

## 목적
멀티에이전트의 우위를 주장하려면 기본 단일 baseline이 아니라 같은 절차적 도움을 받은 강화 단일 에이전트와 비교해야 한다. 이 문서는 `single_baseline`, `single_strong_workflow`, `multi_workflow`, `multi_dynamic_supervisor`를 분리해 기록한다.

## 아키텍처
- `single_baseline`: Toolathlon 기본 `TaskAgent`를 그대로 사용한다. 참고용이며 강한 주장에는 사용하지 않는다.
- `single_strong_workflow`: 하나의 agent/context가 Research → Plan → Execute → Self-Verify → Retry → Finalize 절차, checklist, verifier rubric, retry 지시를 모두 수행한다.
- `multi_workflow`: 같은 절차와 금지사항을 역할별 agent, 분리된 context, 독립 Verification Agent, orchestrator-only `claim_done` 권한으로 수행한다.
- `multi_dynamic_supervisor`: Orchestrator가 중앙 통제권을 유지하며 공개 task metadata와 도구 요구사항으로 선택된 specialist agent를 tool처럼 자율 호출한다.

## 공정성 제약
- task-specific 지시는 원래 Toolathlon task input과 task_config에서만 온다.
- task별 hardcoded repair, groundtruth/evaluation/answer dump 접근, 평가 직전 deterministic final-state patch는 금지한다.
- 멀티만 갖는 차이는 역할별 system prompt, context 분리, 독립 verifier, `claim_done` 권한 분리, 역할별 도구 surface 축소로 제한한다.

## 실행
- model: `gpt-5`
- row count: `31`
- row 구성: 기존 fair workflow 비교 row 30개 + 새 dynamic supervisor row 1개.
- command used: `dynamic supervisor full 10-task run attempted; stopped after repeated Inventory Sync timeout from over-delegation/nontermination`
- date/time: 2026-05-22T10:49:29
- deviations or failures: 결과 파일 기준 run id 1개가 기록됨. 환경 의존 실패는 agent 성능 실패와 분리해서 해석해야 함.
- primary comparison target: 강화 단일 실패 task 9개 중 workflow 성공 1개, dynamic supervisor 성공 0개

## 핵심 발견
- `single_strong_workflow` 대비 `multi_workflow` 추가 성공은 1개 task이다: Inventory Sync.
- `single_strong_workflow` 대비 `multi_dynamic_supervisor` 추가 성공은 0개 task이다: 없음.
- `multi_workflow` 실패를 `multi_dynamic_supervisor`가 통과한 task는 0개다: 없음.
- 10개 전체 dynamic 실행을 시도했지만 첫 task인 Inventory Sync가 두 차례 900초 timeout을 냈다. 실행 중 WooCommerce batch/update 호출은 발생했으나 Orchestrator가 완료 판단으로 수렴하지 못해 전체 반복은 중단했다.
- 따라서 이번 dynamic supervisor 결과는 성공률 비교라기보다 **자율 specialist delegation의 nontermination/over-delegation 실패 사례**로 해석해야 한다.
- `single_baseline`만 성공하고 strong/multi가 실패한 task는 1개다: Excel Data Transformation.
- K8S PR Preview Testing은 Kubernetes MCP namespace handling 문제(`default` vs `pr-preview-123`)가 반복되어 agent 성능 실패 근거로 쓰기 어렵다.
- 표본은 architecture별 task당 1회이므로 성공률 차이는 관찰값이며 통계적 결론은 아니다. 강한 주장은 3회 이상 반복 후에도 같은 패턴이 유지될 때만 가능하다.

## 결과
| task | single_baseline | single_strong_workflow | multi_workflow | multi_dynamic_supervisor | strong→dynamic delta | workflow→dynamic delta | strong audit pass |
|---|---:|---:|---:|---:|---:|---:|---:|
| Inventory Sync | 0 / 1 | 0 / 1 | 1 / 1 | 0 / 1 | 0.000 | -1.000 | 0.000 |
| K8S PR Preview Testing | 0 / 1 | 0 / 1 | 0 / 1 | 0 / 0 | 0.000 | 0.000 | 0.000 |
| Paper Checker | 0 / 1 | 0 / 1 | 0 / 1 | 0 / 0 | 0.000 | 0.000 | 1.000 |
| Privacy Desensitization | 0 / 1 | 0 / 1 | 0 / 1 | 0 / 0 | 0.000 | 0.000 | 1.000 |
| Excel Data Transformation | 1 / 1 | 0 / 1 | 0 / 1 | 0 / 0 | 0.000 | 0.000 | 0.000 |
| Arrange Workspace | 0 / 1 | 0 / 1 | 0 / 1 | 0 / 0 | 0.000 | 0.000 | 0.000 |
| Reimbursement Form Filler | 0 / 1 | 0 / 1 | 0 / 1 | 0 / 0 | 0.000 | 0.000 | 1.000 |
| Detect Revised Terms | 0 / 1 | 0 / 1 | 0 / 1 | 0 / 0 | 0.000 | 0.000 | 0.000 |
| PPT Analysis | 0 / 1 | 0 / 1 | 0 / 1 | 0 / 0 | 0.000 | 0.000 | 0.000 |
| WooCommerce Update Cover | 1 / 1 | 1 / 1 | 1 / 1 | 0 / 0 | -1.000 | -1.000 | 1.000 |

## 사례 메모
- Inventory Sync: baseline과 strong single은 WooCommerce 재고를 갱신하지 못해 0/51로 실패했다. multi는 WooCommerce `products/batch` update를 호출했고 evaluation에서 51/51, 100%로 통과했다. 다만 strong single의 절차 audit은 실패로 분류되어, '충실히 수행한 단일 agent를 독립 verifier가 구조적으로 이겼다'는 가장 강한 사례는 아니다.
- Excel Data Transformation은 baseline만 통과했다. workflow 지시가 항상 성능을 올린다는 근거는 아니며, task별 분산과 모델 비결정성이 크다는 신호다.

## 비용 및 호출 지표
| architecture | rows | pass rate | adequacy pass rate | premature claim rate | missing action rate | avg tools | avg tokens | avg cost |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| single_baseline | 10 | 0.200 | 0.100 | 0.100 | 0.300 | 19.8 | 355104.9 | 0.538 |
| single_strong_workflow | 10 | 0.100 | 0.400 | 0.000 | 0.500 | 15.1 | 280786.5 | 0.453 |
| multi_workflow | 10 | 0.200 | n/a | 0.000 | 0.500 | 25.2 | 495866.6 | 0.765 |
| multi_dynamic_supervisor | 1 | 0.000 | n/a | 0.000 | 0.000 | 0.0 | 0.0 | 0.0 |

## Dynamic Supervisor Specialist 호출
각 dynamic run은 workspace의 `profile_selection.json`에 공개 task metadata와 도구 요구사항을 근거로 선택된 specialist roster를 남긴다.

| task | selected specialists | profile artifact |
|---|---|---|
| Inventory Sync | ecommerce, file_terminal, memory, planner, research, verifier | `/home/primi/workspace/multi-agent-vs-single-agent/experiments/single_vs_multi/results/dumps_dynamic_supervisor/multi_dynamic_supervisor/run_1/finalpool__inventory-sync/workspace/profile_selection.json` |

## 단일 에이전트가 최선을 다했는가
절차 audit은 Toolathlon 성공 판정과 분리된 보조 지표다. trace에서 요구사항 확인, 도구/상태 점검, 명시적 계획 또는 checklist, 실제 상태 변경 시도, 산출물/외부 상태 검증, premature `claim_done` 여부를 휴리스틱으로 본다.
기존 raw row에 `workflow_audit` 필드가 없으면 `n/a`로 표시한다. 새 workflow 실행부터 audit이 row에 기록된다.

| attribution | count |
|---|---:|
| agent_process_failure | 6 |
| context_or_verification_failure | 10 |
| environment_or_tool_failure | 3 |
| weak_prompt_or_baseline_gap | 7 |

## 해석
이번 구현의 dynamic supervisor는 구조적으로 정당하지만, 현재 prompt/tool-budget 설정에서는 성능 향상을 보이지 못했다. 핵심 실패는 specialist가 실제 mutation까지 수행한 뒤에도 evidence를 Orchestrator의 완료 판단으로 압축하지 못하고, 같은 종류의 조사/수정/검증을 반복한 점이다.

즉 현재 결과는 "자율 Orchestrator가 specialist를 쓰면 더 낫다"는 근거가 아니라, **자율 delegation만으로는 종료 조건과 evidence contract가 약해질 수 있다**는 반례다. 다음 개선은 정답 repair가 아니라 일반적인 supervisor 안정화, 예를 들어 specialist output schema 강제, mutation 후 verifier로 자동 전환, 동일 mutation 반복 차단, per-task 전체 wall-clock budget 관리가 되어야 한다.

## 산출물
- `raw_results_dynamic_supervisor.jsonl`: run별 원본 row와 workflow audit.
- `summary_dynamic_supervisor.csv`: architecture별 success, audit, premature claim, missing action, 비용 집계.
- `analysis_dynamic_supervisor.md`: 이 분석 문서.
- `dumps_dynamic_supervisor/`: Toolathlon 원본 trace, workspace, `eval_res.json` 로컬 dump. 크기 때문에 git에는 넣지 않는다.
