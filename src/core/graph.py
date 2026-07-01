"""
graph.py
전·월세 분쟁 팩트체커 — LangGraph 상태 그래프

구조:
  parent: START → (entry_router) → pre_contract | post_contract → 공통 응답 파이프라인 → END
  pre subgraph : 문서 여부 라우팅 → OCR·서류분석·전세가율·위험판정 | 계약 질의 분석 → 컨텍스트
  post subgraph: 쟁점 분류 → 컨텍스트
  공통 파이프라인: retrieve → grade(→쿼리 재작성 루프) → generate → verify(→재생성 루프) → 법적 고지

검색은 vs_method.search_similar(kb_chunks / pgvector) 사용.
멀티턴은 그래프 내부 루프가 아니라 MemorySaver + thread_id 로 상태를 유지하고 매 턴 재호출한다.

설치:
  pip install langgraph langchain-openai "psycopg[binary]" pgvector
환경변수:
  OPENAI_API_KEY, DB_URL (vs_method 가 사용)
"""

from __future__ import annotations

import json
from typing import Optional

from typing_extensions import TypedDict, Annotated

from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages
from langgraph.checkpoint.memory import MemorySaver
from langchain_openai import ChatOpenAI
from langchain_core.messages import AIMessage
from dotenv import load_dotenv
import os

load_dotenv()

import vs_method  # search_similar / get_conn (kb_chunks)

# ──────────────────────────────────────────────
# LLM / 검색 연결
# ──────────────────────────────────────────────
llm = ChatOpenAI(model="gpt-4.1-mini", temperature=0)

MAX_RETRIEVAL_ATTEMPTS = 2
MAX_VERIFY_ATTEMPTS = 2

_conn = None
def conn():
    global _conn
    if _conn is None:
        _conn = vs_method.get_conn()
    return _conn


def _llm_json(prompt: str) -> dict:
    """LLM 응답을 JSON 으로 강제 파싱. 실패 시 빈 dict."""
    raw = llm.invoke(prompt + "\n\nJSON 객체만 출력. 설명·마크다운 금지.").content
    raw = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


# ──────────────────────────────────────────────
# 공유 State
# ──────────────────────────────────────────────
class FactCheckState(TypedDict, total=False):
    # 입력 / 세션
    stage: str                       # 'pre' | 'post'
    question: str
    has_document: bool
    document_path: Optional[str]
    messages: Annotated[list, add_messages]
    # 계약 전 산출물
    document_text: Optional[str]
    findings: Optional[dict]          # 근저당·선순위·보증금 등
    risk_result: Optional[dict]       # {level, ratio, reasons}
    # 검색 / 생성 공통
    query: str
    issues: list
    retrieved: list
    retrieval_attempts: int
    answer: str
    verify_attempts: int


# ══════════════════════════════════════════════
# 외부 파이프라인 스텁 (남진님 기존 모듈에 연결)
# ══════════════════════════════════════════════
def run_ocr(document_path: str) -> str:
    # TODO: pdf2image + pytesseract 한글 OCR 파이프라인 연결
    raise NotImplementedError("OCR 파이프라인 연결 필요")


def fetch_market_price(address: str) -> int:
    # TODO: 실시간 시세 API 연결 → 매매 시세(원) 반환
    raise NotImplementedError("실시간 시세 API 연결 필요")


# ══════════════════════════════════════════════
# 계약 전 서브그래프
# ══════════════════════════════════════════════
def pre_entry_router(state: FactCheckState) -> str:
    return "doc" if state.get("has_document") else "question"


def ocr_extract(state: FactCheckState) -> dict:
    return {"document_text": run_ocr(state["document_path"])}


def analyze_document(state: FactCheckState) -> dict:
    """등기부·계약서 텍스트에서 근저당·선순위·보증금·특약 추출 (LLM)."""
    data = _llm_json(
        "다음 등기부/계약서에서 위험 요소를 추출해라.\n"
        'keys: deposit(보증금·정수), senior_debt(선순위 채권액·정수), '
        'address(문자열), special_terms(특약 리스트), flags(위험 특약 리스트).\n\n'
        f"{state['document_text'][:4000]}"
    )
    return {"findings": data}


