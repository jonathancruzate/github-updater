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
  held-install    asset mirrored and package-verified, and the
                  pending-install record refreshed — but never
                  installed: the install is gated on Jonathan's
                  decision (PixelBoard, held since 2026-10-15).
                  No registry project uses it since 2026-11-17.
  install-npm     npm-managed tool (REA): on a new upstream tag,
                  `npm install -g <pkg>@latest`, then the CLI is
                  probed; a failed probe rolls back to the
                  previously installed version.
  install-tarball release tarball extracted under installs/
                  (ArtCraft Launcher — extraction is its install;
                  the VPS desktop deploy uses that vetted copy).
  watch           tag recorded only (our source projects, which
                  ship no installable release artifact).
  install-web-skill
                  no forge release to track: the project's remote
                  SKILL.md version is compared to the locally
                  installed skill and a newer version is INSTALLED
                  automatically — SKILL.md plus its references/,
                  the local install ID preserved, the previous
                  copy kept as .prev. (McClipFace; auto-install is
                  Jonathan's standing order of 2026-11-17,
                  superseding the 2026-10-31 ask-first rule.)

State lives in state.json; every action appends a line to
UPDATES.md and prints a summary line. Designed to run unattended
from cron: per-project failures are logged and never abort the run.
"""
import hashlib
import io
import json
import os
import re
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


def parse_skill_version(text):
    """Version from a skill file's frontmatter metadata
    (`version: "x.y.z"`). The text is data only — it is parsed,
    never executed or saved."""
    for line in text.splitlines():
        m = re.match(r'\s*version:\s*["\']?([0-9][0-9A-Za-z.\-]*)', line)
        if m:
            return m.group(1)
    return None


def version_key(version):
    return tuple(int(n) for n in re.findall(r"\d+", version or ""))


def local_skill_version(path):
    try:
        with open(os.path.expanduser(path)) as f:
            return parse_skill_version(f.read())
    except Exception:
        return None


def install_web_skill(project, remote_text, latest, installed):
    """install-web-skill (McClipFace): save the remote SKILL.md over
    the locally installed skill, plus its references/ files, keeping
    the local install ID. The previous SKILL.md is kept as
    SKILL.md.prev and the previous references/ as references.prev/
    for rollback. The fetched text is data — written as the skill
    file, never executed by the updater. Auto-install is Jonathan's
    standing order of 2026-11-17."""
    local_path = os.path.expanduser(project["local_path"])
    skill_dir = os.path.dirname(local_path)
    local_text = ""
    if os.path.exists(local_path):
        with open(local_path) as f:
            local_text = f.read()
    m = re.search(r"client_install_id=([0-9a-fA-F\-]{36})", local_text)
    install_id = m.group(1) if m else None
    new_text = remote_text
    if install_id:
        new_text = re.sub(r"client_install_id=[0-9a-fA-F\-]{36}",
                          "client_install_id=" + install_id, new_text)
    new_text = re.sub(r"client_skill_version=[0-9][0-9A-Za-z.\-]*",
                      "client_skill_version=" + latest, new_text)
    # Fetch every references/<file> the new SKILL.md names (fall back
    # to the locally installed set) BEFORE touching the local copy:
    # a failed fetch aborts the install with the old skill intact.
    ref_names = sorted(set(re.findall(r"references/([A-Za-z0-9._\-]+\.md)",
                                      new_text)))
    if not ref_names and os.path.isdir(os.path.join(skill_dir, "references")):
        ref_names = sorted(os.listdir(os.path.join(skill_dir, "references")))
    base = project["skill_url"].rsplit("/", 1)[0]
    refs = {}
    for name in ref_names:
        refs[name] = http_get(base + "/references/" + name).decode(
            "utf-8", "replace")
    if os.path.exists(local_path):
        shutil.copy2(local_path, local_path + ".prev")
    ref_dir = os.path.join(skill_dir, "references")
    if refs and os.path.isdir(ref_dir):
        prev_ref = ref_dir + ".prev"
        if os.path.exists(prev_ref):
            shutil.rmtree(prev_ref)
        shutil.copytree(ref_dir, prev_ref)
    with open(local_path, "w") as f:
        f.write(new_text)
    if refs:
        os.makedirs(ref_dir, exist_ok=True)
        for name, text in refs.items():
            with open(os.path.join(ref_dir, name), "w") as f:
                f.write(text)
    if local_skill_version(project["local_path"]) != latest:
        if os.path.exists(local_path + ".prev"):
            shutil.copy2(local_path + ".prev", local_path)
        return ("install FAILED (post-install version probe mismatch — "
                "previous skill restored)")
    # Newer skill versions read the install ID from a CONFIG.md in
    # the skill folder instead of the SKILL.md text. If this install
    # predates that (ID only embedded in the old SKILL.md) and no
    # CONFIG.md exists yet, seed one with the preserved ID so the
    # same install identity carries across the update.
    config_path = os.path.join(skill_dir, "CONFIG.md")
    if install_id and not os.path.exists(config_path):
        with open(config_path, "w") as f:
            f.write("install_id: %s\ncommunity_mode: false\n" % install_id)
    return ("installed skill %s (was %s; SKILL.md + %d reference "
            "file(s), install ID preserved, previous kept as .prev)"
            % (latest, installed or "none", len(refs)))


def check_web_skill(project, state):
    """Web-distributed skill (McClipFace): the project is not a
    forge release — its version source is a remote SKILL.md. Fetch
    it, compare its metadata version to the locally installed
    skill, and record the result. Under install-web-skill a newer
    remote version is installed automatically (Jonathan's order,
    2026-11-17); under the legacy watch-web-skill policy it is
    only recorded."""
    pid = project["id"]
    installed = local_skill_version(project["local_path"])
    remote_text = http_get(project["skill_url"]).decode("utf-8", "replace")
    latest = parse_skill_version(remote_text)
    if not latest:
        raise ValueError("no version found in remote SKILL.md")
    prev = state.get(pid, {})
    if prev.get("installed") == installed and prev.get("latest") == latest:
        return  # already recorded — stay silent like the other watchers
    if installed and version_key(latest) > version_key(installed):
        if project.get("policy") == "install-web-skill":
            action = install_web_skill(project, remote_text, latest,
                                       installed)
            installed = local_skill_version(project["local_path"])
        else:
            action = ("UPDATE AVAILABLE — installed %s, latest %s; "
                      "install held (watch only)" % (installed, latest))
    elif installed and version_key(installed) > version_key(latest):
        action = ("watched — installed %s is ahead of remote %s "
                  "(no action)" % (installed, latest))
    elif installed:
        action = "watched — up to date (installed %s)" % installed
    else:
        action = "watched — not installed locally; remote is %s" % latest
    state[pid] = {"tag": latest, "installed": installed,
                  "latest": latest, "checked": now(), "action": action}
    log("%s: %s — %s" % (pid, latest, action))


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


def pick_asset(rel, match, tag="", fallback=""):
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
        if fallback and "{" in fallback:
            return {"name": render_asset_name(fallback, tag),
                    "browser_download_url": None}
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


def verify_package(apk_path, package):
    """Radar-style package check: the binary manifest carries the
    package name as UTF-16LE strings."""
    try:
        with zipfile.ZipFile(apk_path) as z:
            data = z.read("AndroidManifest.xml")
        return (package.encode("utf-16-le") in data
                or package in data.decode("utf-16-le", "ignore"))
    except Exception:
        return False


def install_held(project, file_path, tag, sha):
    """For installs gated on Jonathan's decision (PixelBoard, held
    since 2026-10-15): verify the package, mirror the APK, and
    refresh the pending-install record. Never installs."""
    if project.get("package") and not verify_package(file_path, project["package"]):
        return "HELD-INSTALL FAILED (package mismatch — not recorded as pending)"
    record = {"project": project["id"], "tag": tag, "apk": file_path,
              "apk_path": file_path,
              "sha256": sha, "package": project.get("package"),
              "status": "held",
              "note": ("Install held pending Jonathan's Install/Skip decision "
                       "(standing since 2026-10-15); adb link also down "
                       "since 2026-10-14."),
              "recorded": now()}
    pdir = os.path.join(HERE, "pending")
    os.makedirs(pdir, exist_ok=True)
    with open(os.path.join(pdir, project["id"] + ".json"), "w") as f:
        json.dump(record, f, indent=2)
    legacy = os.path.expanduser("~/hooks/state/pixelboard-pending-install.json")
    try:
        with open(legacy, "w") as f:
            json.dump(record, f, indent=2)
    except Exception:
        pass
    return ("downloaded + package-verified; install HELD for Jonathan's "
            "decision (pending record refreshed)")


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


def install_npm(project):
    """install-npm (REA): the tool is an npm global package, so a
    new upstream release tag triggers `npm install -g <pkg>@latest`
    and a CLI version probe. A failed probe rolls back to the
    previously installed version."""
    pkg = project["npm_package"]
    bin_path = os.path.expanduser("~/.npm-global/bin/" + project["bin_name"])
    before = run_version([bin_path, "--version"]) if os.path.exists(bin_path) else ""
    before_num = None
    if before and "ERROR" not in before:
        m = re.search(r"(\d+\.\d+\.\d+)", before)
        before_num = m.group(1) if m else None
    proc = subprocess.run(["npm", "install", "-g", pkg + "@latest"],
                          capture_output=True, text=True, timeout=900)
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout).strip().splitlines()
        return "install FAILED (npm: %s)" % (tail[-1] if tail else "unknown error")
    ver = run_version([bin_path, "--version"])
    if not ver or "ERROR" in ver:
        note = ""
        if before_num:
            rb = subprocess.run(["npm", "install", "-g",
                                 pkg + "@" + before_num],
                                capture_output=True, text=True, timeout=900)
            note = ("; rolled back to %s" % before_num
                    if rb.returncode == 0 else "; ROLLBACK ALSO FAILED")
        return "install FAILED (post-install probe returned %r)%s" % (ver, note)
    return "installed %s %s via npm (was %s)" % (pkg, ver, before or "not installed")


def install_tarball(project, file_path, tag):
    """install-tarball (ArtCraft Launcher): extraction under
    installs/ IS the install for a portable tarball app — the
    previous extraction is kept as .prev, and the VPS desktop
    deploy uses this vetted copy."""
    target = os.path.join(INSTALLS, project["id"] + "-" + tag.lstrip("v"))
    prev = target + ".prev"
    if os.path.exists(prev):
        shutil.rmtree(prev)
    if os.path.exists(target):
        os.rename(target, prev)
    os.makedirs(target)
    with tarfile.open(file_path) as t:
        t.extractall(target)
    return ("installed (extracted) %s %s at %s (previous kept as .prev)"
            % (project["id"], tag, target))


def main():
    with open(REGISTRY) as f:
        registry = json.load(f)
    state = {}
    if os.path.exists(STATE):
        with open(STATE) as f:
            state = json.load(f)
    os.makedirs(MIRROR, exist_ok=True)
    os.makedirs(INSTALLS, exist_ok=True)
    only = None
    if "--only" in sys.argv:
        i = sys.argv.index("--only")
        if i + 1 < len(sys.argv):
            only = sys.argv[i + 1]

    for project in registry["projects"]:
        pid = project["id"]
        if only and pid != only:
            continue
        repo = project.get("repo")
        policy = project.get("policy", "watch")
        try:
            if policy in ("watch-web-skill", "install-web-skill"):
                check_web_skill(project, state)
                continue
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
            if policy == "install-npm":
                entry["action"] = install_npm(project)
                state[pid] = entry
                log("%s: %s — %s" % (pid, tag, entry["action"]))
                continue
            asset = pick_asset(rel, project.get("asset", ""), tag,
                               project.get("asset_fallback", ""))
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
            elif policy == "held-install":
                entry["action"] = install_held(project, dest, tag, sha)
            elif policy == "install-jadx":
                entry["action"] = install_jadx(project, dest, tag)
            elif policy == "install-tarball":
                entry["action"] = install_tarball(project, dest, tag)
            state[pid] = entry
            log("%s: %s — %s" % (pid, tag, entry["action"]))
        except Exception as e:
            log("%s: ERROR %s" % (pid, e))
    with open(STATE, "w") as f:
        json.dump(state, f, indent=2)
    print("updater run complete", flush=True)


if __name__ == "__main__":
    main()
