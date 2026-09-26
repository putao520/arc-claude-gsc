#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOCK = json.loads((ROOT / "runtime.lock.json").read_text(encoding="utf-8"))
REPO = "putao520/arc-claude-gsc"


def github_json(path: str):
    request = urllib.request.Request(
        f"https://api.github.com{path}",
        headers={"Accept": "application/vnd.github+json", "User-Agent": "arc-claude-gsc-release-verifier"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def asset(release: dict, name: str) -> dict:
    for item in release.get("assets", []):
        if item.get("name") == name:
            return item
    raise SystemExit(f"release {release.get('tag_name')} is missing asset {name}")


def verify_digest(item: dict, expected: str) -> None:
    actual = str(item.get("digest") or "")
    wanted = f"sha256:{expected}"
    if actual != wanted:
        raise SystemExit(f"digest mismatch for {item.get('name')}: expected {wanted}, got {actual or '<none>'}")
    print(f"OK {item['name']} {actual}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--require-current-submission", action="store_true")
    args = parser.parse_args()

    runtime_release = github_json(f"/repos/{REPO}/releases/tags/{LOCK['gsc']['releaseTag']}")
    verify_digest(asset(runtime_release, LOCK["gsc"]["asset"]), LOCK["gsc"]["sha256"])
    verify_digest(asset(runtime_release, LOCK["zstd"]["asset"]), LOCK["zstd"]["sha256"])

    submission_release = github_json(f"/repos/{REPO}/releases/tags/submission-v1")
    submission = asset(submission_release, "submission.zip")
    if not submission.get("digest", "").startswith("sha256:"):
        raise SystemExit("submission-v1/submission.zip has no GitHub SHA-256 digest")
    print(f"OK submission-v1/submission.zip {submission['digest']} ({submission['size']} bytes)")

    tag_ref = github_json(f"/repos/{REPO}/git/ref/tags/submission-v1")
    main_ref = github_json(f"/repos/{REPO}/git/ref/heads/main")
    tag_sha = tag_ref["object"]["sha"]
    main_sha = main_ref["object"]["sha"]
    if tag_sha == main_sha:
        print(f"OK submission-v1 tag matches main: {main_sha}")
    else:
        print(f"STALE submission-v1 tag={tag_sha} main={main_sha}")
        if args.require_current_submission:
            return 3

    print("Release metadata and runtime.lock digests are consistent.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
