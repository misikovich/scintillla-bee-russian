# bee-bot

Discord bot "scintilla bee": a fork of [llmcord](https://github.com/jakobdylanc/llmcord) (git remote `upstream`).
Since 2026-11-11 she runs on MiniCPM5-2B (`bee.py`, see below); before that, and still selectable with `/model`, she chatted using [BeeLLM-2.0-12M-Mood](https://huggingface.co/schizophyllume/BeeLLM), a 12M-param toy model
that only buzzes and starts every reply with a `[mood:level]` tag. See `SCINTILLA.md` for setup.

## Layout

- `llmcord.py`: the whole bot (upstream code plus small patches; keep the diff small so upstream merges stay easy)
- `bee.py`: Scintilla on a real model (prompts, examples, tone hints, `buzzify()`); used for models in `bee_models`, each
  with a style: `two-stage` (MiniCPM) or `one-stage` (DeepSeek)
- `translate.py`: Russian <-> English translation via a second llama-server (Gemma 3 4B, CUDA, :8082); kept out of `llmcord.py`
- `config.yaml`: bee settings, hot-reloaded on every message
- `docker-compose.yaml`: `llama` (llama.cpp server on :8081) + `translator` (llama.cpp CUDA on :8082, needs an NVIDIA GPU)
  + `bot` (shares llama's network, reaches both on `localhost`)
- `.env`: `DISCORD_BOT_TOKEN`, `DISCORD_CLIENT_ID` (never commit; `.gitignore` is a whitelist)

## Run

- `docker compose up -d --build`, then `docker compose logs bot` for the invite URL
- Local: `llama-server -hf schizophyllume/BeeLLM -hff BeeLLM-2.0-12M-Mood.gguf -c 4096 --jinja --port 8081`
  and `.venv/bin/python llmcord.py`

## MiniCPM mode (default since 2026-11-11)

- Model `minicpm/minicpm5-2b`: llama.cpp CUDA on this machine (`~/minicpm5`, `http://10.10.0.41:8083/v1`, key `MINICPM_API_KEY` in `.env`).
  The bot reaches it on the host LAN IP. Thinking is off (`chat_template_kwargs`), temp 0.7, `dry_multiplier: 0.8`, 100 tokens.
- Two stages: MiniCPM writes a short plain in-character reply, then `bee.buzzify()` adds the buzzing in code. Asking the model
  to buzz itself made it much worse at understanding (facts 17/24 vs 23/24, memory 3/9 vs 8/9).
- Discord gets the buzzed text; `msg_nodes` keeps the plain reply, and `bee.debuzz()` strips buzzing from history rebuilt from Discord.
- Separate Russian and English prompts and few-shot examples (as chat turns, not in the system prompt), picked by the newest user
  message with words in it. Mixed examples make her put English into Russian replies.
- Keyword tone hints (insult / threat / kind) are added as a system message before the newest user message: MiniCPM misses Russian
  insults on its own. Appended to the user's text instead, she reads the hint out loud.
- `system_prompt` and `response_prefill` are skipped for `bee_models` (they're BeeLLM settings).
- No "~~" in buzz words (Discord strikethrough). Don't add names to the examples (they leak into replies).
- The eval scripts and results are in the Computers repo, `bee-eval/`.

## DeepSeek mode (added 2026-11-11, not the default yet)

- Model `cliproxy/mistral/deepseek-v4-1-flash-1017`: DeepSeek v4.1 Flash hosted by Mistral, through CLIProxyAPI on node-mbp
  (`http://10.10.0.50:8317/v1`, key `CLIPROXY_API_KEY` in `.env`). ~0.5 s per reply; messages leave the LAN (to Mistral).
- `one-stage`: the model buzzes itself from buzzing examples (separate RU/EN prompts). No tone hints, no `buzzify()`, history
  keeps her buzzing. Tested: facts 24/24, memory 9/9, understands slang and insults that MiniCPM misses.
- Mistral answers 422 on unknown fields: no `dry_multiplier`, `min_p` or `chat_template_kwargs` in its model params.
- `/model` only works for `admin_ids` (empty), so to use it: put it first in `models:` and restart, or set `admin_ids`.

## Model quirks (BeeLLM)

- Chat template is `<|user|>…<|bee|>…</s>` only: system messages are dropped, so `system_prompt` stays empty.
- Any extra text in user turns derails her (the `<@ID>: ` prefix made every reply angry), hence `prefix_user_ids: false`.
- Some prompts (e.g. "put u in jar") make her emit EOS as the very first token, giving an empty reply that llmcord
  silently drops. `response_prefill: "["` fixes it; llama.cpp doesn't stream the prefill back, so llmcord re-adds it.
- 4096-token context: keep `max_messages` small.
- Don't put "llama" in the model name: llmcord treats it as a vision model and sends images.

## Translation

- Gemma adds words/emoji ("bzz", 😊) to input unless the prompt forbids it, and that derails her like the user ID prefix did.
- The `ru` prompt makes Gemma copy bee sounds letter for letter (otherwise it drops or invents them);
  `transliterate()` then spells the leftover Latin in Cyrillic. Don't ask the LLM to transliterate.
- Russian replies are sent to Discord, but `msg_nodes` keeps the English text. After a restart, history is rebuilt from
  Discord and gets translated back to English.
- ~1 s per message on an M-series Mac (input + output translation).

## Resource consumption

Measured on 2026-10-15 in Docker Desktop (2 vCPU, 4 GB VM, Apple Silicon):

| | RAM | CPU | Disk |
|---|---|---|---|
| `llama` | ~58 MiB idle, ~64 MiB under load | 0% idle; ~1.2 cores at 4 parallel requests | 1.19 GB image + 46 MB model volume |
| `bot` | ~80 MiB (Python + discord.py, before connecting) | negligible, waits on Discord/model I/O | 303 MB image |

- One reply takes ~12 ms (~1000 tok/s on CPU); 100 replies from 4 parallel clients took 2.3 s.
- No GPU needed. The pair fits in ~150 MiB RAM, so a Raspberry Pi or the smallest VPS is plenty.

## Bot-to-bot

Other bots are answered only if they @mention her in the message text (reply pings don't count),
capped by `max_bot_replies_per_minute` per channel, to prevent bot loops.
