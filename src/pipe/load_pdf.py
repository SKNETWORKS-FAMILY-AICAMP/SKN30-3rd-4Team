"""PDF 로드 파이프라인: data/01_raw/pdf → data/02_loaded (체크포인트) → data/03_processed/pdf (Document JSON)

`notebooks/02_load_pdf_final.ipynb`를 스크립트로 옮긴 버전.
텍스트/Vision 하이브리드 라우팅으로 PDF를 로드하되, 페이지 경계에서 표·문단이 잘리지 않도록
직전 페이지의 꼬리 텍스트를 다음 Vision 호출의 프롬프트 컨텍스트로 함께 전달한다.

실행:
    uv run python src/pipe/load_pdf.py

실행 전 아래 FILE_LIST를 원하는 PDF로 수정한다. 진행 상황은 콘솔과 logs/load_pdf_<timestamp>.log에 남는다.
"""

import base64
import hashlib
import json
import logging
import os
import re
import sys
import time
from pathlib import Path

import fitz  # PyMuPDF
import pymupdf4llm
from dotenv import load_dotenv
from langchain_core.documents import Document
from openai import OpenAI, RateLimitError

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")
# max_retries=0: SDK 내부 재시도를 끄고 아래 vision_transcribe의 백오프만 쓴다.
# (내부 재시도 + 우리 재시도가 겹치면 짧은 간격으로 같은 요청을 반복해 429가 더 자주 남)
client = OpenAI(max_retries=0)  # OPENAI_API_KEY 사용

RAW_DIR = ROOT / "data" / "01_raw"
PDF_DIR = RAW_DIR / "pdf"
LOADED_DIR = ROOT / "data" / "02_loaded"
LOADED_DIR.mkdir(parents=True, exist_ok=True)
PROCESSED_DIR = ROOT / "data" / "03_processed"
PDF_PROCESSED_DIR = PROCESSED_DIR / "pdf"
PDF_PROCESSED_DIR.mkdir(parents=True, exist_ok=True)

# ============================== CONFIG ==============================
# 처리할 PDF 파일 목록 — 여기에 파일을 적어서 원하는 파일만 처리한다.
FILE_LIST: list[Path] = [
    PDF_DIR / "2023_주택·상가건물 임대차분쟁조정 사례집.pdf",
    PDF_DIR / "2024_주택·상가건물 임대차분쟁조정 사례집.pdf",
]

# 라우팅·Vision 파라미터 (필요시 조정)
MIN_CHARS = 50        # 페이지 텍스트가 이보다 짧으면 스캔으로 보고 Vision
IMG_AREA_RATIO = 0.4  # 페이지의 40% 이상 덮는 이미지가 있으면 표·도식으로 보고 Vision
DPI = 200             # Vision용 페이지 렌더 해상도
VISION_MODEL = "gpt-4o-mini"  # 표 품질이 더 필요하면 gpt-4o
VISION_THROTTLE = 2.0  # Vision 호출 간 최소 간격(초) — 분당 토큰한도(TPM) 회피
VISION_MAX_RETRY = 6   # 429/일시 오류 시 지수 백오프 재시도 횟수

# 깨진 텍스트 레이어 강제 Vision 판정용 (본문 페이지 숫자 밀도)
CORRUPT_DIGIT_RATIO = 0.012  # 본문 숫자 비율이 이보다 낮으면 ToUnicode 손상(숫자 누락)으로 봄
CORRUPT_SKIP_PAGES = 3       # 앞쪽 표지·목차는 숫자 밀도가 달라 표본에서 제외
FORCE_VISION_DOCS: set[str] = set()  # 파일명을 넣으면 무조건 전체 Vision
SKIP_CORRUPT_CHECK = False           # True면 자동 깨짐 감지 끔

# 페이지 경계 컨텍스트 전달 (표/문단이 페이지를 넘어가며 잘리는 문제 완화)
CONTEXT_TAIL_CHARS = 500  # 직전 페이지 결과에서 다음 Vision 호출에 넘길 꼬리 글자 수

# ============================== LOGGING ==============================
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")  # Windows 콘솔 코드페이지로 인한 한글 깨짐 방지

LOG_DIR = ROOT / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_PATH = LOG_DIR / f"load_pdf_{time.strftime('%Y%m%d_%H%M%S')}.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_PATH, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("load_pdf")

