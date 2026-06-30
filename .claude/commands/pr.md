# /pr — PR 작성 및 GitHub 게시

PR을 작성하고 GitHub에 게시한다.

인자: $ARGUMENTS

---

## 1. 브랜치 상태 확인

```bash
git status
```

커밋되지 않은 변경사항이 있으면 사용자에게 알리고 멈춘다. (`/commit` 먼저 실행 안내)

```bash
git fetch origin develop                 # 기준 브랜치 최신화
git log origin/develop..HEAD --oneline   # 포함될 커밋 목록
git diff origin/develop...HEAD           # 전체 변경 내용
```

---

## 2. PR 본문 작성

```
📌 배경
<이 작업이 필요한 이유, 해결하려는 문제>

✅ 수정 내역
1. ...
2. ...
```

- `$ARGUMENTS`가 있으면 배경/수정 내역에 반영

---

## 3. PR 제목 결정

- 형식: `<type>: <한글 설명>` (Conventional Commits)
- 70자 이내로 간결하게
- 예시:
  ```
  feat: 등기부 특약 이탈 탐지 추가
  fix: Chroma 임베딩 재시도 오류 수정
  docs: 데이터 파이프라인 명세 업데이트
  ```

---

## 4. 게시 및 결과 보고

```bash
# upstream 미설정 시 push
git push -u origin <현재브랜치>

# PR 생성 (base는 항상 develop)
gh pr create --base develop --title "<제목>" --body "$(cat <<'EOF'
📌 배경
...

✅ 수정 내역
1. ...
EOF
)"
```

body는 반드시 HEREDOC으로 전달하여 포맷 유지.

PR 생성 후 develop으로 이동:

```bash
git checkout develop
git pull --ff-only origin develop   # fast-forward 불가(로컬 develop 갈라짐)면 거부 → 중단
```

`--ff-only`가 거부되면 자동 머지 만들지 말고 멈추고 "로컬 develop이 원격과 갈라짐 — 수동 확인" 안내.

사용자에게 보고:
- PR URL
- CodeRabbit 자동 리뷰가 달릴 예정임 안내
- "머지는 GitHub 웹에서 직접 해주세요." 안내

---

## 규칙

- **base 브랜치는 항상 `develop`** (`main`으로 직접 PR 금지)
- **커밋되지 않은 변경사항 있으면 PR 생성 전에 멈추기**
- **body는 HEREDOC으로 전달** (포맷 유지)
- **`gh pr merge` 절대 금지** — 머지는 GitHub 웹에서 사람이 직접 수행
- **브랜치 삭제·태그 생성은 사용자가 명시적으로 지시할 때만 수행**
- **충돌·divergence(꼬임) 발생 시 자동 해결 금지** → 즉시 멈추고 사용자 확인
