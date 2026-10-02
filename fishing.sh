#!/bin/bash
# 홈페이지 스크래핑 → GitHub 푸시
cd "$(dirname "$0")"
exec python3 run_collection.py
