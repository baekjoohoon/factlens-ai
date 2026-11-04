"""계산 모듈 단위 테스트 (네트워크 불필요)."""
from decimal import Decimal

from factlens.calc import debt_ratio, fmt, gap, growth_rate, to_number


def test_to_number_handles_commas_and_blanks():
    assert to_number("300,870,903") == Decimal("300870903")
    assert to_number(" 1,200 ") == Decimal("1200")
    assert to_number("-") is None
    assert to_number("") is None
    assert to_number(None) is None


def test_to_number_handles_parenthesis_negative():
    assert to_number("(1,500)") == Decimal("-1500")


def test_growth_rate_basic():
    assert growth_rate(Decimal(1000), Decimal(1200)) == Decimal("20")
    assert growth_rate(Decimal(258935494), Decimal(300870903)) == Decimal("16.2")


def test_growth_rate_guards():
    assert growth_rate(Decimal(0), Decimal(100)) is None
    assert growth_rate(None, Decimal(100)) is None
    assert growth_rate(Decimal(-5), Decimal(100)) is None


def test_debt_ratio():
    assert debt_ratio(Decimal(300), Decimal(1000)) == Decimal("30")


def test_gap_and_fmt():
    assert gap(Decimal(40), Decimal("16.2")) == Decimal("23.8")
    assert fmt(Decimal("16.20"), suffix="%") == "16.2%"
    assert fmt(None) == "계산 불가"
