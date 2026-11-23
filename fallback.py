"""Fallback API: when a model's call fails (its provider down, 402 when the account runs out of budget, a timeout...),
the same request goes to a `fallback` model, with `fallback_params` merged over the model's own params. Used for her
chat model (`fallback:` in its `models:` entry), the satellite (`fallback:` in `satellite:`) and the draw prompt writer
(`writer_fallback:` in `imagegen:`).

The first model gets no retries and a short connect timeout when it has a fallback, so a dead provider costs little time.
`read_timeout` (seconds) shortens its read timeout too, for callers with a deadline of their own (the satellite).
"""

import logging
from typing import Any, AsyncIterator, Callable

import httpx
from openai import AsyncOpenAI

import viewer

# (client, model id at that provider, extra_body)
Attempt = tuple[AsyncOpenAI, str, dict[str, Any] | None]

# One AsyncOpenAI (and its connection pool) per provider and settings, not one per call
_clients: dict[tuple, AsyncOpenAI] = {}


def client(name: str, providers: dict[str, Any], max_retries: int | None = None, timeout: httpx.Timeout | None = None) -> tuple[AsyncOpenAI, str]:
    """An OpenAI client for "provider/model[:vision]" and the model id to send. Clients are cached."""
    provider, model = name.removesuffix(":vision").split("/", 1)
    cfg = providers[provider]
    api_key = cfg.get("api_key", "sk-no-key-required")
    key = (cfg["base_url"], api_key, max_retries, timeout and (timeout.connect, timeout.read))
    if key not in _clients:
        kwargs = {k: v for k, v in dict(max_retries=max_retries, timeout=timeout).items() if v is not None}
        _clients[key] = AsyncOpenAI(base_url=cfg["base_url"], api_key=api_key, **kwargs)
    return _clients[key], model


def attempts(
    name: str, params: dict[str, Any] | None, fallback: str | None, fallback_params: dict[str, Any] | None, providers: dict[str, Any],
    read_timeout: float | None = None,
) -> list[Attempt]:
    """The model, then its fallback (if any); each with its extra_body. With a fallback the model gets no retries and a
    short connect timeout; `read_timeout` also caps how long it may take to answer (60 s otherwise)."""
    params = params or {}
    quick = {}
    if fallback or read_timeout:
        quick = dict(timeout=httpx.Timeout(read_timeout or 60, connect=min(5.0, read_timeout) if read_timeout else 5))
        if fallback:
            quick["max_retries"] = 0
    result = [(*client(name, providers, **quick), params or None)]
    if fallback:
        result.append((*client(fallback, providers), (params | (fallback_params or {})) or None))
    return result


def log_failure(attempt: Attempt, e: Exception, next_attempt: Attempt) -> None:
    logging.warning(f"{attempt[1]} failed ({type(e).__name__}: {e}), falling back to {next_attempt[1]} at {next_attempt[0].base_url}")


async def complete(
    tries: list[Attempt], stage: str, what: str, reply: Callable[[Any], str], on_fallback: Callable[[], None] | None = None, **kwargs: Any,
) -> str:
    """chat.completions.create, non-streaming; returns reply(response). Each try is recorded in the viewer (stage, what).
    on_fallback() runs before each fallback try (the satellite restarts its deadline there)."""
    for i, (api, model, body) in enumerate(tries):
        try:
            with viewer.timed(stage, what, model=model) as call:
                resp = await api.chat.completions.create(model=model, extra_body=body, **kwargs)
                call["usage"] = viewer.usage(resp)
                call["reply"] = text = reply(resp)
            return text
        except Exception as e:
            if i == len(tries) - 1:
                raise
            log_failure(tries[i], e, tries[i + 1])
            if on_fallback:
                on_fallback()


async def stream(tries: list[Attempt], **kwargs: Any) -> AsyncIterator[Any]:
    """chat.completions.create(stream=True)'s chunks. Falls back only when a try fails before its first chunk: after
    that the reply is half made (and may be half sent), so an error mid-stream is raised."""
    for i, (api, model, body) in enumerate(tries):
        try:
            chunks = aiter(await api.chat.completions.create(model=model, extra_body=body, **kwargs))
            first = await anext(chunks)
        except StopAsyncIteration:
            return
        except Exception as e:
            if i == len(tries) - 1:
                raise
            log_failure(tries[i], e, tries[i + 1])
            viewer.stage("model", "running", model=tries[i + 1][1], params=tries[i + 1][2], fallback_from=model, fallback_error=f"{type(e).__name__}: {e}"[:500])
            continue
        yield first
        async for chunk in chunks:
            yield chunk
        return
