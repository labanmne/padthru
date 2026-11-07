package io.github.labanmne.padthru;

import android.util.Base64;

import org.json.JSONObject;

import java.io.BufferedInputStream;
import java.io.ByteArrayOutputStream;
import java.io.DataInputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.Inet4Address;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.net.NetworkInterface;
import java.net.Socket;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.security.SecureRandom;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.concurrent.CompletionService;
import java.util.concurrent.ExecutorCompletionService;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;

/**
 * Finds the Padthru PC app on the local network and keeps a WebSocket open to it.
 * Same protocol as the Samsung TV app: JSON text messages on port 47811, discovery
 * with a plain HTTP GET /padthru. A tiny WebSocket client is built in, so the app
 * has no dependencies.
 */
final class PcLink {
    /** Where the PC's address is remembered (app settings, or a file for the helper). */
    interface Store {
        String get();

        void set(String host);
    }

    interface Listener {
        void onStatus(String text, boolean connected);
        void onConnected();
        void onMessage(JSONObject msg);
    }

    static final int PORT = 47811;
    private static final SecureRandom RANDOM = new SecureRandom();

    private final Store store;
    private final Listener listener;
    private final String hello;
    private final Object writeLock = new Object();
    private volatile boolean running = true;
    private volatile Socket socket;
    private volatile OutputStream out;
    private volatile boolean connected;
    volatile String pcName = "";
    volatile String host = "";

    PcLink(Store store, String hello, Listener listener) {
        this.store = store;
        this.hello = hello;
        this.listener = listener;
    }

    void start() {
        Thread t = new Thread(this::loop, "padthru-link");
        t.setDaemon(true);
        t.start();
    }

    void stop() {
        running = false;
        closeQuietly();
    }

    boolean isConnected() {
        return connected;
    }

    /** Forget the saved PC and search again. */
    void rescan() {
        store.set("");
        closeQuietly();
    }

    private void loop() {
        while (running) {
            String target = store.get();
            try {
                if (target.isEmpty() || !ping(target, 1500)) {
                    listener.onStatus("Looking for your PC…", false);
                    target = scan();
                }
                if (target == null) {
                    listener.onStatus("PC not found. Is Padthru running on your PC? Retrying…", false);
                    sleep(5000);
                    continue;
                }
                store.set(target);
                host = target;
                session(target);
            } catch (Exception e) {
                if (running) listener.onStatus("Lost the connection to " + (pcName.isEmpty() ? target : pcName) + ", reconnecting…", false);
            }
            connected = false;
            closeQuietly();
            sleep(1500);
        }
    }

    // ---------------------------------------------------------------- discovery

    private boolean ping(String addr, int timeoutMs) {
        HttpURLConnection c = null;
        try {
            c = (HttpURLConnection) new URL("http://" + addr + ":" + PORT + "/padthru").openConnection();
            c.setConnectTimeout(timeoutMs);
            c.setReadTimeout(timeoutMs);
            c.setUseCaches(false);
            if (c.getResponseCode() != 200) return false;
            InputStream in = c.getInputStream();
            ByteArrayOutputStream buf = new ByteArrayOutputStream();
            byte[] b = new byte[512];
            int n;
            while ((n = in.read(b)) > 0 && buf.size() < 4096) buf.write(b, 0, n);
            JSONObject j = new JSONObject(buf.toString("UTF-8"));
            if (j.optInt("padthru") != 1) return false;
            pcName = j.optString("name", addr);
            return true;
        } catch (Exception e) {
            return false;
        } finally {
            if (c != null) c.disconnect();
        }
    }

    /** This box's own IPv4 addresses on the local network. */
    static List<String> localAddresses() {
        List<String> found = new ArrayList<>();
        try {
            for (NetworkInterface nif : Collections.list(NetworkInterface.getNetworkInterfaces())) {
                if (!nif.isUp() || nif.isLoopback()) continue;
                for (InetAddress a : Collections.list(nif.getInetAddresses())) {
                    if (a instanceof Inet4Address && a.isSiteLocalAddress()) found.add(a.getHostAddress());
                }
            }
        } catch (Exception ignored) {
        }
        return found;
    }

    /** Asks every address on the box's own /24 network(s); returns the first Padthru PC. */
    private String scan() throws InterruptedException {
        List<String> mine = localAddresses();
        List<String> queue = new ArrayList<>();
        for (String me : mine) {
            String prefix = me.substring(0, me.lastIndexOf('.'));
            for (int i = 1; i < 255; i++) {
                String a = prefix + "." + i;
                if (!a.equals(me) && !queue.contains(a)) queue.add(a);
            }
        }
        if (queue.isEmpty()) return null;
        ExecutorService pool = Executors.newFixedThreadPool(48);
        CompletionService<String> done = new ExecutorCompletionService<>(pool);
        for (String a : queue) done.submit(() -> ping(a, 800) ? a : null);
        try {
            for (int i = 0; i < queue.size(); i++) {
                String hit = done.take().get();
                if (hit != null) return hit;
            }
        } catch (Exception ignored) {
        } finally {
            pool.shutdownNow();
            pool.awaitTermination(1, TimeUnit.SECONDS);
        }
        return null;
    }

