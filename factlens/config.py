"""환경 설정. 인증키는 DART_API_KEY 환경변수로만 읽는다."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

API_KEY_ENV = "DART_API_KEY"
BASE_URL = "https://opendart.fss.or.kr/api"
GUIDE_URL = "https://opendart.fss.or.kr/guide/main.do"

DATA_DIR = ROOT / "data"
CACHE_DIR = DATA_DIR / "cache"
EXPORT_DIR = DATA_DIR / "exports"
DB_PATH = DATA_DIR / "factlens.db"
ALIAS_PATH = DATA_DIR / "corp_alias.json"

CORPCODE_TTL_DAYS = 7
# 공식 문서상 개발계정 일일 호출 한도가 존재한다(수치는 정책 변경 가능).
DAILY_CALL_LIMIT_HINT = 20000


def api_key() -> str:
    """인증키를 반환한다. 없으면 예외. 값은 절대 로그로 내보내지 않는다."""
    key = (os.getenv(API_KEY_ENV) or "").strip()
    if not key:
        raise RuntimeError(
            f"{API_KEY_ENV} 가 설정되지 않았습니다. 프로젝트 루트의 .env 파일에 인증키를 입력하세요."
        )
    return key


def has_api_key() -> bool:
    return bool((os.getenv(API_KEY_ENV) or "").strip())


def key_status() -> str:
    """키 값 자체를 노출하지 않는 상태 문자열."""
    key = (os.getenv(API_KEY_ENV) or "").strip()
    if not key:
        return "미설정"
    note = "" if len(key) == 40 else f" (길이 {len(key)}자 - 통상 40자)"
    return "설정됨" + note


def ensure_dirs() -> None:
    for d in (DATA_DIR, CACHE_DIR, EXPORT_DIR):
        d.mkdir(parents=True, exist_ok=True)
