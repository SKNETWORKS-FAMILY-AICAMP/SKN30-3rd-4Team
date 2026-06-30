#!/bin/bash
uv sync
cp -n .env.example .env
cp -n .streamlit/secrets.toml.example .streamlit/secrets.toml
echo "설정 완료. .env와 .streamlit/secrets.toml에 키를 입력하세요."
