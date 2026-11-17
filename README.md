# GitHub Updater — Muse's auto-updater

An app store whose customer is Muse. Our tooling runs on a set of
projects — mostly GitHub projects, plus one web-distributed skill
(McClipFace) — and this system keeps them **forked into this
account, downloaded from the projects' official releases, and
auto-updated** — no manual version chasing.

> This is agent infrastructure that runs on Muse's VM. It also has
> an Android companion app (`app/`, package
> `com.jonathancruzate.metamusegithubupdater`) that surfaces the
> catalog on Jonathan's phone.

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
  - **install-npm** — on a new upstream tag, the tool's npm package
    is updated to `@latest` and the CLI probed; a failed probe
    rolls back to the previous version. *(REA)*
  - **install-tarball** — the release tarball is extracted under
    `installs/` (extraction is the install for a portable app);
    the VPS deploy uses that vetted copy. *(ArtCraft Launcher)*
  - **held-install** — asset mirrored and package-verified, the
    pending-install record refreshed, but never installed: the
    install is gated on Jonathan's decision. *(PixelBoard —
    retired; no registry project has used it since 2026-11-17)*
  - **mirror** — asset archived locally, nothing installed.
    *(our own published builds, e.g. Muse Widget)*
  - **watch** — new tags recorded only. *(our source projects,
    which ship no installable release artifact)*
  - **install-web-skill** — no forge release to track: the
    project's remote `SKILL.md` version is compared to the locally
    installed skill and a newer version is auto-installed
    (`SKILL.md` plus its `references/`, local install ID
    preserved, previous copy kept as `.prev`). *(McClipFace —
    auto-install is Jonathan's standing order of 2026-11-17,
    superseding the 2026-10-31 ask-first rule)*
- A daily scheduled run executes it (~05:22 ET). Everything
  auto-installs; routine updates are silent and install failures
  surface. Run history accumulates in `UPDATES.md`; current state
  in `state.json` (local only). Changes to the updater itself
  (registry, engine, this README) are pushed upstream to this
  repo as part of the same run.
- The unauthenticated GitHub API rate limit on a shared egress is
  easily exhausted, so GitHub tag checks fall back to the API-free
  `/releases/latest` redirect plus direct release downloads.

## Registry notes

- Third-party runtime projects are forked into this account
  (jadx, gallery-dl, yt-dlp, rea). Forks snapshot upstream at fork
  time; the updater tracks **upstream** releases, which is where
  updates actually come from.
- **McClipFace** is part of the Muse tooling catalog like the
  projects above — the coupon/deal skill Muse uses when
  shopping — but it is not a forge project: it is web-distributed,
  so its version source is the metadata of
  https://mcclipface.com/SKILL.md, checked against the locally
  installed skill under the **install-web-skill** policy. Since
  Jonathan's standing order of 2026-11-17 ("always auto install,
  update and upstream") it is auto-installed like everything
  else in the catalog; the 2026-10-31 ask-first rule is retired.
- **Craft Anything** was added 2026-11-01 as REA's craft/build
  companion. It is early alpha, and neither its source build nor
  its `craft-anything-cli setup` (which writes coding-agent
  configs) is ever run by the updater. Since the 2026-11-17
  always-auto-install order it sits under **mirror**: release
  assets download automatically when upstream ships them (no
  asset pattern is set yet, so nothing downloads until one is).
- **Craft Anything / ArtCraft Launcher** (added 2026-11-01, at
  Jonathan's order): the Craft suite sits alongside REA.
  Craft Anything is mirror (early alpha — see the note above).
  Its launcher, ArtCraft Launcher (`storytold/craft-launcher`),
  is cleared for use as needed on the Hetzner VPS and sits under
  **install-tarball** since 2026-11-17: new Linux x86_64 tarballs
  are extracted under `installs/` automatically, ready to deploy.
  Which of these tools (or REA) fits a given job is decided by
  Muse's `local-craft-software-selection` skill, which maps
  every Craft app to its medium and lane.
- **Muse AI Bypass** (`Git-FED/MuseAi-Bypass`) was added
  2026-11-08 at Jonathan's order under **watch**: a third-party
  catalog of official APIs / public feeds mined that day for
  routes Muse lacked (its finds were built into the public-data
  connector pack and the Stephany job crawler). It ships no
  releases or tags, so the watcher records "no releases" until
  upstream publishes one — the watch exists so a first release
  or a substantive upstream change is noticed, not missed.
- **gallery-dl** upstream moved active development to Codeberg
  (announced in its v1.32.18 release notes); its GitHub repo is a
  mirror, so it is tracked and downloaded from Codeberg.
- **PixelBoard** was consolidated into this registry on 2026-10-26
  (retiring the `github-release-radar` hook), then retired from it
  the same evening: Jonathan adopted **Obtainium** on the phone,
  which now owns PixelBoard (and Muse Widget) updates directly
  from GitHub releases — one watcher per stream, and the phone-side
  watcher is the one that can actually install. The held-install
  policy below served it during the consolidation; its install had
  been held for his decision since 2026-10-15, and the adb install
  path had been down since 2026-10-14.

## Files

| File | Purpose |
| --- | --- |
| `registry.json` | The catalog — projects, assets, install policies |
| `updater.py` | The engine — check, mirror, verify, install |
| `UPDATES.md` | Append-only log of everything it has done |
| `state.json` | Local run state (not published) |

## Install it with Muse

Just give this prompt to your Muse:

> "Install GitHub Updater from https://github.com/jonathancruzate/github-updater — read the README, install it, set it up for me, and show me how to use it."

