"""
test_vs.py
Supabase pgvector + OpenAI 임베딩 동작 확인용 단독 테스트. (metadata JSONB · 자유 스키마)

실행:
    python test_vs.py

준비물 (.env):
    DB_URL=postgresql://postgres.xxxx:[PW]@aws-0-...pooler.supabase.com:5432/postgres
    OPENAI_API_KEY=sk-...
"""
import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from dotenv import load_dotenv

load_dotenv()  # ⚠️ vs_method import 보다 먼저 (OPENAI_API_KEY 로드)

from src.core.vs_method import (
    get_conn,
    ensure_schema,
    clear_table,
    ingest_document,
    search_similar,
)

# 샘플 법령 (주택임대차보호법 일부 발췌)
SAMPLE_LAW = """제3조(대항력 등) ① 임대차는 그 등기가 없는 경우에도 임차인이 주택의 인도와 주민등록을 마친 때에는 그 다음 날부터 제3자에 대하여 효력이 생긴다.
제3조의2(보증금의 회수) ① 임차인이 임차주택에 대하여 보증금반환청구소송의 확정판결을 받은 경우 그 집행권원에 의하여 경매를 신청할 수 있다.
② 대항요건을 갖추고 임대차계약증서상의 확정일자를 받은 임차인은 후순위권리자보다 우선하여 보증금을 변제받을 권리가 있다.
제8조(보증금 중 일정액의 보호) ① 임차인은 보증금 중 일정액을 다른 담보물권자보다 우선하여 변제받을 권리가 있다.
② 제1항의 경우에는 제3조제1항의 요건을 그 주택에 대한 경매신청의 등기 전에 갖추어야 한다.
"""

# 샘플 판례 (법령엔 없는 키: court / case_no / 선고일 을 metadata 로 저장)
SAMPLE_PRECEDENT = """대항요건과 확정일자를 갖춘 임차인은 후순위 담보권자 기타 채권자보다 우선하여 보증금을 변제받을 수 있다.
주택의 인도와 주민등록은 대항력의 존속요건이므로 배당요구 종기까지 유지되어야 한다.
"""


def _cite(r: dict) -> str:
    """source_type 별 출처 표기 (문서마다 키가 달라 .get 으로 분기)."""
    st = r.get("source_type")
    if st == "precedent":
        return " ".join(x for x in (r.get("court"), r.get("case_no")) if x) or r.get("doc_title", "")
    # 법령·해석례 등
    head = r.get("law_name") or r.get("doc_title", "")
    return " ".join(x for x in (head, r.get("article")) if x)


def _show(results):
    for r in results:
        print(f"  [{r['similarity']}] ({r.get('authority','?')}) {_cite(r)} "
              f"| {r.get('content','')[:45]}...")


def main():
    conn = get_conn()
    print("====연결 성공====")

    ensure_schema(conn)
    print("====스키마 준비 완료 (extension + kb_chunks + index)====")

    clear_table(conn)  # 재실행 시 중복 방지 (실데이터 운영 땐 빼세요)

    # 법령 적재 (law_name/article 계열 키)
    n1 = ingest_document(
        conn, SAMPLE_LAW,
        {
            "source_type": "statute",          # → authority 자동 유도(binding)
            "source_org": "법제처",
            "doc_title": "주택임대차보호법",
            "doc_year": 2023,
            "stage": "both",                   # 대항력(계약 전)·보증금 회수(계약 후) 모두
            "issue": ["deposit", "opposing_power", "priority_repayment"],
            "law_name": "주택임대차보호법",
        },
        split_preset="law",
    )
    # 판례 적재 (법령엔 없는 키: court / case_no / 선고일)
    n2 = ingest_document(
        conn, SAMPLE_PRECEDENT,
        {
            "source_type": "precedent",        # → authority 자동 유도(binding)
            "source_org": "대법원",
            "doc_title": "대법원 2013다12345 판결",
            "doc_year": 2014,
            "stage": "post",
            "issue": ["deposit", "priority_repayment"],
            "court": "대법원",
            "case_no": "2013다12345",
            "선고일": "2014-03-27",
        },
        split_preset="default",
    )
    print(f"====적재 완료: 법령 {n1}청크 + 판례 {n2}청크====\n")

    # ── 검색 1: 필터 없이 ──
    print("====쿼리: '전세 보증금 못 받을 때 우선변제 받는 방법' (필터 없음)====")
    _show(search_similar(conn, "전세 보증금 못 받을 때 우선변제 받는 방법", k=5))

    # ── 검색 2: metadata 필터 (계약 전 + 대항력 쟁점) ──
    print("\n====쿼리: '대항력을 갖추려면 어떻게 해야 하나' (stage=pre, issues=opposing_power)====")
    _show(search_similar(conn, "대항력을 갖추려면 어떻게 해야 하나",
                         stage="pre", issues=["opposing_power"], k=3))

    # ── 검색 3: 결론 근거만 (authority=binding) ──
    print("\n====쿼리: '보증금 우선변제' (authorities=binding)====")
    _show(search_similar(conn, "보증금 우선변제", authorities=["binding"], k=3))

    # ── 검색 4: 판례만 + 임의 키 필터 (meta_filter) → 판례 전용 키 그대로 접근 ──
    print("\n====쿼리: '보증금 우선변제 판례' (source_types=['precedent'], meta_filter={'court':'대법원'})====")
    for r in search_similar(
        conn, "보증금 우선변제 판례",
        source_types=["precedent"], meta_filter={"court": "대법원"}, k=3,
    ):
        # 판례에만 있는 키(court/case_no/선고일)를 그대로 꺼내 씀
        print(f"  [{r['similarity']}] {r.get('court')} {r.get('case_no')} "
              f"(선고일 {r.get('선고일')}) | {r.get('content','')[:40]}...")

    conn.close()
    print("\n====테스트 종료====")


if __name__ == "__main__":
    main()