#!/usr/bin/env python3
"""
Lab launcher web GUI.

A small HTTPS server for the Proxmox/Ludus node. Sign in with a Proxmox
account (root, or a Ludus admin), pick a lab, choose options and add-ons, and
click Build. All the lab logic lives in lib/labcore.py, shared with the `lab`
terminal menu; this file is only the web layer.

Runs as the unprivileged `ludus-lab` user under systemd (see setup.sh).
Settings come from environment variables:
  LAB_WEB_BIND, LAB_WEB_PORT          where to listen (0.0.0.0:8443)
  LAB_WEB_CERT, LAB_WEB_KEY           TLS certificate and key
  LAB_WEB_PLAIN_HTTP=1                serve plain HTTP (testing only)
  PROXMOX_URL                         where to check sign-ins (https://127.0.0.1:8006)
  LAB_WEB_TMP                         scratch space for downloads
"""

import hashlib
import http.server
import json
import mimetypes
import os
import re
import secrets
import shutil
import socket
import ssl
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict, deque

HERE = os.path.dirname(os.path.realpath(__file__))
for _candidate in (os.environ.get("LAB_LIB"), os.path.join(HERE, "..", "lib"), "/opt/ludus-homelab/lib"):
    if _candidate and os.path.isfile(os.path.join(_candidate, "labcore.py")):
        sys.path.insert(0, os.path.abspath(_candidate))
        break

import labcore as core  # noqa: E402

STATIC_DIR = os.path.join(HERE, "static")
BIND = os.environ.get("LAB_WEB_BIND", "0.0.0.0")
PORT = int(os.environ.get("LAB_WEB_PORT", "8443"))
CERT = os.environ.get("LAB_WEB_CERT", "/etc/ludus-lab/tls/cert.pem")
KEY = os.environ.get("LAB_WEB_KEY", "/etc/ludus-lab/tls/key.pem")
PLAIN_HTTP = os.environ.get("LAB_WEB_PLAIN_HTTP") == "1"
PROXMOX_URL = os.environ.get("PROXMOX_URL", "https://127.0.0.1:8006").rstrip("/")
PROXMOX_NODE = os.environ.get("PROXMOX_NODE", "")
PROXMOX_VM_POOL = os.environ.get("PROXMOX_VM_POOL", "local")
PROXMOX_TOKEN_ID = os.environ.get("PROXMOX_TOKEN_ID", "")
PROXMOX_TOKEN_SECRET = os.environ.get("PROXMOX_TOKEN_SECRET", "")
LAB_UPDATE_DIR = os.environ.get("LAB_UPDATE_DIR", "")
LAB_UPDATE_GIT = os.environ.get("LAB_UPDATE_GIT") == "1"
LAB_UPDATE_REPO = os.environ.get("LAB_UPDATE_REPO", "")
LAB_UPDATE_WEB = os.environ.get("LAB_UPDATE_WEB") == "1"
LAB_UPDATE_SYSCTL = os.environ.get("LAB_UPDATE_SYSCTL", "")
TMP_DIR = os.environ.get("LAB_WEB_TMP", "/var/lib/ludus-lab/tmp")
SESSION_TTL = 12 * 3600
MAX_BODY = 64 * 1024
COOKIE = "lab_session"
LAB_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
TEMPLATE_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9.\-]*-template$")
# What a VM login name may contain, for password resets (local and AD accounts).
ACCOUNT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._\\ -]{0,63}$")

LANE_NAMES = {
    "/ad-basic": {10: "Domain network", 99: "Attacker"},
    "/goad": {10: "GOAD forests"},
    "/linux-net": {10: "DMZ", 20: "Internal", 30: "Management"},
}
STATE_WORDS = {"SUCCESS": "Ready", "DEPLOYING": "Building", "ERROR": "Failed", "ABORTED": "Stopped",
               "DESTROYING": "Deleting", "DESTROYED": "Deleted", "NEVER DEPLOYED": "Not built"}


# --------------------------------------------------------------------------
# Small caches so the page doesn't hammer Ludus on every keystroke
# --------------------------------------------------------------------------
class Cache:
    def __init__(self):
        self._lock = threading.Lock()
        self._data = {}

    def get(self, key, ttl, loader):
        now = time.time()
        with self._lock:
            hit = self._data.get(key)
            if hit and now - hit[0] < ttl:
                return hit[1]
        value = loader()
        with self._lock:
            self._data[key] = (time.time(), value)
        return value

    def drop(self, *prefixes):
        with self._lock:
            for key in list(self._data):
                if any(str(key).startswith(p) for p in prefixes):
                    del self._data[key]


CACHE = Cache()


def template_states():
    return CACHE.get("templates", 10, core.template_states)


def blueprints():
    return CACHE.get("blueprints", 30, lambda: core.ludus_json("blueprint", "list") or [])


def blueprint_config(bp_id):
    def load():
        raw = core.ludus_json("blueprint", "config", "get", bp_id)
        return core.load_yaml(raw.get("result", "") if isinstance(raw, dict) else "")
    return CACHE.get(f"bpcfg:{bp_id}", 60, load)


def ranges():
    return CACHE.get("ranges", 4, core.list_ranges)


def installed_roles_missing(cfg):
    key = "roles:" + hashlib.sha1(json.dumps(sorted(
        str(r) for v in core.vms(cfg) for r in core.role_names(v)), sort_keys=True).encode()).hexdigest()
    return CACHE.get(key, 30, lambda: core.missing_roles(cfg))


def allowed_users():
    def load():
        users = {"root@pam"}
        try:
            for u in core.ludus_json("users", "list", "all") or []:
                if u.get("isAdmin") and u.get("proxmoxUsername"):
                    users.add(f"{u['proxmoxUsername']}@pam")
        except core.LudusError:
            pass
        return users
    return CACHE.get("allowed-users", 300, load)


