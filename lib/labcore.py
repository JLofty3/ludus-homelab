"""
labcore - shared logic for the lab launcher (terminal menu and web GUI).

Everything that decides what a lab looks like lives here: the catalogue of
labs and their options, OS versions, security tooling add-ons, RAM profiles,
and the calls to the `ludus` CLI that build it. The two front ends only collect
answers and show results.
"""

import copy
import json
import math
import os
import re
import subprocess
import tempfile

import yaml

VERSION = "1.8.1"
LUDUS = os.environ.get("LUDUS_BIN", "ludus")
KEY_FILES = [p for p in (os.environ.get("LAB_KEY_FILE"), "/root/.config/ludus-lab/env", "/etc/ludus-lab/env") if p]
DEFAULT_ROUTER_TEMPLATE = "debian-13-x64-server-template"
DEFAULT_ROUTER_RAM_GB = 2
RANGE_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9-]{0,19}$")
SNAPSHOT_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,39}$")


def default_state_dir():
    if os.environ.get("LAB_STATE_DIR"):
        return os.path.expanduser(os.environ["LAB_STATE_DIR"])
    if os.path.isdir("/var/lib/ludus-lab/configs"):
        return "/var/lib/ludus-lab/configs"
    return os.path.expanduser("~/.local/share/ludus-lab")


STATE_DIR = default_state_dir()


# --------------------------------------------------------------------------
# YAML that behaves like Ludus's Go parser (YAML 1.2 core schema).
# PyYAML defaults to YAML 1.1, where "no", "on" or "1:30" silently turn into
# booleans and numbers. Those rules are swapped out so a config read from
# Ludus is written back unchanged apart from the edits made here.
# --------------------------------------------------------------------------
class _Loader(yaml.SafeLoader):
    pass


_Loader.yaml_implicit_resolvers = {
    k: [(tag, rx) for tag, rx in v if tag not in (
        "tag:yaml.org,2002:bool", "tag:yaml.org,2002:int", "tag:yaml.org,2002:float",
        "tag:yaml.org,2002:timestamp")]
    for k, v in yaml.SafeLoader.yaml_implicit_resolvers.items()
}
_Loader.add_implicit_resolver(
    "tag:yaml.org,2002:bool", re.compile(r"^(?:true|True|TRUE|false|False|FALSE)$"), list("tTfF"))
_Loader.add_implicit_resolver(
    "tag:yaml.org,2002:int", re.compile(r"^(?:[-+]?[0-9]+|0o[0-7]+|0x[0-9a-fA-F]+)$"),
    list("-+0123456789"))
_Loader.add_implicit_resolver(
    "tag:yaml.org,2002:float",
    re.compile(r"^(?:[-+]?(?:\.[0-9]+|[0-9]+(?:\.[0-9]*)?)(?:[eE][-+]?[0-9]+)?"
               r"|[-+]?\.(?:inf|Inf|INF)|\.(?:nan|NaN|NAN))$"),
    list("-+0123456789."))


def _construct_int(loader, node):
    value = loader.construct_scalar(node)
    if value.startswith("0o"):
        return int(value[2:], 8)
    return int(value, 0) if value.startswith("0x") else int(value)


_Loader.add_constructor("tag:yaml.org,2002:int", _construct_int)


def load_yaml(text):
    return yaml.load(text, Loader=_Loader) or {}


def dump_yaml(data):
    return yaml.safe_dump(data, sort_keys=False, default_flow_style=False,
                          allow_unicode=True, width=1000)


# --------------------------------------------------------------------------
# Talking to Ludus
# --------------------------------------------------------------------------
class LudusError(Exception):
    pass


_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


def _ludus_env():
    env = dict(os.environ)
    if env.get("LUDUS_API_KEY"):
        return env
    for path in KEY_FILES:
        try:
            with open(path) as fh:
                for line in fh:
                    line = line.strip()
                    if line.startswith("LUDUS_API_KEY="):
                        env["LUDUS_API_KEY"] = line.split("=", 1)[1].strip().strip("'\"")
                        return env
        except OSError:
            continue
    return env


ENV = _ludus_env()


def clean_output(text):
    lines = []
    for raw in _ANSI.sub("", text or "").replace("\r", "\n").splitlines():
        raw = raw.strip()
        if raw and "Waiting for server" not in raw:
            lines.append(raw)
    return "\n".join(lines)


def _failed(proc):
    # The Ludus CLI often exits 0 even when the API refused a request, and
    # reports the problem on stderr as [ERROR]/[FATAL] instead.
    return proc.returncode != 0 or re.search(r"\[(ERROR|FATAL)\]", _ANSI.sub("", proc.stderr or ""))


def _error_text(proc, args):
    message = clean_output(proc.stderr) or clean_output(proc.stdout) or f"ludus {' '.join(args)} failed"
    return re.sub(r"\[(ERROR|FATAL)\]\s*", "", message)


def run(args, timeout=900):
    try:
        proc = subprocess.run([LUDUS, *args], capture_output=True, text=True, env=ENV, timeout=timeout)
    except FileNotFoundError:
        raise LudusError("The `ludus` command wasn't found on this server.")
    except subprocess.TimeoutExpired:
        raise LudusError(f"`ludus {' '.join(args)}` took longer than {timeout // 60} minutes and was stopped.")
    if _failed(proc):
        raise LudusError(_error_text(proc, args))
    return proc


def ludus(*args, timeout=900):
    """Run a ludus command, return its stdout, raise LudusError on failure."""
    return run(args, timeout).stdout


def result_message(proc):
    """The human-readable outcome of a ludus command ([INFO] lines or stdout)."""
    messages = re.sub(r"\[(INFO|WARN)\]\s*", "", clean_output(proc.stderr))
    return clean_output(proc.stdout) or messages or "Done."


def ludus_json(*args, timeout=120):
    try:
        proc = subprocess.run([LUDUS, *args, "--json"], capture_output=True, text=True, env=ENV, timeout=timeout)
    except FileNotFoundError:
        raise LudusError("The `ludus` command wasn't found on this server.")
    except subprocess.TimeoutExpired:
        raise LudusError(f"`ludus {' '.join(args)}` didn't answer within {timeout} seconds.")
    out = _ANSI.sub("", proc.stdout or "")
    starts = [i for i in (out.find("{"), out.find("[")) if i != -1]
    if _failed(proc) or not starts:
        raise LudusError(_error_text(proc, args))
    value, _ = json.JSONDecoder().raw_decode(out[min(starts):])
    return value


def check_connection():
    """Raise LudusError with a helpful message if Ludus can't be used."""
    if not ENV.get("LUDUS_API_KEY"):
        raise LudusError("No Ludus API key found. Run setup.sh first, or export LUDUS_API_KEY.")
    try:
        ludus_json("templates", "list")
    except LudusError as exc:
        hint = ("The API key was refused - re-run setup.sh to create a fresh one."
                if "API key" in str(exc) else "Is it running?  systemctl status ludus")
        raise LudusError(f"Could not talk to the Ludus server: {exc}\n{hint}")


# --------------------------------------------------------------------------
# Operating system versions
# Built-in templates are ready after setup.sh. The rest come from the Bad
# Sector Labs source and are installed + built the first time they're picked.
# --------------------------------------------------------------------------
WINDOWS_SERVERS = [
    ("Windows Server 2022", "win2022-server-x64-template"),
    ("Windows Server 2025", "win2025-server-x64-tpm-template"),
    ("Windows Server 2019", "win2019-server-x64-template"),
    ("Windows Server 2016", "win2016-server-x64-template"),
    ("Windows Server 2012 R2", "win2012r2-server-x64-template"),
]
WINDOWS_CLIENTS = [
    ("Windows 11 22H2", "win11-22h2-x64-enterprise-template"),
    ("Windows 11 23H2", "win11-23h2-x64-enterprise-template"),
    ("Windows 11 24H2", "win11-24h2-x64-enterprise-tpm-template"),
    ("Windows 11 25H2", "win11-25h2-x64-enterprise-template"),
    ("Windows 10 22H2", "win10-22h2-x64-enterprise-template"),
]
LINUX_SERVERS = [
    ("Debian 13", "debian-13-x64-server-template"),
    ("Debian 12", "debian-12-x64-server-template"),
    ("Debian 11", "debian-11-x64-server-template"),
    ("Ubuntu 24.04 LTS", "ubuntu-24.04-x64-server-template"),
    ("Ubuntu 22.04 LTS", "ubuntu-22.04-x64-server-template"),
    ("Ubuntu 20.04 LTS", "ubuntu-20.04-x64-server-template"),
    ("Rocky Linux 9", "rocky-9-x64-server-template"),
    ("Rocky Linux 8", "rocky-8-x64-server-template"),
]
LINUX_DESKTOPS = [
    ("Ubuntu 24.04 desktop", "ubuntu-24.04-x64-desktop-template"),
    ("Kali Linux", "kali-x64-desktop-template"),
    ("REMnux (malware analysis)", "remnux-template"),
]
# A single box to tinker with: desktops first (most people want a GUI), then servers.
SANDBOX_OSES = WINDOWS_CLIENTS + LINUX_DESKTOPS + WINDOWS_SERVERS + LINUX_SERVERS
OS_CATALOGS = {
    "windows_server": WINDOWS_SERVERS,
    "windows_client": WINDOWS_CLIENTS,
    "windows_any": WINDOWS_CLIENTS + WINDOWS_SERVERS,
    "linux_server": LINUX_SERVERS,
    "linux_any": LINUX_SERVERS + LINUX_DESKTOPS,
    "sandbox": SANDBOX_OSES,
}
EXTRA_LABELS = {
    "debian-10-x64-server-template": "Debian 10",
    "win11-25h2-x64-enterprise-no-defender-template": "Windows 11 25H2 (no Defender)",
    "win2019-server-x64-no-security-updates-template": "Windows Server 2019 (unpatched)",
}
# Package names that differ on Rocky Linux.
RHEL_PACKAGE_NAMES = {"bind9-dnsutils": "bind-utils", "mtr-tiny": "mtr"}


