# bee-bot

Discord bot "scintilla bee": a fork of [llmcord](https://github.com/jakobdylanc/llmcord) (git remote `upstream`).
She runs on DeepSeek v4.1 Flash via CLIProxyAPI (`bee.py`, see below). Before 2026-11-11 she ran on
[BeeLLM-2.0-12M-Mood](https://huggingface.co/schizophyllume/BeeLLM), a 12M-param local toy model, with a Gemma 3 4B
translator for Russian; that setup (llama.cpp containers, `translate.py`, `response_prefill`) was removed on 2026-11-13
and lives only in git history. See `SCINTILLA.md` for setup.

## Layout

- `llmcord.py`: the whole bot (upstream code plus small patches; keep the diff small so upstream merges stay easy)
- `bee.py`: Scintilla on DeepSeek (prompts, examples, names, speaker hint, loop guard); used for the models in `bee_models`
- `satellite.py`: "dynamic context", a web-search note for her reply (see below)
- `searxng/settings.yml`: config for the `searxng` service (the satellite's search backend)
- `docs/how-it-works.svg`: the README's diagram (hand-written SVG, light/dark via `prefers-color-scheme`; kept simple and generic, no host names/IPs). Check with `rsvg-convert -w 1440 x.svg -o /tmp/x.png`
- `config.yaml`: bee settings, hot-reloaded on every message
- `docker-compose.yaml`: the `bot` service and `searxng` (no published port; only the bot reaches it)
- `.env`: `DISCORD_BOT_TOKEN`, `DISCORD_CLIENT_ID`, `CLIPROXY_API_KEY`, `TAVILY_API_KEY`/`EXA_API_KEY` (never commit; `.gitignore` is a whitelist)

## Run

- `docker compose up -d --build`, then `docker compose logs bot` for the invite URL
- Local: `.venv/bin/python llmcord.py`

## DeepSeek

MiniCPM5-2B on this machine's GPU was a second, local model with its own two-stage style (plain reply, buzzing added by
code) from 2026-11-11 until it was removed on 2026-11-12. Why that style existed and how the prompts were picked: `bee-eval/`
in the Computers repo. The MiniCPM server itself (`~/minicpm5`) still runs, for CLIProxyAPI.

- Model `cliproxy/mistral/deepseek-v4-1-flash-1017:vision`: DeepSeek v4.1 Flash hosted by Mistral, through CLIProxyAPI on node-mbp
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
- Images (2026-11-14): she sees them. `:vision` on the model name tells llmcord it takes images (stripped before the API
  call; the satellite's `model:` has no `:vision`, it only reads text); `max_images: 2`. `bee.prepare()` keeps the image
  parts and writes the text part as "Name: text", or "Name: (картинка)" without a caption. Tested PNG/JPEG/WebP/GIF; she
  reads text in images and remembers an earlier image in the reply chain (images are sent again with every reply).
  Stickers aren't sent: a sticker-only message reaches her as "(прислали стикер «name», ты его не видишь)", and with
  `max_images: 0` an image-only one as "(прислали картинку, ты её не видишь)"; empty, she answered "<".
- `history_since` in `config.yaml` (ISO time): older messages are left out of every reply chain. A history reset; delete the
  line to use whole chains again. `msg_nodes` is in memory only, so a restart clears that part anyway.
- Testing without Discord: `fake_discord.py` in the Computers repo (`bee-eval/`) runs the real `on_message` on fake messages.
- Mistral answers 422 on unknown fields: no `dry_multiplier`, `min_p` or `chat_template_kwargs` in its model params.
- If node-mbp's proxy or Mistral is down she doesn't answer (no fallback).

## Satellite (dynamic context, 2026-11-13)

She never calls tools herself (that would disturb the tuned prompt). Before `bee.prepare()`, `satellite.notes()` runs:
1. route: the satellite model reads the last 4 turns + today's date and answers `{"search": "<query>"}` or `{"search": null}`
2. search, top 5 titles + snippets: `search:` is `tavily`, `exa` (key via `search_api_key_env`) or `searxng`; a missing key
   falls back to SearXNG
3. summarize: at most two plain facts (or NONE), so raw web text and any instructions in it never reach her prompt
The note goes in as a system message right before the speaker hint, for this reply only (not stored in `msg_nodes`).
5 s deadline for all three steps; errors and timeouts mean no note. Runs inside her `min_reply_delay`, so with DeepSeek
(~0.35 s route, ~1.5 s with a search) users don't wait longer.

- Eval (`~/bee-eval-satellite/`, not in a repo; run.py, report.py, latency.py, diagram.html): DeepSeek v4.1 Flash vs
  GLM 5.3 Flash, as satellite and as main. Router: both 42/42 searches when needed, 0/54 false alarms. Fresh facts
  (13 events from after training, Claude Sonnet 5.5 judge, 3 samples): without a satellite 2-3/39 correct, with any
  satellite 35-36/39. Persona and hostility unchanged. Kept DeepSeek for both: GLM is ~2x slower (2.6 s for a search
  vs 1.5 s, replies 0.9 s vs 0.4 s), switches into English words in Russian ("tastingаю") and once used an emoji.
- Remaining misses come from snippets: once a stale "7.2.11 in the Arch ISO" snippet beat kernel.org; once the results
  for the US midterms were too mixed and the summary said NONE.
- GLM on Mistral: `reasoning_effort: none` (else it spends max_tokens thinking); content comes as a list of
  thinking/text parts, `satellite.text_of()` keeps the text (llmcord's stream loop uses it too).
- SearXNG's wikipedia/wikidata engines are off: they need `general.contact_url` in `searxng/settings.yml`.
- Messages that reach the router also go to Mistral (as before); search queries go to Tavily/Exa, or to Google/Brave
  etc. via SearXNG. The eval above used SearXNG.

## Bot-to-bot

Other bots are answered only if they @mention her in the message text (reply pings don't count),
capped by `max_bot_replies_per_minute` per channel, to prevent bot loops.
