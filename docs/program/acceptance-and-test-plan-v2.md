# be-lazy Acceptance and Test Plan v2

## 1. 목적

완료 여부는 변경량이 아니라 **source 불변성, 신규 저장소 독립성, 동작 동등성, 보안·권한 규칙이 실제로 강제되는지**로 판단한다.

## 2. 신규 저장소 bootstrap 테스트

### BOOT-001 — Source/target separation

- source와 target canonical path가 다르다.
- target 안에 source `.git`가 없다.
- target remote가 source remote에서 자동 상속되지 않는다.
- source Git HEAD와 porcelain status 또는 archive byte length/SHA-256이 작업 전후 동일하다.
- source 내부에 새 cache, coverage, build, log, virtualenv가 생성되지 않는다.

### BOOT-002 — Controlled import

- 모든 imported source path가 `import-map.csv`에 한 행을 가진다.
- 각 행은 source digest, target path, disposition, rationale, verification 결과를 포함한다.
- `.git`, credential, secret, cache, build, dist, egg-info, virtualenv, runtime media가 target에 없다.
- `PORT_WITH_FIX`가 아닌 imported file은 recorded source digest와 exact-byte 일치한다.
- unclassified path가 있으면 bootstrap이 fail closed한다.

### BOOT-003 — Provenance

- `source-baseline.json`이 source commit 또는 archive digest, inventory digest, license, import tool, target bootstrap commit을 포함한다.
- source license와 notice가 보존된다.
- baseline test report가 source와 target 환경·명령·pass/fail/skip 차이를 구분한다.

### BOOT-004 — Target identity and compatibility

- target README H1과 repository-facing 문서는 `be-lazy`를 사용한다.
- 초기 parity phase에서 `video-production-core`, `video_factory`, `video-factory`, 기존 schema IDs와 artifact versions는 호환성 계약으로 분류된다.
- 기존 hosted source repository를 rename하거나 수정하지 않는다.
- 새 remote 생성·push·merge는 coding test 범위에 포함하지 않는다.

## 3. 공통 검증

지원 환경인 Python 3.12에서 최소 다음을 실행한다. 실제 repository에 존재하는 명령으로 조정하고 ExecPlan에 기록한다.

```bash
python -m pytest
python tools/check_core_purity.py
python tools/check_side_effect_free.py
python tools/check_repo_isolation.py
python tools/check_target_boundary.py
python tools/check_w00_provenance.py
python -m build --wheel
```

격리 wheel 설치는 네트워크 없이 수행한다. source baseline test는 source tree에 쓰지 않는 방식으로 실행하거나 exact copy에서 실행한다.

## 4. P0 테스트

### PKG-001 — Schema resource packaging

- wheel 내부에 registry가 요구하는 schema가 모두 존재한다.
- source checkout 없는 격리 설치 환경에서 schema registry가 초기화된다.
- schema manifest와 registered artifact version이 일치한다.

### JSON-001 — Strict JSON

- duplicate key, `NaN`, `Infinity`, `-Infinity`, 잘못된 RFC 3339 date-time을 거부한다.
- 존재하지 않는 path는 구조화된 domain error를 반환한다.
- bytes/path/mapping 입력 API가 모호하게 추론되지 않는다.

### AUTH-001 — Current context binding

- approval effective-config digest와 현재 digest가 다르면 readiness는 false다.
- workflow, policy, rules, manifest, evidence, plan 중 material digest가 바뀌면 authority가 무효다.

## 5. Managed Mutation 테스트

- 6개 mutation schema가 registry와 wheel에 포함된다.
- create/replace/delete/move exact-before와 target precondition이 강제된다.
- absolute path, traversal, workspace escape, symlink/reparse point, case/Unicode collision을 거부한다.
- 동일 idempotency key와 다른 plan digest를 hard reject한다.
- out-of-band drift는 workspace를 `UNTRUSTED`로 만들고 generation/publish를 차단한다.
- break-glass는 독립 승인자 2명, 짧은 만료, exact scope, snapshot, incident record를 요구한다.
- pure core는 실제 filesystem mutation, subprocess, network를 수행하지 않는다.

## 6. Director Mesh와 Blueprint 테스트

- 활성화된 모든 Blueprint field에 primary owner와 verifier가 있다.
- 감독은 소유하지 않은 field를 임의 수정할 수 없다.
- unresolved hard blocker는 executable plan 생성을 차단한다.
- conflict resolution은 결정론적이고 지정된 최대 반복 안에 종료한다.
- legacy projection은 Blueprint digest와 compiler version을 갖고 독립 원본이 되지 않는다.

## 7. 프로세스 개선 테스트

- macro production phase가 6개 이하로 표현된다.
- action frontier가 독립 작업을 동시에 READY로 반환한다.
- 변경된 field에 의존하지 않는 evaluator는 재실행되지 않는다.
- storyboard review, packet review, feasibility가 별도 인간 단계로 강제되지 않는다.
- 고위험 변경이 아니면 정규 episode storyboard 인간 승인이 요구되지 않는다.

## 8. 인간 개입과 승인 테스트

- AI가 signed human grant 또는 ApprovalEvidence를 생성하는 코드 경로가 없다.
- StandingAuthorization이 scope, cost, provider, retry, expiry, exact digests를 모두 검사한다.
- Guarded Autonomous 정상 경로는 최종 release 승인 1회 이하를 목표로 한다.
- CandidateDecision 조건을 만족하면 사람의 개별 후보 선택 없이 진행한다.
- confidence/margin/hard gate 미달은 stable reason code로 escalation한다.
- R4는 독립된 인간 2명과 immutable ledger 없이는 실행되지 않는다.
- 사람에게 파일을 직접 편집·복사·삭제하도록 요구하는 정상 workflow 문서나 CLI가 없다.

## 9. Phase 완료 보고 형식

| 항목 | 결과 |
|---|---|
| 구현 task IDs | |
| target 변경 파일 | |
| source pre/post 불변성 | |
| provenance/import map 변화 | |
| 추가·변경 public contracts | |
| 테스트 명령과 실제 결과 | |
| pass/fail/skip | |
| 호환성 영향 | |
| 남은 위험 | |
| rollback | |
| 다음 phase 선행조건 | |
