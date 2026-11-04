"""LLM 기반 주장 구조화 (실제 모델 호출).

- `CodexPlanner`: 설치된 codex CLI(OpenAI OAuth 로그인)를 비대화형으로 호출한다.
  `--output-schema` 로 JSON Schema 를 강제하고, 결과를 `--output-last-message` 파일로 받는다.
- `RulePlanner`: 기존 규칙 기반 파서. LLM 이 없거나 실패하면 이쪽으로 폴백한다.
- `AutoPlanner`: LLM 우선, 실패 시 규칙으로 폴백하고 사유를 기록한다.

원칙
- LLM 은 **문장 해석만** 한다. 재무 수치는 OpenDART 응답과 `calc.py` 만 만든다.
- LLM 이 반환한 주장 수치는 **원문 문장에 실제로 존재하는 숫자**여야 한다(가드).
- 스키마를 통과하지 못하면 그 결과는 버리고 폴백한다.
"""
from __future__ import annotations

import json
import os
import json as _json
import os as _os
import re
import shutil
import subprocess
import tempfile
import time
import urllib.error as _urlerr
import urllib.request as _urlreq
import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any, Protocol

from .claims import Claim, PeriodSpec, parse_text

METRICS = ["revenue", "operating_income", "net_income", "debt_ratio",
           "liabilities", "equity", "assets", "unknown"]
CLAIM_TYPES = ["growth_rate", "level", "qualitative", "unsupported"]
PERIOD_MODES = ["yoy", "year", "unspecified"]
BASES = ["CFS", "OFS", None]

CLAIM_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["claims"],
    "properties": {
        "claims": {
            "type": "array",
            "minItems": 1,
            "maxItems": 6,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["evidence_text", "company_name", "metric", "claim_type",
                             "claimed_value", "period", "basis", "needs_check"],
                "properties": {
                    "evidence_text": {"type": "string"},
                    "company_name": {"type": "string"},
                    "metric": {"type": "string", "enum": METRICS},
                    "claim_type": {"type": "string", "enum": CLAIM_TYPES},
                    "claimed_value": {"type": ["number", "null"]},
                    "period": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["mode", "year"],
                        "properties": {
                            "mode": {"type": "string", "enum": PERIOD_MODES},
                            "year": {"type": ["integer", "null"]},
                        },
                    },
                    "basis": {"type": ["string", "null"], "enum": BASES},
                    "needs_check": {
                        "type": "array",
                        "maxItems": 5,
                        "items": {"type": "string"},
                    },
                },
            },
        }
    },
}

SYSTEM_PROMPT = """너는 투자 관련 문장을 '검증 가능한 주장'으로 분해하는 분석기다.

규칙:
1. 문장에 없는 기업명·수치·기간을 만들어내지 마라. 모든 값은 입력 문장에서만 추출한다.
2. 문장을 서로 독립적으로 검증 가능한 주장으로 나눈다. 접속사로 이어진 복문은 나눈다.
3. claimed_value 는 문장에 적힌 수치만 넣는다. 계산하거나 추정하지 마라.
   - "40% 증가" -> 40, "10% 감소" -> -10
   - 수치가 없는 정성 주장("부채가 거의 없다") -> null
4. metric 은 다음 중 하나: revenue, operating_income, net_income, debt_ratio,
   liabilities, equity, assets, unknown
5. claim_type: growth_rate(증감률), level(수준·비율), qualitative(정성), unsupported(검증 불가)
6. period.mode: yoy(지난해·작년·전년 대비), year(연도 명시), unspecified
   period.year: 문장에 연도가 있으면 정수, 없으면 null
7. basis: '연결'이면 CFS, '별도'면 OFS, 언급이 없으면 null
8. needs_check: 이 주장을 검증하려면 추가로 확정해야 하는 조건을 짧게 적는다.
   예: "회계기준(연결/별도) 미지정", "금액 단위 확인 필요"
9. evidence_text 는 입력 문장에서 그대로 복사한 부분 문자열이어야 한다.
10. 출력은 주어진 JSON 스키마만 따른다. 설명 문장을 덧붙이지 마라.

예시 입력: "삼성전자 매출이 작년보다 40% 늘었고 부채가 거의 없대."
예시 출력:
{"claims":[
 {"evidence_text":"삼성전자 매출이 작년보다 40% 늘었고","company_name":"삼성전자",
  "metric":"revenue","claim_type":"growth_rate","claimed_value":40,
  "period":{"mode":"yoy","year":null},"basis":null,
  "needs_check":["회계기준(연결/별도) 미지정"]},
 {"evidence_text":"부채가 거의 없대","company_name":"삼성전자",
  "metric":"liabilities","claim_type":"qualitative","claimed_value":null,
  "period":{"mode":"unspecified","year":null},"basis":null,
  "needs_check":["'거의 없다'의 판정 임계값 없음"]}]}
"""


