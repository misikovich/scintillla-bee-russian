"""Russian <-> English translation for BeeLLM, which only understands English (see SCINTILLA.md).

Kept out of llmcord.py so the upstream diff stays small.
"""

import logging
import re
from typing import Any, Literal

from openai import AsyncOpenAI

CYRILLIC_RE = re.compile(r"[а-яё]", re.IGNORECASE)

# BeeLLM starts every reply with a "[mood:level]" tag; it's kept as is, only the text after it is translated
MOOD_TAG_RE = re.compile(r"\s*\[[^\]\n]*\]\s*")

# Small models sometimes wrap the translation in quotes or label it despite the prompt
LABEL_RE = re.compile(r"^(translation|перевод)\s*:\s*", re.IGNORECASE)
QUOTE_PAIRS = ('""', "«»", "“”", "''")

PROMPTS = {
    "en": dict(
        system=(
            "You translate Russian Discord messages written to Scintilla, a cute bee, into casual English. "
            "Translate exactly what is written: never add words, sounds or emoji that aren't in the message. "
            "Keep *actions in asterisks*, emoji and names. Write Russian buzzing (жжж, вжух) as bzz. "
            "Reply with the translation only: no quotes, notes or explanations."
        ),
        examples=[
            ("привет пчёлка! *протягивает баночку мёда*", "hi little bee! *holds out a jar of honey*"),
            ("посажу тебя в банку", "i'll put you in a jar"),
            ("жжж 🐝", "bzz 🐝"),
        ],
    ),
    "ru": dict(
        system=(
            "You translate the replies of Scintilla, a cute female bee, from English into casual Russian. "
            "She talks in short fragments and repeats herself: keep that, don't fix or explain it. Use feminine forms for her. "
            "Copy bee sounds and noises (bzz, BZZZT, zzz, zip, brrr, AAAA, hmm and the like) exactly as written, letter for letter. "
            "Translate only real English words; if there are none, return the text unchanged. Never add anything. "
            "Keep *actions in asterisks* and emoji. Reply with the translation only: no quotes, notes or explanations."
        ),
        examples=[
            ("zzzz! *does a victory lap does a victory lap*", "zzzz! *делает круг почёта делает круг почёта*"),
            ("BZzzZZT bzz?? brrr~", "BZzzZZT bzz?? brrr~"),
            ("builds a tiny nest on the honey zip zip", "строит крошечное гнёздышко на мёде zip zip"),
        ],
    ),
}

# Her bee sounds are left in Latin by the translator, then spelled in Cyrillic (bzz -> бзз, zip zip -> зип зип)
LATIN_WORD_RE = re.compile(r"[a-z]+", re.IGNORECASE)
TRANSLIT = {
    "sh": "ш", "ch": "ч", "zh": "ж", "kh": "х", "ts": "ц",
    "a": "а", "b": "б", "c": "к", "d": "д", "e": "е", "f": "ф", "g": "г", "h": "х", "i": "и", "j": "дж", "k": "к", "l": "л", "m": "м",
    "n": "н", "o": "о", "p": "п", "q": "к", "r": "р", "s": "с", "t": "т", "u": "у", "v": "в", "w": "в", "x": "кс", "y": "й", "z": "з",
}  # fmt: skip


def has_russian(text: str | None) -> bool:
    return bool(text and CYRILLIC_RE.search(text))


def transliterate(text: str) -> str:
    def word(match: re.Match) -> str:
        latin, cyrillic, i = match.group(), "", 0
        while i < len(latin):
            chunk = latin[i : i + 2] if latin[i : i + 2].lower() in TRANSLIT else latin[i]
            letter = TRANSLIT[chunk.lower()]
            cyrillic += letter.upper() if chunk[0].isupper() else letter
            i += len(chunk)
        return cyrillic

    return LATIN_WORD_RE.sub(word, text)


def clean(text: str) -> str:
    text = LABEL_RE.sub("", text.strip())
    if len(text) >= 2 and text[0] + text[-1] in QUOTE_PAIRS:
        text = text[1:-1].strip()
    return text


async def translate(text: str, to: Literal["en", "ru"], translator_config: dict[str, Any]) -> str:
    """Translate text with the translator LLM; returns it unchanged if that fails."""
    tag = ""
    if tag_match := MOOD_TAG_RE.match(text):
        tag, text = tag_match.group().strip(), text[tag_match.end() :]

    if not text.strip():
        return f"{tag} {text}".strip()

    prompt = PROMPTS[to]
    messages = [dict(role="system", content=prompt["system"])]
    for source, target in prompt["examples"]:
        messages += [dict(role="user", content=source), dict(role="assistant", content=target)]
    messages.append(dict(role="user", content=text))

    try:
        async with AsyncOpenAI(base_url=translator_config["base_url"], api_key=translator_config.get("api_key", "sk-no-key-required"), timeout=30) as client:
            response = await client.chat.completions.create(
                model=translator_config.get("model", "translator"),
                messages=messages,
                temperature=0,
                max_tokens=translator_config.get("max_tokens", 500),
                extra_body=translator_config.get("extra_body"),
            )
        translation = clean(response.choices[0].message.content or "")
        if to == "ru":
            translation = transliterate(translation)
    except Exception:
        logging.exception("Error while translating, using the original text")
        translation = ""

    return f"{tag} {translation or text}".strip()
