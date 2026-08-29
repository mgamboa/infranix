# InfraNix — Declarative Infrastructure Orchestrator

InfraNix es un **orquestador declarativo de infraestructura**. Describes *qué*
quieres en un archivo **YAML**, y la aplicación hace el trabajo: provee la
infraestructura (Terraform), la configura (Ansible) y asegura las imágenes
(Image Manager), con un **Safety Gate** que jamás destruye sin permiso.

```
┌──────────────────────────────────────────────────────────┐
│                    TU ARCHIVO YAML                       │
│   (declaración: servers, networks, routers, lb, images)  │
└────────────────────────────┬─────────────────────────────┘
                             ▼
┌──────────────────────────────────────────────────────────┐
│                  infra run -f infra.yaml                 │
│   valida → escanea → planea → Safety Gate → genera → aplica
└────────────────────────────┬─────────────────────────────┘
                             ▼
   ┌──────────┬──────────────┼──────────────┬──────────┐
   ▼          ▼              ▼              ▼          ▼
 Terraform  Ansible    Image Manager     govc/API  (futuro)
 (provee)   (configura) (imágenes)      (discovery)  Packer
```

## Instalación

```bash
pip install -e .          # instala el comando global `infra`
infra init                 # crea ~/.infranix/.env para credenciales
# edita ~/.infranix/.env con tu hypervisor
```

Requiere: [Terraform](https://www.terraform.io) y `govc` en el PATH.

## Uso rápido

```bash
infra scan                       # muestra el estado actual del hypervisor
infra plan -f infra.yaml         # muestra el plan de cambios (no ejecuta)
infra run -f infra.yaml          # la app: valida, escanea, planea, reporta
infra run -f infra.yaml --apply  # la app: además genera y ejecuta Terraform
infra image ensure -f infra.yaml # asegura imágenes (descarga las que falten)
```

## Documentación

- **[docs/MANUAL.md](docs/MANUAL.md)** — Cómo declarar cosas en el YAML.

## Estructura

```
docs/MANUAL.md              # Manual de declaración (cómo usar el YAML)
examples/                   # Manifiestos de ejemplo
infranix/
  app.py                    # Aplicación orquestadora (corre el YAML)
  config.py                 # Credenciales (~/.infranix/.env)
  models/                   # Schema Pydantic del manifiesto
  adapters/discovery.py     # Scan ESXi via govc (+ mock)
  core/planner.py           # Motor de diff (deseado vs actual)
  core/safety.py            # Safety Gate
  terraform_gen.py          # Generador de .tf (provider vsphere)
  ansible_gen.py            # Generador de inventario + roles Ansible
  image_manager.py          # Descarga/subida de imágenes
  cli/                      # Comandos CLI
```

## Seguridad

El **Safety Gate** es el corazón de InfraNix. Nunca ejecuta una operación
destructiva sin `safety.destroy: true` en el manifiesto. Las operaciones
destructivas requieren además `--yes` explícito. Vea `docs/MANUAL.md §10`.
