"""문장 -> 검증 가능한 주장 구조화.

예선 프로토타입은 규칙 기반 파서를 기본으로 쓴다(외부 LLM 키 없이도 재현 가능).
LLM 을 붙일 때는 같은 스키마(Claim)를 반환하는 어댑터만 교체하면 된다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal

# 지표 키워드: 긴 표현을 먼저 매칭해야 '부채비율'이 '부채'로 잘리지 않는다.
METRIC_KEYWORDS: list[tuple[str, str]] = [
    ("부채비율", "debt_ratio"),
    ("매출액", "revenue"),
    ("매출", "revenue"),
    ("영업이익", "operating_income"),
    ("당기순이익", "net_income"),
    ("순이익", "net_income"),
    ("자본총계", "equity"),
    ("부채총계", "liabilities"),
    ("부채", "liabilities"),
    ("자산총계", "assets"),
]

METRIC_LABELS = {
    "revenue": "매출액",
    "operating_income": "영업이익",
    "net_income": "당기순이익",
    "debt_ratio": "부채비율",
    "liabilities": "부채총계",
    "equity": "자본총계",
    "assets": "자산총계",
}

UP_WORDS = ("증가", "늘", "상승", "급증", "성장", "확대", "올랐", "증액")
DOWN_WORDS = ("감소", "줄", "하락", "축소", "줄었", "감액")
VAGUE_WORDS = ("거의 없다", "거의없다", "없다", "미미", "거의 없는", "전혀 없다", "없음")

PCT_RE = re.compile(r"(-?\d+(?:\.\d+)?)\s*(?:%|퍼센트|프로)")
YEAR_RE = re.compile(r"(20\d{2})\s*년")
# 소수점(10.9%)을 문장 경계로 오인하지 않도록 마침표 뒤에 공백/끝이 올 때만 자른다.
SPLIT_RE = re.compile(r"(?P<sep>[.。!?](?=\s|$)|(?:고|며|지만|는데)\s+|,\s*|\n+)")
CONNECTORS = {"고", "며", "지만", "는데"}


@dataclass
class PeriodSpec:
    mode: str = "unspecified"   # "yoy" | "year" | "unspecified"
    year: int | None = None
    source_text: str = ""


@dataclass
class Claim:
    raw: str
    company_text: str = ""
    metric: str | None = None
    claim_type: str = "unsupported"   # growth_rate | level | qualitative | unsupported
    claimed_value: Decimal | None = None
    basis: str | None = None          # "CFS" | "OFS" | None
    period: PeriodSpec = field(default_factory=PeriodSpec)
    notes: list[str] = field(default_factory=list)

    @property
    def metric_label(self) -> str:
        return METRIC_LABELS.get(self.metric or "", "지표 미확인")

    @property
    def supported(self) -> bool:
        return self.claim_type != "unsupported"


def split_claims(text: str) -> list[str]:
    """접속 어미(고/며)는 앞 주장에 붙여 남긴다. '증가했' 같은 잘린 어절을 만들지 않기 위함."""
    source = str(text)
    parts: list[str] = []
    last = 0
    for match in SPLIT_RE.finditer(source):
        segment = source[last : match.start()]
        sep = match.group("sep").strip()
        if sep in CONNECTORS:
            segment += sep
        parts.append(segment.strip(" ·-"))
        last = match.end()
    parts.append(source[last:].strip(" ·-"))
    return [p for p in parts if len(p) >= 2]


def _detect_metric(segment: str) -> str | None:
    for keyword, key in METRIC_KEYWORDS:
        if keyword in segment:
            return key
    return None


def _detect_period(segment: str) -> PeriodSpec:
    year_match = YEAR_RE.search(segment)
    if year_match:
        return PeriodSpec("year", int(year_match.group(1)), year_match.group(0))
    for token in ("지난해", "작년", "전년", "전년도", "YoY", "yoy"):
        if token in segment:
            return PeriodSpec("yoy", None, token)
    return PeriodSpec("unspecified", None, "")


def _detect_basis(segment: str) -> str | None:
    if "연결" in segment:
        return "CFS"
    if "별도" in segment:
        return "OFS"
    return None


def parse_claim(segment: str, company_text: str = "") -> Claim:
    claim = Claim(raw=segment.strip(), company_text=company_text)
    claim.metric = _detect_metric(segment)
    claim.period = _detect_period(segment)
    claim.basis = _detect_basis(segment)

    pct = PCT_RE.search(segment)
    value = Decimal(pct.group(1)) if pct else None

    if claim.metric is None:
        claim.claim_type = "unsupported"
        claim.notes.append("검증 가능한 재무지표를 찾지 못했습니다")
        return claim

    if claim.metric == "debt_ratio" and value is not None:
        claim.claim_type = "level"
        claim.claimed_value = value
        return claim

    if value is not None and any(w in segment for w in UP_WORDS):
        claim.claim_type = "growth_rate"
        claim.claimed_value = value
        return claim

    if value is not None and any(w in segment for w in DOWN_WORDS):
        claim.claim_type = "growth_rate"
        claim.claimed_value = -value
        claim.notes.append("감소 주장으로 해석해 음수로 변환했습니다")
        return claim

    if value is not None:
        claim.claim_type = "level"
        claim.claimed_value = value
        claim.notes.append("증가/감소 방향이 없어 수준(level) 주장으로 처리했습니다")
        return claim

    if any(w in segment for w in VAGUE_WORDS):
        claim.claim_type = "qualitative"
        claim.notes.append("정성 표현이라 수치 판정 기준이 없습니다")
        return claim

    claim.claim_type = "unsupported"
    claim.notes.append("수치 또는 판정 가능한 표현이 없습니다")
    return claim


def parse_text(text: str, company_text: str = "") -> list[Claim]:
    return [parse_claim(seg, company_text) for seg in split_claims(text)]