class SchemaError(ValueError):
    """LLM 응답이 스키마·가드을 통과하지 못했을 때."""


@dataclass
class PlannerInfo:
    name: str = "rule"
    model: str = ""
    latency_ms: int = 0
    fallback: bool = False
    detail: str = ""
    raw_preview: str = ""


class Planner(Protocol):
    def parse(self, text: str) -> tuple[list[Claim], PlannerInfo]: ...


# --------------------------------------------------------------------- utils
def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", str(text)).strip()


def _number_in_text(value: Decimal, text: str) -> bool:
    """LLM 이 준 수치가 원문 문장에 실제로 존재하는지 확인한다."""
    raw = format(value.normalize(), "f")
    candidates = {raw, raw.replace("-", ""), raw + ".0", raw.replace(".0", "")}
    compact = text.replace(",", "").replace(" ", "")
    for cand in candidates:
        if cand and (cand in text or cand in compact):
            return True
    return False


def _require(cond: bool, message: str) -> None:
    if not cond:
        raise SchemaError(message)


def validate_payload(payload: Any, source_text: str) -> list[dict[str, Any]]:
    """스키마 검증 + 수치 가드. 실패하면 SchemaError."""
    _require(isinstance(payload, dict), "최상위가 객체가 아닙니다")
    _require(set(payload) == {"claims"}, "허용되지 않은 최상위 키가 있습니다")
    claims = payload["claims"]
    _require(isinstance(claims, list) and 1 <= len(claims) <= 6, "claims 는 1~6개 배열이어야 합니다")

    source = _norm(source_text)
    for i, item in enumerate(claims):
        tag = f"claims[{i}]"
        _require(isinstance(item, dict), f"{tag} 가 객체가 아닙니다")
        allowed = {"evidence_text", "company_name", "metric", "claim_type",
                   "claimed_value", "period", "basis", "needs_check"}
        _require(set(item) == allowed, f"{tag} 키가 스키마와 다릅니다: {sorted(set(item) ^ allowed)}")
        for key in ("evidence_text", "company_name"):
            _require(isinstance(item[key], str), f"{tag}.{key} 는 문자열이어야 합니다")
        _require(item["metric"] in METRICS, f"{tag}.metric 값이 허용 범위를 벗어났습니다")
        _require(item["claim_type"] in CLAIM_TYPES, f"{tag}.claim_type 값이 허용 범위를 벗어났습니다")
        value = item["claimed_value"]
        _require(value is None or isinstance(value, (int, float)), f"{tag}.claimed_value 는 숫자 또는 null")
        period = item["period"]
        _require(isinstance(period, dict) and set(period) == {"mode", "year"}, f"{tag}.period 구조 오류")
        _require(period["mode"] in PERIOD_MODES, f"{tag}.period.mode 값 오류")
        _require(period["year"] is None or isinstance(period["year"], int), f"{tag}.period.year 는 정수 또는 null")
        _require(item["basis"] in BASES, f"{tag}.basis 값 오류")
        _require(isinstance(item["needs_check"], list)
                 and all(isinstance(x, str) for x in item["needs_check"]), f"{tag}.needs_check 형식 오류")

        _require(_norm(item["evidence_text"]) in source or _norm(item["evidence_text"]) == "",
                 f"{tag}.evidence_text 가 원문에 없습니다")
        if value is not None:
            _require(_number_in_text(Decimal(str(value)), source_text),
                     f"{tag}.claimed_value({value}) 가 원문 문장에 없습니다")
        if item["claim_type"] == "growth_rate":
            _require(value is not None, f"{tag}: growth_rate 인데 수치가 없습니다")
    return claims


def payload_to_claims(payload_claims: list[dict[str, Any]]) -> list[Claim]:
    out: list[Claim] = []
    for item in payload_claims:
        value = item["claimed_value"]
        period = item["period"]
        notes = [f"LLM 판단: {item['company_name'] or '기업 미상'} · {item['claim_type']}"]
        notes += [f"추가 확인: {x}" for x in item["needs_check"]]
        out.append(Claim(
            raw=_norm(item["evidence_text"]),
            company_text=item["company_name"],
            metric=None if item["metric"] == "unknown" else item["metric"],
            claim_type=item["claim_type"],
            claimed_value=None if value is None else Decimal(str(value)),
            basis=item["basis"],
            period=PeriodSpec(period["mode"], period["year"], ""),
            notes=notes,
        ))
    return out


