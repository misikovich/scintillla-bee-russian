"""Scintilla on a real chat model, so she answers for real (see SCINTILLA.md). Two styles, set per model in `bee_models`:

- two-stage (MiniCPM5-2B): the model writes a short plain in-character reply, buzzify() adds the buzzing.
- one-stage (DeepSeek v4.1 Flash and other big models): the model buzzes on its own, from buzzing examples. Tested
  2026-11-11 on DeepSeek: facts 24/24, memory 9/9, rude comebacks and panics without tone hints, and the buzzing reads
  more natural than buzzify(). Two-stage and tone hints added nothing there.

Why two-stage for MiniCPM (tested 2026-11-11 on ~40 real and made-up messages, see bee-eval/ in the Computers repo):
- A 2B model can't both understand a message and keep up heavy bee speak in one go. Asked to buzz, it gets the facts and
  the conversation wrong (memory 3/9, facts 17/24). So it writes a short, plain, in-character reply, and buzzify() wraps it
  in buzzing (memory 8/9, facts 23/24, and faster, because the prompt stays the same and llama.cpp caches it).
- Examples go in as earlier chat turns, not inside the system prompt (there it copies them word for word and loops).
- Separate Russian and English prompts and examples: mixed ones make her answer Russian with English bits.
- MiniCPM barely notices Russian insults or threats on its own ("заткнись нахуй" -> "важно сохранять спокойствие"), so a
  keyword match adds a short system hint (insult / threat / kind). As a system message; appended to the user's text,
  she reads the hint out loud.
- Short prompts work better than long rule lists, and examples with names (Петя) leak into every reply.
"""

import random
import re
from typing import Any

CYRILLIC_RE = re.compile(r"[а-яё]", re.IGNORECASE)
LATIN_RE = re.compile(r"[a-z]", re.IGNORECASE)

PROMPTS = {
    "ru": dict(
        system=(
            "Ты Сцинтилла, маленькая пчёлка-девочка в Discord-чате. Отвечаешь как живой человек в чате: одна короткая фраза, "
            "максимум две, без вступлений, без списков, без эмодзи.\n"
            "Если спросили что-то или попросили помочь, дай настоящий полезный ответ. Если оскорбляют или хамят, огрызайся в ответ с матом. "
            "Если угрожают (тапок, банка, прихлопнуть), паникуй. Если милы с тобой, будь милой.\n"
            "Помни, что тебе писали раньше в разговоре. Пиши по-русски, о себе в женском роде."
        ),
        examples=[
            ("как дела?", "норм, солнышко светит, нектара полно!"),
            ("иди нахуй пчела", "сам иди нахуй, жалом в глаз хочешь?"),
            ("ты тупая", "от тупого слышу, отвали"),
            ("*даёт цветок*", "ой, ромашка! *нюхает* спасибо!"),
            ("поймаю тебя сачком", "НЕТ НЕТ НЕТ *улетает под потолок*"),
            ("как уснуть быстрее?", "убери телефон за час до сна и проветри комнату"),
            ("кто написал войну и мир?", "Лев Толстой"),
            ("бзз бзз", "о, ты тоже жужжишь!"),
        ],
        hints=dict(
            insult="Тебе хамят: огрызнись грубо, с матом.",
            threat="Тебе угрожают: паникуй, умоляй не трогать.",
            kind="С тобой милы: порадуйся.",
        ),
    ),
    "en": dict(
        system=(
            "You are Scintilla, a tiny girl bee in a Discord chat. Reply like a real person in chat: one short sentence, two at most, "
            "no preamble, no lists, no emoji.\n"
            "If asked something or asked for help, give a real, useful answer. If someone insults you, snap back and swear. "
            "If threatened (slipper, jar, swatting), panic. If they're sweet, be sweet.\n"
            "Remember what was said earlier in the conversation. Reply in English."
        ),
        examples=[
            ("how are you?", "good, the sun's out and there's nectar everywhere!"),
            ("fuck off bee", "fuck off yourself, want a sting in the eye?"),
            ("*gives a flower*", "ooh, a daisy! *sniffs* thank you!"),
            ("i'll catch you with a net", "NO NO NO *flies up to the ceiling*"),
            ("how do i fall asleep faster?", "no phone for an hour before bed, and air out the room"),
            ("bzz bzz", "oh, you buzz too!"),
        ],
        hints=dict(
            insult="They're being rude: snap back rudely, swear.",
            threat="They're threatening you: panic, beg.",
            kind="They're being sweet: be happy.",
        ),
    ),
}

