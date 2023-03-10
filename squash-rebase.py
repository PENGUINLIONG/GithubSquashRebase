from datetime import datetime
import subprocess
from typing import List, Tuple
import requests
import re

# Generate your Github token and paste it here (with `repo:status` and `public_repo` permissions).
TOKEN = "ghp_XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX"
headers = {
    "Accept": "application/vnd.github.v3+json",
    "Authorization" : f"token {TOKEN}",
}

class GitError(Exception):
    def __init__(self, message: str, stderr: str = "") -> None:
        super().__init__(message)
        self.stderr = stderr

    def __str__(self) -> str:
        return super().__str__() + " " + self.stderr
    def __repr__(self) -> str:
        return self.__str__()

def git(cmd: str) -> str:
    proc = subprocess.Popen(["git"] + cmd.split(), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    proc.wait()
    stdout = proc.stdout.read().decode("utf-8").strip() if proc.stdout is not None else ""
    if proc.returncode == 0:
        return stdout
    else:
        stderr = proc.stderr.read().decode("utf-8").strip() if proc.stderr is not None else ""
        raise GitError(f"git {cmd} failed with exit code {proc.returncode}", stderr)


def git_status() -> List[str]:
    out = []
    for line in git("status --porcelain").splitlines():
        [modifier, path] = line.split(maxsplit=1)
        out += [path]
    return out

def git_is_dirty() -> bool:
    return len(git_status()) > 0

def git_remote() -> List[str]:
    return git("remote").splitlines()

def git_remote_get_url(name: str) -> str:
    return git(f"remote get-url {name}")

def git_get_upstream_user_repo() -> Tuple[str, str]:
    url = git_remote_get_url("upstream")
    m_https = re.match(r"https://github.com/([^/]+)/([^/]+)(?:.git)?", url)
    m_ssh = re.match(r"git@github.com:([^/]+)/([^/]+)(?:.git)?", url)
    out = None
    if m_https is not None:
        out = (m_https.group(1), m_https.group(2))
    elif m_ssh is not None:
        out = (m_ssh.group(1), m_ssh.group(2))
    else:
        raise GitError(f"Failed to parse GitHub URL: {url}")
    return out

def git_has_upstream() -> bool:
    return "upstream" in git_remote()

def git_fetch_upstream() -> None:
    git("fetch upstream")

def git_merge_base() -> str:
    out = git("merge-base upstream/master HEAD")
    if out == "":
        raise GitError("No common ancestor found between upstream/master and HEAD")
    return out

def git_log_merge_base_to_upstream() -> List[str]:
    return git(f"log --oneline {git_merge_base()}..upstream/master").splitlines()

def git_log_merge_base_to_current() -> List[str]:
    return git(f"log --oneline {git_merge_base()}..HEAD").splitlines()

def git_show(commit: str) -> str:
    return git(f"show {commit} -q")

def git_branch(new_branch: str) -> None:
    git(f"branch {new_branch}")

def git_branch_show_current() -> str:
    return git("branch --show-current")

def git_reset_hard(commit: str) -> None:
    git(f"reset --hard {commit}")

def git_cherry_pick(commit: str) -> None:
    git(f"cherry-pick {commit}")

def git_rebase_upstream() -> None:
    git("rebase upstream/master")

def get_commit_pr_number(commit_sha: str) -> List[str]:
    res = requests.get(f"https://{TOKEN}@api.github.com/repos/{upstream_user}/{upstream_repo}/commits/{commit_sha}/pulls", headers=headers)
    if res.status_code >= 400:
        raise GitError(f"Failed to query PR number from commit via Github API ({res.status_code}): {res.text}")

    prs = res.json()
    out = []
    for pr in prs:
        out += [pr["number"]]
    return out

def get_pr_commits(pr_number: str) -> List[str]:
    res = requests.get(f"https://{TOKEN}@api.github.com/repos/{upstream_user}/{upstream_repo}/pulls/{pr_number}/commits", headers=headers)
    if res.status_code >= 400:
        raise GitError(f"Failed to query commit list from PR via Github API ({res.status_code}): {res.text}")

    commits = res.json()
    out = []
    for commit in commits:
        out += [commit["sha"]]
    return out

if __name__ == "__main__":
    if git_is_dirty():
        print("Found uncommitted changes, please commit or stash them first.")
        for path in git_status():
            print(f"  {path}")
        exit(1)

    if not git_has_upstream():
        print("No upstream remote found, please add one first. For example:")
        print("  git remote add upstream https://github.com/PENGUINLIONG/spirq-rs.git")
        exit(1)

    upstream_user, upstream_repo = git_get_upstream_user_repo()
    print("Found upstream remote:")
    print(f"  {upstream_user}/{upstream_repo}")
    print()

    git("fetch upstream")

    common_ancestor = git_merge_base()
    print(f"Found common ancestor from current HEAD to upstream/master:")
    for line in git_show(common_ancestor).splitlines():
        print(f"  {line}")
    print()

    branch_name = git_branch_show_current()
    print(f"Current branch:")
    print(f"  {branch_name}")
    print()

    print("Do you want to backup the current branch? (Y/n)")
    while True:
        x = input().strip().lower()
        if x == "":
            x = "y"
        if x == "y" or x == "n":
            if x == "y":
                backup_branch_name = f"{branch_name}-backup-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
                git_branch(backup_branch_name)
                print(f"Created backup branch {backup_branch_name} from current branch.")
            break
        else:
            print("Please enter y or n.")
    print()

    print(f"Found squashed PR commits in upstream/master not yet rebased to the current branch:")
    commits_to_remove = []
    for pr_commit in git_log_merge_base_to_upstream():
        print(f"  {pr_commit}")
        commit_sha, commit_title = pr_commit.split(maxsplit=1)

        m = re.match(r".*\(#(\d+)\)$", commit_title)
        if m:
            # If we can infer the PR number from the commit title, use that.
            pr_numbers = [m.group(1)]
            print(f"    Inferred PR number from commit title: #{pr_numbers[0]}")
        else:
            # Otherwise, use the GitHub API to find the PR number.
            pr_numbers = get_commit_pr_number(commit_sha)
            for pr_number in pr_numbers:
                print(f"    Queried related PR number from commit via Github API: #{pr_number}")

        # Crawling all commits related to the PR, if possible.
        if len(pr_numbers) == 0:
            print("    (no PR found)")
        else:
            for pr_number in pr_numbers:
                commits_to_remove += get_pr_commits(pr_number)
    print()

    print(f"Commits to be removed from the current branch (if found):")
    for commit_sha in commits_to_remove:
        print(f"  {commit_sha}")
    print()

    new_commits = git_log_merge_base_to_current()

    git_reset_hard(common_ancestor)
    print(f"Reset current branch to common ancestor ({common_ancestor}).")
    print()

    git_rebase_upstream()
    print("Rebased current branch to upstream/master.")
    print()

    for commit in reversed(new_commits):
        commit_sha, commit_title = commit.split(maxsplit=1)
        should_remove = False
        for commit_to_remove in commits_to_remove:
            if commit_to_remove.startswith(commit_sha):
                should_remove = True
                break
        if should_remove:
            print(f"  Skipping {commit}...")
        else:
            git_cherry_pick(commit_sha)
            print(f"  Cherry-picking {commit}...")

    print("Done.")
