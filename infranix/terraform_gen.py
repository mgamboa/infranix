"""Terraform generator — Phase 1.

Takes the declarative manifest and produces .tf files (HashiCorp/vsphere)
to create VMs on a standalone ESXi, cloning from templates.

Each VM is cloned from a template whose name matches the manifest image
(server.image). Files: provider/variables/main/tfvars/outputs.
"""

from __future__ import annotations

from pathlib import Path

import jinja2

from infranix.models import Manifest


PROVIDER_TF = """\
terraform {
  required_providers {
    vsphere = {
      source  = "vmware/vsphere"
      version = "~> 2.7"
    }
  }
}

provider "vsphere" {
  user                 = var.vsphere_user
  password             = var.vsphere_password
  vsphere_server       = var.vsphere_server
  allow_unverified_ssl = var.vsphere_insecure
}
"""

VARIABLES_TF = """\
variable "vsphere_user" {
  type      = string
  sensitive = true
}

variable "vsphere_password" {
  type      = string
  sensitive = true
}

variable "vsphere_server" {
  type = string
}

variable "vsphere_insecure" {
  type    = bool
  default = true
}

variable "datastore_name" {
  type = string
}

variable "compute_cluster_path" {
  type = string
}

variable "network_name" {
  type = string
}
"""

MAIN_TF = """\
data "vsphere_datacenter" "dc" {
  name = "ha-datacenter"
}

data "vsphere_datastore" "ds" {
  name          = var.datastore_name
  datacenter_id = data.vsphere_datacenter.dc.id
}

data "vsphere_network" "net" {
  name          = var.network_name
  datacenter_id = data.vsphere_datacenter.dc.id
}

data "vsphere_host" "host" {
  name          = var.compute_cluster_path
  datacenter_id = data.vsphere_datacenter.dc.id
}

# ── Resources (VMs) ──
{% for s in servers %}
resource "vsphere_virtual_machine" "{{ s.name }}" {
  name             = "{{ s.name }}"
  resource_pool_id = data.vsphere_host.host.resource_pool_id
  datastore_id     = data.vsphere_datastore.ds.id

  num_cpus = {{ s.cpu }}
  memory   = {{ s.mem }}
  guest_id = "{{ s.guest_id }}"

  network_interface {
    network_id   = data.vsphere_network.net.id
    adapter_type = "vmxnet3"
  }

  disk {
    label            = "disk0"
    size             = {{ s.disk }}
    thin_provisioned = true
  }

  # Clone from template/VM
  clone {
    template_uuid = "{{ s.template_uuid }}"
{% if s.ip %}
    customize {
      linux_options {
        host_name = "{{ s.name }}"
        domain    = "local"
      }
      network_interface {
        ipv4_address = "{{ s.ip }}"
        ipv4_netmask = 24
{% if s.dns %}
        dns_server_list = {{ s.dns | tojson }}
{% endif %}
      }
      ipv4_gateway = "{{ s.gateway }}"
    }
{% endif %}
  }

  wait_for_guest_net_timeout = 0
}
{% endfor %}
"""

OUTPUTS_TF = """\
output "vm_ips" {
  value = {
    {% for s in servers %}"{{ s.name }}" = vsphere_virtual_machine.{{ s.name }}.default_ip_address{% if not loop.last %},{% endif %}
    {% endfor %}
  }
}
"""


class TerraformGenerator:
    """Converts a Manifest into .tf files for a standalone ESXi."""

    guest_ids = {
        "rhel9": "rhel9_64Guest", "rhel8": "rhel8_64Guest",
        "rocky": "rhel9_64Guest", "centos": "centos8_64Guest",
        "ubuntu": "ubuntuGuest", "debian": "debian12_64Guest",
        "vyos": "other4xLinux64Guest", "opnsense": "freebsd13_64Guest",
        "pfsense": "freebsd13_64Guest", "windows": "windows2022Server64Guest",
    }

    def __init__(self, manifest: Manifest, out_dir: Path, datastore: str,
                 compute_cluster: str, config=None):
        self.manifest = manifest
        self.out_dir = Path(out_dir)
        self.datastore = datastore
        self.compute_cluster = compute_cluster
        self.config = config
        self.env = jinja2.Environment(trim_blocks=True, lstrip_blocks=True)

    def _servers_ctx(self):
        out = []
        for s in self.manifest.servers:
            ip, gateway, dns = "", "", []
            if s.network:
                ip = (s.network[0].ip or "").split("/")[0]
                gateway = s.network[0].gateway or ""
                dns = s.network[0].dns or []
            # Look up template UUID from ESXi
            template_uuid = self._get_template_uuid(s.image)
            out.append({
                "name": s.name,
                "image": s.image,
                "cpu": s.cpu,
                "mem": s.mem,
                "disk": s.disk,
                "guest_id": self._guest_id(s.image),
                "ip": ip,
                "gateway": gateway,
                "dns": dns,
                "template_uuid": template_uuid,
            })
        return out

    def _get_template_uuid(self, image_name: str) -> str:
        """Get the UUID of a template/VM on ESXi by name."""
        import subprocess, os, json
        env = dict(os.environ)
        env["GOVC_URL"] = f"https://{self.config.user}:{self.config.password}@{self.config.host}/sdk"
        env["GOVC_INSECURE"] = "true"
        try:
            res = subprocess.run(
                ["govc", "vm.info", "-json", image_name],
                capture_output=True, text=True, env=env, timeout=30)
            if res.returncode == 0:
                data = json.loads(res.stdout)
                vms = data.get("VirtualMachines", [])
                if vms:
                    return vms[0].get("Config", {}).get("Uuid", "")
        except Exception:
            pass
        return ""

    def _guest_id(self, image: str) -> str:
        img = (image or "").lower()
        for key, gid in self.guest_ids.items():
            if key in img:
                return gid
        return "rhel9_64Guest"

    def generate(self) -> Path:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        servers = self._servers_ctx()
        self.out_dir.joinpath("provider.tf").write_text(PROVIDER_TF)
        self.out_dir.joinpath("variables.tf").write_text(VARIABLES_TF)
        self.out_dir.joinpath("main.tf").write_text(
            self.env.from_string(MAIN_TF).render(servers=servers))
        self.out_dir.joinpath("outputs.tf").write_text(
            self.env.from_string(OUTPUTS_TF).render(servers=servers))
        self.out_dir.joinpath("terraform.tfvars").write_text(
            f'datastore_name       = "{self.datastore}"\n'
            f'compute_cluster_path = "{self.compute_cluster}"\n'
            f'network_name         = "VM Network"\n'
        )
        return self.out_dir