# --------------------------------------------------------------------------
# Sign-in: checked against Proxmox itself, so there's no separate password.
# --------------------------------------------------------------------------
SESSIONS = {}
SESSIONS_LOCK = threading.Lock()
FAILURES = defaultdict(deque)
FAILURES_LOCK = threading.Lock()


def proxmox_check(username, password):
    """True if Proxmox accepts the credentials. Raises ValueError with a message for odd cases."""
    data = urllib.parse.urlencode({"username": username, "password": password}).encode()
    ctx = ssl.create_default_context()
    if urllib.parse.urlparse(PROXMOX_URL).hostname in ("127.0.0.1", "localhost", "::1"):
        # Proxmox's own self-signed certificate on this machine.
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    req = urllib.request.Request(f"{PROXMOX_URL}/api2/json/access/ticket", data=data, method="POST")
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=15) as resp:
            body = json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            return False
        raise ValueError(f"Proxmox answered with an error ({exc.code}).")
    except (urllib.error.URLError, OSError, ValueError):
        raise ValueError("Couldn't reach Proxmox to check the sign-in.")
    data = body.get("data") or {}
    if data.get("NeedTFA"):
        raise ValueError("This account uses two-factor sign-in, which this page doesn't support. "
                         "Use an account without it.")
    return bool(data.get("ticket"))


def too_many_failures(ip):
    with FAILURES_LOCK:
        recent = FAILURES[ip]
        while recent and time.time() - recent[0] > 600:
            recent.popleft()
        return len(recent) >= 5


def note_failure(ip):
    with FAILURES_LOCK:
        FAILURES[ip].append(time.time())


def new_session(user):
    token = secrets.token_urlsafe(32)
    with SESSIONS_LOCK:
        now = time.time()
        for t in [t for t, s in SESSIONS.items() if s["expires"] < now]:
            del SESSIONS[t]
        SESSIONS[token] = {"user": user, "expires": now + SESSION_TTL}
    return token


def session_user(token):
    if not token:
        return None
    with SESSIONS_LOCK:
        s = SESSIONS.get(token)
        if not s or s["expires"] < time.time():
            SESSIONS.pop(token, None)
            return None
        return s["user"]


# --------------------------------------------------------------------------
# Proxmox API, via a scoped read-only+guest-agent token created at setup.
# Powers live stats, node storage, and password resets. Everything degrades
# gracefully when no token is configured.
# --------------------------------------------------------------------------
class ProxmoxError(Exception):
    pass


def pve_enabled():
    return bool(PROXMOX_TOKEN_ID and PROXMOX_TOKEN_SECRET and (PROXMOX_NODE or socket.gethostname()))


def pve_node():
    return PROXMOX_NODE or socket.gethostname()


def _pve_ctx():
    ctx = ssl.create_default_context()
    if urllib.parse.urlparse(PROXMOX_URL).hostname in ("127.0.0.1", "localhost", "::1"):
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    return ctx


def pve_request(method, path, data=None, timeout=20):
    if not pve_enabled():
        raise ProxmoxError("Live monitoring isn't set up. Re-run setup.sh on the node.")
    body = urllib.parse.urlencode(data).encode() if data else None
    req = urllib.request.Request(f"{PROXMOX_URL}/api2/json{path}", data=body, method=method)
    req.add_header("Authorization", f"PVEAPIToken={PROXMOX_TOKEN_ID}={PROXMOX_TOKEN_SECRET}")
    try:
        with urllib.request.urlopen(req, context=_pve_ctx(), timeout=timeout) as resp:
            return json.loads(resp.read().decode() or "{}").get("data")
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = (json.loads(exc.read().decode() or "{}").get("errors") or {})
        except (ValueError, OSError):
            pass
        if exc.code in (401, 403):
            raise ProxmoxError("The monitoring token was refused by Proxmox. Re-run setup.sh.")
        raise ProxmoxError(f"Proxmox returned an error ({exc.code}){': ' + str(detail) if detail else ''}.")
    except (urllib.error.URLError, OSError, ValueError):
        raise ProxmoxError("Couldn't reach Proxmox.")


def pve_node_status():
    return CACHE.get("pve:status", 4, lambda: pve_request("GET", f"/nodes/{pve_node()}/status") or {})


def pve_vm_pool():
    """Usage of the datastore Ludus deploys VMs to, as {total, used, avail}."""
    def load():
        for s in pve_request("GET", f"/nodes/{pve_node()}/storage") or []:
            if s.get("storage") == PROXMOX_VM_POOL:
                return {"name": s.get("storage"), "total": s.get("total"), "used": s.get("used"),
                        "avail": s.get("avail")}
        return None
    return CACHE.get("pve:storage", 15, load)


def pve_vm_status(vmid):
    return pve_request("GET", f"/nodes/{pve_node()}/qemu/{int(vmid)}/status/current") or {}


def pve_lab_vmids(lab_id):
    """{proxmoxID: vm-dict} for a lab, from Ludus — the allow-list for any Proxmox action."""
    try:
        r = core.ludus_json("range", "list", "-r", lab_id)
    except core.LudusError:
        return {}
    return {int(v["proxmoxID"]): v for v in (r.get("VMs") or []) if v.get("proxmoxID") is not None}


def pve_set_os_password(vmid, username, password):
    pve_request("PUT", f"/nodes/{pve_node()}/qemu/{int(vmid)}/agent/set-user-password",
                {"username": username, "password": password, "crypted": 0}, timeout=30)


