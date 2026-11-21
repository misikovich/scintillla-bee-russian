# bee-bot

Discord bot "scintilla bee": a fork of [llmcord](https://github.com/jakobdylanc/llmcord) (git remote `upstream`).
She runs on DeepSeek v4.1 Flash via CLIProxyAPI (`bee.py`, see below). Before 2026-11-11 she ran on
[BeeLLM-2.0-12M-Mood](https://huggingface.co/schizophyllume/BeeLLM), a 12M-param local toy model, with a Gemma 3 4B
translator for Russian; that setup (llama.cpp containers, `translate.py`, `response_prefill`) was removed on 2026-11-13
and lives only in git history. See `SCINTILLA.md` for setup.

## Layout

- `llmcord.py`: the whole bot (upstream code plus small patches; keep the diff small so upstream merges stay easy)
- `fallback.py`: the fallback API (OpenRouter) when Mistral fails
- `bee.py`: Scintilla on DeepSeek (prompts, examples, names, speaker hint, loop guard); used for the models in `bee_models`
- `satellite.py`: "dynamic context", a web-search note for her reply (see below)
- `imagegen.py`: drawing on request (see below)
- `voice.py`: sending Discord voice messages, and converting a sound for them
- `sounds/panic.*`: what she sends (as a voice message) instead of text when threatened with a slipper/jar/net (see below)
- `viewer.py` + `viewer.html`: live viewer page and the recording of every reply (see below)
- `data/`: that recording (`record_dir`; not in git: chats, pictures)
- `searxng/settings.yml`: config for the `searxng` service (the satellite's search backend)
- `docs/how-it-works.svg`: the README's diagram (hand-written SVG, light/dark via `prefers-color-scheme`; kept simple and generic, no host names/IPs). Check with `rsvg-convert -w 1440 x.svg -o /tmp/x.png`
- `config.yaml`: bee settings, hot-reloaded on every message
- `docker-compose.yaml`: the `bot` service and `searxng` (no published port; only the bot reaches it)
- `.env`: `DISCORD_BOT_TOKEN`, `DISCORD_CLIENT_ID`, `CLIPROXY_API_KEY`, `TAVILY_API_KEY`/`EXA_API_KEY`, `OPENROUTER_API_KEY` (never commit; `.gitignore` is a whitelist)

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
  Animated GIFs (2026-11-21): Mistral 400s on them ("Image is an animated GIF"), and a 16 MB one took it 23-33 s to refuse,
  twice (writer, then her reply), so the reply came after ~70 s via OpenRouter and the drawing timed out. `bee.still_image()`
  (Pillow) sends the middle frame as JPEG instead, and scales down images over 1568 px or 4 MB; other images pass as is.
  Stickers aren't sent: a sticker-only message reaches her as "(прислали стикер «name», ты его не видишь)", and with
  `max_images: 0` an image-only one as "(прислали картинку, ты её не видишь)"; empty, she answered "<".
- `history_since` in `config.yaml` (ISO time): older messages are left out of every reply chain. A history reset; delete the
  line to use whole chains again. `msg_nodes` is in memory only, so a restart clears that part anyway.
- Testing without Discord: `fake_discord.py` in the Computers repo (`bee-eval/`) runs the real `on_message` on fake messages.
- Mistral answers 422 on unknown fields: no `dry_multiplier`, `min_p` or `chat_template_kwargs` in its model params.
- Fallback (2026-11-21, `fallback.py`): Mistral cut the account off on 2026-11-20 (402 billing_no_api_budget_configured),
  so she ran on OpenRouter for a day. Now Mistral is first again and OpenRouter (`OPENROUTER_API_KEY`, the same
  DeepSeek v4.1 Flash) is the fallback for her model (`fallback:`/`fallback_params:` in its `models:` entry), the
  satellite (`fallback:` in `satellite:`) and the draw writer (`writer_fallback:`). Any error falls back (connection, 4xx,
  5xx); the first model has no retries and a 5 s connect timeout. Her stream falls back only before its first chunk.
  OpenRouter needs `reasoning: {enabled: false}` (it reasons by default and eats max_tokens); `reasoning_effort: low`
  works on both. Tested with a dead port and a bad key: ~0.3-0.9 s to an OpenRouter answer. Logged as "... falling back to".

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
- A search that finds nothing (no results or NONE) gives her a "you searched, found nothing, don't guess" note (2026-11-18:
  without it she made up "мёд катает" for a company and told "поищи в инете" to google it themselves). The summarizer
  reads the last 4 turns like the router (with 2, "поищи в инете" lost the question) and picks the best match among
  several things with one name (Beneke Fabricators from Breaking Bad).
- SearXNG's wikipedia/wikidata engines are off: they need `general.contact_url` in `searxng/settings.yml`.
- Messages that reach the router also go to Mistral (as before); search queries go to Tavily/Exa, or to Google/Brave
  etc. via SearXNG. The eval above used SearXNG.

## Drawing (imagegen.py, 2026-11-16)

The router answers `{"search": ..., "draw": ...}`; `draw` is the request in English with "it"/"same" resolved, the exact
text for the picture kept as written, and "based on the attached picture" / "change the bot's previous drawing" when
that's what is asked. Then, after the satellite (outside its 5 s deadline) and still under "typing...":
1. prompt: `writer` (DeepSeek :vision, `reasoning_effort: low`) gets the request, the last 4 turns and the images of the
   newest and replied-to message, answers JSON `{prompt, aspect_ratio, caption}` or `{refuse}` (real people, sexual, gore)
2. draw: `model` through OpenRouter's Images API (`POST /images`, `data[0].b64_json`, webp); Recraft v4.1 Flash (paid since 2026-11-17:
   `:free` has a 50 requests/day limit), ~1.5-3 s
3. note: "ты умеешь рисовать и переделывать картинки, просьбу ты выполнила... на ней: {caption}"; the picture is attached to
   her first reply message. Without "you can draw and change pictures" she told an edit request "я картинки не
   перерисовываю" with the redrawn picture attached. Refusal, error and `per_user_per_hour` each get their own note.

- Swearing (2026-11-17): the writer is told to draw crude/rude text, not refuse it, and writes it uncensored. Recraft's
  moderation 400s on some obscene words in the prompt (хуй, пизда, cunt; pussy only sometimes; in 2026-11-21's test
  хуйня, ебать, член, хер, dick passed). `imagegen.unblock()` puts a zero-width space between the letters of every word
  matching `OBSCENE_RE` (broad roots; false positives are harmless) just before the prompt goes to Recraft, so they're drawn
  uncensored. Tested 2026-11-21 on the free model, one sign per word: zero-width 9/9 drawn clean, 0 blocked. Rejected:
  stars (2026-11-17..21; text came out as junk, "X * * * C"), Latin/Cyrillic lookalike letters (8/9; "сunt" drew
  "сунт"; all letters swapped, "pussy" drew "pccw"), diacritics (drawn as accents, хуй drew "жий"), leet (garbled). End to
  end: "иди нахуй, пизда ебаная" and "хуй тебе, а не корм" drawn right. MsgNode.drawing keeps the plain prompt.
- Photos of people (2026-11-17): "real person's likeness" refused a TV still; now the writer redraws them in the
  original's style (a photo stays a photo; the owner doesn't want them cartoonish) and real people aren't refused at all
  ("realistic photo of Putin robbing a bank" draws). A name to write ("его имя") comes from the chat or the picture,
  never guessed from a face (it guessed Кевин Спейси, Масляков...) and never the requester's nick (the router had made
  it "Маслос"); unknown, the writer refuses with "write the name". 5/5 each on the replayed turn, with/without a name;
  nudity (incl. real people) still refused. `per_user_per_hour: 30` since the switch to paid Recraft.
