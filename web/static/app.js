"use strict";
/* Lab builder - front end for the Ludus homelab launcher.
   Plain DOM, no framework. Everything shown comes from /api/* on the same
   server; text is always set with textContent, never as HTML. */
(() => {
  const app = document.getElementById("app");
  const toasts = document.getElementById("toasts");

  // ---------------------------------------------------------------- helpers
  function h(tag, attrs, ...kids) {
    const el = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v === undefined || v === null || v === false) continue;
      if (k === "class") el.className = v;
      else if (k === "text") el.textContent = v;
      else if (k === "vars") for (const [name, val] of Object.entries(v)) el.style.setProperty(name, val);
      else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
      else if (v === true) el.setAttribute(k, "");
      else el.setAttribute(k, v);
    }
    for (const kid of kids.flat(Infinity)) {
      if (kid === null || kid === undefined || kid === false) continue;
      el.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
    }
    return el;
  }

  const gb = (n) => (n === null || n === undefined ? "?" : (Math.round(n * 10) / 10).toString()) + " GB";
  const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;
  const when = (iso) => {
    if (!iso || iso.startsWith("0001")) return "never";
    const d = new Date(iso);
    return isNaN(d) ? "never" : d.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
  };

  // Loading affordances: a shimmer skeleton and an indeterminate progress bar.
  function skeletonStack(lines) {
    return h("div", { class: "sk-stack", "aria-hidden": "true" },
      Array.from({ length: lines || 3 }, () => h("div", { class: "skeleton sk-line" })));
  }
  function loadingBar(label, calm) {
    return h("div", { class: "loading", role: "status" },
      h("div", { class: "progress" + (calm ? " calm" : "") }, h("span")),
      label ? h("p", { class: "muted", text: label }) : null);
  }

  // ---------------------------------------------------------------- icons
  // Original line glyphs, drawn on a 24x24 grid. Not any product's logo.
  const SVGNS = "http://www.w3.org/2000/svg";
  const ICONS = {
    dot: [["circle", { cx: 12, cy: 12, r: 3 }]],
    windows: [["rect", { x: 3, y: 4, width: 18, height: 13, rx: 1.5 }], ["path", { d: "M8 20h8M12 17v3" }]],
    linux: [["rect", { x: 3, y: 4, width: 18, height: 16, rx: 1.5 }], ["path", { d: "M7 9l3 3-3 3M13 16h4" }]],
    kali: [["circle", { cx: 12, cy: 12, r: 7 }], ["path", { d: "M12 2v3M12 19v3M2 12h3M19 12h3" }], ["circle", { cx: 12, cy: 12, r: 2 }]],
    tool: [["path", { d: "M12 3l7 3v5c0 4.5-3 7.6-7 9-4-1.4-7-4.5-7-9V6z" }]],
    server: [["rect", { x: 4, y: 4, width: 16, height: 7, rx: 1.5 }], ["rect", { x: 4, y: 13, width: 16, height: 7, rx: 1.5 }], ["path", { d: "M7 7.5h.01M7 16.5h.01" }]],
    dc: [["rect", { x: 4, y: 4, width: 16, height: 7, rx: 1.5 }], ["rect", { x: 4, y: 13, width: 16, height: 7, rx: 1.5 }], ["path", { d: "M7 7.5h.01M7 16.5h.01M16 7.5l1 1 2-2" }]],
    router: [["circle", { cx: 12, cy: 12, r: 3 }], ["path", { d: "M12 2v4M12 18v4M2 12h4M18 12h4M5 5l2.5 2.5M19 5l-2.5 2.5M5 19l2.5-2.5M19 19l-2.5-2.5" }]],
    siem: [["path", { d: "M3 12h4l2-6 4 12 2-6h6" }]],
    edr: [["path", { d: "M12 3l7 3v5c0 4.5-3 7.6-7 9-4-1.4-7-4.5-7-9V6z" }], ["path", { d: "M9 12l2 2 4-4" }]],
    dfir: [["circle", { cx: 11, cy: 11, r: 6 }], ["path", { d: "M20 20l-4.6-4.6" }]],
    activity: [["circle", { cx: 12, cy: 7, r: 3 }], ["path", { d: "M6 21v-1.5A4.5 4.5 0 0110.5 15h3A4.5 4.5 0 0118 19.5V21" }], ["path", { d: "M3 10c1-1 1-3 0-4M21 10c-1-1-1-3 0-4" }]],
    ad: [["circle", { cx: 9, cy: 8, r: 3 }], ["path", { d: "M3 20v-1a5 5 0 015-5h2a5 5 0 015 5v1" }], ["path", { d: "M16 11a3 3 0 000-6M21 20v-1a5 5 0 00-3-4.6" }]],
    eye: [["path", { d: "M2 12s4-7 10-7 10 7 10 7-4 7-10 7-10-7-10-7z" }], ["circle", { cx: 12, cy: 12, r: 2.5 }]],
    build: [["rect", { x: 3, y: 3, width: 18, height: 18, rx: 2 }], ["path", { d: "M12 8v8M8 12h8" }]],
    grid: [["rect", { x: 3, y: 3, width: 7, height: 7, rx: 1 }], ["rect", { x: 14, y: 3, width: 7, height: 7, rx: 1 }], ["rect", { x: 3, y: 14, width: 7, height: 7, rx: 1 }], ["rect", { x: 14, y: 14, width: 7, height: 7, rx: 1 }]],
    stack: [["ellipse", { cx: 12, cy: 6, rx: 8, ry: 3 }], ["path", { d: "M4 6v6c0 1.7 3.6 3 8 3s8-1.3 8-3V6M4 12v6c0 1.7 3.6 3 8 3s8-1.3 8-3v-6" }]],
    connect: [["path", { d: "M9 15l-3 3a2.8 2.8 0 01-4-4l3-3M15 9l3-3a2.8 2.8 0 014 4l-3 3M9.5 9.5l5 5" }]],
    key: [["circle", { cx: 8, cy: 12, r: 4 }], ["path", { d: "M11.5 12H21M18 12v3M21 12v2.5" }]],
    cpu: [["rect", { x: 6, y: 6, width: 12, height: 12, rx: 1.5 }], ["path", { d: "M9 2v2M15 2v2M9 20v2M15 20v2M2 9h2M2 15h2M20 9h2M20 15h2" }]],
    clock: [["circle", { cx: 12, cy: 12, r: 9 }], ["path", { d: "M12 7v5l3.5 2" }]],
    download: [["path", { d: "M12 3v12M7 11l5 5 5-5" }], ["path", { d: "M4 20h16" }]],
    spark: [["path", { d: "M12 3l2 5 5 2-5 2-2 5-2-5-5-2 5-2z" }]],
    warning: [["path", { d: "M12 3l9.5 17H2.5z" }], ["path", { d: "M12 10v4M12 17.5h.01" }]],
    refresh: [["path", { d: "M3.5 12a8.5 8.5 0 0114.5-6l2.5 2.3" }], ["path", { d: "M20.5 4v4.3h-4.3" }],
      ["path", { d: "M20.5 12a8.5 8.5 0 01-14.5 6l-2.5-2.3" }], ["path", { d: "M3.5 20v-4.3h4.3" }]],
    sun: [["circle", { cx: 12, cy: 12, r: 4 }], ["path", { d: "M12 2v2M12 20v2M2 12h2M20 12h2M5 5l1.5 1.5M17.5 17.5L19 19M19 5l-1.5 1.5M6.5 17.5L5 19" }]],
    moon: [["path", { d: "M20 14a8 8 0 01-10-10 8 8 0 1010 10z" }]],
    auto: [["circle", { cx: 12, cy: 12, r: 9 }], ["path", { d: "M12 3a9 9 0 010 18z", fill: "currentColor", stroke: "none" }]],
    cube: [["path", { d: "M12 2l8 4.5v9L12 20l-8-4.5v-9z" }], ["path", { d: "M12 2v18M4 6.5l8 4.5 8-4.5" }]],
    film: [["rect", { x: 3, y: 4, width: 18, height: 16, rx: 2 }], ["path", { d: "M7 4v16M17 4v16M3 9h4M17 9h4M3 15h4M17 15h4" }]],
    folder: [["path", { d: "M3 7a2 2 0 012-2h4l2 2h8a2 2 0 012 2v8a2 2 0 01-2 2H5a2 2 0 01-2-2z" }]],
    ram: [["rect", { x: 2, y: 8, width: 20, height: 8, rx: 1 }], ["path", { d: "M6 16v2.5M10 16v2.5M14 16v2.5M18 16v2.5M6.5 11h2M11 11h2M15.5 11h2" }]],
    disk: [["rect", { x: 3, y: 5, width: 18, height: 14, rx: 2 }], ["circle", { cx: 12, cy: 12, r: 3.5 }], ["circle", { cx: 12, cy: 12, r: 1, fill: "currentColor", stroke: "none" }]],
    logo: [["rect", { x: 3, y: 4, width: 11, height: 16, rx: 2.5 }], ["path", { d: "M14 8.5h6M14 12h5M14 15.5h3" }],
      ["circle", { cx: 7, cy: 8, r: 1.1, fill: "currentColor", stroke: "none" }], ["circle", { cx: 7, cy: 12, r: 1.1, fill: "currentColor", stroke: "none" }]],
    check: [["path", { d: "M4.5 12.5l4.5 4.5L19.5 6.5" }]],
    people: [["circle", { cx: 8, cy: 8, r: 2.6 }], ["circle", { cx: 16, cy: 8, r: 2.6 }],
      ["path", { d: "M3.5 19v-1a4 4 0 014-4h1a4 4 0 013.4 1.9M12.6 15.9A4 4 0 0115.5 14h1a4 4 0 014 4v1" }]],
  };

  function icon(name, cls) {
    const el = document.createElementNS(SVGNS, "svg");
    el.setAttribute("class", "ico" + (cls ? " " + cls : ""));
    el.setAttribute("viewBox", "0 0 24 24");
    el.setAttribute("aria-hidden", "true");
    for (const [tag, attrs] of (ICONS[name] || ICONS.dot)) {
      const node = document.createElementNS(SVGNS, tag);
      for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, String(v));
      el.append(node);
    }
    return el;
  }

  const VM_ICON = { windows: ["windows", "k-windows"], linux: ["linux", "k-linux"], kali: ["kali", "k-kali"],
    tool: ["tool", "k-tool"], router: ["router", "k-router"] };
  function vmIcon(vm) {
    const dc = vm.is_dc || (vm.tags || []).some((t) => String(t).startsWith("DC for"));
    if (dc) return icon("dc", "k-dc");
    const kind = vm.kind || (vm.router ? "router" : vm.family) || "linux";
    const [name, cls] = VM_ICON[kind] || VM_ICON.linux;
    return icon(name, cls);
  }
  const ADDON_ICON = { elastic: "siem", wazuh: "siem", velociraptor: "dfir", sysmon: "eye",
    dfir: "dfir", ghosts: "activity", badblood: "ad" };
  function labIcon(l) {
    const b = String(l.blueprint || "");
    if (b.includes("minecraft")) return icon("cube", "k-linux");
    if (b.includes("jellyfin")) return icon("film", "k-linux");
    if (b.includes("samba")) return icon("folder", "k-linux");
    if (b.includes("linux")) return icon("linux", "k-linux");
    if (b.includes("goad") || b.includes("ad")) return icon("ad", "k-dc");
    return icon("grid");
  }

  function netArt() {
    // A faint, fixed topology motif. Decorative, not data.
    const el = document.createElementNS(SVGNS, "svg");
    el.setAttribute("class", "netart"); el.setAttribute("viewBox", "0 0 400 300");
    el.setAttribute("preserveAspectRatio", "xMidYMid slice"); el.setAttribute("aria-hidden", "true");
    const pts = [[60, 60], [150, 40], [250, 90], [340, 50], [40, 160], [130, 150], [220, 190], [320, 160],
      [90, 250], [200, 260], [300, 240], [360, 200]];
    const links = [[0, 1], [1, 2], [2, 3], [0, 4], [1, 5], [2, 6], [3, 7], [4, 5], [5, 6], [6, 7],
      [4, 8], [5, 9], [6, 10], [7, 11], [8, 9], [9, 10], [10, 11]];
    for (const [a, b] of links) {
      const ln = document.createElementNS(SVGNS, "line");
      ln.setAttribute("x1", pts[a][0]); ln.setAttribute("y1", pts[a][1]);
      ln.setAttribute("x2", pts[b][0]); ln.setAttribute("y2", pts[b][1]);
      el.append(ln);
    }
    for (const [x, y] of pts) {
      const c = document.createElementNS(SVGNS, "circle");
      c.setAttribute("cx", x); c.setAttribute("cy", y); c.setAttribute("r", 2.5);
      el.append(c);
    }
    return el;
  }

  // ---------------------------------------------------------------- theme
  function applyTheme(mode) {
    if (mode === "light" || mode === "dark") document.documentElement.setAttribute("data-theme", mode);
    else document.documentElement.removeAttribute("data-theme");
  }
  function themePref() { try { return localStorage.getItem("lab-theme") || "auto"; } catch { return "auto"; } }
  function setTheme(mode) {
    try { localStorage.setItem("lab-theme", mode); } catch { /* private mode */ }
    applyTheme(mode);
    const row = document.getElementById("theme-toggle");
    if (row) for (const b of row.children) b.setAttribute("aria-pressed", String(b.dataset.mode === mode));
  }
  function themeToggle() {
    const pref = themePref();
    const btn = (mode, ic, label) => h("button", { type: "button", "data-mode": mode, title: label,
      "aria-label": label, "aria-pressed": String(pref === mode), onclick: () => setTheme(mode) }, icon(ic));
    return h("div", { class: "theme-toggle", id: "theme-toggle", role: "group", "aria-label": "Colour theme" },
      btn("light", "sun", "Light"), btn("auto", "auto", "Match system"), btn("dark", "moon", "Dark"));
  }
  applyTheme(themePref());

  class ApiError extends Error {
    constructor(status, data) { super((data && data.error) || `Request failed (${status})`); this.status = status; this.data = data || {}; }
  }

  async function api(method, path, body) {
    const opts = { method, credentials: "same-origin", headers: { "X-Lab-Request": "1" } };
    if (body !== undefined) { opts.headers["Content-Type"] = "application/json"; opts.body = JSON.stringify(body); }
    let res;
    try { res = await fetch(path, opts); } catch { throw new ApiError(0, { error: "Can't reach the lab builder. Is the node on?" }); }
    let data = null;
    try { data = await res.json(); } catch { data = null; }
    if (res.status === 401 && path !== "/api/login" && path !== "/api/session") { signedOut(); }
    if (!res.ok) throw new ApiError(res.status, data);
    return data;
  }

  function toast(message, kind) {
    const t = h("div", { class: "toast" + (kind === "bad" ? " bad" : ""), text: message });
    toasts.append(t);
    while (toasts.children.length > 3) toasts.firstElementChild.remove();
    setTimeout(() => t.remove(), kind === "bad" ? 9000 : 5000);
  }

  async function copy(text) {
    try { await navigator.clipboard.writeText(text); toast("Copied."); }
    catch { toast("Couldn't copy. Select the text and copy it by hand.", "bad"); }
  }

  // Run an async action from a button, showing a spinner and the result.
  async function act(button, work, busyText) {
    const label = button.textContent;
    button.disabled = true;
    button.replaceChildren(h("span", { class: "spinner", "aria-hidden": "true" }), busyText || label);
    try { return await work(); }
    catch (e) { toast(e.message, "bad"); return undefined; }
    finally { button.disabled = false; button.replaceChildren(label); }
  }

  // A button that needs a second click within a few seconds.
  function confirmButton(label, confirmLabel, cls, onConfirm) {
    let armed = null;
    const b = h("button", { class: "btn " + (cls || ""), type: "button", text: label });
    b.addEventListener("click", () => {
      if (!armed) {
        b.textContent = confirmLabel;
        armed = setTimeout(() => { armed = null; b.textContent = label; }, 4000);
        return;
      }
      clearTimeout(armed); armed = null; b.textContent = label;
      onConfirm(b);
    });
    return b;
  }

  // ---------------------------------------------------------------- state
  const state = {
    user: null, node: "", version: "", admin: false, firstRun: false,
    catalog: null,
    build: { blueprint: null, options: {}, addons: [], dfir_template: null, ram_profile: "standard",
             range_id: "", name: "", idTouched: false, nameTouched: false },
    plan: null, planError: null, planSeq: 0, prevVms: new Set(),
    timers: [],
  };
  const CABLES = ["var(--cable-1)", "var(--cable-2)", "var(--cable-3)", "var(--cable-4)", "var(--cable-5)"];

  function every(ms, fn) { const id = setInterval(fn, ms); state.timers.push(id); return id; }
  function clearTimers() { state.timers.forEach(clearInterval); state.timers = []; state.templatePoll = null; }

  // ---------------------------------------------------------------- shell
  const cpuMeter = h("div", { class: "host-mem", title: "CPU usage on the node" });
  const memMeter = h("div", { class: "host-mem", title: "Memory in use on the node" });
  const diskMeter = h("div", { class: "host-mem", title: "VM storage in use on the node" });
  const vmStat = h("div", { class: "host-stat", title: "Running VMs on the node" });
  const upStat = h("div", { class: "host-stat", title: "Node uptime" });

  function fillMeter(el, glyph, label, usedPct) {
    const pct = Math.min(100, Math.max(0, usedPct));
    el.replaceChildren(
      icon(glyph),
      h("span", { class: "hm-label", text: label }),
      h("div", { class: "meter" + (pct >= 85 ? " hot" : pct >= 70 ? " warm" : ""), vars: { "--used": `${pct}%` } }, h("span")),
    );
  }

  function fillStat(el, glyph, value) {
    if (value === null || value === undefined || value === "") { el.replaceChildren(); return; }
    el.replaceChildren(icon(glyph), h("span", { class: "hm-label", text: String(value) }));
  }

  function fmtUptime(secs) {
    if (!secs || secs < 0) return "";
    const d = Math.floor(secs / 86400), hrs = Math.floor((secs % 86400) / 3600), mins = Math.floor((secs % 3600) / 60);
    if (d) return `up ${d}d ${hrs}h`;
    if (hrs) return `up ${hrs}h ${mins}m`;
    return `up ${mins}m`;
  }

  async function refreshStatus() {
    try {
      const o = await api("GET", "/api/overview");
      const c = o.cpu || {};
      if (c.cores) {
        const load = (c.load1 != null && c.load1 !== undefined) ? ` · load ${c.load1.toFixed(2)}` : "";
        fillMeter(cpuMeter, "cpu", `${Math.round(c.usage || 0)}% CPU · ${c.cores}c${load}`, c.usage || 0);
      } else { cpuMeter.replaceChildren(); }
      const m = o.memory || {};
      if (m.total) {
        const used = Math.max(0, m.total - (m.available || 0));
        fillMeter(memMeter, "ram", `${Math.round(m.available)} of ${Math.round(m.total)} GB RAM free`, (used / m.total) * 100);
      } else { memMeter.replaceChildren(); }
      const s = o.storage || {};
      if (s.total) {
        const usedS = Math.max(0, s.total - (s.available || 0));
        fillMeter(diskMeter, "disk", `${Math.round(s.available)} of ${Math.round(s.total)} GB disk free`, (usedS / s.total) * 100);
      } else { diskMeter.replaceChildren(); }
      fillStat(vmStat, "server", (o.running_vms != null) ? `${o.running_vms} VM${o.running_vms === 1 ? "" : "s"} running` : "");
      fillStat(upStat, "clock", fmtUptime(o.uptime));
    } catch { /* the meters are a nicety */ }
  }

  function shell(active, content) {
    const link = (hash, label, key, glyph) => h("a", { href: hash, "aria-current": active === key ? "page" : false },
      icon(glyph), h("span", { text: label }));
    const header = h("header", { class: "topbar" },
      h("div", { class: "brand" }, icon("logo", "brand-logo"),
        h("span", { class: "brand-name", text: "Lab builder" }),
        state.node ? h("span", { class: "brand-node", text: state.node }) : null),
      h("nav", { class: "nav", "aria-label": "Sections" },
        link("#/build", "Build a lab", "build", "build"), link("#/labs", "Your labs", "labs", "grid"),
        link("#/templates", "Templates", "templates", "stack"), link("#/connect", "Connect", "connect", "connect"),
        state.admin ? link("#/people", "People", "people", "people") : null,
        link("#/updates", "Updates", "updates", "download")),
      h("div", { class: "host-meters" }, cpuMeter, memMeter, diskMeter, vmStat, upStat),
      themeToggle(),
      h("div", { class: "who" }, h("span", { text: state.user }),
        h("button", { class: "btn btn-quiet", type: "button", text: "Sign out", onclick: signOut })),
    );
    app.replaceChildren(header, h("main", { id: "main" }, content));
  }

  // ---------------------------------------------------------------- sign in
  function signedOut() {
    if (state.user === null) return;
    state.user = null;
    clearTimers();
    viewSignin("Your session ended. Sign in again.");
  }

  async function signOut() {
    try { await api("POST", "/api/logout", {}); } catch { /* ignore */ }
    state.user = null;
    clearTimers();
    viewSignin();
  }

  function viewSignin(message) {
    const user = h("input", { type: "text", id: "u", name: "username", autocomplete: "username", required: true, autocapitalize: "none", spellcheck: "false" });
    const pass = h("input", { type: "password", id: "p", name: "password", autocomplete: "current-password", required: true });
    const err = h("p", { class: "error", role: "alert", text: message || "" });
    const submit = h("button", { class: "btn btn-build btn-wide", type: "submit", text: "Sign in" });
    const form = h("form", { class: "auth-card reveal", "aria-labelledby": "signin-title" },
      h("h2", { id: "signin-title", text: "Sign in" }),
      h("p", { class: "muted", text: "Use your Proxmox account — root, or your Ludus admin user." }),
      h("div", { class: "field" }, h("label", { for: "u", text: "Username" }), user),
      h("div", { class: "field" }, h("label", { for: "p", text: "Password" }), pass),
      err, submit,
      h("p", { class: "auth-foot", text: "Runs entirely on your own node · nothing leaves your network." }));
    form.addEventListener("submit", async (ev) => {
      ev.preventDefault();
      err.textContent = "";
      await act(submit, async () => {
        try {
          const r = await api("POST", "/api/login", { username: user.value.trim(), password: pass.value });
          state.user = r.user;
          pass.value = "";
          await start();
        } catch (e) { err.textContent = e.message; pass.select(); }
      }, "Signing in");
    });
    const point = (glyph, text) => h("div", { class: "auth-point" }, icon(glyph), h("span", { text }));
    app.replaceChildren(h("div", { class: "auth" },
      h("div", { class: "auth-top" }, themeToggle()),
      h("section", { class: "auth-hero" },
        netArt(),
        h("div", { class: "auth-badge" }, icon("logo", "brand-logo"), h("span", { text: "Lab builder" })),
        h("h1", { text: "Spin up labs and home services on your own server." }),
        h("p", { class: "lead", text: "A console for your Proxmox box: build cyber-range labs, or stand up a Minecraft server, media library or file share — in a few clicks, all in one place." }),
        h("div", { class: "auth-points" },
          point("build", "Cyber-range labs — Active Directory, GOAD, Linux networks"),
          point("cube", "Home services — Minecraft, Jellyfin and Samba"),
          point("eye", "One console with full visibility of your node"))),
      h("div", { class: "auth-form-wrap" }, form)));
    user.focus();
  }

  // ---------------------------------------------------------------- routing
  function route() {
    if (!state.user) return;
    clearTimers();
    let hash = location.hash;
    if (!hash && state.firstRun) hash = "#/welcome";
    if (!hash) hash = "#/build";
    const [, section, id] = hash.split("/");
    if (section === "welcome") { viewSetup(); return; }
    if (section === "people") { viewAccounts(); return; }
    if (section === "labs") viewLabs(id ? decodeURIComponent(id) : null);
    else if (section === "templates") viewTemplates();
    else if (section === "connect") viewConnect();
    else if (section === "updates") viewUpdates();
    else viewBuild();
    refreshStatus();
    every(30000, refreshStatus);
  }

  async function start() {
    try {
      const s = await api("GET", "/api/session");
      state.user = s.user; state.node = s.node; state.version = s.version;
      state.admin = !!s.admin; state.firstRun = !!s.first_run;
    } catch { state.user = null; viewSignin(); return; }
    route();
  }
  window.addEventListener("hashchange", route);

  // ---------------------------------------------------------------- build
  async function viewBuild() {
    const B = state.build;
    const form = h("div", { class: "steps" });
    const plan = h("aside", { class: "plan", "aria-labelledby": "plan-title" });
    shell("build", h("div", { class: "build" }, form, plan));
    form.append(h("section", { class: "step" }, h("div", { class: "skeleton sk-card" }), skeletonStack(4)));
    plan.append(loadingBar("Loading the catalogue…", true));

    try { state.catalog = await api("GET", "/api/catalog"); }
    catch (e) { form.replaceChildren(h("div", { class: "notice bad", text: e.message })); plan.replaceChildren(); return; }
    const labs = state.catalog.labs;
    if (!labs.length) {
      form.replaceChildren(h("div", { class: "notice warn", text: "No labs found. Run setup.sh on the node to install the catalogue." }));
      plan.replaceChildren();
      return;
    }
    if (!B.blueprint || !labs.some((l) => l.blueprint === B.blueprint)) selectLab(labs[0], false);
    renderForm(form, plan);
    renderPlan(plan);
    requestPlan(plan);
  }

  function currentLab() { return state.catalog.labs.find((l) => l.blueprint === state.build.blueprint); }

  function selectLab(lab, reset = true) {
    const B = state.build;
    B.blueprint = lab.blueprint;
    B.options = {};
    for (const f of lab.fields || []) B.options[f.key] = f.default;
    if (reset) { B.idTouched = false; B.nameTouched = false; }
    state.prevVms = new Set();
  }

  function renderForm(form, plan) {
    const B = state.build;
    const lab = currentLab();
    const update = () => requestPlan(plan);

    // Step 1: which lab or service
    const optionBtn = (l) => h("button", {
      type: "button", class: "lab-option", role: "radio", "aria-checked": String(l.blueprint === B.blueprint),
      disabled: !!l.error,
      onclick: () => { if (l.blueprint !== B.blueprint) { selectLab(l); renderForm(form, plan); update(); } },
    }, h("span", { class: "title" }, labIcon(l), h("span", { text: l.title })), h("span", { class: "blurb", text: l.blurb }),
    h("span", { class: "meta", text: l.error ? `Unavailable: ${l.error}` : `${plural(l.base_vms, "VM")} by default, about ${gb(l.base_ram)}` }));
    const labList = state.catalog.labs.filter((l) => l.kind !== "service");
    const svcList = state.catalog.labs.filter((l) => l.kind === "service");
    const picker = h("div", { class: "labs-pick", role: "radiogroup", "aria-label": "What to build" },
      ...(svcList.length ? [h("div", { class: "pick-group", text: "Labs" })] : []),
      ...labList.map(optionBtn),
      ...(svcList.length ? [h("div", { class: "pick-group", text: "Services" }), ...svcList.map(optionBtn)] : []));

    // Step 2: lab settings
    const settings = h("div", { class: "settings-body" });
    const drawSettings = () => {
      settings.replaceChildren();
      if (lab.note) settings.append(h("p", { class: "notice ok", text: lab.note }));
      const fields = (lab.fields || []).filter((f) => !(f.show_if && !B.options[f.show_if]));
      if (!fields.length) { settings.append(h("p", { class: "muted", text: "This lab has no settings to change. Add tooling below if you like." })); return; }
      const control = (f) => fieldControl(f, B.options, () => { if (f.type === "bool" || f.rerender) drawSettings(); update(); });
      // Render consecutive fields that share a `group` under one quiet subhead;
      // fields with no group fall into a single headless grid.
      let i = 0;
      while (i < fields.length) {
        const g = fields[i].group || "";
        const chunk = [];
        while (i < fields.length && (fields[i].group || "") === g) { chunk.push(fields[i]); i += 1; }
        const grid = h("div", { class: "fields" }, chunk.map(control));
        settings.append(g
          ? h("section", { class: "field-group" }, h("h3", { class: "field-group-head", text: g }), grid)
          : h("section", { class: "field-group" }, grid));
      }
    };
    drawSettings();

    // Step 3: add-ons
    const addons = h("div", { class: "addons" });
    const drawAddons = () => {
      addons.replaceChildren();
      const na = (currentLab() || {}).addon_na || {};
      for (const a of state.catalog.addons) {
        const reason = na[a.key];
        if (reason && B.addons.includes(a.key)) B.addons = B.addons.filter((k) => k !== a.key);
        const id = `addon-${a.key}`;
        const box = h("input", { type: "checkbox", id, checked: B.addons.includes(a.key), disabled: !!reason,
          onchange: (ev) => {
            B.addons = ev.target.checked ? [...B.addons, a.key] : B.addons.filter((k) => k !== a.key);
            if (a.key === "dfir") drawAddons();
            update();
          } });
        const row = h("div", { class: reason ? "addon is-na" : "addon" },
          h("label", { class: "check", for: id }, box,
            h("span", {}, h("span", { class: "field-label" },
              icon(ADDON_ICON[a.key] || "tool", "addon-ico"), h("span", { text: a.title }))),
            h("span", { class: "ram", text: a.ram ? `+${a.ram} GB` : "no extra VM" }),
            h("span", { class: "help", text: reason ? `Not available for this lab: ${reason}` : a.detail })));
        if (!reason && a.key === "dfir" && B.addons.includes("dfir")) {
          if (!B.dfir_template) B.dfir_template = state.catalog.dfir_field.default;
          const f = Object.assign({}, state.catalog.dfir_field, { default: B.dfir_template });
          const holder = { dfir_template: B.dfir_template };
          row.append(h("div", { class: "sub" }, fieldControl(f, holder, () => { B.dfir_template = holder.dfir_template; update(); })));
        }
        addons.append(row);
      }
    };
    drawAddons();

    // Step 4: size and name
    const profiles = h("div", { class: "profiles", id: "profiles" });
    const idInput = h("input", { type: "text", id: "range-id", value: B.range_id, maxlength: "20", spellcheck: "false",
      autocomplete: "off", "aria-describedby": "range-id-help range-id-error" });
    const idError = h("p", { class: "error", id: "range-id-error", "aria-live": "polite" });
    idInput.addEventListener("input", () => { B.range_id = idInput.value.trim(); B.idTouched = true; checkId(); renderPlanFooter(plan); });
    const nameInput = h("input", { type: "text", id: "range-name", value: B.name, maxlength: "80" });
    nameInput.addEventListener("input", () => { B.name = nameInput.value; B.nameTouched = true; });

    const isSingle = !!(lab && lab.single);   // sandbox + services: one VM, no tooling section
    if (isSingle) B.addons = [];
    const steps = [step(1, "Choose what to build", picker), step(2, "Settings", settings)];
    let n = 3;
    if (!isSingle) {
      steps.push(step(n++, "Security tooling", h("p", { class: "muted", text: "Optional. Tool servers get their own VLAN; agents go on every Windows and Linux VM except Kali." }), addons));
    }
    steps.push(step(n, isSingle ? "Name it" : "Size and name", isSingle ? null : profiles,
      h("div", { class: "fields" },
        h("div", { class: "field" }, h("label", { for: "range-id", text: "Lab ID" }), idInput,
          h("p", { class: "help", id: "range-id-help", text: "Short; used in VM names. Letters, digits and hyphens." }), idError),
        h("div", { class: "field" }, h("label", { for: "range-name", text: "Display name" }), nameInput))));
    form.replaceChildren(...steps);
    renderProfiles();
  }

  function step(n, title, ...body) {
    return h("section", { class: "step", "aria-labelledby": `step-${n}` },
      h("div", { class: "step-head" }, h("span", { class: "step-num", text: String(n) }), h("h2", { id: `step-${n}`, text: title })),
      ...body);
  }

  function osFamily(t) {
    t = String(t || "");
    if (t.startsWith("win")) return "windows";
    if (t.includes("kali")) return "kali";
    if (t.startsWith("rocky")) return "redhat";
    return "debian";
  }
  // Which app list applies to an OS: Kali and the other Debian-ish distros share one.
  function appFamily(t) {
    const fam = osFamily(t);
    return fam === "windows" ? "windows" : fam === "redhat" ? "redhat" : "debian";
  }

  function fieldControl(f, values, changed) {
    const id = `f-${f.key}`;
    const help = f.help ? h("p", { class: "help", id: `${id}-help`, text: f.help }) : null;
    const set = (v) => { values[f.key] = v; changed(); };
    if (f.type === "bool") {
      return h("div", { class: "field field-wide" }, h("label", { class: "check", for: id },
        h("input", { type: "checkbox", id, checked: !!values[f.key], onchange: (ev) => set(ev.target.checked) }),
        h("span", { class: "field-label", text: f.label }), help));
    }
    if (f.type === "int") {
      const out = h("output", { id, "aria-live": "polite", text: String(values[f.key]) });
      const minus = h("button", { type: "button", "aria-label": `Fewer ${f.label.toLowerCase()}`, text: "−" });
      const plus = h("button", { type: "button", "aria-label": `More ${f.label.toLowerCase()}`, text: "+" });
      const sync = () => { out.textContent = String(values[f.key]); minus.disabled = values[f.key] <= f.min; plus.disabled = values[f.key] >= f.max; };
      minus.addEventListener("click", () => { if (values[f.key] > f.min) { values[f.key]--; sync(); changed(); } });
      plus.addEventListener("click", () => { if (values[f.key] < f.max) { values[f.key]++; sync(); changed(); } });
      sync();
      return h("div", { class: "field" }, h("span", { class: "field-label", id: `${id}-label`, text: f.label }),
        h("div", { class: "stepper", role: "group", "aria-labelledby": `${id}-label` }, minus, out, plus), help);
    }
    if (f.type === "select" && f.choices.length <= 2) {
      const name = `r-${f.key}`;
      return h("div", { class: "field" }, h("span", { class: "field-label", id: `${id}-label`, text: f.label }),
        h("div", { class: "segmented", role: "radiogroup", "aria-labelledby": `${id}-label` },
          f.choices.map(([value, label]) => h("label", {},
            h("input", { type: "radio", name, value: String(value), checked: String(values[f.key]) === String(value),
              onchange: () => set(value) }), label))),
        h("p", { class: "help", text: (f.choices.find((c) => String(c[0]) === String(values[f.key])) || [])[2] || "" }));
    }
    if (f.type === "text") {
      const input = h("input", { type: "text", id, value: values[f.key], spellcheck: "false", "aria-describedby": help ? `${id}-help` : false });
      let t;
      input.addEventListener("input", () => { clearTimeout(t); t = setTimeout(() => set(input.value.trim()), 350); });
      return h("div", { class: "field" }, h("label", { for: id, text: f.label }), input, help);
    }
    if (f.type === "password") {
      const input = h("input", { type: "password", id, value: values[f.key], autocomplete: "new-password", "aria-describedby": help ? `${id}-help` : false });
      let t;
      input.addEventListener("input", () => { clearTimeout(t); t = setTimeout(() => set(input.value), 350); });
      return h("div", { class: "field" }, h("label", { for: id, text: f.label }), input, help);
    }
    if (f.type === "apps") {
      const fam = appFamily(values[f.os_field]);
      const avail = (state.catalog.apps || []).filter((a) => (a.os || []).includes(fam));
      const availKeys = new Set(avail.map((a) => a.key));
      const sel = new Set((Array.isArray(values[f.key]) ? values[f.key] : []).filter((k) => availKeys.has(k)));
      values[f.key] = [...sel];   // drop any apps not valid for the chosen OS
      const count = h("span", { class: "muted small" });
      const setCount = () => { count.textContent = sel.size ? `${sel.size} selected` : "none selected"; };
      const groups = h("div", { class: "apps-groups" });
      for (const cat of (state.catalog.app_categories || [])) {
        const items = avail.filter((a) => a.cat === cat);
        if (!items.length) continue;
        groups.append(h("div", { class: "apps-cat" }, h("h4", { text: cat }),
          h("div", { class: "apps-grid" }, items.map((a) => {
            const aid = `app-${a.key}`;
            return h("label", { class: "app-check", for: aid },
              h("input", { type: "checkbox", id: aid, checked: sel.has(a.key),
                onchange: (ev) => { ev.target.checked ? sel.add(a.key) : sel.delete(a.key); values[f.key] = [...sel]; setCount(); changed(); } }),
              h("span", { text: a.name }));
          }))));
      }
      if (!avail.length) groups.append(h("p", { class: "muted", text: "No pre-install apps for this operating system." }));
      setCount();
      return h("div", { class: "field field-wide apps-field" },
        h("div", { class: "apps-head" }, h("span", { class: "field-label", text: f.label }), count), help, groups);
    }
    let options;
    if (f.type === "template") {
      options = state.catalog.catalogs[f.catalog].map((c) => [c.template, c.label + (c.state === "ready" ? "" :
        c.state === "building" ? " (building now)" : c.state === "not built" ? " (needs a one-off build)" : " (downloads and builds the first time)")]);
    } else {
      options = f.choices.map(([v, label]) => [v, label]);
    }
    const select = h("select", { id, "aria-describedby": help ? `${id}-help` : false,
      onchange: (ev) => { const pick = options.find((o) => String(o[0]) === ev.target.value); set(pick ? pick[0] : ev.target.value); } },
      options.map(([v, label]) => h("option", { value: String(v), selected: String(values[f.key]) === String(v), text: label })));
    return h("div", { class: "field" }, h("label", { for: id, text: f.label }), select, help);
  }

  function renderProfiles() {
    const box = document.getElementById("profiles");
    if (!box) return;
    const B = state.build, p = state.plan;
    const standard = p ? p.ram_standard : null, lean = p ? p.ram_lean : null;
    if (lean === null || lean === undefined) B.ram_profile = "standard";
    const option = (value, title, size, text) => h("label", { class: "profile" },
      h("input", { type: "radio", name: "profile", value, checked: B.ram_profile === value, disabled: size === null || size === undefined,
        onchange: () => { B.ram_profile = value; requestPlan(document.querySelector(".plan")); } }),
      h("span", { class: "field-label", text: title }), h("span", { class: "gb", text: size === null || size === undefined ? "n/a" : gb(size) }),
      h("span", { class: "help", text }));
    box.replaceChildren(
      option("standard", "Standard", standard, "As the lab was designed."),
      option("lean", "Lean", lean, lean === null || lean === undefined ? "Makes no difference for this lab." :
        "Windows VMs get 4 GB and tool servers 2 GB less. Slower, but leaves room for other labs."));
  }

  function checkId() {
    const B = state.build, input = document.getElementById("range-id"), err = document.getElementById("range-id-error");
    if (!input) return true;
    let problem = "";
    if (!/^[A-Za-z][A-Za-z0-9-]{0,19}$/.test(B.range_id)) problem = "Start with a letter; letters, digits and hyphens only; 20 characters max.";
    else if (state.plan && state.plan.existing_ids.includes(B.range_id)) problem = `A lab called ${B.range_id} already exists.`;
    err.textContent = problem;
    input.setAttribute("aria-invalid", problem ? "true" : "false");
    return !problem;
  }

  let planTimer = null;
  function requestPlan(planEl) {
    clearTimeout(planTimer);
    planTimer = setTimeout(() => fetchPlan(planEl), 200);
  }

  async function fetchPlan(planEl) {
    const B = state.build;
    const seq = ++state.planSeq;
    planEl.setAttribute("aria-busy", "true");
    try {
      const p = await api("POST", "/api/plan", { blueprint: B.blueprint, options: B.options, addons: B.addons,
        dfir_template: B.dfir_template, ram_profile: B.ram_profile });
      if (seq !== state.planSeq) return;
      state.plan = p; state.planError = null;
      if (!B.idTouched) { B.range_id = p.suggested_id; const i = document.getElementById("range-id"); if (i) i.value = B.range_id; }
      if (!B.nameTouched) { B.name = p.default_name; const n = document.getElementById("range-name"); if (n) n.value = B.name; }
    } catch (e) {
      if (seq !== state.planSeq) return;
      state.planError = e.message;
    } finally {
      if (seq === state.planSeq) planEl.removeAttribute("aria-busy");
    }
    renderProfiles();
    checkId();
    renderPlan(planEl);
  }

  function laneColour(lane, index) {
    if (lane.tools) return "var(--cable-tools)";
    if (lane.label === "Attacker") return "var(--cable-attack)";
    return CABLES[index % CABLES.length];
  }

  function renderPlan(planEl) {
    const p = state.plan;
    planEl.replaceChildren(h("div", { class: "plan-head" }, h("h2", { id: "plan-title", text: "Range map" }),
      p ? h("span", { class: "muted small", text: p.firewall === "segmented" ? "Firewall: only allowed flows" : "Firewall: open between VLANs" }) : null));
    if (state.planError) planEl.append(h("div", { class: "notice bad", role: "alert", text: state.planError }));
    if (!p) { planEl.append(loadingBar("Working out what this lab needs…", true)); return; }

    const seen = new Set();
    let plain = 0;
    const map = h("div", { class: "map", role: "img", "aria-label": mapDescription(p) },
      h("div", { class: "router" }, h("strong", { text: "Router" }), h("span", { text: ".254 on each VLAN" }),
        h("span", { text: gb(p.router_ram) })),
      p.lanes.map((lane) => {
        const colour = laneColour(lane, lane.tools || lane.label === "Attacker" ? 0 : plain++);
        return h("div", { class: "lane", vars: { "--c": colour } },
          h("div", { class: "lane-head" }, h("strong", { text: lane.label }), h("span", { text: `VLAN ${lane.vlan}` }),
            h("span", { text: `10.x.${lane.vlan}.0/24` })),
          h("div", { class: "tags" }, lane.vms.map((vm) => {
            seen.add(vm.name);
            return h("div", { class: "tag" + (state.prevVms.size && !state.prevVms.has(vm.name) ? " is-new" : "") },
              h("div", { class: "tag-head" }, vmIcon(vm), h("b", { text: vm.hostname })),
              h("span", { class: "os", text: `${vm.os}, ${gb(vm.ram)}, .${vm.octet}` }),
              vm.tags.length ? h("span", { class: "roles", text: vm.tags.join(", ") }) : null);
          })));
      }));
    state.prevVms = seen;

    const m = p.memory || {};
    const total = m.total || p.ram;
    const usedElsewhere = m.total ? Math.max(0, m.total - (m.available || 0)) : 0;
    const over = m.available && p.ram > m.available - 2;
    const bar = h("div", { class: "ram-bar" + (over ? " over" : "") },
      h("div", { class: "legend" }, h("span", {}, h("strong", { text: gb(p.ram) }), " for this lab"),
        m.total ? h("span", { text: `${Math.round(m.available)} GB free of ${Math.round(m.total)} GB` }) : null),
      h("div", { class: "bar", "aria-hidden": "true", vars: {
        "--used": `${Math.min(100, (usedElsewhere / total) * 100)}%`,
        "--this": `${Math.min(100 - Math.min(100, (usedElsewhere / total) * 100), (p.ram / total) * 100)}%` } },
        h("span", { class: "used" }), h("span", { class: "this" })));

    const needed = h("ul", { class: "checklist" }, p.templates.map((t) => h("li", {},
      h("span", { text: t.label }),
      h("span", { class: "state " + (t.state === "ready" ? "state-ready" : t.state === "building" ? "state-building" : "state-missing"),
        text: t.state === "ready" ? "Ready" : t.state === "building" ? "Building" : t.state === "not built" ? "Needs building" : "Downloads first" }))));

    const est = p.estimate;
    const estimate = est ? h("div", { class: "estimate" }, icon("clock", "estimate-ico"),
      h("div", {}, h("strong", { text: `~${est.total}` }), " estimated build time",
        h("div", { class: "help", text: est.templates
          ? `about ${est.templates} building the templates it needs, then ${est.deploy} to deploy`
          : `about ${est.deploy} to deploy (templates already built)` }))) : null;

    planEl.append(map, bar,
      h("div", { class: "field" }, h("h3", { text: "Templates" }), needed),
      estimate,
      p.domains.length ? h("p", { class: "small muted", text: `Domain: ${p.domains.join(", ")}. Timezone: ${p.timezone}.` }) : h("p", { class: "small muted", text: `Timezone: ${p.timezone}.` }),
      ...p.warnings.map((w) => h("div", { class: "notice warn", text: w })),
      h("div", { id: "plan-footer", class: "field" }));
    renderPlanFooter(planEl);
  }

  function mapDescription(p) {
    return p.lanes.map((l) => `${l.label} (VLAN ${l.vlan}): ${l.vms.map((v) => `${v.hostname}, ${v.os}`).join("; ")}`).join(". ");
  }

  function renderPlanFooter(planEl) {
    const footer = planEl.querySelector("#plan-footer");
    const p = state.plan;
    if (!footer || !p) return;
    const B = state.build;
    footer.replaceChildren();
    if (p.missing_roles.length) {
      footer.append(h("div", { class: "notice bad", text: `These add-on roles aren't installed on the node: ${p.missing_roles.join(", ")}. Re-run setup.sh on the node, then reload this page.` }));
    }
    const missing = p.missing_templates;
    const waiting = missing.filter((t) => t.state !== "building");
    if (missing.length) {
      const msg = waiting.length
        ? `${plural(waiting.length, "template")} need${waiting.length === 1 ? "s" : ""} building before this lab can be built. ${missing.some((t) => t.template.startsWith("win")) ? "Windows takes about an hour each." : "Linux takes 10-20 minutes each."}`
        : "The templates this lab needs are building now. This page checks every 30 seconds.";
      const box = h("div", { class: "notice warn" }, h("span", { text: msg }));
      if (waiting.length) {
        const btn = h("button", { class: "btn", type: "button", text: `Download and build ${plural(waiting.length, "template")}` });
        btn.addEventListener("click", () => act(btn, async () => {
          const r = await api("POST", "/api/templates/build", { templates: waiting.map((t) => t.template) });
          toast(r.message);
          requestPlan(planEl);
        }, "Starting"));
        box.append(btn);
      }
      footer.append(box);
      if (!state.templatePoll) {
        state.templatePoll = every(30000, () => { if (document.querySelector(".plan")) requestPlan(planEl); });
      }
    }
    const blocked = missing.length || p.missing_roles.length || state.planError || !checkId();
    const build = h("button", { class: "btn btn-build btn-wide", type: "button", disabled: !!blocked, text: "Build lab" });
    build.addEventListener("click", () => act(build, async () => {
      try {
        const r = await api("POST", "/api/build", { blueprint: B.blueprint, options: B.options, addons: B.addons,
          dfir_template: B.dfir_template, ram_profile: B.ram_profile, range_id: B.range_id, name: B.name });
        toast(r.message);
        B.idTouched = false; B.nameTouched = false;
        location.hash = `#/labs/${encodeURIComponent(r.range_id)}`;
      } catch (e) {
        toast(e.message, "bad");
        requestPlan(planEl);
      }
    }, "Starting the build"));
    footer.append(build,
      h("p", { class: "help", text: "Ludus clones and configures everything. Small labs take 20-40 minutes; GOAD and labs with tooling can take a few hours." }));
  }

  // ---------------------------------------------------------------- labs
  async function viewLabs(selected) {
    const list = h("div", { class: "lab-list", "aria-label": "Labs" });
    const detail = h("section", { class: "detail", "aria-live": "off" });
    shell("labs", h("div", { class: "labs" }, h("div", { class: "field" }, h("h1", { text: "Your labs" }), list), detail));
    const drawList = async () => {
      let data;
      try { data = await api("GET", "/api/labs"); } catch (e) { list.replaceChildren(h("p", { class: "notice bad", text: e.message })); return; }
      if (!data.labs.length) {
        list.replaceChildren(h("div", { class: "panel" }, h("p", { text: "No labs yet." }),
          h("a", { class: "btn btn-build", href: "#/build", text: "Build your first lab" })));
        detail.replaceChildren();
        return;
      }
      list.replaceChildren(...data.labs.map((l) => h("a", { class: "lab-row", href: `#/labs/${encodeURIComponent(l.id)}`,
        "aria-current": l.id === selected ? "true" : false },
        h("span", { class: "id", text: l.id }), h("span", { class: `pill pill-${l.state.split(" ")[0]}`, text: l.state_label }),
        h("span", { class: "sub", text: `${l.name ? l.name + ". " : ""}${plural(l.vms, "VM")}, 10.${l.number}.0.0/16${l.testing ? ", testing mode on" : ""}` }))));
      if (!selected) { location.replace(`#/labs/${encodeURIComponent(data.labs[0].id)}`); }
    };
    await drawList();
    every(8000, drawList);
    if (selected) labDetail(selected, detail);
  }

  async function labDetail(id, box) {
    const enc = encodeURIComponent(id);
    const head = h("div", { class: "detail-head" });
    const toolbar = h("div", { class: "toolbar" });
    const deployBanner = h("div", {});
    const extra = h("div", {});
    const vmsPanel = h("div", { class: "panel" });
    const statsPanel = h("div", { class: "panel", hidden: true });
    const toolsPanel = h("div", { class: "panel" });
    const logBox = h("pre", { class: "log", tabindex: "0", "aria-label": "Build log" });
    const logDetails = h("details", { class: "panel" }, h("summary", {}, h("h3", { text: "Build log" })), logBox);
    const errorsBox = h("pre", { class: "log", hidden: true });
    box.replaceChildren(head, toolbar, deployBanner, extra, vmsPanel, statsPanel, toolsPanel, logDetails, errorsBox);
    let lab = null;
    let toolbarFor = "";
    let statsStarted = false;

    const loadLog = async () => {
      try {
        const r = await api("GET", `/api/labs/${enc}/log`);
        const atBottom = logBox.scrollHeight - logBox.scrollTop - logBox.clientHeight < 40;
        logBox.textContent = r.text || "Nothing logged yet.";
        if (atBottom) logBox.scrollTop = logBox.scrollHeight;
      } catch (e) { logBox.textContent = e.message; }
    };
    logDetails.addEventListener("toggle", () => { if (logDetails.open) loadLog(); });

    const run = (button, action, extraBody, busy) => act(button, async () => {
      const r = await api("POST", `/api/labs/${enc}/action`, Object.assign({ action }, extraBody || {}));
      toast(r.message);
      await draw();
    }, busy);

    const draw = async () => {
      try { lab = await api("GET", `/api/labs/${enc}`); }
      catch (e) { head.replaceChildren(h("div", { class: "notice bad", text: e.message })); return; }
      head.replaceChildren(
        h("div", {}, h("h2", {}, lab.id, lab.name ? h("span", { class: "name", text: lab.name }) : null),
          h("p", { class: "muted small", text: `Network 10.${lab.number}.0.0/16. Last built ${when(lab.last)}.${lab.testing ? " Testing mode is on." : ""}` })),
        h("span", { class: `pill pill-${lab.state.split(" ")[0]}`, text: lab.state_label }));

      const deploying = lab.state === "DEPLOYING";
      // Only rebuild the buttons when the lab's state changes, so a half-confirmed click survives the refresh.
      const signature = `${lab.state}|${lab.testing}`;
      if (signature !== toolbarFor) { toolbarFor = signature; drawToolbar(deploying); }
      drawPanels(deploying);
    };

    const drawToolbar = (deploying) => {
      const powerOn = h("button", { class: "btn", type: "button", text: "Power on" });
      powerOn.addEventListener("click", () => run(powerOn, "power_on", null, "Powering on"));
      const powerOff = h("button", { class: "btn", type: "button", text: "Power off" });
      powerOff.addEventListener("click", () => run(powerOff, "power_off", null, "Powering off"));
      const snap = h("button", { class: "btn", type: "button", text: "Take snapshot" });
      snap.addEventListener("click", () => snapshotForm());
      const back = h("button", { class: "btn", type: "button", text: "Roll back" });
      back.addEventListener("click", () => rollbackList());
      const testing = h("button", { class: "btn", type: "button", text: lab.testing ? "Turn testing mode off" : "Turn testing mode on",
        title: lab.testing ? "Rolls the VMs back to the snapshot taken when testing started" : "Snapshots the VMs and cuts Windows off the internet" });
      testing.addEventListener("click", () => run(testing, lab.testing ? "testing_stop" : "testing_start", null, lab.testing ? "Rolling back" : "Snapshotting"));
      const rebuild = confirmButton("Rebuild", "Click again to rebuild", "", (b) => run(b, "redeploy", null, "Starting"));
      const stop = deploying ? confirmButton("Stop the build", "Click again to stop", "btn-danger", (b) => run(b, "abort", null, "Stopping")) : null;
      const rdp = h("a", { class: "btn", href: `/api/labs/${enc}/rdp`, download: `${id}-rdp.zip`, text: "RDP files" });
      const errs = h("button", { class: "btn btn-quiet", type: "button", text: "Show build errors" });
      errs.addEventListener("click", () => act(errs, async () => {
        const r = await api("GET", `/api/labs/${enc}/errors`);
        errorsBox.hidden = false; errorsBox.textContent = r.text;
      }, "Loading"));
      const del = h("button", { class: "btn btn-danger", type: "button", text: "Delete lab" });
      del.addEventListener("click", () => deleteForm());
      toolbar.replaceChildren(...[powerOn, powerOff, snap, back, testing, rebuild, stop, rdp, errs, del].filter(Boolean));
    };

    const drawPanels = (deploying) => {
      if (deploying) {
        const n = lab.vms.length;
        deployBanner.replaceChildren(h("div", { class: "deploy-progress" },
          h("div", { class: "dp-head" }, icon("spark"), h("span", { text: "Building this lab…" })),
          h("div", { class: "progress" }, h("span")),
          h("p", { class: "dp-sub", text: n ? `${plural(n, "VM")} so far. Ludus clones and configures each one in turn; the log below updates live.`
            : "Ludus is setting up the range. VMs appear here as they're created, and the log below updates live." })));
      } else {
        deployBanner.replaceChildren();
      }
      const canReset = lab.vms.some((v) => v.can_reset);
      const cols = canReset ? ["VM", "IP", "Power", "Password"] : ["VM", "IP", "Power"];
      vmsPanel.replaceChildren(h("h3", { text: "VMs" }), lab.vms.length
        ? h("table", {}, h("thead", {}, h("tr", {}, cols.map((t) => h("th", { text: t })))),
          h("tbody", {}, lab.vms.map((v) => {
            const cells = [
              h("td", {}, h("span", { class: "vm-name" }, vmIcon(v),
                h("span", { text: v.name }), v.router ? h("span", { class: "muted small", text: "router" }) : null)),
              h("td", {}, v.ip ? h("span", { class: "copyable", title: "Copy", onclick: () => copy(v.ip), text: v.ip }) : "-"),
              h("td", {}, h("span", { class: "dot" + (v.on ? " on" : "") }), v.on ? "On" : "Off"),
            ];
            if (canReset) {
              cells.push(h("td", {}, v.can_reset
                ? h("button", { class: "btn btn-quiet btn-sm", type: "button", disabled: !v.on,
                    title: v.on ? "" : "Power the VM on first", onclick: () => resetForm(v) }, icon("key"), "Reset")
                : null));
            }
            return h("tr", {}, ...cells);
          })))
        : h("p", { class: "muted", text: deploying ? "VMs appear here as Ludus creates them." : "No VMs. Rebuild to create them." }));

      if (lab.monitoring && !statsStarted && lab.vms.length && !deploying) {
        statsStarted = true;
        loadStats();
        every(5000, () => { if (document.body.contains(box)) loadStats(); });
      }

      toolsPanel.hidden = !lab.tools.length;
      toolsPanel.replaceChildren(h("h3", { text: "Security tools" }),
        h("table", {}, h("thead", {}, h("tr", {}, ["Tool", "Address", "Username", "Password"].map((t) => h("th", { text: t })))),
          h("tbody", {}, lab.tools.map((t) => h("tr", {}, h("td", { text: t.label }),
            h("td", {}, t.url.startsWith("https://") ? h("a", { href: t.url, target: "_blank", rel: "noopener", text: t.url }) : `RDP to ${t.ip}`),
            h("td", { text: t.username }),
            h("td", {}, h("span", { class: "copyable", title: "Copy", onclick: () => copy(t.password), text: t.password })))))),
        h("p", { class: "help", text: "Connect with WireGuard first (see Connect). The tools use self-signed certificates, so your browser will warn once." }));

      if (deploying && !logDetails.open) { logDetails.open = true; }
      if (logDetails.open) loadLog();
    };

    function snapshotForm() {
      const name = h("input", { type: "text", id: "snap-name", value: "clean", maxlength: "40" });
      const desc = h("input", { type: "text", id: "snap-desc", value: "", maxlength: "200" });
      const go = h("button", { class: "btn btn-build", type: "button", text: "Take snapshot" });
      go.addEventListener("click", () => run(go, "snapshot_create", { name: name.value.trim(), description: desc.value }, "Snapshotting every VM").then(() => { if (extra.contains(go)) extra.replaceChildren(); }));
      extra.replaceChildren(h("div", { class: "panel" }, h("h3", { text: "Take a snapshot of every VM" }),
        h("div", { class: "inline-form" },
          h("div", { class: "field" }, h("label", { for: "snap-name", text: "Name" }), name),
          h("div", { class: "field" }, h("label", { for: "snap-desc", text: "Description (optional)" }), desc),
          go, h("button", { class: "btn btn-quiet", type: "button", text: "Cancel", onclick: () => extra.replaceChildren() }))));
      name.focus();
    }

    async function rollbackList() {
      const panel = h("div", { class: "panel" }, h("h3", { text: "Roll back to a snapshot" }), loadingBar("Loading snapshots…"));
      extra.replaceChildren(panel);
      let r;
      try { r = await api("GET", `/api/labs/${enc}/snapshots`); }
      catch (e) { panel.replaceChildren(h("div", { class: "notice bad", text: e.message })); return; }
      panel.replaceChildren(h("h3", { text: "Roll back to a snapshot" }));
      if (!r.snapshots.length) { panel.append(h("p", { class: "muted", text: "This lab has no snapshots yet." })); }
      else {
        panel.append(h("table", {}, h("tbody", {}, r.snapshots.map((s) => h("tr", {},
          h("td", {}, h("b", { text: s.name }), s.description ? h("div", { class: "help", text: s.description }) : null),
          h("td", { class: "muted", text: `${plural(s.vms, "VM")}${s.ram ? ", with memory" : ""}` }),
          h("td", { class: "muted", text: s.time ? new Date(s.time * 1000).toLocaleString() : "" }),
          h("td", {}, confirmButton("Roll back", "Click again: changes since then are lost", "", (b) => run(b, "snapshot_revert", { name: s.name }, "Rolling back")
            .then(() => { if (extra.contains(b)) extra.replaceChildren(); }))))))));
      }
      panel.append(h("button", { class: "btn btn-quiet", type: "button", text: "Close", onclick: () => extra.replaceChildren() }));
    }

    function deleteForm() {
      const input = h("input", { type: "text", id: "del-confirm", autocomplete: "off", spellcheck: "false" });
      const go = h("button", { class: "btn btn-danger", type: "button", text: "Delete lab", disabled: true });
      input.addEventListener("input", () => { go.disabled = input.value !== id; });
      go.addEventListener("click", () => act(go, async () => {
        const r = await api("POST", `/api/labs/${enc}/action`, { action: "delete", confirm: input.value });
        toast(r.message);
        location.hash = "#/labs";
      }, "Deleting"));
      extra.replaceChildren(h("div", { class: "panel" }, h("h3", { text: `Delete ${id}` }),
        h("p", { text: "This deletes every VM in the lab and the lab itself. It can't be undone." }),
        h("div", { class: "inline-form" },
          h("div", { class: "field" }, h("label", { for: "del-confirm", text: `Type ${id} to confirm` }), input),
          go, h("button", { class: "btn btn-quiet", type: "button", text: "Cancel", onclick: () => extra.replaceChildren() }))));
      input.focus();
    }

    const bytes = (n) => {
      if (n === null || n === undefined) return "-";
      const u = ["B", "KB", "MB", "GB", "TB"]; let i = 0, v = n;
      while (v >= 1024 && i < u.length - 1) { v /= 1024; i++; }
      return `${v >= 10 || i === 0 ? Math.round(v) : v.toFixed(1)} ${u[i]}`;
    };
    const dur = (s) => {
      if (!s) return "just now";
      const d = Math.floor(s / 86400), hrs = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60);
      if (d) return `${d}d ${hrs}h`;
      if (hrs) return `${hrs}h ${m}m`;
      return `${Math.max(1, m)}m`;
    };
    const minibar = (label, pct, value) => h("div", { class: "minibar" },
      h("span", { class: "mb-label", text: label }),
      h("div", { class: "mb-track", vars: { "--pct": `${Math.min(100, Math.max(0, pct || 0))}%` } },
        h("span", { class: pct != null && pct >= 85 ? "mb-fill hot" : "mb-fill" })),
      h("span", { class: "mb-value", text: value }));

    const loadStats = async () => {
      let s;
      try { s = await api("GET", `/api/labs/${enc}/stats`); } catch { return; }
      if (!s.enabled || !s.vms.length) { statsPanel.hidden = true; return; }
      statsPanel.hidden = false;
      const rows = s.vms.map((v) => {
        const running = v.status === "running";
        const cpuPct = running && v.cpu != null ? Math.round(v.cpu * 100) : null;
        const memPct = running && v.mem && v.maxmem ? Math.round((v.mem / v.maxmem) * 100) : null;
        return h("div", { class: "stat-row" + (running ? "" : " is-off") },
          h("div", { class: "stat-name" }, h("b", { text: v.name }),
            h("span", { class: "muted small", text: running ? `up ${dur(v.uptime)}` : (v.status || "stopped") })),
          running
            ? h("div", { class: "stat-metrics" },
                minibar("CPU", cpuPct, cpuPct == null ? "-" : `${cpuPct}%`),
                minibar("RAM", memPct, memPct == null ? "-" : `${bytes(v.mem)} / ${bytes(v.maxmem)}`),
                h("div", { class: "stat-net muted small" }, `↓ ${bytes(v.netin)}   ↑ ${bytes(v.netout)}`))
            : h("div", { class: "stat-metrics muted small" }, "Powered off"));
      });
      statsPanel.replaceChildren(
        h("div", { class: "detail-head" }, h("h3", {}, icon("activity"), "Live stats"),
          h("span", { class: "muted small", text: "every 5s" })),
        h("div", { class: "stats" }, ...rows));
    };

    function resetForm(v) {
      const isDc = v.is_dc;
      let mode = isDc ? "domain" : "local";
      const acct = h("input", { type: "text", id: "reset-acct", spellcheck: "false", autocomplete: "off", maxlength: "64" });
      const pass = h("input", { type: "password", id: "reset-pass", autocomplete: "new-password", maxlength: "128" });
      const show = h("input", { type: "checkbox", id: "reset-show",
        onchange: (e) => { pass.type = e.target.checked ? "text" : "password"; } });
      const note = h("p", { class: "help" });
      const err = h("p", { class: "error", role: "alert" });
      const go = h("button", { class: "btn btn-build", type: "button", text: "Reset password" });

      const applyMode = () => {
        if (mode === "domain") {
          acct.placeholder = "domain username, e.g. jsmith";
          note.textContent = `Resets a domain account in ${v.domain}. Needs 8+ characters with an upper and lower letter and a digit. Runs on the domain controller.`;
        } else {
          if (!acct.value) acct.value = v.default_account || "";
          acct.placeholder = v.default_account || "username";
          note.textContent = v.family === "windows"
            ? "Resets a local Windows account on this machine, not a domain account."
            : "Resets a local account on this machine.";
        }
      };
      applyMode();

      const modePick = isDc ? h("div", { class: "field" },
        h("span", { class: "field-label", id: "reset-mode-label", text: "Account type" }),
        h("div", { class: "segmented", role: "radiogroup", "aria-labelledby": "reset-mode-label" },
          [["domain", "Domain account"], ["local", "Local to this VM"]].map(([val, label]) =>
            h("label", {}, h("input", { type: "radio", name: "reset-mode", value: val, checked: mode === val,
              onchange: () => { mode = val; acct.value = ""; applyMode(); } }), label)))) : null;

      go.addEventListener("click", () => act(go, async () => {
        err.textContent = "";
        const action = mode === "domain" ? "reset_domain_password" : "reset_os_password";
        try {
          const r = await api("POST", `/api/labs/${enc}/action`,
            { action, vmid: v.id, username: acct.value.trim(), password: pass.value });
          toast(r.message);
          extra.replaceChildren();
        } catch (e) { err.textContent = e.message; }
      }, "Resetting"));

      extra.replaceChildren(h("div", { class: "panel reveal" },
        h("h3", {}, icon("key"), `Reset a password on ${v.name}`),
        modePick,
        h("div", { class: "inline-form" },
          h("div", { class: "field" }, h("label", { for: "reset-acct", text: "Account" }), acct),
          h("div", { class: "field" }, h("label", { for: "reset-pass", text: "New password" }), pass)),
        h("label", { class: "check", for: "reset-show" }, show, h("span", { text: "Show password" })),
        note, err,
        h("div", { class: "inline-form" }, go,
          h("button", { class: "btn btn-quiet", type: "button", text: "Cancel", onclick: () => extra.replaceChildren() }))));
      acct.focus();
    }

    await draw();
    every(5000, () => { if (document.body.contains(box)) draw(); });
  }

  // ---------------------------------------------------------------- templates
  async function viewTemplates() {
    const table = h("div", { class: "panel" });
    const add = h("div", { class: "panel" });
    const logBox = h("pre", { class: "log", tabindex: "0", "aria-label": "Template build log" });
    const logDetails = h("details", { class: "panel" }, h("summary", {}, h("h3", { text: "Build log" })), logBox);
    shell("templates", h("div", { class: "detail" },
      h("div", { class: "field" }, h("h1", { text: "Templates" }),
        h("p", { class: "muted", text: "Each operating system is installed once from its official ISO, then every lab clones it. Windows takes about an hour; Linux 10-20 minutes." })),
      table, add, logDetails));

    const loadLog = async () => {
      try { const r = await api("GET", "/api/templates/log"); logBox.textContent = r.text || "Nothing logged yet."; logBox.scrollTop = logBox.scrollHeight; }
      catch (e) { logBox.textContent = e.message; }
    };
    logDetails.addEventListener("toggle", () => { if (logDetails.open) loadLog(); });

    const draw = async () => {
      let d;
      try { d = await api("GET", "/api/templates"); } catch (e) { table.replaceChildren(h("div", { class: "notice bad", text: e.message })); return; }
      const notBuilt = d.templates.filter((t) => t.state === "not built");
      const all = h("button", { class: "btn btn-build", type: "button", disabled: !notBuilt.length,
        text: notBuilt.length ? `Build ${plural(notBuilt.length, "template")} that ${notBuilt.length === 1 ? "isn't" : "aren't"} built` : "Everything is built or building" });
      all.addEventListener("click", () => act(all, async () => { toast((await api("POST", "/api/templates/build", { all: true })).message); await draw(); }, "Starting"));
      table.replaceChildren(h("div", { class: "detail-head" }, h("h3", { text: "On this node" }), all),
        h("table", {}, h("thead", {}, h("tr", {}, ["Operating system", "Template", "Status"].map((t) => h("th", { text: t })))),
          h("tbody", {}, d.templates.map((t) => h("tr", {}, h("td", { text: t.label }), h("td", { class: "muted", text: t.template }),
            h("td", {}, h("span", { class: "state " + (t.state === "ready" ? "state-ready" : t.state === "building" ? "state-building" : "state-missing"),
              text: t.state === "ready" ? "Ready" : t.state === "building" ? "Building" : "Not built" })))))));

      if (d.available.length) {
        const select = h("select", { id: "add-template" }, d.available.map((t) => h("option", { value: t.template, text: t.label })));
        const go = h("button", { class: "btn", type: "button", text: "Download and build" });
        go.addEventListener("click", () => act(go, async () => {
          toast((await api("POST", "/api/templates/build", { templates: [select.value] })).message);
          await draw();
        }, "Starting"));
        add.hidden = false;
        add.replaceChildren(h("h3", { text: "Add another version" }),
          h("p", { class: "muted", text: "Get a version ready before you need it. The lab builder also offers this when you pick a version that isn't here yet." }),
          h("div", { class: "inline-form" }, h("div", { class: "field" }, h("label", { for: "add-template", text: "Operating system" }), select), go));
      } else { add.hidden = true; }
      if (d.templates.some((t) => t.state === "building") && !logDetails.open) logDetails.open = true;
      if (logDetails.open) loadLog();
    };
    await draw();
    every(15000, () => { if (document.body.contains(table)) draw(); });
  }

  // ---------------------------------------------------------------- connect
  function viewConnect() {
    const creds = [["Windows local admin", "localuser", "password"], ["Domain admin", "<DOMAIN>\\domainadmin", "password"],
      ["Domain user", "<DOMAIN>\\domainuser", "password"], ["Debian", "debian", "debian"], ["Kali", "kali", "kali"],
      ["Kali desktop in a browser", "kali", "password"]];
    shell("connect", h("div", { class: "detail" },
      h("h1", { text: "Connect to your labs" }),
      h("div", { class: "panel" },
        h("h3", { text: "1. Get on the lab network" }),
        h("p", { text: "Every lab sits on its own private network inside the node. WireGuard puts your computer on those networks." }),
        h("div", { class: "toolbar" }, h("a", { class: "btn btn-build", href: "/api/wireguard", download: "ludus-wireguard.conf", text: "Download WireGuard config" }),
          h("a", { class: "btn", href: "https://www.wireguard.com/install/", target: "_blank", rel: "noopener", text: "Get the WireGuard app" })),
        h("p", { class: "muted", text: "Import the file into the WireGuard app and turn the tunnel on." })),
      h("div", { class: "panel" },
        h("h3", { text: "2. Open the VMs" }),
        h("p", { text: "Your labs list each VM's IP address. Windows: Remote Desktop, or download ready-made RDP files from the lab's page. Linux: SSH. Kali's desktop: https://<kali IP>:8444 in your browser." })),
      h("div", { class: "panel" },
        h("h3", { text: "Default logins" }),
        h("table", {}, h("thead", {}, h("tr", {}, ["Machine", "Username", "Password"].map((t) => h("th", { text: t })))),
          h("tbody", {}, creds.map(([m, u, p]) => h("tr", {}, h("td", { text: m }), h("td", { text: u }), h("td", { text: p }))))),
        h("p", { class: "help", text: "Security tool logins (Kibana, Wazuh, Velociraptor) are on each lab's page. These passwords are deliberately weak: keep labs on their private networks." })),
    ));
  }

  // ---------------------------------------------------------------- updates
  function cmdLine(text) {
    return h("div", { class: "cmd" }, h("code", { text }),
      h("button", { class: "btn btn-quiet btn-sm", type: "button", text: "Copy", onclick: () => copy(text) }));
  }

  function featureRequestPanel() {
    const ta = h("textarea", { class: "feature-input", rows: "5",
      placeholder: "e.g. add Windows 7 as a bring-your-own-ISO option, or a Linux Mint template, or a new app in the installer..." });
    const out = h("p", { class: "help" });
    const copyBtn = h("button", { class: "btn btn-build", type: "button", text: "Copy request" });
    copyBtn.addEventListener("click", () => {
      const body = ta.value.trim();
      if (!body) { out.textContent = "Type what you'd like first."; return; }
      copy(`Lab builder feature request (v${state.version || "?"}, node ${state.node || "?"}):\n\n${body}`);
      out.textContent = "Copied — paste it to Claude. The change comes back as the next update you pull.";
    });
    return h("div", { class: "panel" },
      h("h3", {}, icon("spark"), " Request a feature"),
      h("p", { class: "muted", text: "Got an idea? Describe it, copy it, and send it to Claude. Improvements come back as updates you pull with one command — no file-copying." }),
      ta, h("div", { class: "toolbar" }, copyBtn), out);
  }

  function refreshPanel() {
    const go = h("button", { class: "btn", type: "button" }, icon("refresh"), " Refresh catalogue");
    go.addEventListener("click", () => act(go, async () => {
      toast((await api("POST", "/api/refresh", {})).message);
    }, "Refreshing"));
    return h("div", { class: "panel" }, h("h3", {}, icon("stack"), " Catalogue & packages"),
      h("p", { class: "muted", text: "Apps and Linux packages already install their latest version at build time. This pulls the latest template and blueprint definitions from the sources so new builds use current images too." }),
      h("div", { class: "toolbar" }, go));
  }

  function dangerPanel() {
    const action = (label, phrase, level, help) => {
      const input = h("input", { type: "text", class: "danger-confirm", autocomplete: "off", spellcheck: "false",
        "aria-label": `Type ${phrase} to confirm`, placeholder: `type "${phrase}" to confirm` });
      const go = h("button", { class: "btn btn-danger", type: "button", text: label, disabled: true });
      input.addEventListener("input", () => { go.disabled = input.value.trim().toLowerCase() !== phrase; });
      go.addEventListener("click", () => act(go, async () => {
        toast((await api("POST", "/api/reset", { level, confirm: input.value.trim() })).message);
        input.value = ""; go.disabled = true;
      }, "Working - this can take a few minutes"));
      return h("div", { class: "danger-action" },
        h("div", { class: "danger-text" }, h("b", { text: label }), h("p", { class: "help", text: help })),
        h("div", { class: "inline-form" }, input, go));
    };
    return h("div", { class: "panel danger-zone" }, h("h3", {}, icon("warning", "danger-ico"), " Danger zone"),
      h("p", { class: "muted", text: "These wipe things the lab builder made on this node. They never touch Proxmox itself, the Ludus install, or any VMs you created outside the lab builder. There's no undo." }),
      action("Delete all labs", "delete all labs", "labs",
        "Destroys every lab and its VMs. Your built templates are kept, so you can rebuild labs quickly."),
      action("Reset the node", "reset everything", "full",
        "Destroys every lab and every built template - a clean slate. Templates will need building again from scratch (hours)."));
  }

  // Kick off a web update, then watch the version until the node comes back.
  async function startUpdate(btn, status, fromVersion) {
    btn.disabled = true;
    btn.replaceChildren(h("span", { class: "spinner", "aria-hidden": "true" }), "Updating...");
    status.classList.remove("bad");
    try {
      const r = await api("POST", "/api/update", {});
      status.textContent = r.message || "Update started.";
    } catch (e) {
      status.classList.add("bad");
      status.textContent = e.message;
      btn.disabled = false;
      btn.replaceChildren(icon("download"), " Update now");
      return;
    }
    // Poll /api/updates. The web service stays up serving the old version for
    // most of the run, then restarts briefly at the end. A failed poll after
    // we've been waiting means that restart; a changed version means success.
    let tries = 0, sawRestart = false;
    const maxTries = 120; // ~6 minutes at 3s
    const timer = setInterval(async () => {
      tries += 1;
      if (tries === 30 && !sawRestart) {
        status.textContent = "Still working — fetching updates and re-applying the config. This can take a few minutes.";
      }
      try {
        const now = await api("GET", "/api/updates");
        if (now && now.version && now.version !== fromVersion) {
          clearInterval(timer);
          status.textContent = `Updated to v${now.version}. Reloading...`;
          setTimeout(() => location.reload(), 1200);
          return;
        }
        if (sawRestart) {
          clearInterval(timer);
          status.textContent = `Update finished — you're on the latest version (v${(now && now.version) || fromVersion}).`;
          btn.disabled = false;
          btn.replaceChildren(icon("download"), " Update now");
          return;
        }
      } catch {
        sawRestart = true; // service is restarting as the update lands
      }
      if (tries >= maxTries) {
        clearInterval(timer);
        status.textContent = "This is taking longer than expected. Check `sudo systemctl status ludus-lab-update` on the node, then reload this page.";
        btn.disabled = false;
        btn.replaceChildren(icon("download"), " Update now");
      }
    }, 3000);
  }

  async function viewUpdates() {
    const panel = h("div", { class: "panel" }, loadingBar("Loading…"));
    shell("updates", h("div", { class: "detail" }, h("h1", {}, icon("download"), " Updates"),
      panel, refreshPanel(), featureRequestPanel(), state.admin ? dangerPanel() : null));
    let u;
    try { u = await api("GET", "/api/updates"); }
    catch (e) { panel.replaceChildren(h("div", { class: "notice bad", text: e.message })); return; }

    const rows = [
      h("div", { class: "detail-head" }, h("h3", { text: "This install" }),
        h("span", { class: "pill pill-SUCCESS", text: "v" + u.version })),
      h("p", { class: "muted", text: "The lab builder, catalogue and launcher running on this node." }),
    ];
    if (u.newer) {
      rows.push(h("div", { class: "notice warn" }, h("strong", { text: `Version ${u.latest} is available.` }),
        ` You're on ${u.version}.`));
    } else if (u.git && u.latest) {
      rows.push(h("div", { class: "notice ok", text: `You're on the latest version (${u.latest}).` }));
    }
    if (u.git && !state.admin) {
      rows.push(h("p", { class: "help", text: "Your admin keeps this install up to date." }));
    } else if (u.git) {
      rows.push(h("h3", { text: "Update" }));
      if (u.web_update) {
        const status = h("p", { class: "help update-status" });
        const btn = h("button", { class: "btn btn-build", type: "button" }, icon("download"), " Update now");
        btn.addEventListener("click", () => startUpdate(btn, status, u.version));
        rows.push(
          h("p", { text: "Pull the latest and re-apply it. It's safe to run any time and never touches running labs — it just updates the builder, catalogue and launcher:" }),
          h("div", { class: "toolbar" }, btn),
          status,
          h("details", { class: "cmd-fallback" },
            h("summary", { class: "help", text: "Prefer the command line?" }),
            h("p", { class: "help", text: "You can also run this on the node:" }),
            cmdLine(u.command)));
      } else {
        rows.push(
          h("p", { text: "On the node, run this — it pulls the latest and re-applies everything. It's safe to run any time and never touches running labs:" }),
          cmdLine(u.command));
      }
      if (u.repo) rows.push(h("p", { class: "help", text: `Updates come from ${u.repo}.` }));
    } else {
      rows.push(h("h3", { text: "Turn on one-command updates" }),
        h("p", { text: "This copy wasn't set up from Git yet, so it can't pull updates. Set it up once (the README's “Updating” section has the full walkthrough):" }),
        h("ol", { class: "steps-list" },
          h("li", {}, "Install Git: ", cmdLine("apt-get install -y git")),
          h("li", {}, "Clone the repo and run ", h("code", { text: "./setup.sh" }), " from the clone once."),
          h("li", {}, "After that, ", h("code", { text: "sudo lab-update" }), " does everything.")));
    }
    panel.replaceChildren(...rows);
  }

  // ---------------------------------------------------------------- first run
  async function viewSetup() {
    clearTimers();
    let ov = {}, cat = {};
    try { ov = await api("GET", "/api/overview"); } catch { /* degrade gracefully */ }
    try { cat = await api("GET", "/api/catalog"); } catch { /* degrade gracefully */ }
    const labs = cat.labs || [];
    const svcCount = labs.filter((l) => l.kind === "service").length;
    const labCount = labs.filter((l) => l.kind !== "service").length;
    const ready = (ov.templates || {}).ready || 0;
    const cpu = ov.cpu || {}, mem = ov.memory || {};

    const finish = async (hash) => {
      try { await api("POST", "/api/setup/done", {}); } catch { /* best effort */ }
      state.firstRun = false;
      if (location.hash === (hash || "#/build")) route(); else location.hash = hash || "#/build";
    };
    const check = (ok, text) => h("div", { class: "ready-row" },
      icon(ok ? "check" : "warning", ok ? "ready-ok" : "ready-warn"), h("span", { text }));
    const startCard = (glyph, title, body, hash) => {
      const card = h("button", { class: "start-card", type: "button" },
        icon(glyph, "start-ico"),
        h("span", { class: "start-text" }, h("span", { class: "start-title", text: title }),
          h("span", { class: "start-body", text: body })));
      card.addEventListener("click", () => finish(hash));
      return card;
    };
    const nodeLine = cpu.cores
      ? `Connected to ${state.node || "your node"} — ${cpu.cores} cores, ${Math.round(mem.total || 0)} GB RAM`
      : `Connected to ${state.node || "your node"}`;

    app.replaceChildren(h("div", { class: "setup" },
      netArt(),
      h("div", { class: "auth-top" }, themeToggle()),
      h("section", { class: "setup-inner reveal" },
        h("div", { class: "auth-badge" }, icon("logo", "brand-logo"), h("span", { text: "Lab builder" })),
        h("h1", { class: "setup-title", text: "You're all set up." }),
        h("p", { class: "lead", text: "Your node is connected and the catalogue is installed. Build a cyber-range lab to learn and break things safely, or stand up a home service for yourself and your family." }),
        h("div", { class: "ready" },
          check(true, nodeLine),
          check(true, `Catalogue ready — ${labCount} labs and ${svcCount} services`),
          check(ready > 0, ready > 0 ? `${ready} VM templates built and ready to use` : "Templates build the first time you need them")),
        h("div", { class: "start-cards" },
          startCard("build", "Build a lab", "Active Directory, GOAD or a segmented Linux network to practise on.", "#/build"),
          startCard("cube", "Stand up a service", "A Minecraft server, Jellyfin media library or a file share.", "#/build")),
        state.admin
          ? h("p", { class: "setup-foot" }, "Setting this up for other people? ",
              h("a", { href: "#/people", onclick: (e) => { e.preventDefault(); finish("#/people"); } }, "Create logins for family or friends"), ".")
          : null,
        h("button", { class: "btn btn-quiet setup-skip", type: "button", text: "Skip to the console",
          onclick: () => finish("#/build") }))));
  }

  // ---------------------------------------------------------------- people
  async function viewAccounts() {
    if (!state.admin) { location.hash = "#/build"; return; }
    const panel = h("div", { class: "panel" }, loadingBar("Loading…"));
    shell("people", h("div", { class: "detail" },
      h("h1", {}, icon("people"), " People"),
      h("p", { class: "muted", text: "Create a sign-in for family or friends so they can build and manage their own labs and services — without sharing your Proxmox password." }),
      panel));

    const load = async () => {
      let data;
      try { data = await api("GET", "/api/accounts"); }
      catch (e) { panel.replaceChildren(h("div", { class: "notice bad", text: e.message })); return; }

      const uname = h("input", { type: "text", id: "acc-user", autocomplete: "off", spellcheck: "false" });
      const pass = h("input", { type: "password", id: "acc-pass", autocomplete: "new-password" });
      const err = h("p", { class: "error" });
      const addBtn = h("button", { class: "btn btn-build", type: "submit", text: "Create login" });
      const form = h("form", { class: "account-new" },
        h("div", { class: "field" }, h("label", { for: "acc-user", text: "Username" }), uname,
          h("p", { class: "help", text: "Lowercase letters, digits, - and _." })),
        h("div", { class: "field" }, h("label", { for: "acc-pass", text: "Password" }), pass,
          h("p", { class: "help", text: "At least 8 characters." })),
        h("div", { class: "account-actions" }, addBtn), err);
      form.addEventListener("submit", (ev) => {
        ev.preventDefault(); err.textContent = "";
        act(addBtn, async () => {
          try {
            await api("POST", "/api/accounts", { username: uname.value.trim(), password: pass.value });
            uname.value = ""; pass.value = ""; toast("Login created."); load();
          } catch (e) { err.textContent = e.message; }
        }, "Creating");
      });

      const rows = [h("h3", { text: "Add a login" }), form, h("h3", { text: "Sign-ins you've created" })];
      if (!data.accounts.length) {
        rows.push(h("p", { class: "muted", text: "None yet. Your own Proxmox admin account always works." }));
      } else {
        rows.push(h("ul", { class: "account-list" }, data.accounts.map((a) => {
          const del = h("button", { class: "btn btn-danger btn-sm", type: "button", text: "Remove" });
          del.addEventListener("click", () => act(del, async () => {
            try { await api("DELETE", `/api/accounts/${encodeURIComponent(a.username)}`); toast(`Removed ${a.username}.`); load(); }
            catch (e) { toast(e.message, "bad"); }
          }, "Removing"));
          return h("li", { class: "account-row" },
            h("span", { class: "acc-id" }, icon("people", "acc-ico"), h("span", { class: "acc-name", text: a.username })), del);
        })));
      }
      panel.replaceChildren(...rows);
    };
    load();
  }

  start();
})();