def os_label(template):
    for catalog in (WINDOWS_SERVERS, WINDOWS_CLIENTS, LINUX_SERVERS, LINUX_DESKTOPS):
        for label, name in catalog:
            if name == template:
                return label
    if template in EXTRA_LABELS:
        return EXTRA_LABELS[template]
    return re.sub(r"-template$", "", str(template))


def os_family(template):
    template = str(template)
    if template.startswith("win"):
        return "windows"
    if "kali" in template:
        return "kali"
    if template.startswith("rocky"):
        return "redhat"
    return "debian"  # Debian and Ubuntu


# --------------------------------------------------------------------------
# App / tool installer. Each app maps to a Chocolatey package on Windows and,
# where it exists in the standard apt/dnf repos, a Linux package. `lin` can be
# a single name, a {family: name} map for distros that name it differently, or
# None when there's no clean standard-repo package (Windows-only in the picker).
# Installed natively by Ludus: windows.chocolatey_packages / linux.packages.
# --------------------------------------------------------------------------
# Linux packages are only listed when they install cleanly from the distro's
# DEFAULT repos (a wrong name fails the whole deploy). `{debian: ...}` with no
# redhat key means Debian/Ubuntu only - Rocky's base repos are much thinner, so
# anything needing EPEL/RPMFusion is left off there. Browsers are Windows-only
# in the installer because every Linux browser needs an extra repo or snap.
APPS = [
    # Browsers (Windows only - Linux desktops already ship one)
    {"key": "chrome", "name": "Google Chrome", "cat": "Browsers", "win": "googlechrome", "lin": None},
    {"key": "firefox", "name": "Firefox", "cat": "Browsers", "win": "firefox", "lin": None},
    {"key": "brave", "name": "Brave", "cat": "Browsers", "win": "brave", "lin": None},
    # Development
    {"key": "vscode", "name": "Visual Studio Code", "cat": "Development", "win": "vscode", "lin": None},
    {"key": "git", "name": "Git", "cat": "Development", "win": "git", "lin": "git"},
    {"key": "python", "name": "Python 3", "cat": "Development", "win": "python", "lin": "python3"},
    {"key": "nodejs", "name": "Node.js (LTS)", "cat": "Development", "win": "nodejs-lts", "lin": "nodejs"},
    {"key": "notepadpp", "name": "Notepad++", "cat": "Development", "win": "notepadplusplus", "lin": None},
    {"key": "sublime", "name": "Sublime Text", "cat": "Development", "win": "sublimetext4", "lin": None},
    {"key": "docker", "name": "Docker Desktop", "cat": "Development", "win": "docker-desktop", "lin": None},
    {"key": "postman", "name": "Postman", "cat": "Development", "win": "postman", "lin": None},
    {"key": "buildtools", "name": "Build tools (gcc/make)", "cat": "Development", "win": None,
     "lin": {"debian": "build-essential", "redhat": "gcc"}},
    # CLI & terminal
    {"key": "winterminal", "name": "Windows Terminal", "cat": "CLI & terminal", "win": "microsoft-windows-terminal", "lin": None},
    {"key": "curl", "name": "curl", "cat": "CLI & terminal", "win": "curl", "lin": "curl"},
    {"key": "wget", "name": "wget", "cat": "CLI & terminal", "win": "wget", "lin": "wget"},
    {"key": "jq", "name": "jq", "cat": "CLI & terminal", "win": "jq", "lin": "jq"},
    {"key": "vim", "name": "Vim", "cat": "CLI & terminal", "win": "vim", "lin": "vim"},
    {"key": "tmux", "name": "tmux", "cat": "CLI & terminal", "win": None, "lin": "tmux"},
    {"key": "htop", "name": "htop", "cat": "CLI & terminal", "win": None, "lin": "htop"},
    # Security & network
    {"key": "wireshark", "name": "Wireshark", "cat": "Security & network", "win": "wireshark", "lin": "wireshark"},
    {"key": "nmap", "name": "Nmap", "cat": "Security & network", "win": "nmap", "lin": "nmap"},
    {"key": "putty", "name": "PuTTY", "cat": "Security & network", "win": "putty", "lin": {"debian": "putty-tools"}},
    {"key": "winscp", "name": "WinSCP", "cat": "Security & network", "win": "winscp", "lin": None},
    {"key": "filezilla", "name": "FileZilla", "cat": "Security & network", "win": "filezilla", "lin": {"debian": "filezilla"}},
    {"key": "sysinternals", "name": "Sysinternals Suite", "cat": "Security & network", "win": "sysinternals", "lin": None},
    {"key": "systeminformer", "name": "System Informer", "cat": "Security & network", "win": "systeminformer", "lin": None},
    {"key": "advancedip", "name": "Advanced IP Scanner", "cat": "Security & network", "win": "advanced-ip-scanner", "lin": None},
    # Utilities
    {"key": "sevenzip", "name": "7-Zip", "cat": "Utilities", "win": "7zip", "lin": {"debian": "p7zip-full"}},
    {"key": "powertoys", "name": "PowerToys", "cat": "Utilities", "win": "powertoys", "lin": None},
    {"key": "everything", "name": "Everything (search)", "cat": "Utilities", "win": "everything", "lin": None},
    {"key": "qbittorrent", "name": "qBittorrent", "cat": "Utilities", "win": "qbittorrent", "lin": {"debian": "qbittorrent"}},
    {"key": "keepass", "name": "KeePassXC", "cat": "Utilities", "win": "keepassxc", "lin": {"debian": "keepassxc"}},
    {"key": "treesize", "name": "TreeSize Free", "cat": "Utilities", "win": "treesizefree", "lin": None},
    # Media & creative
    {"key": "vlc", "name": "VLC", "cat": "Media & creative", "win": "vlc", "lin": {"debian": "vlc"}},
    {"key": "obs", "name": "OBS Studio", "cat": "Media & creative", "win": "obs-studio", "lin": {"debian": "obs-studio"}},
    {"key": "gimp", "name": "GIMP", "cat": "Media & creative", "win": "gimp", "lin": {"debian": "gimp"}},
    {"key": "audacity", "name": "Audacity", "cat": "Media & creative", "win": "audacity", "lin": {"debian": "audacity"}},
    {"key": "handbrake", "name": "HandBrake", "cat": "Media & creative", "win": "handbrake", "lin": {"debian": "handbrake"}},
    {"key": "spotify", "name": "Spotify", "cat": "Media & creative", "win": "spotify", "lin": None},
    # Communication
    {"key": "discord", "name": "Discord", "cat": "Communication", "win": "discord", "lin": None},
    {"key": "zoom", "name": "Zoom", "cat": "Communication", "win": "zoom", "lin": None},
    {"key": "slack", "name": "Slack", "cat": "Communication", "win": "slack", "lin": None},
    {"key": "thunderbird", "name": "Thunderbird", "cat": "Communication", "win": "thunderbird", "lin": "thunderbird"},
    # Office & docs
    {"key": "libreoffice", "name": "LibreOffice", "cat": "Office & docs", "win": "libreoffice-fresh", "lin": {"debian": "libreoffice"}},
    {"key": "adobereader", "name": "Adobe Acrobat Reader", "cat": "Office & docs", "win": "adobereader", "lin": None},
    {"key": "obsidian", "name": "Obsidian", "cat": "Office & docs", "win": "obsidian", "lin": None},
    # Gaming
    {"key": "steam", "name": "Steam", "cat": "Gaming", "win": "steam", "lin": None},
]
APP_KEYS = {a["key"] for a in APPS}
APP_CATEGORIES = list(dict.fromkeys(a["cat"] for a in APPS))  # preserve first-seen order


def lin_package(app, family):
    """The Linux package name for an app on a given family, or None."""
    lin = app.get("lin")
    if isinstance(lin, dict):
        return lin.get(family)
    return lin


