#!/usr/bin/env bash
# setup.sh - connect the lab launcher, the web GUI and the lab catalogue to Ludus.
#
# Run as root on the Proxmox node, AFTER the official Ludus installer has
# finished (ludus-install-status says "Ludus install completed successfully").
# Safe to re-run: it updates the catalogue, launcher and web GUI in place.
#
#   ./setup.sh        interactive
#   ./setup.sh -y     answer yes to everything (starts the template builds)

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR=/opt/ludus-homelab
KEY_DIR=/root/.config/ludus-lab
KEY_FILE="$KEY_DIR/env"
SOURCE_ID=homelab
WEB_USER=ludus-lab
WEB_HOME=/var/lib/ludus-lab
WEB_ETC=/etc/ludus-lab
WEB_PORT=8443
WEB_UNIT=/etc/systemd/system/ludus-lab-web.service
LUDUS_DIR=/opt/ludus
# Roles for the security tooling add-ons.
GALAXY_ROLES=(
  badsectorlabs.ludus_elastic_container
  badsectorlabs.ludus_elastic_agent
  geerlingguy.docker
  aleemladha.wazuh_server_install
  aleemladha.ludus_wazuh_agent
)
# Galaxy collections needed by the add-on roles (GHOSTS server).
GALAXY_COLLECTIONS=(
  community.docker
  community.grafana
)
# Community roles that aren't on Ansible Galaxy, pinned to a reviewed commit:
# name|github repo|commit
VENDORED_ROLES=(
  "ludus_velociraptor_server|fmurer/ludus_velociraptor_server|2214411d2baa6ef45cae8185aaf1aaced541b8c3"
  "ludus_velociraptor_client|fmurer/ludus_velociraptor_client|4c2bb2fc7384d71f8196b64c012002464803a360"
  "ludus_ghosts_server|frack113/ludus_ghosts_server|8d3734280b541252d280b28d4ec289e095bc634a"
  "ludus_ghosts_client|jasonmull/ludus_ghosts_client|7a73428e5192f78549ad6dd21588c8207034b7be"
  "ludus_badblood|curi0usJack/ludus_badblood|3dbe06459baf65570d3f4a368f8ce09f34774860"
)
NEEDED_TEMPLATES=(
  debian-13-x64-server-template
  debian-12-x64-server-template
  kali-x64-desktop-template
  win2022-server-x64-template
  win11-22h2-x64-enterprise-template
)

ASSUME_YES=false
case "${1:-}" in
  -y|--yes) ASSUME_YES=true ;;
  "") ;;
  *) echo "usage: $0 [-y]"; exit 2 ;;
esac

say()  { printf '\033[36m›\033[0m %s\n' "$*"; }
ok()   { printf '\033[32m✓\033[0m %s\n' "$*"; }
warn() { printf '\033[33m!\033[0m %s\n' "$*"; }
die()  { printf '\033[31m✗\033[0m %s\n' "$*" >&2; exit 1; }

# Never fail silently: with `set -e` an unguarded error would just drop back to
# the prompt. This reports where it stopped so a half-finished run is obvious.
trap 'rc=$?; printf "\033[31m✗\033[0m setup stopped at line %s (exit %s): %s\n   Re-run ./setup.sh. If it keeps stopping here, send me this line.\n" "$LINENO" "$rc" "$BASH_COMMAND" >&2' ERR

confirm() {
  local answer
  $ASSUME_YES && return 0
  read -r -p "  $1 [Y/n] " answer </dev/tty || return 1
  [[ -z "$answer" || "$answer" =~ ^[Yy] ]]
}

# The ludus CLI can exit 0 even when the server refused a request; it reports
# that on stderr as [ERROR] or [FATAL]. Treat those as failures.
lud() {
  local errfile rc=0
  errfile="$(mktemp)"
  ludus "$@" 2>"$errfile" || rc=$?
  if (( rc != 0 )) || grep -qE '\[(ERROR|FATAL)\]' "$errfile"; then
    grep -v 'Waiting for server' "$errfile" >&2 || true
    rm -f "$errfile"
    return 1
  fi
  grep -v 'Waiting for server' "$errfile" >&2 || true
  rm -f "$errfile"
}

