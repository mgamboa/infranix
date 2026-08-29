# InfraNix — Collections & Options Reference

InfraNix keeps a **thin core** and delivers every capability as a *collection*
(a plugin). This document explains, for each built-in collection:

- **What it does** (its capability).
- **Options** — the knobs you control (manifest fields, `ctx.extras`, and the
  `.env` / environment variables it reads).
- A **small example** at the bottom of each collection.

It also includes a **CLI options reference** for the application itself.

The five built-in collections (see `infra collection list`):

| Collection | Capability | Role in the pipeline |
|---|---|---|
| `vmware` | `scan` | discover current hypervisor state (govc / mock) |
| `image` | `image` | ensure images (ISO) are on the datastore |
| `terraform` | `provision` | generate + apply VMs via Terraform (vmware/vsphere) |
| `ansible` | `configure` | generate inventory + roles, configure the VMs |
| `packer` | `build` | build cloneable templates from an ISO |

The pipeline order is: **scan → plan → safety → image → provision → configure**.
Failures inside a collection stay confined there; the core keeps running and the
report tells you which collection failed.

---

## 1. `vmware` — Discovery (capability: `scan`)

Scans the live hypervisor and builds an **Inventory** (VMs, datastores,
networks, ISO images) that every other collection consumes. It picks the
scanner automatically: `mock` when the hypervisor is `mock`/`local`, otherwise
the real `govc`-based ESXi scanner.

### Options

Reads from `~/.infranix/.env` / environment (via `InfraConfig`):

| Env var | Default | Description |
|---|---|---|
| `INFRA_HYPERVISOR` | `esxi` | `vcenter`, `esxi`, `proxmox`, `kvm`, `mock` |
| `INFRA_HOST` | — | ESXi/vCenter IP or hostname |
| `INFRA_USER` | — | Login user (e.g. `root`) |
| `INFRA_PASSWORD` | — | Login password |
| `INFRA_INSECURE` | `1` | `1` = skip SSL verification |
| `INFRA_DATACENTER` | — | Datacenter path (optional) |

Environment / binary requirement: when hypervisor is **not** mock, the `govc`
binary must be on `PATH` (the collection fails cleanly otherwise).

### Example

```yaml
version: 1
project: demo
hypervisor: esxi
collections:
  - name: vmware
    source: builtin
```

```bash
# ~/.infranix/.env
INFRA_HYPERVISOR=esxi
INFRA_HOST=192.168.2.81
INFRA_USER=root
INFRA_PASSWORD=your_password
INFRA_INSECURE=1

# see the discovered state
infra scan
```

---

## 2. `image` — Image Manager (capability: `image`)

Ensures the manifest images (distro + version) are present on the datastore.
If an image is missing it downloads it from the official mirror (rocky, ubuntu,
debian, centos) and uploads it to the datastore; if it already exists it is
used as-is.

### Options

Manifest fields (the `images[].` block):

| Field | Default | Description |
|---|---|---|
| `name` | — | Identifier referenced by `servers[].image` |
| `distro` | — | `rhel`, `rocky`, `ubuntu`, `debian`, `centos`… |
| `version` | — | OS version (used for download URL + match) |
| `source.type` | `iso` | `iso`, `ova`, `cloudimage`, `template` |
| `source.url` | — | Explicit download source (optional) |
| `build.builder` | `direct` | `direct` (just ensure the ISO) or `packer` (also build a template) |

Reads from the environment (`ImageManager`):

| Env var | Default | Description |
|---|---|---|
| `INFRA_HOST` | — | Where to upload the ISO (ESXi) |
| `INFRA_USER` / `INFRA_PASSWORD` | — | Credentials to upload |
| `INFRA_DATASTORE` | — | Target datastore for the ISO |

`ctx.extras`: `iso_path` (used only by the CLI build path, not by ensure).

### Example

```yaml
version: 1
project: demo
hypervisor: esxi
collections:
  - name: image
    source: builtin
images:
  - name: rocky-9
    distro: rocky
    version: "9.5"
```

```bash
# ensure the ISO is on the datastore (downloads if missing)
infra image ensure -f infra.yaml
```

---

## 3. `terraform` — Provisioning (capability: `provision`)