def apps_for_family(family):
    """Apps installable on this OS family, as the picker should show them."""
    if family == "windows":
        return [a for a in APPS if a.get("win")]
    return [a for a in APPS if lin_package(a, family if family in ("debian", "redhat") else "debian")]


def apply_apps(vm, app_keys):
    """Add the chosen apps to a VM via Ludus's native package install."""
    keys = [k for k in app_keys if k in APP_KEYS]
    if not keys:
        return
    family = os_family(vm.get("template"))
    chosen = [a for a in APPS if a["key"] in keys]
    if family == "windows":
        pkgs = [a["win"] for a in chosen if a.get("win")]
        if pkgs:
            win = vm.setdefault("windows", {})
            existing = win.get("chocolatey_packages") or []
            win["chocolatey_packages"] = existing + [p for p in pkgs if p not in existing]
            # 3rd-party-hosted choco packages often drift checksums; tolerate that on a tinker box.
            win["chocolatey_ignore_checksums"] = True
    else:
        lin_fam = "redhat" if family == "redhat" else "debian"
        pkgs = [lin_package(a, lin_fam) for a in chosen]
        pkgs = [p for p in pkgs if p]
        if pkgs:
            lx = vm.get("linux")
            if not isinstance(lx, dict):
                lx = {}
                vm["linux"] = lx
            existing = lx.get("packages") or []
            lx["packages"] = existing + [p for p in pkgs if p not in existing]


def template_states():
    """{template name: 'ready' | 'building' | 'not built'} for templates installed on the server."""
    states = {}
    for t in ludus_json("templates", "list"):
        states[t.get("name")] = ("ready" if t.get("built") else
                                 "building" if t.get("status") == "building" else "not built")
    return states


STATE_NOTES = {"ready": "ready", "building": "building now", "not built": "needs a one-off build",
               None: "downloads and builds the first time"}


# --------------------------------------------------------------------------
# Range config helpers
# --------------------------------------------------------------------------
TOOLS_GROUP = "security_tools"


def vms(cfg):
    return cfg.setdefault("ludus", [])


def is_windows(vm):
    return "windows" in vm or str(vm.get("template", "")).startswith("win")


def is_kali(vm):
    return "kali" in str(vm.get("template", ""))


def is_tool(vm):
    return TOOLS_GROUP in (vm.get("ansible_groups") or [])


def is_dc(vm):
    return (vm.get("domain") or {}).get("role") in ("primary-dc", "alt-dc")


def primary_dcs(cfg):
    return [v for v in vms(cfg) if (v.get("domain") or {}).get("role") == "primary-dc"]


def windows_endpoints(cfg):
    return [v for v in vms(cfg) if not is_tool(v) and os_family(v.get("template")) == "windows"]


def default_os_account(template):
    """The default local login Ludus sets on a template, used to prefill the password-reset form."""
    fam = os_family(template)
    if fam == "windows":
        return "localuser"
    if fam == "kali":
        return "kali"
    if str(template).startswith("debian"):
        return "debian"
    return "localuser"  # ubuntu, rocky and anything else


def hostname_matches(vm, prefix):
    return re.fullmatch(prefix + r"\d+", str(vm.get("hostname", "")), re.IGNORECASE) is not None


def free_octet(cfg, vlan, start):
    used = {vm.get("ip_last_octet") for vm in vms(cfg) if vm.get("vlan") == vlan}
    octet = start
    while octet in used or octet >= 254:
        octet = octet + 1 if octet < 253 else 2
    return octet


def router_ram(cfg):
    return (cfg.get("router") or {}).get("ram_gb", DEFAULT_ROUTER_RAM_GB)


def total_ram(cfg):
    return sum(float(vm.get("ram_gb", 0)) for vm in vms(cfg)) + float(router_ram(cfg))


def templates_needed(cfg):
    needed = {vm.get("template") for vm in vms(cfg)}
    needed.add((cfg.get("router") or {}).get("template", DEFAULT_ROUTER_TEMPLATE))
    return sorted(t for t in needed if t)


def remove_vm(cfg, vm):
    """Remove a VM, plus firewall rules for its VLAN if nothing else lives there."""
    vms(cfg).remove(vm)
    vlan = vm.get("vlan")
    if any(other.get("vlan") == vlan for other in vms(cfg)):
        return
    network = cfg.get("network") or {}
    if network.get("rules"):
        network["rules"] = [r for r in network["rules"] if vlan not in (r.get("vlan_src"), r.get("vlan_dst"))]
        if not network["rules"]:
            del network["rules"]


def role_names(vm):
    names = []
    for role in vm.get("roles") or []:
        names.append(role if isinstance(role, str) else role.get("name"))
    return names


def add_role(vm, role, depends_on=None, role_vars=None):
    if role in role_names(vm):
        return
    if vm.get("roles") is None:
        vm["roles"] = []
    if depends_on:
        vm["roles"].append({"name": role, "depends_on": [{"vm_name": depends_on[0], "role": depends_on[1]}]})
    else:
        vm["roles"].append(role)
    if role_vars:
        if vm.get("role_vars") is None:
            vm["role_vars"] = {}
        vm["role_vars"].update(role_vars)


def host_timezone():
    try:
        with open("/etc/timezone") as fh:
            tz = fh.read().strip()
            if tz:
                return tz
    except OSError:
        pass
    try:
        target = os.readlink("/etc/localtime")
        if "zoneinfo/" in target:
            return target.split("zoneinfo/", 1)[1]
    except OSError:
        pass
    return "Europe/London"


def host_memory_gb():
    total = avail = None
    try:
        with open("/proc/meminfo") as fh:
            for line in fh:
                key, value = line.split(":", 1)
                if key == "MemTotal":
                    total = int(value.split()[0]) / 1024 / 1024
                elif key == "MemAvailable":
                    avail = int(value.split()[0]) / 1024 / 1024
    except OSError:
        pass
    return total, avail


def fmt_gb(value):
    return f"{value:g}" if float(value).is_integer() else f"{value:.1f}"


def fmt_duration(minutes):
    minutes = max(1, int(round(minutes)))
    if minutes < 60:
        return f"{minutes} min"
    hours, mins = divmod(minutes, 60)
    if mins == 0:
        return f"{hours}h"
    return f"{hours}h {mins}m"


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------
def valid_fqdn(value):
    labels = str(value).lower().split(".")
    if len(labels) < 2 or not all(re.fullmatch(r"[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?", lab or "-") for lab in labels):
        return "Use a name like homelab.lan or corp.example.lab (letters, digits, hyphens, at least one dot)."
    if len(labels[0]) > 15:
        return "The first part of the domain becomes the NetBIOS name, so keep it to 15 characters or fewer."
    if labels[-1] == "local":
        return ".local clashes with mDNS on Linux/macOS clients; try .lan or .lab instead."
    return None


def valid_range_id(value, existing=()):
    if not RANGE_ID_RE.match(str(value)):
        return "Start with a letter; letters, digits and hyphens only; 20 characters max."
    if value in existing:
        return f"A lab called {value} already exists."
    return None


def valid_snapshot_name(value):
    return None if SNAPSHOT_RE.match(str(value)) else "Start with a letter; letters, digits, - and _ only."


def valid_hostname(value):
    v = str(value)
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{0,14}", v):
        return "Letters, digits and hyphens; start with a letter or digit; 15 characters max (the Windows limit)."
    if v.isdigit():
        return "A hostname can't be all digits."
    return None


def valid_mcversion(value):
    v = str(value).strip().lower()
    if v == "latest":
        return None
    return None if re.fullmatch(r"\d+\.\d+(\.\d+)?", v) else 'A version like 1.21.4, or "latest".'


def valid_motd(value):
    v = str(value)
    if not v.strip():
        return "Give the server a short description."
    if len(v) > 59:
        return "Keep it under 60 characters (Minecraft's limit)."
    return None


def valid_abspath(value):
    v = str(value).strip()
    if not re.fullmatch(r"/[A-Za-z0-9._/-]{1,98}", v) or "//" in v or v.rstrip("/") == "":
        return "An absolute path like /srv/media (letters, digits, . _ - and / only)."
    return None


def valid_sharename(value):
    return None if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 _-]{0,30}", str(value)) else \
        "Letters, digits, spaces, - and _; start with a letter or digit; 31 characters max."


def valid_username(value):
    return None if re.fullmatch(r"[a-z_][a-z0-9_-]{0,30}", str(value)) else \
        "Lowercase letters, digits, - and _; start with a letter or _; 31 characters max."


def valid_password(value):
    v = str(value)
    if len(v) < 8:
        return "At least 8 characters."
    if len(v) > 128:
        return "Keep it under 128 characters."
    return None


VALIDATORS = {"fqdn": valid_fqdn, "hostname": valid_hostname, "mcversion": valid_mcversion,
              "motd": valid_motd, "abspath": valid_abspath, "sharename": valid_sharename,
              "username": valid_username, "password": valid_password}


