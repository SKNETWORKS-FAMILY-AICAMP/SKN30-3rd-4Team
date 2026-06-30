---
name: skn-remote
description: Use when syncing origin/develop to SKNETWORKS-FAMILY-AICAMP org repository main branch.
---

# skn-remote

## Overview
현재 `origin/develop`을 `SKNETWORKS-FAMILY-AICAMP/SKN30-3rd-4Team` main 브랜치에 push한다.

## When to Use
- develop 변경사항을 SKN org 저장소 main에 반영할 때
- NOT: force push가 필요한 상황 (절대 금지)

## Implementation

### 1. org 리모트 확인
```bash
git remote -v
```
`org` 리모트가 없으면 추가:
```bash
git remote add org https://github.com/SKNETWORKS-FAMILY-AICAMP/SKN30-3rd-4Team.git
```

### 2. 최신 develop 가져오기
```bash
git fetch origin
```

### 3. org main으로 push
```bash
git push org origin/develop:main
```

### 4. 결과 보고
- 성공: `SKNETWORKS-FAMILY-AICAMP/SKN30-3rd-4Team` main 업데이트 완료 안내
- 실패: 에러 메시지 그대로 보고 후 중단 (자동 해결 금지)

## Common Mistakes
- force push 절대 금지
- push 전 별도 커밋·머지 작업 금지 — 이미 develop에 반영된 것만 올린다
- 항상 `origin/develop` 기준으로 push
