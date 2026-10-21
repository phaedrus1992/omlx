# Splash and the Qwen3.6 MoE model: investigation notes

State as of 2026-10-21.
This file records what we measured, what we got wrong, what runs now, and what to do next.
It lives on `develop` only.
Do not copy it to a branch that goes upstream.

## Goal

Run agentic coding (Claude Code) on a local model, on an M1 Max with 64 GB.
The model is `mlx-community/Qwen3.6-35B-A3B-4bit`.
The engine is Splash M1 (an unofficial M1 and M2 port of Inco's Splash), served through oMLX.
The target is decode speed near 30 tokens per second, with a prefix cache that works.

## Environment

| Item | Value |
| --- | --- |
| Machine | M1 Max, 64 GB, macOS 27.0 |
| Splash | 1.3.2-m1.2 (1.3.2-m1.1 is still installed next to it) |
| oMLX | the fork, `develop`, run by launchd as `local.omlx-fork` |
| Draft model | `incoai/Qwen3.6-35B-A3B-DFlash2` |
| KV format | int8 |
| Service log | `~/.omlx/logs/omlx-fork.log` |
| Splash log | `~/.omlx/logs/splash/omlx--Qwen3.6-35B-A3B-4bit-34ac1def.log` |

The service tree `~/git/omlx-service` is the production install.
Do not edit it.
Do all development in `~/git/omlx`.

## What is live now

| Setting | Value | Where |
| --- | --- | --- |
| Sampling for the 35B | temperature 0.6, top_p 0.95, top_k 20, `force_sampling` on | `~/.omlx/model_settings.json` |
| Thinking cap | 1024 tokens, on | `~/.omlx/model_settings.json` |
| `splash_state_format` | `fp32` (default) | `~/.omlx/settings.json` |
| `splash_decode_share` | unset | `~/.omlx/settings.json` |
| `splash_path` | unset, so Splash 1.3.2-m1.2 runs | `~/.omlx/settings.json` |
| Model pin | none | `~/.omlx/model_settings.json` |
| Debug hook | on, through `PYTHONPATH` in the launchd plist | `~/.omlx/debug-hook/` |

Backups of the settings files sit next to them with a `.bak-` suffix.
Remove the debug hook when the investigation ends (see "Cleanup").

## Pull requests and branches

All branches start from upstream `main` (`8b7e7018` when we checked).
Everything is merged into `develop`.

| Branch | Upstream PR | State |
| --- | --- | --- |
| `fix/4697-splash-mid-system` | #4708 | open |
| `fix/4701-splash-preserve-thinking` | #4709 | open |
| `fix/auto-mode-classifier-thinking` | #4714 | open (replaces #3193) |
| `refactor/anthropic-content-block-dedupe` | #3194 | open |
| `fix/cluster-slow-transport-error-message` | #3197 | open |
| `fix/cluster-link-local-thunderbolt-bridge` | #3205 | open |
| `fix/cluster-link-ping-tcp-fallback` | none yet (was #3206) | local, needs tests and review |
| `fix/cluster-nested-shard-staging` | none yet (was #3204) | local, needs tests and review |
| `fix/cluster-mdns-probe-retry` | none yet (was #3208) | local, needs tests and review |
| `feat/splash-state-format-decode-share` | none | local, merged into `develop` |

#3196 may be covered by upstream `remote_model_dir`.
Check that before a new PR.
#3195 was rejected upstream and stays closed.
Five stale remote branches are superseded and can go: `fix/classifier-thinking-mode`, `fix/cluster-link-verify-icmp-fallback`, `fix/cluster-mdns-retry`, `fix/cluster-nested-safetensors-shards`, `fix/cluster-remote-model-path-tilde`.

## Findings

### 1. The prefix cache fix works

Two oMLX bugs made Splash miss the cache.
oMLX folded Claude Code's later system messages into the first one (#4697).
oMLX did not send `preserve_thinking` to Splash (#4701).
Splash treats an absent `preserve_thinking` as false, and it then strips earlier thinking from the prompt.

| Measure | Before | After |
| --- | --- | --- |
| Real Claude Code session, 70K tokens | 25–48% cache hits | 98–99.7% |
| Synthetic replay, turns 2–9 | 60.1% (oMLX 0.7.1) | 84.6% (fork) |
| Prefilled tokens in the replay | 144,712 | 56,324 |
| p90 time to first token in the replay | 32.8 s | 9.5 s |

The replay harness is `scripts/bench_splash_session.py`.
The numbers are in `docs/FORK.md`.

Lesson: our first replay showed no difference between oMLX 0.7.1 and the fork.
The harness started each server with `python -c`, which puts the current directory first on `sys.path`.
A checkout of oMLX in that directory replaced the installed package, so both arms ran the same code.
The harness now sets the working directory of the server to the arm's base path.

### 2. The "fence loop": the model prints a command instead of calling the tool

Symptom: a Claude Code session ends each turn after a few seconds.
The model writes the command in a code fence and calls no tool.
It then says the "ICM context ate my output". That claim is false, because no command ever ran.

Cause, proven by replaying the captured request to Splash:

| Assistant turns in the history | Tool calls |
| --- | --- |
| none | 4 of 4 |
| one that printed a command | 1 of 4 |
| three that printed commands | 0 of 4 |

The model slips once.
Its own earlier text then makes it repeat the slip on every turn.
The opening context is not the cause: 20 of 20 first turns made the tool call, with 8 or 44 tools, `preserve_thinking` on or off, and with or without the hook text.
A trailing `system` message from a hook is not the cause either: 8 of 8 runs made the call.

The first slip is a sampling effect.
At temperature 1.0 (the Claude Code default) we saw 1 slip in 12 first turns.
At temperature 0.6 we saw 0 in 12, and 0 in 12 at 0.7 with top_p 0.8.
That is too few runs to prove a difference.
With about 8% per turn, a 20-turn session has about an 80% chance of one slip.
After we forced temperature 0.6, 29 of 29 tool-carrying turns made tool calls.
Later, 40 of 41 did, and the one stop was a correct final answer.

Recovery for a looped session: press Esc twice or run `/rewind` to a turn before the first printed command.
`/compact` should also work because it removes the fence turns, but we did not test it.
If the first turn already slipped, run `/clear`.

### 3. `/goal` checks time out

`/goal` is a prompt-based Stop hook.
After every turn Claude Code sends the whole conversation to the small fast model, which is the Haiku slot.
Here that slot is the same 35B.
The default timeout of a prompt hook is 30 seconds.
The goal prompt shares only a small prefix with the main thread (about 12K tokens), so a 40K-token check is a cold prefill of about 250 seconds.
The check can never finish, and it blocks the next main turn while it prefills.

Options: write our own Stop hook with a longer `timeout`, set `ANTHROPIC_DEFAULT_HAIKU_MODEL` to another model (this changes the Haiku alias everywhere), or do not use `/goal` on this setup.
We did not choose yet.

### 4. Decode speed

Decode is 10–27 tokens per second in real sessions, and it varies a lot.

What we ruled out with a quiet-machine A/B on one fixed 38K-token request:

| Setting | Result |
| --- | --- |
| Splash m1.1 against m1.2 | no difference (ABBA order: 18.2, 14.7, 19.4, 16.3 tok/s) |
| KV format int8 against bf16 | no difference |
| `--state-format bf16` and `--decode-share 1.0` | no difference at matching context (23.3 against 22.2 tok/s) |

The m1.2 release notes say MLX 4-bit decode is unchanged, which agrees.

What we know about the cause:

- Speculative decoding gets 2–3 tokens per cycle on prose and thinking text, and 5–7 on code and tool calls.
  Short tool-call replies reached 112 tok/s at 5.3 tokens per cycle.
  The Splash maintainer reports the same split for the dense 27B: 55–60 tok/s on code and 17–38 on prose.
- A context sweep (`.tmp/bench_curve.py`) did not show a clean context effect.
  Pass 1 gave 48, 57, 87 and 104 ms per cycle at 0.8K, 6.9K, 17.5K and 26.7K tokens.
  Pass 2, in reverse order, gave 83, 88, 101 and 108 ms.
  The same short prompt got 1.7 times slower between passes.
- Splash logged the thermal state going from nominal to fair about a minute into that run.
  The slowdown followed it.
  This is a correlation.
  We did not measure the GPU clock.

We also made errors that we corrected:

- We said bf16 and decode-share cost 40% of decode speed.
  The comparison used small samples at different context sizes, and it was wrong.
  `docs/FORK.md` is corrected.
- We said the service ran Splash m1.1 for several hours.
  The `splash_path` setting never persisted, so m1.2 ran the whole time.
- We said "context length slows decode by 5 times".
  The sweep does not support it.

### 5. Thinking cap

On the captured first turn, the median reasoning was only about 120 tokens, and all 40 requests made tool calls with every cap.
Median output was 317 tokens with no cap, and 177–263 with caps from 1024 down to 128.
The sample is small (8 per setting), and it is the first turn only.
We set the cap to 1024 as protection against a runaway thinking loop.
It is not a proven speed gain.

### 6. Metal aborts

macOS sometimes kills a GPU command with "Impacting Interactivity".
Splash then restarts its engine, which takes about 17 seconds, and oMLX returns HTTP 500 to the client.
It happened at 15:14 on 2026-10-20 during a test run, and at 00:34 on 2026-10-21 with no test running.
The Splash docs describe `--prefill-bound-ms` and `--prefill-mode` for this.
We did not try them.

### 7. What the Splash repository says

Repository: `paperniuk/splash`.
Issue #11 is an M1 Max 64 GB on macOS 27.0.1 with the same low decode numbers.
The maintainer asked for a `decode-profile` run and for `sudo powermetrics --samplers gpu_power -i 1000 -n 10` during generation.
The maintainer suspected GPU clocks or another app using the GPU.
Issue #5 reports that prefill dominates agent workloads.
Issue #9 reports a Qwen model that sampled its end-of-turn token early.
Contributor ghisguth has merged decode kernel changes.
The change for MLX 4-bit is about 2.8% on an M2 Max, and it is not yet in a release.
They asked for M1 Max testers.
Nobody has profiled the MoE model there.

### 8. Other models

Splash runs only Qwen3.8-27B and Qwen3.6-35B-A3B.
No Qwen3.8 MoE below 100B parameters exists.
A "Qwen3.8-35B" is a proposal with no checkpoint.
On this machine Qwen3.8-27B measured 21 tok/s decode, and a 92K prompt needed over 20 minutes to prefill.
Other models would run in the oMLX MLX engine, without Splash's cache work, and our fork changes do not touch that path.
Candidates that fit in 48 GB: GLM-4.7-Flash (30B-A3B, about 16 GB), Qwen3-Coder-30B-A3B (about 17 GB, no thinking mode), and gpt-oss-20b.
Too big: gpt-oss-120b, Qwen3-Coder-Next 80B, and Nemotron 3 Super.
The sources are mostly listicles.
None tested an M1 Max with Claude Code.

### 9. A Qwen3.8-27B fine-tune as GGUF

We tried `DavidAU/Qwen3.8-27B-TURBO-Fable-Cold-Fusion-735-882-Heretic-Uncensored-NEO-CODER-MAX-MTP-GGUF`, file `...NEO-CODER-MAX-Q4_K_M.gguf` (18 GB), with `incoai/Qwen3.8-27B-DFlash2` as the draft.
oMLX recognized the file as a Splash model, and Splash loaded it on the second try.
The first load took 353 seconds: it hashes the file once and measures the Neural Engine split.

Results on the M1 Max:

| Measure | Value |
| --- | --- |
| Tool calls on a short Bash-tool prompt | 6 of 6 |
| Decode | 2.1–2.7 tok/s |
| Tokens per cycle | 1.2–1.3 (the draft model is almost never accepted) |
| Cycle time | about 540 ms |
| Prefill, 27K-token prompt | no result: after about 12 minutes macOS aborted a GPU command ("Impacting Interactivity") |

Stock Qwen3.8-27B measured 21 tok/s on this machine, so this file is about 8 times slower.
Two causes are possible, and we did not separate them.
The DFlash2 draft was trained on the stock model, and this fine-tune and merge may not match it.
GGUF decode on M1 is also less tested: the Splash M1 release notes say GGUF models were not run end to end on M1.
We do not recommend this file.
A dense 27B is also too slow at prefill for 50K-token agent sessions, as the stock model showed.

## Plan

Work in this order.
Each item has an exit condition.

1. **Find out if the GPU clock explains the drift.**
   Run `sudo powermetrics --samplers gpu_power -i 1000 -n 10` during a long reply, once after 10 idle minutes and once after sustained work.
   Exit: we know the GPU frequency in both states.
2. **Write a time-controlled decode benchmark.**
   Interleave configurations and repeat them.
   Add a cool-down rule, and record the thermal state with every run.
   Exit: two runs of the same configuration agree within 10%.
3. **Profile the MoE on the M1 Max.**
   Try the maintainer's `decode-profile` tool on the 35B, or ask whether it supports MoE.
   Exit: a per-kernel table, or the maintainer's answer.
4. **Report upstream to `paperniuk/splash`.**
   Use the account `phaedrus1992`.
   Show the text to the user before posting.
   Content: the 35B on an M1 Max, the cycle times, the A/B result, and the powermetrics numbers from step 1.
   Do not claim that context length triples the cycle time.
5. **Run a model bake-off on the captured request.**
   Candidates: GLM-4.7-Flash and Qwen3-Coder-30B-A3B.
   Download with the `hf` CLI (the HF token is set), and ask before large downloads.
   Measure the tool-call rate over 20 runs, decode, and prefill at 50K tokens.
   Exit: a table, and a decision to stay or switch.
6. **Decide on `/goal`.**
   Write a Stop hook with a longer timeout, or drop `/goal`.
7. **Finish the upstream pull requests.**
   Run the cluster tests with `HOME` set to a temp directory and `-m "not gpu"`.
   Warn the user first, because the tests use the GPU and disturb live sessions.
   Run strict pre-pr-review, push, and open new PRs for the three cluster branches.
   Address the review findings on #4714.
   Do not open a PR for `feat/splash-state-format-decode-share` unless the two options show a gain.

## Tools

| File | Use |
| --- | --- |
| `scripts/bench_splash_session.py` | synthetic agent-session replay for oMLX against Splash (committed) |
| `.tmp/bench_decode.py`, `.tmp/bench_decode2.py` | decode A/B across Splash versions and KV formats |
| `.tmp/bench_curve.py` | decode against context length |
| `.tmp/replay/replay*.py` | replay a captured request to Splash with variations |
| `.tmp/replay/toolprobe*.py` | small tool-call reliability probes through oMLX |
| `~/.omlx/debug-hook/sitecustomize.py` | logs each Splash request and the full output to `~/.omlx/debug/` |
| `~/.omlx/debug/full/` | the 15 newest full request bodies (they hold session text) |
| `.tmp/restore-omlx-settings.py` | restores `~/.omlx/settings.json` after a test run damaged it |

The `.tmp` directory is not tracked by git.
The replay scripts read the newest file in `~/.omlx/debug/full/`.

## Rules we learned

- A test run of the oMLX suite can overwrite the real `~/.omlx/settings.json` and take the GPU.
  Warn the user first.
  Pass `base_path=tmp_path` to `GlobalSettings`, and set `HOME` to a temp directory.
- Do not wait with `sleep`.
  Read a log or use a watcher.
- Do not compare speeds from different time windows or context sizes.
  Interleave the configurations.
- A claim needs the commands that produced it.
  Say what is not measured.

## Cleanup

Do these when the investigation ends:

1. Remove the debug hook: `plutil -remove EnvironmentVariables.PYTHONPATH ~/Library/LaunchAgents/local.omlx-fork.plist`.
   Then run `launchctl bootout gui/$(id -u)/local.omlx-fork` and `launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/local.omlx-fork.plist`.
   Retry the bootstrap if it fails with an input/output error.
2. Delete `~/.omlx/debug/`, because it holds session text.
3. Delete the five stale remote branches after the user agrees.
