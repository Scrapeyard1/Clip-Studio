"""Refresh & update: get the newest Clip Studio from GitHub without touching anyone's work.

check()  -> what version this copy is, what the newest one is, and whether to update.
apply()  -> download the newest version and put it in place. Kept as they are: projects/ (every live,
            edit, render, setting and API key), models/, .venv/ and install-log.txt. A copy made with
            git (a developer's) is updated with `git pull` instead.

This copy's version comes from engine/version.txt, which GitHub fills in with the commit id whenever
someone downloads the ZIP (`export-subst` in .gitattributes). A git copy asks git.
"""
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile

import paths

REPO = "Scrapeyard1/Clip-Studio"
BRANCH = os.environ.get("CLIPSTUDIO_UPDATE_BRANCH", "main")    # override only to test a branch
KEEP = {"projects", "models", ".venv", "install-log.txt", ".git"}     # never touched by an update
TIMEOUT = 30


class UpdateError(RuntimeError):
    pass


def _get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Clip-Studio-updater",
                                               "Accept": "application/vnd.github+json"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return r.read()
    except Exception as e:                                   # no internet, GitHub down, rate limit
        raise UpdateError(f"Couldn't reach GitHub to check for updates ({e}). Check the internet "
                          "connection and try again.")


def local_version(root=None):
    """{"sha", "date"} of this copy, or {"sha": None} if it can't tell (an old download)."""
    root = root or paths.ROOT
    if os.path.isdir(os.path.join(root, ".git")):
        try:
            out = subprocess.run(["git", "-C", root, "log", "-1", "--format=%H %cI"], capture_output=True,
                                 text=True, timeout=10).stdout.split()
            if len(out) == 2:
                return {"sha": out[0], "date": out[1], "git": True}
        except (OSError, subprocess.SubprocessError):
            pass
    try:
        text = open(os.path.join(root, "engine", "version.txt"), encoding="utf-8").read().split()
    except OSError:
        text = []
    if len(text) == 2 and not text[0].startswith("$Format"):
        return {"sha": text[0], "date": text[1], "git": False}
    return {"sha": None, "date": None, "git": False}


def latest():
    d = json.loads(_get(f"https://api.github.com/repos/{REPO}/commits/{BRANCH}"))
    return {"sha": d["sha"], "date": d["commit"]["committer"]["date"],
            "message": d["commit"]["message"].splitlines()[0][:140]}


def check(root=None):
    cur, new = local_version(root), latest()
    return {"current": cur, "latest": new,
            "update_available": cur["sha"] is None or cur["sha"] != new["sha"]}


def _same(a, b):
    try:
        return open(a, "rb").read() == open(b, "rb").read()
    except OSError:
        return False


def install_from_zip(data, root):
    """Put the ZIP's files in place under root. Returns what changed."""
    with tempfile.TemporaryDirectory() as td:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            for m in z.infolist():                           # refuse paths that escape the folder
                target = os.path.realpath(os.path.join(td, m.filename))
                if not target.startswith(os.path.realpath(td) + os.sep):
                    raise UpdateError("The download looked wrong, so nothing was changed.")
            z.extractall(td)
        tops = [os.path.join(td, n) for n in os.listdir(td)]
        src = tops[0] if len(tops) == 1 and os.path.isdir(tops[0]) else td    # GitHub wraps it in one folder
        if not os.path.isfile(os.path.join(src, "app", "server.py")):
            raise UpdateError("The download isn't a Clip Studio folder, so nothing was changed.")
        req_changed = not _same(os.path.join(src, "requirements.txt"), os.path.join(root, "requirements.txt"))
        changed = []
        new_files = set()
        for dirpath, dirnames, filenames in os.walk(src):
            rel_dir = os.path.relpath(dirpath, src)
            if rel_dir.split(os.sep)[0] in KEEP:
                dirnames[:] = []
                continue
            for f in filenames:
                rel = os.path.normpath(os.path.join(rel_dir, f))
                if rel.split(os.sep)[0] in KEEP:
                    continue
                new_files.add(rel)
                dst = os.path.join(root, rel)
                if _same(os.path.join(dirpath, f), dst):   # identical files (like a running launcher) stay put
                    continue
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                tmp = dst + ".updating"
                shutil.copy2(os.path.join(dirpath, f), tmp)
                os.replace(tmp, dst)
                changed.append(rel)
        # files the new version no longer has, inside the app's own folders only
        for top in ("app", "engine", "scripts", "tests", "docs"):
            for dirpath, dirnames, filenames in os.walk(os.path.join(root, top)):
                dirnames[:] = [d for d in dirnames if d != "__pycache__"]
                for f in filenames:
                    rel = os.path.normpath(os.path.relpath(os.path.join(dirpath, f), root))
                    if rel not in new_files and not f.endswith((".pyc", ".updating")):
                        os.remove(os.path.join(root, rel))
                        changed.append(rel + " (removed)")
    return {"changed": changed, "requirements_changed": req_changed}


def apply(root=None, download=None, pip=None):
    """Update this copy to the newest version. download/pip are replaceable for tests."""
    root = root or paths.ROOT
    before = local_version(root)
    if before.get("git"):
        out = subprocess.run(["git", "-C", root, "pull", "--ff-only"], capture_output=True, text=True, timeout=120)
        if out.returncode:
            raise UpdateError("This copy has changes of its own, so it can't update itself: "
                              + (out.stderr or out.stdout).strip()[-300:])
        result = {"changed": ["(git pull)"], "requirements_changed": True}
    else:
        data = (download or (lambda: _get(f"https://codeload.github.com/{REPO}/zip/refs/heads/{BRANCH}")))()
        result = install_from_zip(data, root)
    if result["requirements_changed"]:
        run = pip or (lambda: subprocess.run([sys.executable, "-m", "pip", "install", "-r",
                                              os.path.join(root, "requirements.txt"), "--quiet"],
                                             capture_output=True, text=True, timeout=1800))
        out = run()
        if getattr(out, "returncode", 0):
            raise UpdateError("The new version is in place, but some of its parts didn't install. Close "
                              "Clip Studio and double-click Install again. " + (out.stderr or "")[-300:])
    return {**result, "from": before, "to": local_version(root)}
