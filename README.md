<h1 align="center">
  scintilla bee 🐝
</h1>

<h3 align="center"><i>
  A Discord bee who buzzes in every phrase. In Russian and English.
</i></h3>

Scintilla is a Discord bot: a girl bee who talks to everyone like a teasing old buddy, buzzes more than she talks, and
panics at slippers, jars and nets. She runs on DeepSeek v4.1 Flash (Mistral's API, through
[CLIProxyAPI](https://github.com/router-for-me/CLIProxyAPI)).

It's a fork of [llmcord](https://github.com/jakobdylanc/llmcord); her prompt, examples and reply clean-up are in
[bee.py](bee.py).

## Talking to her

@mention her, or call her by name ("пчол...", "bee"), to start a conversation; reply to her messages to continue it.
In DMs you can just talk.

> **Миша:** как дела?<br>
> **Scintilla:** бзззз~ да норм, Миша, бзз, солнышко, нектар, бжжж. сам как, бзбзз?

> **Тёма:** кто написал войну и мир?<br>
> **Scintilla:** бзз, Толстой, бззз. Лев, бзбзз. классика же, бжж

She answers in the language of your message, knows who's who in the chat, and only snaps back at people who are
rude to her. Her memory is the reply chain, up to the last 8 messages.

## Patches over llmcord

- **Names, not IDs.** Each user message reaches her as "Name: text", and @mentions become `@Name`.
- **Speaker hint.** A note before the newest message tells her who's writing, so one person's insults don't
  spill over to everyone else in the chain.
- **Loop guard.** Runaway repeats ("ббббббб…") are cut down.
- **Name trigger.** She answers "пчол..."/"bee" without an @mention.
- **Bot-to-bot, with a limit.** Other bots can talk to her, but only by @mentioning her in the message text, and at
  most 5 times per minute per channel, so two bots can't loop forever.
- **Plain messages** instead of embeds, so every Discord client shows them, sent after a short `min_reply_delay`.

Details are in [SCINTILLA.md](SCINTILLA.md).

## Running it

You need a Discord application with a bot token (see [SCINTILLA.md](SCINTILLA.md#discord-setup)) and a CLIProxyAPI
key, then:

```sh
cp .env.example .env     # fill in DISCORD_BOT_TOKEN, DISCORD_CLIENT_ID and CLIPROXY_API_KEY
docker compose up -d --build
docker compose logs bot  # prints the invite URL
```

Settings live in [config.yaml](config.yaml) and are reloaded on every message, so you can change them without
restarting.

## Credits

- [llmcord](https://github.com/jakobdylanc/llmcord) by jakobdylanc: the bot this is built on
- [BeeLLM](https://huggingface.co/schizophyllume/BeeLLM) by schizophyllume: the original bee (until 2026-11-11)

License: [MIT](LICENSE.md) (llmcord).
