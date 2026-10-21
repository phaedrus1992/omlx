# SPDX-License-Identifier: Apache-2.0
"""Replay a synthetic Claude Code session against oMLX and measure prefix reuse.

Each run starts one oMLX server on a fresh base path, so the Splash prefix
cache starts empty. The script then sends a growing agent conversation to
/v1/messages, turn by turn, the way Claude Code does:

- the history only grows, and the client replays the model's own output
  (thinking, text and tool calls) the way Claude Code does, because Splash
  resumes from the state after the previous generation,
- earlier assistant turns carry thinking blocks,
- a system message follows the tool result on every third turn (hook and rule
  reminders), which makes it the newest message of that request,
- the person sends a new prompt part-way through the session.

Tool results and reminders are generated from a seed, and sampling is greedy.
Per-turn input and cached token counts come from the Splash log.

Usage:
    bench_splash_session.py --arm stock=/opt/homebrew/opt/omlx/bin/omlx \
        --arm fork=/path/to/venv/bin/omlx --turns 10 --repeat 2 --out results.json
"""

from __future__ import annotations

import argparse
import json
import random
import re
import shutil
import socket
import statistics
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx

BASE_SETTINGS = Path.home() / ".omlx" / "settings.json"
BASE_MODEL_SETTINGS = Path.home() / ".omlx" / "model_settings.json"
STARTED = re.compile(r"^(\d\d:\d\d:\d\d) Started · input ([\d,]+) · cached ([\d,]+)")
DONE = re.compile(
    r"^(\d\d:\d\d:\d\d) Done · input ([\d,]+) · cached ([\d,]+).*?output ([\d,]+)"
    r".*?TTFT ([\d.]+)s · ([\d.]+) tok/s"
)
CHARS_PER_TOKEN = 3.6
EXTRA_TEMPLATE_KWARGS: dict = {}


@dataclass
class Turn:
    index: int
    client_ttft: float
    client_total: float
    retries: int = 0
    input_tokens: int = 0
    cached_tokens: int = 0
    splash_ttft: float = 0.0
    decode_tps: float = 0.0


@dataclass
class Run:
    arm: str
    repeat: int
    turns: list[Turn] = field(default_factory=list)


# -- synthetic session ---------------------------------------------------------


def _words(rng: random.Random, tokens: int) -> str:
    names = ["handle", "parse", "load", "merge", "route", "queue", "cache", "emit"]
    lines = []
    size = 0
    while size < tokens * CHARS_PER_TOKEN:
        line = (
            f"    {rng.choice(names)}_{rng.randrange(10**4)}"
            f" = {rng.choice(names)}({rng.randrange(10**6)}, key='{rng.randrange(10**8)}')"
        )
        lines.append(line)
        size += len(line) + 1
    return "\n".join(lines)


def build_tools() -> list[dict]:
    return [
        {
            "name": f"Tool{i}",
            "description": f"Tool number {i}. "
            + "Reads, writes or searches files. " * 12,
            "input_schema": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "A file path."},
                    "pattern": {"type": "string", "description": "A search pattern."},
                },
                "required": ["path"],
            },
        }
        for i in range(14)
    ]


def build_system(rng: random.Random, tokens: int) -> list[dict]:
    return [
        {"type": "text", "text": "You are a coding agent working in a repository."},
        {"type": "text", "text": _words(rng, tokens)},
    ]


def tool_result(rng: random.Random, call_id: str, tokens: int) -> dict:
    return {
        "type": "tool_result",
        "tool_use_id": call_id,
        "content": _words(rng, tokens),
    }


def follow_up(rng: random.Random, turn: int, content: list[dict], args) -> list[dict]:
    """The client's side of one turn: keep the model's output, answer its tool calls."""
    calls = [b for b in content if b["type"] == "tool_use"]
    if not content:
        content = [{"type": "text", "text": "(no output)"}]
    user = [tool_result(rng, c["id"], args.result_tokens) for c in calls]
    if not calls:
        user.append({"type": "text", "text": "Continue reading the next file."})
    messages = [
        {"role": "assistant", "content": content},
        {"role": "user", "content": user},
    ]
    if turn == args.followup_after:
        # A real prompt from the person, as its own message. Qwen templates treat
        # it as a new query and drop the reasoning of every earlier assistant
        # turn unless preserve_thinking is on.
        text = "Also check how the tests use these files."
        messages.append({"role": "user", "content": text})
    if args.reminder_every and turn % args.reminder_every == args.reminder_every - 1:
        messages.append(reminder(rng, turn))
    return messages


