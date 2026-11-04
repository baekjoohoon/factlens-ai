"""판정 규칙.

일치/불일치의 경계는 '주장에 표기된 반올림 오차'를 허용하기 위해 백분율 포인트(%p)로 둔다.
- |주장 - 계산| <= 1.0%p  : 일치
- <= 3.0%p               : 부분 일치
- 그 이상                 : 불일치
연결/별도 판정이 서로 갈리면 기준 불명확으로 보류한다.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum

from .calc import gap as calc_gap

MATCH_TOL = Decimal("1.0")
PARTIAL_TOL = Decimal("3.0")


class Verdict(str, Enum):
    MATCH = "일치"
    PARTIAL = "부분 일치"
    MISMATCH = "불일치"
    AMBIGUOUS = "기준 불명확"
    INSUFFICIENT = "근거 부족"


SEVERITY = {
    Verdict.MATCH: 0,
    Verdict.PARTIAL: 1,
    Verdict.INSUFFICIENT: 2,
    Verdict.AMBIGUOUS: 3,
    Verdict.MISMATCH: 4,
}


@dataclass
class Judgement:
    verdict: Verdict
    gap: Decimal | None = None
    reason: str = ""
    basis_used: str | None = None


def judge_numeric(claimed: Decimal | None, computed: Decimal | None) -> Judgement:
    if claimed is None or computed is None:
        return Judgement(Verdict.INSUFFICIENT, None, "비교할 계산값이 없습니다")
    diff = calc_gap(claimed, computed)
    assert diff is not None
    if diff <= MATCH_TOL:
        return Judgement(Verdict.MATCH, diff, f"차이 {diff}%p (허용 ±{MATCH_TOL}%p)")
    if diff <= PARTIAL_TOL:
        return Judgement(Verdict.PARTIAL, diff, f"차이 {diff}%p (허용 ±{PARTIAL_TOL}%p 초과)")
    return Judgement(Verdict.MISMATCH, diff, f"차이 {diff}%p")


def judge_across_bases(
    claimed: Decimal | None,
    computed: dict[str, Decimal | None],
    basis: str | None,
) -> Judgement:
    """basis 미지정 시 연결/별도 양쪽을 계산해 판정이 갈리는지 확인한다."""
    available = {k: v for k, v in computed.items() if v is not None}
    if claimed is None:
        return Judgement(Verdict.INSUFFICIENT, None, "주장 수치를 해석하지 못했습니다")
    if not available:
        return Judgement(Verdict.INSUFFICIENT, None, "공시에서 필요한 계정을 찾지 못했습니다")

    if basis:
        if basis not in available:
            return Judgement(
                Verdict.INSUFFICIENT, None, f"주장이 지정한 기준({basis})의 데이터가 없습니다", basis
            )
        j = judge_numeric(claimed, available[basis])
        j.basis_used = basis
        return j

    if len(available) >= 2:
        results = {k: judge_numeric(claimed, v) for k, v in available.items()}
        categories = {r.verdict for r in results.values()}
        if len(categories) > 1:
            detail = ", ".join(f"{k} {results[k].verdict.value}" for k in results)
            return Judgement(
                Verdict.AMBIGUOUS,
                max((r.gap for r in results.values() if r.gap is not None), default=None),
                f"회계기준 미지정 상태에서 판정이 갈립니다({detail}). 기준 확정 필요",
            )
        worst = max(results.values(), key=lambda r: r.gap or Decimal(0))
        worst.basis_used = "CFS/OFS 동일 판정"
        worst.reason += " (연결·별도 동일 판정)"
        return worst

    key = next(iter(available))
    j = judge_numeric(claimed, available[key])
    j.basis_used = key
    j.reason += f" ({key} 단일 기준만 확보)"
    return j


def overall(verdicts: list[Verdict]) -> Verdict:
    if not verdicts:
        return Verdict.INSUFFICIENT
    return max(verdicts, key=lambda v: SEVERITY[v])
