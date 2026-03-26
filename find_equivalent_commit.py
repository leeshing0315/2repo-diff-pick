#!/usr/bin/env python3
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


class GitCommandError(RuntimeError):
    pass


def run_git(args, cwd=None):
    cmd = ["git", *args]
    proc = subprocess.run(
        cmd,
        cwd=cwd,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise GitCommandError(
            f"Command failed: {' '.join(cmd)}\n"
            f"stdout:\n{proc.stdout}\n"
            f"stderr:\n{proc.stderr}"
        )
    return proc.stdout.strip()


def get_download_base_dir():
    if os.name == "nt":
        base_dir = Path.cwd() / "tmp"
    else:
        base_dir = Path("/tmp")
    base_dir.mkdir(parents=True, exist_ok=True)
    return base_dir


def clone_repo(url, branch, target_dir):
    run_git(
        [
            "clone",
            "--quiet",
            "--branch",
            branch,
            "--single-branch",
            url,
            str(target_dir),
        ]
    )


def parse_commit_lines(raw_text):
    commits = []
    if not raw_text:
        return commits

    for line in raw_text.splitlines():
        parts = line.split(" ", 1)
        sha = parts[0]
        subject = parts[1] if len(parts) > 1 else ""
        commits.append({"sha": sha, "subject": subject})
    return commits


def find_equivalent_commit(github_repo_dir, intranet_repo_dir):
    intranet_tree = run_git(["rev-parse", "master^{tree}"], cwd=intranet_repo_dir)

    github_history = run_git(
        ["log", "--first-parent", "--format=%H %T", "main"],
        cwd=github_repo_dir,
    )

    matching_commit = None
    for line in github_history.splitlines():
        sha, tree = line.strip().split(" ", 1)
        if tree == intranet_tree:
            matching_commit = sha
            break

    if not matching_commit:
        return None, []

    commit_list_raw = run_git(
        [
            "log",
            "--reverse",
            "--no-merges",
            "--format=%H %s",
            f"{matching_commit}..main",
        ],
        cwd=github_repo_dir,
    )
    commits_to_pick = parse_commit_lines(commit_list_raw)
    return matching_commit, commits_to_pick


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Find which commit in GitHub main has the same content as "
            "the latest commit in intranet master, then list commits after it."
        )
    )
    parser.add_argument("github_remote", help="GitHub repository remote URL")
    parser.add_argument("intranet_remote", help="Intranet repository remote URL")
    parser.add_argument(
        "--cleanup",
        action="store_true",
        help="Remove the temporary download directory after execution",
    )
    args = parser.parse_args()

    try:
        run_git(["--version"])
    except GitCommandError as exc:
        print(f"git is not available: {exc}", file=sys.stderr)
        return 2

    base_dir = get_download_base_dir()
    work_dir = Path(tempfile.mkdtemp(prefix="repo-diff-pick-", dir=str(base_dir)))
    github_dir = work_dir / "github"
    intranet_dir = work_dir / "intranet"

    exit_code = 0
    try:
        clone_repo(args.github_remote, "main", github_dir)
        clone_repo(args.intranet_remote, "master", intranet_dir)

        matching_commit, commits_to_pick = find_equivalent_commit(github_dir, intranet_dir)

        result = {
            "download_dir": str(work_dir),
            "github_remote": args.github_remote,
            "intranet_remote": args.intranet_remote,
            "matching_github_commit": matching_commit,
            "commits_to_pick_count": len(commits_to_pick),
            "commits_to_pick": commits_to_pick,
        }
        print(json.dumps(result, indent=2, ensure_ascii=False))
    except GitCommandError as exc:
        print(str(exc), file=sys.stderr)
        exit_code = 1
    finally:
        if args.cleanup:
            try:
                shutil.rmtree(work_dir)
            except OSError as exc:
                print(
                    f"Failed to clean up temporary directory {work_dir}: {exc}",
                    file=sys.stderr,
                )

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
