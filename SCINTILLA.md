# scintilla bee 🐝

[llmcord](https://github.com/jakobdylanc/llmcord) (remote `upstream`) wired up to
[BeeLLM-2.0-12M-Mood](https://huggingface.co/schizophyllume/BeeLLM), served by llama.cpp.

## Discord setup

1. [Developer Portal](https://discord.com/developers/applications) → **New Application** → name it `scintilla bee`.
2. **Bot** tab: upload the avatar, **Reset Token** (copy it), enable **Message Content Intent**.
3. **General Information**: copy the **Application ID**.
4. `cp .env.example .env` and fill in both values.
5. Start the bot, then open the invite URL it logs on startup (`docker compose logs bot`).

## Run

With Docker (llama-server + bot, model is downloaded into a volume on first start):

```sh
docker compose up -d --build
```

The `translator` service needs an NVIDIA GPU (~3 GB VRAM, e.g. a GTX 1650 Ti 4 GB) with the
[NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html)
installed. Without it, delete the `translator` service and the `translator:` block in `config.yaml`.

Without Docker:

```sh
llama-server -hf schizophyllume/BeeLLM -hff BeeLLM-2.0-12M-Mood.gguf -c 4096 --jinja --port 8081
llama-server -hf ggml-org/gemma-3-4b-it-GGUF:Q4_K_M --no-mmproj -ngl 99 -c 4096 -np 2 --jinja --port 8082  # translator, optional
uv venv && uv pip install -r requirements.txt && .venv/bin/python llmcord.py
```

Talk to her by @mentioning her; reply to her messages to continue a conversation.

## Changes from upstream

- `prefix_user_ids: false` stops prefixing user messages with `<@ID>: `. BeeLLM was trained on
  plain text and answers prefixed messages with `[angry:low] BZzZzZZZ` every time.
- `allow_bots: true` lets other bots talk to her, but only when they @mention her in the message
  text (a reply ping doesn't count), capped at `max_bot_replies_per_minute` per channel so two
  bots can't loop forever.
- `response_prefill: "["` starts every reply with the mood tag's `[`. Without it some messages
  (e.g. "put u in jar") get an instant end-of-sequence (~99.9%), so the bot only shows "typing" and sends nothing.
- Plain responses are sent as normal message content instead of a Components V2 `TextDisplay`,
  which some clients (e.g. Swiftcord) render as an empty message.
- The model is named `bee/scintilla` because llmcord treats any name containing "llama" as a
  vision model and would send it images.
- No system prompt: BeeLLM's chat template drops system messages.
- Russian translation (`translate.py`, `translator:` in `config.yaml`): BeeLLM only understands English, so messages
  with Cyrillic are translated to English by Gemma 3 4B before she sees them. Her replies to Russian messages are
  translated back. Her `[mood:level]` tag stays as is, and her buzzing is spelled in Cyrillic (`bzz zip` → `бзз зип`).
  The conversation history keeps her English reply. If the translator is down, the bot falls back to the
  untranslated text. Only works with `use_plain_responses: true`.
