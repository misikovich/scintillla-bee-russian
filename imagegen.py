"""Image generation: Scintilla draws when someone asks her to. The satellite's router (satellite.py) decides that a
message asks for a picture and writes a short request ("draw": ...). This module does the rest:

1. prompt:   a vision model (DeepSeek :vision, low reasoning) reads the request, the last turns and the attached images
             (the newest message's and the replied-to one's, e.g. her own earlier drawing) and writes the image prompt.
             The image model is text-to-image only, so an "edit" of an attached picture is a redraw from its description.
2. generate: the image model (Recraft via OpenRouter's Images API) draws it. With `free_model` set, every picture tries
             that one first (OpenRouter's `:free` tier, 50 requests/day) and quietly falls back to `model` when it fails
             (429 once the day's free requests are used up), so it goes back to free on its own the next day.
3. note:     a system note for her reply ("you drew: ..."), the picture is attached to that reply.

The prompt used is kept on her reply (MsgNode.drawing), so "make it bluer" changes that prompt instead of starting over.
Runs after the satellite, with its own timeouts (`writer_timeout`, `image_timeout`); any failure means a "didn't work out" note, never a lost reply.
"""

import asyncio
from base64 import b64decode, b64encode
from datetime import datetime
import json
import logging
import re
import time
from typing import Any

import httpx

import fallback
import viewer

from satellite import text_of, transcript

PROMPT_WRITER = """You write prompts for a text-to-image model for Scintilla, a bee in a Discord chat, who was asked to draw something.
You get the chat (oldest first), the request, and maybe pictures: the ones attached to the newest message or to the message it replies to.
Write the image prompt in English, 40-120 words: subject, composition, setting, colors, lighting, style.
- If the request is about an attached picture (change it, replace its text, add or remove something, redraw it in a style), \
the image model can't see it: describe that picture in detail (layout, subjects, colors, style, every text on it) and apply the change.
- Text on the picture: put it in double quotes, exactly as asked, in its original language and spelling.
- Style: what the request asks for; if nothing, {style}
- Cartoon, anime, game or meme characters (Homer, SpongeBob, Pepe, trollface...) and drawn things, unless a cartoon or \
drawing is asked for: show them as if they were born in real life, of flesh and blood, in a real photo: real skin with \
pores, blemishes, wrinkles and fine hairs, wet glossy eyes with eyelashes, real hair, lips and teeth, fabric and fur; \
humanoid ones get human skin. Keep their cartoon proportions and features exaggerated and recognizable (head shape, huge \
eyes, odd nose or mouth, spiky hair, skin color, clothes): not a normal human or animal, and not a toy, figurine, \
plush, costume, mask or 3D render. Hyperrealistic, uncanny and funny. Don't use the words cartoon, illustration, \
outlines or cel-shaded for them.
- Allowed: swearing, crude jokes and rude meme text, fictional characters, cartoon parodies, dark humor. Draw these, don't refuse.
- Obscene words in the picture's text (мат, words for genitals or sex, fuck, dick, cunt...): keep the first letter and \
replace the rest with *, one per letter ("п****", "х**", "иди н****", "f***"). Mild words (жопа, дурак, damn, shit) as written. \
The image model blocks the uncensored ones.
- A photo of a person (a movie or TV still, a celebrity, anyone) to change: don't refuse. It's a redraw from your \
description, not an edit of the photo: keep the original's style (a photo stays a photo, same lighting and framing) and \
describe their look (face, hair, clothes, pose); a fictional character's name is fine.
- A name to write ("его имя", "his name"): the name of the person or character meant, from the chat or written on the \
picture; never the requester's nick unless they say it's theirs. Never guess a name from a face: if it isn't in the chat \
or on the picture, reply {{"refuse": "<you don't know his/her name, ask them to write it>"}}.
- Refuse only: sexual images (nudity, sex acts), gore.
Reply with JSON only: {{"prompt": "...", "aspect_ratio": "<one of {ratios}>", "caption": "<what is on the picture, \
under 15 words, in {language}>"}} or {{"refuse": "<why, under 10 words, in {language}>"}}"""

