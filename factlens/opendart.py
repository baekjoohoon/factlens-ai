"""OpenDART 오픈 API 클라이언트.

사용 엔드포인트
- corpCode.xml            : 고유번호(corp_code) 전체 목록 (ZIP 안에 CORPCODE.xml)
- fnlttSinglAcntAll.json  : 단일회사 전체 재무제표 (사업보고서/반기/분기)

주의
- crtfc_key 는 요청 파라미터로만 쓰고, 로그/예외 메시지/저장 데이터에 남기지 않는다.
- requests 예외 문자열에는 요청 URL(키 포함)이 들어가므로 그대로 쓰지 않는다.
"""
from __future__ import annotations

import io
import json
import time
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass, field
from typing import Any

import requests

from . import config

STATUS_MESSAGES = {
    "000": "정상",
    "010": "등록되지 않은 인증키",
    "011": "사용할 수 없는 인증키",
    "012": "접근할 수 없는 IP",
    "013": "조회된 데이터가 없습니다",
    "020": "요청 제한을 초과했습니다",
    "021": "조회 가능한 회사 수를 초과했습니다",
    "100": "요청 필드 형식이 올바르지 않습니다",
    "101": "부적절한 접근입니다",
    "800": "시스템 점검 중입니다",
    "900": "정의되지 않은 오류입니다",
}

NO_DATA = "013"
AUTH_ERRORS = {"010", "011", "012"}

REPRT_CODES = {
    "11011": "사업보고서",
    "11012": "반기보고서",
    "11013": "1분기보고서",
    "11014": "3분기보고서",
}

FS_DIVS = {"CFS": "연결재무제표", "OFS": "별도재무제표"}


class OpenDartError(RuntimeError):
    """API 오류. code 는 OpenDART status 코드 또는 내부 코드."""

    def __init__(self, code: str, message: str, *, retryable: bool = False):
        self.code = code
        self.message = message
        self.retryable = retryable
        super().__init__(f"[{code}] {message}")


@dataclass(frozen=True)
class Corp:
    code: str
    name: str
    stock_code: str = ""


@dataclass
class FsResponse:
    corp_code: str
    year: int
    reprt_code: str
    fs_div: str
    rows: list[dict[str, Any]] = field(default_factory=list)

    @property
    def label(self) -> str:
        return f"{self.year} {REPRT_CODES.get(self.reprt_code, self.reprt_code)} {FS_DIVS.get(self.fs_div, self.fs_div)}"


class OpenDartClient:
    def __init__(
        self,
        api_key: str | None = None,
        *,
        timeout: float = 20.0,
        max_retries: int = 3,
        session: requests.Session | None = None,
    ):
        self._key = api_key or config.api_key()
        self._timeout = timeout
        self._max_retries = max_retries
        self._session = session or requests.Session()
        self.call_count = 0

    def __repr__(self) -> str:  # 키 노출 방지
        return f"OpenDartClient(calls={self.call_count})"

    # ------------------------------------------------------------------ http
    def _request(self, endpoint: str, params: dict[str, Any]) -> requests.Response:
        url = f"{config.BASE_URL}/{endpoint}"
        payload = {**params, "crtfc_key": self._key}
        last_error: OpenDartError | None = None

        for attempt in range(self._max_retries + 1):
            try:
                resp = self._session.get(url, params=payload, timeout=self._timeout)
            except requests.RequestException as exc:
                last_error = OpenDartError(
                    "NETWORK", f"네트워크 오류({type(exc).__name__})", retryable=True
                )
            else:
                self.call_count += 1
                if resp.status_code == 429:
                    last_error = OpenDartError("HTTP_429", "호출 한도 초과(HTTP 429)", retryable=True)
                elif resp.status_code >= 500:
                    last_error = OpenDartError(
                        f"HTTP_{resp.status_code}", "서버 오류", retryable=True
                    )
                elif resp.status_code != 200:
                    raise OpenDartError(f"HTTP_{resp.status_code}", "요청이 거부되었습니다")
                else:
                    return resp

            if attempt < self._max_retries:
                time.sleep(1.5 * (2**attempt))

        assert last_error is not None
        raise last_error

    def _error_from_response(self, resp: requests.Response) -> OpenDartError:
        try:
            data = resp.json()
            code = str(data.get("status", "")).strip()
            message = str(data.get("message", "")).strip()
            return OpenDartError(code or "UNKNOWN", message or STATUS_MESSAGES.get(code, "알 수 없는 오류"))
        except ValueError:
            pass
        try:
            root = ET.fromstring(resp.content)
            code = (root.findtext("status") or "").strip()
            message = (root.findtext("message") or "").strip()
            return OpenDartError(code or "UNKNOWN", message or STATUS_MESSAGES.get(code, "알 수 없는 오류"))
        except Exception:
            return OpenDartError("UNKNOWN", "응답을 해석할 수 없습니다")

    # ------------------------------------------------------------- corp code
    def corp_codes(self, *, refresh: bool = False) -> list[Corp]:
        config.ensure_dirs()
        cache = config.CACHE_DIR / "corpcode.json"
        if not refresh and cache.exists():
            age_days = (time.time() - cache.stat().st_mtime) / 86400
            if age_days < config.CORPCODE_TTL_DAYS:
                return [Corp(**row) for row in json.loads(cache.read_text("utf-8"))]

        resp = self._request("corpCode.xml", {})
        if resp.content[:2] != b"PK":
            raise self._error_from_response(resp)

        with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
            xml_name = next(n for n in zf.namelist() if n.lower().endswith(".xml"))
            raw = zf.read(xml_name)

        root = ET.fromstring(raw)
        corps = [
            Corp(
                code=(el.findtext("corp_code") or "").strip(),
                name=(el.findtext("corp_name") or "").strip(),
                stock_code=(el.findtext("stock_code") or "").strip(),
            )
            for el in root.findall("list")
        ]
        corps = [c for c in corps if c.code and c.name]
        cache.write_text(json.dumps([c.__dict__ for c in corps], ensure_ascii=False), "utf-8")
        return corps

    def document_zip(self, rcept_no: str) -> bytes:
        """공시서류 원본파일(ZIP). 원문 대조용."""
        resp = self._request("document.xml", {"rcept_no": rcept_no})
        if resp.content[:2] != b"PK":
            raise self._error_from_response(resp)
        return resp.content

    # ------------------------------------------------------------ financials
    def financial_statements(
        self,
        corp_code: str,
        year: int,
        *,
        reprt_code: str = "11011",
        fs_div: str = "CFS",
    ) -> FsResponse:
        resp = self._request(
            "fnlttSinglAcntAll.json",
            {
                "corp_code": corp_code,
                "bsns_year": str(year),
                "reprt_code": reprt_code,
                "fs_div": fs_div,
            },
        )
        try:
            data = resp.json()
        except ValueError as exc:
            raise OpenDartError("UNKNOWN", "재무제표 응답이 JSON 이 아닙니다") from exc

        status = str(data.get("status", "")).strip()
        if status != "000":
            raise OpenDartError(
                status or "UNKNOWN",
                STATUS_MESSAGES.get(status) or str(data.get("message", "")).strip() or "알 수 없는 오류",
            )

        rows = data.get("list") or []
        if not rows:
            raise OpenDartError(NO_DATA, "재무제표 항목이 비어 있습니다")
        return FsResponse(corp_code=corp_code, year=year, reprt_code=reprt_code, fs_div=fs_div, rows=rows)
