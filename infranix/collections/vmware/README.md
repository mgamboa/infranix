# `vmware` collection — Discovery (capability: `scan`)

Scans the live hypervisor and builds an **Inventory** (VMs, datastores,
networks, ISO images) that every other collection consumes. It picks the
scanner automatically: `mock` when the hypervisor is `mock`/`local`, otherwise
the real `govc`-based ESXi scanner.

## Options

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

## Example

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
