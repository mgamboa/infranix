# InfraNix — Manual de Declaración

InfraNix es un **orquestador declarativo**: tú describes *qué* quieres (estado
deseado) en un archivo YAML, y la aplicación hace el trabajo: provee la
infraestructura (Terraform), la configura (Ansible) y asegura las imágenes
(Image Manager).

Todo el sistema es **seguro por diseño**: nunca destruye ni provoca caídas sin
un permiso explícito en el propio YAML.

---

## 1. Estructura general del archivo YAML

Un manifiesto se compone de secciones top-level. Todas excepto `version`,
`project` y `hypervisor` son opcionales.

```yaml
version: 1
project: mi-proyecto          # nombre del proyecto
hypervisor: esxi              # vcenter | esxi | proxmox | kvm

scan_before_apply: true       # (opcional) escanear estado actual antes de aplicar

safety:                       # (opcional) políticas de seguridad
  destroy: false

images: [...]                 # (opcional) catálogo de sistemas operativos
networks: [...]               # (opcional) redes
servers: [...]                # (opcional) máquinas virtuales
routers: [...]                # (opcional) routers virtuales
load_balancers: [...]         # (opcional) balanceadores de carga
```

---

## 2. Campos raíz

| Campo | Tipo | Obligatorio | Descripción |
|-------|------|-------------|-------------|
| `version` | int | sí | Versión del formato (actualmente 1) |
| `project` | string | sí | Nombre del proyecto |
| `hypervisor` | string | sí | `vcenter`, `esxi`, `proxmox`, `kvm` |
| `scan_before_apply` | bool | no (def `true`) | Escanea el estado actual antes de decidir |
| `safety` | object | no | Políticas de seguridad (ver §10) |

---

## 3. Sección `safety` (seguridad)

Controla las operaciones de riesgo. **Por defecto todo es conservador.**

```yaml
safety:
  destroy: false             # true = permitir destrucción (si además cada
                             #   recurso declara action: destroy)
  allow_downtime: false      # true = aceptar reinicios/paradas de servicios
  confirm_destructive: true  # true = exigir confirmación extra antes de borrar
  scan_before_apply: true    # true = re-escanear antes de aplicar
```

> ⚠️ **Regla de oro:** la aplicación NUNCA destruye nada a menos que
> `safety.destroy` sea `true` Y el recurso use `action: destroy`. En caso
> contrario, la operación queda bloqueada y se propone una alternativa.

---

## 4. Sección `images` (sistemas operativos / plantillas)

Declara qué sistemas operativos puede usar el proyecto y cómo obtenerse.

```yaml
images:
  - name: rhel-9.5              # identificador que referencian los servers
    distro: rhel                # rhel | rocky | ubuntu | debian | centos ...
    version: "9.5"              # versión del SO
    source:
      type: iso                 # iso | ova | cloudimage | template
      url: https://...          # (opcional) fuente explícita de descarga
    build:
      builder: direct           # direct | packer
      cloud_init: true          # preparar con cloud-init (recomendado)
      autounattend: false       # solo Windows
```

**Comportamiento automático (Image Manager):**
- Si la imagen (distro+versión) **ya está** en el datastore → se usa tal cual.
- Si **no está** → la aplicación la **descarga del mirror oficial** (rocky,
  ubuntu, debian, centos) y la **sube al datastore** del hypervisor
  automáticamente.
- Para **RHEL** (requiere suscripción) usará el ISO interno si lo tienes.

---

## 5. Sección `servers` (máquinas virtuales)

Describe cada VM: hardware, red, sistema operativo y qué roles/configurar.

```yaml
servers:
  - name: web-prod-01                    # nombre único
    image: rhel-9.5                      # refiere una imagen (sección images)
    cpu: 4                               # vCPUs
    mem: 8192                            # RAM en MB
    disk: 100                            # disco en GB
    network:
      - name: VM Network                 # nombre de la red (portgroup)
        ip: 192.168.2.150/24             # IP + máscara
        gateway: 192.168.2.1             # puerta de enlace
        dns: [192.168.2.1]               # servidores DNS
    roles:                               # qué debe configurar Ansible
      - webserver
      - monitoring-agent
    action: create                       # create | update | destroy
```

### Ciclo de vida (`action`)

