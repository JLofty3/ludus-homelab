# Ludus homelab launcher

Pick a lab in your browser, choose OS versions, tick the SIEM and DFIR tooling
you want, and click **Build lab**. Ludus then builds the whole thing on your
Proxmox node: VMs, VLANs, firewall rules, the Active Directory domain and every
agent.

**Lab builder** (the web GUI) runs on the node at `https://<node-ip>:8443`:

- **Build a lab:** pick a lab, change its settings and OS versions, add tooling. A live
  *range map* shows each VLAN as a colour-coded cable with its VMs, the RAM it
  needs against what's free, and which templates still need building.
- **Your labs:** status, VMs and IPs, tool logins, power, snapshots and roll back,
  testing mode, rebuild, RDP files, the live build log, delete.
- **Templates:** what's built, build what isn't, add other OS versions ahead of time.
- **Connect:** download your WireGuard config and see the default logins.

Prefer a terminal? `lab` on the node does the same things as a menu, and both build
exactly the same labs.

## What's in the catalogue

| Lab | What you get | You can change | RAM (standard / lean) |
|---|---|---|---|
| **Single VM sandbox** | One machine to tinker with, behind the router so it has internet. No domain, no extra network | **Any Windows or Linux OS**, hostname, RAM, CPUs, and apps to pre-install | ~4 GB + what you give it |
| **Basic Active Directory** | A DC, Windows workstation(s), Kali on its own VLAN | Domain name, DC / workstation / member server **versions**, 1-4 workstations, add a member server, drop Kali, install Office | ~18 GB / ~13 GB |
| **GOAD** (Game of Active Directory) | The official Ludus GOAD blueprint: 3 domains across 2 forests, 5 Windows servers, Kali | Drop Kali, RAM profile (Windows versions stay on the tested Server 2022 build) | ~46 GB / ~26 GB |
| **Linux networking** | Web, app, database and jump servers across DMZ, internal and management VLANs, with the Ludus router as the firewall | **Distro and version**, 1-3 web and app servers, drop DB or jump box, segmented or open firewall, RAM per server | ~10 GB |

Every lab can also get any mix of the **security tooling add-ons** below.

RAM figures include the small router VM Ludus adds to every lab. Labs you aren't
using can be powered off from *Manage my labs* and they stop using RAM.

Any other blueprint you add to Ludus later shows up in the GUI and the menu too,
with the general options (add-ons, RAM profile, timezone).

## OS versions

You pick the version wherever it makes sense.

| | Choices |
|---|---|
| Windows Server (DCs, member servers) | 2022, 2025, 2019, 2016, 2012 R2 |
| Windows client (workstations, sandbox) | 11 22H2, 11 23H2, 11 24H2, 11 25H2, 10 22H2 |
| Linux servers | Debian 13, 12, 11 · Ubuntu 24.04, 22.04, 20.04 · Rocky Linux 9, 8 |
| Linux desktops (sandbox) | Ubuntu 24.04 desktop, Kali, REMnux |
| DFIR workstation | any of the Windows client or server versions |

Server 2022, Windows 11 22H2, Debian 12/13 and Kali are built by `setup.sh`.
Pick anything else and the lab builder offers to download its definition from the
Bad Sector Labs source and build it. That happens once per version (Windows takes an hour
or more), and after that it's instant for every lab. A Server 2025 DC
automatically gets the 2016 domain functional level it requires.

Older Windows (7, 8.1, XP) and other distros (Mint, Arch, Fedora) aren't in the
Bad Sector Labs library, so they need your own ISO and a custom template
definition — ask and I'll wire up a specific one you want.

## The sandbox and the app installer

The **Single VM sandbox** is the quickest way to get one box up: pick any OS
above, give it a hostname, RAM and CPUs, tick any apps, and build. No domain,
no network to think about — just a machine, ready in minutes.

