# `ansible` collection — Configuration (capability: `configure`)

Generates an Ansible inventory + roles from the manifest and, when
`apply=true`, runs `ansible-playbook` against the VMs. Role skeletons
(`webserver`, `postgres`, `monitoring-agent`, `wazuh-*`, `kubernetes`) are
written under `out/ansible/roles/<role>/tasks/main.yml` ready to extend.

## Options

Manifest fields consumed (`servers[].`):

| Field | Default | Description |
|---|---|---|
| `servers[].roles` | `[]` | List of Ansible roles to apply to the VM |
| `servers[].network[].ip` | — | IP used in the Ansible inventory (connection host) |
| `servers[].vars` | `{}` | Ansible vars → written to `inventory/group_vars/<role>.yml` for each role |

`ctx.extras` (set by the orchestrator):

| Extra | Set to | Used for |
|---|---|---|
| `apply` | `true/false` | `true` → actually runs `ansible-playbook`; `false` → generate only |

Reads from `.env` / environment (passed to the playbook as connection vars):
`INFRA_USER` (SSH user). Binary requirements: `ansible-playbook` and
`ansible-galaxy` on `PATH`.

### Auto-installing Ansible Galaxy collections

This collection always installs the **baseline** Ansible collections that core
content relies on — `community.general` and `ansible.posix` — plus any extra
collection a server role needs (e.g. `redhat-satellite` needs
`redhat.satellite`). On every `apply` it:

1. Writes `out/ansible/galaxy/requirements.yml` starting with the baselines
   and adding any role-derived collections.
2. Installs them with `ansible-galaxy collection install -r requirements.yml`.

Role → collection mapping lives in `infranix/ansible_gen.py` (`ROLE_GALAXY`).
Only roles listed there add extra collections; plain roles (webserver, ...)
still get the baselines.

**Offline use:** drop collection `.tar.gz` files (e.g.
`redhat-satellite-5.11.0.tar.gz`) into this collection's local
`collections/` directory — i.e. `infranix/collections/ansible/collections/`.
They are installed directly from disk first (in addition to the baselines), so
no internet is required.

> `destroy` is **unsupported** — Ansible has nothing persistent to remove;
> resources are destroyed by Terraform.

## Example

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
  inventory/group_vars/<role>.yml
  playbooks/site.yml
  roles/webserver/tasks/main.yml
  roles/monitoring-agent/tasks/main.yml
```

## Example: RHEL 9.8 + Red Hat Satellite (subscription via `redhat_subscription`)

Register the host with RHSM and install Satellite. Credentials are kept in
`~/.infranix/.env` and injected through `servers[].vars` (→ `group_vars`), the
generated `redhat-satellite` role uses `community.general.redhat_subscription`
with `auto_attach`.

```yaml
# infra-satellite.yaml
version: 1
project: ${PROJECT_NAME}
hypervisor: ${INFRA_HYPERVISOR}

collections:
  - name: vmware
    source: builtin
  - name: image
    source: builtin
  - name: terraform
    source: builtin
  - name: ansible
    source: builtin

images:
  - name: rhel-9.8
    distro: rhel
    version: "9.8"
    source:
      type: iso
      url: ${RHEL98_ISO_URL}   # internal mirror / subscription ISO (RHEL has no free mirror)
    build:
      builder: direct

servers:
  - name: satellite-01
    image: rhel-9.8
    cpu: 8
    mem: 16384
    disk: 120
    network:
      - name: ${INFRA_NETWORK}
        ip: ${SATELLITE_IP}
        gateway: ${GATEWAY}
        dns: ['${DNS}']
    roles:
      - redhat-satellite
    vars:
      rhn_username: ${RHN_USER}      # → group_vars/redhat-satellite.yml
      rhn_password: ${RHN_PASSWORD}
      auto_attach: true
    action: create
```

```bash
# ~/.infranix/.env
RHN_USER=joe_user
RHN_PASSWORD=somepass
RHEL98_ISO_URL=https://your-internal-mirror/rhel-9.8.iso
SATELLITE_IP=192.168.2.30/24
GATEWAY=192.168.2.1
DNS=192.168.2.1

infra run -f infra-satellite.yaml            # validate + plan + generate artifacts
infra run -f infra-satellite.yaml --apply    # provision VM, subscribe + install Satellite
```

The generated `redhat-satellite` role (`out/ansible/roles/redhat-satellite/`
`tasks/main.yml`) contains the RHSM registration and the Satellite installer
steps, and reads `rhn_username` / `rhn_password` from the group vars.