def pve_guest_exec(vmid, command, input_data=None, wait=25):
    """Run a command in a guest via the agent and return (exitcode, stdout, stderr)."""
    node = pve_node()
    payload = {"command": command}
    if input_data is not None:
        payload["input-data"] = input_data
    started = pve_request("POST", f"/nodes/{node}/qemu/{int(vmid)}/agent/exec", payload, timeout=30)
    pid = (started or {}).get("pid")
    if pid is None:
        raise ProxmoxError("The guest agent didn't start the command.")
    deadline = time.time() + wait
    while time.time() < deadline:
        status = pve_request("GET", f"/nodes/{node}/qemu/{int(vmid)}/agent/exec-status?pid={int(pid)}") or {}
        if status.get("exited"):
            return status.get("exitcode"), status.get("out-data") or "", status.get("err-data") or ""
        time.sleep(1.5)
    raise ProxmoxError("The command is taking too long; it may still be running on the VM.")


# --------------------------------------------------------------------------
# Self-update info for the GUI. The update itself runs as root via `lab-update`;
# the GUI only shows the version and whether a newer one has been published.
# --------------------------------------------------------------------------
def _github_releases_url(repo):
    m = re.search(r"github\.com[:/]+([^/]+)/([^/]+?)(?:\.git)?/?$", repo or "")
    return f"https://api.github.com/repos/{m.group(1)}/{m.group(2)}/releases/latest" if m else None


def _parse_version(v):
    return [int(x) for x in re.findall(r"\d+", str(v))[:3]] or [0]


def _latest_remote_version():
    url = _github_releases_url(LAB_UPDATE_REPO)
    if not url:
        return None
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json",
                                                   "User-Agent": "ludus-lab-web"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            tag = (json.loads(resp.read().decode() or "{}").get("tag_name") or "").lstrip("vV")
        return tag or None
    except (urllib.error.URLError, OSError, ValueError):
        return None


def update_info():
    info = {"version": core.VERSION, "git": LAB_UPDATE_GIT, "command": "sudo lab-update",
            "repo": LAB_UPDATE_REPO or None, "latest": None, "newer": False,
            "web_update": LAB_UPDATE_WEB and LAB_UPDATE_GIT}
    if LAB_UPDATE_GIT and LAB_UPDATE_REPO:
        latest = CACHE.get("update-latest", 900, _latest_remote_version)
        if latest:
            info["latest"] = latest
            info["newer"] = _parse_version(latest) > _parse_version(core.VERSION)
    return info


def trigger_update():
    """Start the scoped root updater unit. The web user can start only this one
    unit (enforced by the sudoers rule setup.sh installs) - nothing else."""
    if not (LAB_UPDATE_WEB and LAB_UPDATE_GIT):
        raise ApiError(400, "Web updates aren't enabled here. Run `sudo lab-update` on the node, "
                            "or re-run setup.sh to turn this on.")
    sysctl = LAB_UPDATE_SYSCTL or shutil.which("systemctl") or "systemctl"
    # --no-block: the unit is Type=oneshot, so a plain `start` would wait for the
    # whole update to finish (minutes). --no-block queues it and returns at once.
    # The sudoers rule setup.sh installs permits exactly this argument vector.
    try:
        subprocess.run(["sudo", "-n", sysctl, "start", "--no-block", "ludus-lab-update.service"],
                       check=True, timeout=30, capture_output=True)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        raise ApiError(502, "Couldn't start the update. Run `sudo lab-update` on the node instead.")
    return {"message": "Update started. This page reloads once it's back on the new version.", "started": True}


# --------------------------------------------------------------------------
# Building plans
# --------------------------------------------------------------------------
class ApiError(Exception):
    def __init__(self, status, message, **extra):
        super().__init__(message)
        self.status = status
        self.extra = extra


def find_blueprint(bp_id):
    for bp in blueprints():
        if bp.get("blueprintID") == bp_id:
            return bp
    raise ApiError(404, "That lab isn't in the catalogue any more. Reload the page.")


def catalog_payload():
    states = template_states()
    labs = []
    for bp in blueprints():
        info = core.lab_info(bp)
        try:
            cfg = blueprint_config(info["blueprint"])
            info.update(fields=core.lab_schema(info["blueprint"], cfg), base_ram=core.total_ram(cfg),
                        base_vms=len(core.vms(cfg)), addon_na=core.addon_applicability(cfg), error=None)
        except (core.LudusError, ValueError) as exc:
            info.update(fields=[], base_ram=None, base_vms=None, addon_na={}, error=str(exc))
        labs.append(info)
    labs.sort(key=lambda e: (not e["known"], e["title"].lower()))
    catalogs = {key: [{"label": label, "template": t, "state": states.get(t)} for label, t in entries]
                for key, entries in core.OS_CATALOGS.items()}
    apps = []
    for a in core.APPS:
        fams = []
        if a.get("win"):
            fams.append("windows")
        if core.lin_package(a, "debian"):
            fams.append("debian")
        if core.lin_package(a, "redhat"):
            fams.append("redhat")
        apps.append({"key": a["key"], "name": a["name"], "cat": a["cat"], "os": fams})
    return {"labs": labs, "addons": core.ADDONS, "dfir_field": core.DFIR_FIELD,
            "catalogs": catalogs, "apps": apps, "app_categories": core.APP_CATEGORIES,
            "timezone": core.host_timezone()}


def compose(body):
    """Validate a build request and return (cfg, details, extras)."""
    bp_id = str(body.get("blueprint") or "")
    bp = find_blueprint(bp_id)
    info = core.lab_info(bp)
    try:
        base = blueprint_config(bp_id)
    except core.LudusError as exc:
        raise ApiError(502, f"Couldn't read the lab's blueprint: {exc}")
    schema = core.lab_schema(bp_id, base)
    try:
        options = core.validate_options(schema, body.get("options") or {})
    except ValueError as exc:
        raise ApiError(400, str(exc))
    na = core.addon_applicability(base)
    addons = [a for a in (body.get("addons") or []) if a in core.ADDON_KEYS and not na.get(a)]
    dfir_template = None
    if "dfir" in addons:
        dfir_template = body.get("dfir_template") or core.DFIR_FIELD["default"]
        if dfir_template not in [t for _, t in core.OS_CATALOGS["windows_any"]]:
            raise ApiError(400, "DFIR workstation version: pick one of the listed versions.")
    profile = "lean" if body.get("ram_profile") == "lean" else "standard"
    cfg, details = core.build_lab_config(bp_id, base, options, addons, dfir_template, profile)
    return cfg, details, {"info": info, "addons": addons, "profile": profile}


