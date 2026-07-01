"""
vs_method.py  (kb_chunks 버전)
전월세 법령·판례·사례 RAG 핵심 모듈 (Supabase + pgvector + OpenAI text-embedding-3-small)

변경점 (legal_docs → kb_chunks):
  - 분류 정보를 JSONB 한 덩어리 대신 '필터·랭킹에 쓰는 것'만 타입 컬럼으로 승격
      source_type / source_org / doc_title / doc_year / authority / stage / issue / law_name / article / case_no
  - 청크 단위 부수 정보(chunk_index, char_len)만 extra(JSONB)에 남김
  - authority(효력 위계: binding|persuasive|reference)는 source_type에서 자동 유도
  - 검색 필터: metadata->> 문자열 매칭 → 타입 컬럼 연산(배열 &&, stage IN (…, 'both'), doc_year >=)

임베딩 엔진은 기존 그대로 사용.
"""

import os
import re

import psycopg
from psycopg.types.json import Json
from langchain_openai import OpenAIEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter

# ──────────────────────────────────────────────
# 설정  (임베딩은 기존 코드 그대로)
# ──────────────────────────────────────────────
EMBEDDING_MODEL = "text-embedding-3-small"
EMBEDDING_DIM = 1024  # ⚠️ 테이블 VECTOR(N)과 반드시 일치시킬 것

# dimensions=1024 로 차원을 줄여 테이블(VECTOR(1024))과 맞춤
embeddings = OpenAIEmbeddings(model=EMBEDDING_MODEL, dimensions=EMBEDDING_DIM)

# source_type → authority(효력 위계) 기본 매핑. 적재 시 override 가능.
#   binding    : 법령·대법원 판례        → 결론의 1차 근거
#   persuasive : 법령해석례·조정/상담 사례 → 참고 근거("이런 경우 이렇게 판단됐다")
#   reference  : 표준계약서·가이드·안내문  → 배경·실무 설명
AUTHORITY_BY_SOURCE = {
    "statute": "binding",
    "precedent": "binding",
    "interpretation": "persuasive",
    "mediation_case": "persuasive",
    "counsel_case": "persuasive",
    "standard_contract": "reference",
    "guide": "reference",
}


# ──────────────────────────────────────────────
# 연결
# ──────────────────────────────────────────────
def get_conn():
    """
    환경변수 DB_URL 로 Supabase 에 연결.
    SQLAlchemy 형식(postgresql+psycopg://)이 들어와도 raw psycopg 용으로 정리.
    """
    db_url = os.environ["DB_URL"].replace("postgresql+psycopg://", "postgresql://")
    return psycopg.connect(db_url)