NOTES = {
    "ru": dict(
        drawn="Ты умеешь рисовать и переделывать картинки. Просьбу ты выполнила: картинка готова и прикреплена к твоему ответу. На ней: {caption}. Ответь как обычно, по-пчелиному, можешь коротко сказать о ней пару слов, промпт не пересказывай. Не переспрашивай и не говори, что рисовала раньше: это новая картинка на это сообщение.",
        refused="Тебя попросили нарисовать картинку, но ты не стала: {reason}. Скажи это по-пчелиному.",
        failed="Тебя попросили нарисовать картинку, но не получилось (кисточка сломалась). Скажи это по-пчелиному, можно попробовать позже.",
        limited="Тебя попросили нарисовать картинку, но ты уже много рисовала и устала. Скажи это по-пчелиному, пусть попросят попозже.",
    ),
    "en": dict(
        drawn="You can draw and change pictures. You did what was asked: the picture is ready and attached to your reply. It shows: {caption}. Answer as usual, buzzing, maybe say a word or two about it, don't recite the prompt. Don't ask what to do and don't say you drew it before: this is a new picture for this message.",
        refused="You were asked to draw a picture but you didn't: {reason}. Say so, buzzing.",
        failed="You were asked to draw a picture but it didn't work out (your brush broke). Say so, buzzing; they can try later.",
        limited="You were asked to draw a picture but you've drawn a lot already and you're tired. Say so, buzzing; they can ask later.",
    ),
}

LANGUAGES = {"ru": "Russian", "en": "English"}
RATIOS = ("1:1", "2:1", "1:2", "3:2", "2:3", "4:3", "3:4", "5:4", "4:5", "16:9", "9:16")
DEFAULT_STYLE = "a hyperrealistic photo: real camera, real light, detailed skin, hair, fur and fabric textures, shallow depth of field."

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
        self.writer_params = cfg.get("writer_params") or {}
        params = {k: v for k, v in self.writer_params.items() if k != "max_tokens"}
        self.writers = fallback.attempts(cfg["writer"], params, cfg.get("writer_fallback"), cfg.get("writer_fallback_params"), providers)
        self.image_models = [self.image_endpoint(cfg[key], providers) for key in ("free_model", "model") if cfg.get(key)]
        self.free_timeout = cfg.get("free_timeout", 20)
        self.style = cfg.get("style") or DEFAULT_STYLE
        self.max_images = cfg.get("max_input_images", 2)
        self.http = http

    async def write_prompt(self, request: str, messages: list[dict[str, Any]], lang: str) -> dict[str, Any]:
        system = PROMPT_WRITER.format(style=self.style, ratios=", ".join(RATIOS), language=LANGUAGES[lang])
        text = f"Today is {datetime.now().astimezone():%Y-%m-%d}.\n\nChat:\n{transcript(messages)}\n\nRequest: {request}"
        images = images_of(messages, self.max_images)
        reply = await fallback.complete(
            self.writers, "draw", "writer", lambda resp: text_of(resp.choices[0].message.content).strip(),
            messages=[dict(role="system", content=system), dict(role="user", content=[dict(type="text", text=text)] + images if images else text)],
            max_tokens=self.writer_params.get("max_tokens", 4000),
        )
        try:
            # The first object only: now and then it writes a second one after it
            return json.JSONDecoder().raw_decode(reply[reply.index("{"):])[0]
        except ValueError:
            raise RuntimeError(f"bad prompt writer reply {reply[:300]!r}")

    @staticmethod
    def image_endpoint(name: str, providers: dict[str, Any]) -> tuple[str, str, str | None]:
        provider, model = name.split("/", 1)
        return model, providers[provider]["base_url"].rstrip("/"), providers[provider].get("api_key")

    async def generate(self, prompt: str, aspect_ratio: str) -> tuple[bytes, str]:
        """Tries the image models in order (the free one first, if set); the last one's error is raised."""
        for i, (model, base, key) in enumerate(self.image_models):
            last = i == len(self.image_models) - 1
            try:
                with viewer.timed("draw", "image", model=model) as call:
                    resp = await self.http.post(
                        f"{base}/images",
                        headers={"Authorization": f"Bearer {key}"},
                        json=dict(model=model, prompt=prompt, n=1, aspect_ratio=aspect_ratio if aspect_ratio in RATIOS else "1:1"),
                        timeout=60 if last else self.free_timeout,
                    )
                    if resp.is_error:
                        raise RuntimeError(f"images HTTP {resp.status_code}: {resp.text[:300]}")
                    body = resp.json()
                    image = body["data"][0]
                    call["usage"] = body.get("usage")
                viewer.stage("draw", "running", step="generate", model=model)
                return b64decode(image["b64_json"]), image.get("media_type") or "image/png"
            except Exception as e:
                if last:
                    raise
                logging.info(f"Imagegen: {model} failed ({type(e).__name__}: {e}), trying {self.image_models[i + 1][0]}")
        raise RuntimeError("no image model")


