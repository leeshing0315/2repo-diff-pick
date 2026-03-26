#!/usr/bin/env python3
import argparse
from datetime import date
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


def remote_branch_exists(repo_dir, remote_name, branch_name):
    out = run_git(["ls-remote", "--heads", remote_name, branch_name], cwd=repo_dir)
    return bool(out)


def create_sync_branches_and_push(github_repo_dir, intranet_personal_repo_dir, commits_to_pick):
    if not commits_to_pick:
        return []

    run_git(["remote", "add", "github-src", str(github_repo_dir)], cwd=intranet_personal_repo_dir)
    run_git(["fetch", "--quiet", "github-src", "main"], cwd=intranet_personal_repo_dir)

    today_str = date.today().strftime("%Y-%m-%d")
    branch_names = [f"sync-{today_str}-{idx}" for idx in range(1, len(commits_to_pick) + 1)]

    for branch_name in branch_names:
        if remote_branch_exists(intranet_personal_repo_dir, "origin", branch_name):
            raise GitCommandError(
                f"Remote branch already exists: {branch_name}. "
                "Please clean it up or rerun on another date."
            )

    created = []
    parent_branch = "master"
    for idx, commit in enumerate(commits_to_pick, start=1):
        branch_name = f"sync-{today_str}-{idx}"
        run_git(["checkout", "--quiet", parent_branch], cwd=intranet_personal_repo_dir)
        run_git(["checkout", "--quiet", "-b", branch_name], cwd=intranet_personal_repo_dir)
        try:
            run_git(["cherry-pick", commit["sha"]], cwd=intranet_personal_repo_dir)
        except GitCommandError as exc:
            try:
                run_git(["cherry-pick", "--abort"], cwd=intranet_personal_repo_dir)
            except GitCommandError:
                pass
            raise GitCommandError(
                f"Cherry-pick failed on branch {branch_name} for commit {commit['sha']}: {exc}"
            )

        run_git(["push", "--quiet", "-u", "origin", branch_name], cwd=intranet_personal_repo_dir)
        created.append(
            {
                "branch": branch_name,
                "source_commit": commit["sha"],
                "subject": commit["subject"],
            }
        )
        parent_branch = branch_name

    run_git(["checkout", "--quiet", "master"], cwd=intranet_personal_repo_dir)
    return created


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Find which commit in GitHub main has the same content as "
            "the latest commit in intranet public master, then list commits after it "
            "and sync them into personal intranet branches."
        )
    )
    parser.add_argument("github_remote", help="GitHub repository remote URL")
    parser.add_argument("intranet_public_remote", help="Intranet public repository remote URL")
    parser.add_argument("intranet_personal_remote", help="Intranet personal repository remote URL")
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
    intranet_public_dir = work_dir / "intranet-public"
    intranet_personal_dir = work_dir / "intranet-personal"

    exit_code = 0
    try:
        clone_repo(args.github_remote, "main", github_dir)
        clone_repo(args.intranet_public_remote, "master", intranet_public_dir)
        clone_repo(args.intranet_personal_remote, "master", intranet_personal_dir)

        matching_commit, commits_to_pick = find_equivalent_commit(github_dir, intranet_public_dir)
        created_branches = []
        if matching_commit:
            created_branches = create_sync_branches_and_push(
                github_dir,
                intranet_personal_dir,
                commits_to_pick,
            )

        result = {
            "download_dir": str(work_dir),
            "github_remote": args.github_remote,
            "intranet_public_remote": args.intranet_public_remote,
            "intranet_personal_remote": args.intranet_personal_remote,
            "matching_github_commit": matching_commit,
            "commits_to_pick_count": len(commits_to_pick),
            "commits_to_pick": commits_to_pick,
            "created_branches_count": len(created_branches),
            "created_branches": created_branches,
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
