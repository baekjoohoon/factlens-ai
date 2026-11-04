"""이미지 → 문장 추출 (Muse 비전 입력).

온라인에서 마주치는 투자 주장은 대개 **텍스트가 아니라 이미지**로 온다.
캡처, 메신저 스크린샷, 카톡 대화, 유튜브 자막 캡처 등이다.
사용자가 그 이미지를 그대로 올리면 여기서 문장을 꺼내 기존 검증 파이프라인에 넘긴다.

역할 경계(프로젝트 전체 원칙과 동일):
- 이 모듈은 **이미지에 보이는 글자를 옮기는 일만** 한다.
- 수치를 만들지 않고, 판정하지 않는다. 판정은 기존 `verdict.py` 가 계산값으로만 결정한다.
- 추출된 문장에 없는 숫자가 있으면 이후 2차 가드(`llm.py`)가 판정 전에 차단한다.
"""
from __future__ import annotations

import base64
import io
import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any

META_BASE_URL = os.getenv("MODEL_API_BASE_URL") or "https://api.meta.ai/v1"
META_DEFAULT_MODEL = os.getenv("FACTLENS_MUSE_MODEL") or "muse-spark-1.3"
META_KEY_ENV = "MODEL_API_KEY"

EXTRACT_PROMPT = """이미지에 보이는 글자 중, 기업의 실적·재무에 관한 '주장' 문장만 그대로 옮겨 적어라.

규칙:
- 이미지에 실제로 적힌 글자만 쓴다. 추측하거나 요약하지 않는다. 숫자를 바꾸지 않는다.
- 주장이 여러 개면 줄바꿈으로 구분한다.
- 주장이 없으면 정확히 NONE 이라고만 쓴다.
- 설명, 인사말, 따옴표, 머리말을 붙이지 않는다."""

MAX_IMAGE_BYTES = 5 * 1024 * 1024


class VisionError(RuntimeError):
    """이미지에서 문장을 얻지 못했을 때. 메시지에 API 키를 넣지 않는다."""


@dataclass
class ExtractResult:
    text: str
    model: str = ""
    latency_ms: int = 0
    usage: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return bool(self.text.strip())


def _api_key() -> str:
    key = (os.getenv(META_KEY_ENV) or "").strip()
    if not key:
        raise VisionError(f"{META_KEY_ENV} 가 설정되지 않았습니다")
    return key


def _mime_for(name: str, data: bytes) -> str:
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[:3] == b"GIF":
        return "image/gif"
    lower = (name or "").lower()
    if lower.endswith(".jpg") or lower.endswith(".jpeg"):
        return "image/jpeg"
    return "image/png"


def _downscale(data: bytes, max_side: int = 1600) -> tuple[bytes, str]:
    """긴 변이 max_side 를 넘으면 줄인다. Pillow 가 없으면 원본 그대로."""
    try:
        from PIL import Image
    except Exception:  # noqa: BLE001 - Pillow 미설치 환경에서도 동작해야 한다
        return data, _mime_for("", data)
    try:
        img = Image.open(io.BytesIO(data))
        if max(img.size) <= max_side:
            return data, _mime_for("", data)
        img = img.convert("RGB")
        ratio = max_side / max(img.size)
        img = img.resize((max(1, int(img.width * ratio)), max(1, int(img.height * ratio))))
        buf = io.BytesIO()
        img.save(buf, "PNG", optimize=True)
        return buf.getvalue(), "image/png"
    except Exception:  # noqa: BLE001
        return data, _mime_for("", data)


def extract_claim_text(
    image_bytes: bytes,
    *,
    filename: str = "",
    model: str | None = None,
    timeout: int = 90,
    max_tokens: int = 700,
) -> ExtractResult:
    """이미지에서 주장 문장을 뽑는다.

    Muse 는 추론 토큰을 먼저 쓰므로 max_tokens 를 넉넉히 두고 reasoning_effort 를 낮춘다.
    """
    import time

    if not image_bytes:
        raise VisionError("이미지가 비어 있습니다")
    if len(image_bytes) > MAX_IMAGE_BYTES:
        raise VisionError(
            f"이미지가 너무 큽니다 ({len(image_bytes) // 1024}KB). 5MB 이하로 올려주세요"
        )

    data, mime = _downscale(image_bytes)
    b64 = base64.b64encode(data).decode("ascii")
    key = _api_key()
    use_model = model or META_DEFAULT_MODEL

    payload = {
        "model": use_model,
        "max_tokens": max_tokens,
        "temperature": 0,
        "reasoning_effort": "minimal",
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": EXTRACT_PROMPT},
                    {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
                ],
            }
        ],
    }

    req = urllib.request.Request(
        f"{META_BASE_URL.rstrip('/')}/chat/completions",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        method="POST",
    )

    started = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:300]
        detail = detail.replace(key, "***")
        raise VisionError(f"Meta API HTTP {exc.code}: {detail}") from exc
    except (TimeoutError, OSError) as exc:
        raise VisionError(f"Meta API 요청 실패: {type(exc).__name__}") from exc

    latency = int((time.perf_counter() - started) * 1000)
    usage = body.get("usage") or {}
    choices = body.get("choices") or []
    if not choices:
        raise VisionError("Meta API 응답에 choices 가 없습니다")

    choice = choices[0]
    raw = ((choice.get("message") or {}).get("content") or "").strip()
    notes: list[str] = []

    if not raw:
        reasoning = (usage.get("completion_tokens_details") or {}).get("reasoning_tokens") or 0
        raise VisionError(
            f"이미지에서 글자를 읽지 못했습니다(finish={choice.get('finish_reason')}, "
            f"추론 토큰 {reasoning}개). 더 선명한 이미지로 다시 시도해 주세요"
        )

    if raw.upper().startswith("NONE"):
        return ExtractResult(text="", model=body.get("model") or use_model,
                             latency_ms=latency, usage=usage,
                             notes=["이미지에서 검증 가능한 실적·재무 주장을 찾지 못했습니다"])

    # 모델이 머리말을 붙이는 경우를 방지: 첫 줄부터 문장만 남긴다
    lines = [ln.strip().strip('"“”\'') for ln in raw.splitlines()]
    lines = [ln for ln in lines if ln and not ln.upper().startswith("NONE")]
    if len(lines) > 1:
        notes.append(f"이미지에서 {len(lines)}줄을 읽어 하나의 문장으로 합쳤습니다")
    text = " ".join(lines)

    return ExtractResult(text=text, model=body.get("model") or use_model,
                         latency_ms=latency, usage=usage, notes=notes)
