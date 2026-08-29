"""Ansible generator — Phase 1.

Takes the declarative manifest and produces:
  - inventory/hosts.yml        (groups by role, hosts with connection vars)
  - playbooks/site.yml         (applies roles to each group)
  - roles/<role>/              (skeleton of feasible base roles)
  - galaxy/requirements.yml    (Ansible Galaxy collections the roles need)

Role→Ansible Galaxy collection resolution: a role is mapped to a Galaxy
collection through `ROLE_GALAXY` (explicit entries). Any role that is not
listed is left out, so plain roles (webserver, monitoring-agent...) never
trigger an extra Galaxy install by mistake. Each unique collection is installed
once via `ansible-galaxy collection install`.

The *baseline* collections that Ansible core content generally relies on are
always installed, regardless of roles: `community.general` and `ansible.posix`.
These are prepended to the role-derived requirements so they are guaranteed
present before any playbook runs.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from infranix.models import Manifest


# Baseline Galaxy collections that Ansible core/content commonly needs.
# Always installed (prepended to requirements.yml), independent of roles.
DEFAULT_GALAXY_COLLECTIONS = [
    "community.general",
    "ansible.posix",
]

# Role name -> Ansible Galaxy collection(s) it depends on.
# Add entries here for any role that needs external content from Galaxy
# (e.g. `redhat-satellite` needs the `redhat.satellite` collection).
ROLE_GALAXY = {
    "redhat-satellite": ["redhat.satellite"],
    "satellite": ["redhat.satellite"],
    "redhat-registration": ["redhat.rhel_system_roles"],
    "rhel-system-roles": ["redhat.rhel_system_roles"],
    "community-kubernetes": ["community.kubernetes"],
}


def galaxy_collections(manifest) -> list[str]:
    """Return the unique Ansible Galaxy collections required by a manifest.

    Starts with the baseline collections (`community.general`, `ansible.posix`)
    and adds every collection demanded by the manifest's server roles.
    """
    result: list[str] = [c for c in DEFAULT_GALAXY_COLLECTIONS]
    seen: set[str] = set(result)
    for server in manifest.servers:
        for role in server.roles:
            for coll in ROLE_GALAXY.get(role, []):
                if coll not in seen:
                    seen.add(coll)
                    result.append(coll)
    return result


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
    "redhat-satellite": """\
# Register the host with Red Hat Subscription Manager (RHSM) using the
# community.general.redhat_subscription module, then install Satellite.
#
# Credentials come from defaults/main.yml in this role (populated from the
# server's `vars:` in the manifest, e.g. ${RHN_USERNAME} / ${RHN_PASSWORD}).
- name: Register host with Red Hat Subscription Manager
  community.general.redhat_subscription:
    state: present
    username: "{{ rhn_username | default('') }}"
    password: "{{ rhn_password | default('') }}"
    auto_attach: "{{ auto_attach | default(true) }}"
  when: rhn_username is defined and rhn_username | length > 0
  become: true

- name: Enable required repositories for Satellite
  community.general.rhsm_repository:
    name: "{{ item }}"
    state: enabled
  loop:
    - rhel-{{ ansible_distribution_major_version }}-for-x86_64-baseos-rpms
    - rhel-{{ ansible_distribution_major_version }}-for-x86_64-appstream-rpms
    - satellite-{{ ansible_distribution_major_version }}-for-rhel-{{ ansible_distribution_major_version }}-x86_64-rpms
  become: true
  when: rhn_username is defined and rhn_username | length > 0

- name: Install Satellite server packages
  ansible.builtin.package:
    name:
      - satellite
    state: present
  become: true

- name: Run Satellite installer
  ansible.builtin.command: >
    satellite-installer --scenario satellite
    --foreman-initial-organization "{{ satellite_org | default('Default Organization') }}"
    --foreman-initial-location "{{ satellite_location | default('Default Location') }}"
    --foreman-initial-admin-username "{{ satellite_admin_user | default('admin') }}"
    --foreman-initial-admin-password "{{ satellite_admin_password | default('changeme') }}"
  args:
    creates: /etc/satelliteserver-package-mgmt-complete
  become: true
