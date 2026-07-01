"""
vs_method.py  (metadata JSONB 버전 · 자유 스키마)
전월세 법령·판례·사례 RAG 핵심 모듈 (Supabase + pgvector + OpenAI text-embedding-3-small)

저장 구조: content + embedding + metadata(jsonb).
  metadata 내용은 source_type 마다 다를 수 있다 (자유 스키마).
    statute      : law_name, article, 시행일, 공포번호 …
    precedent    : case_no, court, 선고일, 사건종류 …
    counsel_case : 사례번호, 처리결과 …
  → ingest_document 은 doc 의 '임의 키'를 그대로 metadata 에 통과시키고,
    필수/정규화 필드(source_type·authority·stage·issue)만 보장한다.

공개 API(ingest_document / search_similar) 는 graph.py / test_legal_rag.py 와 호환.
  검색 결과는 metadata 를 펼쳐서 돌려주므로(예: r['authority'], r['court'] …) 문서마다 키가 달라도 그대로 접근 가능.

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

embeddings = OpenAIEmbeddings(model=EMBEDDING_MODEL, dimensions=EMBEDDING_DIM)

# source_type → authority(효력 위계) 기본 매핑. 적재 시 override 가능.
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
                id         BIGSERIAL PRIMARY KEY,
                content    TEXT NOT NULL,
                embedding  VECTOR({EMBEDDING_DIM}) NOT NULL,
                metadata   JSONB NOT NULL DEFAULT '{{}}',
                created_at TIMESTAMPTZ DEFAULT now()
            );
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS kb_chunks_embedding_idx
            ON kb_chunks USING hnsw (embedding vector_cosine_ops);
            """
        )
        # metadata 필터용 GIN (jsonb_ops: @>, ?, ?| 지원) — 어떤 키가 와도 인덱싱됨
        cur.execute(
            "CREATE INDEX IF NOT EXISTS kb_chunks_metadata_idx ON kb_chunks USING gin (metadata);"
        )
        # 연도 범위 필터 가속 (선택)
        cur.execute(
            "CREATE INDEX IF NOT EXISTS kb_chunks_year_idx ON kb_chunks (((metadata->>'doc_year')::int));"
        )
    conn.commit()


def clear_table(conn):
    with conn.cursor() as cur:
        cur.execute("TRUNCATE kb_chunks RESTART IDENTITY;")
    conn.commit()


# ──────────────────────────────────────────────
# 청킹 (청크마다 다른 per-chunk 정보 생성)
# ──────────────────────────────────────────────
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
            "article": m.group(0) if m else None,   # 조항 자동 감지 (없으면 None)
            "chunk_index": i,
            "char_len": len(chunk),
        })
    return items


# ──────────────────────────────────────────────
# 적재 (doc 임의 키 통과 → 배치 임베딩 → INSERT)
# ──────────────────────────────────────────────
# source_type 마다 필요한 필드만 doc 에 넣으면 됨. 아래는 필수/정규화 필드일 뿐,
# 그 외 어떤 키(court, 선고일, 시행일, 처리결과 …)를 넣어도 metadata 로 그대로 저장된다.
REQUIRED_DOC_KEYS = ("source_type", "doc_title")


def ingest_document(conn, full_text: str, doc: dict, split_preset: str = "law") -> int:
    for key in REQUIRED_DOC_KEYS:
        if not doc.get(key):
            raise ValueError(f"doc['{key}'] 는 필수입니다.")

    source_type = doc["source_type"]
    # doc 전체를 베이스로 깔고(임의 키 보존), 정규화 필드만 보장/덮어씀
    base = {
        **doc,
        "source_type": source_type,
        "authority": doc.get("authority") or AUTHORITY_BY_SOURCE.get(source_type, "reference"),
        "stage": doc.get("stage", "both"),
        "issue": doc.get("issue", []),
    }

    items = chunk_document(full_text, split_preset)
    vectors = embeddings.embed_documents([it["content"] for it in items])  # 배치 임베딩

    rows = []
    for it, vec in zip(items, vectors):
        meta = {
            **base,
            "article": base.get("article") or it["article"],  # doc 지정 우선, 없으면 청크 감지
            "chunk_index": it["chunk_index"],
            "char_len": it["char_len"],
        }
        meta = {k: v for k, v in meta.items() if v is not None}  # None 키 제거
        rows.append((it["content"], str(vec), Json(meta)))

    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO kb_chunks (content, embedding, metadata) VALUES (%s, %s::vector, %s)",
            rows,
        )
    conn.commit()
    return len(rows)