# ============================== HELPERS ==============================
VISION_PROMPT = (
    "다음 이미지는 한국 임대차 관련 공문서 PDF의 한 페이지다. "
    "페이지 내용을 한국어 마크다운으로 그대로 전사하라.\n"
    "규칙:\n"
    "- 표는 반드시 마크다운 표(| 헤더 | ... |)로 구조를 유지한다.\n"
    "- 숫자(기간·금액·조항번호·날짜)를 빠짐없이 정확히 옮긴다.\n"
    "- 머리글·바닥글·페이지번호·로고 등 장식 요소는 제외한다.\n"
    "- 표지·간지처럼 내용이 거의 없으면 빈 문자열만 출력한다(설명·사과 문구 금지).\n"
    "- 원문에 없는 내용을 추가하거나 요약하지 마라.\n"
    "- 전체를 코드블록(```)으로 감싸지 말고, 설명 없이 전사 결과만 출력한다."
)

# 직전 페이지 꼬리를 Vision 프롬프트에 덧붙일 때 쓰는 템플릿.
# 표/문단이 페이지 경계를 넘어갈 때 이어서 전사하도록 유도하고, 중복 전사는 막는다.
CONTEXT_BLOCK_TEMPLATE = (
    "\n\n[직전 페이지 마지막 부분 - 참고용, 그대로 옮기지 말 것]\n"
    "{tail}\n"
    "[참고 끝]\n\n"
    "위 내용은 바로 앞 페이지의 끝부분이다. 지금 이미지가 그 표/문단이 이어지는 부분이면 "
    "자연스럽게 이어서 전사하고, 위 참고 내용과 겹치는 부분(예: 표 헤더, 이미 나온 문장)은 다시 쓰지 마라. "
    "이어지는 내용이 아니면 이 참고 내용은 완전히 무시하고 현재 페이지만 그대로 전사하라."
)

# 모델이 빈/표지 페이지에서 내뱉는 거부·빈내용 문구 → 빈 페이지로 처리
_REFUSAL_RE = re.compile(
    r"(죄송하지만|전사할 수 없|처리할 수 없|빈 문자열|빈 페이지|내용이 없|"
    r"cannot (assist|process|transcribe)|no (content|text))"
)


def _safe_filename(name: str) -> str:
    name = re.sub(r'[\\/:*?"<>|]', "", name)
    return re.sub(r"\s+", "_", name.strip())


def _clean_md(md: str) -> str:
    """모델이 전체를 ```...``` 코드펜스로 감싼 경우 벗겨낸다."""
    md = md.strip()
    if md.startswith("```"):
        md = re.sub(r"^```[a-zA-Z]*\n?", "", md)
        md = re.sub(r"\n?```$", "", md.rstrip())
    return md.strip()


def _tail_context(text: str | None, max_chars: int = CONTEXT_TAIL_CHARS) -> str | None:
    """다음 Vision 호출에 넘길 직전 페이지 꼬리를 뽑는다.

    실패/빈 페이지 placeholder(`<!--`로 시작)는 컨텍스트로 쓸 가치가 없으므로 None 처리한다.
    """
    if not text:
        return None
    text = text.strip()
    if not text or text.startswith("<!--"):
        return None
    return text[-max_chars:]


def file_md5(path) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def is_corrupted_textlayer(doc, skip_pages: int = CORRUPT_SKIP_PAGES,
                           sample_pages: int = 8) -> bool:
    """텍스트 레이어 손상(ToUnicode 매핑 깨짐) 여부를 본문 표본으로 판정.

    이 코퍼스의 깨짐 신호는 '음절 중복'이 아니라 **숫자 누락**이다.
    (예: '위원장 N명 이상 M명 이하' → 숫자가 통째로 사라짐)
    표지·목차(앞 skip_pages장)는 숫자 밀도가 달라 표본에서 제외하고,
    본문에서 한글은 충분한데 숫자 비율이 비정상적으로 낮으면 깨짐으로 본다.
    """
    samples = []
    for idx, page in enumerate(doc):
        if idx < skip_pages:
            continue
        t = page.get_text("text").strip()
        if len(t) >= 100:
            samples.append(t)
        if len(samples) >= sample_pages:
            break
    if not samples:
        return False  # 본문 텍스트가 거의 없음 → 페이지 라우팅(스캔)이 알아서 처리
    joined = "".join(samples)
    hangul = len(re.findall(r"[가-힣]", joined))
    digits = len(re.findall(r"\d", joined))
    return hangul >= 500 and digits / len(joined) < CORRUPT_DIGIT_RATIO


def doc_force_vision(doc, source_name: str) -> bool:
    if source_name in FORCE_VISION_DOCS:
        return True
    if SKIP_CORRUPT_CHECK:
        return False
    return is_corrupted_textlayer(doc)


