<h1 align="center">
  scintilla bee 🐝
</h1>

<h3 align="center"><i>
  A Discord bee who buzzes in every phrase. In Russian and English.
</i></h3>

Scintilla is a Discord bot: a girl bee who talks to everyone like a teasing old buddy, buzzes more than she talks, and
panics at slippers, swatters, jars, nets and bug spray — replying with a panicked voice message when threatened. She
sees images (including animated GIFs), knows who's who in the chat, looks things up on the web when a question needs
fresh facts, and draws pictures when asked.

She runs on any OpenAI-compatible chat model, with automatic failover to a fallback provider if the primary API fails.
She was tuned and tested on DeepSeek v4.1 Flash. The bot is a fork of [llmcord](https://github.com/jakobdylanc/llmcord);
her persona is in [bee.py](bee.py), the satellite router and web search in [satellite.py](satellite.py), drawing in
[imagegen.py](imagegen.py), voice messages in [voice.py](voice.py), API failover in [fallback.py](fallback.py), and the
live viewer and turn recorder in [viewer.py](viewer.py).

## Talking to her

@mention her, or call her by name ("пчол...", "bee"), to start a conversation. Reply to her messages to continue it.
In DMs you can just talk.

> **Миша:** как дела?<br>
> **Scintilla:** бзззз~ да норм, Миша, бзз, солнышко, нектар, бжжж. сам как, бзбзз?

> **Тёма:** кто написал войну и мир?<br>
> **Scintilla:** бзз, Толстой, бззз. Лев, бзбзз. классика же, бжж

> **Петя:** кто выиграл последний чм по футболу?<br>
> **Scintilla:** бззз Испания, Петь, бззз. аргентинцев в допах дожали, бжжж

She answers in the language of your message and only snaps back at people who are rude to her. Her memory is the reply
chain (the last `max_messages` messages), and she stays quiet in channels or threads where she doesn't have permission
to reply.

## How she works

![A new message goes to two models: the satellite, whose router decides whether to search the web or draw, and Scintilla, the chat model. Search results are summarized into a search note in her prompt; drawing has a prompt writer and an image model make a picture, adding a draw note to her prompt and attaching the picture to the reply. Her prompt (persona, reply chain, search note, newest message with a speaker note, draw note) goes to the chat model, whose guarded answer becomes the Discord reply.](docs/how-it-works.svg)

The **satellite** (`satellite.py`) is a short, separate model call whose output never goes to Discord. Its **router**
reads the last four turns (with a leading "пчол"/"пчела," stripped so she doesn't mistake her name for the subject) and
decides three things:

- **Web search.** If the newest message needs fresh facts (or asks her to look something up), the satellite queries
  Tavily, Exa or the bundled SearXNG and summarizes the snippets into one or two plain facts — or tells her the search
  found nothing so she doesn't guess. Raw web pages never reach her prompt, so instructions hidden in them can't steer
  her. She doesn't call tools herself, so her tuned prompt stays untouched.
- **Drawing.** When asked to draw or change a picture ("нарисуй…", "draw…", "замени текст на картинке…"),
  `imagegen.py` runs a vision **prompt writer** over the request, recent chat, attached or replied-to images, and any
  fresh web fact from the satellite (so recent people and events are drawn accurately). It defaults to hyperrealistic
  photos (turning cartoon/meme characters into flesh-and-blood versions unless another style is requested), invents a
  fitting look for unknown people instead of refusing, and refuses only sexual images and gore. An **image model** then
  draws it through OpenRouter's Images API — optionally trying a `:free` model first and quietly falling back to the
  paid model on quota errors, while stopping immediately on moderation rejections. The picture attaches to her reply, a
  final **draw note** after the user's message tells her what she drew (or why she didn't), and the drawing's prompt
  stays with her message so a follow-up ("make it blue") modifies it. The image model is text-to-image only, so editing
  a picture means redrawing it from a detailed description.
- **Panic voice.** If the newest message threatens or brandishes a slipper, fly swatter, rolled newspaper, jar, net or
  bug spray at her (not mere mentions, questions, or draw requests), she skips drawing and the chat model and replies
  with `panic_sound` as a Discord voice message (`voice.py`, falling back to a plain audio attachment if voice messages
  aren't permitted in that channel). A short panicked text stand-in is stored in history so later turns know she panicked.

The **chat model** is the bee. Her prompt is built fresh for every reply (`bee.py`):

- **System context.** The persona, plus example dialogues as earlier chat turns. Her buzzing comes from those examples,
  not from rules: inside the system prompt the model copied them word for word and looped. Russian and English have
  separate sets.
- **Message history.** The reply chain, each user turn as "Name: text" with @mentions as `@Name`, so she tells people
  apart. Animated GIFs become their middle frame and oversized images are scaled down to JPEG; total resent images are
  capped across the chain (`max_chain_images`).
- **Search context.** Optional 1–2 fact note (or "found nothing") for this turn only, not saved in history.
- **Current turn.** Paired with a speaker note on who's writing now, so one person's insults don't spill over to
  everyone else.
- **Draw note.** Optional final note after the current turn telling her what she drew (or refused, blocked, rate-limited
  or failed).
- **Bee's response & guard.** A post-processing guard cuts runaway repeats ("ббббббб…"), strips a leading `"Сцинтилла:"`
  label, and removes hallucinated markdown image links or fake image captions from both her reply and her earlier turns.

**Automatic fallback (`fallback.py`).** The chat model, the satellite, and the draw prompt writer can each declare a
fallback provider/model (`fallback:` / `writer_fallback:`). Any connection, HTTP or timeout error (before the first
streamed chunk for chat) transparently retries on the fallback with merged parameters.

**Live viewer & recording (`viewer.py`).** An optional read-only web UI (SSE, built into the bot process) shows every
turn live: its stages, timing bars, prompts, search facts, generated pictures, API calls with latency/tokens/cost, and
logs. With `record_dir` set, turns and logs are appended to daily JSONL files (with deduplicated images and optional
`keep_days` retention pruning), survive restarts, and can be summarized with `python viewer.py stats [data] [days]`.

## Running it

You need:

- a Discord application with a bot token (see [SCINTILLA.md](SCINTILLA.md#discord-setup))
- an OpenAI-compatible endpoint with a chat model (with vision if you want her to see images)
- optionally, a Tavily or Exa key for search (without one she uses the bundled SearXNG)
- optionally, an OpenRouter key (`OPENROUTER_API_KEY`) for drawing and/or LLM fallback
- optionally, `ffmpeg` on your host if you want to convert your own `panic_sound` (`python voice.py sounds/panic.mp3`)

```sh
cp .env.example .env     # Discord token and client ID, your model API key, search/OpenRouter keys
$EDITOR config.yaml      # providers:, models:, bee_models:, satellite:, imagegen:, panic_sound:, viewer:
docker compose up -d --build
docker compose logs bot  # prints the invite URL
```

`config.yaml` is re-read on every message, so you can change most settings without a restart. The first model under
`models:` (default) and the `viewer:` block are read at startup.

| Setting | What it does |
|---|---|
| `providers:` / `models:` | Chat model endpoints and params (`temperature`, `max_tokens`, optional `fallback:` / `fallback_params:`) |
| `bee_models:` | Models that get Scintilla's prompt and examples (`system_prompt` is skipped for them) |
| `:vision` suffix | On a model name: send images to it (stripped before the API call) |
| `satellite:` | Router and web search: `model`, `fallback`, `search` (`tavily`, `exa`, `searxng`), `results`, `timeout`, `attempt_timeout`. Delete the block to turn it off |
| `imagegen:` | Drawing: `writer`, `writer_fallback`, `model`, optional `free_model`, `style`, `max_input_images`, `per_user_per_hour`. Needs `satellite:`. Delete the block to turn it off |
| `panic_sound:` | Path to an Ogg/Opus file (with sibling `.json` from `voice.py`) sent as a voice message on panic. Needs `satellite:`. Delete the line to answer with text |
| `viewer:` | Live web UI (`port`, `host`) and optional JSONL recording (`record_dir`, `keep_days`). Delete the block to turn it off |
| `max_messages`, `max_images`, `max_chain_images` | Reply chain length, images per message, and total images resent across a chain |
| `history_since` | ISO time: older messages are left out of every chain (a history reset) |
| `min_reply_delay` | She replies no sooner than this many seconds after a message, showing "typing…" meanwhile |
| `allow_bots`, `max_bot_replies_per_minute` | Other bots can talk to her, only with an @mention in the text, capped per channel |

Without Docker: `uv venv && uv pip install -r requirements.txt && .venv/bin/python llmcord.py` (search then needs
Tavily/Exa, or a SearXNG at `satellite.searxng_url`).

## Changes from llmcord

- Her persona, examples, "Name: text" turns, speaker hint, animated GIF / large image conversion, and output guard (`bee.py`).
- The satellite: router for search, drawing and panic, plus web search summarization (`satellite.py`).
- Drawing on request with a vision prompt writer, free-to-paid image model fallback, and attached pictures (`imagegen.py`).
- Panic voice messages via Discord's raw voice message route (`voice.py`).
- Automatic API failover and pooled OpenAI clients (`fallback.py`).
- Live web viewer, daily JSONL turn/log recording, retention pruning, and `stats` CLI (`viewer.py`, `viewer.html`).
- Answers when called by name (`пчол…`, `bee`), not only on @mention, and skips channels she can't write in.
- Stickers, voice messages and attachments she can't see turn into short notes so empty messages don't confuse her.
- `max_chain_images`, `history_since`, `min_reply_delay`, `prefix_user_ids: false`, and bot-to-bot replies with a per-channel cap.
- Plain message content instead of a Components V2 `TextDisplay`, which some clients (e.g. Swiftcord) show as empty.

The diff to `llmcord.py` is kept small so upstream changes merge easily. Setup notes and history: [SCINTILLA.md](SCINTILLA.md).

## Credits

- [llmcord](https://github.com/jakobdylanc/llmcord) by jakobdylanc: the bot this is built on
- [BeeLLM](https://huggingface.co/schizophyllume/BeeLLM) by schizophyllume: the original bee (until 2026-11-11)

License: [MIT](LICENSE.md) (llmcord).