The **app installer** pre-installs software during the build, before you ever
log in. It's a tick-list grouped by category (browsers, dev tools, security,
media, and so on), and it filters to whatever the chosen OS can take. On
Windows it uses Chocolatey (so almost anything is available); on Linux it only
offers packages that install cleanly from the distro's own repos. It's wired
into the sandbox, and the same mechanism is there for any lab.

Every build screen also shows an **estimated build time** before you commit,
worked out from which templates still need building, the number and type of
VMs, and any tooling.

## Security tooling add-ons

Pick any combination when you launch a lab. Tool servers go on their own VLAN
(normally `10.x.50.0/24`), agents go on every Windows and Linux VM in the lab
(never on Kali), and the lab's firewall is opened just enough for agents to
reach the tools.

| Add-on | Adds | Agents / clients go on | Login |
|---|---|---|---|
| **Elastic Security** (ELK) | Debian VM, 8 GB: Elasticsearch, Kibana, Fleet, Elastic Defend | Windows, Debian, Ubuntu, Rocky | Kibana `https://10.x.50.10:5601`, `elastic` / `elasticpassword` |
| **Wazuh** | Ubuntu 22.04 VM, 8 GB: Wazuh 4.12 all-in-one with SOCFortress rules | Windows, Debian, Ubuntu, Rocky | `https://10.x.50.11`, `admin` / `Wazuh-Admin-1` |
| **Velociraptor** | Debian VM, 4 GB | Windows, Debian, Ubuntu (no Rocky client exists) | `https://10.x.50.12:8889`, `admin` / `password` |
| **Sysmon logging** | nothing extra | Windows | Rich Windows logging, fed into whichever SIEM you add (see below) |
| **DFIR workstation** | Windows VM, 6 GB, your choice of version | - | RDP `10.x.50.20`, `localuser` / `password`; tools in `C:\Tools` |
| **GHOSTS user activity** | Ubuntu 24.04 VM, 6 GB: GHOSTS server + Grafana | Windows endpoints (never the DC) | GHOSTS UI `http://10.x.50.13:8080`; Grafana `http://10.x.50.13:3000`, `admin` / `admin` |
| **BadBlood** | nothing extra (runs on the DC) | - | - |

The last two are **realism** add-ons rather than tooling. **GHOSTS** drives
simulated user behaviour (browsing, documents, email, command-line activity)
on each Windows workstation from a Linux controller, so a SIEM has lifelike
noise to hunt in; it is heavy (~12 GB of Docker images and a bundled LLM), so
budget the RAM. **BadBlood** fills the domain with thousands of realistic
users, groups, OUs and ACLs the first time it runs — only offered on labs that
have an Active Directory domain. GHOSTS is only offered on labs that have a
Windows endpoint. The builder greys out either one when the lab can't take it
and says why.

**What's on the DFIR workstation:** Eric Zimmerman's full toolset via his
official `Get-ZimmermanTools` script (Timeline Explorer, Registry Explorer,
MFTECmd, EvtxECmd, PECmd and the rest) plus the .NET 9 runtime they need. It
also has Hayabusa, Chainsaw with Sigma rules, CyberChef, Volatility 3,
Sysinternals Suite, Wireshark, HxD, DB Browser for SQLite, 7-Zip and Notepad++.
Hayabusa, Chainsaw and CyberChef are fetched from their latest GitHub release
when the VM is built. `C:\Tools\README.txt` lists what installed. One tool
failing to download is reported but never breaks the lab.

KAPE and FTK Imager aren't included because their downloads need a
registration form. Grab them by hand once the workstation is up.

**Sysmon logging** is a toggle you can turn on or off per lab, and it routes
to whichever SIEM you picked:

- **With Elastic**, the Elastic agent installs and collects Sysmon itself on
  each Windows endpoint, so the events land in Kibana with no extra wiring.
- **With Wazuh**, every Windows endpoint gets the sysmon-modular config and the
  Wazuh agent forwards the Sysmon channel to the dashboard.
