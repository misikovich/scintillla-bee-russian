# bee-bot

Discord bot "scintilla bee": a fork of [llmcord](https://github.com/jakobdylanc/llmcord) (git remote `upstream`)
that chats using [BeeLLM-2.0-12M-Mood](https://huggingface.co/schizophyllume/BeeLLM), a 12M-param toy model
that only buzzes and starts every reply with a `[mood:level]` tag. See `SCINTILLA.md` for setup.

## Layout

- `llmcord.py`: the whole bot (upstream code plus small patches; keep the diff small so upstream merges stay easy)
- `translate.py`: Russian <-> English translation via a second llama-server (Gemma 3 4B, CUDA, :8082); kept out of `llmcord.py`
- `config.yaml`: bee settings, hot-reloaded on every message
- `docker-compose.yaml`: `llama` (llama.cpp server on :8081) + `translator` (llama.cpp CUDA on :8082, needs an NVIDIA GPU)
  + `bot` (shares llama's network, reaches both on `localhost`)
- `.env`: `DISCORD_BOT_TOKEN`, `DISCORD_CLIENT_ID` (never commit; `.gitignore` is a whitelist)

## Run

- `docker compose up -d --build`, then `docker compose logs bot` for the invite URL
- Local: `llama-server -hf schizophyllume/BeeLLM -hff BeeLLM-2.0-12M-Mood.gguf -c 4096 --jinja --port 8081`
  and `.venv/bin/python llmcord.py`

## Model quirks

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
