"""Image generation: Scintilla draws when someone asks her to. The satellite's router (satellite.py) decides that a
message asks for a picture and writes a short request ("draw": ...). This module does the rest:

1. prompt:   a vision model (DeepSeek :vision, low reasoning) reads the request, the last turns and the attached images
             (the newest message's and the replied-to one's, e.g. her own earlier drawing) and writes the image prompt.
             The image model is text-to-image only, so an "edit" of an attached picture is a redraw from its description.
2. generate: the image model (Recraft via OpenRouter's Images API) draws it.
3. note:     a system note for her reply ("you drew: ..."), the picture is attached to that reply.

The prompt used is kept on her reply (MsgNode.drawing), so "make it bluer" changes that prompt instead of starting over.
Runs after the satellite, with its own timeouts (`writer_timeout`, `image_timeout`); any failure means a "didn't work out" note, never a lost reply.
"""

import asyncio
from base64 import b64decode
from datetime import datetime
import json
import logging
import re
import time
from typing import Any

import httpx
from openai import AsyncOpenAI

from satellite import JSON_RE, text_of, transcript

PROMPT_WRITER = """You write prompts for a text-to-image model for Scintilla, a bee in a Discord chat, who was asked to draw something.
You get the chat (oldest first), the request, and maybe pictures: the ones attached to the newest message or to the message it replies to.
Write the image prompt in English, 40-120 words: subject, composition, setting, colors, lighting, style.
- If the request is about an attached picture (change it, replace its text, add or remove something, redraw it in a style), \
the image model can't see it: describe that picture in detail (layout, subjects, colors, style, every text on it) and apply the change.
- Text on the picture: put it in double quotes, exactly as asked, in its original language and spelling.
- Style: what the request asks for; if nothing, {style}
- No real people's likenesses, nothing sexual, no gore. If the request needs that, refuse.
Reply with JSON only: {{"prompt": "...", "aspect_ratio": "<one of {ratios}>", "caption": "<what is on the picture, \
under 15 words, in {language}>"}} or {{"refuse": "<why, under 10 words, in {language}>"}}"""

NOTES = {
    "ru": dict(
        drawn="Ты умеешь рисовать и переделывать картинки. Просьбу ты выполнила: картинка готова и прикреплена к твоему ответу. На ней: {caption}. Ответь как обычно, по-пчелиному, можешь коротко сказать о ней пару слов, промпт не пересказывай.",
        refused="Тебя попросили нарисовать картинку, но ты не стала: {reason}. Скажи это по-пчелиному.",
        failed="Тебя попросили нарисовать картинку, но не получилось (кисточка сломалась). Скажи это по-пчелиному, можно попробовать позже.",
        limited="Тебя попросили нарисовать картинку, но ты уже много рисовала и устала. Скажи это по-пчелиному, пусть попросят попозже.",
    ),
    "en": dict(
        drawn="You can draw and change pictures. You did what was asked: the picture is ready and attached to your reply. It shows: {caption}. Answer as usual, buzzing, maybe say a word or two about it, don't recite the prompt.",
        refused="You were asked to draw a picture but you didn't: {reason}. Say so, buzzing.",
        failed="You were asked to draw a picture but it didn't work out (your brush broke). Say so, buzzing; they can try later.",
        limited="You were asked to draw a picture but you've drawn a lot already and you're tired. Say so, buzzing; they can ask later.",
    ),
}

LANGUAGES = {"ru": "Russian", "en": "English"}
RATIOS = ("1:1", "2:1", "1:2", "3:2", "2:3", "4:3", "3:4", "5:4", "4:5", "16:9", "9:16")
DEFAULT_STYLE = "a clean, colorful digital illustration."

# user ID -> times of their drawings, for `per_user_per_hour`
drawn_at: dict[int, list[float]] = {}


def images_of(messages: list[dict[str, Any]], limit: int = 2) -> list[dict[str, Any]]:
    """The image parts of the newest message and the one before it (what it replies to), newest first."""
    return [part for msg in messages[:2] if isinstance(msg["content"], list) for part in msg["content"] if part.get("type") == "image_url"][:limit]


def allowed(user_id: int, per_hour: int) -> bool:
    now = time.monotonic()
    recent = drawn_at[user_id] = [t for t in drawn_at.get(user_id, []) if now - t < 3600]
    return len(recent) < per_hour