# -------------------------------------------------------------------- planner
class RulePlanner:
    name = "rule"

    def parse(self, text: str) -> tuple[list[Claim], PlannerInfo]:
        start = time.perf_counter()
        claims = parse_text(text)
        return claims, PlannerInfo(
            name="rule",
            latency_ms=int((time.perf_counter() - start) * 1000),
            detail="규칙 기반 파서",
        )


class CodexPlanner:
    """codex CLI(OpenAI OAuth)로 구조화 출력을 받는다."""

    name = "llm"

    def __init__(self, model: str | None = None, timeout: int = 180, workdir: str | None = None):
        self.model = model or os.getenv("FACTLENS_LLM_MODEL") or None
        self.timeout = timeout
        self.workdir = workdir
        self._schema_file: Path | None = None

    @staticmethod
    def executable() -> str | None:
        return shutil.which("codex")

    def available(self) -> bool:
        return self.executable() is not None

    def _schema_path(self) -> Path:
        if self._schema_file and self._schema_file.exists():
            return self._schema_file
        d = Path(tempfile.gettempdir()) / "factlens_llm"
        d.mkdir(parents=True, exist_ok=True)
        path = d / "claim_schema.json"
        path.write_text(json.dumps(CLAIM_SCHEMA, ensure_ascii=False, indent=2), "utf-8")
        self._schema_file = path
        return path

    def _command(self, out_path: Path) -> list[str]:
        exe = self.executable()
        assert exe is not None
        args = ["exec", "--skip-git-repo-check", "--ephemeral", "-s", "read-only",
                "-C", self.workdir or tempfile.gettempdir(),
                "-o", str(out_path), "--output-schema", str(self._schema_path())]
        if self.model:
            args += ["-m", self.model]
        args.append("-")
        if exe.lower().endswith((".cmd", ".bat")):
            return ["cmd", "/c", exe, *args]
        return [exe, *args]

    def parse(self, text: str) -> tuple[list[Claim], PlannerInfo]:
        if not self.available():
            raise SchemaError("codex CLI 를 찾을 수 없습니다")

        out_path = Path(tempfile.gettempdir()) / "factlens_llm" / f"out_{uuid.uuid4().hex[:8]}.json"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        prompt = f"{SYSTEM_PROMPT}\n\n입력 문장:\n{text}\n"
        started = time.perf_counter()
        proc = subprocess.run(
            self._command(out_path),
            input=prompt.encode("utf-8"),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=self.timeout,
        )
        latency = int((time.perf_counter() - started) * 1000)

        if proc.returncode != 0:
            tail = (proc.stderr or b"").decode("utf-8", "replace")[-300:]
            raise SchemaError(f"codex 실행 실패(exit {proc.returncode}): {tail}")
        if not out_path.exists():
            raise SchemaError("codex 가 출력 파일을 만들지 않았습니다")

        raw = out_path.read_text("utf-8", errors="replace").strip()
        out_path.unlink(missing_ok=True)
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise SchemaError(f"JSON 파싱 실패: {exc.msg}") from exc

        claims = payload_to_claims(validate_payload(payload, text))
        combined = proc.stdout.decode("utf-8", "replace") + "\n" + proc.stderr.decode("utf-8", "replace")
        model = self.model or _detect_model(combined)
        return claims, PlannerInfo(
            name="llm",
            model=model,
            latency_ms=latency,
            detail=f"LLM 구조화 · {len(claims)}건",
            raw_preview=raw[:400],
        )


def _detect_model(stdout: str) -> str:
    m = re.search(r"^model:\s*(.+)$", stdout, re.MULTILINE)
    return m.group(1).strip() if m else ""


META_BASE_URL = "https://api.meta.ai/v1"
META_DEFAULT_MODEL = "muse-spark-1.3"
META_KEY_ENV = "MODEL_API_KEY"


