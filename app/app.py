"""
app.py — 전·월세 분쟁 팩트체커 · 메신저 스타일 Streamlit 초안
실행:  streamlit run app.py

핵심: setup_graph() 가 graph(app) 를 프로세스당 1회 빌드해 @st.cache_resource 로 캐시.
      → MemorySaver 단일 인스턴스 유지(멀티턴 보존), 매 rerun 마다 재빌드 안 함.
"""

import os
import sys
import uuid
import html
import tempfile

import streamlit as st
from dotenv import load_dotenv

load_dotenv()

# graph.py / vs_method.py 가 있는 src/core 를 위로 거슬러 올라가며 자동 탐색
# (app.py 가 레포 루트든 app/ 하위든 모두 대응)
def _find_src_core():
    d = os.path.dirname(os.path.abspath(__file__))
    for _ in range(6):
        cand = os.path.join(d, "src", "core")
        if os.path.isfile(os.path.join(cand, "graph.py")):
            return cand
        parent = os.path.dirname(d)
        if parent == d:            # 루트 도달
            break
        d = parent
    return None


SRC_DIR = _find_src_core()

# graph / vs_method 가 os.environ 으로 읽는 키들 (st.secrets → 환경변수로 승격)
SECRET_KEYS = ("OPENAI_API_KEY", "DB_URL", "LANGCHAIN_TRACING_V2", "LANGSMITH_API_KEY")


def _secret(key: str, default=None):
    """우선순위: st.secrets(Cloud/.streamlit/secrets.toml) → 환경변수(.env)."""
    try:
        if key in st.secrets:            # secrets 파일 없으면 예외 → 폴백
            return st.secrets[key]
    except Exception:
        pass
    return os.getenv(key, default)


def _load_secrets_into_env() -> None:
    """백엔드(graph/vs_method)가 os.environ 을 읽으므로, secrets 값을 환경변수로 밀어넣는다."""
    load_dotenv()  # 로컬 .env 폴백
    for k in SECRET_KEYS:
        val = _secret(k)
        if val is not None and val != "":
            os.environ[k] = str(val)


# ══════════════════════════════════════════════
# 그래프 셋업 (프로세스당 1회 · 캐시)
# ══════════════════════════════════════════════
@st.cache_resource(show_spinner="⚙️ 그래프 초기화 중…")
def setup_graph() -> dict:
    """
    graph 모듈(app/run_turn)을 1회 로드·빌드해 캐시한다.
    반환: {"module", "ready", "error", "db_ready"}  (실패해도 UI 는 뜨도록 예외 대신 dict)

    - st.secrets → os.environ 승격 후 import (백엔드는 os.environ 만 사용)
    - @st.cache_resource: 세션·rerun across 로 동일 객체 공유 → MemorySaver 하나로 멀티턴 유지
    - 필수값 확인 → import graph(빌드) → DB 스키마 보장(첫 질문 지연/실패 방지)
    """
    result = {"module": None, "ready": False, "error": None, "db_ready": False}
    try:
        _load_secrets_into_env()
        missing = [k for k in ("OPENAI_API_KEY", "DB_URL") if not os.getenv(k)]
        if missing:
            result["error"] = f"secrets/.env 누락: {', '.join(missing)}"
            return result

        if SRC_DIR is None:
            result["error"] = ("src/core/graph.py 를 찾지 못했습니다. "
                               "app.py 위치 또는 폴더 구조를 확인하세요.")
            return result
        if SRC_DIR not in sys.path:
            sys.path.insert(0, SRC_DIR)     # graph 안의 `import vs_method` 도 여기서 해결

        import graph                         # 모듈 로드 시 app = build_app() 빌드
        result["module"] = graph
        result["ready"] = True

        # DB 스키마 보장 (선택) — 실패해도 앱은 뜨게 하고 경고만
        try:
            import vs_method
            vs_method.ensure_schema(graph.conn())
            result["db_ready"] = True
        except Exception as e:
            result["error"] = f"DB 준비 경고(검색이 안 될 수 있음): {e}"

    except Exception as e:
        result["error"] = f"그래프 로드 실패: {e}"
    return result


# ──────────────────────────────────────────────
# 페이지 · 스타일
# ──────────────────────────────────────────────
st.set_page_config(page_title="전·월세 분쟁 팩트체커", page_icon="⚖️", layout="centered")

st.markdown("""
<style>
  .stApp { background: #a9c6e2; }
  .block-container { max-width: 520px; padding-top: 3rem; padding-bottom: 0; }
  .app-header { background:#6f9fd8; color:#fff; padding:14px 18px; border-radius:14px;
                font-weight:700; font-size:18px; line-height:1.4; margin-bottom:10px;
                display:flex; align-items:center; justify-content:center; }
  .chat { display:flex; flex-direction:column; gap:12px; padding:6px 2px 96px; }
  .row { display:flex; align-items:flex-end; gap:8px; }
  .row.user { justify-content:flex-end; }
  .row.bot  { justify-content:flex-start; }
  .avatar { width:34px; height:34px; border-radius:50%; background:#c7dbf0;
            display:flex; align-items:center; justify-content:center; font-size:18px; flex:none; }
  .bubble { max-width:74%; padding:10px 14px; border-radius:16px; font-size:15px;
            line-height:1.55; box-shadow:0 1px 1px rgba(0,0,0,.08); word-break:break-word; }
  .bubble.bot  { background:#d3e2f6; color:#1e2a3a; border-top-left-radius:4px; }
  .bubble.user { background:#f5df4e; color:#3b3200; border-top-right-radius:4px; }
  /* 전송 버튼만 노랑 (첨부 📎 버튼은 기본색 유지) */
  [data-testid="stChatInputSubmitButton"] { background:#f5df4e !important; border:none !important; }
  [data-testid="stChatInputSubmitButton"] svg { color:#3b3200 !important; fill:#3b3200 !important; }
  [data-testid="stChatInput"] { background: transparent; }
</style>
""", unsafe_allow_html=True)