key_works() {
  LUDUS_API_KEY="$1" ludus templates list --json 2>/dev/null | grep -q '\['
}

# Install a Galaxy role or collection, idempotently. Ludus prints
# "[ERROR] already installed" (and exits non-zero) when it's already there,
# which is success for us, not a failure - so classify on the message and
# never let it trip `set -e`. Always returns 0.
galaxy_add() {  # $1 = role | collection, $2 = name
  local kind="$1" name="$2" out rc=0
  out="$(ludus ansible "$kind" add "$name" --global 2>&1)" || rc=$?
  if grep -qiE 'already installed|installed successfully|successfully installed|added successfully' <<<"$out"; then
    return 0
  fi
  if (( rc != 0 )) || grep -qiE '\[(error|fatal)\]' <<<"$out"; then
    warn "Couldn't install $kind $name right now - re-run ./setup.sh later to retry."
  fi
  return 0
}

# --------------------------------------------------------------------------
say "Checking the node"
[[ $EUID -eq 0 ]] || die "Run this as root on the Proxmox node."
command -v ludus >/dev/null || die "Ludus isn't installed yet - do README step 2 first."
[[ -f "$LUDUS_DIR/install/.stage-3-complete" ]] \
  || die "The Ludus install hasn't finished. Watch it with: ludus-install-status"
systemctl is-active --quiet ludus || die "The Ludus service isn't running. Check: systemctl status ludus"
for f in lab lab-update fix-kali lib/labcore.py web/server.py web/static/index.html web/static/app.js source/source.yml source/blueprints/ad-basic/range-config.yml source/blueprints/linux-net/range-config.yml source/blueprints/sandbox/range-config.yml \
         source/blueprints/service-minecraft/range-config.yml source/blueprints/service-jellyfin/range-config.yml source/blueprints/service-samba/range-config.yml \
         source/ansible/roles/homelab_sysmon/tasks/main.yml source/ansible/roles/homelab_dfir_tools/tasks/main.yml \
         source/ansible/roles/homelab_wazuh_agent_linux/tasks/main.yml \
         source/ansible/roles/homelab_minecraft/tasks/main.yml source/ansible/roles/homelab_jellyfin/tasks/main.yml source/ansible/roles/homelab_samba/tasks/main.yml; do
  [[ -e "$HERE/$f" ]] || die "Missing $f next to setup.sh - unpack the whole ludus-homelab folder and run it from there."