- **With both**, Elastic handles the Sysmon install (no double-install) and
  Wazuh also reads it.
- **With no SIEM**, the builder warns you: Sysmon still runs, but its events
  just sit in each machine's Windows event log with nothing collecting them.

So "enable/disable Sysmon" is simply ticking or un-ticking the add-on, and it
always feeds the SIEM you chose.

The exact login for each tool in a lab, with the real IP filled in, is on the lab's
page under **Your labs** (or *Manage my labs → Show security tool logins* in `lab`).

**Sizing on 64 GB**, examples:

| Combination | Standard | Lean |
|---|---|---|
| AD lab + Elastic | ~26 GB | ~19 GB |
| AD lab + Elastic + Velociraptor + Sysmon + DFIR workstation | ~36 GB | ~25 GB |
| AD lab + all five add-ons | ~44 GB | ~31 GB |
| GOAD + Elastic + Sysmon | ~54 GB | ~32 GB |
| Linux lab + Wazuh + Velociraptor | ~22 GB | ~18 GB |

To change the tool lists, edit `source/ansible/roles/homelab_dfir_tools/defaults/main.yml`
(Chocolatey packages, GitHub tools, pip packages) and re-run `./setup.sh`.

## 1. Before you start

- Proxmox VE 8 or 9 on the node, connected by **Ethernet** (Ludus doesn't support WiFi).
- At least 200 GB free on fast storage for templates, plus about 50 GB per lab.
- **Don't install Docker on the node itself** - it breaks Ludus networking.

## 2. Install Ludus on the node (about 15 minutes)

SSH to the node as root and run the official installer:

```bash
curl --proto '=https' --tlsv1.2 -sSf https://ludus.cloud/install | bash
```

Say **yes** when it offers to install the server. On an existing Proxmox node it
doesn't need a reboot. When it asks:

- **What pool will store VMs?** your VM storage: `local-lvm`, `local-zfs`, or `local` if that's the only one listed
- **What pool will store ISO files?** `local`
- **Format** keep the suggested one (`qcow2` for `local`, `raw` for LVM-thin or ZFS)
- **Public IP of this host** use the node's **LAN IP**. This is the WireGuard endpoint your laptop will connect to.
- **License key** leave as `community`
- **Node name** keep your existing node name; don't change it to `ludus`
- **Initial admin** your name, email, a short user ID (e.g. `JL`) and a password

Then wait for it to finish:

```bash
ludus-install-status
```

It's done when it says *Ludus install completed successfully* and prints your
admin credentials. If it instead says *sdn zone 'ludus' does not exist*, see
**Troubleshooting** below.

## 3. Copy this folder to the node and run setup

From your computer:

```bash
scp ludus-homelab.tar.gz root@<proxmox-ip>:
ssh root@<proxmox-ip>
tar xzf ludus-homelab.tar.gz
cd ludus-homelab
./setup.sh
```

`setup.sh` does the following:

1. Creates an API key for your Ludus admin and saves it to `/root/.config/ludus-lab/env` (root-only)
2. Installs the terminal menu as `/usr/local/bin/lab`
3. Registers the catalogue with Ludus as the `homelab` source (`homelab/ad-basic`, `homelab/linux-net`) along with the add-on roles
4. Installs the add-on roles. Elastic and Wazuh come from Ansible Galaxy. The two Velociraptor roles are downloaded from GitHub at a pinned commit and cached in `/opt/ludus-homelab/vendor`.
5. Installs the official GOAD blueprint from the Bad Sector Labs source
6. Sets up the web GUI as the `ludus-lab-web` service (details under **About the web GUI**)
7. Offers to start building the five base VM templates: Debian 12 and 13, Kali, Server 2022 and Windows 11

It finishes by printing the GUI's address and who to sign in as.

**The template builds are the long part**: a few hours, once. Ludus downloads
each ISO and installs the OS from scratch, and every lab reuses the result. You
can follow progress on the GUI's **Templates** page, or with `ludus templates logs -f`.

Re-running `setup.sh` is safe; do it after editing the catalogue.

## 4. Build a lab

Open `https://<node-ip>:8443` in your browser. It uses a self-signed certificate,
so accept the warning once, like you do for Proxmox. Sign in with your Ludus
admin's Proxmox username and password (`setup.sh` prints the name), or with `root`.

1. Pick a lab.
2. Change its settings and OS versions.
3. Tick any security tooling.
4. Choose Standard or Lean RAM and a short lab ID.

The range map updates as you go. If a template isn't built yet, the map says so
and offers a **Download and build** button. Once everything is ready, click
**Build lab**. You land on the lab's page, which follows the build log live.

The Basic AD and Linux labs take roughly 20-40 minutes. GOAD and labs with
several add-ons can take a few hours.

In a terminal, `lab` → **Launch a new lab** asks the same questions.

Each lab gets its own network, `10.<range number>.0.0/16`, and its own VLANs,
so labs never clash with each other or with your home network.

## 5. Connect to your labs

In the GUI, open **Connect** and click **Download WireGuard config**. Import the
file into the [WireGuard app](https://www.wireguard.com/install/) and connect.
(In `lab`, *How to connect* saves it to `/root/ludus-wireguard.conf` instead.) You
can then reach lab VMs directly:

- Windows: RDP to the VM's IP (the lab's page has an **RDP files** button with ready-made `.rdp` files)
- Linux: `ssh debian@<ip>`
- Kali desktop in a browser: `https://<kali-ip>:8444`

Default lab logins:

| Machine | Username | Password |
|---|---|---|
| Windows local admin | `localuser` | `password` |
| Domain admin | `<DOMAIN>\domainadmin` | `password` |
| Domain user | `<DOMAIN>\domainuser` | `password` |
| Debian | `debian` | `debian` |
| Kali | `kali` | `kali` (browser desktop: `password`) |
| Kibana | `elastic` | `elasticpassword` |
| Wazuh dashboard | `admin` | `Wazuh-Admin-1` |
| Velociraptor | `admin` | `password` |

These are deliberately weak. Keep the labs on their isolated networks and don't
forward ports to them from the internet.

## Day-to-day

From a lab's page under **Your labs** (or *Manage my labs* in `lab`) you can:

- Power a lab on or off
- Snapshot everything and roll back
- Switch **testing mode** on. Ludus snapshots the VMs and cuts the Windows machines off from the internet, and switching it off rolls them back.
- Re-run the build or delete the lab

Everything the GUI and `lab` do is an ordinary `ludus` command underneath, so you
can always drop to the CLI (`ludus --help`). The configs they generate are kept in
`/var/lib/ludus-lab/configs/<lab-id>.yml`.

## Adding your own labs

Copy one of the folders in `source/blueprints/`, change `blueprint.yml` (give it
a new `id`) and `range-config.yml`, then re-run `./setup.sh`. It appears in the
GUI and the menu straight away. The range config format is documented at
<https://docs.ludus.cloud/docs/configuration>.

## About the web GUI

- It runs as the `ludus-lab-web` systemd service, under its own `ludus-lab` user,
  with no shell and no root rights. It can only talk to Ludus through its API key
  in `/etc/ludus-lab/env`, which only root and that user can read.
- Sign-in is checked by Proxmox itself, so there's no extra password to manage.
  Only `root` and Ludus admins can sign in. Accounts with two-factor sign-in
  aren't supported. Five wrong passwords lock that address out for 10 minutes.
- HTTPS uses a self-signed certificate in `/etc/ludus-lab/tls/`. Sessions last 12 hours.
- It's meant for your home network, like the Proxmox UI. Don't port-forward
  8443 to the internet.
- Restart it with `systemctl restart ludus-lab-web`, and read its log with
  `journalctl -u ludus-lab-web`.
- It has a light/dark theme toggle (top-right, remembered per browser) and
  follows your system theme by default.
- The **Updates** page has a **Refresh catalogue** button (pulls the latest
  template and blueprint definitions) and a **Danger zone**: *Delete all labs*
  (every range) and *Reset the node* (every range plus every built template -
  a clean slate). Both need you to type a confirmation phrase, and both only
  touch what the lab builder made - never Proxmox itself or VMs you made
  outside it.
- The icons and the sign-in artwork are original line drawings generated in the
  page itself (no third-party logos, no stock photos, no external image or font
  requests), so the GUI loads fully offline and nothing phones home.

### Live monitoring and password reset

The top bar shows how much RAM and VM storage the node has free, and each lab's
page has a **Live stats** panel (CPU, memory, network and uptime per VM,
refreshed every 5 seconds) plus a **Reset** button on each VM to set a new
password if someone gets locked out — a local account on any VM, or a domain
account from a domain controller row.

These need a Proxmox API token, which `setup.sh` creates for you
(`root@pam!labbuilder`). It is **privilege-separated** and limited to a custom
`LabBuilder` role with only four privileges: `Sys.Audit`, `Datastore.Audit`,
`VM.Audit` and `VM.Monitor`. So the GUI can read stats and reset guest
passwords through the QEMU guest agent, but **cannot create, delete, move or
reconfigure VMs, storage or networks** — if the service were ever compromised,
the blast radius is read access plus guest-agent commands, not control of the
node.

Be aware of the trade-off this token does carry: `VM.Monitor` allows
guest-agent command execution on the node's VMs, which is what makes the
domain password reset work. That's acceptable for a single-admin homelab on a
trusted LAN behind WireGuard, which is exactly what this is for. If you'd
rather not have it, decline and the token simply won't be created: everything
else in the GUI still works, and the stats panel and reset buttons just don't
appear. To remove it later: `pveum user token remove root@pam labbuilder`,
then delete the `PROXMOX_TOKEN_*` lines from `/etc/ludus-lab/env` and restart
the service. Password resets are also only ever applied to VMs that Ludus
reports as part of one of your labs.

## Updating

New versions install over the old one in place — your labs, templates and the
monitoring token are untouched. There are two ways to get an update onto the node.

### One command, from Git (recommended)

Set this up once and every future update is a single command, with no file
copying. On the node:

```
apt-get install -y git
cd ~
git clone <your-repo-url> ludus-homelab      # how to make the repo is below
cd ludus-homelab
./setup.sh
```

`setup.sh` installs a `lab-update` command, so from then on updating is just:

```
sudo lab-update
```

It pulls the latest version and re-runs `setup.sh`, which is safe to run any
time and never touches running labs.

**One-click updates from the GUI.** The **Updates** page shows your current
version. Once the install is Git-based (set up as below), that page also gets
an **Update now** button — click it and the node pulls the latest and
re-applies it, then the page reloads on the new version. No SSH, no commands.

The button is wired up with a deliberately tight privilege boundary. The web
service itself runs unprivileged and can't touch the system. `setup.sh`
installs a one-line `sudoers` rule (syntax-checked with `visudo` before it's
written) that lets the web user start exactly one thing — the root
`ludus-lab-update.service` unit — and nothing else: no other units, no extra
arguments, no shell. That unit runs the same root-owned `lab-update` script
you'd run by hand. If the rule can't be installed for any reason, the page just
falls back to showing the `sudo lab-update` command. The **Updates** page also
has a box for sending feature requests.

**Making the repo** (one time, free): create an empty private repo on GitHub
(call it `ludus-homelab`). On your computer, in the folder you unpacked:

```
git init
git add .
git commit -m "Lab builder"
git branch -M main
git remote add origin https://github.com/<your-username>/ludus-homelab.git
git push -u origin main
```

Then `git clone` that URL on the node as shown above.

**Getting improvements into the repo.** For `lab-update` to pull a new version,
it has to land in your repo first. The smoothest way: connect your GitHub
account to Claude (claude.ai → Settings → Connectors), and improvements get
pushed straight to your repo — then you just run `sudo lab-update` on the node.
Otherwise you're handed the changed files, drop them into your computer's copy,
`git commit -am "update" && git push`, and run `sudo lab-update`.

### Without Git

If you'd rather not use Git, unpack the new version's folder over the old one on
the node (however you like — `scp`, the Proxmox shell's upload button, a USB
stick) and run `./setup.sh` again. The Git route above exists to save you
exactly that copying step.

## Optional: Ludus's own web UI

Ludus also has its own web interface for visual range design, but it's a Pro feature. Bad Sector Labs offers a free one-year,
non-commercial **Pro NFR** licence for it (apply at <https://ludus.cloud/pricing>).
Once you have a key, set `license_key` in `/opt/ludus/config.yml` and run
`systemctl restart ludus ludus-admin`. Everything set up here keeps working
alongside it. The catalogue appears there as blueprints.

## Troubleshooting

- **The Ludus install ends with "sdn zone 'ludus' does not exist"** - Ludus thought the node was part of a
  cluster, usually because `/etc/pve/nodes` still holds a folder from an old node name. Check `ls /etc/pve/nodes`.
  If there's an empty folder besides this node's, back it up and remove it, e.g.
  `cp -r /etc/pve/nodes/OLDNAME /root/ && rm -r /etc/pve/nodes/OLDNAME`. Then set `cluster_mode: false`
  in `/opt/ludus/config.yml` and run `systemctl restart ludus-admin && sleep 10 && systemctl restart ludus`,
  which retries creating your admin user.
- **The GUI page doesn't load** - check `systemctl status ludus-lab-web`. If you've turned on the Proxmox
  firewall, allow TCP 8443 from your network.
- **Can't sign in to the GUI** - use your Ludus admin's *Proxmox* username (e.g. `jloftus`, not `JL`) or
  `root`. After five wrong tries, wait 10 minutes.
- **"template not found or not built"** - the templates are still building. Check the **Templates** page.
- **A lab deploy failed** - on the lab's page, **Show build errors**, then **Rebuild**. Re-running is safe: Ludus re-applies the config and skips anything already done.
- **VMs get no IP / no internet** - see <https://docs.ludus.cloud/docs/troubleshooting/network>.
- **The launcher says it can't find an API key** - re-run `./setup.sh`.
- **The Kali template won't build / its ISO fails to download** - Kali only keeps the latest release on its
  mirror, so the ISO URL and checksum pinned in Ludus's template go stale and the download fails. Run
  `sudo fix-kali` on the node: it finds the current Kali release, rewrites the template to match (keeping a
  backup), and tells you to rebuild it. Re-run it any time Kali's build starts failing on the ISO again.
- **No live stats / the Reset buttons are missing** - the monitoring token isn't set up. Re-run `./setup.sh`
  (it needs `pveum`, so run it on the Proxmox node itself), then reload the page. If a reset says the guest
  agent isn't reachable, the VM needs to be powered on with the QEMU guest agent running.
- **"These roles aren't installed"** - re-run `./setup.sh` while the node has internet access. It installs the add-on roles.
- **Kibana, Wazuh or Velociraptor isn't up yet** - these servers are configured after the base VMs, so they come up near the end of the build. Check **Show build errors** if the build has finished.

## Credits

The add-ons use community roles: [badsectorlabs.ludus_elastic_container / _agent](https://github.com/badsectorlabs),
[aleemladha.wazuh_server_install / ludus_wazuh_agent](https://github.com/aleemladha) and
[fmurer's Velociraptor roles](https://github.com/fmurer) (GPLv3, pinned to a reviewed commit). The
`homelab_sysmon`, `homelab_dfir_tools` and `homelab_wazuh_agent_linux` roles in `source/ansible/roles` were
written for this package. The GUI uses the Barlow typeface (SIL Open Font License, see
`web/static/fonts/OFL-Barlow.txt`).
