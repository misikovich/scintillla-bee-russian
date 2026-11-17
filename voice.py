"""Discord voice messages (the round play button with a waveform), which discord.py can't send yet.

A voice message is an Ogg/Opus attachment with `duration_secs` and a `waveform` (base64, up to 256 amplitude bytes),
sent with the IS_VOICE_MESSAGE flag and no text. The bot image has no ffmpeg, so the sound is converted once, on a
machine that has it:

    python voice.py sounds/panic.mp3     # writes sounds/panic.ogg and sounds/panic.json (duration and waveform)

and the bot sends the .ogg with the numbers from the .json (`panic_sound: sounds/panic.ogg` in config.yaml).
"""

from base64 import b64encode
import json
import os
import subprocess
import sys

import discord
from discord.http import Route

IS_VOICE_MESSAGE = 1 << 13


def convert(src: str) -> str:
    """src (any audio ffmpeg reads) -> <name>.ogg (Opus, mono, 48 kHz) and <name>.json; returns the .ogg path."""
    base = os.path.splitext(src)[0]
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", src, "-map_metadata", "-1", "-ac", "1", "-ar", "48000", "-c:a", "libopus", "-b:a", "64k", f"{base}.ogg"], check=True)

    # Waveform: peak amplitude of up to 256 equal slices of the sound, scaled to 0-255
    rate = 8000
    pcm = subprocess.run(["ffmpeg", "-loglevel", "error", "-i", src, "-ac", "1", "-ar", str(rate), "-f", "s16le", "-"], check=True, capture_output=True).stdout
    samples = memoryview(pcm).cast("h")
    n = min(256, max(1, len(samples) // 100))
    peaks = [max((abs(s) for s in samples[i * len(samples) // n : (i + 1) * len(samples) // n]), default=0) for i in range(n)]
    top = max(peaks) or 1
    meta = dict(duration_secs=round(len(samples) / rate, 2), waveform=b64encode(bytes(round(p * 255 / top) for p in peaks)).decode())
    with open(f"{base}.json", "w") as f:
        json.dump(meta, f)
    return f"{base}.ogg"


async def reply(msg: discord.Message, ogg_path: str) -> discord.Message:
    """Replies to msg with ogg_path as a voice message (its .json next to it gives the duration and waveform)."""
    with open(os.path.splitext(ogg_path)[0] + ".json") as f:
        meta = json.load(f)
    payload = dict(
        flags=IS_VOICE_MESSAGE,
        attachments=[dict(id=0, filename="voice-message.ogg", duration_secs=meta["duration_secs"], waveform=meta["waveform"])],
        message_reference=dict(message_id=msg.id, channel_id=msg.channel.id, fail_if_not_exists=False),
        allowed_mentions=dict(parse=[], replied_user=False),
    )
    file = discord.File(ogg_path, filename="voice-message.ogg")
    form = [
        dict(name="payload_json", value=json.dumps(payload)),
        dict(name="files[0]", value=file.fp, filename=file.filename, content_type="audio/ogg"),
    ]
    try:
        channel = msg.channel
        data = await channel._state.http.request(Route("POST", "/channels/{channel_id}/messages", channel_id=channel.id), form=form, files=[file])
    finally:
        file.close()
    return discord.Message(state=channel._state, channel=channel, data=data)


if __name__ == "__main__":
    print(convert(sys.argv[1]))
