"""이미지 → 문장 추출 계층 테스트 (네트워크 불필요).

Muse 호출은 monkeypatch 로 대체하고, 경계 조건만 고정한다:
- 빈 값/과대 이미지 거부
- MIME 추정
- NONE 응답 처리
- API 오류 시 키 노출 없이 VisionError
"""
import io
import json

import pytest

from factlens import vision


class _Resp:
    def __init__(self, payload: dict) -> None:
        self._body = json.dumps(payload).encode("utf-8")

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _patch(monkeypatch, payload=None, error=None, captured=None):
    monkeypatch.setenv("MODEL_API_KEY", "LLM_testkey_should_not_leak")

    def fake_urlopen(req, timeout=90):
        if captured is not None:
            captured.append(req)
        if error is not None:
            raise error
        return _Resp(payload)

    monkeypatch.setattr(vision.urllib.request, "urlopen", fake_urlopen)


def _png() -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (60, 30), "white").save(buf, "PNG")
    return buf.getvalue()


def _answer(text: str, finish: str = "stop", reasoning: int = 0) -> dict:
    return {
        "model": "muse-spark-1.3",
        "choices": [{"message": {"content": text}, "finish_reason": finish}],
        "usage": {"completion_tokens_details": {"reasoning_tokens": reasoning}},
    }


def test_empty_image_rejected():
    with pytest.raises(vision.VisionError, match="비어"):
        vision.extract_claim_text(b"")


def test_oversized_image_rejected(monkeypatch):
    _patch(monkeypatch, _answer("x"))
    with pytest.raises(vision.VisionError, match="너무 큽니다"):
        vision.extract_claim_text(b"\x00" * (vision.MAX_IMAGE_BYTES + 1))


def test_missing_key_raises(monkeypatch):
    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    with pytest.raises(vision.VisionError, match="MODEL_API_KEY"):
        vision.extract_claim_text(_png())


def test_mime_sniffing():
    assert vision._mime_for("a.png", b"\x89PNG\r\n\x1a\n0000") == "image/png"
    assert vision._mime_for("a.jpg", b"\xff\xd8\xff\xe0") == "image/jpeg"
    assert vision._mime_for("a.webp", b"RIFF\x00\x00\x00\x00WEBPVP8X") == "image/webp"
    assert vision._mime_for("a.gif", b"GIF89a") == "image/gif"
    assert vision._mime_for("noext.dat", b"\x89PNG\r\n\x1a\n") == "image/png"


def test_extract_single_line(monkeypatch):
    _patch(monkeypatch, _answer("삼성전자 매출이 지난해보다 40% 증가했다"))
    r = vision.extract_claim_text(_png())
    assert r.ok
    assert r.text == "삼성전자 매출이 지난해보다 40% 증가했다"
    assert r.model == "muse-spark-1.3"
    assert r.notes == []


def test_multiline_is_joined(monkeypatch):
    _patch(monkeypatch, _answer("삼성전자 매출이\n지난해보다 40% 증가했다"))
    r = vision.extract_claim_text(_png())
    assert r.text == "삼성전자 매출이 지난해보다 40% 증가했다"
    assert r.notes and "합쳤" in r.notes[0]


def test_none_means_no_claim(monkeypatch):
    _patch(monkeypatch, _answer("NONE"))
    r = vision.extract_claim_text(_png())
    assert not r.ok
    assert r.text == ""
    assert r.notes


def test_empty_body_reports_reasoning_tokens(monkeypatch):
    _patch(monkeypatch, _answer("", finish="length", reasoning=700))
    with pytest.raises(vision.VisionError, match="추론 토큰 700"):
        vision.extract_claim_text(_png())


def test_quote_wrapping_stripped(monkeypatch):
    _patch(monkeypatch, _answer('"카카오 영업이익이 20% 감소했다"'))
    r = vision.extract_claim_text(_png())
    assert r.text == "카카오 영업이익이 20% 감소했다"


def test_request_shape(monkeypatch):
    captured: list = []
    _patch(monkeypatch, _answer("문장"), captured=captured)
    vision.extract_claim_text(_png())
    body = json.loads(captured[0].data.decode("utf-8"))
    assert body["model"] == "muse-spark-1.3"
    assert body["reasoning_effort"] == "minimal"
    assert body["temperature"] == 0
    content = body["messages"][0]["content"]
    assert content[0]["type"] == "text"
    assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")
    assert captured[0].get_header("Authorization").startswith("Bearer LLM_")


def test_http_error_redacts_key(monkeypatch):
    import urllib.error

    key = "LLM_supersecret_value_123"
    monkeypatch.setenv("MODEL_API_KEY", key)

    def boom(req, timeout=90):
        raise urllib.error.HTTPError(
            "https://api.meta.ai/v1/chat/completions", 402, "Payment Required", {},
            io.BytesIO(f'{{"error":"billing","echo":"{key}"}}'.encode()),
        )

    monkeypatch.setattr(vision.urllib.request, "urlopen", boom)
    with pytest.raises(vision.VisionError) as exc:
        vision.extract_claim_text(_png())
    msg = str(exc.value)
    assert "402" in msg
    assert key not in msg
    assert "***" in msg
