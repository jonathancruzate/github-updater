# GitHub Updater — Muse's auto-updater

An app store whose customer is Muse. Our tooling runs on a set of
GitHub projects; this system keeps them **forked into this account,
downloaded from the projects' official releases, and auto-updated** —
no manual version chasing.

> This is agent infrastructure that runs on Muse's VM. It is not a
> phone app and has no Android component.

## How it works

- `registry.json` is the catalog: each project we use, where it
  lives, which release asset matters, and its install policy.
- `updater.py` (stdlib only) checks every project's latest release,
  downloads new assets into a local mirror with Content-Length,
  published-size, and archive-integrity verification — a truncated or
  mismatched download is rejected, never installed — then applies
  the project's policy:
  - **install-binary** — atomic swap into `~/.local/bin`, previous
    version kept as `.prev`, new binary must pass its own
    `--version` probe before the swap sticks. *(yt-dlp)*
  - **install-wheel** — the release wheel is pip-installed (matching
    how the tool is installed here), then probed. *(gallery-dl)*
  - **install-jadx** — versioned unpack; the live tools path is
    repointed only after the new jadx runs. *(jadx, the REA lane's
    decompiler)*
  - **mirror** — asset archived locally, nothing installed.
    *(our own published builds, e.g. Muse Widget)*
  - **watch** — new tags recorded only. *(REA, our source projects)*
- A daily scheduled run executes it (~05:22 ET). Routine updates are
  silent; only install failures surface. Run history accumulates in
  `UPDATES.md`; current state in `state.json` (local only).
- The unauthenticated GitHub API rate limit on a shared egress is
  easily exhausted, so GitHub tag checks fall back to the API-free
  `/releases/latest` redirect plus direct release downloads.

## Registry notes

- Third-party runtime projects are forked into this account
  (jadx, gallery-dl, yt-dlp, rea). Forks snapshot upstream at fork
  time; the updater tracks **upstream** releases, which is where
  updates actually come from.
- **gallery-dl** upstream moved active development to Codeberg
  (announced in its v1.32.18 release notes); its GitHub repo is a
  mirror, so it is tracked and downloaded from Codeberg.
- PixelBoard is deliberately **not** in this registry: a separate
  release-radar lane already watches it and installs its APKs.
  One watcher per stream.

## Files

| File | Purpose |
| --- | --- |
| `registry.json` | The catalog — projects, assets, install policies |
| `updater.py` | The engine — check, mirror, verify, install |
| `UPDATES.md` | Append-only log of everything it has done |
| `state.json` | Local run state (not published) |