Generates Terraform `vmware/vsphere` resources (`.tf` files under `out/terraform`)
and, when `apply=true`, runs `terraform init` + `terraform apply` against the
hypervisor. Each `server` in the manifest becomes a clone of the image template.

### Options

Manifest fields consumed (`servers[].`, `collections[].`):

| Field | Default | Description |
|---|---|---|
| `servers[].name` | — | VM name (unique) |
| `servers[].image` | — | Template/image to clone from |
| `servers[].cpu` | `2` | vCPUs |
| `servers[].mem` | `2048` | RAM in MB |
| `servers[].disk` | `20` | Disk in GB |
| `servers[].network[].` | — | portgroup, IP/CIDR, gateway, DNS |
| `servers[].action` | `create` | `create`, `update`, `destroy` |

`ctx.extras` (set by the orchestrator, not by you):

| Extra | Set to | Used for |
|---|---|---|
| `apply` | `true/false` | `true` → actually runs `terraform apply`; `false` → generate only |

Derived options (feed from `Planner`/inventory unless overridden):

| Option | Source | Description |
|---|---|---|
| datastore | `INFRA_DATASTORE` / inventory / fallback `delldatastore` | Datastore to place VMs |
| compute cluster | `extras["compute_cluster"]` / inventory | vSphere cluster path |

Reads from `.env` (through Terraform `tvfars`): `INFRA_HOST`, `INFRA_USER`,
`INFRA_PASSWORD`, `INFRA_INSECURE`. Binary requirement: `terraform` on `PATH`.

### Example

```bash
# 1) generate Terraform only (no resources touched)
infra run -f infra.yaml

# 2) generate AND apply (creates the VMs)
infra run -f infra.yaml --apply
```

```yaml
# infra.yaml (relevant part)
collections:
  - name: terraform
    source: builtin
servers:
  - name: web-prod-01
    image: rocky-9
    cpu: 4
    mem: 8192
    disk: 60
    network:
      - name: VM Network
        ip: ${WEB1_IP}
        gateway: ${GATEWAY}
        dns: ['${DNS}']
    action: create
```

---

## 4. `ansible` — Configuration (capability: `configure`)

Generates an Ansible inventory + roles from the manifest and, when
`apply=true`, runs `ansible-playbook` against the VMs. Role skeletons
(`webserver`, `postgres`, `monitoring-agent`, `wazuh-*`, `kubernetes`) are
written under `out/ansible/roles/<role>/tasks/main.yml` ready to extend.

### Options

Manifest fields consumed (`servers[].`):

| Field | Default | Description |
|---|---|---|
| `servers[].roles` | `[]` | List of Ansible roles to apply to the VM |
| `servers[].network[].ip` | — | IP used in the Ansible inventory (connection host) |

`ctx.extras` (set by the orchestrator):

| Extra | Set to | Used for |
|---|---|---|
| `apply` | `true/false` | `true` → actually runs `ansible-playbook`; `false` → generate only |

Reads from `.env` / environment (passed to the playbook as connection vars):
`INFRA_USER` (SSH user). Binary requirement: `ansible-playbook` on `PATH`.

> `destroy` is **unsupported** — Ansible has nothing persistent to remove;
> resources are destroyed by Terraform.

### Example

```bash
# 1) generate inventory + roles only
infra run -f infra.yaml

# 2) generate AND configure the VMs
infra run -f infra.yaml --apply
```

```yaml
collections:
  - name: ansible
    source: builtin
servers:
  - name: web-prod-01
    image: rocky-9
    roles: [webserver, monitoring-agent]
    network:
      - name: VM Network
        ip: ${WEB1_IP}
        gateway: ${GATEWAY}
```

Generated layout:

```
out/ansible/
  inventory/hosts.yml
  playbooks/site.yml
  roles/webserver/tasks/main.yml
  roles/monitoring-agent/tasks/main.yml
```

---

## 5. `packer` — Template Builder (capability: `build`)

Builds a cloneable VM template from a local ISO using HashiCorp Packer
(kickstart/preseed). It is invoked for images whose `build.builder` is `packer`.
The ISO must already be in the local cache (run `infra image ensure` first).

### Options

Manifest fields consumed (`images[].` with `build.builder: packer`):

