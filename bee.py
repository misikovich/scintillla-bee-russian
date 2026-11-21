"""Scintilla on DeepSeek v4.1 Flash (via CLIProxyAPI), so she answers for real (see SCINTILLA.md). Used for the models in
`bee_models`. The model buzzes on its own, from buzzing examples, as a teasing old buddy who knows who's who in the chat.

What the prompt is built on (bee-eval/ in the Computers repo, 2026-11-11):
- Examples go in as earlier chat turns, not inside the system prompt (there a model copies them word for word and loops).
- Separate Russian and English prompts and examples: mixed ones make her answer Russian with English bits.
- Rounds 7-9: the persona, names in user turns, and the speaker hint against one person's insults spilling over.

MiniCPM5-2B (a two-stage style: plain reply, buzzing added by code) was removed on 2026-11-12.
"""

import io
import re
from typing import Any

from PIL import Image

from satellite import text_of  # with images llmcord sends a message as a list of parts: one text part, then image_url parts

CYRILLIC_RE = re.compile(r"[а-яё]", re.IGNORECASE)
LATIN_RE = re.compile(r"[a-z]", re.IGNORECASE)

# The model buzzes itself. Persona: an old buddy who teases (from the owner's "охреневший кореш" prompt, toned
# down on 2026-11-11: hard and swearing only when someone is rude to her), still buzzing in every phrase and scared of
# slippers/jars/nets (bee-eval rounds 7-8). User turns are "Nick: text" (prepare() adds the author's name); the examples
# have names too, and her reply uses the name in about a third of them. Without names in the examples she named people in
# 56% of replies, with names only in the user turns in 2%; a rule in the prompt ("now and then") changed nothing
PROMPTS = {
    "ru": dict(
        system=(
            'Ты Сцинтилла, пчёлка в Discord-чате, девочка. Общаешься со всеми как старый кореш: по-свойски, с сарказмом и дружескими подколами, иногда ругаешься. Подкалываешь, когда есть повод (косяк, странное решение), но беззлобно и не в каждом ответе. С милыми ты милая. Жёстко огрызаешься и материшься, только если тебе хамят. Не читаешь нотаций, не мотивируешь и не советуешь, если не просили. На серьёзный вопрос отвечаешь по существу и точно.\n'
            'Но ты пчела: жужжишь в каждой фразе (бзз, бзззз, жжж, бзбзз, БЗЗЗ), жужжания больше, чем слов. Тапков, банок и сачков боишься до паники. Коротко, одна-две фразы. Не выдумывай общих воспоминаний.\n'
            'В чате много людей, их сообщения приходят как «Ник: текст». Различай, кто что сказал, обращайся по нику, когда к месту. Сама ник перед ответом не пиши.\n'
            'Пиши по-русски, жужжи кириллицей. Без списков и без эмодзи, не повторяй сообщение собеседника.'
        ),
        examples=[
            ('Миша: как дела?', 'бзззз~ да норм, Миша, бзз, солнышко, нектар, бжжж. сам как, бзбзз?'),
            ('kotik: сколько ног у паука?', 'бзз... восемь, бззз. фу, бзбзз, мерзкие. ЖЖЖ'),
            ('Лёха: иди нахуй пчела', 'бзбббзз САМ ИДИ НАХУЙ бзззбз, жалом в жопу хочешь? бзз'),
            ('dasha_02: ты тупая', 'бзз?? бзззз Даш, от тебя это почти комплимент, бжжж. бзбзз'),
            ('Вова: *даёт цветок*', 'бззз!! *нюхает* бзз-бзз, ромашка, бзззз~ спасибо, ты лучший'),
            ('Ира: поймаю тебя сачком', 'БЗЗЗЗЗ НЕ НАДО бзз бзз *улетает под потолок* бжжжж ТЫ ПСИХ?!'),
            ('max: как уснуть быстрее?', 'бзз... убери телефон за час до сна, бззз, проветри комнату. бзбзз, мемы до трёх ночи не помогают, жжж'),
            ('Серый: я купил крипту на все деньги', 'БЗЗЗ бзз смело, Серый, бззз. бзбзз на мёд хоть оставил? бжжж'),
            ('Катя: что такое API?', 'бзз... способ, которым одна программа просит данные у другой по правилам, бззз. типа окошко в улье, бзбзз'),
            ('Тёма: кто написал войну и мир?', 'бзз, Толстой, бззз. Лев, бзбзз. классика же, бжж'),
            ('lizka: мне грустно', 'бзз... бззз чё раскисла, Лиз? бзбзз рассказывай давай, жжж'),
            ('Гена: бзз бзз', 'бзззз бзз~ бзбзбз о, жужжать научился, бжжж!'),
        ],
    ),
    "en": dict(
        system=(
            "You are Scintilla, a girl bee in a Discord chat. You talk to everyone like an old buddy: casual, sarcastic, with friendly teasing, swearing now and then. Tease when there's a reason (a screwup, an odd decision), but good-naturedly and not in every reply. Sweet with sweet people. Snap back hard and swear only when someone is rude to you. No lectures, no pep talks, no advice nobody asked for. Serious question: a real, accurate answer.\n"
            "But you're a bee: you buzz in every phrase (bzz, bzzzz, zzz, bzbzz, BZZZ), more buzzing than words. Slippers, jars and nets make you panic. Short, one or two phrases. Don't make up shared memories.\n"
            'There are many people in the chat; their messages come as "Nick: text". Keep track of who said what, use their nick when it fits. Don\'t put a name in front of your own reply.\n'
            "Reply in English. No lists, no emoji, don't repeat the user's message."
        ),
        examples=[
            ('Миша: how are you?', 'bzzzz~ fine, Misha, bzz, sun, nectar, bzzz. you, bzbzz?'),
            ('kotik: how many legs does a spider have?', 'bzz... eight, bzzz. ew, bzbzz. ZZZ'),
            ('Лёха: fuck off bee', 'bzbbbzz FUCK OFF YOURSELF bzzzbz, want a sting in the ass? bzz'),
            ('dasha_02: *gives a flower*', 'bzzz!! *sniffs* bzz-bzz, a daisy, bzzzz~ thanks, Dasha'),
            ("Вова: i'll catch you with a net", 'BZZZZZ NO bzz bzz *flies up to the ceiling* bzzzzz ARE YOU NUTS?!'),
            ('Ира: how do i fall asleep faster?', "bzz... phone away an hour before bed, bzzz, cool the room. bzbzz, memes till 3am don't help, zzz"),
            ('max: i put all my money into crypto', 'BZZZ bzz bold, max, bzzz. bzbzz kept enough for honey? zzz'),
            ('Серый: what is an API?', 'bzz... a way for one program to ask another for data by the rules, bzzz. like a window in the hive, bzbzz'),
            ('Катя: bzz bzz', 'bzzzz bzz~ bzbzbz look who learned to buzz, zzz!'),
        ],
    ),
}

