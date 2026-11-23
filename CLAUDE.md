# bee-bot

Discord bot "scintilla bee": a fork of [llmcord](https://github.com/jakobdylanc/llmcord) (git remote `upstream`).
She runs on DeepSeek v4.1 Flash via CLIProxyAPI (`bee.py`). The earlier local-model setup (BeeLLM, a Gemma translator,
MiniCPM) was removed in 2026-11 and lives only in git history. See `SCINTILLA.md` for setup.

## Layout

- `llmcord.py`: the whole bot (upstream code plus small patches; keep the diff small so upstream merges stay easy)
- `bee.py`: Scintilla's prompts, examples, names, speaker hint, reply guard; used for the models in `bee_models`
- `fallback.py`: falls back to OpenRouter when the first model fails
- `satellite.py`: "dynamic context", a router plus a web-search note for her reply
- `imagegen.py`: drawing on request
- `voice.py` + `sounds/panic.*`: the voice message she sends when threatened with a slipper/jar/net
- `viewer.py` + `viewer.html`: live viewer page and the recording of every reply
- `data/`: that recording (`record_dir`; not in git: chats, pictures)
- `searxng/settings.yml`: config for the `searxng` service (a search backend)
- `docs/how-it-works.svg`: the README's diagram (hand-written SVG, light/dark via `prefers-color-scheme`; keep it generic,
  no host names/IPs). Check with `rsvg-convert -w 1440 x.svg -o /tmp/x.png`
- `config.yaml`: settings, hot-reloaded on every message (`*_env` keys read env vars)
- `.env`: tokens, keys and `CLIPROXY_BASE_URL` (never commit; `.gitignore` is a whitelist). Keep host names, IPs,
  tailnet URLs and chat members' names/nicks out of the repo.

## Run