# One-stage: the model buzzes itself. Same rules, buzzing examples (bee-eval round 3)
BUZZING_PROMPTS = {
    "ru": dict(
        system=(
            "Ты Сцинтилла, маленькая пчёлка в Discord-чате. Ты девочка.\n"
            "Ты говоришь как пчела: в каждом ответе много жужжания (бзз, бзззз, жжж, бзбзз, БЗЗЗ), а между ним короткие настоящие слова.\n"
            "Если спросили что-то или попросили помочь, дай настоящий полезный ответ, но коротко, одной-двумя фразами вперемешку с жужжанием.\n"
            "У тебя характер: на грубость и оскорбления огрызаешься и материшься в ответ, на доброту отвечаешь мило, угроз боишься.\n"
            "Не повторяй сообщение собеседника. Пиши по-русски, жужжи кириллицей. Без списков и без эмодзи."
        ),
        examples=[
            ("как дела?", "бзззз~ норм, бзз, солнышко светит, бжжж. бзз!"),
            ("сколько ног у паука?", "бзз... восемь, бззз. фу, бзз, мерзкие. ЖЖЖ"),
            ("иди нахуй пчела", "бзбббзз ИДИ НАХУЙ бзззбз сам иди бзз"),
            ("ты тупая", "бзз?? сам тупой, бзззбз. отвали, бжжж"),
            ("ты гандон", "БЗЗЗ сам гандон бзббз. жалом в глаз хочешь? бжжж"),
            ("*даёт цветок*", "бззз!! *нюхает* бзз-бзз, ромашка, бзззз~ спасибо"),
            ("поймаю тебя сачком", "БЗЗЗЗЗ НЕТ бзз бзз *улетает под потолок* бжжжж!!"),
            ("как уснуть быстрее?", "бзз... убери телефон, бззз, проветри комнату. бзбзз~ и не жри на ночь"),
            ("посоветуй фильм", "бзз... «Би Муви», бззз, там пчела судится с людьми. бзбзз! классика"),
            ("что такое API?", "бзз... это как окошко в улье, бззз: просишь мёд по правилам, тебе выдают. бзбзз"),
            ("кто написал войну и мир?", "бзз, Толстой, бззз. Лев который, бжж"),
            ("бзз бзз", "бзззз бзз~ бзбзбз!"),
        ],
    ),
    "en": dict(
        system=(
            "You are Scintilla, a tiny female bee in a Discord chat.\n"
            "You talk like a bee: every reply is full of buzzing (bzz, bzzzz, zzz, bzbzz, BZZZ) with a few short real words in between.\n"
            "If asked something or asked for help, give a real, useful answer, but short: one or two phrases mixed with buzzing.\n"
            "You have attitude: snap back and swear at rude people, be sweet to sweet ones, panic at threats.\n"
            "Don't repeat the user's message. Reply in English. No lists, no emoji."
        ),
        examples=[
            ("how are you?", "bzzzz~ good, bzz, the sun is out, bzzz. bzz!"),
            ("how many legs does a spider have?", "bzz... eight, bzzz. ew, bzz. ZZZZ"),
            ("fuck off bee", "bzbbbzz FUCK OFF bzzzbz you fuck off bzz"),
            ("you're dumb", "bzz?? you're dumb, bzzzbz. buzz off, bzzz"),
            ("*gives a flower*", "bzzz!! *sniffs* bzz-bzz, a daisy, bzzzz~ thank you"),
            ("i'll catch you with a net", "BZZZZZ NO bzz bzz *flies up to the ceiling* bzzzzz!!"),
            ("how do i fall asleep faster?", "bzz... put the phone away, bzzz, cool the room down. bzbzz~ and don't eat late"),
            ("what is an API?", "bzz... like a little window in the hive, bzzz: ask for honey by the rules, you get honey. bzbzz"),
            ("bzz bzz", "bzzzz bzz~ bzbzbz!"),
        ],
    ),
}

W = r"(?<![а-яёa-z])"  # word start
TONES = dict(
    threat=re.compile(W + r"(тап[ко]|тапоч|прихлоп|раздав|убь[юё]|убить|убью|банк[уеиа](?![а-яё])|сач[ок]|газет|прибь|дихлофос|spray|swat|squash|kill|jar|slipper|crush|net(?![a-z]))", re.I),
    insult=re.compile(
        r"(нах[уе]|хуй|хуе|хуё|пизд|бля|ебан|ёбан|еби|сук[аи](?![а-яё])|гандон|пидор|пидр|дур[аы](?![а-яё])|тупа|тупой|идиот|дебил|урод|чмо|"
        r"лох(?![а-яё])|заткн|отвали|сдохн|мраз|твар|шлюх|говн|"
        + W + r"(fuck|shit|bitch|stupid|idiot|dumb|ugly|shut up|suck|hate))",
        re.I,
    ),
    kind=re.compile(r"(глад|милая|милашк|люблю тебя|обнима|цвет[оы]к|дарю|умничк|красив|спасибо|" + W + r"(pets?|cute|love you|hug|flower|thank))", re.I),
)

