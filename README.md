# multi-agent-vs-single-agent

이 레포는 Toolathlon 장기 tool-use 작업에서 강한 단일 에이전트와 일반 목적 멀티에이전트 구조를 같은 조건으로 비교하기 위한 실험 래퍼다.

핵심 목적은 “여러 도구와 긴 절차가 필요한 작업에서 역할 분리와 handoff가 단일 에이전트의 실패를 줄일 수 있는가”를 관찰하는 것이다. 레포 안에는 공식 Toolathlon 전체 코드가 들어 있지 않으며, 별도의 Toolathlon checkout을 실행 대상으로 삼는다.

## 무엇을 비교하는가

단일 에이전트 baseline은 Toolathlon 기본 TaskAgent를 그대로 사용한다. 하나의 agent가 계획, 조사, 실행, 검증, 완료 선언을 모두 맡는다.

멀티에이전트 구조는 같은 task 입력과 같은 도구 조건을 유지하면서 Orchestrator, Research/Inspection, Planning, Action/Execution, Verification, Memory/Summary 역할로 나눈다. 추가로 Orchestrator가 task 유형에 맞는 specialist agent를 도구처럼 호출하는 dynamic supervisor 구조도 실험한다. 이전에는 실행 후 평가 전에 task별 post-agent repair pass를 추가했지만, 정답에 해당하는 보정 로직이 들어가 공정성 제약을 위반했으므로 제거했다.

## 이 레포가 맡는 역할

- 실험 대상 task 목록을 관리한다.
- 단일/멀티에이전트 실행을 같은 형식으로 호출한다.
- 각 run의 성공 여부, 도구 호출 수, token 사용량, 비용 추정치를 구조화한다.
- Toolathlon 평가 결과를 한국어 분석 문서와 CSV 요약으로 변환한다.
- 재실행 시 같은 시행착오를 반복하지 않도록 환경 준비 메모와 실패 원인을 남긴다.

## 결과 파일

결과 내용은 아래 파일에서 확인한다.

- [분석 문서](experiments/single_vs_multi/results/analysis.md)
- [Dynamic supervisor 분석 문서](experiments/single_vs_multi/results/analysis_dynamic_supervisor.md)
- [요약 CSV](experiments/single_vs_multi/results/summary.csv)
- [Dynamic supervisor 요약 CSV](experiments/single_vs_multi/results/summary_dynamic_supervisor.csv)
- [원시 JSONL](experiments/single_vs_multi/results/raw_results.jsonl)
- [Dynamic supervisor 원시 JSONL](experiments/single_vs_multi/results/raw_results_dynamic_supervisor.jsonl)
- [정정 인계 문서](HANDOFF_TO_CODEX.md)

## 문서 구조

- [실험 README](experiments/single_vs_multi/README.md): 실험 구조와 실행 방법
- [10개 시나리오 계획](experiments/single_vs_multi/scenario_plan.md): 추가 실험 후보와 제외 기준
- [재현성 지침](AGENTS.md): 환경 준비, 실패 지점, 재실행 주의사항
