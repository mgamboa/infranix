# InfraNix — Declaration Manual

InfraNix is a **declarative orchestrator**: you describe *what* you want
(desired state) in a YAML file, and the application does the work: provisions
the infrastructure (Terraform), configures it (Ansible) and ensures the images
(Image Manager) — with a Safety Gate that **never destroys or causes downtime
without explicit permission** in the YAML itself.

---

## 1. General structure of the YAML file

A manifest is made of top-level sections. All except `version`, `project` and
`hypervisor` are optional.

```yaml
version: 1
project: my-project          # project name
hypervisor: esxi             # vcenter | esxi | proxmox | kvm | mock

scan_before_apply: true      # (optional) scan current state before applying

collections: [...]           # (optional) collections the core needs (auto-installed)
safety:                      # (optional) security policies
  destroy: false

images: [...]                # (optional) operating-system catalog
networks: [...]              # (optional) networks
servers: [...]               # (optional) virtual machines
routers: [...]               # (optional) virtual routers
load_balancers: [...]        # (optional) load balancers
```

---

## 2. Root fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `version` | int | yes | Format version (currently 1) |
| `project` | string | yes | Project name |
| `hypervisor` | string | yes | `vcenter`, `esxi`, `proxmox`, `kvm`, `mock` |
| `scan_before_apply` | bool | no (def `true`) | Scan current state before deciding |
| `collections` | list | no | Collections to auto-install/enable (see §2.5) |
| `safety` | object | no | Security policies (see §10) |

### 2.5 Section `collections`

Declares the collections (plugins) the core needs to run this manifest. The
core behaves like `ansible-galaxy`: anything missing is installed automatically
before execution.

```yaml
collections:
  - name: vmware            # builtin — comes with the core
    source: builtin
  - name: proxmox           # external — pip install infra-collection-proxmox
    source: pip
    version: "1.2.0"        # optional
  - name: mycoll           # external/offline — install from a local tar.gz
    source: archive
    path: dist/infra-collection-mycoll-0.1.0.tar.gz
```

- `source: builtin` — the collection ships with the core (`infra collection list`
  shows them); it is simply (re)enabled.
- `source: pip` — the core runs `pip install infra-collection-<name>` if missing.
- `source: archive` — the core installs from a local `.tar.gz` (for environments
  without internet / blocked pip).

---

## 3. Section `safety` (security)

Controls risky operations. **By default everything is conservative.**

```yaml
safety:
  destroy: false             # true = allow destruction (if each resource
                             #   also declares action: destroy)
  allow_downtime: false      # true = accept service reboots/stops
  confirm_destructive: true  # true = require extra confirmation before deleting
  scan_before_apply: true    # true = re-scan before applying
```

> ⚠️ **Golden rule:** the application NEVER destroys anything unless
> `safety.destroy` is `true` AND the resource uses `action: destroy`.
> Otherwise the operation is blocked and an alternative is proposed.

---

## 4. Section `images` (operating systems / templates)

Declares which operating systems the project may use and how to obtain them.

```yaml
images:
  - name: rhel-9.5              # identifier referenced by servers
    distro: rhel                # rhel | rocky | ubuntu | debian | centos ...
    version: "9.5"              # OS version
    source:
      type: iso                 # iso | ova | cloudimage | template
      url: https://...          # (optional) explicit download source
    build:
      builder: direct           # direct | packer
      cloud_init: true          # prepare with cloud-init (recommended)
      autounattend: false       # Windows only
```

**Automatic behaviour (Image Manager):**
- If the image (distro+version) **already exists** in the datastore → it is used
  as-is.
- If it **does not** → the application **downloads it from the official mirror**
  (rocky, ubuntu, debian, centos) and **uploads it to the datastore** of the
  hypervisor automatically.
- For **RHEL** (requires subscription) it will use your internal ISO if you have
  one.
- With `builder: packer` it will additionally build a cloneable template but
  requires the ISO to be in the local cache (`infra image ensure` first).

---

## 5. Section `servers` (virtual machines)

Describes each VM: hardware, network, operating system and which roles to
configure.

```yaml
servers:
  - name: web-prod-01                    # unique name
    image: rhel-9.5                      # references an image (images section)
    cpu: 4                               # vCPUs
    mem: 8192                            # RAM in MB
    disk: 100                            # disk in GB
    network:
      - name: VM Network                 # network name (portgroup)
        ip: ${SERVER_IP}                 # IP/CIDR — dynamic value
        gateway: ${GATEWAY}              # gateway — dynamic value
        dns: [${DNS}]                    # DNS servers
    roles:                               # what Ansible must configure
      - webserver
      - monitoring-agent
    action: create                       # create | update | destroy
```

### Lifecycle (`action`)

| Value | Effect | Requires |
|-------|--------|----------|
| `create` | Creates the VM (if missing) | — |
| `update` | Updates existing resources | — |
| `destroy` | **Deletes** the VM | `safety.destroy: true` |

> A `destroy` without `safety.destroy: true` is **blocked** by the application.

---

## 6. Section `networks` (networks)

