# src/

런타임 소스 코드. 레이어 간 의존 방향은 아래 순서를 따른다.

- contracts/ — Pydantic 모델, enum, Port 인터페이스 정의 (의존 없음)
- core/ — 순수 함수, 외부 I/O 없음, TDD 대상 (contracts/만 의존)
- adapter/ — ChromaDB, SQLite, MCP 등 외부 I/O 구현체 (contracts/만 의존)
- pipe/ — LangGraph 파이프라인 조합 (core/ + adapter/ 의존)