done
# Every blueprint dir needs a blueprint.yml manifest - Ludus silently skips a
# blueprint that only has a range-config.yml, so it would never appear.
for bp in "$HERE"/source/blueprints/*/; do
  [[ -f "${bp}blueprint.yml" ]] || die "Blueprint $(basename "$bp") is missing blueprint.yml - it won't register with Ludus."
done
if ! python3 -c 'import yaml' 2>/dev/null; then
  say "Installing python3-yaml"
  apt-get install -y -qq python3-yaml >/dev/null
fi
ok "Ludus is installed and running"

# --------------------------------------------------------------------------
say "Setting up the API key the launcher uses"
API_KEY=""
if [[ -n "${LUDUS_API_KEY:-}" ]] && key_works "$LUDUS_API_KEY"; then
  API_KEY="$LUDUS_API_KEY"
elif [[ -f "$KEY_FILE" ]]; then
  saved_key="$(sed -n "s/^LUDUS_API_KEY=//p" "$KEY_FILE" | tr -d "'\"")"
  if [[ -n "$saved_key" ]] && key_works "$saved_key"; then
    API_KEY="$saved_key"
  fi
fi
if [[ -z "$API_KEY" ]]; then
  root_key_file="$LUDUS_DIR/install/root-api-key"
  admin_file="$LUDUS_DIR/install/initial-admin-userid"
  [[ -f "$root_key_file" && -f "$admin_file" ]] \
    || die "Can't find the Ludus admin details. Run ludus-install-status, then: LUDUS_API_KEY='<key>' ./setup.sh"
  admin_id="$(tr -d '\n' < "$admin_file")"
  warn "Creating a new API key for your Ludus admin ($admin_id). Any key ludus-install-status showed earlier stops working."
  API_KEY="$(LUDUS_API_KEY="$(tr -d '\n' < "$root_key_file")" \
    ludus --user "$admin_id" user apikey --no-prompt --value 2>/dev/null | tr -d '\r\n' || true)"
  key_works "$API_KEY" || die "Couldn't create a working API key. Run ludus-install-status and re-run with LUDUS_API_KEY set."
fi
install -d -m 700 "$KEY_DIR"
( umask 077; printf "LUDUS_API_KEY='%s'\n" "$API_KEY" > "$KEY_FILE" )
export LUDUS_API_KEY="$API_KEY"
ok "API key saved to $KEY_FILE (root-only)"

# --------------------------------------------------------------------------
say "Installing the launcher and the lab catalogue"
install -d -m 755 "$APP_DIR"
rm -rf "${APP_DIR:?}/source" "${APP_DIR:?}/lib" "${APP_DIR:?}/web"
cp -r "$HERE/source" "$HERE/lib" "$HERE/web" "$APP_DIR/"
find "$APP_DIR/lib" "$APP_DIR/web" -name '__pycache__' -prune -exec rm -rf {} +
chmod -R u=rwX,go=rX "$APP_DIR/lib" "$APP_DIR/web"
if [[ -f "$HERE/README.md" ]]; then install -m 644 "$HERE/README.md" "$APP_DIR/README.md"; fi
install -m 755 "$HERE/lab" /usr/local/bin/lab
# One-command self-update: bake in the directory this was run from.
sed "s|__LAB_SRC_DIR__|$HERE|g" "$HERE/lab-update" > /usr/local/bin/lab-update
chmod 755 /usr/local/bin/lab-update
install -m 755 "$HERE/fix-kali" /usr/local/bin/fix-kali
ok "Terminal menu installed - run it with: lab"

# Record where updates come from so the GUI can show them.
LAB_UPDATE_DIR="$HERE"; LAB_UPDATE_GIT=0; LAB_UPDATE_REPO=""
if [[ -d "$HERE/.git" ]] && command -v git >/dev/null 2>&1; then
  LAB_UPDATE_GIT=1
  LAB_UPDATE_REPO="$(git -C "$HERE" remote get-url origin 2>/dev/null || true)"
  ok "Updates: run 'sudo lab-update' any time to pull the latest and re-apply"
else
  say "Updates: follow the README 'Updating' steps once, then 'sudo lab-update' does it all"
fi

# Velociraptor roles: download each pinned commit once, keep a copy in
# $APP_DIR/vendor, and ship it inside the catalogue so Ludus installs it.
install -d -m 755 "$APP_DIR/vendor" "$APP_DIR/source/ansible/roles"
for entry in "${VENDORED_ROLES[@]}"; do
  IFS='|' read -r role_name role_repo role_commit <<< "$entry"
  cache="$APP_DIR/vendor/$role_name-$role_commit"
  if [[ ! -d "$cache" ]]; then
    tmp="$(mktemp -d)"
    if curl -fsSL -o "$tmp.tar.gz" "https://github.com/$role_repo/archive/$role_commit.tar.gz" 2>/dev/null \
       && tar xzf "$tmp.tar.gz" -C "$tmp" --strip-components=1 \
       && [[ -f "$tmp/tasks/main.yml" ]]; then
      mv "$tmp" "$cache"
      rm -f "$tmp.tar.gz"
    else
      rm -rf "$tmp" "$tmp.tar.gz"
      warn "Couldn't download $role_name from GitHub; the Velociraptor add-on won't work until setup.sh is re-run with internet access."
      continue
    fi
  fi
  rm -rf "$APP_DIR/source/ansible/roles/$role_name"
  cp -r "$cache" "$APP_DIR/source/ansible/roles/$role_name"
done

if ludus source list --json 2>/dev/null | grep -qE "\"sourceID\": *\"$SOURCE_ID\""; then
  lud source update "$SOURCE_ID" -d "$APP_DIR/source" --global
  lud source add "$SOURCE_ID" --all --global
else
  lud source add -d "$APP_DIR/source" --id "$SOURCE_ID" --all --global
fi
ok "Catalogue installed: labs (AD, GOAD, Linux net, sandbox), services (Minecraft, Jellyfin, Samba) and the add-on roles"

say "Installing the SIEM roles from Ansible Galaxy"
installed_roles="$(ludus ansible role list --json 2>/dev/null | python3 -c '
import json, sys
try:
    print("\n".join(r.get("name", "") for r in json.load(sys.stdin) or []))
except ValueError:
    pass
' || true)"
for role in "${GALAXY_ROLES[@]}"; do
  if grep -qxF "$role" <<< "$installed_roles"; then
    continue
  fi
  galaxy_add role "$role"
done
installed_collections="$(ludus ansible collection list --json 2>/dev/null | python3 -c '
import json, sys
try:
    data = json.load(sys.stdin) or []
except ValueError:
    data = []
# Ludus has reported the name under different keys across versions; accept any.
for c in data:
    if isinstance(c, str):
        print(c)
    elif isinstance(c, dict):
        print(c.get("name") or c.get("collection") or c.get("fqcn") or c.get("id") or "")
' || true)"
for coll in "${GALAXY_COLLECTIONS[@]}"; do
  if grep -qxF "$coll" <<< "$installed_collections"; then
    continue
  fi
  galaxy_add collection "$coll"
done
ok "Add-on roles ready (Elastic, Wazuh, Velociraptor, GHOSTS, BadBlood)"

if ludus blueprint list --json 2>/dev/null | grep -qE '"blueprintID": *"[^"]*/goad"'; then
  ok "GOAD blueprint already installed"