Defines/uses networks in the hypervisor.

```yaml
networks:
  - name: prod-net
    type: portgroup              # portgroup | dvswitch | vlan
    vlan: 100
    subnet: ${SUBNET}
    gateway: ${GATEWAY}
    dhcp: false
```

| Field | Type | Description |
|-------|------|-------------|
| `name` | string | Network/portgroup name |
| `type` | string | `portgroup`, `dvswitch`, `vlan` |
| `vlan` | int | (optional) VLAN ID |
| `subnet` | string | (optional) CIDR subnet |
| `gateway` | string | (optional) gateway |
| `dhcp` | bool | Whether it uses DHCP |

---

## 7. Section `routers` (virtual routers)

Adds routing when there is no managed L3 switch.

```yaml
routers:
  - name: edge-router
    image: vyos-1.5                # router/image to use
    interfaces:
      - { network: wan-net }       # interface towards WAN
      - { network: prod-net }      # interface towards internal network
    routes:
      - { dest: 0.0.0.0/0, via: wan-gw }
    nat: true                      # enable NAT
```

---

## 8. Section `load_balancers` (load balancers)

Publishes services behind a balancer.

```yaml
load_balancers:
  - name: lb-web
    type: haproxy                  # haproxy | nginx | nsx | aws-elb | f5
    listeners:
      - port: 443
        protocol: https
        backend: [web-prod-01, web-prod-02]   # backend servers
        health: /healthz           # healthcheck path
```

---

## 9. Referring between sections

- `server.image` → name of an image in the `images` section.
- `server.network[].name` → name of a network in `networks` (or a hypervisor
  portgroup).
- `lb.listeners[].backend` → names of `servers`.
- `router.interfaces[].network` → names of `networks`.

The application validates that references exist and resolves dependencies
(networks before VMs, images before VMs, etc.).

### 9.5 Dynamic values `${VAR}`

Never hardcode IPs, gateways, passwords or hostnames in the manifest. Use
`${VAR}` placeholders resolved from `~/.infranix/.env` (or the environment):

```yaml
servers:
  - name: web-01
    network:
      - name: VM Network
        ip: ${WEB1_IP}
        gateway: ${GATEWAY}
        dns: [${DNS}]
```

```bash
# ~/.infranix/.env
GATEWAY=10.0.0.1
DNS=10.0.0.1
WEB1_IP=10.0.0.21/24
```

If a `${VAR}` is not defined it is left as-is (so a private template stays
unresolved rather than leaking a real address).

---

## 10. Quick reference: complete example

```yaml
version: 1
project: webstack
hypervisor: esxi

collections:
  - name: vmware
    source: builtin
  - name: terraform
    source: builtin
  - name: ansible
    source: builtin

safety:
  destroy: false
  allow_downtime: false

images:
  - name: rocky-9
    distro: rocky
    version: "9.5"

networks:
  - name: VM Network
    type: portgroup
    subnet: ${SUBNET}
    gateway: ${GATEWAY}

servers:
  - name: web-prod-01
    image: rocky-9
    cpu: 4
    mem: 8192
    disk: 60
    network:
      - { name: VM Network, ip: ${WEB1_IP}, gateway: ${GATEWAY}, dns: [${DNS}] }
    roles: [webserver]
    action: create

load_balancers:
  - name: lb-web
    type: haproxy
    listeners:
      - { port: 443, protocol: https, backend: [web-prod-01], health: /healthz }
```

---

## 11. Application commands

| Command | Description |
|---------|-------------|
| `infra run -f infra.yaml` | Validates, scans, plans and applies in one go (autonomous mode) |
| `infra plan -f infra.yaml` | Shows the plan without executing |
| `infra apply -f infra.yaml` | Executes the plan (re-validates security) |
| `infra scan` | Scans and shows the current state |
| `infra collection list` | Lists discovered collections (builtin + external) |
| `infra collection requirements -f infra.yaml` | Installs/enables what the manifest declares |
| `infra collection init <name>` | Scaffolds a new collection |
| `infra collection install-from-archive <tgz> <name>` | Offline install from tar.gz |
| `infra image ensure -f infra.yaml` | Ensures images (downloads missing ones) |
| `infra image build -f infra.yaml` | Builds cloneable templates via Packer collection |
| `infra destroy -f infra.yaml --yes` | Destroys (requires opt-in) |
| `infra init` | Creates `~/.infranix/.env` for credentials |

### Credentials
Hypervisor credentials live in `~/.infranix/.env` (never in the repo):

```
INFRA_HYPERVISOR=esxi
INFRA_HOST=your-esxi-ip        # IP/hostname of your ESXi/vCenter
INFRA_USER=root
INFRA_PASSWORD=your_password   # your credentials
INFRA_INSECURE=1
```

### Key principle
- **Dry-run by default** — `plan` executes nothing; `apply` re-validates the
  Safety Gate before acting and refuses destructive changes without `--yes` and
  `safety.destroy: true`.
- **Never commit credentials** — secrets and real IPs go in `~/.infranix/.env`,
  never in a manifest or the repository.
