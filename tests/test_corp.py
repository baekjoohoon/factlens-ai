"""기업명 해석 테스트 (네트워크 불필요, 합성 데이터)."""
from factlens.corp import CorpIndex
from factlens.opendart import Corp

CORPS = [
    Corp("00000001", "삼성", ""),
    Corp("00000002", "삼성전자", "005930"),
    Corp("00000003", "삼성물산", "028260"),
    Corp("00000004", "테스트기업", "000001"),
]


def _idx():
    return CorpIndex(CORPS, aliases={})


def test_exact_listed_match():
    r = _idx().resolve("삼성전자")
    assert r.corp and r.corp.code == "00000002"
    assert r.method == "정확 일치"


def test_stock_code_match():
    r = _idx().resolve("005930")
    assert r.corp and r.corp.name == "삼성전자"
    assert r.method == "종목코드"


def test_ambiguous_when_unlisted_exact_match_has_several_listed_candidates():
    r = _idx().resolve("삼성")
    assert r.corp is None
    assert r.ambiguous
    assert {c.name for c in r.candidates} == {"삼성전자", "삼성물산"}


def test_single_listed_candidate_is_preferred():
    index = CorpIndex([Corp("00000001", "삼성", ""), Corp("00000002", "삼성전자", "005930")], aliases={})
    r = index.resolve("삼성")
    assert r.corp is not None and r.corp.name == "삼성전자"
    assert r.method == "상장사 우선"


def test_partial_single_match():
    r = _idx().resolve("테스트")
    assert r.corp and r.corp.name == "테스트기업"


def test_unknown_name():
    r = _idx().resolve("없는회사이름")
    assert r.corp is None and not r.ambiguous