else
  say "Installing the GOAD blueprint from the official Bad Sector Labs source"
  ludus source sync ludus-source-bsl >/dev/null 2>&1 || true
  if lud source add ludus-source-bsl --blueprints goad --global \
     || lud source add https://github.com/badsectorlabs/ludus-source-bsl --blueprints goad --global; then
    ok "GOAD blueprint installed"
  else
    warn "Couldn't install GOAD right now (the source may still be syncing). Re-run ./setup.sh in a few minutes."
  fi
fi

# --------------------------------------------------------------------------
say "Setting up the web GUI"
if ! id "$WEB_USER" >/dev/null 2>&1; then
  useradd --system --home-dir "$WEB_HOME" --no-create-home --shell /usr/sbin/nologin "$WEB_USER"
fi
install -d -m 750 -o "$WEB_USER" -g "$WEB_USER" "$WEB_HOME"
install -d -m 755 -o "$WEB_USER" -g "$WEB_USER" "$WEB_HOME/configs"
install -d -m 700 -o "$WEB_USER" -g "$WEB_USER" "$WEB_HOME/tmp"
# Configs saved by earlier versions of the terminal menu, so their tool logins show up in the GUI.
if [[ -d /root/.local/share/ludus-lab ]]; then
  for f in /root/.local/share/ludus-lab/*.yml; do
    [[ -e "$f" ]] || continue          # no match: the glob stays literal, skip it
    dest="$WEB_HOME/configs/${f##*/}"
    [[ -e "$dest" ]] || cp "$f" "$dest"
  done
fi
install -d -m 750 -o root -g "$WEB_USER" "$WEB_ETC" "$WEB_ETC/tls"

