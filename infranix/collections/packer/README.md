# `packer` collection — Template Builder (capability: `build`)

Builds a cloneable VM template from a local ISO using HashiCorp Packer
(kickstart/preseed). It is invoked for images whose `build.builder` is `packer`.
The ISO must already be in the local cache (run `infra image ensure` first).

## Options

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

## Example

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
