# GitHub Updater — Android companion app

The on-phone half of the GitHub Updater system: a small app that
serves downloaded release APKs to the Android package installer
(via `ApkFilesProvider`) so Muse's tracked projects can be
installed/updated on Jonathan's phone. Build with `./build.sh`
(raw javac/d8/aapt2; shared SDK via `ANDROID_SDK_ROOT` if the
local `tools/sdk` link is absent). Signed with the shared Meta
Muse keystore (`keystore/metamuse.keystore`).

## v1.1.0 (2026-11-01, versionCode 2) — package aligned

Package renamed `com.jonathancruzate.githubupdater` →
**`com.jonathancruzate.metamusegithubupdater`** to join the
family scheme (`com.jonathancruzate.metamuse<product>`) used by
all of Jonathan's apps, per his 2026-11-01 order. The APK
filename (`GitHub-Updater-1.1.0.apk`) and the app label
("GitHub Updater") are unchanged.

This is a **reinstall**, not an upgrade: uninstall the old app
first, then install v1.1.0. Afterwards, the updater's
tracked-apps / Obtainium setup on the phone may need the new
app re-pointed (the old package no longer exists).
