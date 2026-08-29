"""Generador de Ansible — Fase 1.

Toma el manifiesto declarativo y produce:
  - inventory/hosts.yml   (grupos por rol, hosts con vars de conexión)
  - playbooks/site.yml    (aplica roles a cada grupo)
  - roles/<rol>/          (esqueleto de roles base realizables)

Los roles lista: webserver, postgres, monitoring-agent, wazuh-server,
wazuh-indexer, wazuh-dashboard, kubernetes. Cada uno es un esqueleto con
tasks/main.yml listo para ampliar.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from infranix.models import Manifest


# Esqueleto de tasks por rol
ROLE_TASKS = {
    "webserver": """\
- name: Instalar nginx webserver
  ansible.builtin.package:
    name: nginx
    state: present
  become: true

- name: Iniciar y habilitar nginx
  ansible.builtin.service:
    name: nginx
    state: started
    enabled: true
  become: true
""",
    "postgres": """\
- name: Instalar PostgreSQL
  ansible.builtin.package:
    name: postgresql-server
    state: present
  become: true

- name: Inicializar base de datos (si no existe)
  ansible.builtin.command: postgresql-setup --initdb
  args:
    creates: /var/lib/pgsql/data/PG_VERSION
  become: true
  ignore_errors: true

- name: Iniciar y habilitar postgresql
  ansible.builtin.service:
    name: postgresql
    state: started
    enabled: true
  become: true
""",
    "monitoring-agent": """\
- name: Instalar agente (ej: node_exporter)
  ansible.builtin.package:
    name: prometheus-node-exporter
    state: present
  become: true

- name: Iniciar node_exporter
  ansible.builtin.service:
    name: prometheus-node-exporter
    state: started
    enabled: true
  become: true
""",
    "wazuh-server": """\
- name: Instalar wazuh-manager (placeholder)
  ansible.builtin.debug:
    msg: "Rol wazuh-server a implementar con repositorio oficial de Wazuh"
""",
    "wazuh-indexer": """\
- name: Instalar wazuh-indexer (placeholder)
  ansible.builtin.debug:
    msg: "Rol wazuh-indexer a implementar"
""",
    "wazuh-dashboard": """\
- name: Instalar wazuh-dashboard (placeholder)
  ansible.builtin.debug:
    msg: "Rol wazuh-dashboard a implementar"
""",
    "kubernetes": """\
- name: Instalar kubelet/kubeadm (placeholder)
  ansible.builtin.debug:
    msg: "Rol kubernetes a implementar"
""",
}


class AnsibleGenerator:
    """Genera inventario + playbooks + esqueletos de roles Ansible."""

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

        # ── Inventario (YAML) ──
        hosts = {}
        all_vars = {"ansible_user": "root"}  # placeholder; se ajusta por env
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
        # grupos por rol
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

        # ── Playbook site.yml ──
        site_play = {
            "name": "Converger roles InfraNix",
            "hosts": "all",
            "gather_facts": True,
        }
        pb_dir.mkdir(parents=True, exist_ok=True)
        (pb_dir / "site.yml").write_text(
            yaml.dump([site_play], default_flow_style=False, sort_keys=False))

        # ── Esqueletos de roles ──
        roles_dir = base / "roles"
        for role, tasks in ROLE_TASKS.items():
            t_dir = roles_dir / role / "tasks"
            t_dir.mkdir(parents=True, exist_ok=True)
            (t_dir / "main.yml").write_text(tasks)

        # markdown de como usar
        (base / "README.md").write_text(
            "# InfraNix - Ansible\n\n"
            "Inventario y roles generados desde el manifiesto.\n\n"
            "Ejecutar: `ANSIBLE_HOST_KEY_CHECKING=False ansible-playbook "
            "-i inventory/hosts.yml playbooks/site.yml`\n"
        )
        return base
