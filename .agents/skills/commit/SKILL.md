---
name: commit
description: Use when committing changes to git — handles branch check, code cleanup, logical commit splitting, rebase, and push.
---

# commit

## Overview
변경된 파일을 논리 단위로 분리하여 커밋하고 push까지 완료한다.
develop/main 브랜치에 있으면 기능 브랜치를 먼저 생성한다.

## When to Use
- 로컬 변경사항을 커밋하고 원격에 올릴 때
- NOT: 변경사항이 아직 완성되지 않았을 때

## Quick Reference

| type | 용도 |
|------|------|
| feat | 새 기능 |
| fix | 버그 수정 |
| refactor | 동작 변경 없는 코드 개선 |
| chore | 빌드/설정/의존성 변경 |
| docs | 문서 수정 |
| test | 테스트 추가/수정 |
| style | 포맷/공백 등 스타일 |
| perf | 성능 개선 |

## Implementation

### 0. 브랜치 확인 (최우선)
현재 브랜치가 `main` 또는 `develop`이면 `/start` 흐름 인라인 실행 후 계속.
기능 브랜치면 바로 진행.

### 1. 변경 파악
```bash
git status
git diff          # unstaged
git diff --cached # staged
```

### 2. 스테이징 전 코드 점검 (변경된 hunk 범위 내에서만)
- 디버그 로그 제거: `print`, `logging.debug` 등
- 죽은 코드 제거: 주석 처리된 코드, 미사용 변수/함수
- 불필요한 주석 제거: 코드가 자명한 설명, 변경 컨텍스트 주석
- 수정이 필요하면 Edit으로 처리 후 바로 커밋 진행

### 3. 스테이징 제외 대상
- `.env` — API 키
- `.claude/settings.local.json` 등 로컬·비밀 설정만 제외 (`.claude/commands/`는 추적 대상 → 포함)
- `__pycache__/`, `*.pyc`, `.venv/`, `.ruff_cache/`, `.pytest_cache/`
- `data/05_vectordb/` — 별도 확인 후 커밋

### 4. 커밋 형식 (HEREDOC 필수)
```bash
git commit -m "$(cat <<'EOF'
<type>: <한글 설명>

- 세부 변경사항 (필요 시)
EOF
)"
```
- 메시지는 반드시 한글
- Co-Authored-By 태그 절대 금지
- 하나의 커밋 = 하나의 논리적 변경

### 5. 원격 최신화 및 푸시
```bash
git fetch origin

# 원격에 같은 브랜치가 있을 때만 동기화 (없으면 첫 푸시이므로 건너뜀)
# 충돌 시: 자동 해결 금지 → git rebase --abort 후 즉시 중단·사용자 확인
if git ls-remote --exit-code --heads origin <현재 브랜치> >/dev/null 2>&1; then
  git pull --rebase
fi

# 푸시 (첫 푸시면 -u로 upstream 설정, 이후에도 안전)
git push -u origin <현재 브랜치>

git status   # "up to date with 'origin/<브랜치>'" 확인
```
develop 통합(rebase origin/develop)은 여기서 하지 않는다 — PR 시점에만.

### 6. 후속 작업 금지
push까지만. PR 생성·머지·브랜치 삭제는 명시적 지시 시만.

## Common Mistakes
- develop/main에서 바로 커밋 → 반드시 기능 브랜치 먼저
- 충돌 발생 시 자동 해결 금지 → 멈추고 사용자에게 확인
- 로컬 커밋으로 끝내지 말 것 → push까지 완료