def calc_jeonse_ratio(state: FactCheckState) -> dict:
    """결정론적 계산: (보증금 + 선순위채권) / 매매시세.  LLM 아님."""
    f = state.get("findings") or {}
    deposit = int(f.get("deposit") or 0)
    senior = int(f.get("senior_debt") or 0)
    price = fetch_market_price(f.get("address", "")) or 1
    ratio = round((deposit + senior) / price, 3)
    return {"findings": {**f, "jeonse_ratio": ratio, "market_price": price}}


def assess_risk(state: FactCheckState) -> dict:
    """결정론적 임계값 판정."""
    f = state.get("findings") or {}
    ratio = f.get("jeonse_ratio", 0)
    reasons, level = [], "low"
    if ratio >= 0.8:
        level, r = "high", f"전세가율 {ratio:.0%} (깡통전세 위험 구간)"
        reasons.append(r)
    elif ratio >= 0.7:
        level = "medium"
        reasons.append(f"전세가율 {ratio:.0%} (경계 구간)")
    if f.get("senior_debt"):
        reasons.append("선순위 근저당 존재 → 우선변제 순위 확인 필요")
    return {"risk_result": {"level": level, "ratio": ratio, "reasons": reasons}}


def analyze_pre_query(state: FactCheckState) -> dict:
    """질문에서 검색 쿼리·쟁점 태그 추출 (LLM)."""
    data = _llm_json(
        "다음 세입자 질문(계약 전)에서 검색용 쿼리와 쟁점 태그를 뽑아라.\n"
        'keys: query(핵심 검색 문장), issues(태그 리스트: '
        'deposit,opposing_power,priority_repayment,fraud,special_terms 중).\n\n'
        f"질문: {state.get('question','')}"
    )
    return {"query": data.get("query", state.get("question", "")),
            "issues": data.get("issues", [])}


def build_pre_context(state: FactCheckState) -> dict:
    """위험 판정 결과가 있으면 검색 쿼리·쟁점에 병합. stage 고정."""
    out = {"stage": "pre", "retrieval_attempts": 0, "verify_attempts": 0}
    risk = state.get("risk_result")
    if risk and risk["reasons"]:
        out["query"] = state.get("query", "") + " / " + "; ".join(risk["reasons"])
        out["issues"] = list({*state.get("issues", []), "fraud", "priority_repayment"})
    return out


def build_pre_graph():
    g = StateGraph(FactCheckState)
    g.add_node("ocr_extract", ocr_extract)
    g.add_node("analyze_document", analyze_document)
    g.add_node("calc_jeonse_ratio", calc_jeonse_ratio)
    g.add_node("assess_risk", assess_risk)
    g.add_node("analyze_pre_query", analyze_pre_query)
    g.add_node("build_pre_context", build_pre_context)

    g.add_conditional_edges(START, pre_entry_router,
                            {"doc": "ocr_extract", "question": "analyze_pre_query"})
    g.add_edge("ocr_extract", "analyze_document")
    g.add_edge("analyze_document", "calc_jeonse_ratio")
    g.add_edge("calc_jeonse_ratio", "assess_risk")
    g.add_edge("assess_risk", "analyze_pre_query")   # 문서 경로도 질의 분석으로 합류
    g.add_edge("analyze_pre_query", "build_pre_context")
    g.add_edge("build_pre_context", END)
    return g.compile()


# ══════════════════════════════════════════════
# 계약 후 서브그래프
# ══════════════════════════════════════════════
def classify_issue(state: FactCheckState) -> dict:
    """수리·분쟁 질문에서 쟁점 분류 + 검색 쿼리 (LLM)."""
    data = _llm_json(
        "다음 세입자 질문(계약 후)에서 검색 쿼리와 쟁점 태그를 뽑아라.\n"
        'keys: query(핵심 검색 문장), issues(태그 리스트: '
        'deposit,repair,contract_renewal,eviction,maintenance_duty 중).\n\n'
        f"질문: {state.get('question','')}"
    )
    return {"stage": "post",
            "query": data.get("query", state.get("question", "")),
            "issues": data.get("issues", []),
            "retrieval_attempts": 0, "verify_attempts": 0}