# --------------------------------------------------------------------------
# Lab catalogue
# Each lab has an option schema (shown as questions in the terminal and as a
# form in the web GUI) and an apply() that turns the answers into config edits.
#
# Field types: text, int, bool, select (choices: [[value, label, help]]),
# template (catalog: key of OS_CATALOGS), apps (multi-select of APP keys).
# Optional: help, show_if (bool field key), validate (key of VALIDATORS),
# min/max for int.
# --------------------------------------------------------------------------
def _ad_parts(cfg):
    machines = vms(cfg)
    dc = next((v for v in machines if (v.get("domain") or {}).get("role") == "primary-dc"), None)
    workstations = [v for v in machines if hostname_matches(v, "WS")]
    kali = next((v for v in machines if is_kali(v)), None)
    return machines, dc, workstations, kali


def schema_ad_basic(cfg):
    machines, dc, workstations, kali = _ad_parts(cfg)
    if not dc or not workstations:
        return []
    fields = [
        {"key": "domain", "type": "text", "label": "Domain name", "default": dc["domain"]["fqdn"],
         "validate": "fqdn", "help": "e.g. homelab.lan. The first part becomes the NetBIOS name."},
        {"key": "dc_template", "type": "template", "catalog": "windows_server",
         "label": "Domain controller version", "default": dc["template"]},
        {"key": "workstations", "type": "int", "label": "Workstations", "min": 1, "max": 4,
         "default": len(workstations), "help": "About 6 GB each."},
        {"key": "ws_template", "type": "template", "catalog": "windows_client",
         "label": "Workstation version", "default": workstations[0]["template"]},
        {"key": "member_server", "type": "bool", "label": "Add a member server",
         "default": any(hostname_matches(v, "SRV") for v in machines),
         "help": "A domain-joined Windows Server for file shares, GPO and IIS practice. 4 GB."},
        {"key": "server_template", "type": "template", "catalog": "windows_server",
         "label": "Member server version", "default": dc["template"], "show_if": "member_server"},
    ]
    if kali:
        fields.append({"key": "kali", "type": "bool", "label": "Include Kali", "default": True,
                       "help": "An attacker VM on its own VLAN."})
    fields.append({"key": "office", "type": "bool", "label": "Install Microsoft Office on workstations",
                   "default": False, "help": "Adds a lot of build time."})
    return fields


def apply_ad_basic(cfg, o):
    machines, dc, workstations, kali = _ad_parts(cfg)
    if not dc or not workstations:
        return
    fqdn = o["domain"].lower()
    for vm in machines:
        if vm.get("domain"):
            vm["domain"]["fqdn"] = fqdn
    dc["template"] = o["dc_template"]
    if "win2025" in dc["template"]:
        # Server 2025 domain controllers need at least the 2016 functional level.
        defaults = cfg.setdefault("defaults", {})
        defaults.setdefault("ad_domain_functional_level", "WinThreshold")
        defaults.setdefault("ad_forest_functional_level", "WinThreshold")

    base = workstations[0]
    base["template"] = o["ws_template"]
    for vm in workstations:
        machines.remove(vm)
    for vm in [v for v in machines if hostname_matches(v, "SRV")]:
        machines.remove(vm)

    new_ws = []
    for i in range(1, o["workstations"] + 1):
        ws = copy.deepcopy(base)
        ws["vm_name"] = "{{ range_id }}-ws%02d" % i
        ws["hostname"] = "WS%02d" % i
        ws["ip_last_octet"] = free_octet(cfg, base["vlan"], base["ip_last_octet"] + i - 1)
        win = ws.setdefault("windows", {})
        if o["office"]:
            win["office_version"] = 2019
            win["office_arch"] = "64bit"
        else:
            win.pop("office_version", None)
            win.pop("office_arch", None)
        machines.append(ws)
        new_ws.append(ws)

    if o["member_server"]:
        machines.insert(machines.index(dc) + 1, {
            "vm_name": "{{ range_id }}-srv01",
            "hostname": "SRV01",
            "template": o["server_template"],
            "vlan": dc["vlan"],
            "ip_last_octet": free_octet(cfg, dc["vlan"], 12),
            "ram_gb": 4,
            "cpus": 2,
            # May be cloned from the same template as the DC, so give it a fresh SID.
            "windows": {"sysprep": True},
            "domain": {"fqdn": fqdn, "role": "member"},
        })

    if kali and not o.get("kali", True):
        remove_vm(cfg, kali)

    def rank(v):
        return 0 if v is dc else 1 if hostname_matches(v, "SRV") else 2 if v in new_ws else 3
    machines.sort(key=rank)


def _net_parts(cfg):
    machines = vms(cfg)
    return machines, {name: [v for v in machines if hostname_matches(v, name)] for name in ("web", "app", "db", "jump")}


def schema_linux_net(cfg):
    machines, roles = _net_parts(cfg)
    if not roles["web"] or not roles["app"]:
        return []
    fields = [
        {"key": "linux_template", "type": "template", "catalog": "linux_server",
         "label": "Linux version", "default": roles["web"][0]["template"]},
        {"key": "web_count", "type": "int", "label": "Web servers in the DMZ (VLAN 10)", "min": 1, "max": 3,
         "default": len(roles["web"])},
        {"key": "app_count", "type": "int", "label": "App servers on the internal VLAN (VLAN 20)", "min": 1, "max": 3,
         "default": len(roles["app"])},
    ]
    if roles["db"]:
        fields.append({"key": "db", "type": "bool", "label": "Include the database server", "default": True,
                       "help": "MariaDB on the internal VLAN."})
    if roles["jump"]:
        fields.append({"key": "jump", "type": "bool", "label": "Include the management jump box", "default": True,
                       "help": "On VLAN 30, allowed to reach everything."})
    segmented = (cfg.get("network") or {}).get("inter_vlan_default", "ACCEPT") != "ACCEPT"
    current_ram = roles["web"][0].get("ram_gb", 2)
    fields += [
        {"key": "services", "type": "bool", "label": "Pre-install services and network tools", "default": True,
         "help": "nginx, MariaDB, tcpdump, nmap, mtr and friends."},
        {"key": "firewall", "type": "select", "label": "Firewall between VLANs",
         "default": "segmented" if segmented else "open",
         "choices": [["segmented", "Segmented", "Only the documented flows are allowed. Good for firewall practice."],
                     ["open", "Open", "Everything can talk to everything."]]},
        {"key": "ram", "type": "select", "label": "RAM per server",
         "default": current_ram if current_ram in (1, 2, 4) else 2,
         "choices": [[1, "1 GB", ""], [2, "2 GB", "Recommended"], [4, "4 GB", ""]]},
    ]
    return fields


def apply_linux_net(cfg, o):
    machines, roles = _net_parts(cfg)
    if not roles["web"] or not roles["app"]:
        return
    template = o["linux_template"]

    def rebuild(kind, count):
        current = roles[kind]
        base = current[0]
        for vm in current:
            machines.remove(vm)
        for i in range(1, count + 1):
            vm = copy.deepcopy(base)
            vm["vm_name"] = "{{ range_id }}-%s%02d" % (kind, i)
            vm["hostname"] = "%s%02d" % (kind, i)
            vm["ip_last_octet"] = free_octet(cfg, base["vlan"], base["ip_last_octet"] + i - 1)
            machines.append(vm)

    rebuild("web", o["web_count"])
    rebuild("app", o["app_count"])
    if roles["db"] and not o.get("db", True):
        for vm in roles["db"]:
            remove_vm(cfg, vm)
    if roles["jump"] and not o.get("jump", True):
        for vm in roles["jump"]:
            remove_vm(cfg, vm)

    for vm in machines:
        if os_family(vm.get("template")) in ("debian", "redhat"):
            vm["template"] = template
        if not o["services"]:
            vm["linux"] = True
        elif os_family(template) == "redhat" and isinstance(vm.get("linux"), dict):
            vm["linux"]["packages"] = [RHEL_PACKAGE_NAMES.get(p, p) for p in vm["linux"].get("packages", [])]
        vm["ram_gb"] = o["ram"]
        if o["ram"] > 1:
            vm["ram_min_gb"] = 1
        else:
            vm.pop("ram_min_gb", None)

    if o["firewall"] == "open":
        cfg["network"] = {"inter_vlan_default": "ACCEPT"}

    order = {"web": 0, "app": 1, "db": 2, "jump": 3}
    machines.sort(key=lambda v: (order.get(re.sub(r"\d+$", "", str(v.get("hostname", ""))), 9), str(v.get("hostname"))))


def schema_kali_toggle(cfg):
    if any(is_kali(v) for v in vms(cfg)):
        return [{"key": "kali", "type": "bool", "label": "Include Kali", "default": True,
                 "help": "An attacker VM inside the lab."}]
    return []


