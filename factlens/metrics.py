"""공시 재무제표 행에서 지표를 선택하는 규칙.

OpenDART 응답은 회사마다 계정명이 조금씩 다르다(매출액 / 수익(매출액) / 영업수익).
따라서 account_id(IFRS 표준 태그)를 우선하고, 없으면 계정명 후보로 매칭한다.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Iterable

from .calc import to_number


@dataclass(frozen=True)
class MetricSpec:
    key: str
    label: str
    statement: str            # "IS" | "CIS" | "BS"
    account_ids: tuple[str, ...]
    account_names: tuple[str, ...]


METRICS: dict[str, MetricSpec] = {
    "revenue": MetricSpec(
        "revenue", "매출액", "IS",
        ("ifrs-full_Revenue", "ifrs_Revenue", "dart_Revenue"),
        ("매출액", "수익(매출액)", "영업수익", "매출"),
    ),
    "operating_income": MetricSpec(
        "operating_income", "영업이익", "IS",
        ("ifrs-full_OperatingIncomeLoss", "dart_OperatingIncomeLoss"),
        ("영업이익", "영업이익(손실)", "영업손익"),
    ),
    "net_income": MetricSpec(
        "net_income", "당기순이익", "IS",
        ("ifrs-full_ProfitLoss", "ifrs-full_ProfitLossAttributableToOwnersOfParent"),
        ("당기순이익", "당기순이익(손실)", "분기순이익", "반기순이익"),
    ),
    "liabilities": MetricSpec(
        "liabilities", "부채총계", "BS",
        ("ifrs-full_Liabilities",),
        ("부채총계", "부채 합계"),
    ),
    "equity": MetricSpec(
        "equity", "자본총계", "BS",
        ("ifrs-full_Equity", "ifrs-full_EquityAttributableToOwnersOfParent"),
        ("자본총계", "자본 합계"),
    ),
    "assets": MetricSpec(
        "assets", "자산총계", "BS",
        ("ifrs-full_Assets",),
        ("자산총계", "자산 합계"),
    ),
}

STATEMENT_ALIASES = {
    "IS": {"IS", "CIS"},
    "CIS": {"IS", "CIS"},
    "BS": {"BS"},
}


def _statement_ok(row: dict[str, Any], spec: MetricSpec) -> bool:
    return str(row.get("sj_div", "")).upper() in STATEMENT_ALIASES[spec.statement]


def find_row(rows: Iterable[dict[str, Any]], metric_key: str) -> dict[str, Any] | None:
    spec = METRICS[metric_key]
    candidates = [r for r in rows if _statement_ok(r, spec)]

    for row in candidates:
        if str(row.get("account_id", "")).strip() in spec.account_ids:
            return row

    for row in candidates:
        name = str(row.get("account_nm", "")).replace(" ", "")
        if name in spec.account_names:
            return row

    for row in candidates:  # 마지막 완화: 부분 일치
        name = str(row.get("account_nm", "")).replace(" ", "")
        if any(name.startswith(cand) for cand in spec.account_names):
            return row
    return None


def amount(rows: Iterable[dict[str, Any]], metric_key: str, column: str = "thstrm") -> tuple[Decimal | None, dict[str, Any] | None]:
    """column: 'thstrm'(당기) | 'frmtrm'(전기) | 'bfrmtrm'(전전기)."""
    row = find_row(rows, metric_key)
    if row is None:
        return None, None
    value = to_number(row.get(f"{column}_amount"))
    return value, row