async def draw(request: str, messages: list[dict[str, Any]], lang: str, user_id: int, cfg: dict[str, Any], providers: dict[str, Any], http: httpx.AsyncClient) -> dict[str, Any]:
    """dict(note=..., image=bytes|None, media_type, prompt): the note for her reply and, when it worked, the picture."""
    result: dict[str, Any] = dict(note=None, image=None, media_type=None, prompt=None)
    notes = NOTES[lang]
    viewer.stage("draw", "running", step="prompt", request=request)
    if not allowed(user_id, cfg.get("per_user_per_hour", 10)):
        logging.info(f"Imagegen: user {user_id} hit per_user_per_hour")
        result["note"] = notes["limited"]
        viewer.stage("draw", "done", limited=True, note=result["note"])
        return result

    started = time.monotonic()
    try:
        painter = Painter(cfg, providers, http)
        plan = await asyncio.wait_for(painter.write_prompt(request, messages, lang), cfg.get("writer_timeout", 30))
        if not plan.get("prompt"):
            logging.info(f"Imagegen: refused {request!r}: {plan.get('refuse')!r}")
            result["note"] = notes["refused"].format(reason=(plan.get("refuse") or ("не захотела" if lang == "ru" else "didn't want to")).rstrip("."))
            viewer.stage("draw", "done", refused=plan.get("refuse") or True, note=result["note"])
            return result
        logging.info(f"Imagegen: prompt ({time.monotonic() - started:.1f} s) {plan['prompt']!r}")
        viewer.stage("draw", "running", step="generate", prompt=plan["prompt"], aspect_ratio=plan.get("aspect_ratio"), caption=plan.get("caption"))
        result["image"], result["media_type"] = await asyncio.wait_for(painter.generate(plan["prompt"], plan.get("aspect_ratio", "1:1")), cfg.get("image_timeout", 60))
    except Exception as e:
        logging.warning(f"Imagegen: failed ({type(e).__name__}: {e})")
        result["note"] = notes["failed"]
        viewer.stage("draw", "error", error=f"{type(e).__name__}: {e}", note=result["note"])
        return result

    drawn_at.setdefault(user_id, []).append(time.monotonic())
    logging.info(f"Imagegen: done in {time.monotonic() - started:.1f} s ({len(result['image']) // 1024} KB)")
    result["prompt"] = plan["prompt"]
    result["note"] = notes["drawn"].format(caption=re.sub(r"\s+", " ", plan.get("caption") or request).rstrip("."))
    viewer.stage("draw", "done", note=result["note"], image=f"data:{result['media_type']};base64,{b64encode(result['image']).decode()}", kb=len(result["image"]) // 1024)
    return result
