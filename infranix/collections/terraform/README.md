# `terraform` collection — Provisioning (capability: `provision`)

Generates Terraform `vmware/vsphere` resources (`.tf` files under
`out/terraform`) and, when `apply=true`, runs `terraform init` +
`terraform apply` against the hypervisor. Each `server` in the manifest
becomes a clone of the image template.

## Options

Manifest fields consumed (`servers[].`):

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

Reads from `.env` (through Terraform `tfvars`): `INFRA_HOST`, `INFRA_USER`,
`INFRA_PASSWORD`, `INFRA_INSECURE`. Binary requirement: `terraform` on `PATH`.

## Example

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
