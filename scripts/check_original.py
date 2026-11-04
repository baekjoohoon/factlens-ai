"""공시 원문(사업보고서 원본파일)과 API 수치를 대조한다.

실행: python scripts/check_original.py 20260310002820
"""
from __future__ import annotations

import io
import re
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from factlens.opendart import OpenDartClient  # noqa: E402

TAG = re.compile(r"<[^>]+>")


def plain(xml_text: str) -> str:
    return TAG.sub(" ", xml_text).replace("&cr;", " ").replace("&nbsp;", " ")


def main() -> int:
    rcept_no = sys.argv[1] if len(sys.argv) > 1 else "20260310002820"
    client = OpenDartClient()
    raw = client.document_zip(rcept_no)
    print(f"원본파일 수신: {len(raw):,} bytes")

    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        names = zf.namelist()
        print(f"포함 파일 {len(names)}개")
        hits = 0
        for name in names:
            if not name.lower().endswith(".xml"):
                continue
            text = plain(zf.read(name).decode("utf-8", errors="replace"))
            for target in ("333,605,938", "333605938"):
                for m in re.finditer(re.escape(target), text):
                    start = max(0, m.start() - 120)
                    snippet = " ".join(text[start : m.end() + 120].split())
                    print(f"\n[{name}] …{snippet}…")
                    hits += 1
                    if hits >= 6:
                        break
                if hits >= 6:
                    break
            if hits >= 6:
                break
    if not hits:
        print("원문에서 해당 수치 표기를 찾지 못했습니다(표기 형식 확인 필요)")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
