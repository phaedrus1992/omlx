# The phaedrus1992 fork of oMLX

This is a fork of [jundot/omlx](https://github.com/jundot/omlx).
It is meant to be short-lived.
Each change in it is also sent upstream, and the fork shrinks as upstream accepts them.
This file explains why the fork exists and how to install it from nothing on a new Mac.

## Why the fork exists

The fork runs Claude Code against a local Qwen3.6-35B-A3B model that Splash serves.
Splash is an external inference engine that oMLX can start for a model.
Three problems made that setup slow or broken on upstream `main`.
Each one has a fix in this fork and a pull request upstream.

| Problem | Effect | Upstream issue | Upstream PR |
| --- | --- | --- | --- |
| oMLX folds Claude Code's mid-conversation system messages into the first system message | The prompt changes on every turn, so the prefix cache misses and the whole prompt is processed again | [#4697](https://github.com/jundot/omlx/issues/4697) | [#4708](https://github.com/jundot/omlx/pull/4708) |
| The Splash engine does not send `preserve_thinking` | Splash drops earlier reasoning from the prompt, and the cache misses after each user message | [#4701](https://github.com/jundot/omlx/issues/4701) | [#4709](https://github.com/jundot/omlx/pull/4709) |
| The Claude Code auto-mode classifier request gets thinking blocks it never asked for | The classifier is slow and its plain-text answer breaks | none | [#3193](https://github.com/jundot/omlx/pull/3193), closed by the stale bot for conflicts |

With the first two fixes, prefix-cache reuse on a 70K-token Claude Code session went from about 25–48% to 98–99.7% per turn.
The measured numbers are in the comments on #4697 and #4701.

The fork also carries cluster fixes and a refactor that are open upstream or were closed for conflicts.

| Change | Upstream PR |
| --- | --- |
| Show the real reason tensor-parallel planning fails | [#3197](https://github.com/jundot/omlx/pull/3197) |
| Keep a self-assigned Thunderbolt Bridge address as a link candidate | [#3205](https://github.com/jundot/omlx/pull/3205) |
| Share Anthropic content-block text extraction, with property tests | [#3194](https://github.com/jundot/omlx/pull/3194) |
| Send remote nodes an absolute model path | [#3196](https://github.com/jundot/omlx/pull/3196), closed |
| Stage safetensors shards from a subdirectory | [#3204](https://github.com/jundot/omlx/pull/3204), closed |
| Fall back to a TCP probe when the link ping fails | [#3206](https://github.com/jundot/omlx/pull/3206), closed |
| Retry flaky mDNS resolution in cluster SSH probes | [#3208](https://github.com/jundot/omlx/pull/3208), closed |

When upstream merges a change, the fork drops its own copy at the next sync.
When upstream merges all of them, delete the fork.

## Branches

- `main` is identical to `jundot/omlx` `main`.
  Never commit to it.
- `develop` is upstream `main` plus every change of the fork.
  Install and run this branch.
- A change meant for upstream starts from `main` on its own branch.
  Open the pull request from that branch.
  Merge the branch into `develop` when the work is done, even if upstream has not accepted it.

To sync:

```bash
git fetch upstream
git switch main && git merge --ff-only upstream/main
git push origin main
git switch develop && git merge main
```

## Install on a new Mac

### Requirements

- An Apple Silicon Mac.
  The Splash path needs enough memory for the model: about 40 GB for Qwen3.6-35B-A3B-4bit with a full 256K context.
  A 64 GB Mac works.
- macOS 26.4 or later for Splash M1.
- [Homebrew](https://brew.sh), `git`, and `uv`.
- About 25 GB of free disk for the model and its draft, plus room for the Splash SSD cache.

```bash
brew install uv python@3.14 git
```

### 1. Install Splash

Pick the build by chip.

| Chip | Build | Launcher | Install |
| --- | --- | --- | --- |
| M3 or later | Splash (official) | `splash` | `brew install incoai/tap/splash` |
| M1 or M2 | Splash M1 | `splash-m1` | `curl -fsSL https://github.com/paperniuk/splash/releases/latest/download/install-m1.sh \| sh` |

The Splash M1 installer checks the SHA-256 of the download.
It puts the build in `~/Library/Application Support/Splash-M1/` and writes the `splash-m1` command to `/opt/homebrew/bin` or `~/.local/bin`.
It does not touch a Homebrew `splash` on the same Mac.
Both builds can be installed at once.
oMLX finds a launcher on `PATH`, then in `/opt/homebrew/bin`, `/usr/local/bin` and `~/.local/bin`.
It prefers Splash M1 on an M1 or M2 Mac and the official build elsewhere.
To force one, set the launcher path under Settings → Integrations → External engines.

Check the install:

```bash
splash-m1 --version   # or: splash --version
```

Splash 1.3.0 or later is required.
The `preserve_thinking` and `later_system` handling in this fork need nothing newer.

### 2. Get the fork

```bash
mkdir -p ~/git && cd ~/git
git clone -b develop git@github.com:phaedrus1992/omlx.git omlx-service
cd omlx-service
git remote add upstream git@github.com:jundot/omlx.git
```

### 3. Create the Python environment

The fork runs on Python 3.14.
Upstream declares Python 3.11–3.13 and has not tested 3.14.
All dependencies install from prebuilt wheels on 3.14, and the server starts and serves requests.
Use `--python 3.13` instead if you want a version upstream supports.

```bash
uv venv --python 3.14 .venv-service
uv pip install --python .venv-service/bin/python -e ".[audio]"
```

The install takes about one minute.
The `audio` extra adds text-to-speech and speech-to-text.
Leave it out if you do not need audio.
Add `mcp` or `cluster` to the extras list for MCP support or cluster discovery.

If you installed `audio`, add the spaCy model it needs.
Check the SHA-256 of the wheel before you install it:

```bash
curl -sLo /private/tmp/en_core_web_sm-3.8.0-py3-none-any.whl \
  https://github.com/explosion/spacy-models/releases/download/en_core_web_sm-3.8.0/en_core_web_sm-3.8.0-py3-none-any.whl
echo "1932429db727d4bff3deed6b34cfc05df17794f4a52eeb26cf8928f7c1a0fb85  /private/tmp/en_core_web_sm-3.8.0-py3-none-any.whl" | shasum -a 256 -c -
uv pip install --python .venv-service/bin/python --no-deps /private/tmp/en_core_web_sm-3.8.0-py3-none-any.whl
```

### 4. Download the models

Splash serves Qwen3.8-27B and Qwen3.6-35B-A3B.
Each needs its DFlash2 draft model, which Splash uses for speculative decoding.
This guide uses Qwen3.6-35B-A3B.

```bash
mkdir -p ~/.omlx/models/mlx-community ~/.omlx/models/incoai
.venv-service/bin/hf download mlx-community/Qwen3.6-35B-A3B-4bit \
  --local-dir ~/.omlx/models/mlx-community/Qwen3.6-35B-A3B-4bit
.venv-service/bin/hf download incoai/Qwen3.6-35B-A3B-DFlash2 \
  --local-dir ~/.omlx/models/incoai/Qwen3.6-35B-A3B-DFlash2
```

For Qwen3.8-27B, use `mlx-community/Qwen3.8-27B-4bit` and `incoai/Qwen3.8-27B-DFlash2`.
The 27B model is dense and prefills much more slowly than the 35B-A3B model on an M1 Max.
oMLX can also download both from the admin dashboard.

### 5. Start the server once by hand

```bash
.venv-service/bin/omlx serve
```

Open `http://127.0.0.1:8000/admin` and set an API key.
Clients send the key as a bearer token, so keep it.
Configure the server as the next section describes.
Then stop it with Ctrl-C.

### 6. Configure

oMLX keeps its settings in `~/.omlx/settings.json` and per-model settings in `~/.omlx/model_settings.json`.
Use the dashboard to change them.
These are the values this fork was tuned with, on a 64 GB M1 Max.
Scale the memory numbers to your Mac.

| Setting | Value | Reason |
| --- | --- | --- |
| Memory → guard tier | `custom`, ceiling 48 GB | The preset tiers moved their ceiling between 9 and 35 GB while Splash was loading, and the model failed to load |
| Integrations → Splash memory limit | 40 GB | Splash planned a 40 GB budget for this model at load, inside the 48 GB guard |
| Integrations → Splash SSD cache | 200 GB | Splash keeps prefix-cache pages on disk between restarts |
| Integrations → KV cache precision | `int8` | The fast path of Splash on Apple Silicon |
| Scheduler → max concurrent requests | 2 | Splash does not batch a second prefill with decode, so more requests only slow each other down |
| Model → max context window | 262144 | The full 256K window of the model |

Then turn the model over to Splash.
Open the model's Model Settings → Advanced, turn on **Use external engine**, and pick Splash.
The switch stays off, with the reason shown, until the launcher, the draft model and the model are all in place.

The SSD cache lives under `~/.omlx/cache`.
To keep it on another drive, replace that folder with a symlink to the drive.
Do not let the system disk fill up.
When free space is low, Splash shrinks its cache to a small share of what is free, and the hit rate drops.

### 7. Run it under launchd

This starts the server at login and restarts it if it stops.
The shell expands `$HOME` when it writes the file.

```bash
mkdir -p ~/.omlx/logs
cat > ~/Library/LaunchAgents/local.omlx-fork.plist <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
	<key>Label</key>
	<string>local.omlx-fork</string>
	<key>ProgramArguments</key>
	<array>
		<string>$HOME/git/omlx-service/.venv-service/bin/python</string>
		<string>-c</string>
		<string>import sys; from omlx.cli import main; sys.exit(main())</string>
		<string>serve</string>
	</array>
	<key>EnvironmentVariables</key>
	<dict>
		<key>OMLX_SUPERVISED</key>
		<string>launchd</string>
		<key>PATH</key>
		<string>/opt/homebrew/bin:/opt/homebrew/sbin:/usr/bin:/bin:/usr/sbin:/sbin</string>
	</dict>
	<key>WorkingDirectory</key>
	<string>$HOME/.omlx</string>
	<key>KeepAlive</key>
	<true/>
	<key>RunAtLoad</key>
	<true/>
	<key>StandardOutPath</key>
	<string>$HOME/.omlx/logs/omlx-fork.log</string>
	<key>StandardErrorPath</key>
	<string>$HOME/.omlx/logs/omlx-fork.log</string>
</dict>
</plist>
EOF
plutil -lint ~/Library/LaunchAgents/local.omlx-fork.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/local.omlx-fork.plist
```

`OMLX_SUPERVISED=launchd` tells oMLX that a supervisor restarts it, so the dashboard's restart button works.
If you also installed oMLX with Homebrew, stop its service first with `brew services stop omlx`.
Both would use port 8000.

| Task | Command |
| --- | --- |
| Restart | `launchctl kickstart -k gui/$(id -u)/local.omlx-fork` |
| Stop and unload | `launchctl bootout gui/$(id -u)/local.omlx-fork` |
| Read the log | `tail -f ~/.omlx/logs/omlx-fork.log` |

If `bootstrap` fails right after a `bootout`, the old process was still stopping.
Run `bootstrap` again.

### 8. Check that it works

```bash
curl -s localhost:8000/health
```

The answer has `"status":"healthy"`.
Send one chat request so oMLX loads the model.
The log then shows `Serving ... through Splash` and `Loaded model`.
The Splash log is `~/.omlx/logs/splash/omlx--<model>-<hash>.log`.
It must contain a line that says the chat template was patched, or is native, to render later system messages in place.

## Use it with Claude Code

Point Claude Code at the server with the oMLX launcher, or set `ANTHROPIC_BASE_URL` and `ANTHROPIC_AUTH_TOKEN` yourself.
In the dashboard, set Claude Code mode to local and pick the model for the opus, sonnet and haiku slots.
Using one model for all three slots keeps one model in memory.
A second model would evict the first, and a reload takes about a minute.
Claude Code runs subagents on the haiku slot, so they use the same model.

### The auto-mode classifier

Claude Code's auto mode sends each tool call to a classifier model before it runs.
A classifier request that thinks first is slow, and Claude Code cancels it after about a minute.
The fork handles this in two ways.

1. oMLX recognizes the classifier request by its system prompt and turns thinking off for it ([#3193](https://github.com/jundot/omlx/pull/3193)).
   An admin toggle can also route classifier requests to a chosen model tier.
2. Claude Code 2.1.303 and later reads `CLAUDE_CODE_AUTO_MODE_MODEL` and sends the classifier to that model name.
   Point it at a model profile with thinking off.

For the second way, add a profile with thinking off.
The profile shares the model's engine, so it needs no extra memory.
oMLX lists it as `Qwen3.6-35B-A3B-4bit:classifier`.
The profile lives in `~/.omlx/model_profiles.json`:

```json
{
  "version": 1,
  "profiles": {
    "Qwen3.6-35B-A3B-4bit": {
      "classifier": {
        "name": "classifier",
        "display_name": "Classifier (no thinking)",
        "api_name": "classifier",
        "description": "Claude Code auto-mode classifier: same engine, thinking off.",
        "settings": { "enable_thinking": false },
        "expose_as_model": true
      }
    }
  }
}
```

Restart the server after you edit the file.
Then start Claude Code with:

```bash
export CLAUDE_CODE_AUTO_MODE_MODEL="Qwen3.6-35B-A3B-4bit:classifier"
```

A classifier that the server has configured on Anthropic's side overrides this variable.
`CLAUDE_CODE_AUTO_MODE_SERVER` only turns on Anthropic's own classifier, which does not help here.

### Check the cache

Every request that Splash serves has a line in its log:

```bash
rg "Started" ~/.omlx/logs/splash/omlx--Qwen3.6-35B-A3B-4bit-*.log | tail
```

A healthy session shows `cached` close to `input` after the first turn:
`Started · input 72,932 · cached 71,968`.
The first request after a restart is cold.
A compaction in Claude Code also causes one cold turn.
If `cached` stays low for no reason, send the log with an issue.

## What to expect

These numbers are from an M1 Max with 64 GB, Splash M1 1.3.2 and Qwen3.6-35B-A3B-4bit.

- Prefill runs at about 160 tokens per second.
  A cold 70K-token prompt takes about 7 minutes.
  Prefill is most of the wait in an agent session, so the cache matters more than decode speed.
- Decode runs at about 75 tokens per second on a short chat.
  It falls toward 25 tokens per second at a long context, or while a second request prefills.
- Qwen3.8-27B decodes at about 21 tokens per second, and a 92K-token prefill takes more than 20 minutes.
  Use the 35B-A3B model on an M1 or M2 Mac.
- One Qwen model of 24–27 GB fits in memory at a time on a 64 GB Mac.
  Loading a second one evicts the first, and a reload takes about a minute.

## Troubleshooting

### The cache hit rate is low

1. Look at the `Started` lines in the Splash log, as shown above.
2. Check that the Splash log has the chat-template line.
   Without it, oMLX folds later system messages again.
3. Check free disk space.
   Splash limits its SSD cache to a quarter of the free space beyond 10 GB.
   At 15 GB free, the cache dropped from 32.9 GB to 1.4 GB, and the hit rate fell with it.
4. Check memory pressure.
   When the Mac is short of memory, Splash stops growing its KV pool and drops saved restore points between turns.
   Close other large apps, or lower the Splash memory limit so the model stays inside the guard.

### Claude Code cancels long requests

Claude Code gives up on a stream that stays silent for too long.
The default limit is 300000 ms.
A cold prefill of a large prompt takes longer than that, so Claude Code cancels the request and retries it later, and the retry starts the prefill again.
Set `CLAUDE_STREAM_IDLE_TIMEOUT_MS` (and `CLAUDE_BYTE_STREAM_IDLE_TIMEOUT_MS`) to a larger value before you start Claude Code.
The classifier is a different case.
It was cancelled after about 60 seconds because it thought for 600–900 tokens at 10–14 tokens per second.
Turn its thinking off, as described above.

### The server did not start

- Read `~/.omlx/logs/omlx-fork.log` for the error.
- If port 8000 is busy, the Homebrew service may still run.
  Stop it with `brew services stop omlx`.
- The log shows `Web UI package omlx_web is not installed` when the editable install is missing.
  Run the install command of step 3 again.

## Update

```bash
cd ~/git/omlx-service
git pull --ff-only origin develop
launchctl kickstart -k gui/$(id -u)/local.omlx-fork
```

When `pyproject.toml` changes dependencies, run the `uv pip install` command of step 3 again before the restart.

To update Splash M1, run the `install-m1.sh` command of step 1 again.
It keeps the older version on disk and moves the `current` link.

## Go back to the release

```bash
launchctl bootout gui/$(id -u)/local.omlx-fork
rm ~/Library/LaunchAgents/local.omlx-fork.plist
brew services start omlx
```

Settings, models and caches live in `~/.omlx`, so the release version uses them unchanged.

## Known problems

- On an M1 Max, six `test_qwen4_fused_verify_equals_per_op_verify_and_rollback` tests fail on upstream `main` too.
- `tests/test_deepseek_v41.py::test_tensor_parallel_ranks_match_single_process` can fail with `[ring] Socket 4 was closed by the peer` under `pytest -n 3`.
  It passes when run alone.
- Splash returns HTTP 400 for a `preserve_thinking` value that is not a boolean.
  oMLX logs a warning and drops such a value.