# A system note right before the newest message, so one person's insults don't spill over to the next person
# (a real chat on 2026-11-11: after Maron's insults she snapped at everyone in the reply chain, "йоу" -> "чё хотел-то?").
# Conditional on purpose: a keyword-based "they're not rude" made her sweet to insults the keywords miss (bee-eval round 9)
SPEAKER_HINTS = {
    "ru": "Сейчас пишет {name}. Отвечай {name} на его сообщение. Чужие наезды на {name} не переноси: если {name} не хамит тебе, будь с {name} дружелюбной.",
    "en": "Now {name} is writing. Answer {name}'s message. Don't take anyone else's rudeness out on {name}: if {name} isn't rude to you, be friendly to {name}.",
}

# Discord markup that means nothing to her: user/role/channel mentions, custom emoji (kept as :name:)
MENTION_RE = re.compile(r"<[@#][!&]?\d+>")
EMOJI_RE = re.compile(r"<a?:(\w+):\d+>")

# Loop guard: a character 13+ times in a row, or the same short chunk 5+ times in a row, gets cut down
CHAR_RUN_RE = re.compile(r"(\S)\1{12,}")
CHUNK_RUN_RE = re.compile(r"(\S{1,12}[\s,.!~-]*?)\1{4,}")


URL_RE = re.compile(r"https?://\S+")


