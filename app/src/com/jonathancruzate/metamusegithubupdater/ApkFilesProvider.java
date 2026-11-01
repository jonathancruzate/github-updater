package com.jonathancruzate.metamusegithubupdater;

import android.content.ContentProvider;
import android.content.ContentValues;
import android.database.Cursor;
import android.database.MatrixCursor;
import android.net.Uri;
import android.os.ParcelFileDescriptor;
import android.provider.OpenableColumns;

import java.io.File;
import java.io.FileNotFoundException;

/**
 * Serves APKs downloaded into the app's updates folder to the
 * system package installer through a content:// URI (file://
 * intents are blocked on modern Android, and androidx's
 * FileProvider is not available to this no-Gradle build).
 * Only files inside the updates folder are reachable.
 */
public class ApkFilesProvider extends ContentProvider {

    public static final String AUTHORITY =
            "com.jonathancruzate.metamusegithubupdater.apkfiles";

    public static Uri uriFor(File file) {
        return Uri.parse("content://" + AUTHORITY + "/" + file.getName());
    }

    private File resolve(Uri uri) {
        if (getContext() == null) return null;
        File dir = new File(getContext().getExternalFilesDir(null), "updates");
        return new File(dir, uri.getLastPathSegment());
    }

    @Override
    public boolean onCreate() { return true; }

    @Override
    public ParcelFileDescriptor openFile(Uri uri, String mode)
            throws FileNotFoundException {
        File f = resolve(uri);
        if (f == null || !f.exists()) throw new FileNotFoundException(uri.toString());
        return ParcelFileDescriptor.open(f, ParcelFileDescriptor.MODE_READ_ONLY);
    }

    @Override
    public Cursor query(Uri uri, String[] projection, String selection,
                        String[] selectionArgs, String sortOrder) {
        File f = resolve(uri);
        MatrixCursor cursor = new MatrixCursor(new String[]{
                OpenableColumns.DISPLAY_NAME, OpenableColumns.SIZE});
        if (f != null && f.exists()) {
            cursor.addRow(new Object[]{f.getName(), f.length()});
        }
        return cursor;
    }

    @Override
    public String getType(Uri uri) {
        return "application/vnd.android.package-archive";
    }

    @Override
    public Uri insert(Uri uri, ContentValues values) { return null; }

    @Override
    public int delete(Uri uri, String selection, String[] selectionArgs) { return 0; }

    @Override
    public int update(Uri uri, ContentValues values, String selection,
                      String[] selectionArgs) { return 0; }
}