def reminder(rng: random.Random, turn: int) -> dict:
    text = f"<system-reminder>Step {turn}. {_words(rng, 40)}</system-reminder>"
    return {"role": "system", "content": text}


# -- server and requests -------------------------------------------------------


def prepare_base_path(path: Path, state_format: str | None, decode_share: float | None):
    """A clean oMLX base path with the live settings and no caches."""
    shutil.rmtree(path, ignore_errors=True)
    path.mkdir(parents=True)
    settings = json.loads(BASE_SETTINGS.read_text())
    settings["server"]["port"] = 8100
    integrations = settings["integrations"]
    integrations.pop("splash_state_format", None)
    integrations.pop("splash_decode_share", None)
    if state_format:
        integrations["splash_state_format"] = state_format
    if decode_share is not None:
        integrations["splash_decode_share"] = decode_share
    (path / "settings.json").write_text(json.dumps(settings, indent=2))
    models = json.loads(BASE_MODEL_SETTINGS.read_text())
    for entry in models["models"].values():
        entry["is_pinned"] = False
    (path / "model_settings.json").write_text(json.dumps(models, indent=2))
    return settings["auth"]["api_key"]


def start_server(executable: str, base_path: Path, port: int) -> subprocess.Popen:
    if (
        httpx.get(f"http://127.0.0.1:{port}/health", timeout=3).is_success
        if _port_open(port)
        else False
    ):
        raise RuntimeError(f"port {port} is already served; stop that server first")
    log = open(base_path / "server.out", "w")  # noqa: SIM115 - the child owns it
    if executable.endswith("python"):
        command = [
            executable,
            "-c",
            "import sys; from omlx.cli import main; sys.exit(main())",
        ]
    else:
        command = [executable]
    command += ["serve", "--base-path", str(base_path), "--port", str(port)]
    # `python -c` puts the cwd first on sys.path; a repo checkout there shadows the arm.
    return subprocess.Popen(
        command, stdout=log, stderr=log, start_new_session=True, cwd=base_path
    )


def _port_open(port: int) -> bool:
    with socket.socket() as probe:
        probe.settimeout(1)
        return probe.connect_ex(("127.0.0.1", port)) == 0


def wait_healthy(port: int, process: subprocess.Popen, timeout: float = 300) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if process.poll() is not None:
            raise RuntimeError("oMLX exited before it became healthy")
        try:
            health = httpx.get(f"http://127.0.0.1:{port}/health", timeout=3).json()
            if health.get("status") == "healthy":
                return
        except httpx.HTTPError:
            pass
        time.sleep(2)
    raise TimeoutError("oMLX did not become healthy")


class _Blocks:
    """Rebuilds the assistant message from a streamed Messages response."""

    def __init__(self) -> None:
        self.blocks: dict[int, dict] = {}
        self.json_parts: dict[int, str] = {}

    def feed(self, event: dict) -> None:
        index = event.get("index", 0)
        if event.get("type") == "content_block_start":
            block = dict(event["content_block"])
            if block["type"] == "thinking":
                block.setdefault("thinking", "")
                block.setdefault("signature", "sig")
            elif block["type"] == "text":
                block.setdefault("text", "")
            self.blocks[index] = block
        elif event.get("type") == "content_block_delta":
            delta, block = event["delta"], self.blocks.get(index)
            if block is None:
                return
            if delta["type"] == "thinking_delta":
                block["thinking"] += delta["thinking"]
            elif delta["type"] == "signature_delta":
                block["signature"] = delta["signature"]
            elif delta["type"] == "text_delta":
                block["text"] += delta["text"]
            elif delta["type"] == "input_json_delta":
                self.json_parts[index] = (
                    self.json_parts.get(index, "") + delta["partial_json"]
                )

    def content(self) -> list[dict]:
        content = []
        for index in sorted(self.blocks):
            block = self.blocks[index]
            if block["type"] == "tool_use":
                try:
                    block["input"] = json.loads(self.json_parts.get(index) or "{}")
                except json.JSONDecodeError:
                    continue
            if block["type"] == "text" and not block["text"]:
                continue
            content.append(block)
        return content


