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

`ctx.extras` (set by the orchestrator):

| Extra | Set to | Used for |
|---|---|---|
| `apply` | `true/false` | `true` → actually runs `ansible-playbook`; `false` → generate only |

Reads from `.env` / environment (passed to the playbook as connection vars):
`INFRA_USER` (SSH user). Binary requirements: `ansible-playbook` and
`ansible-galaxy` on `PATH`.

### Auto-installing Ansible Galaxy collections

When a server role needs content from Ansible Galaxy (e.g. `redhat-satellite`
needs the `redhat.satellite` collection), this collection:

1. Writes `out/ansible/galaxy/requirements.yml` listing those collections.
2. On `apply`, installs them with `ansible-galaxy collection install`.

Role → collection mapping lives in `infranix/ansible_gen.py` (`ROLE_GALAXY`).
Only roles listed there trigger an install; plain roles (webserver, ...) never do.

**Offline use:** drop collection `.tar.gz` files (e.g.
`redhat-satellite-5.11.0.tar.gz`) into this collection's local
`collections/` directory — i.e. `infranix/collections/ansible/collections/`.
They are installed directly from disk first, so no internet is required.

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
  playbooks/site.yml
  roles/webserver/tasks/main.yml
  roles/monitoring-agent/tasks/main.yml
```