def apply_kali_toggle(cfg, o):
    kali = next((v for v in vms(cfg) if is_kali(v)), None)
    if kali and not o.get("kali", True):
        remove_vm(cfg, kali)


def _sandbox_vm(cfg):
    machines = vms(cfg)
    return machines[0] if machines else None


def schema_sandbox(cfg):
    vm = _sandbox_vm(cfg)
    if not vm:
        return []
    catalog_templates = [t for _, t in SANDBOX_OSES]
    default_os = vm.get("template") if vm.get("template") in catalog_templates else SANDBOX_OSES[0][1]
    return [
        {"key": "os", "type": "template", "catalog": "sandbox", "label": "Operating system",
         "default": default_os, "rerender": True, "os_field_for": "apps",
         "help": "One machine to tinker with. Desktops have a GUI; servers are headless."},
        {"key": "hostname", "type": "text", "label": "Hostname", "default": vm.get("hostname") or "sandbox",
         "validate": "hostname", "help": "What the machine calls itself."},
        {"key": "ram", "type": "int", "label": "RAM (GB)", "min": 2, "max": 32, "default": int(vm.get("ram_gb") or 4)},
        {"key": "cpus", "type": "int", "label": "CPUs", "min": 1, "max": 8, "default": int(vm.get("cpus") or 2)},
        {"key": "apps", "type": "apps", "label": "Apps to pre-install", "default": [], "os_field": "os",
         "help": "Installed during the build, before you ever log in."},
    ]


def apply_sandbox(cfg, o):
    vm = _sandbox_vm(cfg)
    if not vm:
        return
    slug = re.sub(r"[^A-Za-z0-9-]", "-", str(o["hostname"]).lower()).strip("-")[:30] or "box"
    vm["template"] = o["os"]
    vm["hostname"] = o["hostname"]
    vm["vm_name"] = "{{ range_id }}-" + slug
    vm["ram_gb"] = o["ram"]
    vm["cpus"] = o["cpus"]
    # Reset OS markers so they match the chosen family, then re-apply apps.
    vm.pop("windows", None)
    vm.pop("linux", None)
    vm.pop("domain", None)
    if os_family(o["os"]) == "windows":
        vm["windows"] = {"sysprep": False}
    else:
        vm["linux"] = True
    apply_apps(vm, o.get("apps") or [])


# --------------------------------------------------------------------------
# Services: single-VM, ready-to-use servers (not cyber-range labs). Each is one
# headless Debian VM with an Ansible role that installs and configures the
# service. They show up in their own group in the builder and, once deployed,
# in "Your labs" alongside everything else - a single pane of glass.
# --------------------------------------------------------------------------
SERVICE_TEMPLATE = "debian-12-x64-server-template"


def _service_vm(cfg):
    machines = vms(cfg)
    return machines[0] if machines else None


def _apply_service(cfg, o, role, role_vars, default_host):
    vm = _service_vm(cfg)
    if not vm:
        return
    host = str(o.get("hostname") or default_host)
    slug = re.sub(r"[^A-Za-z0-9-]", "-", host.lower()).strip("-")[:30] or default_host
    vm["template"] = SERVICE_TEMPLATE
    vm["hostname"] = host
    vm["vm_name"] = "{{ range_id }}-" + slug
    vm["ram_gb"] = int(o["ram"])
    vm["cpus"] = int(o.get("cpus") or 2)
    vm.pop("windows", None)
    vm.pop("domain", None)
    vm["linux"] = True
    vm["roles"] = [role]
    vm["role_vars"] = role_vars


MC_DIFFICULTY = [("peaceful", "Peaceful"), ("easy", "Easy"), ("normal", "Normal"), ("hard", "Hard")]
MC_GAMEMODE = [("survival", "Survival"), ("creative", "Creative"), ("adventure", "Adventure")]
MC_FLAVOR = [("paper", "Paper", "Faster, and supports plugins"),
             ("vanilla", "Vanilla", "The official Mojang server")]


def schema_minecraft(cfg):
    return [
        {"key": "flavor", "type": "select", "label": "Server type", "default": "paper", "choices": MC_FLAVOR,
         "group": "Server", "help": "Paper performs better and supports plugins; Vanilla is the stock Mojang server."},
        {"key": "mc_version", "type": "text", "label": "Minecraft version", "default": "latest", "validate": "mcversion",
         "group": "Server", "help": 'A version like 1.21.4, or "latest" for the newest release at build time.'},
        {"key": "hostname", "type": "text", "label": "Hostname", "default": "minecraft", "validate": "hostname", "group": "Server"},
        {"key": "ram", "type": "int", "label": "RAM (GB)", "min": 2, "max": 24, "default": 6, "group": "Resources",
         "help": "Minecraft likes RAM; 6-8 GB is comfortable for a handful of players."},
        {"key": "cpus", "type": "int", "label": "CPUs", "min": 1, "max": 8, "default": 2, "group": "Resources"},
        {"key": "max_players", "type": "int", "label": "Max players", "min": 1, "max": 100, "default": 10, "group": "Gameplay"},
        {"key": "difficulty", "type": "select", "label": "Difficulty", "default": "normal", "choices": MC_DIFFICULTY, "group": "Gameplay"},
        {"key": "gamemode", "type": "select", "label": "Default game mode", "default": "survival", "choices": MC_GAMEMODE, "group": "Gameplay"},
        {"key": "motd", "type": "text", "label": "Server description", "default": "A Minecraft Server", "validate": "motd",
         "group": "Gameplay", "help": "The line players see in their server list."},
        {"key": "whitelist", "type": "bool", "label": "Whitelist only", "default": False, "group": "Gameplay",
         "help": "On: only names you add can join. Off: anyone who can reach it (online-mode still checks real Mojang accounts)."},
    ]


def apply_minecraft(cfg, o):
    _apply_service(cfg, o, "homelab.homelab_minecraft", {
        "minecraft_flavor": o["flavor"],
        "minecraft_version": str(o["mc_version"]).lower(),
        "minecraft_max_players": int(o["max_players"]),
        "minecraft_difficulty": o["difficulty"],
        "minecraft_gamemode": o["gamemode"],
        "minecraft_motd": o["motd"],
        "minecraft_whitelist": bool(o["whitelist"]),
        "minecraft_online_mode": True,
    }, "minecraft")


def schema_jellyfin(cfg):
    return [
        {"key": "hostname", "type": "text", "label": "Hostname", "default": "jellyfin", "validate": "hostname"},
        {"key": "ram", "type": "int", "label": "RAM (GB)", "min": 2, "max": 16, "default": 4,
         "help": "4 GB is plenty for direct play; add more if several people transcode at once."},
        {"key": "cpus", "type": "int", "label": "CPUs", "min": 2, "max": 12, "default": 4,
         "help": "Transcoding is CPU-heavy, so more cores help with multiple simultaneous streams."},
        {"key": "media_dir", "type": "text", "label": "Media folder (on the VM)", "default": "/srv/media", "validate": "abspath",
         "help": "Where your libraries live. An empty folder is created; add media later over the file share or SSH."},
    ]


def apply_jellyfin(cfg, o):
    _apply_service(cfg, o, "homelab.homelab_jellyfin", {"jellyfin_media_dir": o["media_dir"]}, "jellyfin")


def schema_samba(cfg):
    return [
        {"key": "hostname", "type": "text", "label": "Hostname", "default": "fileshare", "validate": "hostname", "group": "Machine"},
        {"key": "ram", "type": "int", "label": "RAM (GB)", "min": 1, "max": 8, "default": 2, "group": "Machine"},
        {"key": "cpus", "type": "int", "label": "CPUs", "min": 1, "max": 4, "default": 2, "group": "Machine"},
        {"key": "share_name", "type": "text", "label": "Share name", "default": "share", "validate": "sharename",
         "group": "Share", "help": r"What the share is called on the network, e.g. \\fileshare\share."},
        {"key": "share_dir", "type": "text", "label": "Share folder (on the VM)", "default": "/srv/share", "validate": "abspath", "group": "Share"},
        {"key": "smb_user", "type": "text", "label": "Username", "default": "share", "validate": "username", "group": "Share"},
        {"key": "smb_password", "type": "password", "label": "Password", "default": "", "validate": "password",
         "group": "Share", "help": "Used to connect to the share. At least 8 characters."},
    ]


def apply_samba(cfg, o):
    _apply_service(cfg, o, "homelab.homelab_samba", {
        "samba_share_name": o["share_name"],
        "samba_share_dir": o["share_dir"],
        "samba_user": o["smb_user"],
        "samba_password": o["smb_password"],
    }, "fileshare")