def lane_label(bp_id, vlan, members, tools_vlan):
    if vlan == tools_vlan:
        return "Security tools"
    for suffix, names in LANE_NAMES.items():
        if bp_id.endswith(suffix) and vlan in names:
            return names[vlan]
    if members and all(m["kind"] == "kali" for m in members):
        return "Attacker"
    return f"VLAN {vlan}"


def plan_payload(body):
    cfg, details, extra = compose(body)
    bp_id = extra["info"]["blueprint"]
    states = template_states()
    machines = [core.describe_vm(vm, "") for vm in core.vms(cfg)]
    for m in machines:
        m["name"] = m["name"].lstrip("-")
    lanes = []
    for vlan in sorted({m["vlan"] for m in machines}, key=lambda v: (v == details["tools_vlan"], v)):
        members = [m for m in machines if m["vlan"] == vlan]
        lanes.append({"vlan": vlan, "label": lane_label(bp_id, vlan, members, details["tools_vlan"]),
                      "tools": vlan == details["tools_vlan"], "vms": members})
    total, avail = core.host_memory_gb()
    need = core.total_ram(cfg)
    missing = [{"template": t, "label": core.os_label(t), "state": s}
               for t, s in core.missing_templates(cfg, states)]
    needed = [{"template": t, "label": core.os_label(t), "state": states.get(t)} for t in core.templates_needed(cfg)]
    roles_missing = installed_roles_missing(cfg)
    existing = {r.get("rangeID") for r in ranges()}
    warnings = list(details["notes"])
    if avail and need > avail - 2:
        warnings.append(f"This needs about {core.fmt_gb(need)} GB but the node has {avail:.0f} GB free right now. "
                        "Power off another lab, or pick the Lean profile.")
    firewall = (cfg.get("network") or {}).get("inter_vlan_default", "ACCEPT")
    return {
        "lanes": lanes,
        "router_ram": core.router_ram(cfg),
        "firewall": "segmented" if firewall != "ACCEPT" else "open",
        "domains": sorted({(vm.get("domain") or {}).get("fqdn") for vm in core.vms(cfg) if vm.get("domain")}),
        "ram": need,
        "ram_standard": details["ram_standard"],
        "ram_lean": details["ram_lean"],
        "ram_profile": details["ram_profile"],
        "memory": {"total": total, "available": avail},
        "templates": needed,
        "missing_templates": missing,
        "missing_roles": roles_missing,
        "addons": details["addons"],
        "warnings": warnings,
        "suggested_id": core.unique_range_id(extra["info"]["range_id"], existing),
        "existing_ids": sorted(i for i in existing if i),
        "default_name": extra["info"]["title"],
        "timezone": (cfg.get("defaults") or {}).get("timezone"),
        "estimate": build_estimate(cfg, states),
    }


def build_estimate(cfg, states):
    est = core.build_time_estimate(cfg, states)
    return {
        "total": core.fmt_duration(est["total_min"]),
        "low": core.fmt_duration(est["low_min"]),
        "high": core.fmt_duration(est["high_min"]),
        "deploy": core.fmt_duration(est["deploy_min"]),
        "templates": core.fmt_duration(est["templates_min"]) if est["templates_min"] else None,
    }


def build_lab(body):
    cfg, details, extra = compose(body)
    range_id = str(body.get("range_id") or "").strip()
    name = str(body.get("name") or "").strip()[:80] or extra["info"]["title"]
    existing = {r.get("rangeID") for r in core.list_ranges()}
    problem = core.valid_range_id(range_id, existing)
    if problem:
        raise ApiError(400, f"Lab ID: {problem}")
    missing = core.missing_templates(cfg, core.template_states())
    if missing:
        raise ApiError(409, "Some templates aren't built yet. Build them first, then come back.",
                       missing_templates=[t for t, _ in missing])
    roles_missing = core.missing_roles(cfg)
    if roles_missing:
        raise ApiError(409, "Some add-on roles aren't installed on the server. Re-run setup.sh, then try again: "
                       + ", ".join(roles_missing))
    try:
        core.launch(cfg, range_id, name, extra["info"]["blueprint"], details["ram_profile"])
    except core.LaunchError as exc:
        raise ApiError(502, str(exc), stage=exc.stage)
    finally:
        CACHE.drop("ranges")
    return {"range_id": range_id, "message": f"Build started for {range_id}."}


# --------------------------------------------------------------------------
# Labs, templates, downloads
# --------------------------------------------------------------------------
def labs_payload():
    items = []
    for r in ranges():
        state = r.get("rangeState") or "NEVER DEPLOYED"
        items.append({"id": r.get("rangeID"), "name": r.get("name") or "", "number": r.get("rangeNumber"),
                      "state": state, "state_label": STATE_WORDS.get(state, state.title()),
                      "vms": r.get("numberOfVMs", len(r.get("VMs") or [])),
                      "testing": bool(r.get("testingEnabled")), "last": r.get("lastDeployment")})
    total, avail = core.host_memory_gb()
    return {"labs": items, "memory": {"total": total, "available": avail}}


def check_lab_id(lab_id):
    if not LAB_ID_RE.match(lab_id or ""):
        raise ApiError(400, "That isn't a valid lab ID.")
    return lab_id