def send_turn(client: httpx.Client, model: str, system, tools, messages, max_tokens):
    """Send one turn; return (TTFT, total seconds, the assistant's content)."""
    body = {
        "model": model,
        "max_tokens": max_tokens,
        "temperature": 0,
        "stream": True,
        "system": system,
        "tools": tools,
        "thinking": {"type": "adaptive"},
        "messages": messages,
    }
    if EXTRA_TEMPLATE_KWARGS:
        body["chat_template_kwargs"] = EXTRA_TEMPLATE_KWARGS
    start = time.perf_counter()
    first = None
    blocks = _Blocks()
    with client.stream("POST", "/v1/messages", json=body) as response:
        response.raise_for_status()
        for line in response.iter_lines():
            if not line.startswith("data:"):
                continue
            event = json.loads(line[5:])
            if first is None and event.get("type") == "content_block_delta":
                first = time.perf_counter() - start
            if event.get("type") == "error":
                raise RuntimeError(f"oMLX returned an error event: {event}")
            blocks.feed(event)
    total = time.perf_counter() - start
    return (first if first is not None else total), total, blocks.content()


def splash_requests(base_path: Path) -> list[dict]:
    """Completed Splash requests in order; a Started line with no Done failed."""
    logs = list((base_path / "logs" / "splash").glob("*.log"))
    if not logs:
        raise RuntimeError(f"no Splash log under {base_path}/logs/splash")
    requests, pending = [], None
    for line in logs[0].read_text().splitlines():
        if match := STARTED.match(line):
            pending = (int(match[2].replace(",", "")), int(match[3].replace(",", "")))
        elif (match := DONE.match(line)) and pending:
            requests.append(
                {
                    "input": pending[0],
                    "cached": pending[1],
                    "ttft": float(match[5]),
                    "tps": float(match[6]),
                }
            )
            pending = None
    return requests


def run_session(arm: str, executable: str, repeat: int, args) -> Run:
    base_path = Path(args.workdir) / f"{arm}-{repeat}"
    settings = args.arm_settings.get(arm, (None, None))
    api_key = prepare_base_path(base_path, *settings)
    process = start_server(executable, base_path, 8100)
    run = Run(arm, repeat)
    try:
        wait_healthy(8100, process)
        headers = {
            "Authorization": f"Bearer {api_key}",
            "anthropic-version": "2023-06-01",
        }
        timeout = httpx.Timeout(3600, connect=10)
        with httpx.Client(
            base_url="http://127.0.0.1:8100", headers=headers, timeout=timeout
        ) as client:
            rng = random.Random(args.seed)
            system = build_system(rng, args.system_tokens)
            tools = build_tools()
            task = (
                f"Read src/module_1.py through src/module_{args.turns}.py one file "
                "at a time with Tool0, then summarize what you found."
            )
            messages: list[dict] = [{"role": "user", "content": task}]
            for turn in range(1, args.turns + 1):
                if args.dump_requests:
                    (base_path / f"request-{turn}.json").write_text(
                        json.dumps(
                            {"system": system, "tools": tools, "messages": messages}
                        )
                    )
                retries = 0
                while True:
                    try:
                        ttft, total, content = send_turn(
                            client, args.model, system, tools, messages, args.max_tokens
                        )
                        break
                    except (RuntimeError, httpx.HTTPError) as error:
                        # macOS can abort a long GPU command; Splash then
                        # restarts its engine, which takes a while.
                        retries += 1
                        if retries > 3:
                            raise
                        print(
                            f"  turn {turn} failed ({error}); retry {retries}",
                            flush=True,
                        )
                        time.sleep(60)
                run.turns.append(Turn(turn, ttft, total, retries))
                print(
                    f"  {arm} #{repeat} turn {turn}: TTFT {ttft:.1f}s, total {total:.1f}s",
                    flush=True,
                )
                messages += follow_up(rng, turn, content, args)
                time.sleep(args.think_time)
    finally:
        process.terminate()
        try:
            process.wait(timeout=60)
        except subprocess.TimeoutExpired:
            process.kill()
    # oMLX sends a few warm-up requests when it loads the model; the session's
    # own requests are the last ones in the log.
    seen_requests = splash_requests(base_path)[-len(run.turns) :]
    for turn, seen in zip(run.turns, seen_requests, strict=True):
        turn.input_tokens, turn.cached_tokens = seen["input"], seen["cached"]
        turn.splash_ttft, turn.decode_tps = seen["ttft"], seen["tps"]
    time.sleep(10)
    return run


