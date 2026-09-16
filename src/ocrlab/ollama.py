"""로컬 Ollama HTTP 클라이언트 + 관대한 JSON 파싱."""

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
_THOUSANDS_RE = re.compile(r'(?<=[:\[,\s])(-?\d{1,3}(?:,\d{3})+(?:\.\d+)?)(?=\s*[,}\]])')


class OllamaError(RuntimeError):
    """서버에 닿지 못했거나 서버가 오류를 돌려준 경우."""


def _short(exc: Exception, limit: int = 90) -> str:
    """requests 예외는 원문이 길어 메뉴 한 줄을 넘긴다. 핵심만 남긴다."""
    if isinstance(exc, requests.ConnectionError):
        return "연결이 거부되었습니다"
    text = " ".join(str(exc).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


class JSONParseError(ValueError):
    """응답에서 JSON 을 건져내지 못한 경우."""


def _find_json_span(text: str) -> str | None:
    """문자열 리터럴을 존중하며 첫 번째 균형 잡힌 {...} / [...] 를 찾는다."""
    for opener, closer in (("{", "}"), ("[", "]")):
        start = text.find(opener)
        if start < 0:
            continue
        depth = 0
        in_string = escaped = False
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
    text = _THOUSANDS_RE.sub(lambda m: m.group(1).replace(",", ""), text)
    text = re.sub(r"\bNone\b", "null", text)
    text = re.sub(r"\b(True|False)\b", lambda m: m.group(1).lower(), text)
    text = re.sub(r"\b(NaN|Infinity|-Infinity|undefined)\b", "null", text)
    return text


def loads_lenient(text: str) -> Any:
    """모델 응답에서 JSON 을 최대한 복구해 파싱한다."""
    if not text or not text.strip():
        raise JSONParseError("빈 응답")

    stripped = text.strip()
    candidates = [stripped]
    fenced = _FENCE_RE.search(stripped)
    if fenced:
        candidates.append(fenced.group(1).strip())
    for base in list(candidates):
        span = _find_json_span(base)
        if span:
            candidates.append(span)
    candidates.extend(_repair(candidate) for candidate in list(candidates))

    for candidate in candidates:
        try:
            return json.loads(candidate)
        except (json.JSONDecodeError, ValueError):
            continue
    raise JSONParseError(f"JSON 파싱 실패 (응답 앞부분: {stripped[:200]!r})")


class OllamaClient:
    """`/api/generate` 를 감싼 얇은 클라이언트."""

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
            "options": {"temperature": self.temperature, "num_ctx": self.num_ctx},
        }
        if system:
            payload["system"] = system
        if images:
            payload["images"] = [base64.b64encode(image).decode("ascii") for image in images]
        if json_mode:
            payload["format"] = "json"

        try:
            response = self.session.post(
                f"{self.host}/api/generate", json=payload, timeout=self.timeout
            )
        except requests.Timeout as exc:
            raise OllamaError(
                f"{self.timeout}초 안에 응답이 없습니다. --timeout 을 늘리거나 "
                "--max-edge / --num-ctx 를 낮춰보세요."
            ) from exc
        except requests.RequestException as exc:
            raise OllamaError(
                f"Ollama 서버({self.host}) — {_short(exc)}. "
                "`ollama serve` 가 실행 중인지 확인하세요."
            ) from exc

        if response.status_code == 404:
            raise OllamaError(
                f"모델 '{self.model}' 을 찾을 수 없습니다. `ollama pull {self.model}` 을 실행하세요."
            )
        if response.status_code >= 400:
            raise OllamaError(f"Ollama 오류 {response.status_code}: {response.text.strip()[:300]}")

        try:
            data = response.json()
        except ValueError as exc:
            raise OllamaError(f"응답을 해석하지 못했습니다: {response.text[:200]!r}") from exc
        if data.get("error"):
            raise OllamaError(str(data["error"]))
        return str(data.get("response", ""))

    def generate_json(
        self,
        prompt: str,
        *,
        images: Sequence[bytes] | None = None,
        system: str | None = None,
    ) -> Any:
        return loads_lenient(
            self.generate(prompt, images=images, system=system, json_mode=True)
        )

    def list_models(self) -> list[str]:
        try:
            response = self.session.get(
                f"{self.host}/api/tags", timeout=min(self.timeout, 30)
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            raise OllamaError(
                f"Ollama 서버({self.host}) — {_short(exc)}. "
                "`ollama serve` 가 실행 중인지 확인하세요."
            ) from exc
        return [entry.get("name", "") for entry in response.json().get("models", [])]

    def has_model(self) -> bool:
        available = self.list_models()
        if not available:
            return True        # 목록이 비면 판단하지 않는다
        names = set(available) | {name.split(":")[0] for name in available}
        return self.model in names or self.model.split(":")[0] in names
