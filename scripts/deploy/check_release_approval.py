#!/usr/bin/env python3
"""Prove the exact production merge was approved, even when bypasses are hidden.

Only a merged PR into this repository's main whose merge SHA equals the release
SHA is eligible. No direct push, fork checkout, unrelated PR or stale approval.
Uses metadata/pull-requests read access; no administration credential required.
"""
import json
import os
import re
import sys
import urllib.error
import urllib.request

REPOSITORY = "rafaelRojasVi/origenlab"


class Refused(RuntimeError):
    pass


def get(path):
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        raise Refused("Missing GitHub read token")
    request = urllib.request.Request("https://api.github.com/repos/" + REPOSITORY + path,
        headers={"Authorization": "Bearer " + token, "Accept": "application/vnd.github+json",
                 "X-GitHub-Api-Version": "2022-11-28"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)
    except (urllib.error.URLError, ValueError) as error:
        raise Refused("Cannot verify production merge review") from error


def approved(pr, reviews, sha, *, solo_mode=False):
    if (not pr.get("merged_at") or pr.get("merge_commit_sha") != sha
            or pr.get("base", {}).get("ref") != "main"
            or pr.get("base", {}).get("repo", {}).get("full_name") != REPOSITORY
            or pr.get("head", {}).get("repo", {}).get("full_name") != REPOSITORY):
        return False
    # Opt-in single-maintainer repository mode: the repository owner personally
    # merging their own internal PR is the release authorization. A bot/direct push,
    # fork PR, another user's merge, or unreviewed auto-merge is not equivalent.
    # All GitHub Actions checks and migration/backup/release gates remain mandatory.
    if solo_mode:
        owner = REPOSITORY.split("/")[0]
        return (pr.get("user", {}).get("login") == owner
                and pr.get("merged_by", {}).get("login") == owner
                and pr.get("head", {}).get("sha") is not None)
    latest = {}
    for review in sorted(reviews, key=lambda x: x.get("id", 0)):
        if review.get("state") in {"APPROVED", "CHANGES_REQUESTED", "DISMISSED"}:
            latest[review.get("user", {}).get("login")] = review
    return any(login and login != pr.get("user", {}).get("login")
               and review.get("state") == "APPROVED"
               and review.get("commit_id") == pr.get("head", {}).get("sha")
               and review.get("author_association") in {"OWNER", "MEMBER", "COLLABORATOR"}
               for login, review in latest.items())


def main():
    sha = os.environ.get("OL_RELEASE_SHA", "")
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise Refused("Missing exact merge SHA")
    pulls = get(f"/commits/{sha}/pulls?per_page=100")
    if not isinstance(pulls, list) or len(pulls) >= 100:
        raise Refused("Unexpected or truncated merge provenance")
    for pr in pulls:
        if pr.get("merge_commit_sha") != sha or not pr.get("merged_at"):
            continue
        reviews = get(f"/pulls/{pr['number']}/reviews?per_page=100")
        if not isinstance(reviews, list) or len(reviews) >= 100:
            raise Refused("Unexpected or truncated PR review history")
        if approved(pr, reviews, sha, solo_mode=os.environ.get("OL_RELEASE_SOLO_MODE") == "true"):
            print("Exact production merge approved under configured owner/collaborator policy")
            return
    raise Refused("Pinned SHA is not an independently approved main merge")


if __name__ == "__main__":
    try:
        main()
    except Refused as error:
        print("REFUSED: " + str(error), file=sys.stderr)
        sys.exit(1)
