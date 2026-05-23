# Toolathlon dynamic supervisor 멀티에이전트 실험

## 목적
멀티에이전트의 우위를 주장하려면 기본 단일 baseline이 아니라 같은 절차적 도움을 받은 강화 단일 에이전트와 비교해야 한다. 이 문서는 `single_baseline`, `single_strong_workflow`, `multi_workflow`, dynamic supervisor 계열을 분리해 기록한다.

## 아키텍처
- `single_baseline`: Toolathlon 기본 `TaskAgent`를 그대로 사용한다. 참고용이며 강한 주장에는 사용하지 않는다.
- `single_strong_workflow`: 하나의 agent/context가 Research → Plan → Execute → Self-Verify → Retry → Finalize 절차, checklist, verifier rubric, retry 지시를 모두 수행한다.
- `multi_workflow`: 같은 절차와 금지사항을 역할별 agent, 분리된 context, 독립 Verification Agent, orchestrator-only `claim_done` 권한으로 수행한다.
- `multi_dynamic_supervisor`: Orchestrator가 중앙 통제권을 유지하며 공개 task metadata와 도구 요구사항으로 선택된 specialist agent를 tool처럼 자율 호출한다.
- `multi_dynamic_supervisor_v2`: dynamic supervisor에 verifier-gated termination, duplicate delegation guard, specialist status contract, timeout 후 verifier 전환 지시를 추가한다.

## 공정성 제약
- task-specific 지시는 원래 Toolathlon task input과 task_config에서만 온다.
- task별 hardcoded repair, groundtruth/evaluation/answer dump 접근, 평가 직전 deterministic final-state patch는 금지한다.
- 멀티만 갖는 차이는 역할별 system prompt, context 분리, 독립 verifier, `claim_done` 권한 분리, 역할별 도구 surface 축소로 제한한다.

## 실행
- model: `gpt-5`
- row count: `40`
- command used: `/home/primi/workspace/multi-agent-vs-single-agent/experiments/single_vs_multi/run_experiment.py --toolathlon-root /tmp/toolathlon_inspect --task-list /home/primi/workspace/multi-agent-vs-single-agent/experiments/single_vs_multi/toolathlon_10_scenarios.txt --arch dynamic_v2 --runs 1 --raw-results-path /home/primi/workspace/multi-agent-vs-single-agent/experiments/single_vs_multi/results/raw_results_dynamic_v2_10_20260523_1156.jsonl --summary-path /home/primi/workspace/multi-agent-vs-single-agent/experiments/single_vs_multi/results/summary_dynamic_v2_10_20260523_1156.csv --analysis-path /home/primi/workspace/multi-agent-vs-single-agent/experiments/single_vs_multi/results/analysis_dynamic_v2_10_20260523_1156.md --dump-path /home/primi/workspace/multi-agent-vs-single-agent/experiments/single_vs_multi/results/dumps_dynamic_v2_10_20260523_1156 --comparison-results-path /home/primi/workspace/multi-agent-vs-single-agent/experiments/single_vs_multi/results/raw_results_fair_workflow.jsonl`
- date/time: 2026-05-23T13:02:23
- deviations or failures: 결과 파일 기준 run id 1개가 기록됨. 환경 의존 실패는 agent 성능 실패와 분리해서 해석해야 함.
- primary comparison target: 강화 단일 실패 task 9개 중 workflow 성공 1개, 멀티 dynamic supervisor v2 성공 0개

## 핵심 발견
- `single_strong_workflow` 대비 `multi_workflow` 추가 성공은 1개 task이다: Inventory Sync.
- `single_strong_workflow` 대비 `multi_dynamic_supervisor_v2` 추가 성공은 0개 task이다: 없음.
- `multi_workflow` 실패를 `multi_dynamic_supervisor_v2`가 통과한 task는 0개다: 없음.
- 최신 strict-gated v2 구현은 Inventory Sync의 실제 WooCommerce 갱신 경로까지는 도달했지만, verifier PASS를 얻고 gated `claim_done`까지 완료하지 못해 제한 시간에서 실패했다.
- 이전 v2 prototype run은 WooCommerce 평가를 통과한 적이 있으나, 그 시점에는 `claim_done` gate가 도구 출력까지 차단하지 못했다. 따라서 최신 공정 기준에서는 성공으로 세지 않는다.
- `single_baseline`만 성공하고 strong/multi가 실패한 task는 1개다: Excel Data Transformation.
- K8S PR Preview Testing은 Kubernetes MCP namespace handling 문제(`default` vs `pr-preview-123`)가 반복되어 agent 성능 실패 근거로 쓰기 어렵다.
- 표본은 architecture별 task당 1회이므로 성공률 차이는 관찰값이며 통계적 결론은 아니다. 강한 주장은 3회 이상 반복 후에도 같은 패턴이 유지될 때만 가능하다.

