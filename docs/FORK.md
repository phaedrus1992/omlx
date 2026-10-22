# The phaedrus1992 fork of oMLX

This is a fork of [jundot/omlx](https://github.com/jundot/omlx).
It is meant to be short-lived.
Each change in it is also sent upstream, and the fork shrinks as upstream accepts them.
This file explains why the fork exists and how to install it from nothing on a new Mac.

## Quick start

This gets an M1 or M2 Max with 64 GB to the setup the author runs.
Each step links to a longer section.
Scale the memory numbers if your Mac has less memory.

1. Install the tools: `brew install uv python@3.14 git`.
2. Install Splash M1 ([step 1](#1-install-splash)).
   On an M3 or later Mac, install the official Splash instead.
3. Get the fork and create the Python environment ([steps 2 and 3](#2-get-the-fork)).
4. Download the model and its draft model ([step 4](#4-download-the-models)).
   The author runs `mlx-community/KAT-Coder-V2.5-Dev-OptiQ-4bit`, a coding fine-tune of Qwen3.6-35B-A3B.
   It uses the same draft model, `incoai/Qwen3.6-35B-A3B-DFlash2`, and the same Splash settings.
   Download it the same way as the base model:
   `.venv-service/bin/hf download mlx-community/KAT-Coder-V2.5-Dev-OptiQ-4bit --local-dir ~/.omlx/models/mlx-community/KAT-Coder-V2.5-Dev-OptiQ-4bit`.
5. Start the server once, set an API key in the dashboard, and stop the server ([step 5](#5-start-the-server-once-by-hand)).
6. Merge these values into `~/.omlx/settings.json`:

   ```json
   {
     "memory": { "memory_guard_tier": "custom", "memory_guard_custom_ceiling_gb": 48.0 },
     "scheduler": { "max_concurrent_requests": 2 },
     "integrations": {
       "splash_kv_format": "int8",
       "splash_max_memory_gb": 40.0,
       "splash_ssd_cache_gb": 200,
       "splash_state_format": "fp32",
       "splash_decode_share": 0.15
     }
   }
   ```

7. Add this entry under `models` in `~/.omlx/model_settings.json`:

   ```json
   "KAT-Coder-V2.5-Dev-OptiQ-4bit": {
     "external_engine": "splash",
     "max_context_window": 262144,
     "temperature": 0.6,
     "top_p": 0.95,
     "top_k": 20,
     "force_sampling": true,
     "thinking_budget_enabled": true,
     "thinking_budget_tokens": 1024
   }
   ```

8. Run the server under launchd ([step 7](#7-run-it-under-launchd)).
9. Point Claude Code at it ([Use it with Claude Code](#use-it-with-claude-code)).
   Use the one model for the opus, sonnet and haiku slots.
   Add the classifier profile for that model (the profile in the next section, under the name `KAT-Coder-V2.5-Dev-OptiQ-4bit`), and export `CLAUDE_CODE_AUTO_MODE_MODEL` as `KAT-Coder-V2.5-Dev-OptiQ-4bit:classifier`.
10. Check the cache ([Check the cache](#check-the-cache)).

Three values in these files matter more than the rest.

- `force_sampling` with temperature 0.6 stops the model from printing commands instead of calling tools.
  See [The model prints a command instead of running it](#the-model-prints-a-command-instead-of-running-it).
- `splash_state_format` and `splash_kv_format` stay at the defaults above.
  We found no gain from the alternatives.
- `splash_decode_share` of 0.15 is an experiment for several sessions at once.
  The default is 0.5.
  See [Several sessions at once](#several-sessions-at-once).

The evidence behind these choices is in [`splash-moe-investigation.md`](splash-moe-investigation.md).

## Which model to use

Use `KAT-Coder-V2.5-Dev-OptiQ-4bit`.
It is a coding fine-tune of Qwen3.6-35B-A3B, so it runs on Splash with the same draft model and the same memory use (22 GB).
Splash is the only engine here that keeps the prefix cache across turns, and prefill is most of the wait in an agent session.

The choice rests on three measured facts. One fact is missing.

- KAT-Coder called the tool on 17 of 17 runs of the bake-off prompt, the same as the base model.
- Its replies are short: a median of 106 tokens, against 264–286 for the base model.
  A shorter reply shortens a turn as much as faster decode does.
- Its cold prefill (169 s) and warm time to first token (1.26 s) match the base model.
- Answer quality is the missing fact, and it is not measured.
  The published SWE-bench Verified score is 69.4 for KAT-Coder and 64.4 for the base model, from the vendor's own harness.
  We did not verify it, and our benchmark does not test answer quality.

Decode speed does not separate the models.
The same base model gave 18.6 and 38.0 tokens per second on one prompt, and heat is the likely cause.
A gap smaller than 2 times between two models is within that swing.

### Models that were rejected

| Model | Result | Reason for rejection |
| --- | --- | --- |
| Qwen3.6-35B-A3B (base), Splash | Works, 17 of 17 tool calls | Not wrong. It is the fallback. Its replies are 2 to 3 times longer, and KAT-Coder is the same size and speed. |
| Qwen3.8-27B (dense), Splash | 21 tokens per second decode, 92K-token prefill over 20 minutes | Too slow at prefill for 50K-token agent sessions. |
| `DavidAU` Qwen3.8-27B NEO-CODER GGUF fine-tune, Splash | 2.1–2.7 tokens per second, and macOS aborted a GPU command after 12 minutes of prefill | About 8 times slower than stock Qwen3.8-27B. The draft model almost never matches this fine-tune, and Splash M1 did not test GGUF end to end. |
| Nemotron-3.5-Lightning-30B-A3B 4-bit, oMLX MLX engine | 17 of 17 tool calls, 271 s cold prefill | It cannot use Splash, so it has no Splash cache work, and the fork changes do not touch that path. Prefill was about 115 tokens per second, against about 180 for Splash. |
| Qwen3.8-Flash-Next Q2_0 with MTP, `ds4` | 17 of 17 tool calls, fastest prefill (about 250 tokens per second) | It needs 43 GiB for a 131K context, so nothing else large fits. It is a 2-bit quant with unmeasured quality, and it is not served through oMLX. |
| MiniCPM5-2B and Bonsai 2-bit | MiniCPM5-2B: 96 tokens per second on a short chat. Bonsai: 11 tokens per second. | Too small for the opus and sonnet slots, and a second resident model evicts the Qwen model (a reload takes about a minute). We deleted both. |

We have not tested GLM-4.7-Flash, Qwen3-Coder-30B-A3B or gpt-oss-20b.
They fit in 48 GB, but they run in the MLX engine without the Splash cache.
The plan to test them is in [`splash-moe-investigation.md`](splash-moe-investigation.md).
Qwen3.8 has no MoE model below 100B parameters, and gpt-oss-120b, Qwen3-Coder-Next 80B and Nemotron 3 Super do not fit.

### A Mac with 36 GB or less

KAT-Coder is not bigger than the base model.
Both use 22 GB, so the base model is not a smaller choice.
Splash runs only Qwen3.8-27B and Qwen3.6-35B-A3B and their fine-tunes, so Splash has no model below this size.
We did not test any model on a Mac with 36 GB.

- When the memory limit is too low for a full context window, Splash fits a shorter context, and oMLX reports that shorter limit.
  Below the memory for the weights and one request, Splash cannot start.
  See [`Splash.md`](Splash.md).
  Set the memory limit by hand, as in the quick start, and check `/v1/models` for the context that results.
- The only measured model below 22 GB is Nemotron-3.5-Lightning-30B-A3B 4-bit on the oMLX MLX engine.
  It uses 17 GB and called the tool on 17 of 17 runs.
  It has no Splash prefix cache, and its cold prefill was about 115 tokens per second.
  A cold 31K-token prompt took 271 s.
  Expect slow turns after each cache miss.
- GLM-4.7-Flash (about 16 GB) and Qwen3-Coder-30B-A3B (about 17 GB) are the next candidates.
  They also run on the MLX engine without the Splash cache.
  We did not run them. Their sizes come from model listings, not from our tests.

On this class of Mac, try KAT-Coder first with a short context, and move to Nemotron only if Splash fails to load.

## Why the fork exists

The fork runs Claude Code against a local Qwen3.6-35B-A3B model that Splash serves.
Splash is an external inference engine that oMLX can start for a model.
Three problems made that setup slow or broken on upstream `main`.
Each one has a fix in this fork and a pull request upstream.

| Problem | Effect | Upstream issue | Upstream PR |
| --- | --- | --- | --- |
| oMLX folds Claude Code's mid-conversation system messages into the first system message | The prompt changes on every turn, so the prefix cache misses and the whole prompt is processed again | [#4697](https://github.com/jundot/omlx/issues/4697) | [#4708](https://github.com/jundot/omlx/pull/4708) |
| The Splash engine does not send `preserve_thinking` | Splash drops earlier reasoning from the prompt, and the cache misses after each user message | [#4701](https://github.com/jundot/omlx/issues/4701) | [#4709](https://github.com/jundot/omlx/pull/4709) |
| The Claude Code auto-mode classifier request gets thinking blocks it never asked for | The classifier is slow and its plain-text answer breaks | none | [#4714](https://github.com/jundot/omlx/pull/4714) (replaces #3193, which the stale bot closed for conflicts) |

With the first two fixes, prefix-cache reuse on a 70K-token Claude Code session went from about 25–48% to 98–99.7% per turn.
The measured numbers are in the comments on #4697 and #4701.

The fork also carries cluster fixes and a refactor that are open upstream or wait for a new pull request.

| Change | Upstream PR |
| --- | --- |
| Show the real reason tensor-parallel planning fails | [#3197](https://github.com/jundot/omlx/pull/3197) |
| Keep a self-assigned Thunderbolt Bridge address as a link candidate | [#3205](https://github.com/jundot/omlx/pull/3205) |
| Share Anthropic content-block text extraction, with property tests | [#3194](https://github.com/jundot/omlx/pull/3194) |
| Send remote nodes an absolute model path | [#3196](https://github.com/jundot/omlx/pull/3196) was closed by the stale bot. Upstream `remote_model_dir` may already cover it. A check is pending. |
| Stage safetensors shards from a subdirectory | Branch `fix/cluster-nested-shard-staging` is current. New pull request pending (replaces #3204). |
| Fall back to a TCP probe when the link ping fails | Branch `fix/cluster-link-ping-tcp-fallback` is current. New pull request pending (replaces #3206). |
| Retry flaky mDNS resolution in cluster SSH probes | Branch `fix/cluster-mdns-probe-retry` is current. New pull request pending (replaces #3208). |

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
The quick start swaps in a fine-tune of it ([which model to use](#which-model-to-use)).

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
- Decode runs at about 75–110 tokens per second on a short reply of tool-call text.
  In agent sessions it is 10–30 tokens per second and it varies a lot.
  Speculative decoding gets 5–7 tokens per cycle on code and tool calls, and 2–3 on prose and thinking.
  The same request ran up to 1.7 times slower a few minutes later, after Splash reported a thermal state of fair.
  Another request that prefills at the same time also slows decode.
- Qwen3.8-27B decodes at about 21 tokens per second, and a 92K-token prefill takes more than 20 minutes.
  Use the 35B-A3B model on an M1 or M2 Mac.
- One Qwen model of 24–27 GB fits in memory at a time on a 64 GB Mac.
  Loading a second one evicts the first, and a reload takes about a minute.

## Benchmarks

`scripts/bench_splash_session.py` replays a synthetic agent session against a real server and reads the cache numbers from the Splash log.
Each session has 9 turns, 14 tools, and a 2,000-token system prompt.
Each turn adds a 1,500-token tool result.
Turn 5 adds a new user message.
Every third turn adds a trailing system reminder, as Claude Code does.
The model is Qwen3.6-35B-A3B-4bit on an M1 Max with 64 GB, with Splash M1 1.3.2.
Each arm ran 2 sessions.

| Arm | Cache hit, turns 2–9 | Prefilled tokens | Median TTFT | p90 TTFT | Session time |
|---|---|---|---|---|---|
| oMLX 0.7.1 | 60.1% | 144,712 | 8.6 s | 32.8 s | 3.0 min |
| Fork | 84.6% | 56,324 | 8.1 s | 9.5 s | 1.7 min |
| Fork, `--state-format bf16` and `--decode-share 1.0` | 84.6% | 56,304 | 7.9 s | 9.3 s | 1.7 min |

TTFT is the time to the first token.

- oMLX 0.7.1 loses most of the cache on turns 3, 6, and 9 (the reminder turns).
  On turn 9 it reuses 7,008 of 34,736 tokens.
  The fork reuses 31,488 of 35,079 tokens.
- The fork keeps the system reminder in place, and it sends `preserve_thinking` to Splash.
  Without these two changes, Splash renders the earlier prompt in a different way and the prefix no longer matches.
- The `bf16` and `decode-share` options do not change the cache result.
  At a matching context of about 95K tokens, decode ran at 23.3 tokens per second with both options on and 22.2 with both off.
  The two options show no gain and no loss in these measurements.
  The sample is small, and concurrent requests change decode speed.
- Real Claude Code sessions with a 70K-token context showed 98–99.7% cache hits on the fork.
  Before the fix the same sessions showed 25–48%.
- A small resident model for the auto-mode classifier did not help.
  A 4B model in process prefilled at about 110 tokens per second.
  Splash prefilled the 35B model at about 160 tokens per second.

To repeat the run, stop the live service first.
The run uses the GPU and slows down other sessions.

1. Install each arm in its own environment.
2. Run `python scripts/bench_splash_session.py --arm stock=<omlx> --arm fork=<python> --turns 9 --followup-after 5 --result-tokens 1500 --system-tokens 2000 --repeat 2 --out results.json`.
3. Start each server from a directory that is not an oMLX checkout.
   The script does this for you.
   A checkout in the current directory replaces the installed package.

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

### The model prints a command instead of running it

Symptom: a turn ends after a few seconds.
Claude writes the command in a code fence, calls no tool, and may blame the hook output for "truncated output".
No command ran, so there was no output to truncate.

The cause is sampling plus the history.
The model sometimes writes a command as text at temperature 1.0, which is the Claude Code default.
Once one earlier turn holds a printed command, the model copies it on every later turn.
In replays of a captured request, the model called the tool in 4 of 4 runs with no earlier printed command, 1 of 4 with one, and 0 of 4 with three.
At temperature 1.0 we saw 1 miss in 12 first turns.
At temperature 0.6 we saw none in 12.
With `force_sampling` on at 0.6, 40 of 41 later tool turns made tool calls, and the one stop was a correct answer.

The fix is the sampling in the quick start.
To recover a session that already loops, press Esc twice or run `/rewind` to a turn before the first printed command.
If the first turn of the session already failed, run `/clear`.

### A `/goal` check times out

`/goal` sends the whole conversation to the small fast model after every turn.
Here that is the same 35B model, and its prompt shares only a small prefix with the main thread.
A 40K-token check is a cold prefill of about 250 seconds, and a prompt-based hook has a 30-second timeout.
The check cannot finish, and it holds up the next turn.
Do not use `/goal` on this setup.
A Stop hook with a longer `timeout` is an alternative that we did not try.

### Several sessions at once

Each Claude Code session sends main turns, and auto mode adds a classifier request per tool call.
Splash does not batch a second prefill with decode, so two sessions slow each other down.
A classifier request whose prompt has a few thousand new tokens waited 11–25 seconds for its first token while two other sessions ran.
One was cancelled after 69 seconds.

`splash_decode_share` sets how much decode time other requests are owed for each unit of prefill time.
The default is 0.5.
We lowered it to 0.15 so that a classifier prefill gets more of the GPU.
In the first 30 minutes with two sessions, cache hits stayed at 99%, but main-turn decode was a median of 6 tokens per second.
Three classifier-sized requests were cancelled, and Claude Code did not show a warning.
We do not know yet if 0.15 helps.
If decode stays slow, raise it to 0.3 or return to the default.

Narrow allow rules such as `Bash(git commit *)` are resolved before the classifier runs.
They remove the classifier call for routine commands, and they matter most for runs without someone at the keyboard.

### Decode is slower than expected

- Compare like with like.
  Speed depends on how predictable the text is, on what else runs, and on the thermal state.
- We found no effect from Splash 1.3.2-m1.1 against m1.2, `int8` against `bf16` KV, `--state-format bf16`, or `--decode-share 1.0`.
- A thinking budget of 1024 tokens costs nothing that we saw.
  It protects against a runaway thinking loop.
  It is not a proven speed gain.
- macOS sometimes aborts a GPU command with "Impacting Interactivity".
  Splash restarts its engine in about 17 seconds, and oMLX returns HTTP 500 to the client.
  Keep other GPU work off the Mac while the server runs.

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
