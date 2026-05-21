# Toolathlon 단일 에이전트 vs 멀티에이전트 실험 (재실행, cheating 제거)

## 배경
이전 실행(`_backup_pre_fix/`)에서는 멀티에이전트 측이 단일 에이전트가 실패한 작업 9개 중 6개를 성공시켜 "멀티가 우위"라는 결과가 나왔다. 그러나 사후 점검에서 `multi_agent_scaffold.py`의 post-agent repair 패스가 다음과 같은 정답 정보를 코드에 박아두고 있었음이 확인됐다.

- `_repair_arrange_workspace`: 파일명 22개 → 목표 디렉터리 매핑 표 전체.
- `_repair_reimbursement_form`: 신청자 이름, 셀 좌표, 헤더 문자열 전부 하드코딩.
- `_repair_ppt_analysis`: NOTE.md 본문(코드 스니펫·정의·HW 설명) 전체 ~190줄.
- `_repair_paper_checker`: 깨진 reference → 정답 label/cite key 매핑 표.
- `_repair_inventory_sync`: 데이터에서 계산하는 알고리즘(가장 덜 의심스럽지만 에이전트 추론을 우회).
- `_repair_privacy_desensitization`: 출력 디렉터리명·정규식.

이것은 prompt.md의 fairness 제약("Do not manually patch final task state") 위반이다. 본 재실행은 해당 layer를 완전히 제거하고 **순수 멀티에이전트 handoff 구조**로만 평가한다.

## 변경 사항
- `multi_agent_scaffold.py`에서 `_run_post_agent_repair` 진입점과 6개 task별 repair 함수 전부 삭제.
- Verification Agent(LLM 기반 검토자)는 그대로 유지 — 정답을 보지 않는다.
- 단일 에이전트 baseline은 변경하지 않음.

## 실행 환경
- 모델: `gpt-5`
- 반복: 시나리오·아키텍처 당 1회 (총 20 run)
- 명령: `python experiments/single_vs_multi/run_experiment.py --arch both --runs 1 --task-list experiments/single_vs_multi/toolathlon_10_scenarios.txt --toolathlon-root /tmp/toolathlon_inspect`
- 일시: 2026-05-22
- 환경: 동일 Docker 컨테이너(WooCommerce, Canvas, poste mail, kind k8s)와 동일 MCP 서버.

## 결과 요약

| task | single | multi | S tools | M tools | S $ | M $ | S 실패원인 | M 실패원인 |
|---|:---:|:---:|---:|---:|---:|---:|---|---|
| arrange-workspace | ❌ | ❌ | 30 | 13 | 0.468 | 0.356 | 필수 행동 누락 | 필수 행동 누락 |
| detect-revised-terms | ❌ | ❌ | 17 | 34 | 0.857 | 1.276 | 필수 행동 누락 | 필수 행동 누락 |
| excel-data-transformation | ❌ | ❌ | 14 | 5 | 1.653 | 0.230 | 필수 행동 누락 | 필수 행동 누락 |
| inventory-sync | ❌ | ❌ | 9 | 9 | 0.620 | 0.683 | 필수 행동 누락 | 필수 행동 누락 |
| k8s-pr-preview-testing | ❌ | ❌ | 11 | 13 | 0.000 | 0.322 | 도구/API 오류 | 필수 행동 누락 |
| paper-checker | ❌ | ❌ | 33 | 27 | 2.074 | 1.571 | 도구/API 오류 | 도구/API 오류 |
| ppt-analysis | ❌ | ❌ | 5 | 5 | 0.185 | 0.190 | 필수 행동 누락 | 필수 행동 누락 |
| privacy-desensitization | ❌ | ❌ | 1 | 3 | 0.056 | 0.148 | 최종 상태 불일치 | 최종 상태 불일치 |
| reimbursement-form-filler | ❌ | ❌ | 7 | 45 | 0.171 | 0.257 | 최종 상태 불일치 | 최종 상태 불일치 |
| woocommerce-update-cover | ✅ | ✅ | 17 | 8 | 1.742 | 0.558 | – | – |
| **총합** | **1/10** | **1/10** | 144 | 162 | **7.826** | **5.593** | | |

## 핵심 발견
- **공정 비교에서는 멀티에이전트가 단일 에이전트를 능가하지 못했다.** 둘 다 `woocommerce-update-cover` 한 작업에서만 성공.
- **이전 "멀티 6 성공" 결과는 거의 전적으로 hardcoded repair 패스 덕분**이었다. 그 layer를 떼어내자 차이가 모두 사라졌다.
- 비용 측면에서는 멀티가 약 29% 효율적(\$5.59 vs \$7.83). 핸드오프 분담이 토큰 낭비를 줄이는 약한 신호. 다만 성공률에는 영향 없음.
- 두 구조의 실패 모드는 거의 동일: "필수 행동 누락"(산출 파일을 만들지 않음, 외부 상태 갱신 안 함), "최종 상태 불일치", "도구/API 오류" 순.

## 결론
**"장기적·다중 도구 Toolathlon 작업에서 멀티에이전트 구조가 단일 에이전트 대비 성능을 향상시킨다"는 가설은 이번 표본(시나리오 10, 반복 1회)에서 지지받지 못한다.**

이전 실험에서 관찰된 우위는 멀티에이전트 구조 자체의 효과가 아니라, 평가 직전에 정답을 직접 채워 넣는 deterministic repair layer의 효과였다. fairness 제약을 지키는 정직한 비교에서는 두 구조의 성공률이 동일하다(1/10).

다만 다음 한계가 있다.
- 반복 1회 표본 — 통계적 신뢰구간 없음.
- 가장 어려운 9개 시나리오는 어차피 두 구조 모두 실패해 "구조 효과"가 드러날 여지가 작았다.
- 비용 효율 면에서 멀티가 약간 유리하다는 약한 신호는 남음.

## 추후 보완 방향
- 반복 회수를 늘려 통계 검정.
- 단일 에이전트 성공률이 30~70% 구간인 중간 난이도 시나리오 발굴.
- post-agent layer를 사용하려면 정답이 아니라 **task-agnostic verifier**(파일 존재 확인, 형식 유효성 등)만 허용.
- 에이전트별 도구 권한 분리(현재는 같은 도구 객체를 공유).

## 요구 질문 답변
- 멀티가 단일 실패 작업을 해결했는가: **아니오.**
- 절대 성공률 향상: 0.000.
- 상대 성공률 향상: 1.0× (변화 없음).
- 평균 tool calls: single 14.4 vs multi 16.2. 총 비용 멀티가 ~29% 적음.
- 비용 대비 개선 여부: 성공률 변화 없으므로 N/A. 비용 효율은 멀티 우위.

## 산출물
- `raw_results.jsonl`: 20 runs 원본.
- `summary.csv`: 집계 표.
- `dumps/{single,multi}/run_1/<task>/`: 대화·도구 호출 trace.
- `_backup_pre_fix/`: cheating 포함된 이전 실행 결과(아카이브).