def ingest_not_chunks(conn, chunks: list, doc: dict) -> int:
    """이미 청킹된 청크들을 그대로 적재 (내부 청킹 없음).

    chunks: 미리 나눠 둔 청크 리스트.
            각 원소는 str 이거나 {"content": ..., 그 외 per-chunk 메타} dict.
    doc   : 문서 단위 메타 (ingest_document 과 동일 규칙 · 임의 키 보존).
    """
    for key in REQUIRED_DOC_KEYS:
        if not doc.get(key):
            raise ValueError(f"doc['{key}'] 는 필수입니다.")

    source_type = doc["source_type"]
    base = {
        **doc,
        "source_type": source_type,
        "authority": doc.get("authority") or AUTHORITY_BY_SOURCE.get(source_type, "reference"),
        "stage": doc.get("stage", "both"),
        "issue": doc.get("issue", []),
    }

    # str / dict 정규화 + 빈 청크 제거
    norm = []
    for item in (chunks or []):
        if isinstance(item, dict):
            content, extra = item.get("content", ""), {k: v for k, v in item.items() if k != "content"}
        else:
            content, extra = item, {}
        if content and content.strip():
            norm.append((content, extra))
    if not norm:
        return 0

    vectors = embeddings.embed_documents([c for c, _ in norm])  # 배치 임베딩

    rows = []
    for i, ((content, extra), vec) in enumerate(zip(norm, vectors)):
        meta = {**base, **extra, "chunk_index": i, "char_len": len(content)}
        if not meta.get("article"):                      # 지정값 없으면 조항 자동 감지
            m = re.search(r"제\d+조(?:의\d+)?", content)
            if m:
                meta["article"] = m.group(0)
        meta = {k: v for k, v in meta.items() if v is not None}
        rows.append((content, str(vec), Json(meta)))

    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO kb_chunks (content, embedding, metadata) VALUES (%s, %s::vector, %s)",
            rows,
        )
    conn.commit()
    return len(rows)


# ──────────────────────────────────────────────
# 검색 (코사인 + metadata 필터)
#   반환: {**metadata, content, similarity}  → 문서마다 키가 달라도 그대로 접근
# ──────────────────────────────────────────────
def search_similar(
    conn,
    query: str,
    *,
    stage: str = None,                 # 'pre'|'post' → 해당 stage + 'both' 공통 포함
    issues: list[str] = None,          # 쟁점 태그 overlap (하나라도 겹치면)
    source_types: list[str] = None,    # 예: ['statute','precedent']
    authorities: list[str] = None,     # 예: ['binding']
    min_year: int = None,              # 최신성 필터
    meta_filter: dict = None,          # 임의 키 등식 필터 (예: {'court': '대법원'})
    k: int = 5,
    min_score: float = 0.0,
) -> list[dict]:
    qvec = str(embeddings.embed_query(query))
    where, params = [], {"q": qvec, "k": k}

    def or_contains(field, values, prefix):
        ors = []
        for i, v in enumerate(values):
            key = f"{prefix}{i}"
            ors.append(f"metadata @> %({key})s::jsonb")
            params[key] = Json({field: v})
        where.append("(" + " OR ".join(ors) + ")")

    if stage:
        where.append("(metadata @> %(stage1)s::jsonb OR metadata @> %(stageboth)s::jsonb)")
        params["stage1"] = Json({"stage": stage})
        params["stageboth"] = Json({"stage": "both"})
    if issues:
        # 배열 overlap → {"issue":[tag]} 개별 containment OR
        or_contains("issue", [[t] for t in issues], "iss")  # {"issue":["deposit"]} 형태
    if source_types:
        or_contains("source_type", source_types, "st")
    if authorities:
        or_contains("authority", authorities, "au")
    if min_year:
        where.append("(metadata->>'doc_year')::int >= %(minyear)s")
        params["minyear"] = min_year
    if meta_filter:  # 임의 키 등식(AND)
        for i, (kk, vv) in enumerate(meta_filter.items()):
            key = f"mf{i}"
            where.append(f"metadata @> %({key})s::jsonb")
            params[key] = Json({kk: vv})

    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    sql = f"""
        SELECT content, metadata,
               1 - (embedding <=> %(q)s::vector) AS similarity
        FROM kb_chunks
        {where_sql}
        ORDER BY embedding <=> %(q)s::vector
        LIMIT %(k)s
    """

    with conn.cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()

    out = []
    for content, metadata, similarity in rows:
        sim = round(similarity, 4)
        if sim < min_score:
            continue
        out.append({**(metadata or {}), "content": content, "similarity": sim})
    return out


# ──────────────────────────────────────────────
# 사용 예 (source_type 마다 다른 metadata)
# ──────────────────────────────────────────────
if __name__ == "__main__":
    conn = get_conn()
    ensure_schema(conn)

    # 법령: law_name/article/시행일
    ingest_document(
        conn,
        "제3조(대항력 등) ① 임대차는 그 등기가 없는 경우에도 임차인이 주택의 인도와 "
        "주민등록을 마친 때에는 그 다음 날부터 제삼자에 대하여 효력이 생긴다. …",
        {
            "source_type": "statute", "source_org": "법제처",
            "doc_title": "주택임대차보호법", "doc_year": 2023,
            "stage": "both", "issue": ["deposit", "opposing_power"],
            "law_name": "주택임대차보호법", "시행일": "2023-07-19",
        },
        split_preset="law",
    )

    # 판례: court/case_no/선고일  (법령엔 없는 키)
    ingest_document(
        conn,
        "대항력을 갖춘 임차인은 후순위 담보권자보다 우선하여 보증금을 변제받을 수 있다 …",
        {
            "source_type": "precedent", "source_org": "대법원",
            "doc_title": "대법원 2013다12345 판결", "doc_year": 2014,
            "stage": "post", "issue": ["deposit", "priority_repayment"],
            "court": "대법원", "case_no": "2013다12345", "선고일": "2014-03-27",
        },
        split_preset="default",
    )

    # 판례만, 보증금 쟁점
    for hit in search_similar(
        conn, "보증금 우선변제 판례",
        source_types=["precedent"], issues=["priority_repayment"], k=3,
    ):
        # 판례에만 있는 키(court/case_no)도 그대로 접근
        print(f"[{hit['similarity']}] ({hit['authority']}) "
              f"{hit.get('court','')} {hit.get('case_no','')} | {hit['content'][:40]}...")