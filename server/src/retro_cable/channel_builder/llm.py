"""The local model behind the channel builder: an OpenAI-compatible chat endpoint (LiteLLM on
the rig). Nothing here calls a cloud model; the URL, key and model come from the environment:

  RC_LLM_URL    base URL of the endpoint, default http://rig:4000 (LiteLLM)
  RC_LLM_KEY    bearer key (the LiteLLM master or a virtual key); never logged
  RC_LLM_MODEL  model alias, default `chat`

Replies must be JSON. `ask_json` parses and validates a reply and, when it is invalid, sends
the problems back to the model and asks again, a bounded number of times.

The rig is shared with other jobs, so a busy endpoint (a timeout, 429 or 5xx) is asked again
after a pause, and `ReplyLog` keeps every reply on disk so a run cut short can resume.
"""

import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
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
    def __init__(self, message: str, transient: bool = False) -> None:
        super().__init__(message)
        self.transient = transient  # the endpoint was busy or down; asking again may work


BUSY = {408, 429, 500, 502, 503, 504}


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
    attempts: int = 4
    backoff: float = 60.0  # seconds, times the attempt number

    @staticmethod
    def from_env() -> "OpenAIChat":
        return OpenAIChat(
            url=os.environ.get("RC_LLM_URL", DEFAULT_URL).rstrip("/"),
            key=os.environ.get("RC_LLM_KEY", ""),
            model=os.environ.get("RC_LLM_MODEL", DEFAULT_MODEL),
        )

    def complete(self, messages: list[Message]) -> str:
        for attempt in range(1, self.attempts + 1):
            try:
                return self._post(messages)
            except LLMError as e:
                if not e.transient or attempt == self.attempts:
                    raise
                time.sleep(self.backoff * attempt)
        raise AssertionError("unreachable")

    def _post(self, messages: list[Message]) -> str:
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
            raise LLMError(f"model endpoint answered HTTP {e.code}", e.code in BUSY) from e
        except (urllib.error.URLError, TimeoutError) as e:
            raise LLMError(f"model endpoint unreachable at {self.url}: {e}", True) from e
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as e:
            raise LLMError("model endpoint gave no message") from e
        return str(content or "")


class ReplyLog:
    """A model whose replies are appended to a JSON-lines file, keyed by a hash of the chat.

    With `replay`, chats already in the file are answered from it, so a proposal cut short by
    the endpoint resumes where it stopped: the prompts are deterministic up to that point, and
    the first new chat goes to the model. Without it the file starts empty. Only the model's
    replies are stored, never the key."""

    def __init__(self, inner: LLM, path: Path, replay: bool = False) -> None:
        self.inner, self.path, self.model = inner, path, inner.model
        self.replayed = 0
        self._known: dict[str, str] = {}
        path.parent.mkdir(parents=True, exist_ok=True)
        if replay and path.exists():
            for line in path.read_text().splitlines():
                try:
                    entry = json.loads(line)
                    self._known[entry["key"]] = entry["reply"]
                except (json.JSONDecodeError, KeyError, TypeError):
                    continue  # a line cut off when the last run died
        else:
            path.write_text("")

    def complete(self, messages: list[Message]) -> str:
        key = hashlib.sha256(json.dumps([self.model, messages]).encode()).hexdigest()
        if key in self._known:
            self.replayed += 1
            return self._known[key]
        reply = self.inner.complete(messages)
        with self.path.open("a") as f:
            f.write(json.dumps({"key": key, "reply": reply}) + "\n")
        self._known[key] = reply
        return reply


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