PVE_NODE="$(sed -n 's/^proxmox_node: *//p' "$LUDUS_DIR/config.yml" 2>/dev/null | tr -d "'\" " || true)"
[[ -n "$PVE_NODE" ]] || PVE_NODE="$(hostname)"
PVE_POOL="$(sed -n 's/^proxmox_vm_storage_pool: *//p' "$LUDUS_DIR/config.yml" 2>/dev/null | tr -d "'\" " || true)"
[[ -n "$PVE_POOL" ]] || PVE_POOL="local"

# A scoped Proxmox token lets the GUI show live stats and reset VM passwords.
# It is privilege-separated (--privsep 1) with a custom read + guest-agent role,
# so it can read stats and reset guest passwords but NOT create/destroy/reconfigure anything.
TOKEN_ID="root@pam!labbuilder"; TOKEN_SECRET=""
pve_token_works() {
  [[ -n "$1" ]] || return 1
  curl -ksf -m 10 -H "Authorization: PVEAPIToken=$TOKEN_ID=$1" \
    "https://127.0.0.1:8006/api2/json/version" >/dev/null 2>&1
}
if ! command -v pveum >/dev/null 2>&1; then
  warn "pveum not found, so live monitoring/password-reset can't be set up. The rest of the GUI still works."
else
  OLD_SECRET="$(sed -n "s/^PROXMOX_TOKEN_SECRET=//p" "$WEB_ETC/env" 2>/dev/null | tr -d "'\"" || true)"
  pveum role add LabBuilder -privs "Sys.Audit Datastore.Audit VM.Audit VM.Monitor" 2>/dev/null \
    || pveum role modify LabBuilder -privs "Sys.Audit Datastore.Audit VM.Audit VM.Monitor" >/dev/null 2>&1 || true
  if pve_token_works "$OLD_SECRET"; then
    TOKEN_SECRET="$OLD_SECRET"
  else
    pveum user token remove root@pam labbuilder >/dev/null 2>&1 || true
    TOKEN_SECRET="$(pveum user token add root@pam labbuilder --privsep 1 --output-format json 2>/dev/null \
      | python3 -c 'import json,sys; print(json.load(sys.stdin).get("value",""))' 2>/dev/null || true)"
  fi
  pveum acl modify / --tokens "$TOKEN_ID" --roles LabBuilder >/dev/null 2>&1 || true
  if pve_token_works "$TOKEN_SECRET"; then
    ok "Live monitoring token ready (read-only + guest agent, privilege-separated)"
  else
    warn "Couldn't set up the monitoring token. The GUI works but won't show live stats or reset passwords."
    TOKEN_SECRET=""
  fi
fi

( umask 027
  {
    printf "LUDUS_API_KEY='%s'\n" "$API_KEY"
    printf "PROXMOX_NODE='%s'\n" "$PVE_NODE"
    printf "PROXMOX_VM_POOL='%s'\n" "$PVE_POOL"
    printf "LAB_UPDATE_DIR='%s'\n" "$LAB_UPDATE_DIR"
    printf "LAB_UPDATE_GIT='%s'\n" "$LAB_UPDATE_GIT"
    printf "LAB_UPDATE_REPO='%s'\n" "$LAB_UPDATE_REPO"
    if [[ -n "$TOKEN_SECRET" ]]; then
      printf "PROXMOX_TOKEN_ID='%s'\n" "$TOKEN_ID"
      printf "PROXMOX_TOKEN_SECRET='%s'\n" "$TOKEN_SECRET"
    fi
  } > "$WEB_ETC/env"
)
chown root:"$WEB_USER" "$WEB_ETC/env"; chmod 640 "$WEB_ETC/env"

