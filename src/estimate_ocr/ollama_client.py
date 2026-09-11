"""로컬 Ollama HTTP 클라이언트 + 관대한 JSON 파싱.

vision 모델은 요청과 달리 코드펜스를 붙이거나 앞뒤에 설명을 덧붙이는 일이 잦다.
`loads_lenient()` 는 그런 응답에서 JSON 객체를 최대한 건져낸다.
"""

from __future__ import annotations

import base64
import json
import logging
import re
from typing import Any, Sequence

import requests

log = logging.getLogger(__name__)

DEFAULT_MODEL = "qwen3-vl:8b"
DEFAULT_HOST = "http://localhost:11434"
DEFAULT_NUM_CTX = 8192
DEFAULT_TIMEOUT = 600

_FENCE_RE = re.compile(r"```(?:json|JSON)?\s*(.*?)```", re.DOTALL)
_TRAILING_COMMA_RE = re.compile(r",\s*([}\]])")
_NUM_WITH_COMMA_RE = re.compile(r'(?<=[:\[,\s])(-?\d{1,3}(?:,\d{3})+(?:\.\d+)?)(?=\s*[,}\]])')


class OllamaError(RuntimeError):
    """Ollama 서버와 통신하지 못했거나 서버가 오류를 돌려준 경우."""


class JSONParseError(ValueError):
    """응답에서 JSON 을 건져내지 못한 경우."""


def _strip_fences(text: str) -> str:
    match = _FENCE_RE.search(text)
    return match.group(1) if match else text


def _find_json_span(text: str) -> str | None:
    """문자열 리터럴을 존중하며 첫 번째 균형 잡힌 {...} / [...] 를 찾는다."""
    for opener, closer in (("{", "}"), ("[", "]")):
        start = text.find(opener)
        if start < 0:
            continue
        depth = 0
        in_string = False
        escaped = False
        for index in range(start, len(text)):
            char = text[index]
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
            elif char == opener:
                depth += 1
            elif char == closer:
                depth -= 1
                if depth == 0:
                    return text[start : index + 1]
    return None


def _repair(text: str) -> str:
    text = _TRAILING_COMMA_RE.sub(r"\1", text)
    # 1,200 처럼 모델이 자릿수 쉼표를 그대로 남긴 숫자를 복구
    text = _NUM_WITH_COMMA_RE.sub(lambda m: m.group(1).replace(",", ""), text)
    # 파이썬/자바스크립트 리터럴
    text = re.sub(r"\bNone\b", "null", text)
    text = re.sub(r"\b(True|False)\b", lambda m: m.group(1).lower(), text)
    text = re.sub(r"\b(NaN|Infinity|-Infinity|undefined)\b", "null", text)
    return text


def loads_lenient(text: str) -> Any:
    """모델 응답 문자열에서 JSON 을 최대한 복구해 파싱한다."""
    if not text or not text.strip():
        raise JSONParseError("빈 응답")

    candidates: list[str] = []
    stripped = text.strip()
    candidates.append(stripped)

    unfenced = _strip_fences(stripped).strip()
    if unfenced != stripped:
        candidates.append(unfenced)

    for base in list(candidates):
        span = _find_json_span(base)
        if span:
            candidates.append(span)

    for candidate in list(candidates):
        candidates.append(_repair(candidate))

    for candidate in candidates:
        try:
            return json.loads(candidate)
        except (json.JSONDecodeError, ValueError):
            continue

    preview = stripped[:200].replace("\n", " ")
    raise JSONParseError(f"JSON 파싱 실패 (응답 앞부분: {preview!r})")


class OllamaClient:
    """로컬 Ollama 서버의 `/api/generate` 를 감싼 얇은 클라이언트."""

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        host: str = DEFAULT_HOST,
        *,
        num_ctx: int = DEFAULT_NUM_CTX,
        timeout: int = DEFAULT_TIMEOUT,
        temperature: float = 0.0,
        session: requests.Session | None = None,
    ) -> None:
        self.model = model
        self.host = host.rstrip("/")
        self.num_ctx = num_ctx
        self.timeout = timeout
        self.temperature = temperature
        self.session = session or requests.Session()

    # -- 저수준 -----------------------------------------------------------
    def generate(
        self,
        prompt: str,
        *,
        images: Sequence[bytes] | None = None,
        system: str | None = None,
        json_mode: bool = False,
    ) -> str:
        payload: dict[str, Any] = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": self.temperature,
                "num_ctx": self.num_ctx,
            },
        }
        if system:
            payload["system"] = system
        if images:
            payload["images"] = [base64.b64encode(image).decode("ascii") for image in images]
        if json_mode:
            payload["format"] = "json"

        url = f"{self.host}/api/generate"
        log.debug("POST %s (model=%s, images=%d)", url, self.model, len(images or ()))
        try:
            response = self.session.post(url, json=payload, timeout=self.timeout)
        except requests.Timeout as exc:
            raise OllamaError(
                f"{self.timeout}초 안에 응답이 없습니다. --timeout 을 늘리거나 "
                "--max-edge / --num-ctx 를 낮춰보세요."
            ) from exc
        except requests.RequestException as exc:
            raise OllamaError(
                f"Ollama 서버({self.host})에 연결하지 못했습니다: {exc}. "
                "`ollama serve` 가 실행 중인지 확인하세요."
            ) from exc

        if response.status_code >= 400:
            detail = response.text.strip()[:300]
            if response.status_code == 404:
                raise OllamaError(
                    f"모델 '{self.model}' 을 찾을 수 없습니다. `ollama pull {self.model}` 을 먼저 실행하세요."
                )
            raise OllamaError(f"Ollama 오류 {response.status_code}: {detail}")

        try:
            data = response.json()
        except ValueError as exc:
            raise OllamaError(f"Ollama 응답을 해석하지 못했습니다: {response.text[:200]!r}") from exc

        if data.get("error"):
            raise OllamaError(str(data["error"]))
        return str(data.get("response", ""))

    # -- 고수준 -----------------------------------------------------------
    def generate_json(
        self,
        prompt: str,
        *,
        images: Sequence[bytes] | None = None,
        system: str | None = None,
    ) -> Any:
        """JSON 모드로 요청하고 관대하게 파싱한다."""
        text = self.generate(prompt, images=images, system=system, json_mode=True)
        return loads_lenient(text)

    def list_models(self) -> list[str]:
        try:
            response = self.session.get(f"{self.host}/api/tags", timeout=min(self.timeout, 30))
            response.raise_for_status()
        except requests.RequestException as exc:
            raise OllamaError(
                f"Ollama 서버({self.host})에 연결하지 못했습니다: {exc}. "
                "`ollama serve` 가 실행 중인지 확인하세요."
            ) from exc
        return [model.get("name", "") for model in response.json().get("models", [])]

    def ensure_model(self) -> None:
        """모델이 없으면 바로 알려준다 (페이지 한 장 돌린 뒤 실패하는 것보다 낫다)."""
        available = self.list_models()
        if not available:
            return
        names = {name.split(":")[0] for name in available} | set(available)
        if self.model in available or self.model in names or self.model.split(":")[0] in names:
            return
        raise OllamaError(
            f"모델 '{self.model}' 이 설치되어 있지 않습니다. "
            f"`ollama pull {self.model}` 을 실행하세요. (설치된 모델: {', '.join(available) or '없음'})"
        )
