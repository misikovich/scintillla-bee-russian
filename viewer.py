"""Viewer: a read-only live page of what she's doing, for the owner (see
SCINTILLA.md). Each reply is a "turn" with stages, shown top to bottom with a timing bar each:

    received -> chain -> satellite (route, search, summarize) -> draw -> prompt -> model -> reply

llmcord.py, satellite.py and imagegen.py call stage(); the current turn is a ContextVar set by begin() in on_message,
so the calls are no-ops outside a turn (tests) and need no extra arguments. call() records one API call
(model, time, token usage, raw reply) in the current turn. Images (data: URLs) are named by a hash of their bytes and
served at /img/<name> instead of being sent inline. The page keeps the last `turns` turns and `logs` log lines.

Recording (`record_dir`): each finished turn is appended as one JSON line to <record_dir>/turns-YYYY-MM-DD.jsonl (UTC
date) with everything the page shows (info, stages with data and timings, sub-step times, API calls, its log lines);
every log line goes to log-YYYY-MM-DD.jsonl; images are saved once as images/<name>. After a restart the page reloads
the last turns and log lines from there. Nothing is deleted. `python viewer.py stats [record_dir] [days]` summarizes it.

`viewer:` in config.yaml (read at startup): port, host (127.0.0.1 unless set; docker-compose sets VIEWER_HOST=0.0.0.0 and
publishes the port on the host's 127.0.0.1 only), record_dir. Delete the block to turn it all off.
"""

import asyncio
from collections import deque
from contextlib import contextmanager
from contextvars import ContextVar
from base64 import b64decode
from datetime import date, datetime, timedelta, timezone
import glob
import hashlib
import itertools
import json
import logging
import mimetypes
import os
import re
import sys
import time
import traceback
from typing import Any, Callable

from aiohttp import web

STAGES = ("received", "chain", "satellite", "draw", "prompt", "model", "reply")

turns: deque["Turn"] = deque(maxlen=60)
logs: deque[dict[str, Any]] = deque(maxlen=400)
subscribers: set[asyncio.Queue] = set()
current: ContextVar["Turn | None"] = ContextVar("viewer_turn", default=None)
state_fn: Callable[[], dict[str, Any]] = dict
started_at = time.time()
_ids = itertools.count(1)
record_dir: str | None = None
_keep_days: int = 0
_last_prune_day: str | None = None
_bg_tasks: set[asyncio.Task] = set()
IMAGE_NAME = re.compile(r"^[0-9a-f]{24}\.[a-z0-9]{2,5}$")
DATA_URI_RE = re.compile(r"data:[^\s\"'<>]{1,80};base64,[A-Za-z0-9+/=]{100,}")
IMG_REF_RE = re.compile(r"img/([0-9a-f]{24}\.[a-z0-9]{2,5})")
DATED_FILE_RE = re.compile(r"^(?:turns|log)-(\d{4}-\d{2}-\d{2})\.jsonl$")


def _scrub(text: str, limit: int | None = None) -> str:
    """Replace embedded base64 data URIs (e.g. echoed in provider errors) and optionally cap length."""
    text = DATA_URI_RE.sub("<base64 omitted>", text)
    return text[:limit] if limit is not None and len(text) > limit else text


def prune(directory: str, keep_days: int) -> tuple[int, int]:
    """Delete turns/log JSONL files older than `keep_days` UTC days and unreferenced images older than 1 hour."""
    if keep_days <= 0:
        return 0, 0
    cutoff = datetime.now(timezone.utc).date() - timedelta(days=keep_days)
    deleted_jsonl = 0
    kept_turns: list[str] = []
    try:
        entries = os.listdir(directory)
    except OSError:
        return 0, 0

    for name in entries:
        m = DATED_FILE_RE.match(name)
        if not m:
            continue
        try:
            file_date = date.fromisoformat(m.group(1))
        except ValueError:
            continue
        path = os.path.join(directory, name)
        if file_date < cutoff:
            try:
                os.remove(path)
                deleted_jsonl += 1
            except OSError:
                pass
        elif name.startswith("turns-"):
            kept_turns.append(path)

    referenced: set[str] = set()
    for path in kept_turns:
        try:
            with open(path, encoding="utf-8") as f:
                for line in f:
                    referenced.update(IMG_REF_RE.findall(line))
        except OSError:
            pass

    deleted_images = 0
    images_dir = os.path.join(directory, "images")
    age_cutoff = time.time() - 3600
    try:
        image_files = os.listdir(images_dir)
    except OSError:
        image_files = []
    for name in image_files:
        if not IMAGE_NAME.match(name) or name in referenced:
            continue
        path = os.path.join(images_dir, name)
        try:
            if os.path.getmtime(path) < age_cutoff:
                os.remove(path)
                deleted_images += 1
        except OSError:
            pass

    return deleted_jsonl, deleted_images