| Valor | Efecto | Requiere |
|-------|--------|----------|
| `create` | Crea la VM (si no existe) | — |
| `update` | Actualiza recursos existentes | — |
| `destroy` | **Elimina** la VM | `safety.destroy: true` |

> Un `destroy` sin `safety.destroy: true` es **bloqueado** por la aplicación.

---

## 6. Sección `networks` (redes)

Define/usa redes en el hypervisor.

```yaml
networks:
  - name: prod-net
    type: portgroup              # portgroup | dvswitch | vlan
    vlan: 100
    subnet: 192.168.10.0/24
    gateway: 192.168.10.1
    dhcp: false
```

| Campo | Tipo | Descripción |
|-------|------|-------------|
| `name` | string | Nombre de la red/portgroup |
| `type` | string | `portgroup`, `dvswitch`, `vlan` |
| `vlan` | int | (opcional) ID de VLAN |
| `subnet` | string | (opcional) subred CIDR |
| `gateway` | string | (opcional) gateway |
| `dhcp` | bool | Si usa DHCP |

---

## 7. Sección `routers` (routers virtuales)

Agrega enrutamiento cuando no hay switch L3 gestionado.

```yaml
routers:
  - name: edge-router
    image: vyos-1.5                # imagen/router a usar
    interfaces:
      - { network: wan-net }       # interfaz hacia red WAN
      - { network: prod-net }      # interfaz hacia red interna
    routes:
      - { dest: 0.0.0.0/0, via: wan-gw }
    nat: true                      # habilitar NAT
```

---

## 8. Sección `load_balancers` (balanceadores de carga)

Publica servicios detrás de un balanceador.

```yaml
load_balancers:
  - name: lb-web
    type: haproxy                  # haproxy | nginx | nsx | aws-elb | f5
    listeners:
      - port: 443
        protocol: https
        backend: [web-prod-01, web-prod-02]   # servers de backend
        health: /healthz           # ruta de healthcheck
```

---

## 9. Referenciando cosas entre secciones

- `server.image` → nombre de una imagen de la sección `images`.
- `server.network[].name` → nombre de una red de `networks` (o un portgroup
  del hypervisor).
- `lb.listeners[].backend` → nombres de `servers`.
- `router.interfaces[].network` → nombres de `networks`.

La aplicación valida que las referencias existan y resuelve dependencias
(redes antes que VMs, imágenes antes que VMs, etc.).

---

## 10. Resumen rápido: ejemplo completo

```yaml
version: 1
project: webstack
hypervisor: esxi

safety:
  destroy: false
  allow_downtime: false

images:
  - name: rocky-9
    distro: rocky
    version: "9.5"

networks:
  - name: VM Network
    type: portgroup
    subnet: 192.168.2.0/24
    gateway: 192.168.2.1

servers:
  - name: web-prod-01
    image: rocky-9
    cpu: 4
    mem: 8192
    disk: 60
    network:
      - { name: VM Network, ip: 192.168.2.150/24, gateway: 192.168.2.1, dns: [192.168.2.1] }
    roles: [webserver]
    action: create

load_balancers:
  - name: lb-web
    type: haproxy
    listeners:
      - { port: 443, protocol: https, backend: [web-prod-01], health: /healthz }
```

---

## 11. Comandos de la aplicación

| Comando | Descripción |
|---------|-------------|
| `infra run -f infra.yaml` | Valida, escanea, planea, y aplica de una vez (modo autónomo) |
| `infra plan -f infra.yaml` | Muestra el plan sin ejecutar |
| `infra apply -f infra.yaml` | Ejecuta el plan (re-valida seguridad) |
| `infra scan` | Escanea y muestra el estado actual |
| `infra image ensure -f infra.yaml` | Asegura imágenes (descarga las que falten) |
| `infra destroy -f infra.yaml --yes` | Destruye (requiere opt-in) |
| `infra init` | Crea `~/.infranix/.env` para credenciales |

### Credenciales
Las credenciales del hypervisor viven en `~/.infranix/.env` (nunca en el repo):

```
INFRA_HYPERVISOR=esxi
INFRA_HOST=192.168.X.X          # IP de tu ESXi/vCenter
INFRA_USER=root
INFRA_PASSWORD=tu_password      # tus credenciales
INFRA_INSECURE=1
```
