# InfraNix — Declarative Infrastructure Orchestrator

> Un sistema declarativo que interpreta un manifiesto YAML de infraestructura y,
> de forma autosuficiente y segura, la provee (Terraform), configura (Ansible),
> descubre (govc/vSphere API) y mantiene a lo largo del tiempo.

## 1. Filosofía de diseño

### 1.1 Declarativo primero
El usuario describe el **estado deseado**, nunca los pasos. InfraNix calcula el
**estado actual** (scan), lo compara contra el deseado y aplica solo el *delta*.

### 1.2 Seguridad = núcleo (no un add-on)
Tres principios no negociables:

1. **El sistema es el último árbitro.** Si una operación puede causar pérdida
   de datos o caída de un servicio existente, NO se ejecuta — se detiene y se
   propone una alternativa.

2. **Destrucción con opt-in explícito.** Toda operación destructiva
   (borrar VM, eliminar redes, remover load balancer, destruir storage) requiere
   que el manifiesto lo declare explícitamente:
   ```yaml
   destroy: true        # global opt-in
   # o por recurso:
   servers:
     - name: old-vm
       action: destroy
   ```
   Sin `destroy: true` (global o por recurso), la operación se bloquea.

3. **Dry-run por defecto.** El comando por defecto es `plan` (equivalente a
   `terraform plan`): muestra el plan completo de cambios y NO ejecuta nada.
   `apply` requiere confirmación interactiva si hay cambios destructivos.

### 1.3 Autosuficiente pero auditado
InfraNix toma decisiones (dónde esto gestionado por Jarvis/brain para resolución
de versiones, IPs, mirror selection), pero cada decisión queda registrada en un
**plan de cambio** auditable antes de ejecutarse.

## 2. Stack tecnológico

| Capa           | Tecnología                          | Rol                                          |
|----------------|-------------------------------------|----------------------------------------------|
| CLI/Orquestador| Python                             | Interpreta manifest, orquesta backend        |
| Provisioning   | Terraform                          | Crea/actualiza recursos (VM, redes, LB, cloud)|
| Configuración  | Ansible                            | Configura SO/produtos sobre las VMs           |
| Image Mgmt     | Packer + govc + Python              | Construye/list templates de SO                |
| VMware API     | govc (CLI) + PyVmomi (SDK)          | Scan, manipulación directa de vSphere/ESXi    |
| API/Red        | vSphere vDS + NSX + Ansible network | Switches, routers, LBs                        |
| Estado         | Terraform state + archivo de estado | Persistencia y migración del estado           |

## 3. Arquitectura en capas

```
┌─────────────────────────────────────────────────────────────┐
│                    LAYER 0: INTERFACES                      │
│   CLI (infra plan/apply/scan/destroy)   REST API     Web UI │
└──────────────────────────┬──────────────────────────────────┘
                           │
┌──────────────────────────▼──────────────────────────────────┐
│               LAYER 1: ORCHESTRATOR (nucleo)                │
│   Manifest Parser · Planner (state diff) · Executor         │
│   SafetyGate · Dependency Resolver · Approvals             │
└───────┬──────────────────────┬──────────────────┬───────────┘
        │                      │                  │
┌───────▼────────┐   ┌─────────▼────────┐  ┌──────▼─────────┐
│ LAYER 2: PROV  │   │ LAYER 2: CONFIG  │  │ LAYER 2: SCAN  │
│  Terraform     │   │   Ansible        │  │  govc/API      │
│  providers     │   │   inventories    │  │  discovery     │
│  vsphere/esxi  │   │   roles/products │  │  state survey  │
│  aws/gcp/azr   │   │   network (IOS..)│  │                │
└───────┬────────┘   └─────────┬────────┘  └──────┬─────────┘
        │                      │                  │
┌───────▼──────────────────────▼──────────────────▼─────────┐
│               LAYER 3: PLATFORM ADAPTERS                    │
│   vSphere/vCenter · ESXi · KVM/libvirt · Proxmox · Network │
└─────────────────────────────────────────────────────────────┘
```

## 4. El Manifiesto declarativo (formato)

```yaml
# infra.yaml
version: 1
project: infra-nix-demo
hypervisor: esxi            # vcenter | esxi | proxmox | kvm

# Respeta los entornos existentes; se hace scan antes de actuar
scan_before_apply: true

# POLÍTICAS DE SEGURIDAD
safety:
  destroy: false            # opt-in global para operaciones destructivas
  allow_downtime: false
  confirm_destructive: true

# IMAGENES / TEMPLATES (auto-descarga si no existe)
images:
  - name: rhel-9.4
    distro: rhel
    version: "9.4"
    source:
      type: iso                   # iso | ova | cloudimage | template
      url: https://mirror.example/rhel-9.4.iso
    build:                        # como convertirlo en template usable
      builder: packer
      autounattend: false
      cloud_init: true

# SERVIDORES (VMs)
servers:
  - name: web-prod-01
    image: rhel-9.4
    cpu: 4
    mem: 8192
    disk: 100
    network:
      - name: prod-net
        ip: 192.168.10.10/24
        gateway: 192.168.10.1
        dns: [192.168.10.5]
    roles:            # lo que Ansible debe instalar/configurar
      - webserver
      - monitoring-agent
    action: create    # create | update | destroy (destroy exige safety)

# REDES
networks:
  - name: prod-net
    type: portgroup    # portgroup | dvswitch | vlan
    vlan: 100
    subnet: 192.168.10.0/24
    gateway: 192.168.10.1
    dhcp: false

# ROUTERS VIRTUALES
routers:
  - name: edge-router
    image: vyos-1.5
    interfaces:
      - network: wan-net
      - network: prod-net
    routes:
      - { dest: 0.0.0.0/0, via: wan-gw }
    nat: true

# LOAD BALANCERS
load_balancers:
  - name: lb-web
    type: haproxy           # haproxy | nginx | nsx | aws-elb | f5
    listeners:
      - port: 443
        protocol: https
        backend: web-prod-01, web-prod-02
        health: /healthz
```

