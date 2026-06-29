# 전·월세 분쟁 팩트체커

> 계약 전 예방부터 계약 후 갈등까지, 법으로 따져주는 자취생 도우미

세입자(사회초년생)를 위한 챗봇 서비스입니다.
**계약 전** 등기부·특약을 분석해 전세사기 위험을 짚어주고, **계약 후**엔 이사 팁·수리 분쟁·보증금 갈등을 도와줍니다.

## 주요 기능
- 🛡️ **계약 전 챗봇** — 등기부·특약 분석, 전세사기 위험 진단
- 🤝 **계약 후 챗봇** — 이사 팁, 수리 분쟁, 보증금 갈등 대응
- 📄 **PDF 업로드** — 계약서·등기부 문서 분석
- 🖼️ **이미지 정보 읽기(OCR)** — 사진 속 문서 내용 추출

## 기술 스택

![Python](https://img.shields.io/badge/Python-3776AB?style=for-the-badge&logo=python&logoColor=white)
![Streamlit](https://img.shields.io/badge/Streamlit-FF4B4B?style=for-the-badge&logo=streamlit&logoColor=white)
![LangChain](https://img.shields.io/badge/LangChain-1C3C3C?style=for-the-badge&logo=langchain&logoColor=white)
![LangGraph](https://img.shields.io/badge/LangGraph-1C3C3C?style=for-the-badge&logoColor=white)
![OpenAI](https://img.shields.io/badge/OpenAI-412991?style=for-the-badge&logo=openai&logoColor=white)
![LangSmith](https://img.shields.io/badge/LangSmith-F5A623?style=for-the-badge&logoColor=white)
![uv](https://img.shields.io/badge/uv-DE5FE9?style=for-the-badge&logo=uv&logoColor=white)

## 폴더 구조

```
.
├── data/
│   ├── 01_raw/          # 원본 문서 (PDF)
│   ├── 02_loaded/       # 문서 로드 결과 (텍스트 추출)
│   ├── 03_processed/    # 전처리 결과 (정제·정규화)
│   ├── 04_chunks/       # 청킹 결과 체크포인트
│   └── 05_vectordb/     # Chroma 벡터스토어
├── src/                 # 핵심 로직
├── app/                 # Streamlit 챗봇 UI
├── eval/                # LangChain / LangGraph 평가
└── docs/                # 기획서·설계 문서
```

## 시작하기 (Mac / Windows 공통)
```bash
# 1. 의존성 설치 (uv가 .venv와 lock 기반으로 동일 환경 구성)
uv sync

# 2. 환경 변수 설정 — 예시 파일을 복사해 본인 키 입력
cp .env.example .env      # Windows(PowerShell): copy .env.example .env

# 3. 앱 실행 (코드 추가 후)
uv run streamlit run app/main.py
```