class Painter:
    def __init__(self, cfg: dict[str, Any], providers: dict[str, Any], http: httpx.AsyncClient):
        provider, self.writer_model = cfg["writer"].removesuffix(":vision").split("/", 1)
        writer_provider = providers[provider]
        self.writer = AsyncOpenAI(base_url=writer_provider["base_url"], api_key=writer_provider.get("api_key", "sk-no-key-required"))
        self.writer_params = cfg.get("writer_params") or {}
        provider, self.image_model = cfg["model"].split("/", 1)
        self.image_base = providers[provider]["base_url"].rstrip("/")
        self.image_key = providers[provider].get("api_key")
        self.style = cfg.get("style") or DEFAULT_STYLE
        self.max_images = cfg.get("max_input_images", 2)
        self.http = http

    async def write_prompt(self, request: str, messages: list[dict[str, Any]], lang: str) -> dict[str, Any]:
        system = PROMPT_WRITER.format(style=self.style, ratios=", ".join(RATIOS), language=LANGUAGES[lang])
        text = f"Today is {datetime.now().astimezone():%Y-%m-%d}.\n\nChat:\n{transcript(messages)}\n\nRequest: {request}"
        images = images_of(messages, self.max_images)
        resp = await self.writer.chat.completions.create(
            model=self.writer_model,
            messages=[dict(role="system", content=system), dict(role="user", content=[dict(type="text", text=text)] + images if images else text)],
            max_tokens=self.writer_params.get("max_tokens", 4000),
            extra_body={k: v for k, v in self.writer_params.items() if k != "max_tokens"} or None,
        )
        reply = text_of(resp.choices[0].message.content).strip()
        try:
            return json.loads(JSON_RE.search(reply).group())
        except (AttributeError, ValueError):
            raise RuntimeError(f"bad prompt writer reply {reply[:300]!r}")

    async def generate(self, prompt: str, aspect_ratio: str) -> tuple[bytes, str]:
        resp = await self.http.post(
            f"{self.image_base}/images",
            headers={"Authorization": f"Bearer {self.image_key}"},
            json=dict(model=self.image_model, prompt=prompt, n=1, aspect_ratio=aspect_ratio if aspect_ratio in RATIOS else "1:1"),
            timeout=60,
        )
        if resp.is_error:
            raise RuntimeError(f"images HTTP {resp.status_code}: {resp.text[:300]}")
        image = resp.json()["data"][0]
        return b64decode(image["b64_json"]), image.get("media_type") or "image/png"


async def draw(request: str, messages: list[dict[str, Any]], lang: str, user_id: int, cfg: dict[str, Any], providers: dict[str, Any], http: httpx.AsyncClient) -> dict[str, Any]:
    """dict(note=..., image=bytes|None, media_type, prompt): the note for her reply and, when it worked, the picture."""
    result: dict[str, Any] = dict(note=None, image=None, media_type=None, prompt=None)
    notes = NOTES[lang]
    if not allowed(user_id, cfg.get("per_user_per_hour", 10)):
        logging.info(f"Imagegen: user {user_id} hit per_user_per_hour")
        result["note"] = notes["limited"]
        return result

    started = time.monotonic()
    try:
        painter = Painter(cfg, providers, http)
        plan = await asyncio.wait_for(painter.write_prompt(request, messages, lang), cfg.get("writer_timeout", 30))
        if not plan.get("prompt"):
            logging.info(f"Imagegen: refused {request!r}: {plan.get('refuse')!r}")
            result["note"] = notes["refused"].format(reason=(plan.get("refuse") or ("не захотела" if lang == "ru" else "didn't want to")).rstrip("."))
            return result
        logging.info(f"Imagegen: prompt ({time.monotonic() - started:.1f} s) {plan['prompt']!r}")
        result["image"], result["media_type"] = await asyncio.wait_for(painter.generate(plan["prompt"], plan.get("aspect_ratio", "1:1")), cfg.get("image_timeout", 60))
    except Exception as e:
        logging.warning(f"Imagegen: failed ({type(e).__name__}: {e})")
        result["note"] = notes["failed"]
        return result

    drawn_at.setdefault(user_id, []).append(time.monotonic())
    logging.info(f"Imagegen: done in {time.monotonic() - started:.1f} s ({len(result['image']) // 1024} KB)")
    result["prompt"] = plan["prompt"]
    result["note"] = notes["drawn"].format(caption=re.sub(r"\s+", " ", plan.get("caption") or request).rstrip("."))
    return result
