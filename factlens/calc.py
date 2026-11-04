"""결정적 계산 모듈.

LLM 이 만든 숫자를 신뢰하지 않기 위해, 모든 산술은 이 모듈의 순수 함수가 수행한다.
금액 단위는 공시 응답의 '표시 단위'를 그대로 쓰며, 증가율/부채비율 같은 비율은
분자·분모가 같은 단위이므로 단위 표기 문제의 영향을 받지 않는다.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

TWO = Decimal("0.01")


def to_number(raw: object) -> Decimal | None:
    """'300,870,903' 같은 공시 표기 문자열을 Decimal 로 변환한다. 빈값/'-' 는 None."""
    if raw is None:
        return None
    if isinstance(raw, Decimal):
        return raw
    if isinstance(raw, (int, float)):
        return Decimal(str(raw))
    text = str(raw).strip().replace(",", "").replace(" ", "")
    if text in {"", "-", "—", "N/A", "nan"}:
        return None
    if text.startswith("(") and text.endswith(")"):
        text = "-" + text[1:-1]
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def q2(value: Decimal) -> Decimal:
    return value.quantize(TWO, rounding=ROUND_HALF_UP)


def growth_rate(prev: Decimal | None, curr: Decimal | None) -> Decimal | None:
    """전기 대비 증감률(%). prev 가 0 이하이면 계산 불가(None)."""
    if prev is None or curr is None:
        return None
    if prev <= 0:
        return None
    return q2((curr - prev) / prev * Decimal(100))


def ratio(numerator: Decimal | None, denominator: Decimal | None) -> Decimal | None:
    if numerator is None or denominator is None:
        return None
    if denominator == 0:
        return None
    return q2(numerator / denominator * Decimal(100))


def debt_ratio(liabilities: Decimal | None, equity: Decimal | None) -> Decimal | None:
    """부채비율(%) = 부채총계 / 자본총계 * 100."""
    return ratio(liabilities, equity)


def gap(a: Decimal | None, b: Decimal | None) -> Decimal | None:
    if a is None or b is None:
        return None
    return q2(abs(a - b))


def fmt(value: Decimal | None, *, suffix: str = "", signed: bool = False) -> str:
    if value is None:
        return "계산 불가"
    text = f"{value:,.2f}".rstrip("0").rstrip(".")
    if signed and value > 0:
        text = "+" + text
    return f"{text}{suffix}"


def fmt_amount(raw: str | Decimal | None) -> str:
    """공시 표시값을 그대로 보여준다(단위 미확정을 숨기지 않기 위해 임의 환산하지 않음)."""
    if raw is None:
        return "-"
    if isinstance(raw, Decimal):
        return f"{raw:,}"
    return str(raw)