KNOWN_SERVICES = [
    {"suffix": "/service-minecraft", "title": "Minecraft server", "range_id": "mc", "kind": "service", "single": True,
     "blurb": "A ready-to-play Minecraft Java server (Paper or Vanilla), sized and configured how you like.",
     "note": "Once it's deployed, connect in Minecraft (Java Edition) to this VM's IP on port 25565. "
             "It's reachable on your LAN/VPN; to let friends in from outside, forward TCP 25565 to it on your router.",
     "schema": schema_minecraft, "apply": apply_minecraft},
    {"suffix": "/service-jellyfin", "title": "Jellyfin media server", "range_id": "jf", "kind": "service", "single": True,
     "blurb": "Your own streaming service for the movies, shows and music you own. Finish setup in the browser.",
     "note": "Once it's deployed, open http://<this VM's IP>:8096 in a browser to create your admin account "
             "and add libraries. Copy media onto it over the file share or SSH.",
     "schema": schema_jellyfin, "apply": apply_jellyfin},
    {"suffix": "/service-samba", "title": "Samba file share", "range_id": "fs", "kind": "service", "single": True,
     "blurb": "A private, password-protected network drive on your LAN. Map it from Windows, macOS or Linux.",
     "note": r"Once it's deployed, map \\<this VM's IP>\<share name> with the username and password you set above.",
     "schema": schema_samba, "apply": apply_samba},
]


KNOWN_LABS = [
    {"suffix": "/sandbox", "title": "Single VM sandbox", "range_id": "box", "single": True,
     "blurb": "One machine to tinker with: pick any Windows or Linux OS, size it, and tick apps to pre-install. "
              "No domain, no network to manage.",
     "schema": schema_sandbox, "apply": apply_sandbox},
    {"suffix": "/ad-basic", "title": "Basic Active Directory lab", "range_id": "ad",
     "blurb": "A domain controller, workstations, an optional member server and Kali. Pick the Windows versions.",
     "schema": schema_ad_basic, "apply": apply_ad_basic},
    {"suffix": "/goad", "title": "GOAD - Game of Active Directory", "range_id": "goad",
     "blurb": "A deliberately vulnerable multi-domain AD lab: 5 Windows servers and Kali. "
              "Windows versions stay on the tested Server 2022 build.",
     "schema": schema_kali_toggle, "apply": apply_kali_toggle},
    {"suffix": "/linux-net", "title": "Linux networking lab", "range_id": "net",
     "blurb": "Linux servers across DMZ, internal and management VLANs with firewall rules. Pick Debian, Ubuntu or Rocky.",
     "schema": schema_linux_net, "apply": apply_linux_net},
]


def known_lab(blueprint_id):
    return next((lab for lab in KNOWN_LABS + KNOWN_SERVICES if str(blueprint_id).endswith(lab["suffix"])), None)


def lab_info(blueprint):
    """Display info for a blueprint list entry."""
    bp_id = blueprint.get("blueprintID", "")
    lab = known_lab(bp_id)
    base_id = lab["range_id"] if lab else re.sub(r"[^A-Za-z0-9-]", "-", bp_id.split("/")[-1])[:20].strip("-") or "lab"
    if not base_id[0].isalpha():
        base_id = "lab-" + base_id
    return {
        "blueprint": bp_id,
        "title": lab["title"] if lab else blueprint.get("name") or bp_id,
        "blurb": lab["blurb"] if lab else blueprint.get("description", ""),
        "known": bool(lab),
        "kind": (lab.get("kind", "lab") if lab else "lab"),
        "single": bool(lab.get("single")) if lab else False,
        "note": (lab.get("note") if lab else None),
        "range_id": base_id[:20],
    }


def lab_schema(blueprint_id, cfg):
    lab = known_lab(blueprint_id)
    return lab["schema"](cfg) if lab else []


def validate_options(schema, raw):
    """Check answers against a schema. Returns a cleaned dict; raises ValueError with a readable message."""
    raw = raw or {}
    clean = {}
    for field in schema:
        key = field["key"]
        if field.get("show_if") and not clean.get(field["show_if"]):
            clean[key] = field["default"]
            continue
        value = raw.get(key, field["default"])
        kind = field["type"]
        if kind in ("text", "password"):
            value = str(value).strip() if kind == "text" else str(value)
            check = VALIDATORS.get(field.get("validate"))
            problem = check(value) if check else None
            if problem:
                raise ValueError(f"{field['label']}: {problem}")
        elif kind == "int":
            try:
                value = int(value)
            except (TypeError, ValueError):
                raise ValueError(f"{field['label']}: enter a whole number.")
            if not field.get("min", value) <= value <= field.get("max", value):
                raise ValueError(f"{field['label']}: choose from {field['min']} to {field['max']}.")
        elif kind == "bool":
            if isinstance(value, str):
                value = value.lower() in ("1", "true", "yes", "on")
            value = bool(value)
        elif kind == "select":
            allowed = [c[0] for c in field["choices"]]
            match = next((a for a in allowed if str(a) == str(value)), None)
            if match is None:
                raise ValueError(f"{field['label']}: pick one of the listed options.")
            value = match
        elif kind == "template":
            allowed = [t for _, t in OS_CATALOGS[field["catalog"]]]
            if value not in allowed:
                raise ValueError(f"{field['label']}: pick one of the listed versions.")
        elif kind == "apps":
            want = value if isinstance(value, (list, tuple)) else []
            value = [k for k in want if k in APP_KEYS]
        clean[key] = value
    return clean


# --------------------------------------------------------------------------
# Security tooling add-ons. These can be added to any lab.
# Tool servers go on their own VLAN; agents go on every Windows/Linux VM in
# the lab except Kali.
# --------------------------------------------------------------------------
ELASTIC_PASSWORD = "elasticpassword"
WAZUH_PASSWORD = "Wazuh-Admin-1"   # Wazuh needs upper, lower, digit and one of . * + ? -
VELOCIRAPTOR_PASSWORD = "password"

ADDONS = [
    {"key": "elastic", "title": "Elastic Security (ELK SIEM)", "ram": 8,
     "detail": "Elasticsearch, Kibana and Fleet on a Debian VM. Elastic Agent on every endpoint."},
    {"key": "wazuh", "title": "Wazuh SIEM / XDR", "ram": 8,
     "detail": "Wazuh on an Ubuntu VM. Wazuh agent on every endpoint."},
    {"key": "velociraptor", "title": "Velociraptor", "ram": 4,
     "detail": "DFIR and endpoint visibility server. Client on every Windows, Debian and Ubuntu endpoint."},
    {"key": "sysmon", "title": "Sysmon logging on Windows", "ram": 0,
     "detail": "Rich process, network and registry logging on every Windows endpoint, fed into "
               "whichever SIEM you add: Elastic collects it through its own agent, Wazuh reads the "
               "Sysmon channel. On its own, with no SIEM, the events just stay in the Windows log."},
    {"key": "dfir", "title": "DFIR analyst workstation", "ram": 6,
     "detail": "A Windows VM with Eric Zimmerman's tools, Hayabusa, Chainsaw, Volatility 3, Sysinternals and Wireshark."},
    {"key": "ghosts", "title": "GHOSTS user activity", "ram": 6, "requires_windows": True,
     "detail": "Simulated user behaviour driven from a Linux server, with a client on each Windows endpoint. "
               "Heavy: ~12 GB of Docker images and a bundled LLM."},
    {"key": "badblood", "title": "BadBlood (random AD objects)", "ram": 0, "requires_dc": True,
     "detail": "Fills the domain with thousands of realistic users, groups, OUs and ACLs. Runs on the domain controller."},
]
ADDON_KEYS = [a["key"] for a in ADDONS]


def addon_applicability(cfg):
    """{addon_key: reason-it-can't-apply or None} for a given base config."""
    has_dc = bool(primary_dcs(cfg))
    has_win = bool(windows_endpoints(cfg))
    reasons = {}
    for a in ADDONS:
        if a.get("requires_dc") and not has_dc:
            reasons[a["key"]] = "needs an Active Directory domain"
        elif a.get("requires_windows") and not has_win:
            reasons[a["key"]] = "needs a Windows endpoint"
        else:
            reasons[a["key"]] = None
    return reasons
DFIR_FIELD = {"key": "dfir_template", "type": "template", "catalog": "windows_any",
              "label": "DFIR workstation version", "default": "win11-22h2-x64-enterprise-template"}

# Role on the server VM -> what to show once it's deployed.
TOOL_ACCESS = [
    ("ludus_elastic_container", "Kibana (Elastic Security)", "https://{ip}:5601",
     lambda rv: ("elastic", rv.get("ludus_elastic_password", ELASTIC_PASSWORD))),
    ("wazuh_server_install", "Wazuh dashboard", "https://{ip}",
     lambda rv: ("admin", rv.get("wazuh_admin_password", "(random - see the deploy log)"))),
    ("ludus_velociraptor_server", "Velociraptor GUI", "https://{ip}:8889",
     lambda rv: (rv.get("velociraptor_admin_user", "admin"), rv.get("velociraptor_admin_password", "pleasechangeme"))),
    ("homelab_dfir_tools", "DFIR workstation", "rdp://{ip}",
     lambda rv: ("localuser", "password")),
    ("ludus_ghosts_server", "GHOSTS UI", "http://{ip}:8080",
     lambda rv: ("", "(no login)")),
    ("ludus_ghosts_server", "GHOSTS Grafana", "http://{ip}:3000",
     lambda rv: ("admin", "admin")),
]


