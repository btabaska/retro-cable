"""The local model behind the channel builder: an OpenAI-compatible chat endpoint (LiteLLM on
the rig). Nothing here calls a cloud model; the URL, key and model come from the environment:

  RC_LLM_URL    base URL of the endpoint, default http://rig:4000 (LiteLLM)
  RC_LLM_KEY    bearer key (the LiteLLM master or a virtual key); never logged
  RC_LLM_MODEL  model alias, default `chat`

Replies must be JSON. `ask_json` parses and validates a reply and, when it is invalid, sends
the problems back to the model and asks again, a bounded number of times.
"""

import json
import os
import re
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol

from pydantic import BaseModel, ValidationError

DEFAULT_URL = "http://rig:4000"
DEFAULT_MODEL = "chat"

Message = dict[str, str]


class LLM(Protocol):
    model: str

    def complete(self, messages: list[Message]) -> str:
        """The assistant's reply text to a chat."""
        ...


class LLMError(Exception):
    pass


class InvalidReply(ValueError):
    """A reply that parsed but broke a rule; the message is sent back to the model."""


@dataclass
class OpenAIChat:
    """POST /v1/chat/completions with urllib (no new dependency)."""

    url: str = DEFAULT_URL
    key: str = field(default="", repr=False)
    model: str = DEFAULT_MODEL
    timeout: float = 900.0  # a cold model load on the rig takes a few minutes
    temperature: float = 0.3

    @staticmethod
    def from_env() -> "OpenAIChat":
        return OpenAIChat(
            url=os.environ.get("RC_LLM_URL", DEFAULT_URL).rstrip("/"),
            key=os.environ.get("RC_LLM_KEY", ""),
            model=os.environ.get("RC_LLM_MODEL", DEFAULT_MODEL),
        )

    def complete(self, messages: list[Message]) -> str:
        body = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "response_format": {"type": "json_object"},
        }
        headers = {"Content-Type": "application/json"}
        if self.key:
            headers["Authorization"] = f"Bearer {self.key}"
        req = urllib.request.Request(
            f"{self.url}/v1/chat/completions",
            data=json.dumps(body).encode(),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.load(resp)
        except urllib.error.HTTPError as e:
            # The body can echo the request, never the key; keep it short anyway.
            raise LLMError(f"model endpoint answered HTTP {e.code}") from e
        except (urllib.error.URLError, TimeoutError) as e:
            raise LLMError(f"model endpoint unreachable at {self.url}: {e}") from e
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as e:
            raise LLMError("model endpoint gave no message") from e
        return str(content or "")


def extract_json(text: str) -> object:
    """The JSON object in a reply, allowing for code fences or a reasoning preamble."""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, flags=re.S)
    if fenced:
        text = fenced.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            return json.loads(text[start : end + 1])
        raise


@dataclass
class Transcript:
    """Every exchange with the model, for the proposal's audit trail (no keys in it)."""

    calls: int = 0
    retries: int = 0
    failures: list[str] = field(default_factory=list)


def ask_json[T: BaseModel](
    llm: LLM,
    system: str,
    user: str,
    model: type[T],
    check: Callable[[T], None] | None = None,
    retries: int = 2,
    transcript: Transcript | None = None,
) -> T | None:
    """The model's reply as `model`, or None when it is still invalid after `retries` more
    tries. `check` raises InvalidReply for rules the schema can't express."""
    log = transcript or Transcript()
    messages: list[Message] = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    problem = ""
    for attempt in range(retries + 1):
        if attempt:
            log.retries += 1
        log.calls += 1
        reply = llm.complete(messages)
        try:
            parsed = model.model_validate(extract_json(reply))
            if check is not None:
                check(parsed)
            return parsed
        except (json.JSONDecodeError, ValidationError, InvalidReply) as e:
            problem = _describe(e)
        messages += [
            {"role": "assistant", "content": reply[:8000]},
            {
                "role": "user",
                "content": f"That reply has problems:\n{problem}\n"
                "Reply again with the whole corrected JSON object only.",
            },
        ]
    log.failures.append(f"{model.__name__}: {problem}")
    return None


def _describe(e: Exception) -> str:
    if isinstance(e, ValidationError):
        lines = [
            f"- {'.'.join(str(p) for p in err['loc']) or 'reply'}: {err['msg']}"
            for err in e.errors()[:12]
        ]
        return "\n".join(lines)
    if isinstance(e, json.JSONDecodeError):
        return f"- not valid JSON ({e.msg})"
    return "\n".join(f"- {line}" for line in str(e).splitlines() if line.strip())
