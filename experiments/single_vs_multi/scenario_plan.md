# 추가 Toolathlon 시나리오 계획

## 목표

총 10개 시나리오에서 단일 에이전트 baseline과 멀티에이전트 구조를 같은 모델, 같은 Toolathlon task_config, 같은 evaluation 기준으로 비교한다.

현재 실행 완료된 시나리오는 `inventory-sync`, `k8s-pr-preview-testing` 두 개다. 나머지 8개는 Snowflake처럼 외부 계정이 필요한 작업을 제외하고 즉시 실행 가능성이 높은 로컬 파일, 로컬 서비스, WooCommerce, 문서 처리 중심 작업에서 고른다.

## 제외 기준

다음 MCP 서버가 핵심 요구사항인 작업은 이번 10개 시나리오에서 제외한다.

- `snowflake`
- `notion`, `notion_official`
- `google-cloud`, `google_sheet`, `google_calendar`, `google_forms`, `google_map`
- `huggingface`, `wandb`
- 외부 쓰기 권한이 필요한 `github`
- 계정 또는 실시간 외부 서비스 변동성이 큰 서비스

## 10개 시나리오

| 번호 | task_id | 주 도메인 | 선택 이유 | 주요 리스크 |
|---:|---|---|---|---|
| 1 | `finalpool/inventory-sync` | WooCommerce + SQLite | 단일 실패/멀티 성공 사례가 이미 확인된 핵심 비교축 | WooCommerce 컨테이너와 상품 초기화 상태 |
| 2 | `finalpool/k8s-pr-preview-testing` | Kubernetes + Git + 테스트 보고서 | K8S 배포/검증/보고서 작성이 결합된 장기 작업 | kind cluster, namespace, localhost 포트 |
| 3 | `finalpool/paper-checker` | filesystem + terminal | 외부 계정 없이 파일 검사와 로컬 실행 중심 | 평가 스크립트가 기대하는 파일 구조 |
| 4 | `finalpool/privacy-desensitization` | filesystem + terminal | 상태 변경과 검증이 명확한 로컬 데이터 처리 작업 | 민감정보 치환 누락 |
| 5 | `finalpool/excel-data-transformation` | Excel + filesystem + terminal | 구조화 데이터 변환과 산출물 검증을 비교하기 좋음 | Excel MCP/라이브러리 의존성 |
| 6 | `finalpool/arrange-workspace` | filesystem + PDF + Excel | 여러 파일 유형을 읽고 workspace를 정리하는 장기 작업 | 파일 이동/이름 규칙 누락 |
| 7 | `finalpool/reimbursement-form-filler` | PDF + Excel + filesystem | 문서 추출과 양식 작성이 결합된 작업 | PDF field 처리와 형식 보존 |
| 8 | `finalpool/detect-revised-terms` | PDF + filesystem | 문서 비교 기반 deterministic 평가가 가능함 | 약관 변경점 누락 |
| 9 | `finalpool/ppt-analysis` | PPTX + PDF + filesystem | 프레젠테이션 분석/요약 산출물 비교에 적합 | PPTX MCP 변환 품질 |
| 10 | `finalpool/woocommerce-update-cover` | WooCommerce | 로컬 WooCommerce 서비스 기반으로 외부 계정 없이 실행 가능 | 이미지/상품 메타 업데이트 여부 |

## K8S 처리 방침

K8S 시나리오는 우선 포함한다. 다만 다음 조건 중 하나가 반복되면 K8S를 스킵하고 대체 시나리오를 넣는다.

- kind cluster 생성 실패
- `localhost:31123` 포트 바인딩 실패
- Kubernetes MCP가 클러스터에 연결하지 못함
- namespace/context 보정 후에도 agent loop 전에 runner가 실패함

대체 후보는 `finalpool/courses-ta-hws`, `finalpool/interview-report`, `finalpool/sales-accounting` 순서로 사용한다.

## 실행 후 메모

이번 실행에서는 K8S를 포함해 실제로 돌렸고, schema 호환성 문제는 해결됐지만 Kubernetes MCP가 여전히 `kubectl apply ... -n default`를 사용하는 흐름이 남아 실패했다. 추가 보정 후 run 2도 수행했으나 YAML parse error 또는 namespace mismatch가 재발했다.

따라서 다음 반복 실험에서는 K8S를 안정 표본에서 제외하고, 아래 9개 즉시 실행 가능 시나리오를 우선 사용한다.

1. `finalpool/inventory-sync`
2. `finalpool/paper-checker`
3. `finalpool/privacy-desensitization`
4. `finalpool/excel-data-transformation`
5. `finalpool/arrange-workspace`
6. `finalpool/reimbursement-form-filler`
7. `finalpool/detect-revised-terms`
8. `finalpool/ppt-analysis`
9. `finalpool/woocommerce-update-cover`

## 실행 명령

```bash
cd /tmp/toolathlon_inspect
OPENAI_API_KEY="${OPENAI_API_KEY:-}" RUN_TIMEOUT_SECONDS=1800 \
uv run /home/primi/workspace/multi-agent-vs-single-agent/experiments/single_vs_multi/run_experiment.py \
  --toolathlon-root /tmp/toolathlon_inspect \
  --task-list /home/primi/workspace/multi-agent-vs-single-agent/experiments/single_vs_multi/toolathlon_10_scenarios.txt \
  --runs 1 \
  --skip-existing
```

`--skip-existing`는 이미 기록된 `inventory-sync`, `k8s-pr-preview-testing` row를 다시 쓰지 않고 빠진 시나리오만 채우기 위한 옵션이다.