def _schedule_prune(directory: str, keep_days: int) -> bool:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return False

    async def _run() -> None:
        try:
            jsonl_n, img_n = await asyncio.to_thread(prune, directory, keep_days)
            if jsonl_n or img_n:
                logging.info(f"Viewer: pruned {jsonl_n} old log/turn file(s) and {img_n} unreferenced image(s)")
        except Exception as e:
            logging.warning(f"Viewer: prune failed ({type(e).__name__}: {e})")

    task = loop.create_task(_run())
    _bg_tasks.add(task)
    task.add_done_callback(_bg_tasks.discard)
    return True


# --- recording ---

def _own(path: str) -> None:
    """Files take the owner of record_dir (in Docker the bot runs as root; the host's user should still own them)."""
    try:
        st = os.stat(record_dir)
        os.chown(path, st.st_uid, st.st_gid)
    except (OSError, TypeError):
        pass


def _append(kind: str, at: float, record: dict[str, Any]) -> None:
    global _last_prune_day
    if not record_dir:
        return
    day = time.strftime("%Y-%m-%d", time.gmtime(at))
    path = os.path.join(record_dir, f"{kind}-{day}.jsonl")
    try:
        new = not os.path.exists(path)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        if new:
            _own(path)
    except OSError as e:  # not logging.*: this also runs inside the log handler
        print(f"viewer: can't write {path}: {e}", file=sys.stderr)
    if _keep_days and day != _last_prune_day and _schedule_prune(record_dir, _keep_days):
        _last_prune_day = day


def _read_last(kind: str, n: int) -> list[dict[str, Any]]:
    """The last n records of `kind`, oldest first, from the newest files backwards."""
    out: list[dict[str, Any]] = []
    for path in sorted(glob.glob(os.path.join(record_dir, f"{kind}-*.jsonl")), reverse=True):
        with open(path, encoding="utf-8") as f:
            lines = [json.loads(line) for line in f if line.strip()]
        out = lines[-(n - len(out)):] + out
        if len(out) >= n:
            break
    return out


def usage(resp: Any) -> dict[str, Any] | None:
    """Token usage of an OpenAI-style response or stream chunk, as a plain dict."""
    u = getattr(resp, "usage", None)
    return u.model_dump(exclude_none=True) if hasattr(u, "model_dump") else u


def publish(kind: str, data: Any) -> None:
    if not subscribers:
        return
    msg = f"event: {kind}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n".encode()
    for q in list(subscribers):
        try:
            q.put_nowait(msg)
        except asyncio.QueueFull:
            pass


