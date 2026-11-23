# scintilla bee 🐝

[llmcord](https://github.com/jakobdylanc/llmcord) (remote `upstream`) running Scintilla on DeepSeek v4.1 Flash
(Mistral's API through CLIProxyAPI). Until 2026-11-11 she ran on
[BeeLLM-2.0-12M-Mood](https://huggingface.co/schizophyllume/BeeLLM) via llama.cpp, with a Gemma 3 4B translator for
Russian; both were removed on 2026-11-13 (see git history).

## Discord setup

1. [Developer Portal](https://discord.com/developers/applications) → **New Application** → name it `scintilla bee`.
2. **Bot** tab: upload the avatar, **Reset Token** (copy it), enable **Message Content Intent**.
3. **General Information**: copy the **Application ID**.
4. `cp .env.example .env` and fill in both values, plus `CLIPROXY_BASE_URL` and `CLIPROXY_API_KEY`.
5. Start the bot, then open the invite URL it logs on startup (`docker compose logs bot`).

## Run

```sh
docker compose up -d --build
```

Without Docker:

```sh
uv venv && uv pip install -r requirements.txt && .venv/bin/python llmcord.py
```

Talk to her by @mentioning her or calling her by name; reply to her messages to continue a conversation.

## Viewer

A live page of what she's doing (each message with her reply and the steps in between: the satellite, drawing, her prompt; and a log tab): `viewer.py`,
`viewer:` in `config.yaml`. Every reply (steps, timings, API calls with tokens, log lines) and every log line is also
saved to `data/` (`record_dir`), so the page survives restarts and you can analyze it: `python viewer.py stats data 7`.
Docker publishes it on `127.0.0.1:8770` only. It has no auth and shows the chats, so keep it private.

## The model

DeepSeek v4.1 Flash buzzes on its own and answers for real: "бззбз сам заткнись, бззз. жужжу как хочу, бжжж",
"бзз, Париж, бззз. там мёд вкусный". It's in `bee_models` in `config.yaml`, code in `bee.py`. If the proxy and Mistral are
down, she falls back to OpenRouter (`fallback.py`).

## Satellite (web search)

Before she answers, a second call to the same DeepSeek model (`satellite.py`, `satellite:` in `config.yaml`) decides
whether the newest message needs fresh facts. If so, it searches Tavily or Exa (`TAVILY_API_KEY`/`EXA_API_KEY` in `.env`)
or the self-hosted SearXNG (the `searxng` service in `docker-compose.yaml`) and turns the results into a one- or two-sentence note for her
reply. "кто выиграл последний чм по футболу?" → "бззз Испания, Петь, бззз. аргентинцев в допах дожали, бжжж". The whole
thing has a 5 s deadline; on a miss or error she answers without the note. Delete the `satellite:` block to turn it off.

## Drawing

The satellite's router also spots requests to draw or to change a picture. `imagegen.py` (`imagegen:` in `config.yaml`)
then has DeepSeek (vision, low reasoning) write an image prompt, Recraft v4.1 Flash on OpenRouter (`OPENROUTER_API_KEY`
in `.env`, the free variant) draws it, and the picture is attached to her reply. About 3 s for a new drawing,
about 5 s for a change. An attached picture is redrawn from its description: Recraft can't edit images. Limited to
`per_user_per_hour` drawings per person; past that, or on an error or refusal, she says so instead.

## Changes from upstream

- `bee_models` (`bee.py`): her prompt and examples, "Name: text" user turns, the speaker hint and the loop guard.
- `satellite` (`satellite.py`): a web-search note for the reply when the newest message needs fresh facts.
- `imagegen` (`imagegen.py`): draws on request; the picture goes with her reply.
- `min_reply_delay` (3 s since 2026-11-11): she waits at least that long after a message, showing "typing...", so
  replies don't look instant. Slower replies aren't held up further.
- `history_since`: messages older than this are left out of every reply chain (a history reset).
- She answers when called by name ("пчол...", "bee"), not only on @mention.
- `prefix_user_ids: false` stops prefixing user messages with `<@ID>: ` (`bee.py` adds names itself).
- `allow_bots: true` lets other bots talk to her, but only when they @mention her in the message
  text (a reply ping doesn't count), capped at `max_bot_replies_per_minute` per channel so two
  bots can't loop forever.
- Plain responses are sent as normal message content instead of a Components V2 `TextDisplay`,
  which some clients (e.g. Swiftcord) render as an empty message.