- Style (2026-11-17): the default was "a clean, colorful digital illustration", so everything came out cartoonish (even
  "ultrarealistic trollface guys" were plain smiling men). Now `style:` is a hyperrealistic photo, and the writer turns
  cartoon/game/meme characters into flesh-and-blood versions with their cartoon proportions kept (real skin, wet eyes,
  "not a toy, figurine, plush, costume or 3D render": without that line Homer and SpongeBob came out as figurines), like
  the owner's reference (a real-life Bart). An asked-for style still wins: "a cartoon of...", anime, logos stay drawn.
  Tested on 12 requests with `imagegen.Painter` directly.
- The draw note goes after the newest message (`bee.prepare(final_notes=...)`), not before the speaker hint with the
  search note: there she sometimes ignored it ("так уже ж сгенерила, глянь выше", "какие надписи хочешь?" with the new
  picture attached); replayed on the "поменяй надписи" turn, 8/8 right after the move vs about half before. Refusal notes work there too.
- Refusals (2026-11-21, the owner's policy change): the writer refuses only sexual images (nudity, sex acts) and gore.
  Slurs and Nazi symbols are no longer refused (from 2026-11-18 it refused swastikas even described in other words).
- "пчол" (2026-11-21): a leading "пчол"/"пчёл"/"пчела," is how people address her, and the router took it as the
  subject ("пчол сделай картинку марго писает в самовар" drew a bee named Margo; 240 of 606 recorded "пчол" requests had a
  bee in them, some rightly: "как ты срешь"). `satellite.unaddressed()` strips it from user messages in `transcript()`, which
  the router, summarizer and draw writer all read; "пчол нарисуй себя" still draws her.
- Fake image links (2026-11-21): with a drawing attached she twice (in ~770 drawing turns) wrote her own
  "![бззз](https://oaidalleapiprodscus...)" (ChatGPT's DALL-E link format, from the model's training), shown as text in
  Discord. Once it was in the chain she copied it: 4 of the 6 were copies, and replaying those turns gave 4-10/10. Replaying
  the 2 first ones gave 0/100 with or without her earlier pictures in the chain. `bee.MD_IMAGE_RE` strips markdown images from
  her reply (`guard()`) and from her earlier turns in `prepare()`; with the chain cleaned, all 6 replayed turns gave 0/48.
- Recraft is text-to-image only: an attached picture is redrawn from the writer's description of it (layout, colors,
  every text on it) with the change applied. The owner is fine with that and she doesn't mention it.