def build_post_graph():
    g = StateGraph(FactCheckState)
    g.add_node("classify_issue", classify_issue)
    g.add_edge(START, "classify_issue")
    g.add_edge("classify_issue", END)
    return g.compile()


# ══════════════════════════════════════════════
# 공통 응답 파이프라인
# ══════════════════════════════════════════════
def retrieve(state: FactCheckState) -> dict:
    """pgvector 하이브리드 검색 (stage + issue 필터). binding·persuasive 함께 가져와 뒤에서 층 분리."""
    hits = vs_method.search_similar(
        conn(),
        query=state["query"],
        stage=state["stage"],
        issues=state.get("issues") or None,
        k=8,
        min_score=0.30,
    )
    return {"retrieved": hits}


def grade_documents(state: FactCheckState) -> dict:
    """검색 결과가 질문을 커버하는지 판정 (관련성 평가)."""
    ctx = "\n".join(f"- ({h['authority']}) {h['content'][:120]}" for h in state["retrieved"])
    v = _llm_json(
        "검색된 조항이 질문에 답하기에 충분한가?\n"
        'keys: sufficient(bool), gap(부족하면 무엇이 빠졌는지 한 문장).\n\n'
        f"질문: {state['query']}\n조항:\n{ctx or '(없음)'}"
    )
    return {"_grade": v}  # 임시 채널 (라우터에서 읽고 버림)


def grade_router(state: FactCheckState) -> str:
    v = state.get("_grade", {})
    if v.get("sufficient"):
        return "generate"
    if state.get("retrieval_attempts", 0) < MAX_RETRIEVAL_ATTEMPTS:
        return "rewrite"
    return "generate"  # 상한 초과 → 있는 근거로 진행(부족 고지)


def rewrite_query(state: FactCheckState) -> dict:
    """부족한 부분을 반영해 쿼리 재작성 후 재검색 루프."""
    gap = state.get("_grade", {}).get("gap", "")
    new_q = llm.invoke(
        f"원 질문: {state['query']}\n부족한 점: {gap}\n"
        "검색이 잘 되도록 쿼리를 한 문장으로 재작성해라. 문장만 출력."
    ).content.strip()
    return {"query": new_q, "retrieval_attempts": state.get("retrieval_attempts", 0) + 1}


def generate(state: FactCheckState) -> dict:
    """근거 기반 답변 생성. 결론은 binding, 사례는 persuasive 로 층 분리."""
    binding = [h for h in state["retrieved"] if h["authority"] == "binding"]
    persuasive = [h for h in state["retrieved"] if h["authority"] == "persuasive"]
    ref = [h for h in state["retrieved"] if h["authority"] == "reference"]

    def fmt(hs):
        return "\n".join(
            f"- {h.get('law_name') or h['doc_title']} {h.get('article') or ''}: {h['content'][:200]}"
            for h in hs) or "(없음)"

    risk = state.get("risk_result")
    risk_txt = f"\n[위험 진단] {risk['level']} / {'; '.join(risk['reasons'])}" if risk else ""

    answer = llm.invoke(
        "너는 세입자를 돕는 법률 정보 도우미다. 아래 근거만 사용해 답하라.\n"
        "규칙: 결론의 법적 근거는 반드시 [법령·판례]에서 인용(법령명·조항 명시). "
        "[사례]는 '이런 경우 이렇게 판단된 적 있다'는 참고로만. 근거에 없는 내용은 단정하지 말 것.\n"
        f"{risk_txt}\n\n"
        f"[법령·판례]\n{fmt(binding)}\n\n[사례]\n{fmt(persuasive)}\n\n[실무 참고]\n{fmt(ref)}\n\n"
        f"질문: {state.get('question','')}"
    ).content.strip()
    return {"answer": answer}


