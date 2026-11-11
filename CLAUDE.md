# bee-bot

Discord bot "scintilla bee": a fork of [llmcord](https://github.com/jakobdylanc/llmcord) (git remote `upstream`).
Since 2026-11-11 she runs on DeepSeek v4.1 Flash via CLIProxyAPI (`bee.py`, see below); before that she chatted using [BeeLLM-2.0-12M-Mood](https://huggingface.co/schizophyllume/BeeLLM), a 12M-param toy model
that only buzzes and starts every reply with a `[mood:level]` tag. See `SCINTILLA.md` for setup.

## Layout

- `llmcord.py`: the whole bot (upstream code plus small patches; keep the diff small so upstream merges stay easy)
- `bee.py`: Scintilla on DeepSeek (prompts, examples, names, speaker hint, loop guard); used for the models in `bee_models`
- `translate.py`: Russian <-> English translation via a second llama-server (Gemma 3 4B, CUDA, :8082); kept out of `llmcord.py`
- `config.yaml`: bee settings, hot-reloaded on every message
- `docker-compose.yaml`: `llama` (llama.cpp server on :8081) + `translator` (llama.cpp CUDA on :8082, needs an NVIDIA GPU)
  + `bot` (shares llama's network, reaches both on `localhost`)
- `.env`: `DISCORD_BOT_TOKEN`, `DISCORD_CLIENT_ID` (never commit; `.gitignore` is a whitelist)

## Run

- `docker compose up -d --build`, then `docker compose logs bot` for the invite URL
- Local: `llama-server -hf schizophyllume/BeeLLM -hff BeeLLM-2.0-12M-Mood.gguf -c 4096 --jinja --port 8081`
  and `.venv/bin/python llmcord.py`

## DeepSeek mode (the default since 2026-11-11)

MiniCPM5-2B on this machine's GPU was a second, local model with its own two-stage style (plain reply, buzzing added by
code) from 2026-11-11 until it was removed on 2026-11-12. Why that style existed and how the prompts were picked: `bee-eval/`
in the Computers repo. The MiniCPM server itself (`~/minicpm5`) still runs, for CLIProxyAPI.

- Model `cliproxy/mistral/deepseek-v4-1-flash-1017`: DeepSeek v4.1 Flash hosted by Mistral, through CLIProxyAPI on node-mbp
  (`http://10.10.0.50:8317/v1`, key `CLIPROXY_API_KEY` in `.env`). ~0.5 s per reply; messages leave the LAN (to Mistral).
- The model buzzes itself from buzzing examples (separate RU/EN prompts, as chat turns, not in the system prompt). Tested:
  facts 24/24, memory 9/9, understands slang and insults.
- `system_prompt` and `response_prefill` are skipped for `bee_models` (they're BeeLLM settings).
- Persona (2026-11-11): a teasing old buddy (from the owner's "охреневший кореш" prompt, toned down: hard and swearing only
  when someone is rude to her) who still buzzes in every phrase and panics at slippers/jars/nets.
- Names: llmcord passes the author's display name with each user message (`MsgNode.author_name`) and turns @mentions into
  `@Name` for `bee_models`; `bee.prepare()` writes user turns as "Name: text". How often she uses names comes from the
  examples (named examples, the name in about a third of her replies), not from the prompt.
- Speaker hint: a system note before the newest message, "now {name} is writing; don't take others' rudeness out
  on them; if they're not rude, be friendly". Without it one person's insults spilled over to everyone in the reply chain
  (seen live 2026-11-11). It's conditional on purpose: a keyword-based "they're not rude" made her sweet to insults the
  keywords miss. Judged by Claude Sonnet in bee-eval round 9: snark at neutral messages 15/72 -> 0/72.
- Image/sticker-only messages reach her as "(прислали картинку, ты её не видишь)"; empty, she answered "<".
- `history_since` in `config.yaml` (ISO time): older messages are left out of every reply chain. A history reset; delete the
  line to use whole chains again. `msg_nodes` is in memory only, so a restart clears that part anyway.
- Testing without Discord: `fake_discord.py` in the Computers repo (`bee-eval/`) runs the real `on_message` on fake messages.
- Mistral answers 422 on unknown fields: no `dry_multiplier`, `min_p` or `chat_template_kwargs` in its model params.
- If node-mbp's proxy or Mistral is down she doesn't answer (no fallback). The only other model is BeeLLM
  (`bee/scintilla`): put it first in `models:` and restart (`/model` only works for `admin_ids`, which is empty, and the
  first model is read at startup).

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