class MusePlanner:
    """Meta Model API(Muse Spark)로 구조화 출력을 받는다.

    - OpenAI 호환 `/v1/chat/completions` + `response_format=json_schema(strict)`.
    - Muse 계열은 추론 토큰을 먼저 소비하므로 `max_tokens` 를 넉넉히 주고
      `reasoning_effort` 를 낮춰 지연을 줄인다.
    - API 키는 환경변수에서만 읽고 어떤 예외 메시지에도 넣지 않는다.
    """

    name = "muse"

    def __init__(
        self,
        model: str | None = None,
        base_url: str | None = None,
        timeout: int = 120,
        max_tokens: int = 2000,
        reasoning_effort: str | None = "minimal",
        api_key: str | None = None,
    ):
        self.model = model or _os.getenv("FACTLENS_MUSE_MODEL") or META_DEFAULT_MODEL
        self.base_url = (base_url or _os.getenv("MODEL_API_BASE_URL") or META_BASE_URL).rstrip("/")
        self.timeout = timeout
        self.max_tokens = max_tokens
        self.reasoning_effort = reasoning_effort if reasoning_effort is not None else (_os.getenv("FACTLENS_MUSE_REASONING") or "minimal")
        self._api_key = api_key
        self.last_usage: dict[str, Any] = {}

    @staticmethod
    def api_key() -> str | None:
        return (_os.getenv(META_KEY_ENV) or "").strip() or None

    def available(self) -> bool:
        return bool(self._api_key or self.api_key())

    def _key(self) -> str:
        key = self._api_key or self.api_key()
        if not key:
            raise SchemaError(f"{META_KEY_ENV} 가 설정되지 않았습니다")
        return key

    def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        key = self._key()
        body = _json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = _urlreq.Request(
            f"{self.base_url}/chat/completions",
            data=body,
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with _urlreq.urlopen(req, timeout=self.timeout) as resp:
                return _json.loads(resp.read().decode("utf-8"))
        except _urlerr.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:400]
            # 키가 응답 본문에 섞여 나오는 경우를 대비해 가린다
            if key:
                detail = detail.replace(key, "***")
            raise SchemaError(f"Meta API HTTP {exc.code}: {detail}") from exc
        except (TimeoutError, OSError) as exc:
            raise SchemaError(f"Meta API 요청 실패: {type(exc).__name__}") from exc

    def parse(self, text: str) -> tuple[list[Claim], PlannerInfo]:
        if not self.available():
            raise SchemaError(f"{META_KEY_ENV} 가 설정되지 않았습니다")

        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": f"입력 문장:\n{text}"},
            ],
            "max_tokens": self.max_tokens,
            "temperature": 0,
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "claims", "schema": CLAIM_SCHEMA, "strict": True},
            },
        }
        if self.reasoning_effort:
            payload["reasoning_effort"] = self.reasoning_effort

        started = time.perf_counter()
        resp = self._post(payload)
        latency = int((time.perf_counter() - started) * 1000)

        usage = resp.get("usage") or {}
        self.last_usage = {
            "prompt_tokens": usage.get("prompt_tokens"),
            "completion_tokens": usage.get("completion_tokens"),
            "reasoning_tokens": (usage.get("completion_tokens_details") or {}).get("reasoning_tokens"),
            "total_tokens": usage.get("total_tokens"),
        }

        choices = resp.get("choices") or []
        if not choices:
            raise SchemaError("Meta API 응답에 choices 가 없습니다")
        choice = choices[0]
        raw = (choice.get("message") or {}).get("content") or ""
        if not raw.strip():
            finish = choice.get("finish_reason")
            reasoning = self.last_usage.get("reasoning_tokens") or 0
            hint = (
                f"추론 토큰 {reasoning}개 소모" if reasoning else "빈 응답"
            )
            raise SchemaError(f"Meta API 본문이 비었습니다(finish={finish}, {hint})")

        try:
            data = _json.loads(raw)
        except _json.JSONDecodeError as exc:
            raise SchemaError(f"JSON 파싱 실패: {exc.msg}") from exc

        claims = payload_to_claims(validate_payload(data, text))
        return claims, PlannerInfo(
            name="muse",
            model=resp.get("model") or self.model,
            latency_ms=latency,
            detail=f"Muse 구조화 · {len(claims)}건",
            raw_preview=raw[:400],
        )


class AutoPlanner:
    """LLM 우선, 실패하면 규칙 기반으로 폴백하고 사유를 남긴다."""

    def __init__(self, primary: Planner | None = None, fallback: Planner | None = None):
        self.primary = primary or MusePlanner()
        self.fallback = fallback or RulePlanner()
        self.last_error = ""

    def parse(self, text: str) -> tuple[list[Claim], PlannerInfo]:
        try:
            return self.primary.parse(text)
        except Exception as exc:  # noqa: BLE001 - 폴백 사유를 남기기 위해 모두 잡는다
            self.last_error = f"{type(exc).__name__}: {exc}"
            claims, info = self.fallback.parse(text)
            info.fallback = True
            info.detail = f"LLM 실패 → 규칙 기반 폴백 ({self.last_error[:160]})"
            return claims, info


def build_planner(mode: str = "auto") -> Planner:
    if mode == "rule":
        return RulePlanner()
    if mode in ("llm", "muse"):
        return MusePlanner()
    if mode == "codex":
        return CodexPlanner()
    return AutoPlanner()
