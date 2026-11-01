package com.jonathancruzate.metamusegithubupdater;

import android.app.Activity;
import android.app.DownloadManager;
import android.content.Context;
import android.content.Intent;
import android.content.pm.PackageInfo;
import android.content.pm.PackageManager;
import android.graphics.Typeface;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Environment;
import android.os.Handler;
import android.os.Looper;
import android.provider.Settings;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.TextView;
import android.widget.Toast;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/**
 * GitHub Updater: an app store for the GitHub projects we use.
 * The catalog is registry.json in jonathancruzate/github-updater
 * (fetched fresh each open; a bundled copy seeds the first run).
 * For each project it reads the latest GitHub Release; APK
 * projects compare against the installed package and offer a
 * one-tap download + install; tools offer the release asset as a
 * download; source projects link out to the repo.
 */
public class MainActivity extends Activity {

    private static final String REGISTRY_URL =
            "https://raw.githubusercontent.com/jonathancruzate/github-updater/main/registry.json";
    private static final String API = "https://api.github.com/repos/";

    private static final class Project {
        String id, name, repo, kind, pkg, assetMatch, note;
        // runtime state
        String tag, assetName, assetUrl;
        long assetSize;
        String installed;
        boolean noReleases;
        TextView statusView;
        Button button;
    }

