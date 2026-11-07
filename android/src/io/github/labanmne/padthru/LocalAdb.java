package io.github.labanmne.padthru;

import android.content.Context;
import android.util.Base64;

import java.io.ByteArrayOutputStream;
import java.io.DataInputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.OutputStream;
import java.math.BigInteger;
import java.net.InetSocketAddress;
import java.net.Socket;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.nio.charset.StandardCharsets;
import java.security.KeyFactory;
import java.security.KeyPair;
import java.security.KeyPairGenerator;
import java.security.Signature;
import java.security.interfaces.RSAPublicKey;
import java.security.spec.PKCS8EncodedKeySpec;
import java.security.spec.X509EncodedKeySpec;

/**
 * A minimal client for the box's own debug connection (adbd on 127.0.0.1:5555), so the app
 * can start its background helper with shell rights, also after a reboot, without a computer.
 * The first time, the box asks on screen whether to allow debugging from "Padthru"; choose
 * "Always allow".
 */
final class LocalAdb implements java.io.Closeable {
    private static final int A_CNXN = 0x4e584e43, A_AUTH = 0x48545541, A_OPEN = 0x4e45504f;
    private static final int A_OKAY = 0x59414b4f, A_CLSE = 0x45534c43, A_WRTE = 0x45545257;
    private static final int AUTH_TOKEN = 1, AUTH_SIGNATURE = 2, AUTH_RSAPUBLICKEY = 3;
    private static final int VERSION = 0x01000001, MAX_DATA = 256 * 1024;
    private static final byte[] SHA1_PREFIX = {0x30, 0x21, 0x30, 0x09, 0x06, 0x05, 0x2b, 0x0e, 0x03, 0x02, 0x1a, 0x05, 0x00, 0x04, 0x14};

    private final Socket socket;
    private final DataInputStream in;
    private final OutputStream out;

    private LocalAdb(Socket s) throws IOException {
        socket = s;
        in = new DataInputStream(s.getInputStream());
        out = s.getOutputStream();
    }

    /** Runs a shell command through the box's own adbd and returns its output. */
    static String shell(Context ctx, String command, int authWaitMs) throws Exception {
        KeyPair key = key(ctx);
        Socket s = new Socket();
        s.connect(new InetSocketAddress("127.0.0.1", 5555), 2000);
        s.setSoTimeout(authWaitMs);
        try (LocalAdb adb = new LocalAdb(s)) {
            adb.connect(key);
            s.setSoTimeout(15000);
            return adb.run("shell:" + command);
        }
    }

    private void connect(KeyPair key) throws Exception {
        send(A_CNXN, VERSION, MAX_DATA, "host::padthru\0".getBytes(StandardCharsets.UTF_8));
        boolean sentSignature = false, sentKey = false;
        while (true) {
            int[] h = new int[3];
            byte[] data = read(h);
            if (h[0] == A_CNXN) return;
            if (h[0] != A_AUTH || h[1] != AUTH_TOKEN) throw new IOException("unexpected message " + Integer.toHexString(h[0]));
            if (!sentSignature) {
                Signature sig = Signature.getInstance("NONEwithRSA");
                sig.initSign(key.getPrivate());
                sig.update(SHA1_PREFIX);
                sig.update(data);
                send(A_AUTH, AUTH_SIGNATURE, 0, sig.sign());
                sentSignature = true;
            } else if (!sentKey) {
                // not known yet: the box now asks on screen whether to allow this key
                String pub = Base64.encodeToString(adbPublicKey((RSAPublicKey) key.getPublic()), Base64.NO_WRAP) + " padthru@box\0";
                send(A_AUTH, AUTH_RSAPUBLICKEY, 0, pub.getBytes(StandardCharsets.UTF_8));
                sentKey = true;
            } else {
                throw new IOException("debugging not allowed");
            }
        }
    }

