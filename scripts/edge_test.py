import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from factlens.opendart import OpenDartClient, OpenDartError
from factlens.corp import CorpIndex

c = OpenDartClient()
for label, fn in [("잘못된 고유번호", lambda: c.financial_statements("99999999", 2025)),
                  ("데이터 없는 연도", lambda: c.financial_statements("00126380", 1999))]:
    try:
        fn()
        print(label, "-> 예상과 달리 성공")
    except OpenDartError as e:
        print(f"{label} -> [{e.code}] {e.message}")

idx = CorpIndex(c.corp_codes())
for q in ["삼성", "삼성전자", "005930", "없는회사이름", "삼전"]:
    r = idx.resolve(q)
    print(f"resolve({q}) -> {r.corp.name if r.corp else None} / {r.method} / 후보 {len(r.candidates)}")
print("calls", c.call_count)