# --- HTTPS certificate, signed by a small local certificate authority --------
# The GUI is served over HTTPS. A self-signed cert makes every browser show a
# warning; instead we run a tiny local CA (created once, kept across runs) and
# have it sign the server's certificate. Install the CA on a phone or laptop
# once - the GUI offers it for download at /ca.crt - and the warning is gone for
# good on that device. The CA's private key never leaves the node and is never
# served.
NODE_IP="$(sed -n 's/^proxmox_local_ip: *//p' "$LUDUS_DIR/config.yml" 2>/dev/null | tr -d "'\" " || true)"
[[ -n "$NODE_IP" ]] || NODE_IP="$(hostname -I | awk '{print $1}')"
CA_KEY="$WEB_ETC/tls/ca.key"; CA_CRT="$WEB_ETC/tls/ca.crt"
SRV_KEY="$WEB_ETC/tls/key.pem"; SRV_CRT="$WEB_ETC/tls/cert.pem"

# 1. The certificate authority: create once, then leave it untouched so that
#    copies already installed on your devices keep working.
if [[ ! -s "$CA_KEY" || ! -s "$CA_CRT" ]]; then
  openssl req -x509 -newkey rsa:4096 -nodes -days 3650 -sha256 \
    -subj "/O=Lab builder/CN=Lab builder local CA ($(hostname))" \
    -addext "basicConstraints=critical,CA:TRUE,pathlen:0" \
    -addext "keyUsage=critical,keyCertSign,cRLSign" \
    -keyout "$CA_KEY" -out "$CA_CRT" >/dev/null 2>&1 \
    || die "Couldn't create the local certificate authority (is openssl installed?)."
fi

# 2. The server certificate: (re)issue if it's missing, if this node's IP isn't
#    in it, or if it's no longer signed by the current CA. Apple devices only
#    trust leaf certs valid for 825 days or fewer and require a SAN plus the
#    serverAuth usage, so the cert is built to those rules.
if [[ ! -s "$SRV_CRT" || ! -s "$SRV_KEY" ]] \
   || ! openssl x509 -in "$SRV_CRT" -noout -ext subjectAltName 2>/dev/null | grep -q "IP Address:$NODE_IP" \
   || ! openssl verify -CAfile "$CA_CRT" "$SRV_CRT" >/dev/null 2>&1; then
  SRV_EXT="$WEB_ETC/tls/server-ext.cnf"
  {
    printf '[ v3_srv ]\n'
    printf 'basicConstraints=CA:FALSE\n'
    printf 'keyUsage=critical,digitalSignature,keyEncipherment\n'
    printf 'extendedKeyUsage=serverAuth\n'
    printf 'subjectAltName=IP:%s,IP:127.0.0.1,DNS:%s,DNS:localhost\n' "$NODE_IP" "$(hostname)"
  } > "$SRV_EXT"
  { openssl req -newkey rsa:2048 -nodes -keyout "$SRV_KEY.tmp" -out "$WEB_ETC/tls/server.csr" \
      -subj "/CN=$NODE_IP" >/dev/null 2>&1 \
    && openssl x509 -req -in "$WEB_ETC/tls/server.csr" -CA "$CA_CRT" -CAkey "$CA_KEY" -CAcreateserial \
      -days 825 -sha256 -extfile "$SRV_EXT" -extensions v3_srv -out "$SRV_CRT.tmp" >/dev/null 2>&1; } \
    || die "Couldn't create the GUI's HTTPS certificate (is openssl installed?)."
  mv -f "$SRV_KEY.tmp" "$SRV_KEY"; mv -f "$SRV_CRT.tmp" "$SRV_CRT"
  rm -f "$WEB_ETC/tls/server.csr" "$SRV_EXT"
fi

chown root:root "$CA_KEY"                     # CA private key: root only, the web service never touches it
chown root:"$WEB_USER" "$CA_CRT" "$SRV_KEY" "$SRV_CRT"
chmod 600 "$CA_KEY"                           # crown jewels: readable by root alone
chmod 644 "$CA_CRT" "$SRV_CRT"                # the two certificates are public
chmod 640 "$SRV_KEY"                          # server key: readable by the web service group only

