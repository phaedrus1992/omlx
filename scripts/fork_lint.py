"""Lint only the Python lines that the fork adds or changes against upstream.

Usage: python scripts/fork_lint.py [--base REF]

Upstream does not gate on ruff, and its files carry many findings. A fix to an
upstream line makes a conflict on each sync, so this gate checks fork lines only.
Exit 0 when no finding is on a fork line. Exit 1 when a finding is. Exit 2 on a
usage error or a failed git or ruff call.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

HUNK_HEADER = re.compile(r"^@@ -[0-9]+(?:,[0-9]+)? \+([0-9]+)(?:,([0-9]+))? @@")


def run(cmd: list[str], ok_codes: tuple[int, ...] = (0,)) -> str:
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode not in ok_codes:
        sys.stderr.write(
            f"fork_lint: '{' '.join(cmd)}' exited {result.returncode}.\n"
            f"{result.stderr}\nFix the command error, then run again.\n"
        )
        sys.exit(2)
    return result.stdout


def changed_lines(base: str, files: list[str]) -> dict[str, set[int]]:
    """Return the new-side line numbers that each file adds or changes."""
    lines: dict[str, set[int]] = {}
    current: set[int] | None = None
    diff = run(["git", "diff", "-U0", "--no-color", base, "--", *files])
    for line in diff.splitlines():
        if line.startswith("+++ "):
            path = line[4:].removeprefix("b/")
            current = lines.setdefault(str(Path(path).resolve()), set())
            continue
        match = HUNK_HEADER.match(line)
        if match and current is not None:
            start = int(match.group(1))
            count = int(match.group(2) or "1")
            current.update(range(start, start + count))
    return lines


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base", default="upstream/main", help="upstream ref")
    args = parser.parse_args()

    # During an uncommitted merge, upstream lines from MERGE_HEAD are not fork lines.
    heads = ["HEAD"]
    git_dir = Path(run(["git", "rev-parse", "--git-dir"]).strip())
    if (git_dir / "MERGE_HEAD").is_file():
        heads.append("MERGE_HEAD")
    base = run(["git", "merge-base", args.base, *heads]).strip()
    names = ["git", "diff", "--name-only", "--diff-filter=AM", base, "--", "*.py"]
    files = [f for f in run(names).splitlines() if Path(f).is_file()]
    added = run(["git", "diff", "--name-only", "--diff-filter=A", base, "--", "*.py"])
    new_files = [f for f in added.splitlines() if Path(f).is_file()]
    if not files:
        print("fork_lint: no fork Python changes")
        return 0

    fork_lines = changed_lines(base, files)
    report = run(
        ["ruff", "check", "--output-format", "json", "--no-fix", *files],
        ok_codes=(0, 1),
    )
    findings = [
        f
        for f in json.loads(report)
        if f["location"]["row"] in fork_lines.get(str(Path(f["filename"])), set())
    ]
    for f in findings:
        rel = Path(f["filename"]).relative_to(Path.cwd())
        print(f"{rel}:{f['location']['row']}: {f['code']} {f['message']}")

    format_failed = False
    if new_files:
        fmt = subprocess.run(
            ["ruff", "format", "--check", *new_files], capture_output=True, text=True
        )
        if fmt.returncode != 0:
            format_failed = True
            print(fmt.stdout, end="")
            print("fork_lint: run 'ruff format' on the files above.")

    print(f"fork_lint: {len(findings)} findings on fork lines in {len(files)} files")
    return 1 if findings or format_failed else 0


if __name__ == "__main__":
    sys.exit(main())