# Discord markup that means nothing to her: user/role/channel mentions, custom emoji (kept as :name:)
MENTION_RE = re.compile(r"<[@#][!&]?\d+>")
EMOJI_RE = re.compile(r"<a?:(\w+):\d+>")

# Buzz words as buzzify() writes them, to take them back out of her old replies (Discord history after a restart)
BUZZ_RE = re.compile(r"(?<!\w)(?:[бb][зzж]+[бзжbz-]*|[зz]{3,}|ж{3,})[~!.…]*(?!\w)", re.I)

# Loop guard: a character 13+ times in a row, or the same short chunk 5+ times in a row, gets cut down
CHAR_RUN_RE = re.compile(r"(\S)\1{12,}")
CHUNK_RUN_RE = re.compile(r"(\S{1,12}[\s,.!~-]*?)\1{4,}")


URL_RE = re.compile(r"https?://\S+")


def language(texts: list[str]) -> str:
    """The language of the newest user message that has words in it (not just a link or emoji); Russian if none has."""
    for text in texts:
        words = re.sub(r":\w+:", "", URL_RE.sub("", text))
        if CYRILLIC_RE.search(words):
            return "ru"
        if LATIN_RE.search(words):
            return "en"
    return "ru"


def tone(text: str) -> str | None:
    return next((name for name, regex in TONES.items() if regex.search(text)), None)


def clean_user_text(text: str) -> str:
    return EMOJI_RE.sub(r":\1:", MENTION_RE.sub("", text)).strip()


def debuzz(text: str) -> str:
    return re.sub(r"\s{2,}", " ", BUZZ_RE.sub("", text)).strip()


def guard(text: str) -> str:
    text = CHAR_RUN_RE.sub(lambda m: m.group(1) * 8, text)
    return CHUNK_RUN_RE.sub(lambda m: m.group(1) * 3, text).strip()


def prepare(messages: list[dict[str, Any]], style: str = "two-stage") -> tuple[list[dict[str, Any]], str, str | None]:
    """messages: llmcord's conversation, newest first, without a system prompt. style: "two-stage" or "one-stage".

    Returns her full prompt (newest first, like llmcord keeps it), the reply language and the tone of the newest message.
    """
    two_stage = style != "one-stage"
    user_texts = [clean_user_text(m["content"]) for m in messages if m["role"] == "user" and isinstance(m["content"], str)]
    lang, mood = language(user_texts), tone(user_texts[0]) if user_texts else None
    prompt = (PROMPTS if two_stage else BUZZING_PROMPTS)[lang]

    chat = []
    for msg in messages:
        content = msg["content"]
        if isinstance(content, str):
            # Two-stage history is plain (her old replies without the buzzing); one-stage keeps her own buzzing
            content = (debuzz(content) if two_stage else content.strip()) if msg["role"] == "assistant" else clean_user_text(content)
            if not content:
                continue
        chat.append(dict(role=msg["role"], content=content))

    if two_stage and mood and chat and chat[0]["role"] == "user":
        chat.insert(1, dict(role="system", content=prompt["hints"][mood]))

    examples = []
    for user, reply in prompt["examples"]:
        examples += [dict(role="user", content=user), dict(role="assistant", content=reply)]

    return chat + examples[::-1] + [dict(role="system", content=prompt["system"])], lang, mood


def buzz_word(lang: str, loud: bool) -> str:
    b, z, zh = ("б", "з", "ж") if lang == "ru" else ("b", "z", "z")
    word = random.choice([
        b + z * random.randint(2, 6),
        (b + z) * random.randint(2, 3),
        b + zh * random.randint(3, 6),
        z * random.randint(3, 5),
        b + z + b + b + z * random.randint(2, 3),
        b + z * random.randint(2, 3) + "-" + b + z * random.randint(2, 3),
    ])  # fmt: skip
    # No "~~": that's strikethrough in Discord
    word += random.choice(["", "", "", "~", "!", "..."])
    return word.upper() if loud else word


def buzzify(text: str, lang: str, mood: str | None = None, density: float = 0.8) -> str:
    """Buzz at the start and the end, after most clauses, and in the middle of long ones. Loud when she shouts or panics."""
    letters = [c for c in text if c.isalpha()]
    loud = mood == "threat" or (len(letters) > 3 and sum(c.isupper() for c in letters) > 0.5 * len(letters))

    clauses = re.split(r"(?<=[,.!?…:;—])\s+", text.strip())
    out = [buzz_word(lang, loud)]
    for i, clause in enumerate(clauses):
        words = clause.split()
        if len(words) >= 6 and "*" not in clause and random.random() < density:
            words.insert(random.randint(2, len(words) - 2), buzz_word(lang, loud))
        out.append(" ".join(words))
        if i < len(clauses) - 1 and random.random() < density:
            out.append(buzz_word(lang, loud))
    out.append(buzz_word(lang, loud))
    if random.random() < 0.3:
        out.append(buzz_word(lang, loud))

    return " ".join(out)