def tool_server(name, template, vlan, octet, ram, cpus, role, role_vars, packages=None):
    return {
        "vm_name": "{{ range_id }}-" + name,
        "hostname": name,
        "template": template,
        "vlan": vlan,
        "ip_last_octet": octet,
        "ram_gb": ram,
        "cpus": cpus,
        "linux": {"packages": packages} if packages else True,
        "ansible_groups": [TOOLS_GROUP],
        # Tool servers keep internet access in testing mode (updates, rule feeds).
        "testing": {"snapshot": False, "block_internet": False},
        "roles": [role],
        "role_vars": role_vars,
    }


def apply_addons(cfg, keys, dfir_template=None):
    """Wire the chosen add-ons into the config. Returns (titles, notes, tools_vlan)."""
    keys = [k for k in ADDON_KEYS if k in keys]
    if not keys:
        return [], [], None
    endpoints = [v for v in vms(cfg) if not is_kali(v) and not is_tool(v)]
    used = {v.get("vlan") for v in vms(cfg)}
    vlan = next(v for v in (50, 60, 70, 80, 90, 150, 200, 250) if v not in used)
    new_vms, ports, notes = [], [], []

    if "elastic" in keys:
        server = tool_server("elastic", "debian-12-x64-server-template", vlan, 10, 8, 4,
                             "badsectorlabs.ludus_elastic_container", {"ludus_elastic_password": ELASTIC_PASSWORD})
        new_vms.append(server)
        ports += ["5601", "8220", "9200"]
        for vm in endpoints:
            if os_family(vm.get("template")) in ("windows", "debian", "redhat"):
                # When the Sysmon add-on is on, let Elastic install and collect Sysmon itself
                # on Windows (its own supported path, so the events reliably reach Kibana). The
                # separate sysmon-modular role then skips Elastic's Windows endpoints.
                want_sysmon = "sysmon" in keys and os_family(vm.get("template")) == "windows"
                add_role(vm, "badsectorlabs.ludus_elastic_agent",
                         (server["vm_name"], "badsectorlabs.ludus_elastic_container"),
                         {"ludus_elastic_install_sysmon": want_sysmon})

    if "wazuh" in keys:
        server = tool_server("wazuh", "ubuntu-22.04-x64-server-template", vlan, 11, 8, 4,
                             "aleemladha.wazuh_server_install", {"wazuh_admin_password": WAZUH_PASSWORD})
        new_vms.append(server)
        ports += ["443", "1514", "1515", "55000"]
        # Give the agents the server's address directly. The Windows agent role's own lookup
        # text-matches role lists and would also match the depends_on entries below.
        manager = "10.{{ range_second_octet }}.%d.11" % vlan
        for vm in endpoints:
            family = os_family(vm.get("template"))
            if family == "windows":
                add_role(vm, "aleemladha.ludus_wazuh_agent", (server["vm_name"], "aleemladha.wazuh_server_install"),
                         {"ludus_wazuh_siem_server": manager})
            elif family in ("debian", "redhat"):
                add_role(vm, "homelab.homelab_wazuh_agent_linux", (server["vm_name"], "aleemladha.wazuh_server_install"),
                         {"homelab_wazuh_manager": manager})

    if "velociraptor" in keys:
        server = tool_server("velociraptor", "debian-12-x64-server-template", vlan, 12, 4, 2,
                             "ludus_velociraptor_server",
                             {"velociraptor_admin_user": "admin", "velociraptor_admin_password": VELOCIRAPTOR_PASSWORD},
                             packages=["python3-pexpect"])
        new_vms.append(server)
        ports += ["8000", "8889"]
        skipped = []
        for vm in endpoints:
            if os_family(vm.get("template")) in ("windows", "debian"):
                add_role(vm, "ludus_velociraptor_client", (server["vm_name"], "ludus_velociraptor_server"))
            else:
                skipped.append(str(vm.get("hostname")))
        if skipped:
            notes.append("Velociraptor's client role doesn't support Rocky Linux, so these get no client: "
                         + ", ".join(skipped))

    if "sysmon" in keys:
        # Sysmon is only useful if a SIEM collects its events.
        if "elastic" not in keys and "wazuh" not in keys:
            notes.append("Sysmon is on but no SIEM was added to collect it, so its events just "
                         "stay in each machine's Windows event log. Add Elastic or Wazuh to see them.")
        for vm in endpoints:
            if os_family(vm.get("template")) != "windows":
                continue
            # Elastic installs and collects its own Sysmon on its Windows endpoints (above),
            # so only add sysmon-modular where Elastic isn't doing it. Wazuh reads the Sysmon
            # channel from this install.
            if "elastic" not in keys:
                add_role(vm, "homelab.homelab_sysmon")

    if "ghosts" in keys and windows_endpoints(cfg):
        server = tool_server("ghosts", "ubuntu-24.04-x64-server-template", vlan, 13, 6, 4,
                             "ludus_ghosts_server", {})
        new_vms.append(server)
        ports += ["5000"]   # Windows clients report activity to the GHOSTS API
        for vm in endpoints:
            # Simulated users belong on workstations and member servers, not domain controllers.
            if os_family(vm.get("template")) == "windows" and not is_dc(vm):
                # The client auto-discovers the server VM by its role, like the Elastic agent.
                add_role(vm, "ludus_ghosts_client",
                         (server["vm_name"], "ludus_ghosts_server"))

    if "badblood" in keys:
        for dc in primary_dcs(cfg):
            # BadBlood runs last on the DC: it clones from GitHub and adds thousands of AD objects.
            add_role(dc, "ludus_badblood")

    if "dfir" in keys:
        new_vms.append({
            "vm_name": "{{ range_id }}-dfir01",
            "hostname": "DFIR01",
            "template": dfir_template or DFIR_FIELD["default"],
            "vlan": vlan,
            "ip_last_octet": 20,
            "ram_gb": 6,
            "cpus": 4,
            "windows": {"sysprep": False},
            "ansible_groups": [TOOLS_GROUP],
            "roles": ["homelab.homelab_dfir_tools"],
        })

    # Tool servers go first so they're up before the agents try to enrol.
    cfg["ludus"] = new_vms + vms(cfg)

    network = cfg.get("network")
    if network and network.get("inter_vlan_default", "ACCEPT") != "ACCEPT":
        rules = network.get("rules") or []
        if ports:
            rules.append({"name": "Endpoints to the security tools", "vlan_src": "all", "vlan_dst": vlan,
                          "protocol": "tcp", "ports": ",".join(ports), "action": "ACCEPT"})
        rules.append({"name": "Security tools VLAN to everything", "vlan_src": vlan, "vlan_dst": "all",
                      "protocol": "all", "ports": "all", "action": "ACCEPT"})
        network["rules"] = rules

    return [a["title"] for a in ADDONS if a["key"] in keys], notes, vlan


def tool_logins(cfg, range_number):
    """[{label, url, username, password}] for the tool servers in a config."""
    found = []
    for vm in vms(cfg):
        roles = " ".join(str(r) for r in role_names(vm))
        for marker, label, url, login in TOOL_ACCESS:
            if marker in roles:
                ip = f"10.{range_number}.{vm.get('vlan')}.{vm.get('ip_last_octet')}"
                user, password = login(vm.get("role_vars") or {})
                found.append({"label": label, "url": url.format(ip=ip), "ip": ip,
                              "username": user, "password": password})
    return found


# --------------------------------------------------------------------------
# RAM profiles
# --------------------------------------------------------------------------
def lean_copy(cfg):
    lean = copy.deepcopy(cfg)
    for vm in vms(lean):
        ram = vm.get("ram_gb", 0)
        if is_windows(vm):
            vm["ram_gb"] = min(ram, max(4, math.ceil(ram / 2)))
        elif is_kali(vm):
            vm["ram_gb"] = min(ram, max(3, math.ceil(ram / 2)))
        elif is_tool(vm):
            vm["ram_gb"] = min(ram, max(2, ram - 2))
    return lean


def ram_profiles(cfg):
    """(standard GB, lean GB or None if lean saves nothing)."""
    standard = total_ram(cfg)
    lean = total_ram(lean_copy(cfg))
    return standard, (lean if lean < standard else None)


def apply_ram_profile(cfg, profile):
    if profile == "lean":
        cfg["ludus"] = lean_copy(cfg)["ludus"]


