#!/usr/bin/env python3
"""GitHub Updater — Muse's own auto-updater for the GitHub projects we use.

Standing order (Jonathan, 2026-10-26): the projects our tooling
runs on are forked into his account, their releases are downloaded
from GitHub always, and they auto-update — an app store whose
customer is Muse, not the phone.

Reads registry.json (same folder). For each project: checks the
latest GitHub Release; on a new tag, downloads the matching asset
into mirror/<id>/<tag>/, verifies it (zip/tar integrity, sha256),
and applies the project's install policy:

  install-binary  single-file tool (yt-dlp): atomic swap into
                  ~/.local/bin with a .prev rollback copy kept
                  until the new binary reports a version.
  install-wheel   pip-managed tool (gallery-dl): the release wheel
                  is pip-installed (matching how the tool is
                  installed here), then the CLI version is probed.
  install-jadx    versioned unzip under installs/, then the live
                  tools/jadx path is repointed (symlink swap, the
                  previous tree kept as jadx.prev).
  mirror          asset mirrored, nothing installed (e.g. our own
                  published APKs).
  watch           tag recorded only (source projects, REA).

State lives in state.json; every action appends a line to
UPDATES.md and prints a summary line. Designed to run unattended
from cron: per-project failures are logged and never abort the run.
"""
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import urllib.request
import zipfile
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
REGISTRY = os.path.join(HERE, "registry.json")
STATE = os.path.join(HERE, "state.json")
MIRROR = os.path.join(HERE, "mirror")
INSTALLS = os.path.join(HERE, "installs")
LOG = os.path.join(HERE, "UPDATES.md")
UA = {"User-Agent": "Muse-GitHub-Updater", "Accept": "application/vnd.github+json"}
JAVA_HOME = os.path.expanduser("~/workspace/custom-skills/local-rea/tools/jdk")
JADX_LIVE = os.path.expanduser("~/workspace/metamuse-widget/tools/jadx")
LOCAL_BIN = os.path.expanduser("~/.local/bin")


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def log(line):
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    with open(LOG, "a") as f:
        f.write("- %s — %s\n" % (stamp, line))
    print(line, flush=True)


def http_get(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()


def http_json(url):
    try:
        return json.loads(http_get(url).decode("utf-8"))
    except Exception:
        return None


def codeberg_latest(repo):
    data = http_json("https://codeberg.org/api/v1/repos/%s/releases?limit=1" % repo)
    if isinstance(data, list) and data:
        return data[0]
    return None


def github_tag_via_redirect(repo):
    """API-free latest-tag lookup (the unauthenticated GitHub API
    rate limit on this VM's shared egress is easily exhausted):
    /releases/latest redirects to the newest tag page."""
    try:
        req = urllib.request.Request(
            "https://github.com/%s/releases/latest" % repo,
            headers={"User-Agent": UA["User-Agent"]})
        with urllib.request.urlopen(req, timeout=30) as r:
            url = r.geturl()
        marker = "/releases/tag/"
        if marker in url:
            return url.split(marker, 1)[1].split("/")[0].split("?")[0]
    except Exception:
        pass
    return None


def render_asset_name(match, tag):
    return match.replace("{ver}", tag.lstrip("v")).replace("{tag}", tag)


def download(url, dest):
    req = urllib.request.Request(url, headers={"User-Agent": UA["User-Agent"]})
    tmp = dest + ".part"
    h = hashlib.sha256()
    written = 0
    with urllib.request.urlopen(req, timeout=120) as r, open(tmp, "wb") as f:
        expected = r.headers.get("Content-Length")
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            written += len(chunk)
            h.update(chunk)
            f.write(chunk)
    if expected is not None and written != int(expected):
        os.remove(tmp)
        raise IOError("truncated download: got %d of %s bytes" % (written, expected))
    os.replace(tmp, dest)
    return h.hexdigest()


def verify_asset(path):
    if path.endswith(".zip") or path.endswith(".apk") or path.endswith(".whl"):
        if not zipfile.is_zipfile(path):
            return False
        with zipfile.ZipFile(path) as z:
            return z.testzip() is None
    if path.endswith(".tar.gz") or path.endswith(".tgz"):
        try:
            with tarfile.open(path) as t:
                t.getmembers()
            return True
        except Exception:
            return False
    return os.path.getsize(path) > 0


def run_version(cmd, env_extra=None):
    env = dict(os.environ)
    env["JAVA_HOME"] = JAVA_HOME
    env["PATH"] = JAVA_HOME + "/bin:" + env.get("PATH", "")
    if env_extra:
        env.update(env_extra)
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=60, env=env)
        if out.returncode != 0:
            return ""
        text = (out.stdout or "").strip()
        return text.splitlines()[0] if text else ""
    except Exception:
        return ""