def verify(state: FactCheckState) -> dict:
    """답변이 검색 근거에 충실한지(환각 여부) 검증."""
    ctx = "\n".join(f"- {h['content'][:200]}" for h in state["retrieved"])
    v = _llm_json(
        "답변이 아래 근거에 충실한가? 근거에 없는 사실 단정이 있으면 faithful=false.\n"
        'keys: faithful(bool), problem(문제 있으면 한 문장).\n\n'
        f"근거:\n{ctx}\n\n답변:\n{state['answer']}"
    )
    return {"_verify": v}


def verify_router(state: FactCheckState) -> str:
    v = state.get("_verify", {})
    if v.get("faithful"):
        return "notice"
    if state.get("verify_attempts", 0) < MAX_VERIFY_ATTEMPTS:
        return "regenerate"
    return "notice"  # 상한 초과 → 고지에 한계 명시


def bump_verify(state: FactCheckState) -> dict:
    return {"verify_attempts": state.get("verify_attempts", 0) + 1}


DISCLAIMER = ("\n\n---\n※ 본 답변은 법률 정보 제공이며 변호사의 법률 자문이 아닙니다. "
              "구체적 사안은 대한법률구조공단(132) 또는 변호사 상담을 권장합니다.")


def legal_notice(state: FactCheckState) -> dict:
    """법적 고지 부착 후 최종 답변 확정 + 세션 기록."""
    final = state["answer"] + DISCLAIMER
    return {"answer": final, "messages": [AIMessage(content=final)]}


# ══════════════════════════════════════════════
# 부모 그래프
# ══════════════════════════════════════════════
def entry_router(state: FactCheckState) -> str:
    return "pre_contract" if state.get("stage") == "pre" else "post_contract"


def build_app():
    g = StateGraph(FactCheckState)

    # 서브그래프를 노드로 장착 (State 공유)
    g.add_node("pre_contract", build_pre_graph())
    g.add_node("post_contract", build_post_graph())

    # 공통 파이프라인
    g.add_node("retrieve", retrieve)
    g.add_node("grade", grade_documents)
    g.add_node("rewrite_query", rewrite_query)
    g.add_node("generate", generate)
    g.add_node("verify", verify)
    g.add_node("bump_verify", bump_verify)
    g.add_node("legal_notice", legal_notice)

    # 진입 라우팅
    g.add_conditional_edges(START, entry_router,
                            {"pre_contract": "pre_contract", "post_contract": "post_contract"})
    g.add_edge("pre_contract", "retrieve")
    g.add_edge("post_contract", "retrieve")

    # 검색 → 관련성 평가 (→ 쿼리 재작성 루프)
    g.add_edge("retrieve", "grade")
    g.add_conditional_edges("grade", grade_router,
                            {"generate": "generate", "rewrite": "rewrite_query"})
    g.add_edge("rewrite_query", "retrieve")

    # 생성 → 충실성 검증 (→ 재생성 루프)
    g.add_edge("generate", "verify")
    g.add_conditional_edges("verify", verify_router,
                            {"notice": "legal_notice", "regenerate": "bump_verify"})
    g.add_edge("bump_verify", "generate")
    g.add_edge("legal_notice", END)

    return g.compile(checkpointer=MemorySaver())


app = build_app()


# ──────────────────────────────────────────────
# 한 턴 실행 헬퍼 (멀티턴 = 같은 thread_id 로 재호출)
# ──────────────────────────────────────────────
def run_turn(thread_id: str, question: str, *, stage: str,
             has_document: bool = False, document_path: str | None = None) -> str:
    cfg = {"configurable": {"thread_id": thread_id}}
    out = app.invoke(
        {"question": question, "stage": stage,
         "has_document": has_document, "document_path": document_path},
        config=cfg,
    )
    return out["answer"]


if __name__ == "__main__":
    # 계약 후 · 보증금 분쟁
    print(run_turn("user-42", "집주인이 보증금을 안 돌려줘요", stage="post"))
    # 같은 thread → 세션 유지 (stage 기억)
    print(run_turn("user-42", "그럼 내용증명은 어떻게 보내요?", stage="post"))
