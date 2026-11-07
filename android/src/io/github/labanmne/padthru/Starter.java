package io.github.labanmne.padthru;

import android.app.job.JobInfo;
import android.app.job.JobParameters;
import android.app.job.JobScheduler;
import android.app.job.JobService;
import android.content.BroadcastReceiver;
import android.content.ComponentName;
import android.content.Context;
import android.content.Intent;

import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.net.InetSocketAddress;
import java.net.Socket;

/**
 * Keeps the background helper running: starts it through the box's own debug connection
 * when the box boots, when Padthru is updated or opened, and every 15 minutes if it died.
 */
public class Starter extends JobService {
    static final int JOB_NOW = 1, JOB_WATCH = 2;
    static volatile String lastResult = "";

    /** Starts after boot and app updates. */
    public static class Boot extends BroadcastReceiver {
        @Override
        public void onReceive(Context context, Intent intent) {
            schedule(context);
        }
    }

    static void schedule(Context ctx) {
        JobScheduler js = ctx.getSystemService(JobScheduler.class);
        ComponentName me = new ComponentName(ctx, Starter.class);
        js.schedule(new JobInfo.Builder(JOB_NOW, me).setOverrideDeadline(0).build());
        js.schedule(new JobInfo.Builder(JOB_WATCH, me).setPeriodic(15 * 60 * 1000).setPersisted(true).build());
    }

    static boolean helperRunning() {
        try (Socket s = new Socket()) {
            s.connect(new InetSocketAddress("127.0.0.1", Helper.STATUS_PORT), 300);
            s.setSoTimeout(1000);
            return new BufferedReader(new InputStreamReader(s.getInputStream())).readLine() != null;
        } catch (Exception e) {
            return false;
        }
    }

    /** Starts the helper if it isn't running. Blocks; returns a short status for the screen. */
    static String ensure(Context ctx) {
        if (helperRunning()) return lastResult = "running";
        String apk = ctx.getApplicationInfo().sourceDir;
        String cmd = "mkdir -p " + Helper.DIR + " && CLASSPATH=" + apk + " setsid nohup app_process /system/bin "
                + Helper.class.getName() + " >> " + Helper.DIR + "/helper.log 2>&1 < /dev/null & sleep 1; echo started";
        for (int attempt = 0; attempt < 6; attempt++) { // adbd may still be starting after a boot
            try {
                // up to a minute for someone to press "Always allow" the first time
                LocalAdb.shell(ctx, cmd, 60000);
                for (int i = 0; i < 20 && !helperRunning(); i++) Thread.sleep(250);
                return lastResult = helperRunning() ? "running" : "started, but not answering (see " + Helper.DIR + "/helper.log)";
            } catch (java.net.ConnectException e) {
                lastResult = "the box's debug connection isn't on (Developer options → USB debugging)";
            } catch (Exception e) {
                lastResult = "couldn't start: " + e.getMessage();
            }
            try {
                Thread.sleep(10000);
            } catch (InterruptedException e) {
                break;
            }
        }
        return lastResult;
    }

    @Override
    public boolean onStartJob(JobParameters params) {
        Thread t = new Thread(() -> {
            ensure(getApplicationContext());
            jobFinished(params, false);
        }, "padthru-starter");
        t.start();
        return true;
    }

    @Override
    public boolean onStopJob(JobParameters params) {
        return false;
    }
}