## 결과
| task | single_baseline | single_strong_workflow | multi_workflow | multi_dynamic_supervisor_v2 | strong→dynamic delta | workflow→dynamic delta | strong audit pass |
|---|---:|---:|---:|---:|---:|---:|---:|
| Inventory Sync | 0 / 1 | 0 / 1 | 1 / 1 | 0 / 1 | 0.000 | -1.000 | 0.000 |
| K8S PR Preview Testing | 0 / 1 | 0 / 1 | 0 / 1 | 0 / 1 | 0.000 | 0.000 | 0.000 |
| Paper Checker | 0 / 1 | 0 / 1 | 0 / 1 | 0 / 1 | 0.000 | 0.000 | 1.000 |
| Privacy Desensitization | 0 / 1 | 0 / 1 | 0 / 1 | 0 / 1 | 0.000 | 0.000 | 1.000 |
| Excel Data Transformation | 1 / 1 | 0 / 1 | 0 / 1 | 0 / 1 | 0.000 | 0.000 | 0.000 |
| Arrange Workspace | 0 / 1 | 0 / 1 | 0 / 1 | 0 / 1 | 0.000 | 0.000 | 0.000 |
| Reimbursement Form Filler | 0 / 1 | 0 / 1 | 0 / 1 | 0 / 1 | 0.000 | 0.000 | 1.000 |
| Detect Revised Terms | 0 / 1 | 0 / 1 | 0 / 1 | 0 / 1 | 0.000 | 0.000 | 0.000 |
| PPT Analysis | 0 / 1 | 0 / 1 | 0 / 1 | 0 / 1 | 0.000 | 0.000 | 0.000 |
| WooCommerce Update Cover | 1 / 1 | 1 / 1 | 1 / 1 | 1 / 1 | 0.000 | 0.000 | 1.000 |

## 사례 메모
- Inventory Sync: baseline과 strong single은 WooCommerce 재고를 갱신하지 못해 0/51로 실패했다. multi는 WooCommerce `products/batch` update를 호출했고 evaluation에서 51/51, 100%로 통과했다. 다만 strong single의 절차 audit은 실패로 분류되어, '충실히 수행한 단일 agent를 독립 verifier가 구조적으로 이겼다'는 가장 강한 사례는 아니다.
- Dynamic Supervisor v2 / Inventory Sync: root orchestrator가 직접 WooCommerce 조회와 batch update를 수행하는 경로는 만들어졌지만, specialist verifier가 전수 확인을 반복하거나 timeout되면서 종료 조건을 만족하지 못했다. 이 실패는 정답 접근 문제가 아니라 control-plane 수렴성 문제다.
- v2에서 새로 확인한 구조적 병목은 세 가지다: specialist timeout 이후 verifier fallback도 timeout될 수 있음, verifier가 전수 검증을 과하게 반복함, `claim_done`은 termination checker만이 아니라 도구 자체도 gate해야 함.
- Excel Data Transformation은 baseline만 통과했다. workflow 지시가 항상 성능을 올린다는 근거는 아니며, task별 분산과 모델 비결정성이 크다는 신호다.

## 비용 및 호출 지표
| architecture | rows | pass rate | adequacy pass rate | premature claim rate | missing action rate | avg tools | avg tokens | avg cost |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| single_baseline | 10 | 0.200 | 0.100 | 0.100 | 0.300 | 19.8 | 355104.9 | 0.538 |
| single_strong_workflow | 10 | 0.100 | 0.400 | 0.000 | 0.500 | 15.1 | 280786.5 | 0.453 |
| multi_workflow | 10 | 0.200 | n/a | 0.000 | 0.500 | 25.2 | 495866.6 | 0.765 |
| multi_dynamic_supervisor_v2 | 10 | 0.100 | n/a | 0.000 | 0.300 | 10.6 | 545356.7 | 0.799 |

## Dynamic Supervisor Specialist 호출
각 dynamic run은 workspace의 `profile_selection.json`에 공개 task metadata와 도구 요구사항을 근거로 선택된 specialist roster를 남긴다.