# ──────────────────────────────────────────────
# 스키마 보장 (확장 + 테이블 + 인덱스)
# ──────────────────────────────────────────────
def ensure_schema(conn):
    with conn.cursor() as cur:
        cur.execute("CREATE EXTENSION IF NOT EXISTS vector;")
        cur.execute(
            f"""
            CREATE TABLE IF NOT EXISTS kb_chunks (
                id          BIGSERIAL PRIMARY KEY,
                source_type TEXT NOT NULL,                 -- statute|precedent|interpretation|
                                                           -- mediation_case|counsel_case|standard_contract|guide
                source_org  TEXT,                          -- 국토교통부 | 한국부동산원 | 서울시 | 법제처
                doc_title   TEXT NOT NULL,                 -- 원문 파일/문서명 (연도 추적)
                doc_year    INT,                           -- 2021~2026 (개정·최신성 필터)
                authority   TEXT NOT NULL,                 -- binding | persuasive | reference
                stage       TEXT NOT NULL,                 -- pre | post | both
                issue       TEXT[] NOT NULL DEFAULT '{{}}', -- 쟁점 태그 (예: {{deposit,repair}})
                law_name    TEXT,                          -- 법령·판례에만
                article     TEXT,                          -- 조항 (제3조 등)
                case_no     TEXT,                          -- 판례·조정 사건번호
                content     TEXT NOT NULL,                 -- 청크 원문
                embedding   VECTOR({EMBEDDING_DIM}) NOT NULL,
                extra       JSONB,                         -- chunk_index, char_len 등 부수정보
                created_at  TIMESTAMPTZ DEFAULT now()
            );
            """
        )
        # 벡터 검색용 HNSW (코사인)
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS kb_chunks_embedding_idx
            ON kb_chunks USING hnsw (embedding vector_cosine_ops);
            """
        )
        # 메타 필터 가속
        cur.execute("CREATE INDEX IF NOT EXISTS kb_chunks_stype_stage_idx ON kb_chunks (source_type, stage);")
        cur.execute("CREATE INDEX IF NOT EXISTS kb_chunks_authority_idx ON kb_chunks (authority);")
        cur.execute("CREATE INDEX IF NOT EXISTS kb_chunks_issue_idx ON kb_chunks USING gin (issue);")
    conn.commit()


def clear_table(conn):
    """테스트 재실행 시 중복 적재를 막기 위해 비움."""
    with conn.cursor() as cur:
        cur.execute("TRUNCATE kb_chunks RESTART IDENTITY;")
    conn.commit()


# ──────────────────────────────────────────────
# 청킹 (청크마다 다른 per-chunk 필드 생성)
# ──────────────────────────────────────────────
# 법령·판례는 '제○조' 경계 우선. 사례집은 사례 1건이 한 청크가 되도록
# separators/chunk_size 를 호출부에서 바꿔 넣는다(아래 SPLIT_PRESETS 참고).
SPLIT_PRESETS = {
    "law": dict(chunk_size=500, chunk_overlap=50,
                separators=["\n제", "\n\n", "\n", ". ", " ", ""]),
    "case": dict(chunk_size=1200, chunk_overlap=80,
                 separators=["\n사례", "\n\n", "\n", ". ", " ", ""]),
    "default": dict(chunk_size=700, chunk_overlap=60,
                    separators=["\n\n", "\n", ". ", " ", ""]),
}


def chunk_document(full_text: str, split_preset: str = "law") -> list[dict]:
    cfg = SPLIT_PRESETS.get(split_preset, SPLIT_PRESETS["default"])
    splitter = RecursiveCharacterTextSplitter(**cfg)
    chunks = splitter.split_text(full_text)

    items = []
    for i, chunk in enumerate(chunks):
        m = re.search(r"제\d+조(?:의\d+)?", chunk)
        items.append({
            "content": chunk,
            "article": m.group(0) if m else None,       # 청크에서 조항 감지 (없으면 None)
            "extra": {"chunk_index": i, "char_len": len(chunk)},
        })
    return items


# ──────────────────────────────────────────────
# 적재 (청킹 → 배치 임베딩 → 배치 INSERT)
# ──────────────────────────────────────────────
def ingest_document(conn, full_text: str, doc: dict, split_preset: str = "law") -> int:
    """
    doc = 문서 단위 타입 필드:
        source_type (필수)  : statute|precedent|...
        doc_title   (필수)
        stage       (필수)  : pre|post|both
        issue       (list)  : 쟁점 태그, 없으면 []
        authority   (선택)  : 미지정 시 source_type 에서 자동 유도
        source_org / doc_year / law_name / case_no (선택)
    article 은 청크에서 자동 감지되며, doc['article'] 로 강제 지정도 가능.
    """
    source_type = doc["source_type"]
    authority = doc.get("authority") or AUTHORITY_BY_SOURCE.get(source_type, "reference")
    stage = doc.get("stage", "both")
    issue = doc.get("issue", [])

    items = chunk_document(full_text, split_preset)
    vectors = embeddings.embed_documents([it["content"] for it in items])  # 한 번에 배치 임베딩

    rows = []
    for it, vec in zip(items, vectors):
        rows.append((
            source_type,
            doc.get("source_org"),
            doc["doc_title"],
            doc.get("doc_year"),
            authority,
            stage,
            issue,                                   # text[] ← 파이썬 list 자동 변환
            doc.get("law_name"),
            doc.get("article") or it["article"],     # doc 지정 우선, 없으면 청크 감지값
            doc.get("case_no"),
            it["content"],
            str(vec),                                # '[...]' → ::vector 캐스팅
            Json(it["extra"]),
        ))

    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO kb_chunks
                (source_type, source_org, doc_title, doc_year, authority, stage,
                 issue, law_name, article, case_no, content, embedding, extra)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::vector, %s)
            """,
            rows,
        )
    conn.commit()
    return len(rows)