## 5. Flujo de ejecución

```
infra plan -f infra.yaml
        │
        ▼
┌─ LAYER 0 ─────────────────────────────────┐
│ 1. Parse manifest + validar schema (Pydantic)│
└──────────────┬─────────────────────────────┘
               ▼
┌─ LAYER 2 scan ─────────────────────────────┐
│ 2. Discovery actual (govc):
│    - list VMs, networks, templates, datastores│
│    - comparar con lo declarado               │
└──────────────┬─────────────────────────────┘
               ▼
┌─ LAYER 1 planner ──────────────────────────┐
│ 3. Calcular DIFF (deseado - actual)         │
│ 4. Clasificar cada cambio:                 │
│    create ✓ · update ✓ · destroy ✋(gated)  │
│ 5. SAFETY GATE: si hay destroy sin opt-in, │
│    bloquear y proponer alternativas         │
│ 6. Resolver dependencias (red antes que VM)│
└──────────────┬─────────────────────────────┘
               ▼
┌─ OUTPUT ───────────────────────────────────┐
│ PLAN legible: qué se crea/actualiza/borra,  │
│ qué se va a descargar/construir, coste,     │
│ advertencias                               │
└────────────────────────────────────────────┘

infra apply -f infra.yaml        # ejecuta tras re-validar safety gate
infra scan --hypervisor esxi     # solo discover, sin acciones
infra destroy -f infra.yaml      # exige --yes + safety.destroy=true
```

## 6. Image Manager (auto-descarga de SO)

Cuando el manifest pide un SO cuya imagen/template NO existe en el hypervisor:

1. **Resolver fuente** — Jarvis decide el mirror oficial (RHEL, Ubuntu cloud,
   Rocky, Debian, etc.) según `distro` + `version`.
2. **Descargar** el ISO/OVA/cloud-image (cache local reutilizable).
3. **Subir al datastore** (govc datastore.upload).
4. **Construir template** — Packer (kickstart/cloud-init/autounattend) para
   dejarlo booteable y listo como template de clonado.
5. **Registrar** en el catálogo local de InfraNix (para no repetir).

Cache: `~/.infranix/images/` con hash + metadatos de versión. Reutiliza entre
proyectos y evita re-descargas.

## 7. Seguridad (Safety Gate) — detalle

El Safety Gate es un componente transversal que se evalúa en `plan` y de nuevo
(con más fuerza) en `apply`. Reglas:

| Regla | Comportamiento |
|-------|----------------|
| `DestroyRecurso` | Bloqueado salvo `safety.destroy: true` global O `action: destroy` + confirmación en el recurso |
| `DownServicioExistente` | Bloqueado salvo `safety.allow_downtime: true` |
| `OverwriteConfig` | Siempre pide confirmación; registra backup |
| `ImagenNoExiste` | No bloquea; dispara Image Manager (descarga/build) |
| `ConflictoIP` | Bloquea; propone IP libre alternativa |
| `FaltaDependencia` | Resuelve topológicamente o bloquea con mensaje claro |

El Safety Gate **nunca** es opcional; es la capa por defecto. En modo
`--force-destroy` solo se puede activar declarando en el manifold.

## 8. Roadmap por fases

- **Fase 0 — Fundación**: estructura del proyecto, schema Pydantic del manifest,
  CLI (`plan`/`scan`), motor de diff, Safety Gate. Soporte ESXi standalone.
- **Fase 1 — Provisioning**: generación de Terraform (provider vsphere/esxi),
  creación de VMs, redes (portgroup/vDS), imágenes.
- **Fase 2 — Configuración**: inventarios Ansible, roles base (SO, productos),
  ejecución post-provision.
- **Fase 3 — Image Manager**: auto-descarga + build de templates.
- **Fase 4 — Redes & LB**: routers virtuales (VyOS/OPNsense), load balancers
  (haproxy/nginx/NSX), switches via Ansible network.
- **Fase 5 — Hipervisores múltiples**: vCenter, Proxmox, KVM/libvirt, cloud.
- **Fase 6 — UI/API**: REST API + web dashboard del estado y planes.

## 9. Estado del proyecto

- Lenguaje motor: **Python 3.14+**
- Dependencias core: `pydantic`, `click` (CLI), `jinja2` (templates),
  `PyYAML`. Adapters: `govc` (VMware), `terraform`, `ansible`.
- Workspace: `/home/mgamboa/ai/infranix`