    private final List<Project> projects = new ArrayList<>();
    private final ExecutorService net = Executors.newSingleThreadExecutor();
    private final Handler ui = new Handler(Looper.getMainLooper());
    private LinearLayout listView;
    private TextView statusLine;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_main);
        listView = findViewById(R.id.project_list);
        statusLine = findViewById(R.id.status_line);
        statusLine.setOnClickListener(new View.OnClickListener() {
            @Override public void onClick(View v) { refresh(); }
        });
        loadBundledRegistry();
        render();
        refresh();
    }

    @Override
    protected void onDestroy() {
        net.shutdownNow();
        super.onDestroy();
    }

    // ---- Registry ----

    private void loadBundledRegistry() {
        try {
            InputStream in = getResources().openRawResource(R.raw.registry);
            ByteArrayOutputStream buf = new ByteArrayOutputStream();
            byte[] chunk = new byte[4096];
            int n;
            while ((n = in.read(chunk)) > 0) buf.write(chunk, 0, n);
            in.close();
            parseRegistry(new JSONObject(buf.toString("UTF-8")));
        } catch (Exception ignored) {
            // The remote fetch in refresh() is the fallback.
        }
    }

    private void parseRegistry(JSONObject root) throws Exception {
        JSONArray arr = root.getJSONArray("projects");
        List<Project> out = new ArrayList<>();
        for (int i = 0; i < arr.length(); i++) {
            JSONObject o = arr.getJSONObject(i);
            Project p = new Project();
            p.id = o.optString("id");
            p.name = o.optString("name", p.id);
            p.repo = o.optString("repo");
            p.kind = o.optString("kind", "source");
            p.pkg = o.optString("package", "");
            p.assetMatch = o.optString("asset", "");
            p.note = o.optString("note", "");
            out.add(p);
        }
        projects.clear();
        projects.addAll(out);
    }

    private void refresh() {
        statusLine.setText(R.string.checking);
        net.execute(new Runnable() {
            @Override public void run() {
                String fresh = httpGet(REGISTRY_URL);
                if (fresh != null) {
                    try {
                        parseRegistry(new JSONObject(fresh));
                        ui.post(new Runnable() {
                            @Override public void run() { render(); }
                        });
                    } catch (Exception ignored) {}
                }
                for (final Project p : new ArrayList<>(projects)) {
                    fetchRelease(p);
                    ui.post(new Runnable() {
                        @Override public void run() { updateCard(p); }
                    });
                }
                ui.post(new Runnable() {
                    @Override public void run() {
                        statusLine.setText(projects.size() + " projects · tap to refresh");
                    }
                });
            }
        });
    }

    private void fetchRelease(Project p) {
        String body = httpGet(API + p.repo + "/releases/latest");
        if (body == null) { p.noReleases = true; return; }
        try {
            JSONObject rel = new JSONObject(body);
            p.tag = rel.optString("tag_name", "");
            JSONArray assets = rel.optJSONArray("assets");
            if (assets != null) {
                for (int i = 0; i < assets.length(); i++) {
                    JSONObject a = assets.getJSONObject(i);
                    String name = a.optString("name", "");
                    if (p.assetMatch.isEmpty() || name.contains(p.assetMatch)) {
                        p.assetName = name;
                        p.assetUrl = a.optString("browser_download_url", "");
                        p.assetSize = a.optLong("size", 0);
                        if (!p.assetMatch.isEmpty()) break;
                    }
                }
            }
        } catch (Exception e) {
            p.noReleases = true;
        }
    }

    // ---- Rendering (One UI: cards, 24dp margins, bottom counter) ----

    private void render() {
        listView.removeAllViews();
        for (final Project p : projects) {
            LinearLayout card = new LinearLayout(this);
            card.setOrientation(LinearLayout.VERTICAL);
            card.setBackgroundResource(R.drawable.bg_card);
            card.setPadding(dp(16), dp(14), dp(16), dp(14));
            LinearLayout.LayoutParams cardLp = new LinearLayout.LayoutParams(
                    ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
            cardLp.bottomMargin = dp(12);
            card.setLayoutParams(cardLp);

            TextView name = new TextView(this);
            name.setText(p.name);
            name.setTextSize(17);
            name.setTypeface(Typeface.DEFAULT_BOLD);
            name.setTextColor(getColor(R.color.foreground));
            card.addView(name);

            TextView meta = new TextView(this);
            meta.setText(p.repo + (p.note.isEmpty() ? "" : "\n" + p.note));
            meta.setTextSize(13);
            meta.setTextColor(getColor(R.color.secondary));
            meta.setPadding(0, dp(2), 0, 0);
            card.addView(meta);

            p.statusView = new TextView(this);
            p.statusView.setTextSize(14);
            p.statusView.setPadding(0, dp(8), 0, 0);
            card.addView(p.statusView);

            p.button = new Button(this);
            p.button.setBackgroundResource(R.drawable.bg_button_primary);
            p.button.setTextColor(getColor(R.color.on_primary));
            p.button.setAllCaps(false);
            LinearLayout.LayoutParams btnLp = new LinearLayout.LayoutParams(
                    ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT);
            btnLp.gravity = Gravity.END;
            btnLp.topMargin = dp(10);
            p.button.setLayoutParams(btnLp);
            p.button.setOnClickListener(new View.OnClickListener() {
                @Override public void onClick(View v) { onAction(p); }
            });
            card.addView(p.button);

            updateCard(p);
            listView.addView(card);
        }
    }

    private void updateCard(Project p) {
        if (p.statusView == null) return;
        p.installed = installedVersion(p);
        String status;
        String action = null;
        boolean enabled = true;
        if ("apk".equals(p.kind)) {
            if (p.noReleases) {
                status = "No release published yet";
                enabled = false;
            } else if (p.tag == null || p.tag.isEmpty()) {
                status = "Checking…";
                enabled = false;
            } else if (p.installed == null) {
                status = "Latest: " + p.tag + " · not installed";
                action = "Install";
            } else if (compareVersions(p.installed, stripV(p.tag)) < 0) {
                status = "Update available: " + p.installed + " → " + p.tag;
                action = "Update";
            } else {
                status = "Installed " + p.installed + " · latest " + p.tag;
                action = "Up to date";
                enabled = false;
            }
        } else if ("tool".equals(p.kind)) {
            if (p.noReleases) {
                status = "No releases";
                action = "Open repo";
            } else {
                status = "Latest release: " + p.tag
                        + (p.assetName != null ? " · " + p.assetName : "");
                action = p.assetUrl != null ? "Download" : "Open repo";
            }
        } else {
            status = p.noReleases ? "Source project · no releases"
                    : "Source project · latest " + p.tag;
            action = "Open repo";
        }
        p.statusView.setText(status);
        p.statusView.setTextColor(getColor(enabled && action != null
                && !"Up to date".equals(action)
                ? R.color.primary_dark : R.color.secondary));
        p.button.setText(action != null ? action : "…");
        p.button.setEnabled(enabled && action != null);
        p.button.setVisibility(action != null ? View.VISIBLE : View.GONE);
    }

    // ---- Actions ----

    private void onAction(final Project p) {
        if ("apk".equals(p.kind) && p.assetUrl != null) {
            downloadAndInstall(p);
        } else if ("tool".equals(p.kind) && p.assetUrl != null) {
            DownloadManager dm = (DownloadManager) getSystemService(DOWNLOAD_SERVICE);
            DownloadManager.Request req = new DownloadManager.Request(Uri.parse(p.assetUrl));
            req.setTitle(p.assetName);
            req.setNotificationVisibility(
                    DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED);
            req.setDestinationInExternalPublicDir(Environment.DIRECTORY_DOWNLOADS, p.assetName);
            dm.enqueue(req);
            Toast.makeText(this, "Downloading " + p.assetName, Toast.LENGTH_SHORT).show();
        } else {
            startActivity(new Intent(Intent.ACTION_VIEW,
                    Uri.parse("https://github.com/" + p.repo)));
        }
    }

    private void downloadAndInstall(final Project p) {
        if (Build.VERSION.SDK_INT >= 26
                && !getPackageManager().canRequestPackageInstalls()) {
            Intent settings = new Intent(Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES,
                    Uri.parse("package:" + getPackageName()));
            startActivity(settings);
            Toast.makeText(this, "Allow installs for GitHub Updater, then tap Update again",
                    Toast.LENGTH_LONG).show();
            return;
        }
        p.button.setEnabled(false);
        net.execute(new Runnable() {
            @Override public void run() {
                File dir = new File(getExternalFilesDir(null), "updates");
                dir.mkdirs();
                final File out = new File(dir, p.id + ".apk");
                boolean ok = download(p.assetUrl, out, p);
                ui.post(new Runnable() {
                    @Override public void run() {
                        updateCard(p);
                    }
                });
                if (!ok) {
                    ui.post(new Runnable() {
                        @Override public void run() {
                            Toast.makeText(MainActivity.this, "Download failed",
                                    Toast.LENGTH_SHORT).show();
                        }
                    });
                    return;
                }
                Intent install = new Intent(Intent.ACTION_VIEW);
                install.setDataAndType(ApkFilesProvider.uriFor(out),
                        "application/vnd.android.package-archive");
                install.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION
                        | Intent.FLAG_ACTIVITY_NEW_TASK);
                startActivity(install);
            }
        });
    }

    private boolean download(String url, File out, final Project p) {
        HttpURLConnection conn = null;
        try {
            conn = (HttpURLConnection) new URL(url).openConnection();
            conn.setConnectTimeout(10000);
            conn.setReadTimeout(30000);
            conn.setRequestProperty("User-Agent", "GitHub-Updater");
            InputStream in = conn.getInputStream();
            long total = conn.getContentLengthLong();
            FileOutputStream fos = new FileOutputStream(out);
            byte[] chunk = new byte[65536];
            long done = 0;
            int n;
            while ((n = in.read(chunk)) > 0) {
                fos.write(chunk, 0, n);
                done += n;
                if (total > 0) {
                    final int pct = (int) (done * 100 / total);
                    ui.post(new Runnable() {
                        @Override public void run() {
                            if (p.statusView != null) {
                                p.statusView.setText("Downloading… " + pct + "%");
                            }
                        }
                    });
                }
            }
            fos.close();
            in.close();
            return out.length() > 0;
        } catch (Exception e) {
            return false;
        } finally {
            if (conn != null) conn.disconnect();
        }
    }

    // ---- Helpers ----

    private String httpGet(String url) {
        HttpURLConnection conn = null;
        try {
            conn = (HttpURLConnection) new URL(url).openConnection();
            conn.setConnectTimeout(8000);
            conn.setReadTimeout(8000);
            conn.setRequestProperty("User-Agent", "GitHub-Updater");
            conn.setRequestProperty("Accept", "application/vnd.github+json");
            InputStream in = conn.getInputStream();
            ByteArrayOutputStream buf = new ByteArrayOutputStream();
            byte[] chunk = new byte[8192];
            int n;
            while ((n = in.read(chunk)) > 0) buf.write(chunk, 0, n);
            in.close();
            return buf.toString("UTF-8");
        } catch (Exception e) {
            return null;
        } finally {
            if (conn != null) conn.disconnect();
        }
    }

    private String installedVersion(Project p) {
        if (p.pkg == null || p.pkg.isEmpty()) return null;
        try {
            PackageInfo info = getPackageManager().getPackageInfo(p.pkg, 0);
            return info.versionName;
        } catch (PackageManager.NameNotFoundException e) {
            return null;
        }
    }

    private static String stripV(String tag) {
        return tag != null && tag.startsWith("v") ? tag.substring(1) : tag;
    }

    /** Dotted-numeric compare; non-numeric parts compare as text. */
    private static int compareVersions(String a, String b) {
        if (a == null || b == null) return 0;
        String[] pa = a.split("\\."), pb = b.split("\\.");
        for (int i = 0; i < Math.max(pa.length, pb.length); i++) {
            String x = i < pa.length ? pa[i] : "0";
            String y = i < pb.length ? pb[i] : "0";
            int cmp;
            try {
                cmp = Integer.compare(Integer.parseInt(x), Integer.parseInt(y));
            } catch (NumberFormatException e) {
                cmp = x.compareTo(y);
            }
            if (cmp != 0) return cmp;
        }
        return 0;
    }

    private int dp(int v) {
        return Math.round(v * getResources().getDisplayMetrics().density);
    }
}