    private String run(String service) throws IOException {
        int local = 1;
        send(A_OPEN, local, 0, (service + "\0").getBytes(StandardCharsets.UTF_8));
        ByteArrayOutputStream output = new ByteArrayOutputStream();
        int remote = 0;
        while (true) {
            int[] h = new int[3];
            byte[] data;
            try {
                data = read(h);
            } catch (IOException e) {
                break; // connection closed after the command
            }
            if (h[0] == A_OKAY) {
                remote = h[1];
            } else if (h[0] == A_WRTE) {
                output.write(data);
                send(A_OKAY, local, h[1], new byte[0]);
            } else if (h[0] == A_CLSE) {
                send(A_CLSE, local, remote == 0 ? h[1] : remote, new byte[0]);
                break;
            }
        }
        return output.toString("UTF-8");
    }

    private void send(int cmd, int arg0, int arg1, byte[] data) throws IOException {
        ByteBuffer b = ByteBuffer.allocate(24 + data.length).order(ByteOrder.LITTLE_ENDIAN);
        int sum = 0;
        for (byte x : data) sum += x & 0xff;
        b.putInt(cmd).putInt(arg0).putInt(arg1).putInt(data.length).putInt(sum).putInt(~cmd).put(data);
        out.write(b.array());
        out.flush();
    }

    private byte[] read(int[] header) throws IOException {
        byte[] raw = new byte[24];
        in.readFully(raw);
        ByteBuffer b = ByteBuffer.wrap(raw).order(ByteOrder.LITTLE_ENDIAN);
        header[0] = b.getInt();
        header[1] = b.getInt();
        header[2] = b.getInt();
        int len = b.getInt();
        if (len < 0 || len > MAX_DATA) throw new IOException("bad length");
        byte[] data = new byte[len];
        in.readFully(data);
        return data;
    }

    @Override
    public void close() throws IOException {
        socket.close();
    }

    /** The app's own debug key, made once and kept in its private storage. */
    private static synchronized KeyPair key(Context ctx) throws Exception {
        File priv = new File(ctx.getFilesDir(), "adbkey"), pub = new File(ctx.getFilesDir(), "adbkey.pub.der");
        KeyFactory kf = KeyFactory.getInstance("RSA");
        if (priv.exists() && pub.exists()) {
            return new KeyPair(kf.generatePublic(new X509EncodedKeySpec(readAll(pub))),
                    kf.generatePrivate(new PKCS8EncodedKeySpec(readAll(priv))));
        }
        KeyPairGenerator g = KeyPairGenerator.getInstance("RSA");
        g.initialize(2048);
        KeyPair kp = g.generateKeyPair();
        writeAll(priv, kp.getPrivate().getEncoded());
        writeAll(pub, kp.getPublic().getEncoded());
        return kp;
    }

    /** The public key in adb's own format (Android's mincrypt RSAPublicKey struct). */
    private static byte[] adbPublicKey(RSAPublicKey key) {
        final int words = 64; // 2048 bits
        BigInteger n = key.getModulus();
        BigInteger r32 = BigInteger.ONE.shiftLeft(32);
        BigInteger n0inv = n.mod(r32).modInverse(r32).negate().mod(r32);
        BigInteger rr = BigInteger.ONE.shiftLeft(2048).pow(2).mod(n);
        ByteBuffer b = ByteBuffer.allocate(4 + 4 + words * 4 + words * 4 + 4).order(ByteOrder.LITTLE_ENDIAN);
        b.putInt(words);
        b.putInt(n0inv.intValue());
        for (int i = 0; i < words; i++) b.putInt(n.shiftRight(32 * i).mod(r32).intValue());
        for (int i = 0; i < words; i++) b.putInt(rr.shiftRight(32 * i).mod(r32).intValue());
        b.putInt(key.getPublicExponent().intValue());
        return b.array();
    }

    private static byte[] readAll(File f) throws IOException {
        try (FileInputStream in = new FileInputStream(f)) {
            ByteArrayOutputStream o = new ByteArrayOutputStream();
            byte[] buf = new byte[4096];
            int n;
            while ((n = in.read(buf)) > 0) o.write(buf, 0, n);
            return o.toByteArray();
        }
    }

    private static void writeAll(File f, byte[] data) throws IOException {
        try (FileOutputStream o = new FileOutputStream(f)) {
            o.write(data);
        }
    }
}