def page_plan(page, page_area: float):
    """정상 문서의 페이지 라우팅. (needs_vision, text, 최대이미지면적비) 반환."""
    text = page.get_text("text").strip()
    biggest = 0.0
    for im in page.get_image_info():
        x0, y0, x1, y1 = im["bbox"]
        biggest = max(biggest, abs((x1 - x0) * (y1 - y0)) / page_area)
    needs = len(text) < MIN_CHARS or biggest > IMG_AREA_RATIO
    return needs, text, biggest


def plan_pages(doc, force: bool) -> list[str]:
    """페이지별 method('text'|'vision') 목록. Vision 호출 없음 → dry-run 공용.

    - force=True(깨진 사례집): 이미지 유무와 무관하게 전 페이지 vision
    - force=False(일반 문서): 텍스트가 짧거나 큰 이미지가 덮은 페이지만 vision
    """
    if force:
        return ["vision"] * len(doc)
    parea = doc[0].rect.width * doc[0].rect.height
    return ["vision" if page_plan(p, parea)[0] else "text" for p in doc]


def render_png(page, dpi: int = DPI) -> bytes:
    return page.get_pixmap(dpi=dpi).tobytes("png")


def vision_transcribe(png: bytes, prev_tail: str | None = None) -> str:
    """페이지 이미지를 마크다운으로 전사. 429 등은 지수 백오프로 재시도하고,
    빈/표지 페이지의 거부 문구는 빈 안내로 치환하며, 감싼 코드펜스는 제거한다.

    prev_tail이 있으면 직전 페이지 꼬리를 프롬프트 컨텍스트로 덧붙여, 표/문단이
    페이지 경계를 넘어갈 때 잘리지 않고 이어서 전사되도록 한다.
    """
    prompt = VISION_PROMPT
    if prev_tail:
        prompt += CONTEXT_BLOCK_TEMPLATE.format(tail=prev_tail)
    b64 = base64.b64encode(png).decode()
    for attempt in range(VISION_MAX_RETRY):
        try:
            resp = client.chat.completions.create(
                model=VISION_MODEL,
                temperature=0,
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url",
                         "image_url": {"url": f"data:image/png;base64,{b64}",
                                       "detail": "high"}},
                    ],
                }],
            )
            md = _clean_md(resp.choices[0].message.content or "")
            if not md or _REFUSAL_RE.search(md):
                return "<!-- (빈/표지 페이지) -->"
            return md
        except Exception as e:  # noqa: BLE001
            # 429(rate limit)·일시 오류는 백오프 후 재시도, 마지막엔 주석으로 기록
            if attempt == VISION_MAX_RETRY - 1:
                return f"<!-- vision 실패: {e} -->"
            wait = 2 ** attempt  # 1, 2, 4, 8, 16초 기본
            if isinstance(e, RateLimitError):
                retry_after = e.response.headers.get("retry-after")
                if retry_after:
                    wait = max(wait, float(retry_after))
            time.sleep(wait)
    return "<!-- vision 실패: 재시도 초과 -->"


