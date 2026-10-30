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
import os
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


def fork_files(base: str) -> tuple[list[str], list[str]]:
    """Return (files with fork changes, files that are new), repo-relative.

    New files include untracked ones, which git diff does not list.
    """
    status = run(["git", "diff", "--name-status", "-M", base, "--", "*.py"])
    files, new = [], []
    for row in status.splitlines():
        code, *names = row.split("\t")
        if code[0] not in "AMR" or not Path(names[-1]).is_file():
            continue
        files.append(names[-1])
        if code[0] == "A":
            new.append(names[-1])
    untracked = run(["git", "ls-files", "--others", "--exclude-standard", "--", "*.py"])
    for name in untracked.splitlines():
        files.append(name)
        new.append(name)
    return files, new


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base", default="upstream/main", help="upstream ref")
    args = parser.parse_args()

    os.chdir(run(["git", "rev-parse", "--show-toplevel"]).strip())
    # During an uncommitted merge, upstream lines from MERGE_HEAD are not fork lines.
    heads = ["HEAD"]
    git_dir = Path(run(["git", "rev-parse", "--git-dir"]).strip())
    if (git_dir / "MERGE_HEAD").is_file():
        heads.append("MERGE_HEAD")
    base = run(["git", "merge-base", args.base, *heads]).strip()
    files, new_files = fork_files(base)
    if not files:
        print("fork_lint: no fork Python changes")
        return 0

    # A new file is all fork lines. An edited file is the lines in its diff.
    fork_lines = changed_lines(base, [f for f in files if f not in new_files])
    for name in new_files:
        count = len(Path(name).read_text(encoding="utf-8").splitlines())
        fork_lines[str(Path(name).resolve())] = set(range(1, count + 1))
    report = run(
        ["ruff", "check", "--output-format", "json", "--no-fix", *files],
        ok_codes=(0, 1),
    )
    findings = []
    for finding in json.loads(report):
        key = str(Path(finding["filename"]).resolve())
        if key not in fork_lines:
            sys.stderr.write(
                f"fork_lint: ruff reported {finding['filename']}, which is not a "
                "fork file. Check the repo path for a symlink.\n"
            )
            return 2
        if finding["location"]["row"] in fork_lines[key]:
            findings.append(finding)
    for f in findings:
        rel = os.path.relpath(f["filename"])
        print(f"{rel}:{f['location']['row']}: {f['code']} {f['message']}")
    print(f"fork_lint: {len(findings)} findings on fork lines in {len(files)} files")

    if new_files:
        fmt = subprocess.run(
            ["ruff", "format", "--check", *new_files], capture_output=True, text=True
        )
        if fmt.returncode != 0:
            print(fmt.stdout, end="")
            print("fork_lint: run 'ruff format' on the files above.")
            return 1
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