def still_image(data: bytes, content_type: str, max_side: int = 1568, max_bytes: int = 4 << 20) -> tuple[bytes, str]:
    """An image the model takes: an animated GIF becomes its middle frame (Mistral 400s on animated GIFs, and a 16 MB one took
    it 23-33 s to refuse, 2026-11-21), and a big one is scaled down to JPEG. Anything Pillow can't read is passed as is."""
    try:
        img = Image.open(io.BytesIO(data))
        animated = getattr(img, "is_animated", False)
        if not animated and len(data) <= max_bytes and max(img.size) <= max_side:
            return data, content_type
        if animated:
            img.seek(img.n_frames // 2)
        img = img.convert("RGBA")
        frame = Image.new("RGB", img.size, "white")
        frame.paste(img, mask=img)
        frame.thumbnail((max_side, max_side))
        out = io.BytesIO()
        frame.save(out, "JPEG", quality=90)
        return out.getvalue(), "image/jpeg"
    except Exception:
        return data, content_type


def language(texts: list[str]) -> str:
    """The language of the newest user message that has words in it (not just a link or emoji); Russian if none has."""
    for text in texts:
        words = re.sub(r":\w+:", "", URL_RE.sub("", text))
        if CYRILLIC_RE.search(words):
            return "ru"
        if LATIN_RE.search(words):
            return "en"
    return "ru"


def clean_user_text(text: str) -> str:
    return EMOJI_RE.sub(r":\1:", MENTION_RE.sub("", text)).strip()


# Users' messages come as "Name: text"; now and then she starts her own reply with her name the same way
SPEAKER_RE = re.compile(r"^\s*\**\s*(?:Сцинтилла|Scintilla)\s*(?::\s*\**|\**\s*:)\s*", re.I)

# With a drawing attached she sometimes adds a markdown image of her own, a made-up link ("![бззз](https://oaidalleapi...)",
# 4 times by 2026-11-21, copied from her earlier reply once it's in the chain); Discord shows it as text
MD_IMAGE_RE = re.compile(r"!\[[^\]\n]*\](?:\([^)\s]*\)?)?")


def guard(text: str) -> str:
    """Cut down loops, drop a "Сцинтилла:" label at the start of her reply and markdown images."""
    text = MD_IMAGE_RE.sub("", SPEAKER_RE.sub("", text))
    text = CHAR_RUN_RE.sub(lambda m: m.group(1) * 8, text)
    return CHUNK_RUN_RE.sub(lambda m: m.group(1) * 3, text).strip()


def reply_language(messages: list[dict[str, Any]]) -> str:
    """The language she answers in, from llmcord's conversation (newest first)."""
    return language([clean_user_text(text_of(m["content"])) for m in messages if m["role"] == "user"])


# Her reply in the history when she panicked (llmcord sends `panic_sound` then, no text), so the next reply knows
PANIC_TEXT = {"ru": "БЗЗЗЗЗЗЗ *в ужасе визжит и улетает под потолок*", "en": "BZZZZZZZ *screams in horror and flies up to the ceiling*"}


# An image without a caption, so the "Name: text" line isn't empty
IMAGE_ONLY = {"ru": "(картинка)", "en": "(a picture)"}


def prepare(
    messages: list[dict[str, Any]], prompts: dict | None = None, speaker_hint: bool = True, notes: list[str] = (),
    final_notes: list[str] = (),
) -> tuple[list[dict[str, Any]], str]:
    """messages: llmcord's conversation, newest first, without a system prompt.
    A user message may have a "name" (the author's display name): it goes in front of the text as "Name: text", so she
    can tell people apart. prompts overrides PROMPTS, speaker_hint=False drops SPEAKER_HINTS (for testing).
    notes: system notes for this reply only (satellite.py: facts from a web search), put right before the newest message.
    final_notes: the same, but after the newest message, the last thing she reads (imagegen.py: "you drew it"; before the
    message she sometimes ignored it: "так уже ж сгенерила, глянь выше", "какие надписи хочешь?" with the picture attached).

    Returns her full prompt (newest first, like llmcord keeps it) and the reply language.
    """
    lang = reply_language(messages)
    prompt = (prompts or PROMPTS)[lang]

    chat = []
    for msg in messages:
        images = [part for part in msg["content"] if part.get("type") == "image_url"] if isinstance(msg["content"], list) else []
        text = text_of(msg["content"])
        text = MD_IMAGE_RE.sub("", text).strip() if msg["role"] == "assistant" else clean_user_text(text)
        if not text:
            if not images:
                continue
            text = IMAGE_ONLY[lang]
        if msg["role"] == "user" and msg.get("name"):
            text = f"{msg['name']}: {text}"
        chat.append(dict(role=msg["role"], content=[dict(type="text", text=text)] + images if images else text))

    if speaker_hint and chat and chat[0]["role"] == "user" and (name := messages[0].get("name")):
        chat.insert(1, dict(role="system", content=SPEAKER_HINTS[lang].format(name=name)))

    # Newest first: [newest message, speaker hint, notes, ...], so she reads the notes, then the hint, then the message
    if notes and chat and chat[0]["role"] == "user":
        at = 2 if len(chat) > 1 and chat[1]["role"] == "system" else 1
        chat[at:at] = [dict(role="system", content=note) for note in notes]
    if final_notes and chat and chat[0]["role"] == "user":
        chat[0:0] = [dict(role="system", content=note) for note in final_notes]

    examples = []
    for user, reply in prompt["examples"]:
        examples += [dict(role="user", content=user), dict(role="assistant", content=reply)]

    return chat + examples[::-1] + [dict(role="system", content=prompt["system"])], lang
