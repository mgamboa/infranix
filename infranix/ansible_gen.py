"""Ansible generator — Phase 1.

Takes the declarative manifest and produces:
  - inventory/hosts.yml   (groups by role, hosts with connection vars)
  - playbooks/site.yml    (applies roles to each group)
  - roles/<rol>/          (skeleton of feasible base roles)

The listed roles: webserver, postgres, monitoring-agent, wazuh-server,
wazuh-indexer, wazuh-dashboard, kubernetes. Each one is a skeleton with
tasks/main.yml ready to extend.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from infranix.models import Manifest


# Task skeleton per role
ROLE_TASKS = {
    "webserver": """\
- name: Install nginx webserver
  ansible.builtin.package:
    name: nginx
    state: present
  become: true

- name: Start and enable nginx
  ansible.builtin.service:
    name: nginx
    state: started
    enabled: true
  become: true
""",
    "postgres": """\
- name: Install PostgreSQL
  ansible.builtin.package:
    name: postgresql-server
    state: present
  become: true

- name: Initialize database (if it does not exist)
  ansible.builtin.command: postgresql-setup --initdb
  args:
    creates: /var/lib/pgsql/data/PG_VERSION
  become: true
  ignore_errors: true

- name: Start and enable postgresql
  ansible.builtin.service:
    name: postgresql
    state: started
    enabled: true
  become: true
""",
    "monitoring-agent": """\
- name: Install agent (e.g. node_exporter)
  ansible.builtin.package:
    name: prometheus-node-exporter
    state: present
  become: true

- name: Start node_exporter
  ansible.builtin.service:
    name: prometheus-node-exporter
    state: started
    enabled: true
  become: true
""",
    "wazuh-server": """\
- name: Install wazuh-manager (placeholder)
  ansible.builtin.debug:
    msg: "wazuh-server role to be implemented with the official Wazuh repository"
""",
    "wazuh-indexer": """\
- name: Install wazuh-indexer (placeholder)
  ansible.builtin.debug:
    msg: "wazuh-indexer role to be implemented"
""",
    "wazuh-dashboard": """\
- name: Install wazuh-dashboard (placeholder)
  ansible.builtin.debug:
    msg: "wazuh-dashboard role to be implemented"
""",
    "kubernetes": """\
- name: Install kubelet/kubeadm (placeholder)
  ansible.builtin.debug:
    msg: "kubernetes role to be implemented"
""",
}


class AnsibleGenerator:
    """Generates Ansible inventory + playbooks + role skeletons."""

    def __init__(self, manifest: Manifest, out_dir: Path):
        self.manifest = manifest
        self.out_dir = Path(out_dir)

    def _host_ip(self, server) -> str:
        if server.network:
            ip = (server.network[0].ip or "")
            return ip.split("/")[0]
        return ""

    def generate(self) -> Path:
        base = self.out_dir / "ansible"
        inv_dir = base / "inventory"
        pb_dir = base / "playbooks"

        # ── Inventory (YAML) ──
        hosts = {}
        all_vars = {"ansible_user": "root"}  # placeholder; adjusted per environment
        for s in self.manifest.servers:
            if s.action.value != "destroy":
                hosts[s.name] = {"ansible_host": self._host_ip(s) or ""}
                for role in s.roles:
                    hosts.setdefault("_role_" + role, {}).setdefault("hosts", []).append(s.name)

        inventory = {
            "all": {
                "hosts": {n: h for n, h in hosts.items() if not n.startswith("_role_")},
                "vars": all_vars,
            }
        }
        # groups by role
        role_groups = {}
        for key in list(hosts):
            if key.startswith("_role_"):
                role_name = key[len("_role_"):]
                role_groups[role_name] = {"hosts": hosts[key]["hosts"]}
        if role_groups:
            inventory["all"]["children"] = role_groups

        inv_dir.mkdir(parents=True, exist_ok=True)
        (inv_dir / "hosts.yml").write_text(
            yaml.dump(inventory, default_flow_style=False, sort_keys=False))

        # ── site.yml playbook ──
        site_play = {
            "name": "Converge InfraNix roles",
            "hosts": "all",
            "gather_facts": True,
        }
        pb_dir.mkdir(parents=True, exist_ok=True)
        (pb_dir / "site.yml").write_text(
            yaml.dump([site_play], default_flow_style=False, sort_keys=False))

        # ── Role skeletons ──
        roles_dir = base / "roles"
        for role, tasks in ROLE_TASKS.items():
            t_dir = roles_dir / role / "tasks"
            t_dir.mkdir(parents=True, exist_ok=True)
            (t_dir / "main.yml").write_text(tasks)

        # markdown on how to use it
        (base / "README.md").write_text(
            "# InfraNix - Ansible\n\n"
            "Inventory and roles generated from the manifest.\n\n"
            "Run: `ANSIBLE_HOST_KEY_CHECKING=False ansible-playbook "
            "-i inventory/hosts.yml playbooks/site.yml`\n"
        )
        return base