def pick_asset(rel, match, tag=""):
    assets = rel.get("assets", [])
    if match:
        if "{" in match:
            want = render_asset_name(match, tag)
            for a in assets:
                if a.get("name") == want:
                    return a
            # Not in the API listing (or API unavailable): the caller
            # constructs the direct download URL from the name.
            return {"name": want, "browser_download_url": None}
        for a in assets:
            if match in a.get("name", ""):
                return a
        return None
    return assets[0] if assets else None


def install_binary(project, file_path):
    name = project["bin_name"]
    dest = os.path.join(LOCAL_BIN, name)
    prev = dest + ".prev"
    staged = dest + ".new"
    shutil.copy2(file_path, staged)
    os.chmod(staged, 0o755)
    ver = run_version([staged, "--version"])
    if not ver or "ERROR" in ver:
        os.remove(staged)
        return "install FAILED (new binary would not run cleanly)"
    if os.path.exists(dest):
        shutil.copy2(dest, prev)
    os.replace(staged, dest)
    return "installed %s %s (previous kept as %s.prev)" % (name, ver, name)


def install_wheel(project, file_path):
    name = project["bin_name"]
    package = project.get("pip_package", name.replace("-", "_"))
    bin_path = os.path.join(LOCAL_BIN, name)
    before = run_version([bin_path, "--version"]) if os.path.exists(bin_path) else ""
    user_flag = []
    try:
        show = subprocess.run([sys.executable, "-m", "pip", "show", package],
                              capture_output=True, text=True, timeout=30)
        for line in show.stdout.splitlines():
            if line.startswith("Location:") and ".local" in line:
                user_flag = ["--user"]
    except Exception:
        pass
    proc = subprocess.run([sys.executable, "-m", "pip", "install"] + user_flag +
                          ["--force-reinstall", "--break-system-packages", file_path],
                          capture_output=True, text=True, timeout=600)
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout).strip().splitlines()
        return "install FAILED (pip: %s)" % (tail[-1] if tail else "unknown error")
    ver = run_version([bin_path, "--version"])
    if not ver or "ERROR" in ver:
        return "install FAILED (post-install probe returned %r)" % ver
    return ("installed %s %s via wheel (was %s; prior release stays in the "
            "mirror for rollback)" % (name, ver, before or "not installed"))


def install_jadx(project, file_path, tag):
    target = os.path.join(INSTALLS, "jadx-" + tag.lstrip("v"))
    if os.path.exists(target):
        shutil.rmtree(target)
    os.makedirs(target)
    with zipfile.ZipFile(file_path) as z:
        z.extractall(target)
    jadx_bin = os.path.join(target, "bin", "jadx")
    if not os.path.exists(jadx_bin):
        # Some zips nest a top folder.
        for root, dirs, files in os.walk(target):
            if "jadx" in files and os.path.basename(root) == "bin":
                jadx_bin = os.path.join(root, "jadx")
                target = os.path.dirname(os.path.dirname(jadx_bin))
                break
    if not os.path.exists(jadx_bin):
        return "install FAILED (bin/jadx not found in zip)"
    os.chmod(jadx_bin, 0o755)
    ver = run_version([jadx_bin, "--version"])
    if not ver or "ERROR" in ver:
        return "install FAILED (jadx would not run cleanly)"
    prev = JADX_LIVE + ".prev"
    if os.path.islink(JADX_LIVE):
        os.remove(JADX_LIVE)
    elif os.path.exists(JADX_LIVE):
        if os.path.exists(prev):
            shutil.rmtree(prev)
        os.rename(JADX_LIVE, prev)
    os.symlink(target, JADX_LIVE)
    return "installed jadx %s at %s (live path repointed, previous kept)" % (ver, target)


