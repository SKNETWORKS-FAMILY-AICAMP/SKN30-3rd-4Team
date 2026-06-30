---
name: start
description: Use when starting a new task — evaluates ambiguity before branching, creates feature branch from develop.
---

# start

## Overview
새 작업을 시작하기 전 기획 모호도를 평가하고, 명확해지면 develop 기반 기능 브랜치를 생성한다.

## When to Use
- 새 기능/수정 작업을 시작할 때
- NOT: 이미 기능 브랜치에서 작업 중일 때

## Quick Reference

| prefix | 용도 |
|--------|------|
| feat | 새 기능 |
| fix | 버그 수정 |
| refactor | 동작 변경 없는 코드 개선 |
| chore | 빌드/설정/의존성 |
| docs | 문서 |
| test | 테스트 추가·수정 |

## Implementation

### 0. 모호도 게이트 (최우선, 필수)
브랜치 생성 전에 작업 설명의 모호도를 평가한다.

**모호도 0.2 미만**일 때만 절차 진입. 이상이면 즉시 질문.
모호도 0.2 미만이 될 때까지 반복.

**평가 기준 (하나라도 불명확하면 모호도 높음):**
- 변경 대상 기능/레이어가 특정되는가
- 기대 동작·결과가 명확한가
- 데이터 흐름 상 어느 단계인가 (오프라인 빌드 vs 런타임)
- 엣지 케이스·범위가 합의됐는가

**핵심: 추측 금지, 즉시 질문, 모호도 0.2 미만 확보 후 시작.**

### 1. 최신 develop 가져오기
```bash
git fetch origin develop
```

### 2. 새 브랜치 생성
```bash
git checkout origin/develop -b <prefix>/<브랜치명>
```
브랜치명은 작업 개요를 영문 kebab-case로 요약.
애매하면 사용자에게 확인.

### 3. 완료 보고
- 생성된 브랜치명
- 기준 커밋 (develop HEAD)

### 4. 코드 탐색
관련 영역 파악:
- `src/` — 핵심 로직
- `app/` — Streamlit UI
- `data/` — 오프라인 파이프라인
- `eval/` — 평가 실험

## Common Mistakes
- 모호도 게이트 없이 바로 브랜치 생성 금지
- 추측으로 모호한 요구사항 채워넣기 금지
