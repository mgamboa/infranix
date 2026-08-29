# `image` collection — Image Manager (capability: `image`)

Ensures the manifest images (distro + version) are present on the datastore.
If an image is missing it downloads it from the official mirror (rocky, ubuntu,
debian, centos) and uploads it to the datastore; if it already exists it is
used as-is.

## Options

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

## Example

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