""",
}


# Standard role directories (same layout as `ansible-galaxy init <role>`).
ROLE_DIRS = [
    "tasks",
    "handlers",
    "defaults",
    "vars",
    "meta",
    "templates",
    "files",
]


HANDLERS_MAIN = """\
# Handlers triggered by the tasks (e.g. "notify:").
- name: restart <service>
  ansible.builtin.service:
    name: "{{ item }}"
    state: restarted
"""


META_MAIN = """\
galaxy_info:
  author: InfraNix
  description: Role generated by InfraNix
  license: MIT
  min_ansible_version: "2.9"
  platforms:
    - name: EL
      versions:
        - all
dependencies: []
"""


ROLE_README = """\
# Role: {role}

Generated by InfraNix.

{description}

## Variables

Defaults live in `defaults/main.yml` (overridable). They are populated from
the server's `vars:` block in the InfraNix manifest.

Override any default at the group/host level in `inventory/group_vars/` or
`inventory/host_vars/`.

See `tasks/main.yml` for what this role does.
"""


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
                host_vars = {"ansible_host": self._host_ip(s) or ""}
                # Pass root_password as SSH credential for Ansible
                if s.vars and s.vars.get("root_password"):
                    host_vars["ansible_ssh_pass"] = s.vars["root_password"]
                    host_vars["ansible_become_pass"] = s.vars["root_password"]
                hosts[s.name] = host_vars
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

        # ── Roles: standard structure + defaults/main.yml from servers[].vars ──
        # Each role gets the same layout as `ansible-galaxy init <role>`:
        #   tasks/ handlers/ defaults/ vars/ meta/ templates/ files/
        # Server variables (servers[].vars) land in <role>/defaults/main.yml,
        # so they can be overridden by group/host vars at inventory level.
        roles_dir = base / "roles"
        role_vars: dict[str, dict[str, object]] = {}
        for s in self.manifest.servers:
            if not s.vars:
                continue
            for role in s.roles:
                role_vars.setdefault(role, {}).update(s.vars)

        # every role referenced by any server gets a full scaffold
        ref_roles: set[str] = {r for s in self.manifest.servers for r in s.roles}

        def _scaffold(role: str, description: str = ""):
            rdir = roles_dir / role
            for sub in ROLE_DIRS:
                (rdir / sub).mkdir(parents=True, exist_ok=True)
            # defaults: server vars (clean, unresolved ${...} removed)
            vdict = role_vars.get(role, {})
            clean = {k: v for k, v in vdict.items()
                     if not isinstance(v, str) or "${" not in v}
            if clean:
                (rdir / "defaults" / "main.yml").write_text(
                    yaml.dump(clean, default_flow_style=False, sort_keys=False))
            # handlers + meta + README only if not present (don't overwrite)
            for rel, content in {
                "handlers/main.yml": HANDLERS_MAIN,
                "meta/main.yml": META_MAIN,
            }.items():
                target = rdir / rel
                if not target.exists():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(content)
            readme = rdir / "README.md"
            if not readme.exists():
                readme.write_text(ROLE_README.format(
                    role=role, description=description))

        # scaffold every role referenced in the manifest
        for role in sorted(ref_roles):
            _scaffold(role)
        # plus built-in roles (so ROLE_TASKS bodies are generated)
        for role, tasks in ROLE_TASKS.items():
            if role not in ref_roles:
                _scaffold(role, "built-in role skeleton")
            t_dir = roles_dir / role / "tasks"
            t_dir.mkdir(parents=True, exist_ok=True)
            (t_dir / "main.yml").write_text(tasks)

        # ── Galaxy requirements (Ansible collections the roles need) ──
        colls = galaxy_collections(self.manifest)
        galaxy_dir = base / "galaxy"
        if colls:
            galaxy_dir.mkdir(parents=True, exist_ok=True)
            (galaxy_dir / "requirements.yml").write_text(
                yaml.dump(
                    {"collections": [{"name": c} for c in colls]},
                    default_flow_style=False, sort_keys=False))

        # markdown on how to use it
        (base / "README.md").write_text(
            "# InfraNix - Ansible\n\n"
            "Inventory and roles generated from the manifest.\n\n"
            "Run: `ANSIBLE_HOST_KEY_CHECKING=False ansible-playbook "
            "-i inventory/hosts.yml playbooks/site.yml`\n"
        )
        return base