if ss -ltnH "sport = :$WEB_PORT" 2>/dev/null | grep -q . && ! systemctl is-active --quiet ludus-lab-web; then
  warn "Something else is already listening on port $WEB_PORT. Edit LAB_WEB_PORT in $WEB_UNIT and re-run."
fi

cat > "$WEB_UNIT" <<UNIT
[Unit]
Description=Lab builder web GUI for Ludus
After=network-online.target ludus.service
Wants=network-online.target

[Service]
Type=simple
User=$WEB_USER
Group=$WEB_USER
EnvironmentFile=$WEB_ETC/env
Environment=HOME=$WEB_HOME
Environment=LAB_STATE_DIR=$WEB_HOME/configs
Environment=LAB_WEB_TMP=$WEB_HOME/tmp
Environment=LAB_WEB_PORT=$WEB_PORT
Environment=LAB_WEB_CERT=$WEB_ETC/tls/cert.pem
Environment=LAB_WEB_KEY=$WEB_ETC/tls/key.pem
Environment=LAB_WEB_CA=$WEB_ETC/tls/ca.crt
ExecStart=/usr/bin/python3 -I -u $APP_DIR/web/server.py
Restart=on-failure
RestartSec=5
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=yes
PrivateTmp=yes
ReadWritePaths=$WEB_HOME
ProtectKernelTunables=yes
ProtectKernelModules=yes
ProtectControlGroups=yes
RestrictSUIDSGID=yes
LockPersonality=yes

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload || true
systemctl enable --quiet ludus-lab-web || true
systemctl restart ludus-lab-web || true   # the is-active check below gives a friendly message if it didn't start
sleep 3
if systemctl is-active --quiet ludus-lab-web; then
  ok "Web GUI running at https://$NODE_IP:$WEB_PORT"
else
  warn "The web GUI didn't start. See: journalctl -u ludus-lab-web -n 30"
fi

# Let plain `ludus` commands in a root shell find the API key too.
if ! grep -qs 'ludus-lab/env' /root/.bashrc; then
  echo '[ -r /root/.config/ludus-lab/env ] && . /root/.config/ludus-lab/env && export LUDUS_API_KEY' >> /root/.bashrc
fi

# --------------------------------------------------------------------------
# Let the web GUI trigger an update with one click - without sudo, which Proxmox
# nodes usually don't have installed (everything there runs as root). The
# unprivileged web service only ever creates one file, inside its own data dir
# (the single path its systemd sandbox lets it write to). A root-owned systemd
# .path unit watches for that file and starts the update unit, which runs the
# root-owned lab-update script in its own cgroup (so it survives the web service
# restart the update performs). The web user can do nothing else - it can't
# change the unit or the script, only ask for the one update to run.
rm -f /etc/sudoers.d/ludus-lab-update 2>/dev/null || true   # drop any rule from older versions
LAB_UPDATE_WEB=0
LAB_UPDATE_TRIGGER="$WEB_HOME/update-request"   # inside ReadWritePaths= of the web unit
if command -v systemctl >/dev/null 2>&1; then
  SYSCTL_BIN="$(command -v systemctl)"
  # Give the triggered update the same HOME and PATH the admin had when they
  # ran setup by hand, so git, ludus and ansible-galaxy resolve the same way
  # under systemd's otherwise-minimal environment.
  UNIT_HOME="${HOME:-/root}"
  UNIT_PATH="${PATH:-/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin}"
  cat > /etc/systemd/system/ludus-lab-update.service <<UNIT
[Unit]
Description=Pull and apply the latest lab builder version
After=network-online.target

[Service]
Type=oneshot
# the update runs git + ansible-galaxy + setup.sh, which can take several
# minutes - disable the start timeout so systemd never kills it partway
TimeoutStartSec=infinity
Environment=HOME=${UNIT_HOME}
Environment=PATH=${UNIT_PATH}
ExecStartPre=-/bin/rm -f ${LAB_UPDATE_TRIGGER}
ExecStart=/usr/local/bin/lab-update
# Always bounce the web GUI at the end - even when there was nothing new to
# pull - so the page gets a reliable "done" signal to reload on.
ExecStartPost=-${SYSCTL_BIN} restart ludus-lab-web
UNIT

  cat > /etc/systemd/system/ludus-lab-update.path <<UNIT