def lab_detail(lab_id):
    check_lab_id(lab_id)
    try:
        r = core.ludus_json("range", "list", "-r", lab_id)
    except core.LudusError as exc:
        raise ApiError(404, f"Couldn't load {lab_id}: {exc}")
    state = r.get("rangeState") or "NEVER DEPLOYED"
    number = r.get("rangeNumber")
    try:
        cfg = core.load_lab_config(lab_id)
    except (core.LudusError, ValueError, OSError):
        cfg = None
    by_name = {}
    if cfg:
        for v in core.vms(cfg):
            by_name[str(v.get("vm_name", "")).replace("{{ range_id }}", lab_id)] = v
    machines = []
    for v in r.get("VMs") or []:
        cv = by_name.get(v.get("name"))
        template = cv.get("template") if cv else None
        router = bool(v.get("isRouter"))
        machines.append({
            "name": v.get("name"), "ip": v.get("ip") or "", "on": bool(v.get("poweredOn")),
            "router": router, "id": v.get("proxmoxID"),
            "os": "Router" if router else (core.os_label(template) if template else ""),
            "family": "" if router else (core.os_family(template) if template else ""),
            "default_account": "" if (router or not template) else core.default_os_account(template),
            "is_dc": bool(cv and core.is_dc(cv)),
            "domain": (cv.get("domain") or {}).get("fqdn") if cv else None,
            "can_reset": pve_enabled() and not router and v.get("proxmoxID") is not None,
        })
    tools = core.tool_logins(cfg, number) if cfg else []
    return {"id": r.get("rangeID", lab_id), "name": r.get("name") or "", "number": number,
            "state": state, "state_label": STATE_WORDS.get(state, state.title()),
            "testing": bool(r.get("testingEnabled")), "last": r.get("lastDeployment"),
            "monitoring": pve_enabled(), "vms": machines, "tools": tools}


def lab_stats(lab_id):
    check_lab_id(lab_id)
    if not pve_enabled():
        return {"enabled": False, "vms": []}
    out = []
    for vmid, v in sorted(pve_lab_vmids(lab_id).items()):
        try:
            st = pve_vm_status(vmid)
            out.append({"id": vmid, "name": v.get("name"), "status": st.get("status"),
                        "cpu": st.get("cpu"), "cpus": st.get("cpus"), "mem": st.get("mem"),
                        "maxmem": st.get("maxmem"), "maxdisk": st.get("maxdisk"), "uptime": st.get("uptime"),
                        "netin": st.get("netin"), "netout": st.get("netout"), "agent": bool(st.get("agent"))})
        except ProxmoxError:
            out.append({"id": vmid, "name": v.get("name"), "status": "unknown"})
    return {"enabled": True, "vms": out}


def reset_os_password(lab_id, vmid, username, password):
    vmids = pve_lab_vmids(lab_id)
    if vmid not in vmids:
        raise ApiError(404, "That VM isn't part of this lab.")
    if not ACCOUNT_RE.match(username or ""):
        raise ApiError(400, "That account name has characters I won't pass through.")
    if len(password) < 8:
        raise ApiError(400, "Use a password of at least 8 characters.")
    try:
        pve_set_os_password(vmid, username, password)
    except ProxmoxError as exc:
        raise ApiError(502, f"Couldn't set the password: {exc} The VM must be running with its guest agent up.")
    return {"message": f"Password for {username} on {vmids[vmid].get('name')} was reset."}


def reset_domain_password(lab_id, vmid, username, password):
    vmids = pve_lab_vmids(lab_id)
    if vmid not in vmids:
        raise ApiError(404, "That VM isn't part of this lab.")
    if not ACCOUNT_RE.match(username or ""):
        raise ApiError(400, "That account name has characters I won't pass through.")
    if len(password) < 8 or password.lower() == password or password.upper() == password or not any(c.isdigit() for c in password):
        raise ApiError(400, "Active Directory needs a complex password: 8+ characters with upper, lower and a digit.")
    # Single quotes are the only metacharacter inside a PowerShell single-quoted string.
    u = username.replace("'", "''")
    p = password.replace("'", "''")
    script = ("$ErrorActionPreference='Stop';"
              f"$s=ConvertTo-SecureString '{p}' -AsPlainText -Force;"
              f"Set-ADAccountPassword -Identity '{u}' -Reset -NewPassword $s;"
              f"Set-ADUser -Identity '{u}' -ChangePasswordAtLogon $false;"
              "Write-Output 'OK'")
    try:
        code, out, err = pve_guest_exec(vmid, ["powershell.exe", "-NonInteractive", "-Command", script])
    except ProxmoxError as exc:
        raise ApiError(502, f"Couldn't run the reset on the domain controller: {exc}")
    if code != 0:
        msg = (err or out or "").strip() or "the domain controller rejected it"
        if "cannot find an object" in msg.lower() or "cannot find object" in msg.lower():
            msg = f"the domain has no account called '{username}'"
        raise ApiError(400, f"Reset failed: {msg[:300]}")
    return {"message": f"Domain password for {username} was reset."}


def log_text(args):
    raw = core.ludus_json(*args)
    return (raw.get("result") if isinstance(raw, dict) else "") or ""


def template_log():
    lines, last = [], None
    for line in log_text(("templates", "logs", "-t", "300")).splitlines():
        if "QEMU guest agent is not running" in line:
            line = line[:20] + " Waiting for the VM to boot and finish its first setup..."
        if line != last:
            lines.append(line)
        last = line
    return "\n".join(lines[-200:])


def snapshots_payload(lab_id):
    check_lab_id(lab_id)
    raw = core.ludus_json("snapshots", "list", "-r", lab_id)
    grouped = {}
    for s in (raw or {}).get("snapshots") or []:
        if s.get("name") == "current":
            continue
        g = grouped.setdefault(s["name"], {"name": s["name"], "description": s.get("description") or "",
                                           "time": s.get("snaptime") or 0, "vms": 0, "ram": False})
        g["vms"] += 1
        g["time"] = max(g["time"], s.get("snaptime") or 0)
        g["ram"] = g["ram"] or bool(s.get("includesRAM"))
    return {"snapshots": sorted(grouped.values(), key=lambda g: -g["time"])}


