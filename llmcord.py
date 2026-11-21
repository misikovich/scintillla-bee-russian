import asyncio
from base64 import b64encode
from dataclasses import dataclass, field
from datetime import datetime, timezone
from io import BytesIO
import logging
import os
import re
from typing import Any, Literal, Optional

import discord
from discord.app_commands import Choice
from discord.ext import commands
from dotenv import load_dotenv
import httpx
import yaml

import bee
import fallback
import imagegen
import satellite
import viewer
import voice

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s: %(message)s",
)

VISION_MODEL_TAGS = ("chat-latest", "claude", "gemini", "gemma", "gpt-4", "gpt-5", "gpt-latest", "grok-4", "llama", "vision", "vl")

EMBED_COLOR_COMPLETE = discord.Color.dark_green()
EMBED_COLOR_INCOMPLETE = discord.Color.orange()

STREAMING_INDICATOR = " ⚪"
EDIT_DELAY_SECONDS = 1

MAX_MESSAGE_NODES = 500

# She also answers when called by name, without an @mention: "пчол..." (пчола, пчолка) or "bee"/"beee"/"bees" (not "been", "beer")
NAME_TRIGGER = re.compile(r"\bпчол|\bbee+s?\b", re.IGNORECASE)


def is_called(msg: discord.Message) -> bool:
    return discord_bot.user in msg.mentions or bool(NAME_TRIGGER.search(msg.content))


def can_reply(channel: discord.abc.GuildChannel | discord.Thread) -> bool:
    perms = channel.permissions_for(channel.guild.me)
    send = perms.send_messages_in_threads if isinstance(channel, discord.Thread) else perms.send_messages
    return perms.view_channel and perms.read_message_history and send


def resolve_env(node: Any) -> Any:
    if isinstance(node, dict):
        return {key.removesuffix("_env"): os.environ.get(value) if key.endswith("_env") else resolve_env(value) for key, value in node.items()}
    return node


def get_config(filename: str = "config.yaml") -> dict[str, Any]:
    with open(filename, encoding="utf-8") as file:
        return resolve_env(yaml.safe_load(file))


config = get_config()
curr_model = next(iter(config["models"]))

msg_nodes = {}
last_task_time = 0
bot_reply_times = {}

intents = discord.Intents.default()
intents.message_content = True
activity = discord.CustomActivity(name=(config.get("status_message") or "github.com/jakobdylanc/llmcord")[:128])
discord_bot = commands.Bot(intents=intents, activity=activity, command_prefix=None)

httpx_client = httpx.AsyncClient()


@dataclass
class MsgNode:
    role: Literal["user", "assistant"] = "assistant"

    text: Optional[str] = None
    images: list[dict[str, Any]] = field(default_factory=list)

    has_bad_attachments: bool = False
    fetch_parent_failed: bool = False

    parent_msg: Optional[discord.Message] = None

    # Display name of the author, so bee models can tell people apart (see bee.py)
    author_name: Optional[str] = None

    # The image prompt of the picture she drew in this reply (imagegen.py), so a follow-up can change it
    drawing: Optional[str] = None

    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


@discord_bot.tree.command(name="model", description="View or switch the current model")
async def model_command(interaction: discord.Interaction, model: str) -> None:
    global curr_model

    if model == curr_model:
        output = f"Current model: `{curr_model}`"
    else:
        if user_is_admin := interaction.user.id in config["permissions"]["users"]["admin_ids"]:
            curr_model = model
            output = f"Model switched to: `{model}`"
            logging.info(output)
        else:
            output = "You don't have permission to change the model."

    await interaction.response.send_message(output, ephemeral=(interaction.channel.type == discord.ChannelType.private))