class Turn:
    def __init__(self, **info: Any):
        self.id = next(_ids)
        self.at = time.time()
        self.t0 = time.monotonic()
        self.info = info
        self.status = "running"
        self.ended_ms: int | None = None
        self.stages: dict[str, dict[str, Any]] = {name: dict(status="pending", data={}) for name in STAGES}
        self.images: dict[str, tuple[bytes, str]] = {}  # name -> (bytes, type), only when not recording
        self.logs: list[dict[str, Any]] = []  # log lines written while this turn was current
        self.calls: list[dict[str, Any]] = []  # API calls, see call()
        self._image_names: dict[str, str] = {}

    def image(self, url: str) -> str:
        """Stores a data: URL's image (on disk when recording) and returns its img/<name> link."""
        if name := self._image_names.get(url):
            return name
        header, _, payload = url.partition(",")
        kind = header.removeprefix("data:").split(";")[0] or "image/png"
        try:
            raw = b64decode(payload)
        except ValueError:
            return "img/invalid"
        name = hashlib.sha256(raw).hexdigest()[:24] + (mimetypes.guess_extension(kind) or ".bin")
        if record_dir:
            path = os.path.join(record_dir, "images", name)
            if not os.path.exists(path):
                try:
                    with open(path, "wb") as f:
                        f.write(raw)
                    _own(path)
                except OSError as e:
                    print(f"viewer: can't write {path}: {e}", file=sys.stderr)
            else:
                try:
                    os.utime(path, None)  # keep mtime fresh so a concurrent prune won't delete a re-posted image
                except OSError:
                    pass
        else:
            self.images[name] = (raw, kind)
        self._image_names[url] = f"img/{name}"
        return f"img/{name}"

    def clean(self, value: Any) -> Any:
        """JSON-safe copy; data: URLs become img/<name> links, embedded base64 blobs are scrubbed."""
        if isinstance(value, str):
            return self.image(value) if value.startswith("data:") else _scrub(value)
        if isinstance(value, dict):
            return {k: self.clean(v) for k, v in value.items() if k != "lock"}
        if isinstance(value, (list, tuple)):
            return [self.clean(v) for v in value]
        if value is None or isinstance(value, (int, float, bool)):
            return value
        return _scrub(str(value))

    def stage(self, name: str, status: str, **data: Any) -> None:
        st = self.stages.setdefault(name, dict(status="pending", data={}))
        now = round((time.monotonic() - self.t0) * 1000)
        if st["status"] in ("pending", "skipped") and status != "skipped":
            st["t0"] = now
        if status in ("done", "error", "skipped"):
            st.setdefault("t0", now)
            st["t1"] = now
        if (step := data.get("step")) and (not st.get("steps") or st["steps"][-1][0] != step):
            st.setdefault("steps", []).append([step, now])  # when each sub-step (route, search...) started
        st["status"] = status
        st["data"] |= self.clean(data)
        publish("turn", self.json())

    def json(self) -> dict[str, Any]:
        return dict(id=self.id, at=self.at, status=self.status, info=self.info, stages=self.stages, calls=self.calls, logs=self.logs, ms=self.ended_ms if self.ended_ms is not None else round((time.monotonic() - self.t0) * 1000))


class Stored:
    """A turn reloaded from record_dir after a restart: only its JSON (images are on disk)."""

    images: dict[str, tuple[bytes, str]] = {}

    def __init__(self, record: dict[str, Any]):
        self.record, self.id, self.status = record, record["id"], record["status"]

    def json(self) -> dict[str, Any]:
        return self.record


def begin(**info: Any) -> Turn:
    turn = Turn(**info)
    turns.append(turn)
    current.set(turn)
    turn.stage("received", "done")
    publish("state", state())
    return turn


def stage(name: str, status: str, **data: Any) -> None:
    if turn := current.get():
        turn.stage(name, status, **data)


def call(stage: str, what: str, ms: float, **data: Any) -> None:
    """Records an API call in the current turn: stage, what (route, search, writer...), how long it took and, in data,
    model, usage (tokens), the raw reply or error..."""
    if turn := current.get():
        at = round((time.monotonic() - turn.t0) * 1000 - ms)
        turn.calls.append(dict(stage=stage, what=what, at=at, ms=round(ms)) | turn.clean(data))


@contextmanager
def timed(stage: str, what: str, **data: Any):
    """call() around a block; the block adds to the yielded dict (usage, reply...); an exception is recorded as error."""
    record, started = dict(data), time.monotonic()
    try:
        yield record
    except BaseException as e:  # CancelledError too: that's a timeout
        record["error"] = _scrub(type(e).__name__ + (f": {e}" if str(e) else ""), 2000)
        raise
    finally:
        call(stage, what, (time.monotonic() - started) * 1000, **record)


def fail(error: BaseException | None = None) -> None:
    """Marks the running stage (or the model stage) as failed."""
    if not (turn := current.get()):
        return
    running = [n for n, s in turn.stages.items() if s["status"] == "running"] or ["model"]
    err_text = _scrub("".join(traceback.format_exception(error)))[-3000:] if error else "failed"
    turn.stage(running[-1], "error", error=err_text)
    turn.status = "error"


def end(**info: Any) -> None:
    if not (turn := current.get()):
        return
    turn.info |= turn.clean(info)
    for name, st in turn.stages.items():
        if st["status"] == "running":
            turn.stage(name, "done")
        elif st["status"] == "pending":
            turn.stage(name, "skipped")
    if turn.status == "running":
        turn.status = "done" if info.get("replies") else "quiet"
    turn.ended_ms = round((time.monotonic() - turn.t0) * 1000)
    publish("turn", turn.json())
    _append("turns", turn.at, dict(v=1) | turn.json())
    current.set(None)
    publish("state", state())