[Unit]
Description=Watch for a lab builder update request from the web GUI

[Path]
PathExists=${LAB_UPDATE_TRIGGER}
Unit=ludus-lab-update.service

[Install]
WantedBy=paths.target
UNIT

  systemctl daemon-reload || true
  rm -f "$LAB_UPDATE_TRIGGER" 2>/dev/null || true   # clear any stale request
  if systemctl enable --now ludus-lab-update.path >/dev/null 2>&1; then
    LAB_UPDATE_WEB=1
    ok "Web GUI can trigger updates (no sudo needed; scoped to the update unit only)"
  else
    warn "Couldn't set up one-click updates; the GUI will show the update command instead."
  fi
fi

# The update command to show in the GUI: use sudo only if it's actually present
# (Proxmox is usually operated as root with no sudo package installed).
if command -v sudo >/dev/null 2>&1; then
  LAB_UPDATE_CMD="sudo lab-update"
else
  LAB_UPDATE_CMD="lab-update"
fi

# Record the web-update settings for the GUI (the env file is rewritten each run).
{ printf "LAB_UPDATE_WEB='%s'\n" "$LAB_UPDATE_WEB"
  printf "LAB_UPDATE_TRIGGER='%s'\n" "$LAB_UPDATE_TRIGGER"
  printf "LAB_UPDATE_CMD='%s'\n" "$LAB_UPDATE_CMD"; } >> "$WEB_ETC/env"
systemctl restart ludus-lab-web >/dev/null 2>&1 || true   # pick up the new env

# --------------------------------------------------------------------------
say "Checking VM templates"
mapfile -t missing < <(ludus templates list --json 2>/dev/null | python3 -c '
import json, sys
wanted = sys.argv[1:]
try:
    listing = json.load(sys.stdin)
except ValueError:
    listing = []
state = {t.get("name"): t for t in listing}
for name in wanted:
    t = state.get(name, {})
    if not t.get("built") and t.get("status") != "building":
        print(name)
' "${NEEDED_TEMPLATES[@]}")

if (( ${#missing[@]} == 0 )); then
  ok "All templates are built or already building"
else
  warn "These templates still need building:"
  printf '    %s\n' "${missing[@]}"
  say "Ludus downloads each ISO and installs the OS from scratch, once. Expect a few hours"
  say "in total (Windows is the slow part). The node stays usable while it works."
  if printf '%s\n' "${missing[@]}" | grep -q kali; then
    say "Pointing the Kali template at the current release first (Kali rotates its images)..."
    if fix-kali >/dev/null 2>&1; then
      ok "Kali template refreshed to the current release"
    else
      warn "Couldn't auto-refresh the Kali ISO. If its build fails, run: sudo fix-kali"
    fi
  fi
  if confirm "Start building them now?"; then
    lud templates build -n "$(IFS=,; echo "${missing[*]}")"
    ok "Template builds started. Follow them with: ludus templates logs -f"
  else
    say "Skipped. Start them later from the launcher's Templates menu."
  fi
fi

ADMIN_LOGIN="$(ludus users list all --json 2>/dev/null | python3 -c '
import json, sys
try:
    print(next(u["proxmoxUsername"] for u in json.load(sys.stdin) if u.get("isAdmin") and u.get("proxmoxUsername")))
except (ValueError, StopIteration):
    pass
' || true)"
echo
ok "All done."
say "In a browser:   https://$NODE_IP:$WEB_PORT"
say "                Sign in as ${ADMIN_LOGIN:-your Ludus admin} (or root) with that account's Proxmox password."
say "                Your browser will warn about the self-signed certificate once; that's expected."
say "In a terminal:  lab"
