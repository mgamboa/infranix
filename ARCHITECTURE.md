# InfraNix — Declarative Infrastructure Orchestrator

> A declarative system that interprets an infrastructure YAML manifest and,
> self-sufficiently and safely, provisions it (Terraform), configures it
> (Ansible), discovers it (govc/vSphere API) and maintains it over time.

## 1. Design philosophy

### 1.1 Declarative first
The user describes the **desired state**, never the steps. InfraNix computes the
**current state** (scan), compares it against the desired one and applies only
the *delta*.

### 1.2 Safety = core (not an add-on)
Three non-negotiable principles:

1. **The system is the last arbiter.** If an operation can cause data loss or
   downtime of an existing service, it is NOT executed — it stops and proposes
   an alternative.

2. **Destruction with explicit opt-in.** Every destructive operation (delete
   VM, remove networks, drop load balancer, destroy storage) requires the
   manifest to declare it explicitly:
   ```yaml
   safety:
     destroy: true        # global opt-in
   # or per resource:
   servers:
     - name: old-vm
       action: destroy
   ```
   Without `destroy: true` (global or per resource), the operation is blocked.

3. **Dry-run by default.** The default command is `plan` (equivalent to
   `terraform plan`): it shows the full change plan and executes nothing.
   `apply` requires interactive confirmation if there are destructive changes.

### 1.3 Self-sufficient but audited
InfraNix makes decisions (version resolution, IPs, mirror selection), but every
decision is recorded in an auditable **change plan** before it runs.

## 2. Technology stack

| Layer            | Technology                           | Role                                        |
|------------------|--------------------------------------|---------------------------------------------|
| CLI/Orchestrator | Python                              | Interprets manifest, orchestrates backends  |
| Provisioning     | Terraform                           | Creates/updates resources (VM, networks, LB)|
| Configuration    | Ansible                             | Configures OS/products on the VMs           |
| Image Mgmt       | Packer + govc + Python               | Builds/lists OS templates                    |
| VMware API       | govc (CLI) + PyVmomi (SDK)           | Scan, direct vSphere/ESXi manipulation      |
| API/Network      | vSphere vDS + NSX + Ansible network  | Switches, routers, LBs                      |
| State            | Terraform state + state file         | Persistence and state migration             |

## 3. Layered architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    LAYER 0: INTERFACES                      │
│   CLI (infra plan/apply/scan/destroy)   REST API     Web UI │
└──────────────────────────┬──────────────────────────────────┘
                           │
┌──────────────────────────▼──────────────────────────────────┐
│               LAYER 1: ORCHESTRATOR (thin core)             │
│   Manifest Parser · Planner (state diff) · SafetyGate       │
│   Collection Registry (resolve/auto-install) · Executor     │
└───────┬──────────────────────┬──────────────────┬───────────┘
        │ collection           │ collection       │ collection
        │ SCAN                 │ PROVISION        │ CONFIGURE
┌───────▼────────┐   ┌─────────▼────────┐  ┌──────▼─────────┐
│ LAYER 2: SCAN  │   │ LAYER 2: PROV    │  │ LAYER 2: CONFIG│
│  vmware(govc)  │   │  terraform       │  │  ansible       │
│  mock          │   │  vsphere/esxi    │  │  inventory     │
│                │   │  (IMAGE/BUILD too)│ │  roles/products│
└───────┬────────┘   └─────────┬────────┘  └──────┬─────────┘
        │                      │                  │
┌───────▼──────────────────────▼──────────────────▼─────────┐
│               LAYER 3: PLATFORM ADAPTERS                   │
│   vSphere/vCenter · ESXi · KVM/libvirt · Proxmox · Network │
└─────────────────────────────────────────────────────────────┘
```

The core (Layer 1) keeps the manifest schema, the diff planner and the Safety
Gate. Every *capability* (scan, provision, configure, image, build) is delivered
by a **collection** — an independent Python package implementing
`PluginProvider` and discovered via `infranix.collections` entry points.

## 4. The declarative manifest (format)

```yaml
# infra.yaml
version: 1
project: infra-nix-demo
hypervisor: esxi            # vcenter | esxi | proxmox | kvm | mock

# Respect existing environments; scan before acting
scan_before_apply: true

# COLLECTIONS the core needs (auto-installed before running, like ansible-galaxy)
collections:
  - name: vmware
    source: builtin
  - name: terraform
    source: builtin
  # external/offline example:
  # - name: proxmox
  #   source: archive
  #   path: dist/infra-collection-proxmox-0.1.0.tar.gz

# SECURITY POLICIES
safety:
  destroy: false            # global opt-in for destructive operations
  allow_downtime: false
  confirm_destructive: true

# IMAGES / TEMPLATES (auto-download if missing)
images:
  - name: rhel-9.4
    distro: rhel
    version: "9.4"
    source:
      type: iso                   # iso | ova | cloudimage | template
      url: https://mirror.example/rhel-9.4.iso
    build:                        # how to turn it into a usable template
      builder: packer
      autounattend: false
      cloud_init: true