def state() -> dict[str, Any]:
    try:
        extra = state_fn()
    except Exception as e:
        extra = dict(state_error=repr(e))
    return dict(started_at=started_at, now=time.time(), in_flight=sum(t.status == "running" for t in turns), turns_total=len(turns)) | extra


class LogHandler(logging.Handler):
    """Keeps log lines for the page; a line logged during a turn (same context) is also kept with that turn."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            line = dict(at=record.created, level=record.levelname, name=record.name, msg=_scrub(self.format(record).strip("\n"), 4000))
        except Exception:
            return
        if turn := current.get():
            line["turn"] = turn.id
            if len(turn.logs) < 50:
                turn.logs.append(line)
        logs.append(line)
        _append("log", record.created, line)
        publish("log", line)


# --- web ---

HTML_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "viewer.html")


async def index(request: web.Request) -> web.StreamResponse:
    return web.FileResponse(HTML_PATH, headers={"Cache-Control": "no-cache"})


async def snapshot(request: web.Request) -> web.Response:
    return web.json_response(dict(state=state(), turns=[t.json() for t in turns], logs=list(logs)), dumps=lambda d: json.dumps(d, ensure_ascii=False, default=str))


async def image(request: web.Request) -> web.StreamResponse:
    name = request.match_info["name"]
    for turn in turns:
        if name in turn.images:
            raw, kind = turn.images[name]
            return web.Response(body=raw, content_type=kind, headers={"Cache-Control": "max-age=86400"})
    if record_dir and IMAGE_NAME.match(name) and os.path.isfile(path := os.path.join(record_dir, "images", name)):
        return web.FileResponse(path, headers={"Cache-Control": "max-age=86400"})
    raise web.HTTPNotFound()


async def events(request: web.Request) -> web.StreamResponse:
    resp = web.StreamResponse(headers={"Content-Type": "text/event-stream", "Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
    await resp.prepare(request)
    q: asyncio.Queue = asyncio.Queue(maxsize=1000)
    subscribers.add(q)
    try:
        await resp.write(f"event: state\ndata: {json.dumps(state(), default=str)}\n\n".encode())
        while True:
            try:
                msg = await asyncio.wait_for(q.get(), 5)
            except asyncio.TimeoutError:
                msg = f"event: state\ndata: {json.dumps(state(), ensure_ascii=False, default=str)}\n\n".encode()
            await resp.write(msg)
    except (ConnectionResetError, asyncio.CancelledError):
        pass
    finally:
        subscribers.discard(q)
    return resp


async def serve(cfg: dict[str, Any] | None, get_state: Callable[[], dict[str, Any]]) -> None:
    global state_fn, record_dir, _ids, _keep_days, _last_prune_day
    if not cfg:
        return
    state_fn = get_state
    keep_days = cfg.get("keep_days")
    _keep_days = keep_days if isinstance(keep_days, int) and not isinstance(keep_days, bool) and keep_days > 0 else 0
    _last_prune_day = None
    if cfg.get("record_dir"):
        try:
            os.makedirs(os.path.join(cfg["record_dir"], "images"), exist_ok=True)
            record_dir = cfg["record_dir"]
            _own(os.path.join(record_dir, "images"))
            if _keep_days:
                _last_prune_day = time.strftime("%Y-%m-%d", time.gmtime())
                jsonl_n, img_n = await asyncio.to_thread(prune, record_dir, _keep_days)
                if jsonl_n or img_n:
                    logging.info(f"Viewer: pruned {jsonl_n} old log/turn file(s) and {img_n} unreferenced image(s)")
            turns.extend(Stored(t) for t in _read_last("turns", turns.maxlen))
            logs.extend(_read_last("log", logs.maxlen))
            _ids = itertools.count(max((t.id for t in turns), default=0) + 1)
        except (OSError, ValueError, KeyError) as e:
            logging.warning(f"Viewer: recording to {cfg['record_dir']!r} failed ({type(e).__name__}: {e})")
    handler = LogHandler()
    handler.setFormatter(logging.Formatter("%(message)s"))
    logging.getLogger().addHandler(handler)

    app = web.Application()
    app.router.add_get("/", index)
    app.router.add_get("/api/snapshot", snapshot)
    app.router.add_get("/events", events)
    app.router.add_get("/img/{name}", image)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    host, port = cfg.get("host") or "127.0.0.1", cfg.get("port", 8770)
    await web.TCPSite(runner, host, port).start()
    logging.info(f"Viewer on http://{host}:{port}" + (f", recording to {os.path.abspath(record_dir)}" if record_dir else ""))


# --- stats: python viewer.py stats [record_dir] [days] ---

def stats(directory: str = "data", days: int = 7) -> None:
    """Prints what the recorded turns of the last `days` days say about speed, tokens and what she did."""
    since = time.time() - days * 86400
    rows = []
    for path in sorted(glob.glob(os.path.join(directory, "turns-*.jsonl"))):
        with open(path, encoding="utf-8") as f:
            rows += [t for line in f if line.strip() and (t := json.loads(line))["at"] >= since]
    if not rows:
        print(f"No turns in {directory!r} from the last {days} days.")
        return

    def pct(values: list[float]) -> str:
        v = sorted(values)
        return f"{len(v):5}  p50 {v[len(v) // 2]:6.0f}  p90 {v[int(len(v) * .9)]:6.0f}  max {v[-1]:6.0f} ms" if v else "    0"

    def count(what: str, n: int) -> None:
        print(f"  {what:<26}{n:5}  ({n / len(rows):.0%})")

    print(f"{len(rows)} turns, {time.strftime('%Y-%m-%d %H:%M', time.localtime(rows[0]['at']))} .. {time.strftime('%Y-%m-%d %H:%M', time.localtime(rows[-1]['at']))}\n")
    print("What happened")
    for status in ("done", "quiet", "error"):
        count(f"status {status}", sum(t["status"] == status for t in rows))
    sat = [t["stages"].get("satellite", {}) for t in rows]
    count("searched", sum(bool(s.get("data", {}).get("query")) for s in sat))
    count("search gave a fact", sum(bool(s.get("data", {}).get("fact")) for s in sat))
    count("asked to draw", sum(bool(s.get("data", {}).get("draw")) for s in sat))
    count("drew a picture", sum(bool(t["stages"].get("draw", {}).get("data", {}).get("image")) for t in rows))
    count("panic", sum(bool(s.get("data", {}).get("panic")) for s in sat))
    for name in STAGES[1:]:
        if n := sum(t["stages"].get(name, {}).get("status") == "error" for t in rows):
            count(f"{name} errors", n)

    print("\nTime (ms)")
    print(f"  {'whole turn':<14}{pct([t['ms'] for t in rows])}")
    for name in STAGES[1:]:
        print(f"  {name:<14}{pct([s['t1'] - s['t0'] for t in rows if (s := t['stages'].get(name, {})).get('status') in ('done', 'error') and 't1' in s])}")

    print("\nAPI calls (ms; tokens in -> out)")
    calls: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for t in rows:
        for c in t.get("calls", []):
            calls.setdefault((c["stage"], c["what"], str(c.get("model", ""))), []).append(c)
        model = t["stages"].get("model", {})
        if (s := model.get("data", {})).get("usage") and "t1" in model:
            calls.setdefault(("model", "reply", str(s.get("model", ""))), []).append(dict(ms=model["t1"] - model["t0"], usage=s["usage"]))
    for (stage, what, model), cs in sorted(calls.items()):
        tin = sum((c.get("usage") or {}).get("prompt_tokens", 0) for c in cs)
        tout = sum((c.get("usage") or {}).get("completion_tokens", 0) for c in cs)
        cost = sum((c.get("usage") or {}).get("cost") or 0 for c in cs)
        errors = sum(bool(c.get("error")) for c in cs)
        print(f"  {stage + ' ' + what:<20}{model[:36]:<37}{pct([c['ms'] for c in cs])}" + (f"  {tin:,} -> {tout:,}" if tin or tout else "") + (f"  ${cost:.4f}" if cost else "") + (f"  {errors} failed" if errors else ""))


if __name__ == "__main__":
    if sys.argv[1:2] != ["stats"]:
        sys.exit("usage: python viewer.py stats [record_dir] [days]")
    stats(*sys.argv[2:3], *[int(d) for d in sys.argv[3:4]])
