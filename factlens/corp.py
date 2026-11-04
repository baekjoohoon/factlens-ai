"""기업명 -> OpenDART 고유번호(corp_code) 해석.

정확 일치를 우선하고, 별칭/종목코드/부분 일치로 확장한다.
후보가 2개 이상이면 임의로 고르지 않고 '기준 불명확'으로 되돌린다.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Iterable

from . import config
from .opendart import Corp

_SUFFIX = re.compile(r"\(주\)|㈜|주식회사|\s+")


def normalize(name: str) -> str:
    return _SUFFIX.sub("", str(name)).strip().lower()


def load_aliases() -> dict[str, str]:
    if not config.ALIAS_PATH.exists():
        return {}
    try:
        return {normalize(k): v for k, v in json.loads(config.ALIAS_PATH.read_text("utf-8")).items()}
    except Exception:
        return {}


@dataclass
class Resolution:
    corp: Corp | None
    method: str = "none"
    candidates: list[Corp] = field(default_factory=list)
    note: str = ""

    @property
    def ambiguous(self) -> bool:
        return self.corp is None and len(self.candidates) > 1


class CorpIndex:
    def __init__(self, corps: Iterable[Corp], aliases: dict[str, str] | None = None):
        self.corps = list(corps)
        self._by_norm: dict[str, list[Corp]] = {}
        self._by_stock: dict[str, Corp] = {}
        for c in self.corps:
            self._by_norm.setdefault(normalize(c.name), []).append(c)
            if c.stock_code:
                self._by_stock[c.stock_code] = c
        self.aliases = aliases if aliases is not None else load_aliases()

    def __len__(self) -> int:
        return len(self.corps)

    def resolve(self, text: str) -> Resolution:
        raw = str(text or "").strip()
        if not raw:
            return Resolution(None, "none", note="기업명이 입력되지 않았습니다")

        key = normalize(raw)
        if key in self._by_norm:
            found = self._by_norm[key]
            if len(found) == 1:
                corp = found[0]
                # 이름이 정확히 같은 비상장 법인이 있고, 더 긴 상장사 이름이 존재하면
                # 임의로 비상장 법인을 고르지 않고 후보를 제시한다. (예: '삼성')
                if not corp.stock_code:
                    listed = [c for c in self.corps
                              if c.stock_code and normalize(c.name).startswith(key)]
                    listed.sort(key=lambda c: -len(c.name))
                    if len(listed) == 1:
                        return Resolution(listed[0], "상장사 우선")
                    if len(listed) > 1:
                        return Resolution(
                            None, "상장사 후보 다수", listed[:8],
                            note=f"'{raw}' 는 비상장 법인명과도 일치하지만 상장사 후보가 "
                                 f"{len(listed)}개 있습니다. 기업을 특정할 수 없습니다.",
                        )
                return Resolution(corp, "정확 일치")

        if raw in self._by_stock:
            return Resolution(self._by_stock[raw], "종목코드")

        alias_target = self.aliases.get(key)
        if alias_target:
            target = normalize(alias_target)
            if target in self._by_norm and len(self._by_norm[target]) == 1:
                return Resolution(self._by_norm[target][0], "별칭 사전")

        partial = [c for c in self.corps if key and key in normalize(c.name)]
        if len(partial) == 1:
            return Resolution(partial[0], "부분 일치")
        if len(partial) > 1:
            partial.sort(key=lambda c: (not c.stock_code, len(c.name)))
            return Resolution(
                None, "후보 다수", partial[:8],
                note=f"'{raw}' 로 {len(partial)}개 기업이 검색되었습니다. 기업을 특정할 수 없습니다.",
            )
        return Resolution(None, "none", note=f"'{raw}' 에 해당하는 상장/비상장 법인을 찾지 못했습니다")

    def find_in_text(self, text: str) -> list[tuple[str, Corp]]:
        """문장 안에 등장하는 기업명을 긴 이름 우선으로 찾는다."""
        hits: list[tuple[str, Corp]] = []
        haystack = str(text)
        for c in sorted(self.corps, key=lambda x: -len(x.name)):
            if len(c.name) >= 3 and c.name in haystack:
                hits.append((c.name, c))
        return hits