def load_pdf(path, use_vision: bool = True, verbose: bool = True,
             page_range: tuple | None = None):
    """한 PDF를 페이지 단위로 로드. (meta, pages) 반환.

    각 page 레코드는 text와 vision을 **둘 다** 들고 있어 원시 체크포인트와
    최종 Document 병합에 함께 쓰인다:
      {page, method, text, vision}
        - text  : PyMuPDF4LLM 로더 텍스트 (전 페이지 항상 채움)
        - vision: 이미지·표 페이지(또는 깨진 문서)만 LLM 전사, 그 외 None

    직전 페이지 결과의 꼬리(prev_tail)를 추적해 다음 vision 호출에 넘긴다 —
    표/문단이 페이지 경계를 넘어갈 때 내용이 끊기지 않도록 하기 위함.

    page_range: (start, end) 1-based inclusive, e.g. (1, 11). None이면 전 페이지.
    """
    path = Path(path)
    doc = fitz.open(str(path))
    force = doc_force_vision(doc, path.name)
    methods = plan_pages(doc, force)
    # 로더 텍스트는 항상 확보 (깨진 문서도 text.md 산출물엔 담는다)
    if verbose:
        logger.info(f"  로컬 텍스트 추출 중 (PyMuPDF4LLM, {len(doc)}p)...")
    md_chunks = pymupdf4llm.to_markdown(
        str(path), page_chunks=True, show_progress=True)

    # 처리할 페이지 인덱스 결정 (0-based)
    if page_range:
        s, e = page_range[0] - 1, page_range[1]
        indices = list(range(s, min(e, len(doc))))
    else:
        indices = list(range(len(doc)))

    pages, n_text, n_vis = [], 0, 0
    prev_tail: str | None = None
    for i in indices:
        page = doc[i]
        text = (md_chunks[i]["text"].strip()
                if i < len(md_chunks) else page.get_text("text").strip())
        vision = None
        if methods[i] == "vision":
            if use_vision:
                vision = vision_transcribe(render_png(page), prev_tail=prev_tail)
                time.sleep(VISION_THROTTLE)  # TPM 한도 회피
            else:
                vision = "<!-- (vision 보류: use_vision=False) -->"
            n_vis += 1
            prev_tail = _tail_context(vision)
        else:
            n_text += 1
            prev_tail = _tail_context(text)
        pages.append({"page": i + 1, "method": methods[i],
                      "text": text, "vision": vision})
        if verbose:
            logger.info(f"  p{i + 1}/{len(doc)} [{methods[i]}]")
    doc.close()
    meta = {
        "source": path.name,
        "md5": file_md5(path),
        "num_pages": len(pages),
        "n_text": n_text,
        "n_vision": n_vis,
        "forced_vision": force,
        "page_range": page_range,
    }
    if verbose:
        tag = "  [문서 전체 강제 Vision]" if force else ""
        logger.info(f"  완료: {len(pages)}p (text {n_text} / vision {n_vis}){tag}")
    return meta, pages


def _header(meta, kind: str) -> list[str]:
    range_tag = (f" · pages {meta['page_range'][0]}-{meta['page_range'][1]}"
                 if meta.get("page_range") else "")
    return [
        f"# {meta['source']}  ({kind})\n",
        f"> md5: {meta['md5']}  ",
        f"> pages: {meta['num_pages']} (text {meta['n_text']} / vision {meta['n_vision']})"
        f"{' · 강제Vision' if meta.get('forced_vision') else ''}{range_tag}\n",
    ]


def save_text_md(meta, pages, out_dir: Path = LOADED_DIR) -> Path:
    """① 텍스트만 — 전 페이지 로더 텍스트 (LLM 없음)."""
    slug = _safe_filename(os.path.splitext(meta["source"])[0])
    out = _header(meta, "text")
    for p in pages:
        out.append(f"\n<!-- page {p['page']} -->\n")
        out.append(p["text"])
    path = out_dir / f"{slug}.text.md"
    path.write_text("\n".join(out), encoding="utf-8")
    return path


def save_tables_json(meta, pages, out_dir: Path = LOADED_DIR) -> Path:
    """② Vision 전사 페이지 — 표뿐 아니라 해당 페이지 전체(본문+표)가 전사된다.

    page_plan은 이미지 영역만 크롭하지 않고 페이지 전체를 렌더링해 Vision에 넘기므로,
    본문과 표가 섞인 페이지는 이 리스트에 본문+표가 함께 담긴다 (위치 순서는 원본 그대로).
    """
    slug = _safe_filename(os.path.splitext(meta["source"])[0])
    records = [{"page": p["page"], "markdown": p["vision"]}
               for p in pages if p["method"] == "vision" and p["vision"]]
    payload = {
        "source": meta["source"],
        "md5": meta["md5"],
        "forced_vision": meta.get("forced_vision", False),
        "tables": records,
    }
    path = out_dir / f"{slug}.tables.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                    encoding="utf-8")
    return path


def save_all(meta, pages, out_dir: Path = LOADED_DIR) -> dict:
    """원시 체크포인트 2종 저장: text.md / tables.json (data/02_loaded/)."""
    return {
        "text": save_text_md(meta, pages, out_dir),
        "tables": save_tables_json(meta, pages, out_dir),
    }


def pages_to_documents(meta: dict, pages: list[dict]) -> list[Document]:
    """텍스트 페이지=로더, Vision 페이지=전사 결과로 병합해 페이지당 Document 하나씩 생성.

    병합 규칙은 기존 save_final_md와 동일: method=='vision'이고 vision 값이 있으면
    그것으로 페이지 내용을 덮어쓰고, 그 외에는 로더 텍스트를 그대로 쓴다.
    page는 0-based (data/03_processed/PyMuPDF4LLM 등 기존 관례에 맞춤).
    """
    docs = []
    for p in pages:
        content = p["vision"] if (p["method"] == "vision" and p["vision"]) else p["text"]
        docs.append(Document(
            page_content=content,
            metadata={
                "source": meta["source"],
                "page": p["page"] - 1,
                "total_pages": meta["num_pages"],
                "method": p["method"],
                "forced_vision": meta.get("forced_vision", False),
                "md5": meta["md5"],
            },
        ))
    return docs