@model_command.autocomplete("model")
async def model_autocomplete(interaction: discord.Interaction, curr_str: str) -> list[Choice[str]]:
    global config

    if curr_str == "":
        config = await asyncio.to_thread(get_config)

    choices = [Choice(name=f"◉ {curr_model} (current)", value=curr_model)] if curr_str.lower() in curr_model.lower() else []
    choices += [Choice(name=f"○ {model}", value=model) for model in config["models"] if model != curr_model and curr_str.lower() in model.lower()]

    return choices[:25]


@discord_bot.event
async def on_ready() -> None:
    if client_id := config.get("client_id"):
        logging.info(f"\n\nBOT INVITE URL:\nhttps://discord.com/oauth2/authorize?client_id={client_id}&permissions=412317191168&scope=bot\n")

    await discord_bot.tree.sync()


@discord_bot.event
async def on_message(new_msg: discord.Message) -> None:
    global last_task_time

    received_at = asyncio.get_running_loop().time()

    is_dm = new_msg.channel.type == discord.ChannelType.private

    if new_msg.author == discord_bot.user or (not is_dm and not is_called(new_msg)):
        return

    # Other bots must @mention us in the message text (a reply ping isn't enough), so two bots don't reply to each other forever
    if new_msg.author.bot and not any(mention in new_msg.content for mention in (f"<@{discord_bot.user.id}>", f"<@!{discord_bot.user.id}>")):
        return

    role_ids = set(role.id for role in getattr(new_msg.author, "roles", ()))
    channel_ids = set(filter(None, (new_msg.channel.id, getattr(new_msg.channel, "parent_id", None), getattr(new_msg.channel, "category_id", None))))

    config = await asyncio.to_thread(get_config)

    allow_dms = config.get("allow_dms", True)

    permissions = config["permissions"]

    user_is_admin = new_msg.author.id in permissions["users"]["admin_ids"]

    (allowed_user_ids, blocked_user_ids), (allowed_role_ids, blocked_role_ids), (allowed_channel_ids, blocked_channel_ids) = (
        (perm["allowed_ids"], perm["blocked_ids"]) for perm in (permissions["users"], permissions["roles"], permissions["channels"])
    )

    allow_all_users = not allowed_user_ids if is_dm else not allowed_user_ids and not allowed_role_ids
    is_good_user = user_is_admin or allow_all_users or new_msg.author.id in allowed_user_ids or any(id in allowed_role_ids for id in role_ids)
    is_bad_user = not is_good_user or new_msg.author.id in blocked_user_ids or any(id in blocked_role_ids for id in role_ids)

    allow_all_channels = not allowed_channel_ids
    is_good_channel = user_is_admin or allow_dms if is_dm else allow_all_channels or any(id in allowed_channel_ids for id in channel_ids)
    is_bad_channel = not is_good_channel or any(id in blocked_channel_ids for id in channel_ids)

    if is_bad_user or is_bad_channel:
        return

    if new_msg.author.bot:
        if not config.get("allow_bots", False):
            return

        now_ts = datetime.now().timestamp()
        recent_bot_replies = [t for t in bot_reply_times.get(new_msg.channel.id, []) if now_ts - t < 60]
        if len(recent_bot_replies) >= config.get("max_bot_replies_per_minute", 5):
            return
        bot_reply_times[new_msg.channel.id] = recent_bot_replies + [now_ts]

    # A channel she can read but not write in: Discord answers 403 to "typing..." and the reply, so stay quiet
    if not is_dm and not can_reply(new_msg.channel):
        logging.info(f"No permission to reply in #{getattr(new_msg.channel, 'name', new_msg.channel.id)} ({new_msg.channel.id}), ignoring")
        return

    viewer.begin(
        author=new_msg.author.display_name, author_id=new_msg.author.id, bot=new_msg.author.bot, content=new_msg.content,
        channel=getattr(new_msg.channel, "name", None) or "DM", guild=getattr(new_msg.guild, "name", None), url=new_msg.jump_url, model=curr_model,
    )
    viewer.stage("chain", "running")

    provider_slash_model = curr_model
    provider, model = provider_slash_model.removesuffix(":vision").split("/", 1)

    # Scintilla's models (bee.py): her own prompt and examples
    bee_mode = provider_slash_model in (config.get("bee_models") or [])

    # Messages older than this are left out of conversations (a history reset; her in-memory cache only lasts until a
    # restart, the reply chains in Discord stay)
    if history_since := config.get("history_since"):
        history_since = datetime.fromisoformat(str(history_since))
        history_since = history_since if history_since.tzinfo else history_since.replace(tzinfo=timezone.utc)

    provider_config = config["providers"][provider]

    model_parameters = dict(config["models"].get(provider_slash_model) or {})
    # Fallback API (fallback.py): the same request to another model when this one fails
    fallback_model, fallback_params = model_parameters.pop("fallback", None), model_parameters.pop("fallback_params", None)

    extra_headers = provider_config.get("extra_headers")
    extra_query = provider_config.get("extra_query")
    extra_body = (provider_config.get("extra_body") or {}) | model_parameters or None
    model_tries = fallback.attempts(provider_slash_model, extra_body, fallback_model, fallback_params, config["providers"])

    accept_images = any(x in provider_slash_model.lower() for x in VISION_MODEL_TAGS)

    max_text = config.get("max_text", 100000)
    max_images = config.get("max_images", 5) if accept_images else 0
    max_messages = config.get("max_messages", 25)

    # Build message chain and set user warnings
    messages = []
    user_warnings = set()
    curr_msg = new_msg

    while curr_msg != None and len(messages) < max_messages and not (history_since and curr_msg.created_at < history_since):
        curr_node = msg_nodes.setdefault(curr_msg.id, MsgNode())

        async with curr_node.lock:
            if curr_node.text == None:
                cleaned_content = curr_msg.content.removeprefix(discord_bot.user.mention).lstrip()

                curr_node.author_name = curr_msg.author.display_name

                # Bee models read names, not IDs: "<@123>" -> "@Name" (her own mention is dropped)
                if bee_mode:
                    for user in curr_msg.mentions:
                        name = "" if user == discord_bot.user else f"@{user.display_name}"
                        cleaned_content = cleaned_content.replace(f"<@{user.id}>", name).replace(f"<@!{user.id}>", name)

                    # What she can't see (stickers; images too unless the model takes them): say it was sent, instead of an
                    # empty message (she answered "<" to one)
                    if not cleaned_content:
                        media = [f"(прислали стикер «{sticker.name}», ты его не видишь)" for sticker in curr_msg.stickers]
                        media += [
                            "(прислали файл)" if not (att.content_type or "").startswith("image") else "" if max_images else "(прислали картинку, ты её не видишь)"
                            for att in curr_msg.attachments
                        ]
                        cleaned_content = " ".join(filter(None, media))

                good_attachments = [att for att in curr_msg.attachments if att.content_type and any(att.content_type.startswith(x) for x in ("text", "image"))]

                attachment_responses = await asyncio.gather(*[httpx_client.get(att.url) for att in good_attachments])

                curr_node.role = "assistant" if curr_msg.author == discord_bot.user else "user"

                curr_node.text = "\n".join(
                    ([cleaned_content] if cleaned_content else [])
                    + ["\n".join(filter(None, (embed.title, embed.description, embed.footer.text))) for embed in curr_msg.embeds]
                    + [component.content for component in curr_msg.components if component.type == discord.ComponentType.text_display]
                    + [resp.text for att, resp in zip(good_attachments, attachment_responses) if att.content_type.startswith("text")]
                )

                curr_node.images = [
                    dict(type="image_url", image_url=dict(url=f"data:{att.content_type};base64,{b64encode(resp.content).decode('utf-8')}"))
                    for att, resp in zip(good_attachments, attachment_responses)
                    if att.content_type.startswith("image")
                ]

                if curr_node.role == "user" and (curr_node.text or curr_node.images) and config.get("prefix_user_ids", True):
                    curr_node.text = f"<@{curr_msg.author.id}>: {curr_node.text}"

                curr_node.has_bad_attachments = len(curr_msg.attachments) > len(good_attachments)

                try:
                    if (
                        curr_msg.reference == None
                        and not is_called(curr_msg)
                        and (prev_msg_in_channel := ([m async for m in curr_msg.channel.history(before=curr_msg, limit=1)] or [None])[0])
                        and prev_msg_in_channel.type in (discord.MessageType.default, discord.MessageType.reply)
                        and prev_msg_in_channel.author == (discord_bot.user if curr_msg.channel.type == discord.ChannelType.private else curr_msg.author)
                    ):
                        curr_node.parent_msg = prev_msg_in_channel
                    else:
                        is_public_thread = curr_msg.channel.type == discord.ChannelType.public_thread
                        parent_is_thread_start = is_public_thread and curr_msg.reference == None and curr_msg.channel.parent.type == discord.ChannelType.text

                        if parent_msg_id := curr_msg.channel.id if parent_is_thread_start else getattr(curr_msg.reference, "message_id", None):
                            if parent_is_thread_start:
                                curr_node.parent_msg = curr_msg.channel.starter_message or await curr_msg.channel.parent.fetch_message(parent_msg_id)
                            else:
                                curr_node.parent_msg = curr_msg.reference.cached_message or await curr_msg.channel.fetch_message(parent_msg_id)

                except (discord.NotFound, discord.HTTPException):
                    logging.exception("Error fetching next message in the chain")
                    curr_node.fetch_parent_failed = True

            if curr_node.images[:max_images]:
                content = [dict(type="text", text=curr_node.text[:max_text])] + curr_node.images[:max_images]
            else:
                content = curr_node.text[:max_text]

            if content != "":
                messages.append(
                    dict(content=content, role=curr_node.role)
                    | (dict(name=curr_node.author_name) if bee_mode and curr_node.role == "user" else {})
                    | (dict(drawing=curr_node.drawing) if bee_mode and curr_node.drawing else {})
                )

            if len(curr_node.text) > max_text:
                user_warnings.add(f"⚠️ Max {max_text:,} characters per message")
            if len(curr_node.images) > max_images:
                user_warnings.add(f"⚠️ Max {max_images} image{'' if max_images == 1 else 's'} per message" if max_images > 0 else "⚠️ Can't see images")
            if curr_node.has_bad_attachments:
                user_warnings.add("⚠️ Unsupported attachments")
            if curr_node.fetch_parent_failed or (curr_node.parent_msg != None and len(messages) == max_messages):
                user_warnings.add(f"⚠️ Only using last {len(messages)} message{'' if len(messages) == 1 else 's'}")

            curr_msg = curr_node.parent_msg

    viewer.stage("chain", "done", messages=messages[::-1], warnings=sorted(user_warnings))
    logging.info(f"Message received (user ID: {new_msg.author.id}, attachments: {len(new_msg.attachments)}, conversation length: {len(messages)}):\n{new_msg.content}")

    drawing = panic_sound = None
    if bee_mode:
        # Dynamic context (satellite.py): a second model may search the web first and give her a note for this reply
        notes, final_notes, draw_request = [], [], None
        if satellite_cfg := config.get("satellite"):
            async with new_msg.channel.typing():
                lang = bee.reply_language(messages)
                notes, draw_request, panic = await satellite.notes(messages, lang, satellite_cfg, config["providers"], httpx_client)
                # Threatened with a slipper/jar/net: she sends `panic_sound` instead of a text reply
                if panic and (panic_sound := config.get("panic_sound")) and not os.path.isfile(panic_sound):
                    logging.warning(f"panic_sound {panic_sound!r} not found, answering with text")
                    panic_sound = None
                # Asked to draw (imagegen.py): the picture goes with her reply, a note tells her about it
                elif draw_request and (imagegen_cfg := config.get("imagegen")):
                    drawing = await imagegen.draw(draw_request, messages, lang, new_msg.author.id, imagegen_cfg, config["providers"], httpx_client)
                    final_notes.append(drawing["note"])
        messages, lang = bee.prepare(messages, notes=notes, final_notes=final_notes)
        viewer.stage("prompt", "done", lang=lang, examples=2 * len(bee.PROMPTS[lang]["examples"]), notes=notes + final_notes)

    if (system_prompt := config.get("system_prompt")) and not bee_mode:
        now = datetime.now().astimezone()

        system_prompt = system_prompt.replace("{date}", now.strftime("%B %d %Y")).replace("{time}", now.strftime("%H:%M:%S %Z%z")).strip()

        messages.append(dict(role="system", content=system_prompt))

    # Generate and send response message(s) (can be multiple if response is long)
    curr_content = None
    finish_reason = None
    usage = None
    model_text = None
    response_msgs = []
    response_contents = []

    openai_kwargs = dict(messages=messages[::-1], stream=True, extra_headers=extra_headers, extra_query=extra_query)

    if use_plain_responses := config.get("use_plain_responses", False):
        # Plain message content (not a Components V2 TextDisplay) so every Discord client shows it; its limit is 2000
        max_message_length = 2000
    else:
        max_message_length = 4096 - len(STREAMING_INDICATOR)
        embed = discord.Embed.from_dict(dict(fields=[dict(name=warning, value="", inline=False) for warning in sorted(user_warnings)]))

    async def reply_helper(**reply_kwargs) -> None:
        reply_target = new_msg if not response_msgs else response_msgs[-1]
        if drawing and drawing["image"] and not response_msgs:
            reply_kwargs["file"] = discord.File(BytesIO(drawing["image"]), filename=f"bee.{drawing['media_type'].split('/')[-1]}")
        response_msg = await reply_target.reply(**reply_kwargs)
        response_msgs.append(response_msg)

        msg_nodes[response_msg.id] = MsgNode(parent_msg=new_msg)
        await msg_nodes[response_msg.id].lock.acquire()

    if panic_sound:
        # No model call: a panicked buzz as a voice message (voice.py); the history keeps a text stand-in so she knows she panicked
        model_text = bee.PANIC_TEXT[lang]
        viewer.stage("reply", "running", text=f"{model_text} [{panic_sound}]")
        try:
            async with new_msg.channel.typing():
                if (wait := config.get("min_reply_delay", 0) - (asyncio.get_running_loop().time() - received_at)) > 0:
                    await asyncio.sleep(wait)
                try:
                    response_msg = await voice.reply(new_msg, panic_sound)
                except discord.HTTPException as e:  # e.g. no "Send Voice Messages" permission there: a plain audio file
                    logging.warning(f"Voice message failed ({e}), sending the sound as a file")
                    response_msg = await new_msg.reply(file=discord.File(panic_sound))
            response_msgs.append(response_msg)
            msg_nodes[response_msg.id] = MsgNode(text=model_text, parent_msg=new_msg)
        except Exception as e:
            logging.exception("Error while sending the panic sound")
            viewer.fail(e)
        viewer.end(replies=[msg.jump_url for msg in response_msgs])
        return

    viewer.stage("model", "running", model=model, params=extra_body, messages=messages[::-1])
    try:
        async with new_msg.channel.typing():
            async for chunk in fallback.stream(model_tries, **openai_kwargs):
                usage = viewer.usage(chunk) or usage  # Mistral sends it with the last chunk
                if finish_reason != None:
                    break

                if not (choice := chunk.choices[0] if chunk.choices else None):
                    continue

                finish_reason = choice.finish_reason

                prev_content = curr_content or ""
                # Text parts only: Mistral's reasoning models (GLM) stream "thinking" parts as lists
                curr_content = satellite.text_of(choice.delta.content)

                new_content = prev_content if finish_reason == None else (prev_content + curr_content)

                if response_contents == [] and new_content == "":
                    continue

                if start_next_msg := response_contents == [] or len(response_contents[-1] + new_content) > max_message_length:
                    response_contents.append("")

                response_contents[-1] += new_content

                if not use_plain_responses:
                    time_delta = datetime.now().timestamp() - last_task_time

                    ready_to_edit = time_delta >= EDIT_DELAY_SECONDS
                    msg_split_incoming = finish_reason == None and len(response_contents[-1] + curr_content) > max_message_length
                    is_final_edit = finish_reason != None or msg_split_incoming
                    is_good_finish = finish_reason != None and finish_reason.lower() in ("stop", "end_turn")

                    if start_next_msg or ready_to_edit or is_final_edit:
                        embed.description = response_contents[-1] if is_final_edit else (response_contents[-1] + STREAMING_INDICATOR)
                        embed.color = EMBED_COLOR_COMPLETE if msg_split_incoming or is_good_finish else EMBED_COLOR_INCOMPLETE

                        if start_next_msg:
                            await reply_helper(embed=embed, silent=True)
                        else:
                            await asyncio.sleep(EDIT_DELAY_SECONDS - time_delta)
                            await response_msgs[-1].edit(embed=embed)

                        last_task_time = datetime.now().timestamp()

            if use_plain_responses:
                viewer.stage("model", "done", raw="".join(response_contents), finish_reason=finish_reason, usage=usage)
                # Loop guard and no "Сцинтилла:" label (see bee.py)
                if bee_mode and (model_text := bee.guard("".join(response_contents))):
                    response_contents = [model_text[i : i + max_message_length] for i in range(0, len(model_text), max_message_length)]

                # Don't answer instantly: wait until min_reply_delay seconds after the message came in ("typing..." shows meanwhile)
                viewer.stage("reply", "running", text=model_text or "".join(response_contents))
                if (wait := config.get("min_reply_delay", 0) - (asyncio.get_running_loop().time() - received_at)) > 0:
                    await asyncio.sleep(wait)

                for content in response_contents:
                    await reply_helper(content=content, suppress_embeds=True)

    except Exception as e:
        logging.exception("Error while generating response")
        viewer.fail(e)

    viewer.end(replies=[msg.jump_url for msg in response_msgs])

    for response_msg in response_msgs:
        msg_nodes[response_msg.id].text = model_text or "".join(response_contents)
        if drawing and drawing["image"] and response_msg == response_msgs[0]:
            msg_nodes[response_msg.id].drawing = drawing["prompt"]
            msg_nodes[response_msg.id].images = [dict(type="image_url", image_url=dict(url=f"data:{drawing['media_type']};base64,{b64encode(drawing['image']).decode()}"))]
        msg_nodes[response_msg.id].lock.release()

    # Delete oldest MsgNodes (lowest message IDs) from the cache
    if (num_nodes := len(msg_nodes)) > MAX_MESSAGE_NODES:
        for msg_id in sorted(msg_nodes.keys())[: num_nodes - MAX_MESSAGE_NODES]:
            async with msg_nodes.setdefault(msg_id, MsgNode()).lock:
                msg_nodes.pop(msg_id, None)


async def main() -> None:
    await viewer.serve(config.get("viewer"), lambda: dict(
        model=curr_model, ready=discord_bot.is_ready(), user=str(discord_bot.user), latency_ms=round(discord_bot.latency * 1000) if discord_bot.is_ready() else None,
        guilds=len(discord_bot.guilds), msg_nodes=len(msg_nodes), satellite=bool(config.get("satellite")), imagegen=bool(config.get("imagegen")),
    ))
    await discord_bot.start(config["bot_token"])


try:
    asyncio.run(main())
except KeyboardInterrupt:
    pass
