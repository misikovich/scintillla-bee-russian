# bee-bot

Discord bot "scintilla bee": a fork of [llmcord](https://github.com/jakobdylanc/llmcord) (git remote `upstream`).
She runs on DeepSeek v4.1 Flash via CLIProxyAPI (`bee.py`, see below). Before 2026-11-11 she ran on
[BeeLLM-2.0-12M-Mood](https://huggingface.co/schizophyllume/BeeLLM), a 12M-param local toy model, with a Gemma 3 4B
translator for Russian; that setup (llama.cpp containers, `translate.py`, `response_prefill`) was removed on 2026-11-13
and lives only in git history. See `SCINTILLA.md` for setup.

## Layout

- `llmcord.py`: the whole bot (upstream code plus small patches; keep the diff small so upstream merges stay easy)
- `bee.py`: Scintilla on DeepSeek (prompts, examples, names, speaker hint, loop guard); used for the models in `bee_models`
- `config.yaml`: bee settings, hot-reloaded on every message
- `docker-compose.yaml`: just the `bot` service
- `.env`: `DISCORD_BOT_TOKEN`, `DISCORD_CLIENT_ID`, `CLIPROXY_API_KEY` (never commit; `.gitignore` is a whitelist)

## Run

- `docker compose up -d --build`, then `docker compose logs bot` for the invite URL
- Local: `.venv/bin/python llmcord.py`

## DeepSeek

MiniCPM5-2B on this machine's GPU was a second, local model with its own two-stage style (plain reply, buzzing added by
code) from 2026-11-11 until it was removed on 2026-11-12. Why that style existed and how the prompts were picked: `bee-eval/`
in the Computers repo. The MiniCPM server itself (`~/minicpm5`) still runs, for CLIProxyAPI.

- Model `cliproxy/mistral/deepseek-v4-1-flash-1017`: DeepSeek v4.1 Flash hosted by Mistral, through CLIProxyAPI on node-mbp
  (`http://10.10.0.50:8317/v1`, key `CLIPROXY_API_KEY` in `.env`). ~0.5 s per reply; messages leave the LAN (to Mistral).
- The model buzzes itself from buzzing examples (separate RU/EN prompts, as chat turns, not in the system prompt). Tested:
  facts 24/24, memory 9/9, understands slang and insults.
- `system_prompt` is skipped for `bee_models` (the prompt is in `bee.py`).
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
- If node-mbp's proxy or Mistral is down she doesn't answer (no fallback).

## Bot-to-bot

Other bots are answered only if they @mention her in the message text (reply pings don't count),
capped by `max_bot_replies_per_minute` per channel, to prevent bot loops.