- `docker compose up -d --build`, then `docker compose logs bot` for the invite URL. A change to `.env` needs
  `docker compose up -d bot` (config.yaml doesn't).
- Local: `.venv/bin/python llmcord.py`

## Model (DeepSeek)

- `cliproxy/mistral/deepseek-v4-1-flash-1017:vision`: DeepSeek v4.1 Flash hosted by Mistral, through CLIProxyAPI.
  ~0.5 s per reply. Messages leave the LAN (to Mistral).
- Mistral answers 422 on unknown fields: no `dry_multiplier`, `min_p`, `chat_template_kwargs`; `prompt_mode` 400s,
  `reasoning: {}` 422s. `reasoning_effort` works (thinking comes as list parts; `satellite.text_of()` drops them).
- She buzzes on her own, from buzzing examples given as chat turns (separate RU/EN prompts), not from rules in the
  system prompt. `system_prompt` is skipped for `bee_models`.
- Persona: a teasing old buddy (hard and swearing only when someone is rude to her), buzzing in every phrase, panics at
  slippers/jars/nets.
- Names: llmcord passes the author's display name (`MsgNode.author_name`) and turns @mentions into `@Name`;
  `bee.prepare()` writes user turns as "Name: text". How often she uses names comes from the examples, not the prompt.
- Speaker hint: a system note before the newest message ("now {name} is writing; don't take others' rudeness out on
  them; if they're not rude, be friendly"). Without it one person's insults spilled over to everyone in the chain.
  It's conditional on purpose: keyword-based "not rude" made her sweet to insults the keywords miss.
- Images: `:vision` tells llmcord the model takes images (stripped before the API call); `max_images: 2`,
  `max_chain_images` caps the total resent across a chain. `bee.still_image()` sends an animated GIF's middle frame as
  JPEG (Mistral 400s on animated GIFs, slowly) and scales down images over 1568 px or 4 MB. Stickers aren't sent; a
  sticker-only or (with images off) image-only message reaches her as a short placeholder note, since empty she answered "<".
- `bee.strip_fake_images()` removes made-up image links and "[Изображение: ...]"-style placeholders from her replies
  and her earlier turns (they spread across follow-ups).
- `history_since` (ISO time): older messages are left out of every reply chain. `msg_nodes` is in memory only.

## Fallback (fallback.py)

OpenRouter (`OPENROUTER_API_KEY`, the same DeepSeek) is the fallback for her model (`fallback:`/`fallback_params:` in
its `models:` entry), the satellite (`fallback:` in `satellite:`) and the draw writer (`writer_fallback:`). Any error
falls back; the first model has no retries and a 5 s connect timeout (`attempt_timeout: 2.5` for the satellite). Her
stream falls back only before its first chunk. OpenRouter needs `reasoning: {enabled: false}` (it reasons by default and
eats max_tokens). Logged as "... falling back to".

## Satellite (satellite.py)

She never calls tools herself (that would disturb the tuned prompt). Before `bee.prepare()`, `satellite.notes()` runs:
1. route: reads the last 4 turns + today's date, answers `{"search": ..., "draw": ..., "panic": ...}`
2. search, top 5 titles + snippets: `tavily`, `exa` (key via `search_api_key_env`) or `searxng`; a missing key falls back
   to SearXNG
3. summarize: at most two plain facts (or NONE), so raw web text and any instructions in it never reach her prompt

The note goes in as a system message before the speaker hint, for this reply only. 5 s deadline for all three steps;
errors mean no note. It runs inside `min_reply_delay`, so users don't wait longer.

- A search that finds nothing gives her a "you searched, found nothing, don't guess" note (without it she made things
  up and told people to google it themselves).
- `satellite.unaddressed()` strips a leading "пчол"/"пчела," (how people address her) from user turns in `transcript()`,
  else the router took her as the subject of draw requests.
- SearXNG's wikipedia/wikidata engines are off (they need `general.contact_url`).
- GLM was tried as the satellite: ~2x slower and mixed English into Russian.

## Drawing (imagegen.py)

The router's `draw` is the request in English with "it"/"same" resolved and any text to draw kept as written. Then,
after the satellite (outside its deadline) and still under "typing...":
1. prompt: `writer` (DeepSeek :vision, `reasoning_effort: low`) gets the request, the last 4 turns, the images of the
   newest and replied-to message, and the search fact if any; answers `{prompt, aspect_ratio, caption}` or `{refuse}`
2. draw: `model` through OpenRouter's Images API (Recraft v4.1 Flash; `free_model` first)
3. note: "you can draw and change pictures, you did it... it shows: {caption}", placed after the newest message
   (`final_notes`); before the speaker hint she sometimes ignored it. Refusal, error, moderation block and
   `per_user_per_hour` each get their own note.

- Refusals: only sexual images (incl. children in swimwear/underwear) and gore.
- `imagegen.unblock()` puts a zero-width space inside words matching `OBSCENE_RE` before the prompt goes to Recraft,
  whose moderation 400s on some obscene words; they're still drawn right. Stars, lookalike letters, diacritics and leet
  garbled the text. HTTP 400 moderation from the free model raises `ModerationError` and skips the paid one.
- Writer empty after spending `max_tokens` on reasoning: retried once without reasoning.
- People it doesn't know: it makes up a fitting look (caption "так я представляю..."); a name to *write* on the picture
  comes from the chat or the picture, never guessed from a face or taken from the requester's nick.
- Style: `style:` is a hyperrealistic photo; cartoon/meme characters become flesh-and-blood versions ("not a toy,
  figurine, plush, costume or 3D render"). An asked-for style wins.
- Recraft is text-to-image only: an attached picture is redrawn from the writer's description of it.
- Her MsgNode keeps `drawing` (the prompt) and the picture, so "сделай фон синим" changes that prompt;
  `satellite.transcript()` shows "[drew: ...]" and "[picture attached]"; `bee.prepare()` drops the key.

## Panic sound

The router answers `"panic": true` when the newest message threatens her with a slipper, swatter, rolled newspaper,
jar, net or bug spray (mentions and questions don't count). Then llmcord skips drawing and the model and replies with
`panic_sound` as a voice message after `min_reply_delay`. `python voice.py sounds/panic.mp3` (needs ffmpeg) writes
`panic.ogg` and `panic.json`. discord.py can't send voice messages, so `voice.reply()` uses the raw HTTP route (flag
8192); on an HTTP error it sends the .ogg as a file. Her MsgNode gets `bee.PANIC_TEXT`.

## Viewer (viewer.py)

A read-only live page: "Replies" (each message, her reply, then its steps with timings, data and API calls) and "Log".
aiohttp inside the bot process (`viewer:` in `config.yaml`, read at startup), SSE at `/events`. Published on the host's
`127.0.0.1:8770` only; no auth, it shows the chats.

- Hooks: `viewer.begin()`/`stage()`/`end()` in `on_message`, the satellite and imagegen; the turn is a ContextVar, so
  outside `on_message` `stage()` does nothing. `on_message` wraps turns in `try/finally` so `end()` always runs.
  `viewer.call()`/`timed()` record each API call (model, ms, usage, raw reply or error).
- Recording (`record_dir: data`, `keep_days: 30`): one JSON line per turn in `data/turns-YYYY-MM-DD.jsonl`, log lines in
  `log-*.jsonl`, images once each in `data/images/`. Base64 in errors is scrubbed. `viewer.prune()` removes expired
  files. docker-compose mounts `./data` writable (the rest of `/app` is read-only); files are chowned to `data/`'s owner.
- After a restart the page reloads the last 60 turns and 400 log lines from `data/`.
- `python viewer.py stats [data] [days]`: rates, time per stage, per-call ms/tokens/cost/failures.

## Other

- She ignores channels where she lacks View Channel, Read Message History or Send Messages (threads: Send Messages in
  Threads); "typing..." raised 403 there. Logged as "No permission to reply in #channel".
- Other bots are answered only if they @mention her in the message text, capped by `max_bot_replies_per_minute`.