# ──────────────────────────────────────────────
# 백엔드 준비
# ──────────────────────────────────────────────
gs = setup_graph()
backend = gs["module"]


# ──────────────────────────────────────────────
# 세션 상태
# ──────────────────────────────────────────────
if "thread_id" not in st.session_state:
    st.session_state.thread_id = str(uuid.uuid4())
if "messages" not in st.session_state:
    st.session_state.messages = []
if "stage" not in st.session_state:
    st.session_state.stage = "pre"


# ──────────────────────────────────────────────
# 상단: 헤더 + 계약 단계 토글 + 새 대화
# ──────────────────────────────────────────────
st.markdown('<div class="app-header">⚖️ 전·월세 분쟁 팩트체커</div>', unsafe_allow_html=True)

col_stage, col_new = st.columns([3, 1])
with col_stage:
    label = st.radio(
        "계약 단계", ["계약 전 (예방)", "계약 후 (분쟁)"],
        horizontal=True, label_visibility="collapsed",
        index=0 if st.session_state.stage == "pre" else 1,
    )
    st.session_state.stage = "pre" if label.startswith("계약 전") else "post"
with col_new:
    if st.button("새 대화", use_container_width=True):
        st.session_state.thread_id = str(uuid.uuid4())
        st.session_state.messages = []
        st.rerun()

if gs["error"]:
    st.warning(gs["error"])
if not gs["ready"]:
    st.info("백엔드 미연결 — UI 미리보기 모드입니다. (.env 설정 후 새로고침)")


# ──────────────────────────────────────────────
# 채팅 렌더링 (커스텀 말풍선)
# ──────────────────────────────────────────────
def _bubble(role: str, text: str) -> str:
    safe = html.escape(text).replace("\n", "<br>")
    if role == "user":
        return f'<div class="row user"><div class="bubble user">{safe}</div></div>'
    return (f'<div class="row bot"><div class="avatar">⚖️</div>'
            f'<div class="bubble bot">{safe}</div></div>')


GREETING = {
    "pre":  "안녕하세요! 계약 전 단계예요. 등기부·특약이 걱정되면 서류를 올리거나, "
            "전세사기·보증금 관련해 궁금한 점을 물어보세요.",
    "post": "안녕하세요! 계약 후 단계예요. 수리 책임, 보증금 반환, 계약 갱신 등 "
            "분쟁 상황을 알려주시면 법령·판례 근거로 안내할게요.",
}

rows = [_bubble("assistant", GREETING[st.session_state.stage])]
for m in st.session_state.messages:
    rows.append(_bubble(m["role"], m["content"]))
st.markdown(f'<div class="chat">{"".join(rows)}</div>', unsafe_allow_html=True)


# ──────────────────────────────────────────────
# 입력 → 그래프 호출 → 응답
# ──────────────────────────────────────────────
def answer_of(question: str, doc_path: str | None) -> str:
    if backend is None:
        return f"⚠️ 백엔드 미연결로 답변할 수 없습니다. ({gs['error']})"
    try:
        return backend.run_turn(
            st.session_state.thread_id, question,
            stage=st.session_state.stage,
            has_document=bool(doc_path),
            document_path=doc_path,
        )
    except Exception as e:
        return f"⚠️ 처리 중 오류: {e}"


# 하단 채팅바: 📎 이미지/PDF 첨부 + 전송 (Streamlit ≥1.43). 구버전이면 텍스트 전용 폴백.
try:
    submitted = st.chat_input(
        "메시지를 입력하세요",
        accept_file=True,
        file_type=["png", "jpg", "jpeg", "pdf"],
    )
    _attach_ok = True
except TypeError:
    submitted = st.chat_input("메시지를 입력하세요")
    _attach_ok = False

if submitted:
    # 반환형 정규화: 첨부 지원 시 객체(text/files), 아니면 문자열
    if _attach_ok:
        text = (submitted.text or "").strip()
        files = submitted.files or []
    else:
        text, files = str(submitted).strip(), []

    # 첨부 파일 저장 → document_path
    doc_path = None
    if files:
        up = files[0]
        doc_path = os.path.join(tempfile.gettempdir(), up.name)
        with open(doc_path, "wb") as fp:
            fp.write(up.getbuffer())

    # 사용자 말풍선(첨부 표시 포함)
    shown = text or "(파일 첨부)"
    if files:
        shown += f"　📎 {files[0].name}"
    st.session_state.messages.append({"role": "user", "content": shown})

    with st.spinner("근거를 찾는 중…"):
        reply = answer_of(text or "업로드한 서류를 분석해줘", doc_path)
    st.session_state.messages.append({"role": "assistant", "content": reply})
    st.rerun()