    // ---------------------------------------------------------------- WebSocket

    private void session(String addr) throws IOException {
        Socket s = new Socket();
        s.setTcpNoDelay(true);
        s.connect(new InetSocketAddress(addr, PORT), 3000);
        s.setSoTimeout(20000); // the PC pings every 5 s
        socket = s;
        InputStream rawIn = new BufferedInputStream(s.getInputStream());
        DataInputStream in = new DataInputStream(rawIn);
        out = s.getOutputStream();

        byte[] nonce = new byte[16];
        RANDOM.nextBytes(nonce);
        String key = Base64.encodeToString(nonce, Base64.NO_WRAP);
        String req = "GET / HTTP/1.1\r\nHost: " + addr + ":" + PORT + "\r\nUpgrade: websocket\r\n"
                + "Connection: Upgrade\r\nSec-WebSocket-Key: " + key + "\r\nSec-WebSocket-Version: 13\r\n\r\n";
        synchronized (writeLock) {
            out.write(req.getBytes(StandardCharsets.US_ASCII));
            out.flush();
        }
        String status = readLine(in);
        if (!status.startsWith("HTTP/1.1 101")) throw new IOException("handshake failed: " + status);
        while (!readLine(in).isEmpty()) { /* skip headers */ }

        sendText(hello);
        while (running) {
            int b0 = in.readUnsignedByte();
            int b1 = in.readUnsignedByte();
            int opcode = b0 & 0x0F;
            long len = b1 & 0x7F;
            if (len == 126) len = in.readUnsignedShort();
            else if (len == 127) len = in.readLong();
            byte[] mask = null;
            if ((b1 & 0x80) != 0) {
                mask = new byte[4];
                in.readFully(mask);
            }
            if (len > 1 << 20) throw new IOException("frame too large");
            byte[] payload = new byte[(int) len];
            in.readFully(payload);
            if (mask != null) for (int i = 0; i < payload.length; i++) payload[i] ^= mask[i & 3];
            if (opcode == 0x1) {
                handleText(new String(payload, StandardCharsets.UTF_8));
            } else if (opcode == 0x9) {
                sendFrame(0xA, payload); // pong
            } else if (opcode == 0x8) {
                throw new IOException("closed by the PC");
            }
        }
    }

    private void handleText(String text) {
        JSONObject m;
        try {
            m = new JSONObject(text);
        } catch (Exception e) {
            return;
        }
        if (m.has("padthru")) {
            pcName = m.optString("name", pcName);
            connected = true;
            listener.onStatus("Connected to " + pcName, true);
            listener.onConnected();
            return;
        }
        listener.onMessage(m);
    }

    /** Sends a text message; false if not connected. Safe from any thread. */
    boolean sendText(String text) {
        try {
            sendFrame(0x1, text.getBytes(StandardCharsets.UTF_8));
            return true;
        } catch (IOException e) {
            closeQuietly();
            return false;
        }
    }

    private void sendFrame(int opcode, byte[] payload) throws IOException {
        OutputStream o = out;
        if (o == null) throw new IOException("not connected");
        int len = payload.length;
        byte[] frame = new byte[2 + (len < 126 ? 0 : len < 65536 ? 2 : 8) + 4 + len];
        int p = 0;
        frame[p++] = (byte) (0x80 | opcode);
        if (len < 126) {
            frame[p++] = (byte) (0x80 | len);
        } else if (len < 65536) {
            frame[p++] = (byte) (0x80 | 126);
            frame[p++] = (byte) (len >> 8);
            frame[p++] = (byte) len;
        } else {
            frame[p++] = (byte) (0x80 | 127);
            for (int i = 7; i >= 0; i--) frame[p++] = (byte) ((long) len >> (8 * i));
        }
        byte[] mask = new byte[4];
        RANDOM.nextBytes(mask);
        System.arraycopy(mask, 0, frame, p, 4);
        p += 4;
        for (int i = 0; i < len; i++) frame[p + i] = (byte) (payload[i] ^ mask[i & 3]);
        synchronized (writeLock) {
            o.write(frame);
            o.flush();
        }
    }

    private static String readLine(DataInputStream in) throws IOException {
        StringBuilder sb = new StringBuilder();
        int c;
        while ((c = in.read()) != -1) {
            if (c == '\n') break;
            if (c != '\r') sb.append((char) c);
            if (sb.length() > 4096) throw new IOException("header too long");
        }
        if (c == -1 && sb.length() == 0) throw new IOException("connection closed");
        return sb.toString();
    }

    private void closeQuietly() {
        connected = false;
        out = null;
        Socket s = socket;
        socket = null;
        if (s != null) {
            try {
                s.close();
            } catch (IOException ignored) {
            }
        }
    }

    private static void sleep(long ms) {
        try {
            Thread.sleep(ms);
        } catch (InterruptedException ignored) {
        }
    }
}