| task | architecture | selected specialists | verifier pass | claim_done blocked | profile artifact |
|---|---|---|---:|---:|---|
| Inventory Sync | multi_dynamic_supervisor_v2 | ecommerce, file_terminal, memory, planner, research, verifier | 0 | 0 | `/home/primi/workspace/multi-agent-vs-single-agent/experiments/single_vs_multi/results/dumps_dynamic_v2_10_20260523_1156/multi_dynamic_supervisor_v2/run_1/finalpool__inventory-sync/workspace/profile_selection.json` |
| K8S PR Preview Testing | multi_dynamic_supervisor_v2 | file_terminal, k8s_browser, memory, planner, research, verifier | 0 | 0 | `/home/primi/workspace/multi-agent-vs-single-agent/experiments/single_vs_multi/results/dumps_dynamic_v2_10_20260523_1156/multi_dynamic_supervisor_v2/run_1/finalpool__k8s-pr-preview-testing/workspace/profile_selection.json` |
| Paper Checker | multi_dynamic_supervisor_v2 | academic_reference, file_terminal, memory, planner, research, verifier | 0 | 0 | `/home/primi/workspace/multi-agent-vs-single-agent/experiments/single_vs_multi/results/dumps_dynamic_v2_10_20260523_1156/multi_dynamic_supervisor_v2/run_1/finalpool__paper-checker/workspace/profile_selection.json` |
| Privacy Desensitization | multi_dynamic_supervisor_v2 | file_terminal, memory, planner, privacy, research, verifier | 0 | 0 | `/home/primi/workspace/multi-agent-vs-single-agent/experiments/single_vs_multi/results/dumps_dynamic_v2_10_20260523_1156/multi_dynamic_supervisor_v2/run_1/finalpool__privacy-desensitization/workspace/profile_selection.json` |
| Excel Data Transformation | multi_dynamic_supervisor_v2 | n/a | 0 | 0 | `n/a` |
| Arrange Workspace | multi_dynamic_supervisor_v2 | n/a | 0 | 0 | `n/a` |
| Reimbursement Form Filler | multi_dynamic_supervisor_v2 | n/a | 0 | 0 | `n/a` |
| Detect Revised Terms | multi_dynamic_supervisor_v2 | document, file_terminal, memory, planner, research, verifier | 0 | 0 | `/home/primi/workspace/multi-agent-vs-single-agent/experiments/single_vs_multi/results/dumps_dynamic_v2_10_20260523_1156/multi_dynamic_supervisor_v2/run_1/finalpool__detect-revised-terms/workspace/profile_selection.json` |
| PPT Analysis | multi_dynamic_supervisor_v2 | document, file_terminal, memory, planner, research, verifier | 0 | 0 | `/home/primi/workspace/multi-agent-vs-single-agent/experiments/single_vs_multi/results/dumps_dynamic_v2_10_20260523_1156/multi_dynamic_supervisor_v2/run_1/finalpool__ppt-analysis/workspace/profile_selection.json` |
| WooCommerce Update Cover | multi_dynamic_supervisor_v2 | ecommerce, memory, planner, research, verifier | 1 | 0 | `/home/primi/workspace/multi-agent-vs-single-agent/experiments/single_vs_multi/results/dumps_dynamic_v2_10_20260523_1156/multi_dynamic_supervisor_v2/run_1/finalpool__woocommerce-update-cover/workspace/profile_selection.json` |

## 단일 에이전트가 최선을 다했는가
절차 audit은 Toolathlon 성공 판정과 분리된 보조 지표다. trace에서 요구사항 확인, 도구/상태 점검, 명시적 계획 또는 checklist, 실제 상태 변경 시도, 산출물/외부 상태 검증, premature `claim_done` 여부를 휴리스틱으로 본다.
기존 raw row에 `workflow_audit` 필드가 없으면 `n/a`로 표시한다. 새 workflow 실행부터 audit이 row에 기록된다.

| attribution | count |
|---|---:|
| agent_process_failure | 5 |
| context_or_verification_failure | 14 |
| environment_or_tool_failure | 8 |
| weak_prompt_or_baseline_gap | 7 |

## 해석
멀티 workflow가 강화 단일 workflow보다 높은 pass rate를 보였다. 강한 주장은 절차 audit을 통과한 단일 실패를 멀티가 독립 verifier/retry로 복구한 trace가 있을 때만 유지한다.

## v2 실패 원인
- 핵심 실패 요인은 문제 해결 능력 자체보다 supervisor control loop의 수렴 실패다. trace상 source DB 집계, WooCommerce product 조회, `products/batch` update, SKU별 readback까지 진행했지만 verifier PASS가 상태 파일에 기록되기 전 timeout됐다.
- 엄격한 verifier gate를 추가하자 이전 prototype의 '평가 pass 후 claim' 경로가 막혔다. 이는 공정성 관점에서는 맞는 수정이지만, verifier가 너무 무거워져 성공률은 떨어졌다.
- 현재 v2는 agent-as-tool 방식과 root direct-tool 방식을 섞은 hybrid supervisor다. 이 구조는 loop 탈출에는 도움이 되지만, verifier와 specialist가 같은 데이터를 반복 확인하면 비용과 시간이 급증한다.
- 다음 개선은 task 정답을 보는 repair가 아니라 generic control 개선이어야 한다: verifier 입력을 `source_evidence.json`과 `target_readback.json` 같은 compact artifact로 제한하고, verifier는 추가 도구 호출 없이 artifact consistency부터 판정하게 해야 한다. 필요할 때만 작은 샘플 API 조회를 허용하는 방식이 더 적절하다.

## 산출물
- `raw_results_dynamic_v2_10_20260523_1156.jsonl`: run별 원본 row와 workflow audit.
- `summary_dynamic_v2_10_20260523_1156.csv`: architecture별 success, audit, premature claim, missing action, 비용 집계.
- `analysis_dynamic_v2_10_20260523_1156.md`: 이 분석 문서.
- `dumps_dynamic_v2_10_20260523_1156/`: Toolathlon 원본 trace, workspace, `eval_res.json` 로컬 dump. 크기 때문에 git에는 넣지 않는다.
