/*
 * Padthru TV app (Samsung Tizen)
 *
 * Shows one of the TV's HDMI inputs full screen inside the app (tizen.tvwindow),
 * reads the controllers paired to the TV through the Gamepad API and streams
 * their state to the Padthru PC app over a WebSocket. The PC app turns that into
 * a virtual Xbox 360 controller.
 *
 * Remote: Red = show/hide info, Green = settings, 1-4 = HDMI input, Back = exit.
 */
(function () {
  'use strict';

  var VERSION = '0.1.0';
  var PORT = 47811;
  var POLL_MS = 4;             // how often controllers are read
  var KEEPALIVE_MS = 100;      // unchanged state is resent this often (the PC releases input after 600 ms of silence)
  var PROBE_TIMEOUT_MS = 1500; // per address while searching the network
  var SCAN_PARALLEL = 32;

  var store = {
    get: function (k, d) { try { var v = localStorage.getItem('padthru.' + k); return v === null ? d : v; } catch (e) { return d; } },
    set: function (k, v) { try { localStorage.setItem('padthru.' + k, String(v)); } catch (e) {} }
  };

  var host = store.get('host', '');
  var hdmi = parseInt(store.get('hdmi', '0'), 10) || 0;
  var sources = [];       // HDMI inputs as the TV describes them
  var link = null;        // verified WebSocket to the PC
  var pcName = '';
  var connecting = false, scanning = false, failures = 0;
  var pads = {}, rumble = {};
  var hudTimer = null, settingsOpen = false, editOctet = '', draftHdmi = 0;
  var logQueue = [];

  /* ---------- helpers ---------- */
  function $(id) { return document.getElementById(id); }
  function text(id, value, cls) { var el = $(id); el.textContent = value; if (cls !== undefined) el.className = 'v ' + cls; }
  function showHud(ms) {
    var h = $('hud'); h.className = 'card'; clearTimeout(hudTimer);
    if (ms) hudTimer = setTimeout(function () { h.className = 'card hidden'; }, ms);
  }
  function toggleHud() {
    var h = $('hud');
    if (h.className.indexOf('hidden') >= 0) showHud(0); else { clearTimeout(hudTimer); h.className = 'card hidden'; }
  }
  function send(obj) {
    if (link && link.readyState === 1) { try { link.send(JSON.stringify(obj)); return true; } catch (e) {} }
    return false;
  }
  function log(msg) {
    msg = new Date().toISOString().slice(11, 23) + ' ' + msg;
    if (!send({ log: msg })) { logQueue.push(msg); if (logQueue.length > 40) logQueue.shift(); }
  }
  function padName(id) {
    var m = /^"?([^"(]+)"?/.exec(id || '');
    return (m ? m[1] : id || 'Controller').trim().slice(0, 40);
  }

  /* ---------- picture: an HDMI input inside the app ---------- */
  function loadSources(cb) {
    try {
      tizen.systeminfo.getPropertyValue('VIDEOSOURCE', function (vs) {
        var isHdmi = function (s) { return s.type === 'HDMI'; };
        var connected = (vs.connected || []).filter(isHdmi);
        sources = connected.concat((vs.disconnected || []).filter(isHdmi));
        sources.sort(function (a, b) { return a.number - b.number; });
        cb(connected);
      }, function (e) { log('VIDEOSOURCE error: ' + e.message); cb([]); });
    } catch (e) { log('systeminfo unavailable: ' + e.message); cb([]); }
  }

  function sourceFor(n) {
    for (var i = 0; i < sources.length; i++) if (sources[i].number === n) return sources[i];
    return null;
  }

  function showPicture(n) {
    loadSources(function (connected) {
      var target = n || hdmi;
      if (!sourceFor(target)) target = connected.length ? connected[0].number : (sources.length ? sources[0].number : 0);
      var src = sourceFor(target);
      if (!src) { text('src', 'No HDMI input found', 'bad'); return; }
      hdmi = target; store.set('hdmi', hdmi);
      try {
        // setSource() needs the TV's own source object, not a hand-made {type, number}
        tizen.tvwindow.setSource(src, function () {
          tizen.tvwindow.show(function () {
            text('src', 'HDMI ' + hdmi, 'ok');
            setTimeout(describeSignal, 1500);
          }, function (e) { text('src', 'Could not show HDMI ' + hdmi + ': ' + e.message, 'bad'); },
          ['0px', '0px', '1920px', '1080px'], 'MAIN', 'BEHIND');
        }, function (e) { text('src', 'Could not switch to HDMI ' + hdmi + ': ' + e.message, 'bad'); }, 'MAIN');
      } catch (e) { text('src', 'TV window not available: ' + e.message, 'bad'); }
    });
  }

  function describeSignal() {
    try {
      var r = tizen.tvwindow.getVideoResolution('MAIN');
      if (r && r.width >= 640) {
        var hz = r.frequency > 1000 ? Math.round(r.frequency / 100) : r.frequency; // reported in 1/100 Hz
        text('src', 'HDMI ' + hdmi + ' · ' + r.width + '×' + r.height + ' @ ' + hz + ' Hz', 'ok');
        log('signal ' + r.width + 'x' + r.height + ' @' + hz);
      }
    } catch (e) {}
  }

  /* ---------- finding and talking to the PC ---------- */
  // Opens a WebSocket, says hello and hands back the socket only if a Padthru PC answers.
  function probe(addr, timeout, done) {
    var ws, finished = false, timer;
    var finish = function (ok) {
      if (finished) return;
      finished = true; clearTimeout(timer);
      if (!ok) { try { ws.close(); } catch (e) {} }
      done(ok ? ws : null, addr);
    };
    timer = setTimeout(function () { finish(false); }, timeout);
    try { ws = new WebSocket('ws://' + addr + ':' + PORT); } catch (e) { finish(false); return; }
    ws.onopen = function () {
      try { ws.send(JSON.stringify({ hello: { app: 'padthru-tv', v: VERSION, ua: navigator.userAgent } })); } catch (e) { finish(false); }
    };
    ws.onmessage = function (ev) {
      var m; try { m = JSON.parse(ev.data); } catch (e) { return; }
      if (m.padthru) { pcName = m.name || addr; finish(true); }
    };
    ws.onerror = function () { finish(false); };
    ws.onclose = function () { finish(false); };
  }

  // Searching uses plain HTTP: the TV's browser deliberately slows down new WebSocket
  // attempts after many failures, which would make the search miss the PC.
  function ping(addr, timeout, done) {
    var x = new XMLHttpRequest(), finished = false;
    var finish = function (ok) { if (!finished) { finished = true; done(ok, addr); } };
    try {
      x.open('GET', 'http://' + addr + ':' + PORT + '/padthru', true);
      x.timeout = timeout;
      x.onload = function () {
        var j = null; try { j = JSON.parse(x.responseText); } catch (e) {}
        finish(!!(j && j.padthru));
      };
      x.onerror = x.ontimeout = x.onabort = function () { finish(false); };
      x.send();
      setTimeout(function () { if (!finished) { try { x.abort(); } catch (e) {} finish(false); } }, timeout + 200);
    } catch (e) { finish(false); }
  }

  function adopt(ws, addr) {
    link = ws; host = addr; store.set('host', addr);
    failures = 0; scanning = false; connecting = false;
    text('pc', 'Connected to ' + pcName, 'ok'); showHud(4000);
    while (logQueue.length) send({ log: logQueue.shift() });
    for (var k in pads) pads[k].sent = 0; // resend full state
    ws.onmessage = function (ev) {
      var m; try { m = JSON.parse(ev.data); } catch (e) { return; }
      if (m.r) rumble[m.r[0]] = [m.r[1] / 255, m.r[2] / 255];
    };
    ws.onerror = function () {};
    ws.onclose = function () {
      if (link !== ws) return;
      link = null;
      text('pc', 'Lost connection to ' + pcName + ', reconnecting…', 'bad'); showHud(10000);
      setTimeout(connect, 1000);
    };
    log('connected to ' + addr + ' (TV app ' + VERSION + ')');
  }

  function connect() {
    if (link || connecting || scanning) return;
    if (!host) { scan(); return; }
    connecting = true;
    text('pc', 'Connecting to ' + host + '…', 'bad');
    probe(host, 2500, function (ws, addr) {
      connecting = false;
      if (ws) { adopt(ws, addr); return; }
      failures++;
      if (failures >= 2) scan(); else setTimeout(connect, 1500);
    });
  }

  var tvAddress = '';
  function isIp(ip) { return /^\d+\.\d+\.\d+\.\d+$/.test(ip || '') && ip !== '0.0.0.0'; }
  function myAddress() {
    if (isIp(tvAddress)) return tvAddress;
    try { var ip = webapis.network.getIp(); if (isIp(ip)) tvAddress = ip; } catch (e) {}
    return tvAddress;
  }
  // The TV's address, asked of every API that might know it.
  function findMyAddress(cb) {
    if (myAddress()) { cb(tvAddress); return; }
    var props = ['ETHERNET_NETWORK', 'WIFI_NETWORK'], i = 0;
    var tryNext = function () {
      if (i >= props.length) { cb(''); return; }
      var prop = props[i++];
      try {
        tizen.systeminfo.getPropertyValue(prop, function (n) {
          if (n && isIp(n.ipAddress)) { tvAddress = n.ipAddress; cb(tvAddress); } else tryNext();
        }, function () { tryNext(); });
      } catch (e) { tryNext(); }
    };
    tryNext();
  }

  // Tries every address on the TV's own /24 network, SCAN_PARALLEL at a time.
  function scan() {
    if (scanning || link) return;
    scanning = true; failures = 0;
    text('pc', 'Looking for your PC…', 'bad'); showHud(0);
    findMyAddress(function (mine) { runScan(mine); });
  }

  function runScan(mine) {
    log('searching for the PC, TV address ' + (mine || 'unknown'));
    var prefixes = mine ? [mine.replace(/\.\d+$/, '')] : ['192.168.1', '192.168.0', '10.0.0', '192.168.2'];
    var queue = [];
    if (host) queue.push(host);
    prefixes.forEach(function (p) {
      for (var i = 1; i < 255; i++) { var a = p + '.' + i; if (a !== mine && a !== host) queue.push(a); }
    });
    var total = queue.length, tried = 0, active = 0, found = false;
    var next = function () {
      while (!found && active < SCAN_PARALLEL && queue.length) {
        active++;
        ping(queue.shift(), PROBE_TIMEOUT_MS, function (ok, addr) {
          active--; tried++;
          if (found) return;
          if (ok) {
            found = true;
            log('found the PC at ' + addr + ' after ' + tried + ' addresses');
            probe(addr, 3000, function (ws) {
              if (ws) { adopt(ws, addr); return; }
              // it answered the search but the controller link failed; remember it and keep trying it
              scanning = false; host = addr; store.set('host', addr);
              setTimeout(connect, 1000);
            });
            return;
          }
          if (tried % 8 === 0) text('pc', 'Looking for your PC… ' + Math.round(tried * 100 / total) + '%', 'bad');
          if (queue.length) next();
          else if (active === 0) {
            scanning = false;
            text('pc', 'PC not found. Is Padthru running on your PC? Retrying…', 'bad');
            setTimeout(connect, 5000);
          }
        });
      }
    };
    next();
  }

  function reconnectNow() {
    failures = 0;
    if (link) { var old = link; link = null; try { old.close(); } catch (e) {} }
    connect();
  }

  /* ---------- controllers ---------- */
  function r3(v) { return Math.round(v * 1000) / 1000; }

  function poll() {
    var now = Date.now();
    var list = navigator.getGamepads ? navigator.getGamepads() : [];
    var seen = {};
    for (var i = 0; i < list.length; i++) {
      var gp = list[i];
      if (!gp || !gp.connected) continue;
      var idx = gp.index;
      seen[idx] = true;
      var b = [], a = [], j;
      for (j = 0; j < gp.buttons.length; j++) b.push(r3(gp.buttons[j].value || (gp.buttons[j].pressed ? 1 : 0)));
      for (j = 0; j < gp.axes.length; j++) a.push(r3(gp.axes[j]));
      var sig = b.join(',') + '|' + a.join(',');
      var p = pads[idx];
      if (!p) {
        p = pads[idx] = { sig: '', sent: 0 };
        text('pad', padName(gp.id), 'ok'); showHud(4000);
        log('controller ' + idx + ': ' + gp.id + ' mapping=' + gp.mapping + ' buttons=' + gp.buttons.length + ' axes=' + gp.axes.length);
      }
      if (link && (sig !== p.sig || now - p.sent >= KEEPALIVE_MS)) {
        var msg = { i: idx, b: b, a: a };
        if (!p.sent) { msg.id = gp.id; msg.m = gp.mapping; }
        if (send(msg)) { p.sig = sig; p.sent = now; }
      }
      var rm = rumble[idx];
      if (rm && (rm[0] > 0 || rm[1] > 0) && gp.vibrationActuator && (!p.rumbleAt || now - p.rumbleAt > 150)) {
        p.rumbleAt = now;
        try { gp.vibrationActuator.playEffect('dual-rumble', { duration: 200, strongMagnitude: rm[0], weakMagnitude: rm[1] }); } catch (e) {}
      }
    }
    for (var k in pads) {
      if (!seen[k]) {
        send({ i: +k, gone: 1 });
        delete pads[k];
        text('pad', 'Disconnected. Press any button to reconnect', 'bad'); showHud(5000);
      }
    }
  }

  function releaseAll() {
    for (var k in pads) send({ i: +k, gone: 1 });
    pads = {};
  }

  /* ---------- settings (Green) ---------- */
  function prefix() {
    var base = host || myAddress();
    return base ? base.replace(/\.\d+$/, '') : '192.168.1';
  }
  function hdmiChoices() {
    var n = sources.map(function (s) { return s.number; });
    return n.length ? n : [1, 2, 3, 4];
  }
  function renderSettings() {
    var last = editOctet || (host ? host.split('.').pop() : '');
    $('set-host').innerHTML = prefix() + '.<b>' + (last || '…') + '</b>' + (editOctet ? '' : (host ? '' : ' (searching automatically)'));
    $('set-hdmi').textContent = '◀  HDMI ' + draftHdmi + '  ▶';
  }
  function openSettings() {
    settingsOpen = true; editOctet = ''; draftHdmi = hdmi || hdmiChoices()[0];
    renderSettings(); $('settings').className = 'card';
  }
  function closeSettings() { settingsOpen = false; $('settings').className = 'card hidden'; }
  function settingsKey(c) {
    if (c >= 48 && c <= 57) {
      editOctet = (editOctet + (c - 48)).slice(-3);
      if (+editOctet > 254 || +editOctet === 0) editOctet = String(c - 48);
      renderSettings();
    } else if (c === 37 || c === 39) {
      var ch = hdmiChoices(), pos = ch.indexOf(draftHdmi);
      pos = (pos + (c === 39 ? 1 : ch.length - 1)) % ch.length;
      draftHdmi = ch[pos < 0 ? 0 : pos]; renderSettings();
    } else if (c === 13) {
      closeSettings();
      if (draftHdmi !== hdmi) showPicture(draftHdmi);
      if (editOctet && +editOctet > 0) {
        var addr = prefix() + '.' + (+editOctet);
        if (addr !== host) { host = addr; store.set('host', addr); reconnectNow(); }
      }
    } else if (c === 405) {
      closeSettings(); host = ''; store.set('host', ''); reconnectNow();
    } else if (c === 10009 || c === 404) {
      closeSettings();
    }
  }

  /* ---------- remote control ---------- */
  ['0', '1', '2', '3', '4', '5', '6', '7', '8', '9', 'ColorF0Red', 'ColorF1Green', 'ColorF2Yellow'].forEach(function (k) {
    try { tizen.tvinputdevice.registerKey(k); } catch (e) {}
  });

  document.addEventListener('keydown', function (e) {
    var c = e.keyCode;
    if (settingsOpen) { settingsKey(c); e.preventDefault(); return; }
    if (c >= 49 && c <= 52) { showPicture(c - 48); showHud(3000); }
    else if (c === 403) toggleHud();
    else if (c === 404) openSettings();
    else if (c === 10009) { try { tizen.application.getCurrentApplication().exit(); } catch (x) {} }
  });

  document.addEventListener('visibilitychange', function () {
    if (document.hidden) { releaseAll(); log('app hidden'); }
    else { log('app visible'); showPicture(hdmi); }
  });

  try { webapis.appcommon.setScreenSaver(webapis.appcommon.AppCommonScreenSaverState.SCREEN_SAVER_OFF); } catch (e) {}
  try { tizen.tvwindow.addVideoResolutionChangeListener(function () { setTimeout(describeSignal, 300); }, 'MAIN'); } catch (e) {}

  showHud(0);
  showPicture(0);
  connect();
  setInterval(poll, POLL_MS);
})();
