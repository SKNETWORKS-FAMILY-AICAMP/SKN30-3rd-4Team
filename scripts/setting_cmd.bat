@echo off
uv sync
if not exist .env copy .env.example .env
if not exist .streamlit\secrets.toml copy .streamlit\secrets.toml.example .streamlit\secrets.toml
echo 설정 완료. .env와 .streamlit\secrets.toml에 키를 입력하세요.
