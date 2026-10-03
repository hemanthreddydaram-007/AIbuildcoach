"""Gemini Interactions API adapter for AI Gateway."""

import json
import socket
import time
import urllib.error
import urllib.request
from typing import Dict, Any, Optional

from backend.ai_gateway.provider import AIProviderAdapter
from backend.ai_gateway.models import RawInteractionResponse
from backend.ai_gateway.exceptions import (
    ProviderTimeoutError,
    ProviderRateLimitError,
    ProviderAPIError,
    MalformedModelResponseError,
)

DEFAULT_GEMINI_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/interactions"
DEFAULT_GEMINI_MODEL = "gemini-3.8-flash"


class GeminiInteractionsAdapter(AIProviderAdapter):
    """Adapter for Google Gemini Interactions API using Python standard-library urllib."""

    def __init__(
        self,
        endpoint_url: str = DEFAULT_GEMINI_ENDPOINT,
        model_name: str = DEFAULT_GEMINI_MODEL,
        max_retries: int = 2,
    ):
        self.endpoint_url = endpoint_url
        self.model_name = model_name
        self.max_retries = max_retries

    def get_provider_name(self) -> str:
        return "gemini"

    def get_model_name(self) -> str:
        return self.model_name

    def complete_interaction(
        self,
        system_instruction: str,
        user_input: str,
        response_format: Dict[str, Any],
        api_key: str,
        timeout: float = 30.0,
    ) -> RawInteractionResponse:
        """Dispatches an interaction to the Gemini Interactions API."""
        if not api_key or not api_key.strip():
            raise ProviderAPIError("Missing or empty Gemini API key.", status_code=401)

        payload = self._build_request_payload(system_instruction, user_input, response_format)
        body_bytes = json.dumps(payload).encode("utf-8")

        headers = {
            "Content-Type": "application/json",
            "x-goog-api-key": api_key.strip(),
        }

        req = urllib.request.Request(
            url=self.endpoint_url,
            data=body_bytes,
            headers=headers,
            method="POST",
        )

        attempts = 0
        backoff_delay = 1.0

        while True:
            attempts += 1
            start_time = time.perf_counter()
            try:
                with urllib.request.urlopen(req, timeout=timeout) as response:
                    latency_ms = (time.perf_counter() - start_time) * 1000.0
                    status_code = response.status
                    raw_bytes = response.read()
                    raw_text = raw_bytes.decode("utf-8")

                    try:
                        raw_json = json.loads(raw_text)
                    except json.JSONDecodeError as exc:
                        raise MalformedModelResponseError(
                            f"Invalid JSON returned by Gemini API: {exc}"
                        ) from exc

                    tokens_prompt = None
                    tokens_candidate = None
                    usage = raw_json.get("usage_metadata") or raw_json.get("usage")
                    if isinstance(usage, dict):
                        tokens_prompt = usage.get("prompt_token_count")
                        tokens_candidate = usage.get("candidates_token_count")

                    return RawInteractionResponse(
                        status_code=status_code,
                        raw_json=raw_json,
                        raw_text=raw_text,
                        latency_ms=round(latency_ms, 2),
                        tokens_prompt=tokens_prompt,
                        tokens_candidate=tokens_candidate,
                    )

            except urllib.error.HTTPError as http_err:
                latency_ms = (time.perf_counter() - start_time) * 1000.0
                err_code = http_err.code
                err_body = ""
                try:
                    err_body = http_err.read().decode("utf-8", errors="replace")
                except Exception:
                    pass

                # Transient errors: HTTP 429 and HTTP 503 can be retried with backoff
                if err_code in (429, 503) and attempts <= self.max_retries:
                    time.sleep(backoff_delay)
                    backoff_delay *= 2.0
                    continue

                if err_code == 429:
                    raise ProviderRateLimitError(
                        f"Gemini API rate limit exceeded (HTTP 429): {err_body}"
                    ) from http_err

                raise ProviderAPIError(
                    f"Gemini API HTTP {err_code} error: {err_body}",
                    status_code=err_code,
                ) from http_err

            except (urllib.error.URLError, TimeoutError, socket.timeout) as net_err:
                # Do NOT automatically retry ambiguous network timeouts on POST!
                reason = getattr(net_err, "reason", None)
                if isinstance(net_err, (TimeoutError, socket.timeout)) or (
                    reason and isinstance(reason, (socket.timeout, TimeoutError))
                ):
                    raise ProviderTimeoutError(
                        f"Gemini API request timed out after {timeout} seconds."
                    ) from net_err

                raise ProviderAPIError(
                    f"Gemini API connection error: {net_err}",
                    status_code=503,
                ) from net_err

    def _build_request_payload(
        self,
        system_instruction: str,
        user_input: str,
        response_format: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Constructs the exact Gemini Interactions API request payload."""
        return {
            "model": self.model_name,
            "system_instruction": system_instruction,
            "input": user_input,
            "response_format": response_format,
            "generation_config": {
                "thinking_level": "low",
            },
            "store": False,
        }
