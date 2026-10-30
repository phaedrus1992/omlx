#!/usr/bin/env bash
# Fork verify gate: the checks that upstream CI runs, plus lint on fork lines.
# verify-replaces: python
#
# Usage: scripts/verify.sh [-h|--help]
#
# Upstream gates on pytest only, and its files carry many ruff and type
# findings. So this script replaces the generic python checks:
#   1. the native kernel binaries are not older than their source
#   2. ruff lint on the lines the fork adds or changes (scripts/fork_lint.py)
#   3. pytest with the upstream CI marker filter, less known M1 failures (#33)
#   4. the web UI node tests that upstream CI runs
# Pytest runs with HOME and OMLX_BASE_PATH in a new temp dir. A test that
# writes settings then cannot change the live ~/.omlx files.
# Many tests use the GPU. Stop a live oMLX service before a run on the same Mac.
set -euo pipefail

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
	sed -n '2,15p' "$0" | sed 's/^# \{0,1\}//'
	exit 0
fi

repo_root=$(git rev-parse --show-toplevel)
cd "$repo_root"
python="$repo_root/.venv/bin/python"
if [[ ! -x "$python" ]]; then
	echo "verify: $python is missing. Run 'uv sync' in $repo_root." >&2
	exit 2
fi

status=0
run_check() {
	local name=$1
	shift
	if "$@"; then
		echo "PASS $name"
	else
		echo "FAIL $name"
		status=1
	fi
}

# The temp dir is outside $HOME, because tests that walk HOME must not find it.
sandbox=$(mktemp -d /private/tmp/omlx-verify.XXXXXX)
trap 'rm -rf "$sandbox"' EXIT
mkdir -p "$sandbox/home" "$sandbox/omlx"

# These upstream GPU tests fail on M1-class GPUs only (#33).
known_m1_failures=()
if sysctl -n machdep.cpu.brand_string | grep -q 'Apple M1'; then
	gdn=tests/test_qwen35_gdn_prework.py::test_qwen4_fused_verify_equals_per_op_verify_and_rollback
	sdpa=tests/test_decode_fast_sdpa.py::test_matches_mx_fast
	known_m1_failures=(
		--deselect "${gdn}[True-3-6-signatures0]"
		--deselect "${gdn}[True-11-7-signatures0]"
		--deselect "${gdn}[True-11-9-signatures1]"
		--deselect "${gdn}[False-3-6-signatures0]"
		--deselect "${gdn}[False-11-7-signatures0]"
		--deselect "${gdn}[False-11-9-signatures1]"
		--deselect "${sdpa}[1-8-1-4-2048-192-128-dtype0]"
		--deselect "${sdpa}[1-8-1-4-2048-256-256-dtype2]"
		--deselect tests/test_glm_moe_dsa_patch.py::test_glm_native_fused_kernels_match_reference
	)
fi

# Old kernel binaries make GPU tests fail after a sync changes kernel source.
# Each binary depends on its own directory and on the shared include headers.
# shellcheck disable=SC2329 # run_check calls this function by name.
kernels_current() {
	local ext newer stale=0
	for ext in omlx/custom_kernels/*/_ext*.so; do
		[[ -e "$ext" ]] || continue
		newer=$(find "$(dirname "$ext")" omlx/custom_kernels/include -type f \
			\( -name '*.metal' -o -name '*.h' -o -name '*.cpp' -o -name '*.mm' \) \
			-newer "$ext" | head -n 3)
		if [[ -n "$newer" ]]; then
			echo "verify: kernel source is newer than $ext:" >&2
			echo "$newer" >&2
			stale=1
		fi
	done
	if ((stale)); then
		echo "verify: run 'make kernels PYTHON=.venv/bin/python', then run again." >&2
		return 1
	fi
}

run_check kernels-current kernels_current
run_check fork-lint env PATH="$repo_root/.venv/bin:$PATH" "$python" scripts/fork_lint.py
run_check pytest env HOME="$sandbox/home" OMLX_BASE_PATH="$sandbox/omlx" \
	TMPDIR="$sandbox" "$python" -m pytest tests/ apps/omlx-web/tests \
	apps/omlx-mac/Tests/python -m "not slow and not integration" -q \
	${known_m1_failures[@]+"${known_m1_failures[@]}"}
# Upstream CI does not run these two, and a run of both did not finish in 2 minutes.
node_skipped=(network_auth_ui ssd_cache_clear_ui)
node_tests=()
for test_file in apps/omlx-web/tests/*.test.cjs; do
	name=$(basename "$test_file" .test.cjs)
	if [[ " ${node_skipped[*]} " != *" $name "* ]]; then
		node_tests+=("$test_file")
	fi
done
run_check node-ui node --test "${node_tests[@]}"

exit "$status"