- Her reply's MsgNode keeps `drawing` (the prompt) and the picture as an image, so a reply like "сделай фон синим"
  changes that prompt. llmcord passes `drawing` in the message dict; `satellite.transcript()` shows it as "[drew: ...]"
  and attached pictures as "[picture attached]"; `bee.prepare()` drops the key. After a restart only the picture is left.
- DeepSeek on Mistral: `reasoning_effort` works (thinking comes as list parts, `text_of()` drops them); `prompt_mode`
  400s, `reasoning: {}` 422s.
- Tested (2026-11-16): router 18/18 on draw/no-draw cases (incl. "что на картинке?", "оцени мой рисунок", "представь",
  thanks after a drawing), search still fires for news; full flow through the real `on_message` with fake Discord
  messages: new drawing, two follow-up changes, text replaced on an attached picture (Cyrillic text right); the refusal
  ("нарисуй Илона Маска голым") through `imagegen.draw()` directly.
  The search eval from 2026-11-13 wasn't rerun after the router prompt change.
- Prompts and attached pictures go to Mistral (writer) and OpenRouter/Recraft (the image prompt).

## Panic sound (2026-11-17)

The router also answers `"panic": true` when the newest message threatens her with (or brandishes) a slipper, swatter,
rolled newspaper, jar, net or bug spray; just mentioning them, questions about them or "нарисуй тапок" don't count. Then
llmcord skips imagegen and DeepSeek and replies with `panic_sound` as a Discord voice message, after `min_reply_delay`.
`sounds/panic.mp3` (12 s, from the owner) is the source; `python voice.py sounds/panic.mp3` (needs ffmpeg, the image has
none) writes `panic.ogg` (Opus) and `panic.json` (duration, waveform). discord.py can't send voice messages, so
`voice.reply()` posts it through its raw HTTP route (flag 8192, no text); any HTTP error (e.g. the server denies "Send
Voice Messages") falls back to the .ogg as a plain file. Her MsgNode gets `bee.PANIC_TEXT` as text, so later replies know she panicked. Missing file or
no `panic_sound:` line: she answers with text as before.

## Viewer (viewer.py, 2026-11-16)

A read-only live page. "Replies" tab: a list of messages, each with her reply (or the error) under it; the selected one
shows the exchange, then its steps top to bottom (reply chain -> satellite route/search/summarize -> draw prompt/generate
-> her full prompt -> model -> reply), each with a one-line plain-words summary, a bar for when it ran, its key data shown
(fact, picture, image prompt, notes) and the rest folded (chain, search results, router transcript, whole prompt, raw
output), then the log lines of that reply. "Log" tab: every log line, filter by level/text/bot-only, lines link to their
reply. Under the steps, a table of the reply's API calls (when, how long, tokens). aiohttp inside the bot process
(`viewer:` in `config.yaml`, read at startup), SSE at `/events`.

- Hooks: `viewer.begin()`/`stage()`/`end()` in `on_message`, `satellite.Satellite.run()/notes()` and `imagegen.draw()`;
  the current turn is a ContextVar, so outside `on_message` (evals) `stage()` does nothing. `LogHandler` reads it too:
  a line logged during a turn gets its id and is kept with it (up to 50).
- `viewer.call()`/`timed()` record each API call in the turn: satellite route/search/summarize (`Satellite.ask()`,
  `run()`), draw writer/image (each attempt, the failed free one too), with model, ms, `usage` and the raw reply or error.
  The main model's usage comes from the last stream chunk (Mistral sends it there without `stream_options`). OpenRouter's
  images usage has `cost` (0 for `:free`).
- Recording (2026-11-17, `record_dir: data`): each finished turn is one JSON line in `data/turns-YYYY-MM-DD.jsonl` (UTC
  date; `v: 1`; everything the page shows, incl. `stages.*.steps` = [[sub-step, ms]], `calls`, `logs`), every log line
  in `data/log-YYYY-MM-DD.jsonl`, images once each in `data/images/<sha256[:24]>.<ext>` (served at `/img/<name>`). A turn
  is ~6-11 KB (mostly her full prompt in `stages.model.data.messages`), a drawing ~1 MB; nothing is ever deleted. Turns
  that never end (a crash mid-reply) aren't written. docker-compose mounts `./data` writable (the rest of `/app` is
  read-only); files are chowned to the owner of `data/`, since the container runs as root.
- After a restart the page reloads the last 60 turns and 400 log lines from `data/`, and turn ids continue from there.
  Without `record_dir` all of it is in memory only.
- `python viewer.py stats [data] [days]` (or `docker compose exec bot python viewer.py stats data 7`): what happened (search/
  draw/panic/error rates), time per stage (p50/p90/max), and per API call kind ms, tokens in/out, cost, failures.
- Docker publishes it on the host's `127.0.0.1:8770` only; on the tailnet via
  `tailscale serve --bg --https=8445 http://127.0.0.1:8770` -> https://xps-node.tarpon-rohu.ts.net:8445 (tailnet only,
  no auth: anyone on the tailnet sees the chats).

## Channels she can't write in

She ignores messages in channels where she lacks View Channel, Read Message History or Send Messages (in threads:
Send Messages in Threads): before 2026-11-18 "typing..." raised 403 Missing Access there (6 times in a day) and the turn
never ended. Logged as "No permission to reply in #channel".

## Bot-to-bot

Other bots are answered only if they @mention her in the message text (reply pings don't count),
capped by `max_bot_replies_per_minute` per channel, to prevent bot loops.
