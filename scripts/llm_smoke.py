import sys, json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from factlens.llm import CodexPlanner

text = "삼성전자 매출이 작년보다 40% 늘었고 부채가 거의 없대."
p = CodexPlanner(workdir=r"C:/AI_Agent/FactLens_AI")
print("available:", p.available())
claims, info = p.parse(text)
print("info:", info.name, info.model, info.latency_ms, "ms", "fallback", info.fallback)
for c in claims:
    print(" -", repr(c.raw), "|", c.company_text, "|", c.metric, "|", c.claim_type,
          "|", c.claimed_value, "|", c.period.mode, c.period.year, "|", c.basis)
    print("   notes:", c.notes)
