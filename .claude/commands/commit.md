# /commit — 커밋 워크플로우

변경된 파일을 분석하고 작업 단위로 논리적으로 분리하여 커밋 → rebase → push까지 수행한다.

---

## 0. 브랜치 확인 (가장 먼저 실행)

현재 브랜치가 `main` 또는 `develop`이면 `/start` 흐름을 **자동으로 인라인 실행**한 뒤 커밋을 이어서 진행한다.

```
현재 브랜치 == main 또는 develop?
  → 1. 사용자에게 작업 개요를 물어본다
  → 2. git fetch origin develop
  → 3. git checkout -b <prefix>/<브랜치명> origin/develop
  → 4. 브랜치 생성 완료 보고 후 아래 커밋 절차(1~6) 계속 진행
```

이미 기능 브랜치(`feat/*`, `fix/*` 등)에 있으면 바로 커밋 진행.

---

## 1. 변경 파악

```bash
git status
git diff          # unstaged
git diff --cached # staged
```

---

## 2. 스테이징 전 코드 점검 (변경된 hunk 범위 내에서만)

- **디버그 로그 제거**: `print`, `logging.debug` 등 임시 출력
- **죽은 코드 제거**: 주석 처리된 코드, 미사용 변수/함수
- **불필요한 주석 제거**: 코드가 자명한 설명, "X를 위해 추가" 류 변경 컨텍스트 주석
- **스테일 주석 정리**: 이번 변경으로 맞지 않게 된 주석 → 수정 또는 삭제
- 수정이 필요하면 `Edit`으로 처리 후 바로 커밋 진행 (별도 안내 불필요)

---

## 3. 스테이징 제외 대상

절대 스테이징하지 않을 것:

- `.env` — API 키 포함 환경 변수
- `.claude/settings.local.json` 등 로컬·비밀 설정 — `.claude/commands/`(슬래시 커맨드)는 추적 대상이므로 포함
- `__pycache__/`, `*.pyc` — Python 캐시
- `.venv/` — 가상환경
- `.ruff_cache/`, `.pytest_cache/` — 도구 캐시
- `data/05_vectordb/` — 용량 큰 벡터스토어는 별도 확인 후 커밋

---

## 4. 커밋 규칙

### 형식
```
<type>: <한글 설명>
```

| type | 용도 |
|------|------|
| `feat` | 새 기능 |
| `fix` | 버그 수정 |
| `refactor` | 동작 변경 없는 코드 개선 |
| `chore` | 빌드/설정/의존성 변경 |
| `docs` | 문서 수정 |
| `test` | 테스트 추가/수정 |
| `style` | 포맷/공백 등 스타일 |
| `perf` | 성능 개선 |

### 필수 규칙
- **메시지는 반드시 한글**
- **Co-Authored-By 태그 절대 금지**
- 하나의 커밋 = 하나의 논리적 변경 (관련 없는 변경은 별도 커밋)

### 커밋 명령 형식 (HEREDOC 필수)
```bash
git commit -m "$(cat <<'EOF'
<type>: <한글 설명>

- 세부 변경사항 (필요 시)
EOF
)"
```

---

## 5. 원격 최신화 및 푸시

같은 브랜치를 팀원과 공유할 수 있으므로 push 전 반드시 동기화한다.

```bash
git fetch origin

# 원격에 같은 브랜치가 있을 때만 동기화 (없으면 첫 푸시이므로 건너뜀)
if git ls-remote --exit-code --heads origin <현재 브랜치> >/dev/null 2>&1; then
  git pull --rebase
fi
```

> `git pull --rebase`는 **같은 브랜치**의 원격 최신 상태를 내 커밋 아래에 깔아주는 것.
> 커밋이 사라지지 않고, 히스토리가 깔끔하게 유지된다.
> develop 통합(rebase origin/develop)은 여기서 하지 않는다 — PR 시점에만.

```bash
# 푸시 (첫 push: upstream 설정)
git push -u origin <현재 브랜치>
# 이후 푸시: git push

# 검증
git status   # "up to date with 'origin/<브랜치>'" 확인
```

- **충돌 발생 시**: 자동 해결 금지 → `git rebase --abort`로 되돌리고 즉시 멈춰 사용자에게 확인 후 진행
- **푸시까지 완료가 목표** — 로컬 커밋만으로 끝내지 말 것

---

## 6. 후속 작업 금지

`push`까지만 진행. 아래 작업은 사용자가 명시적으로 지시할 때만 수행:

- `develop` → `main` 머지
- PR 생성
- 브랜치 삭제
- 태그 생성
