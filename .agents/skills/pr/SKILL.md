---
name: pr
description: Use when creating a pull request to the develop branch on GitHub after commits are pushed.
---

# pr

## Overview
커밋이 push된 기능 브랜치에서 develop 대상 PR을 작성하고 GitHub에 게시한다.

## When to Use
- 기능 브랜치 작업 완료 후 develop으로 PR을 올릴 때
- NOT: 커밋되지 않은 변경사항이 남아있을 때 (`/commit` 먼저)

## Quick Reference
- base 브랜치: 항상 `develop`
- body 형식: `📌 배경` + `✅ 수정 내역`
- body 전달: HEREDOC 필수
- 머지: GitHub 웹에서 사람이 직접

## Implementation

### 1. 브랜치 상태 확인
```bash
git status
git log develop..HEAD --oneline
git diff develop...HEAD
```
커밋되지 않은 변경사항 있으면 멈추고 `/commit` 먼저 안내.

### 2. PR 본문 작성
```
📌 배경
<이 작업이 필요한 이유, 해결하려는 문제>

✅ 수정 내역
1. ...
2. ...
```
인자($ARGUMENTS)가 있으면 배경/수정 내역에 반영.

### 3. PR 제목
- 형식: `<type>: <한글 설명>` (70자 이내)
- 예: `feat: 등기부 특약 이탈 탐지 추가`

### 4. 게시
```bash
git push -u origin <현재브랜치>  # upstream 미설정 시

gh pr create --base develop --title "<제목>" --body "$(cat <<'EOF'
📌 배경
...

✅ 수정 내역
1. ...
EOF
)"
```

### 5. PR 생성 후
```bash
git checkout develop
git pull origin develop
```
사용자에게 PR URL + CodeRabbit 자동 리뷰 예정 + "머지는 GitHub 웹에서" 안내.

## Common Mistakes
- `gh pr merge` 사용 금지 — 머지는 GitHub 웹에서만
- main으로 직접 PR 금지 — base는 항상 develop
- body를 HEREDOC 없이 전달하면 포맷 깨짐