def save_documents_json(docs: list[Document], out_path: Path) -> Path:
    """Document 리스트를 [{page_content, metadata}] JSON으로 저장 (기존 *_processed.json 관례)."""
    payload = [{"page_content": d.page_content, "metadata": d.metadata} for d in docs]
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return out_path


def load_documents_json(path: Path) -> list[Document]:
    """저장된 *_processed.json을 Document 리스트로 복원 (재실행 스킵 시 사용)."""
    data = json.loads(path.read_text(encoding="utf-8"))
    return [Document(page_content=r["page_content"], metadata=r["metadata"]) for r in data]


def load_pdfs(file_list: list, *, use_vision: bool = True,
              skip_existing: bool = True, page_range: tuple | None = None,
              verbose: bool = True) -> tuple[list[Document], list[dict]]:
    """파일 리스트를 받아 전체를 로드하는 배치 함수.

    재실행 스킵 판정은 data/03_processed/pdf/{slug}_processed.json 존재 여부로 한다
    (존재하면 Vision을 다시 부르지 않고 저장된 Document를 그대로 읽어온다).

    파일 하나가 예외로 실패해도(깨진 PDF 등) 전체를 중단하지 않고 다음 파일로 넘어간다.
    페이지 단위 Vision 실패는 이미 vision_transcribe에서 문구로 남기고 계속 진행하므로
    여기서 잡는 예외는 파일 자체를 못 여는 수준의 실패다.
    (docs, failed) 를 반환하며 failed는 [{"file", "error"}] 형태.
    """
    all_docs, failed = [], []
    total = len(file_list)
    for idx, f in enumerate(file_list, 1):
        f = Path(f)
        slug = _safe_filename(f.stem)
        out_json = PDF_PROCESSED_DIR / f"{slug}_processed.json"
        if skip_existing and out_json.exists():
            if verbose:
                logger.info(f"[{idx}/{total}] skip(이미 있음): {out_json.name}")
            all_docs.extend(load_documents_json(out_json))
            continue
        if verbose:
            logger.info(f"[{idx}/{total}] 처리 중: {f.name}")
        try:
            meta, pages = load_pdf(f, use_vision=use_vision, verbose=verbose, page_range=page_range)
            save_all(meta, pages)  # 원시 체크포인트: text.md/tables.json → data/02_loaded/
            docs = pages_to_documents(meta, pages)
            save_documents_json(docs, out_json)
            all_docs.extend(docs)
            if verbose:
                logger.info(f"  -> {out_json.name} ({len(docs)} pages)")
        except Exception as e:  # noqa: BLE001
            failed.append({"file": f.name, "error": str(e)})
            if verbose:
                logger.warning(f"  !! 실패, 다음 파일로 계속 진행: {f.name}  ({e})")
            continue
    if verbose and failed:
        logger.warning(f"실패 {len(failed)}건:")
        for it in failed:
            logger.warning(f"  - {it['file']}: {it['error']}")
    return all_docs, failed


def main() -> None:
    logger.info(f"OPENAI_API_KEY 설정됨: {bool(os.environ.get('OPENAI_API_KEY'))}")
    logger.info(f"입력: {PDF_DIR.resolve()}")
    logger.info(f"원시 체크포인트 출력: {LOADED_DIR.resolve()}")
    logger.info(f"최종 Document 출력: {PDF_PROCESSED_DIR.resolve()}")
    logger.info(f"file_list: {[p.name for p in FILE_LIST]}")
    logger.info(f"로그 파일: {LOG_PATH}")

    docs, failed = load_pdfs(FILE_LIST, use_vision=True, skip_existing=True)

    logger.info(f"완료: 총 {len(docs)}개 Document (파일 {len(FILE_LIST)}개, 실패 {len(failed)}개)")
    failed_names = {it["file"] for it in failed}
    for f in FILE_LIST:
        f = Path(f)
        if f.name in failed_names:
            logger.warning(f"[FAIL] {f.name}")
            continue
        n_pages = sum(1 for d in docs if d.metadata["source"] == f.name)
        logger.info(f"[OK] {f.name}  ({n_pages} pages)")


if __name__ == "__main__":
    main()