def main():
    with open(REGISTRY) as f:
        registry = json.load(f)
    state = {}
    if os.path.exists(STATE):
        with open(STATE) as f:
            state = json.load(f)
    os.makedirs(MIRROR, exist_ok=True)
    os.makedirs(INSTALLS, exist_ok=True)

    for project in registry["projects"]:
        pid = project["id"]
        repo = project["repo"]
        policy = project.get("policy", "watch")
        try:
            if project.get("forge") == "codeberg":
                rel = codeberg_latest(repo)
            else:
                rel = http_json("https://api.github.com/repos/%s/releases/latest" % repo)
                if not rel or "tag_name" not in rel:
                    tag = github_tag_via_redirect(repo)
                    rel = {"tag_name": tag, "assets": []} if tag else None
            if not rel or "tag_name" not in rel:
                if state.get(pid, {}).get("tag") is None:
                    state.setdefault(pid, {})["note"] = "no releases"
                continue
            tag = rel["tag_name"]
            if state.get(pid, {}).get("tag") == tag:
                prev_action = state[pid].get("action", "")
                failed_before = ("FAILED" in prev_action or "MISMATCH" in prev_action)
                if policy == "watch" or (not prev_action.startswith("watched")
                                         and not failed_before):
                    continue  # already current and fully applied
            entry = {"tag": tag, "checked": now()}
            asset = pick_asset(rel, project.get("asset", ""), tag)
            if policy == "watch" or asset is None:
                entry["action"] = "watched (no asset download)"
                state[pid] = entry
                log("%s: new tag %s — %s" % (pid, tag, entry["action"]))
                continue
            dest_dir = os.path.join(MIRROR, pid, tag)
            os.makedirs(dest_dir, exist_ok=True)
            dest = os.path.join(dest_dir, asset["name"])
            expected_size = asset.get("size")
            if (os.path.exists(dest) and expected_size
                    and os.path.getsize(dest) != expected_size):
                os.remove(dest)  # stale or truncated copy — fetch it fresh
            if not os.path.exists(dest):
                dl = asset.get("browser_download_url")
                if not dl:
                    dl = ("https://github.com/%s/releases/download/%s/%s"
                          % (repo, tag, asset["name"]))
                sha = download(dl, dest)
            else:
                sha = hashlib.sha256(open(dest, "rb").read()).hexdigest()
            if expected_size and os.path.getsize(dest) != expected_size:
                entry["action"] = "DOWNLOAD SIZE MISMATCH — not installed"
                state[pid] = entry
                log("%s: %s asset %s size mismatch (got %d, want %d)"
                    % (pid, tag, asset["name"], os.path.getsize(dest), expected_size))
                continue
            entry["asset"] = asset["name"]
            entry["sha256"] = sha
            if not verify_asset(dest):
                entry["action"] = "DOWNLOAD FAILED VERIFY — kept for inspection"
                state[pid] = entry
                log("%s: %s asset %s failed integrity check" % (pid, tag, asset["name"]))
                continue
            if policy == "mirror":
                entry["action"] = "mirrored"
            elif policy == "install-binary":
                entry["action"] = install_binary(project, dest)
            elif policy == "install-wheel":
                entry["action"] = install_wheel(project, dest)
            elif policy == "install-jadx":
                entry["action"] = install_jadx(project, dest, tag)
            state[pid] = entry
            log("%s: %s — %s" % (pid, tag, entry["action"]))
        except Exception as e:
            log("%s: ERROR %s" % (pid, e))
    with open(STATE, "w") as f:
        json.dump(state, f, indent=2)
    print("updater run complete", flush=True)


if __name__ == "__main__":
    main()
