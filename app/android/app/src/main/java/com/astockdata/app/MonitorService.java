package com.astockdata.app;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Intent;
import android.content.SharedPreferences;
import android.os.Build;
import android.os.Handler;
import android.os.IBinder;
import android.os.Looper;

import androidx.core.app.NotificationCompat;

import org.json.JSONObject;

import java.io.File;
import java.io.FileInputStream;

public class MonitorService extends Service {

    private static final String CHANNEL_ID = "astock_monitor";
    private static final int NOTIFICATION_ID = 1;
    private static final String PREFS = "astock_monitor_prefs";
    private static final String KEY_LAST_TS = "last_notify_ts";

    private final Handler handler = new Handler(Looper.getMainLooper());
    private long lastTs = 0;

    private final Runnable pollTask = new Runnable() {
        @Override
        public void run() {
            checkSignalFile();
            handler.postDelayed(this, 20000);
        }
    };

    @Override
    public void onCreate() {
        super.onCreate();
        createChannel();
        lastTs = getSharedPreferences(PREFS, MODE_PRIVATE).getLong(KEY_LAST_TS, 0);
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        startForeground(NOTIFICATION_ID, buildNotification("A股后台服务运行中", "保持后台监控能力，做T监控启动后每分钟检查买卖点"));
        handler.removeCallbacks(pollTask);
        handler.post(pollTask);
        return START_STICKY;
    }

    @Override
    public void onDestroy() {
        handler.removeCallbacks(pollTask);
        super.onDestroy();
    }

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }

    private void createChannel() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            NotificationChannel channel = new NotificationChannel(
                    CHANNEL_ID,
                    "A股做T信号",
                    NotificationManager.IMPORTANCE_HIGH);
            channel.setDescription("做T买卖点信号推送");
            NotificationManager manager = getSystemService(NotificationManager.class);
            if (manager != null) {
                manager.createNotificationChannel(channel);
            }
        }
    }

    private void checkSignalFile() {
        try {
            File file = new File(getFilesDir(), "t_notify.json");
            if (!file.exists()) {
                return;
            }
            FileInputStream in = new FileInputStream(file);
            byte[] bytes = new byte[(int) file.length()];
            int read = in.read(bytes);
            in.close();
            String text = new String(bytes, 0, Math.max(read, 0), "UTF-8");
            JSONObject obj = new JSONObject(text);
            long ts = obj.optLong("ts", 0);
            if (ts <= lastTs) {
                return;
            }
            lastTs = ts;
            getSharedPreferences(PREFS, MODE_PRIVATE)
                    .edit().putLong(KEY_LAST_TS, ts).apply();
            String title = obj.optString("title", "做T信号");
            String message = obj.optString("message", "");
            NotificationManager manager = getSystemService(NotificationManager.class);
            if (manager != null) {
                manager.notify((int) (ts % 100000), buildNotification(title, message));
            }
        } catch (Exception e) {
            // ignore parse/io errors
        }
    }

    private Notification buildNotification(String title, String message) {
        Intent intent = new Intent(this, MainActivity.class);
        PendingIntent pending = PendingIntent.getActivity(
                this, 0, intent,
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
        return new NotificationCompat.Builder(this, CHANNEL_ID)
                .setSmallIcon(R.drawable.ic_launcher_foreground)
                .setContentTitle(title)
                .setContentText(message)
                .setContentIntent(pending)
                .setAutoCancel(true)
                .build();
    }
}