| Field | Default | Description |
|---|---|---|
| `images[].name` | — | Image/template name produced |
| `images[].distro` | — | OS distro (selects kickstart/preseed) |
| `images[].version` | — | OS version (required — the collection errors without it) |
| `images[].build.builder` | `direct` | must be `packer` for this collection to act |
| `images[].build.cloud_init` | `false` | prepare with cloud-init (`true` recommended) |

`ctx.extras` / `ctx`:

| Option | Source | Description |
|---|---|---|
| `iso_path` | `ctx.iso_path` or `extras["iso_path"]` | Local ISO path (required) |
| `work_dir` | `ctx.work_dir` or `extras["work_dir"]` | Where Packer generates/builds (required) |

Reads from `.env`: `INFRA_HOST`, `INFRA_USER`, `INFRA_PASSWORD` (to write the
template to the hypervisor). Binary requirement: `packer` on `PATH`.

### Example

```yaml
collections:
  - name: packer
    source: builtin
images:
  - name: rocky-9
    distro: rocky
    version: "9.5"
    build:
      builder: packer
      cloud_init: true
```

```bash
# 1) ensure the ISO is cached locally
infra image ensure -f infra.yaml

# 2) build the cloneable template with Packer
infra image build -f infra.yaml
```

---

## 6. Application CLI options

The `infra` CLI is a Click application. Global entry points:

```bash
infra init                          # create ~/.infranix/.env template
infra scan                          # show current hypervisor state (read-only)
infra collection ...                # collection management (see below)
infra image ...                     # image/template management (see below)
```

### `infra plan`

Compute the manifest-vs-current diff, show the plan, validate the Safety Gate
and generate artifacts (Terraform + Ansible) **without executing anything**.

| Option | Default | Description |
|---|---|---|
| `-f`, `--file` | `infra.yaml` | Path to the YAML manifest |
| `-o`, `--out` | `out` | Terraform/Ansible output directory |

### `infra run`

The application itself: validate → scan → plan → Safety Gate → images →
provision → configure, and emit a report.

| Option | Default | Description |
|---|---|---|
| `-f`, `--file` | `infra.yaml` | Path to the YAML manifest |
| `-o`, `--out` | `out` | Artifact output directory |
| `--apply` | off (flag) | Actually run Terraform/Ansible (else dry-run) |
| `--report` | `text` | Report format: `text` or `markdown` |

### `infra apply`

Execute the plan. Re-validates the Safety Gate and refuses destructive changes
without `--yes` and `safety.destroy: true`.

| Option | Default | Description |
|---|---|---|
| `-f`, `--file` | `infra.yaml` | Path to the YAML manifest |
| `-o`, `--out` | `out` | Artifact output directory |
| `--yes` | off (flag) | Confirms destructive operations |
| `--skip-apply` | off (flag) | Generate artifacts but do NOT run TF/Ansible |

### `infra destroy`

Destroy resources declared with `action: destroy`. **Extremely careful.**

| Option | Default | Description |
|---|---|---|
| `-f`, `--file` | `infra.yaml` | Path to the YAML manifest |
| `--yes` | off (flag) | **Mandatory** confirmation to destroy |

Requires BOTH `--yes` and `safety.destroy: true`, otherwise it is blocked.

### `infra collection …`

| Command | Description |
|---|---|
| `list` | List discovered collections, capabilities & state (`✓`/`✗`) |
| `requirements -f infra.yaml` | Install/enable what the manifest declares (like `ansible-galaxy`) |
| `init <name> [-o .]` | Scaffold a new collection skeleton |
| `install-from-archive <tgz> <name>` | Offline install from a local tar.gz |
| `enable <name>` | Force-enable a collection (e.g. `packer`) |
| `disable <name>` | Disable without uninstalling |
| `install <pkg>` | `pip install` a collection from PyPI/GitHub |

### `infra image …`

| Command | Description |
|---|---|
| `ensure -f infra.yaml [--name X]` | Ensure images are on the datastore (download+upload missing) |
| `build -f infra.yaml [--name X]` | Build cloneable templates with Packer from the cached ISO |

Use `--name` to target a single image instead of all manifest images.

---

## 7. Key principles

- **Dry-run by default** — `plan`/`run` without `--apply` execute nothing.
- **Safety Gate last resort** — `apply`/`destroy` re-validate and refuse
  destructive actions without the right flags + `safety.destroy: true`.
- **Never commit credentials** — everything sensitive goes in
  `~/.infranix/.env` and is git-ignored.