# ──────────────────────────────────────────────
# 검색 (코사인 유사도 + 타입 컬럼 필터, 기본 k=5)
# ──────────────────────────────────────────────
def search_similar(
    conn,
    query: str,
    *,
    stage: str = None,                 # 'pre'|'post' → 해당 stage + 'both' 공통 조항 포함
    issues: list[str] = None,          # 쟁점 태그 배열 overlap
    source_types: list[str] = None,    # 예: ['statute','precedent']
    authorities: list[str] = None,     # 예: ['binding'] — 결론 근거만
    min_year: int = None,              # 최신성 필터 (개정 반영)
    k: int = 5,
    min_score: float = 0.0,
) -> list[dict]:
    qvec = str(embeddings.embed_query(query))

    where = []
    params = {"q": qvec, "k": k}
    if stage:
        where.append("stage IN (%(stage)s, 'both')")
        params["stage"] = stage
    if issues:
        where.append("issue && %(issues)s::text[]")
        params["issues"] = issues
    if source_types:
        where.append("source_type = ANY(%(stypes)s::text[])")
        params["stypes"] = source_types
    if authorities:
        where.append("authority = ANY(%(auths)s::text[])")
        params["auths"] = authorities
    if min_year:
        where.append("doc_year >= %(minyear)s")
        params["minyear"] = min_year

    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    sql = f"""
        SELECT source_type, authority, law_name, article, case_no,
               doc_title, doc_year, content,
               1 - (embedding <=> %(q)s::vector) AS similarity
        FROM kb_chunks
        {where_sql}
        ORDER BY embedding <=> %(q)s::vector
        LIMIT %(k)s
    """

    cols = ["source_type", "authority", "law_name", "article", "case_no",
            "doc_title", "doc_year", "content", "similarity"]
    with conn.cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()

    out = []
    for r in rows:
        d = dict(zip(cols, r))
        d["similarity"] = round(d["similarity"], 4)
        if d["similarity"] >= min_score:
            out.append(d)
    return out


# ──────────────────────────────────────────────
# 사용 예
# ──────────────────────────────────────────────
if __name__ == "__main__":
    conn = get_conn()
    ensure_schema(conn)

    # 법령 적재 (조항 단위)
    ingest_document(
        conn,
        full_text="제3조(대항력 등) ① 임대차는 그 등기가 없는 경우에도 임차인이 "
                  "주택의 인도와 주민등록을 마친 때에는 그 다음 날부터 제삼자에 대하여 효력이 생긴다. …",
        doc={
            "source_type": "statute",
            "source_org": "법제처",
            "doc_title": "주택임대차보호법(20200731 개정판)",
            "doc_year": 2020,
            "stage": "pre",
            "issue": ["deposit", "opposing_power"],
            "law_name": "주택임대차보호법",
        },
        split_preset="law",
    )

    # 상담사례집 적재 (사례 1건 = 한 청크)
    ingest_document(
        conn,
        full_text="사례 12) 보증금 반환을 미루는 임대인 … (질문) … (판단) …",
        doc={
            "source_type": "counsel_case",
            "source_org": "한국부동산원",
            "doc_title": "2024 주택임대차 상담사례집(최종 배포)",
            "doc_year": 2024,
            "stage": "post",
            "issue": ["deposit"],
        },
        split_preset="case",
    )

    # 계약 후 · 보증금 쟁점, 결론 근거는 binding 만
    for hit in search_similar(
        conn,
        query="집주인이 보증금을 안 돌려줘요",
        stage="post", issues=["deposit"], authorities=["binding"], k=3,
    ):
        print(f"[{hit['similarity']}] ({hit['authority']}) "
              f"{hit['law_name'] or hit['doc_title']} {hit['article'] or ''}")