def lab_action(lab_id, body):
    check_lab_id(lab_id)
    action = body.get("action")
    simple = {
        "power_on": (["power", "on", "-n", "all", "-r", lab_id], 300),
        "power_off": (["power", "off", "-n", "all", "-r", lab_id], 300),
        "testing_start": (["testing", "start", "-r", lab_id], 1800),
        "testing_stop": (["testing", "stop", "-r", lab_id], 1800),
        "redeploy": (["range", "deploy", "-r", lab_id], 120),
        "abort": (["range", "abort", "-r", lab_id], 120),
    }
    if action in simple:
        args, timeout = simple[action]
    elif action == "snapshot_create":
        name = str(body.get("name") or "")
        problem = core.valid_snapshot_name(name)
        if problem:
            raise ApiError(400, f"Snapshot name: {problem}")
        args, timeout = ["snapshots", "create", name, "-d", str(body.get("description") or "")[:200], "-r", lab_id], 1800
    elif action == "snapshot_revert":
        name = str(body.get("name") or "")
        if core.valid_snapshot_name(name):
            raise ApiError(400, "Pick a snapshot from the list.")
        args, timeout = ["snapshots", "revert", name, "-r", lab_id], 1800
    elif action == "delete":
        if body.get("confirm") != lab_id:
            raise ApiError(400, f"Type {lab_id} exactly to confirm.")
        args, timeout = ["range", "rm-range", "-r", lab_id, "--no-prompt", "--force"], 300
    elif action == "reset_os_password":
        return reset_os_password(lab_id, int(body.get("vmid") or 0),
                                 str(body.get("username") or ""), str(body.get("password") or ""))
    elif action == "reset_domain_password":
        return reset_domain_password(lab_id, int(body.get("vmid") or 0),
                                     str(body.get("username") or ""), str(body.get("password") or ""))
    else:
        raise ApiError(400, "Unknown action.")
    try:
        message = core.result_message(core.run(args, timeout))
    except core.LudusError as exc:
        raise ApiError(502, str(exc))
    finally:
        CACHE.drop("ranges")
    return {"message": message}


# Typed confirmation phrases for the destructive node reset.
RESET_PHRASES = {"labs": "delete all labs", "full": "reset everything"}


def reset_node(level, confirm):
    """Tear down everything the lab builder made. 'labs' deletes all ranges;
    'full' also deletes every built template (built-in definitions stay, so you
    can rebuild). Only touches Ludus's own objects - never Proxmox itself."""
    if level not in RESET_PHRASES:
        raise ApiError(400, "Unknown reset level.")
    if (confirm or "").strip().lower() != RESET_PHRASES[level]:
        raise ApiError(400, f'Type "{RESET_PHRASES[level]}" exactly to confirm.')
    labs = templates = 0
    errors = []
    for r in core.list_ranges():
        rid = r.get("rangeID")
        if not rid:
            continue
        try:
            core.run(["range", "rm-range", "-r", rid, "--no-prompt", "--force"], 600)
            labs += 1
        except core.LudusError as exc:
            errors.append(f"lab {rid}: {exc}")
    if level == "full":
        for name, state in template_states().items():
            if state != "ready":   # only remove built templates; leave builds in progress alone
                continue
            try:
                core.run(["templates", "rm", "-n", name], 300)
                templates += 1
            except core.LudusError as exc:
                errors.append(f"template {name}: {exc}")
    CACHE.drop("ranges")
    CACHE.drop("templates")
    parts = [f"{labs} lab{'s' if labs != 1 else ''}"]
    if level == "full":
        parts.append(f"{templates} template{'s' if templates != 1 else ''}")
    msg = "Removed " + " and ".join(parts) + "."
    if errors:
        msg += f" {len(errors)} couldn't be removed - see the Ludus logs."
    return {"message": msg, "labs": labs, "templates": templates, "errors": errors[:5]}


def refresh_sources():
    """Pull the latest template and blueprint definitions from the sources, so
    new builds use current versions. Runs through the Ludus API, no root needed."""
    try:
        core.run(["source", "sync"], 300)
    except core.LudusError as exc:
        raise ApiError(502, f"Couldn't refresh the catalogue: {exc}")
    CACHE.drop("blueprints", "templates")
    return {"message": "Catalogue refreshed. Any newer template or blueprint definitions are now available; "
                       "rebuild a template to pick up its latest image."}


def templates_payload():
    states = template_states()
    known = {t for entries in core.OS_CATALOGS.values() for _, t in entries}
    installed = [{"template": name, "label": core.os_label(name), "state": state}
                 for name, state in sorted(states.items(), key=lambda kv: core.os_label(kv[0]))]
    extra = [{"template": t, "label": core.os_label(t)} for t in sorted(known - set(states), key=core.os_label)]
    return {"templates": installed, "available": extra}


def start_templates(body):
    states = core.template_states()
    if body.get("all"):
        names = [n for n, s in states.items() if s == "not built"]
        if not names:
            return {"message": "Every installed template is already built or building."}
    else:
        names = [str(n) for n in body.get("templates") or []]
        known = {t for entries in core.OS_CATALOGS.values() for _, t in entries} | set(states)
        if not names or not all(TEMPLATE_NAME_RE.match(n) and n in known for n in names):
            raise ApiError(400, "Pick templates from the list.")
    try:
        message = core.start_template_builds(names, states)
    except core.LudusError as exc:
        raise ApiError(502, str(exc))
    finally:
        CACHE.drop("templates")
    return {"message": message}