# --------------------------------------------------------------------------
# Putting a lab together
# --------------------------------------------------------------------------
def build_lab_config(blueprint_id, base_cfg, options, addons=(), dfir_template=None,
                     ram_profile="standard", timezone=None):
    """Return (cfg, info) for a blueprint plus the user's choices. Options must be validated."""
    cfg = copy.deepcopy(base_cfg)
    lab = known_lab(blueprint_id)
    if lab:
        lab["apply"](cfg, options)
    titles, notes, tools_vlan = apply_addons(cfg, addons, dfir_template)
    standard, lean = ram_profiles(cfg)
    if ram_profile == "lean" and lean is not None:
        apply_ram_profile(cfg, "lean")
    else:
        ram_profile = "standard"
    defaults = cfg.get("defaults") or {}
    defaults.setdefault("timezone", timezone or host_timezone())
    cfg["defaults"] = defaults
    return cfg, {"addons": titles, "notes": notes, "tools_vlan": tools_vlan,
                 "ram_profile": ram_profile, "ram_standard": standard, "ram_lean": lean}


def describe_vm(vm, range_id="x"):
    family = os_family(vm.get("template"))
    kind = "tool" if is_tool(vm) else "kali" if family == "kali" else "windows" if family == "windows" else "linux"
    tags = []
    names = " ".join(str(r) for r in role_names(vm))
    for marker, tag in (("elastic_agent", "Elastic agent"), ("wazuh_agent", "Wazuh agent"),
                        ("velociraptor_client", "Velociraptor"), ("homelab_sysmon", "Sysmon"),
                        ("ludus_adcs", "ADCS"), ("ludus_mssql", "MSSQL")):
        if marker in names:
            tags.append(tag)
    # Elastic installs Sysmon through its own agent rather than the homelab_sysmon role.
    if (vm.get("role_vars") or {}).get("ludus_elastic_install_sysmon") and "Sysmon" not in tags:
        tags.append("Sysmon")
    domain = (vm.get("domain") or {})
    if domain.get("role") == "primary-dc":
        tags.insert(0, f"DC for {domain.get('fqdn')}")
    elif domain.get("role") == "member":
        tags.insert(0, f"Joined to {domain.get('fqdn')}")
    return {
        "name": str(vm.get("vm_name", "")).replace("{{ range_id }}", range_id),
        "hostname": str(vm.get("hostname", "")).replace("{{ range_id }}", range_id),
        "template": vm.get("template"),
        "os": os_label(vm.get("template")),
        "vlan": vm.get("vlan"),
        "octet": vm.get("ip_last_octet"),
        "ram": vm.get("ram_gb"),
        "cpus": vm.get("cpus"),
        "kind": kind,
        "tags": tags,
    }


def missing_templates(cfg, states):
    """[(template, state)] for templates the config needs that aren't built yet."""
    return [(t, states.get(t)) for t in templates_needed(cfg) if states.get(t) != "ready"]


def build_time_estimate(cfg, states):
    """Rough minutes to build this lab: one-off template builds (sequential) plus deploy.
    Heuristics tuned for a single node; returns a dict the UI formats into a range."""
    # Templates that still need building. Ludus builds them one at a time.
    tmpl_min = 0
    for t in templates_needed(cfg):
        if states.get(t) == "ready":
            continue
        fam = os_family(t)
        tmpl_min += 55 if fam == "windows" else (20 if "desktop" in str(t) else 12)

    deploy_min = 4  # router + range plumbing
    for vm in vms(cfg):
        names = " ".join(str(r) for r in role_names(vm))
        if is_tool(vm):
            deploy_min += (25 if "ghosts" in names else
                           18 if ("elastic" in names or "wazuh" in names) else
                           20 if "dfir_tools" in names else 10)
            continue
        fam = os_family(vm.get("template"))
        vm_min = 12 if fam == "windows" else 6
        role = (vm.get("domain") or {}).get("role")
        if role in ("primary-dc", "alt-dc"):
            vm_min += 10   # dcpromo + reboots
        elif role == "member":
            vm_min += 4    # domain join
        if "badblood" in names:
            vm_min += 8    # thousands of AD objects
        if "homelab_minecraft" in names:
            vm_min += 8    # install Java + download the server jar
        elif "homelab_jellyfin" in names:
            vm_min += 6    # add the repo + install Jellyfin
        elif "homelab_samba" in names:
            vm_min += 3    # install and configure Samba
        # Chocolatey app installs are the slow extra, ~1 min each, capped.
        win = vm.get("windows") or {}
        if isinstance(win, dict):
            vm_min += min(8, len(win.get("chocolatey_packages") or []))
        deploy_min += vm_min

    total = tmpl_min + deploy_min
    return {"templates_min": tmpl_min, "deploy_min": deploy_min, "total_min": total,
            "low_min": int(total * 0.8), "high_min": int(round(total * 1.3))}


def missing_roles(cfg):
    """Roles the config uses that aren't installed. Collection roles (a.b.c) are skipped."""
    needed = set()
    for vm in vms(cfg):
        for role in vm.get("roles") or []:
            if isinstance(role, str):
                needed.add(role)
            else:
                needed.add(role.get("name"))
                needed.update(d.get("role") for d in role.get("depends_on") or [])
    needed = {n for n in needed if n and n.count(".") < 2}
    try:
        rows = ludus_json("ansible", "role", "list") or []
    except LudusError:
        return []
    # Ludus lists a source's roles under its namespace ("homelab.homelab_minecraft"),
    # while blueprints reference the bare role name ("homelab_minecraft"). Accept
    # either form, so an installed namespaced role satisfies a bare requirement.
    installed = set()
    for r in rows:
        name = r.get("name")
        if name:
            installed.add(name)
            installed.add(name.rsplit(".", 1)[-1])
    return sorted(needed - installed)


def bsl_source_id():
    """The ID of the Bad Sector Labs source, which carries the extra OS templates."""
    try:
        ids = [s.get("sourceID", "") for s in ludus_json("source", "list") or []]
    except LudusError:
        ids = []
    if "ludus-source-bsl" in ids:
        return "ludus-source-bsl"
    return next((sid for sid in ids if sid.endswith("ludus-source-bsl")), "ludus-source-bsl")


def start_template_builds(names, states=None):
    """Download (if needed) and start building templates. Returns a message."""
    states = template_states() if states is None else states
    names = [n for n in names if states.get(n) != "ready"]
    unknown = [n for n in names if n not in states]
    to_build = [n for n in names if states.get(n) != "building"]
    if not to_build:
        return "Those templates are already building."
    if unknown:
        ludus("source", "add", bsl_source_id(), "--templates", ",".join(unknown))
    ludus("templates", "build", "-n", ",".join(to_build))
    return f"Started building {len(to_build)} template{'s' if len(to_build) != 1 else ''}."


def list_ranges():
    try:
        data = ludus_json("range", "list", "all")
    except LudusError:
        return []
    return data if isinstance(data, list) else []


def unique_range_id(base, existing):
    if base not in existing:
        return base
    n = 2
    while f"{base}-{n}" in existing:
        n += 1
    return f"{base}-{n}"


def save_config(range_id, cfg, header=""):
    os.makedirs(STATE_DIR, exist_ok=True)
    path = os.path.join(STATE_DIR, f"{range_id}.yml")
    fd, tmp = tempfile.mkstemp(dir=STATE_DIR, prefix=f".{range_id}.", suffix=".yml")
    with os.fdopen(fd, "w") as fh:
        if header:
            fh.write(header.rstrip() + "\n")
        fh.write(dump_yaml(cfg))
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)
    return path


def load_lab_config(range_id):
    """The config a lab was built with: the saved copy, or Ludus's current one."""
    path = os.path.join(STATE_DIR, f"{range_id}.yml")
    try:
        with open(path) as fh:
            return load_yaml(fh.read())
    except OSError:
        return load_yaml(ludus("range", "config", "get", "-r", range_id))


class LaunchError(Exception):
    def __init__(self, stage, message, config_path=None):
        super().__init__(message)
        self.stage = stage
        self.config_path = config_path


def launch(cfg, range_id, name, blueprint_id, profile, remove_on_reject=True):
    """Create the range, hand Ludus the config and start the deploy. Raises LaunchError."""
    saved = save_config(range_id, cfg, f"# Generated by lab {VERSION} from blueprint {blueprint_id} "
                                       f"(RAM profile: {profile})")
    try:
        ludus("range", "create", "-r", range_id, "-n", name,
              "-d", f"From blueprint {blueprint_id} via the lab launcher")
    except LudusError as exc:
        raise LaunchError("create", f"Couldn't create the lab: {exc}", saved)
    try:
        ludus("range", "config", "set", "-f", saved, "-r", range_id)
    except LudusError as exc:
        if remove_on_reject:
            try:
                ludus("range", "rm-range", "-r", range_id, "--no-prompt", "--force")
            except LudusError:
                pass
        raise LaunchError("config", f"Ludus rejected the configuration: {exc}", saved)
    try:
        ludus("range", "deploy", "-r", range_id)
    except LudusError as exc:
        raise LaunchError("deploy", f"Couldn't start the build: {exc}", saved)
    return saved
