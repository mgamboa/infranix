# InfraNix — Declarative Infrastructure Orchestrator

InfraNix is a **declarative infrastructure orchestrator**. You describe *what*
you want in a **YAML** file, and the application does the work: provisions the
infrastructure (Terraform), configures it (Ansible), ensures images (Image
Manager), scans/discovery (govc) and builds templates (Packer) — all behind a
**Safety Gate** that never destroys without explicit opt-in.

```
┌──────────────────────────────────────────────────────────┐
│                    YOUR YAML MANIFEST                     │
│   (declaration: servers, networks, routers, lb, images)   │
└────────────────────────────┬─────────────────────────────┘
                             ▼
┌──────────────────────────────────────────────────────────┐
│                  infra run -f infra.yaml                 │
│   validate → scan → plan → Safety Gate → generate → apply
└────────────────────────────┬─────────────────────────────┘
                             ▼
   ┌──────────┬──────────────┼──────────────┬──────────┐
   ▼          ▼              ▼              ▼          ▼
 Terraform  Ansible    Image Manager    govc/API     Packer
 (provision) (configure) (images)      (discovery)   (build)
```

## Install

```bash
pip install -e .          # installs the global `infra` command
infra init                # creates ~/.infranix/.env for credentials
# edit ~/.infranix/.env with your hypervisor details
```

Requires: [Terraform](https://www.terraform.io) and `govc` on PATH (only for
the Terraform/discovery collections).

## Quick start

```bash
infra scan                       # show the current hypervisor state
infra plan -f infra.yaml         # show the change plan (does not execute)
infra run -f infra.yaml          # the app: validate, scan, plan, report
infra run -f infra.yaml --apply  # the app: also generate and run Terraform
infra image ensure -f infra.yaml # ensure images (download the missing ones)
```

## Collections (plugins)

InfraNix keeps a **thin core** and delivers every capability as a *collection*
— the same plugin model as Ansible collections:

| Collection | Capability | What it does |
|---|---|---|
| `vmware` | scan | discover the hypervisor via govc (or mock) |
| `image` | image | download/upload ISO images to the datastore |
| `terraform` | provision | generate + apply VMs via HashiCorp/Terraform (vmware/vsphere) |
| `ansible` | configure | generate inventory + roles and configure the VMs |
| `packer` | build | build cloneable templates from an ISO (kickstart/preseed) |

```bash
infra collection list                     # discover installed collections
infra collection requirements -f infra.yaml  # install anything the manifest needs
infra collection init mycollection        # scaffold a new collection
infra collection install-from-archive dist/my.tgz mycollection  # offline install
infra collection enable/disable terraform # force/avoid a collection
```

Collections are declarative: a manifest declares what it needs in the
`collections:` section, and the core auto-installs anything missing before
running (like `ansible-galaxy` installing `requirements.yml`).

## Dynamic values & credentials

**Never hardcode real IPs, hostnames or passwords.** Use `${VAR}` placeholders —
they resolve at runtime from the environment or `~/.infranix/.env`:

```yaml
hypervisor: ${INFRA_HYPERVISOR}
networks:
  - name: ${INFRA_NETWORK}
    subnet: ${SUBNET}
    gateway: ${GATEWAY}
servers:
  - name: web-01
    network:
      - ip: ${WEB_IP}
        dns: ['${DNS}']
```

Supported variables (set in `~/.infranix/.env`):

- `INFRA_HYPERVISOR` `INFRA_HOST` `INFRA_USER` `INFRA_PASSWORD`
  `INFRA_URL` `INFRA_INSECURE` `INFRA_NETWORK` `INFRA_DATASTORE` `INFRA_DATACENTER`
- Demo/network values: `SUBNET` `GATEWAY` `DNS` `WEB1_IP` `WEB2_IP` `WEB_IP`
  `ROUTER_IP` `POC_*` `PROJECT_NAME` `TARGET_*` `EXISTING_SERVER_*` `ROCKY_VERSION` `RHEL8_VERSION`

See `docs/MANUAL.md §9.5` for the full rules. The credentials file is
per-user, local to the machine and ignored by git — never committed.

## Documentation

- **[docs/MANUAL.md](docs/MANUAL.md)** — How to declare things in the YAML.
- **[docs/COLLECTIONS.md](docs/COLLECTIONS.md)** — Every collection's options + the full CLI reference, with examples.
- **[ARCHITECTURE.md](ARCHITECTURE.md)** — How the system is structured.

## Project structure

```
docs/MANUAL.md              # declaration manual (how to use the YAML)
examples/                   # example manifests
infranix/
  app.py                    # thin orchestrator (runs the YAML)
  config.py                 # credentials (~/.infranix/.env)
  models/                   # Pydantic schema for the manifest
  pluginbase.py             # collection protocol (PluginProvider)
  core/registry.py          # collection discovery + auto-install
  core/planner.py           # diff engine (desired vs current)
  core/safety.py            # Safety Gate
  collections/              # built-in collections (vmware, image, terraform, ansible, packer)
  terraform_gen.py          # .tf generator (vsphere provider)
  ansible_gen.py            # Ansible inventory + roles generator
  image_manager.py          # image download/upload
  cli/                      # CLI commands
```

## Safety

The **Safety Gate** is the heart of InfraNix. It never runs a destructive
operation without `safety.destroy: true` in the manifest. Destructive
operations additionally require an explicit `--yes`. See `docs/MANUAL.md §10`.
