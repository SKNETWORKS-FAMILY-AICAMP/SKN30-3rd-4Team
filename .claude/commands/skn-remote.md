# /skn-remote — SKN org 저장소 동기화

현재 `develop`을 `SKNETWORKS-FAMILY-AICAMP/SKN30-3rd-4Team` main 브랜치에 push한다.

---

## 절차

### 1. org 리모트 확인

`org` 리모트가 없으면 추가하고, 있으면 URL이 기대 저장소와 일치하는지 검증한다.
다르면 `set-url`로 바로잡은 뒤 진행한다 (잘못된 저장소로 push 방지).

```bash
EXPECTED="https://github.com/SKNETWORKS-FAMILY-AICAMP/SKN30-3rd-4Team.git"
if git remote get-url org >/dev/null 2>&1; then
  [ "$(git remote get-url org)" = "$EXPECTED" ] || git remote set-url org "$EXPECTED"
else
  git remote add org "$EXPECTED"
fi
git remote -v   # org URL 최종 확인
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

- push 성공 시: `SKNETWORKS-FAMILY-AICAMP/SKN30-3rd-4Team` main 업데이트 완료 안내
- 실패 시: 에러 메시지 그대로 보고 후 중단 (자동 해결 금지)

---

## 규칙

- **항상 `origin/develop` 기준으로 push**
- **force push 절대 금지**
- **push 전 별도 커밋·머지 작업 금지** — 이미 develop에 반영된 것만 올린다