def rdp_zip(lab_id):
    check_lab_id(lab_id)
    os.makedirs(TMP_DIR, exist_ok=True)
    path = os.path.join(TMP_DIR, f"rdp-{secrets.token_hex(8)}.zip")
    try:
        core.ludus("range", "rdp", "-r", lab_id, "-o", path, timeout=120)
        with open(path, "rb") as fh:
            return fh.read()
    except core.LudusError as exc:
        raise ApiError(502, f"Couldn't get the RDP files: {exc}")
    except OSError:
        raise ApiError(502, "Ludus didn't produce an RDP file. Is the lab built?")
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


# --------------------------------------------------------------------------
# HTTP plumbing
# --------------------------------------------------------------------------
SECURITY_HEADERS = {
    "Content-Security-Policy": "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
                               "font-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; "
                               "form-action 'self'",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
}


class Handler(http.server.BaseHTTPRequestHandler):
    server_version = "ludus-lab-web"
    sys_version = ""
    timeout = 60

    def log_message(self, fmt, *args):
        sys.stdout.write("%s %s\n" % (self.client_address[0], fmt % args))
        sys.stdout.flush()

    # ---- responses
    def _send(self, status, body=b"", ctype="application/json", headers=None):
        self.send_response(status)
        for k, v in SECURITY_HEADERS.items():
            self.send_header(k, v)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, status, payload, headers=None):
        self._send(status, json.dumps(payload, default=str).encode(), "application/json", headers)

    def _error(self, status, message, **extra):
        self._json(status, {"error": message, **extra})

    # ---- request helpers
    def _cookie(self, name):
        for part in (self.headers.get("Cookie") or "").split(";"):
            k, _, v = part.strip().partition("=")
            if k == name:
                return v
        return None

    def _user(self):
        return session_user(self._cookie(COOKIE))

    def _body(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            raise ApiError(413, "Request too large.")
        raw = self.rfile.read(length) if length else b"{}"
        try:
            data = json.loads(raw.decode() or "{}")
        except ValueError:
            raise ApiError(400, "Request body isn't valid JSON.")
        if not isinstance(data, dict):
            raise ApiError(400, "Request body must be a JSON object.")
        return data

    def _same_origin(self):
        if self.headers.get("X-Lab-Request") != "1":
            return False
        origin = self.headers.get("Origin")
        if origin:
            return urllib.parse.urlparse(origin).netloc == self.headers.get("Host")
        return True

    def _cookie_header(self, token, max_age):
        secure = "" if PLAIN_HTTP else " Secure;"
        return f"{COOKIE}={token}; Path=/; HttpOnly;{secure} SameSite=Strict; Max-Age={max_age}"

    # ---- routing
    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        if not path.startswith("/api/"):
            return self._static(path)
        self._api("GET", path)

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        if not path.startswith("/api/"):
            return self._error(404, "Not found.")
        if not self._same_origin():
            return self._error(403, "Request blocked: it didn't come from this page.")
        self._api("POST", path)

    def _static(self, path):
        if path in ("/", "/index.html"):
            path = "/index.html"
        elif not path.startswith("/static/"):
            path = "/index.html"  # single-page app
        rel = path[len("/static/"):] if path.startswith("/static/") else "index.html"
        full = os.path.realpath(os.path.join(STATIC_DIR, rel))
        if not full.startswith(os.path.realpath(STATIC_DIR) + os.sep) or not os.path.isfile(full):
            return self._error(404, "Not found.")
        ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
        if full.endswith(".woff2"):
            ctype = "font/woff2"
        if ctype.startswith("text/") or ctype in ("application/javascript",):
            ctype += "; charset=utf-8"
        with open(full, "rb") as fh:
            self._send(200, fh.read(), ctype)

    ROUTES = [
        ("GET", r"/api/session", "session", False),
        ("POST", r"/api/login", "login", False),
        ("POST", r"/api/logout", "logout", False),
        ("GET", r"/api/overview", "overview", True),
        ("GET", r"/api/catalog", "catalog", True),
        ("POST", r"/api/plan", "plan", True),
        ("POST", r"/api/build", "build", True),
        ("GET", r"/api/labs", "labs", True),
        ("GET", r"/api/labs/(?P<id>[^/]+)", "lab", True),
        ("GET", r"/api/labs/(?P<id>[^/]+)/stats", "lab_stats", True),
        ("GET", r"/api/labs/(?P<id>[^/]+)/log", "lab_log", True),
        ("GET", r"/api/labs/(?P<id>[^/]+)/errors", "lab_errors", True),
        ("GET", r"/api/labs/(?P<id>[^/]+)/snapshots", "lab_snapshots", True),
        ("GET", r"/api/labs/(?P<id>[^/]+)/rdp", "lab_rdp", True),
        ("POST", r"/api/labs/(?P<id>[^/]+)/action", "lab_action", True),
        ("GET", r"/api/templates", "templates", True),
        ("GET", r"/api/templates/log", "templates_log", True),
        ("POST", r"/api/templates/build", "templates_build", True),
        ("GET", r"/api/wireguard", "wireguard", True),
        ("GET", r"/api/updates", "updates", True),
        ("POST", r"/api/update", "update", True),
        ("POST", r"/api/refresh", "refresh", True),
        ("POST", r"/api/reset", "reset", True),
    ]

    def _api(self, method, path):
        for verb, pattern, name, needs_auth in self.ROUTES:
            m = re.fullmatch(pattern, path)
            if verb == method and m:
                break
        else:
            return self._error(404, "Not found.")
        user = self._user()
        if needs_auth and not user:
            return self._error(401, "Sign in first.")
        try:
            getattr(self, "api_" + name)(user=user, **m.groupdict())
        except ApiError as exc:
            self._error(exc.status, str(exc), **exc.extra)
        except core.LudusError as exc:
            self._error(502, str(exc))
        except Exception as exc:  # keep the server up; report the problem
            self.log_message("error in %s: %r", path, exc)
            self._error(500, "Something went wrong on the server. Check: journalctl -u ludus-lab-web")

    # ---- endpoints
    def api_session(self, user):
        if not user:
            return self._error(401, "Sign in first.")
        self._json(200, {"user": user, "version": core.VERSION, "node": socket.gethostname()})

    def api_login(self, user):
        ip = self.client_address[0]
        if too_many_failures(ip):
            return self._error(429, "Too many failed sign-ins. Wait a few minutes and try again.")
        body = self._body()
        username = str(body.get("username") or "").strip()
        password = str(body.get("password") or "")
        if not username or not password:
            return self._error(400, "Enter your username and password.")
        full = username if "@" in username else f"{username}@pam"
        if full not in allowed_users():
            note_failure(ip)
            return self._error(401, "Sign in with root or your Ludus admin account.")
        try:
            accepted = proxmox_check(full, password)
        except ValueError as exc:
            return self._error(502, str(exc))
        if not accepted:
            note_failure(ip)
            return self._error(401, "That username and password weren't accepted.")
        with FAILURES_LOCK:
            FAILURES.pop(ip, None)
        token = new_session(full)
        self._json(200, {"user": full}, {"Set-Cookie": self._cookie_header(token, SESSION_TTL)})

    def api_logout(self, user):
        token = self._cookie(COOKIE)
        with SESSIONS_LOCK:
            SESSIONS.pop(token, None)
        self._json(200, {"ok": True}, {"Set-Cookie": self._cookie_header("", 0)})

    def api_overview(self, user):
        total, avail = core.host_memory_gb()
        states = template_states()
        counts = {"ready": 0, "building": 0, "not built": 0}
        for s in states.values():
            counts[s] = counts.get(s, 0) + 1
        storage = None
        if pve_enabled():
            try:
                pool = pve_vm_pool()
                if pool and pool.get("total"):
                    gb = 1024 ** 3
                    storage = {"name": pool.get("name"),
                               "total": round(pool["total"] / gb, 1),
                               "available": round((pool.get("avail") or 0) / gb, 1)}
            except ProxmoxError:
                storage = None
        self._json(200, {"node": pve_node(), "version": core.VERSION,
                         "memory": {"total": total, "available": avail}, "templates": counts,
                         "storage": storage, "monitoring": pve_enabled(), "labs": len(ranges())})

    def api_lab_stats(self, user, id):
        self._json(200, lab_stats(id))

    def api_catalog(self, user):
        self._json(200, catalog_payload())

    def api_updates(self, user):
        self._json(200, update_info())

    def api_update(self, user):
        self._json(200, trigger_update())

    def api_refresh(self, user):
        self._json(200, refresh_sources())

    def api_reset(self, user):
        body = self._body()
        self._json(200, reset_node(body.get("level"), body.get("confirm")))

    def api_plan(self, user):
        self._json(200, plan_payload(self._body()))

    def api_build(self, user):
        self._json(200, build_lab(self._body()))

    def api_labs(self, user):
        self._json(200, labs_payload())

    def api_lab(self, user, id):
        self._json(200, lab_detail(id))

    def api_lab_log(self, user, id):
        check_lab_id(id)
        self._json(200, {"text": log_text(("range", "logs", "-r", id, "-t", "400"))})

    def api_lab_errors(self, user, id):
        check_lab_id(id)
        try:
            text = core.clean_output(core.ludus("range", "errors", "-r", id, timeout=60))
        except core.LudusError as exc:
            text = str(exc)
        self._json(200, {"text": text or "No errors recorded for the last build."})

    def api_lab_snapshots(self, user, id):
        self._json(200, snapshots_payload(id))

    def api_lab_rdp(self, user, id):
        data = rdp_zip(id)
        self._send(200, data, "application/zip",
                   {"Content-Disposition": f'attachment; filename="{id}-rdp.zip"'})

    def api_lab_action(self, user, id):
        self._json(200, lab_action(id, self._body()))

    def api_templates(self, user):
        self._json(200, templates_payload())

    def api_templates_log(self, user):
        self._json(200, {"text": template_log()})

    def api_templates_build(self, user):
        self._json(200, start_templates(self._body()))

    def api_wireguard(self, user):
        try:
            conf = core.ludus("user", "wireguard", timeout=60)
        except core.LudusError as exc:
            raise ApiError(502, f"Couldn't get the WireGuard config: {exc}")
        self._send(200, conf.encode(), "text/plain; charset=utf-8",
                   {"Content-Disposition": 'attachment; filename="ludus-wireguard.conf"'})


class Server(http.server.ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    ssl_ctx = None

    def finish_request(self, request, client_address):
        # Runs in the per-connection thread, so a slow TLS handshake can't stall other visitors.
        if self.ssl_ctx:
            request.settimeout(30)
            try:
                request = self.ssl_ctx.wrap_socket(request, server_side=True)
            except (ssl.SSLError, OSError):
                return
        super().finish_request(request, client_address)

    def handle_error(self, request, client_address):
        exc = sys.exc_info()[1]
        if isinstance(exc, (ConnectionError, ssl.SSLError, TimeoutError)):
            return
        super().handle_error(request, client_address)


def main():
    try:
        core.check_connection()
    except core.LudusError as exc:
        print(f"Warning: {exc}", file=sys.stderr)
    httpd = Server((BIND, PORT), Handler)
    scheme = "http"
    if not PLAIN_HTTP:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        ctx.load_cert_chain(CERT, KEY)
        httpd.ssl_ctx = ctx
        scheme = "https"
    print(f"Lab launcher web GUI {core.VERSION} listening on {scheme}://{BIND}:{PORT}", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
