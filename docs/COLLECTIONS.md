# InfraNix — Collections & Options Reference

InfraNix keeps a **thin core** and delivers every capability as a *collection*
(a plugin). Every built-in collection ships its **own `README.md`** in its
source directory with its full options reference (manifest fields, `ctx.extras`,
and the `.env` / environment variables it reads) and a small **example** at the
bottom.

The five built-in collections (see `infra collection list`):

| Collection | Capability | Role in the pipeline | Options reference |
|---|---|---|---|
| `vmware` | `scan` | discover current hypervisor state (govc / mock) | [`infranix/collections/vmware/README.md`](../infranix/collections/vmware/README.md) |
| `image` | `image` | ensure images (ISO) are on the datastore | [`infranix/collections/image/README.md`](../infranix/collections/image/README.md) |
| `terraform` | `provision` | generate + apply VMs via Terraform (vmware/vsphere) | [`infranix/collections/terraform/README.md`](../infranix/collections/terraform/README.md) |
| `ansible` | `configure` | generate inventory + roles, configure the VMs | [`infranix/collections/ansible/README.md`](../infranix/collections/ansible/README.md) |
| `packer` | `build` | build cloneable templates from an ISO | [`infranix/collections/packer/README.md`](../infranix/collections/packer/README.md) |

The pipeline order is: **scan → plan → safety → image → provision → configure**.
Failures inside a collection stay confined there; the core keeps running and the
report tells you which collection failed.

---

## 1. Application CLI options

The `infra` CLI is a Click application. For per-collection options, follow the
link in the table above — each collection's `README.md` documents its options
and ends with an example.

```bash
infra init                          # no name: create ~/.infranix/.env template
infra init <name>                   # with a name: create an InfraNix role scaffold
infra role ...                      # role management (init | run)
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

### `infra role …` — InfraNix-native roles

An **InfraNix role** is a self-contained folder (same spirit as an Ansible
role, but for the application). Variables live in the role itself, not in
`~/.infranix/.env`.

| Command | Description |
|---|---|
| `init <name> [-o .]` | Scaffold a role: `collections/`, `defaults/`, `infra/` |
| `run <name> [-o out] [--apply] [--vault-password PW]` | Run a role (auto-decrypts vault values) |

Scaffolded layout:

```
<name>/
  collections/requirements.yml   # collections `infra` reads & installs
  defaults/main.yml              # your variables (credentials, network, …)
  infra/infra.yaml               # the InfraNix manifest to execute
  README.md
```

`infra role run <name>` loads the variables from `defaults/main.yml` (real
process env still wins; role defaults win over `~/.infranix/.env`), installs
any collection in `collections/requirements.yml`, and runs the manifest.

```bash
infra role init satellite            # scaffold
# edit satellite/defaults/main.yml with your variables
infra role run satellite --apply     # provision + configure
```

### `infra role vault …` — encrypt sensitive defaults

Encrypt sensitive values (passwords, API keys) directly in `defaults/main.yml`
so they can be committed to git safely. Uses AES-128-CBC (Fernet) with a
PBKDF2-derived key — similar to `ansible-vault`.

| Command | Description |
|---|---|
| `encrypt <path> [-k KEY] [-p PW]` | Encrypt one or all string values in a YAML file |
| `decrypt <path> [-p PW]` | Decrypt all vault values in a YAML file (in place) |
| `view <path> [-p PW]` | Show decrypted values without modifying the file |
| `rekey <path> [--old-password OPW] [--new-password NPW]` | Re-encrypt with a new password (rotation) |

Encrypted values look like this in `defaults/main.yml`:

```yaml
INFRA_PASSWORD: vault:a1b2c3d4e5f6.gAAAAABwX8...
INFRA_USER: root                          # plain-text, not sensitive
SATELLITE_PASSWORD: vault:f7e8d9c0b1a2.gAAAAABwY9...
```

Password resolution order (highest → lowest):
  1. `--vault-password` CLI flag
  2. `INFRA_VAULT_PASSWORD` environment variable
  3. Interactive prompt (`getpass`)

```bash
# Encrypt a single key
infra role vault encrypt satellite/defaults/main.yml -k INFRA_PASSWORD

# Encrypt all string values at once
infra role vault encrypt satellite/defaults/main.yml

# View decrypted values (does not change the file)
infra role vault view satellite/defaults/main.yml

# Decrypt back to plain text (in place)
infra role vault decrypt satellite/defaults/main.yml

# Rotate password
infra role vault rekey satellite/defaults/main.yml

# Run without prompt (CI/CD)
INFRA_VAULT_PASSWORD=secret infra role run satellite --apply
```

---

## 2. Key principles

- **Dry-run by default** — `plan`/`run` without `--apply` execute nothing.
- **Safety Gate last resort** — `apply`/`destroy` re-validate and refuse
  destructive actions without the right flags + `safety.destroy: true`.
- **Variables live with the role** — `infra role run` reads them from
  `defaults/main.yml` (no `~/.infranix/.env` needed); secrets should still
  not be committed to git in plain text — use `infra role vault encrypt`.