# SERVERS (VMs)
servers:
  - name: web-prod-01
    image: rhel-9.4
    cpu: 4
    mem: 8192
    disk: 100
    network:
      - name: prod-net
        ip: ${SERVER_IP}          # values are dynamic — set in ~/.infranix/.env
        gateway: ${GATEWAY}
        dns: ['${DNS}']
    roles:            # what Ansible must install/configure
      - webserver
      - monitoring-agent
    action: create    # create | update | destroy (destroy requires safety)

# NETWORKS
networks:
  - name: prod-net
    type: portgroup    # portgroup | dvswitch | vlan
    vlan: 100
    subnet: ${SUBNET}
    gateway: ${GATEWAY}
    dhcp: false

# VIRTUAL ROUTERS
routers:
  - name: edge-router
    image: vyos-1.5
    interfaces:
      - network: wan-net
      - network: prod-net
    routes:
      - { dest: 0.0.0.0/0, via: wan-gw }
    nat: true

# LOAD BALANCERS
load_balancers:
  - name: lb-web
    type: haproxy           # haproxy | nginx | nsx | aws-elb | f5
    listeners:
      - port: 443
        protocol: https
        backend: web-prod-01, web-prod-02
        health: /healthz
```

## 5. Execution flow

```
infra plan -f infra.yaml
        │
        ▼
┌─ ORCHESTRATOR ────────────────────────────┐
│ 0. Resolve collections (auto-install if any missing)
│ 1. Validate manifest + schema (Pydantic)  │
└──────────────┬─────────────────────────────┘
               ▼
┌─ SCAN collection ─────────────────────────┐
│ 2. Current discovery (govc):
│    - list VMs, networks, templates, datastores
│    - compare with what is declared        │
└──────────────┬─────────────────────────────┘
               ▼
┌─ PLANNER (core) ──────────────────────────┐
│ 3. Compute DIFF (desired - current)        │
│ 4. Classify each change:                  │
│    create ✓ · update ✓ · destroy ✋(gated) │
│ 5. SAFETY GATE: if destroy w/o opt-in,    │
│    block and propose alternatives         │
└──────────────┬─────────────────────────────┘
               ▼
┌─ PROVISION/CONFIGURE collections ─────────┐
│ 6. Generate Terraform + Ansible           │
│ 7. (apply) run them, with images ensured  │
└────────────────────────────────────────────┘

infra apply -f infra.yaml        # executes after re-validating safety gate
infra scan                       # discovery only, no actions
infra destroy -f infra.yaml      # requires --yes + safety.destroy=true
```

## 6. Image Manager (auto-download of OS)

When the manifest asks for an OS whose image/template does NOT exist on the
hypervisor:

1. **Resolve source** — the system decides the official mirror (RHEL, Ubuntu
   cloud, Rocky, Debian, etc.) based on `distro` + `version`.
2. **Download** the ISO/OVA/cloud-image (reusable local cache).
3. **Upload to the datastore** (govc datastore.upload).
4. **Build template** — Packer (kickstart/cloud-init/autounattend) to leave it
   bootable and ready as a clone template.
5. **Register** in the local InfraNix catalog (to avoid repeating).

Cache: `~/.infranix/images/` with version metadata. Reused across projects and
avoids re-downloads.

## 7. Safety (Safety Gate) — detail

The Safety Gate is a cross-cutting component evaluated at `plan` and again
(stronger) at `apply`. Rules:

| Rule                | Behaviour |
|---------------------|-----------|
| `DestroyResource`   | Blocked unless `safety.destroy: true` (global) OR `action: destroy` + confirmation on the resource |
| `ExistingServiceDown` | Blocked unless `safety.allow_downtime: true` |
| `OverwriteConfig`   | Always asks for confirmation; records a backup |
| `ImageMissing`      | Does not block; triggers Image Manager (download/build) |
| `IPConflict`        | Blocks; proposes a free alternative IP |
| `MissingDependency` | Resolves topologically or blocks with a clear message |

The Safety Gate is **never** optional; it is the default layer.

## 8. Roadmap by phase

- **Phase 0 — Foundation**: project structure, Pydantic manifest schema, CLI
  (`plan`/`scan`), diff engine, Safety Gate. Standalone ESXi support.
- **Phase 1 — Provisioning**: Terraform generation (vsphere/esxi provider), VM
  creation, networks (portgroup/vDS), images.
- **Phase 2 — Configuration**: Ansible inventories, base roles (OS, products),
  post-provision execution.
- **Phase 3 — Image Manager**: auto-download + template build.
- **Phase 4 — Networks & LB**: virtual routers (VyOS/OPNsense), load balancers
  (haproxy/nginx/NSX), Ansible network switches.
- **Phase 5 — Multiple hypervisors**: vCenter, Proxmox, KVM/libvirt, cloud.
- **Phase 6 — UI/API**: REST API + web dashboard of state and plans.

## 9. Project status

- Engine language: **Python 3.11+**
- Core dependencies: `pydantic`, `click` (CLI), `jinja2` (templates), `PyYAML`.
  Adapters: `govc` (VMware), `terraform`, `ansible`, `packer` — each provided
  by its own collection.