# -- report --------------------------------------------------------------------


def summarize(runs: list[Run]) -> str:
    rows = [
        "| arm | turns 2+ cache hit | prefilled tokens (total) | median TTFT | p90 TTFT | session time |",
        "|---|---|---|---|---|---|",
    ]
    for arm in dict.fromkeys(run.arm for run in runs):
        later = [t for r in runs if r.arm == arm for t in r.turns if t.index > 1]
        sessions = [sum(t.client_total for t in r.turns) for r in runs if r.arm == arm]
        inputs = sum(t.input_tokens for t in later) or 1
        cached = sum(t.cached_tokens for t in later)
        ttfts = sorted(t.client_ttft for t in later)
        prefilled = sum(t.input_tokens - t.cached_tokens for t in later)
        rows.append(
            f"| {arm} | {100 * cached / inputs:.1f}% | {prefilled:,} "
            f"| {statistics.median(ttfts):.1f}s | {ttfts[int(0.9 * len(ttfts)) - 1]:.1f}s "
            f"| {statistics.median(sessions) / 60:.1f} min |"
        )
    return "\n".join(rows)


def parse_args(argv):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--arm", action="append", required=True, metavar="NAME=EXECUTABLE"
    )
    parser.add_argument(
        "--arm-setting",
        action="append",
        default=[],
        metavar="NAME=STATE_FORMAT,DECODE_SHARE",
    )
    parser.add_argument("--model", default="Qwen3.6-35B-A3B-4bit")
    parser.add_argument("--turns", type=int, default=9)
    parser.add_argument("--repeat", type=int, default=2)
    parser.add_argument("--system-tokens", type=int, default=3000)
    parser.add_argument("--result-tokens", type=int, default=3500)
    parser.add_argument("--max-tokens", type=int, default=400)
    parser.add_argument(
        "--preserve-thinking",
        action="store_true",
        help="send chat_template_kwargs.preserve_thinking=true with every request",
    )
    parser.add_argument(
        "--reminder-every",
        type=int,
        default=3,
        help="a system message follows the tool result on every Nth turn (0: never)",
    )
    parser.add_argument(
        "--followup-after",
        type=int,
        default=5,
        help="the person sends a new prompt with this turn's tool result (0: never)",
    )
    parser.add_argument(
        "--think-time",
        type=float,
        default=5.0,
        help="seconds between a reply and the next request, like a tool running",
    )
    parser.add_argument(
        "--dump-requests", action="store_true", help="save each request body"
    )
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--workdir", default="/private/tmp/omlx-bench")
    parser.add_argument("--out", default="bench-results.json")
    args = parser.parse_args(argv)
    args.arm_settings = {}
    for item in args.arm_setting:
        name, _, value = item.partition("=")
        state, _, share = value.partition(",")
        args.arm_settings[name] = (state or None, float(share) if share else None)
    return args


def main(argv=None) -> int:
    args = parse_args(argv)
    if args.preserve_thinking:
        EXTRA_TEMPLATE_KWARGS["preserve_thinking"] = True
    runs = []
    for repeat in range(1, args.repeat + 1):
        for item in args.arm:
            name, _, executable = item.partition("=")
            print(f"== {name} run {repeat}", flush=True)
            runs.append(run_session(name, executable, repeat, args))
            Path(args.out).write_text(
                json.dumps(
                    [
                        r.__dict__ | {"turns": [t.__dict__ for t in r.turns]}
                        for r in runs
                    ],
                    indent=2,
                )
            )
    print(summarize(runs))
    return 0


if __name__ == "__main__":
    sys.exit(main())
