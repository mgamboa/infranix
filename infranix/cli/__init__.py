"""CLI de InfraNix.

Comandos:
  init    - genera ~/.infranix/.env con plantilla (para poner credenciales)
  scan    - describe la infraestructura actual del hypervisor (solo lectura)
  plan    - calcula el diff manifest vs actual, muestra el plan (no ejecuta)
  apply   - ejecuta el plan (re-valida Safety Gate antes)
  destroy - operación destructiva; requiere --yes + safety.destroy
"""

from __future__ import annotations

import sys

import click
import yaml

from infranix.config import load_config, write_config_template, resolve_vars
from infranix.core.planner import Planner, ChangeKind
from infranix.core.safety import SafetyGate
from infranix.models import Manifest


def _load_manifest(path: str) -> Manifest:
    try:
        with open(path, "r") as f:
            data = yaml.safe_load(f)
    except FileNotFoundError:
        raise click.ClickException(f"Manifiesto no encontrado: {path}")
    data = resolve_vars(data)
    return Manifest(**data)


@click.group()
def cli():
    """InfraNix — Orquestador declarativo de infraestructura."""
    pass


@cli.command()
def init():
    """Genera ~/.infranix/.env con credenciales (plantilla segura)."""
    path = write_config_template()
    click.echo(f"Plantilla de config creada en {path}")
    click.echo("Editálala con tus credenciales ESXi y luego ejecuta 'infra scan'.")


@cli.command()
def scan():
    """Describe la infraestructura actual del hypervisor (solo lectura)."""
    config = load_config()
    if not config.configured and config.hypervisor != "mock":
        click.echo("No hay credenciales. Ejecuta 'infra init' y edita ~/.infranix/.env, "
                   "o usa INFRA_HYPERVISOR=mock para desarrollo.")
        sys.exit(1)

    from infranix.core.registry import get_registry
    from infranix.pluginbase import Capability, PluginContext
    scanner = get_registry().resolve(Capability.SCAN)
    if scanner is None:
        raise click.ClickException("Sin colección SCAN habilitada.")
    click.echo(f"Escaneando {config.hypervisor} @ {config.host or 'mock'}...")
    try:
        ctx = PluginContext(config=config)
        sreport = scanner.apply(ctx)
        if not sreport.ok:
            raise click.ClickException("; ".join(sreport.errors or [sreport.message]))
        inv = sreport.data["inventory"]
    except click.ClickException:
        raise
    except Exception as e:
        raise click.ClickException(f"Error escaneando: {e}")

    click.echo(f"\nHost:  {inv.host}  ({inv.os_version})")
    click.echo(f"VMs:   {len(inv.vms)}")
    click.echo(f"Redes: {', '.join(inv.networks) or '-'}")
    click.echo(f"Datastores: {len(inv.datastores)}")
    for d in inv.datastores:
        click.echo(f"  - {d.name}: {d.free_bytes/1e9:.0f}GB libres / "
                   f"{d.capacity_bytes/1e9:.0f}GB total")

    from tabulate import tabulate
    if inv.vms:
        rows = [[v.name, v.power_state, v.cpu, v.mem_mb, v.guest_os,
                 v.ip or "-"] for v in inv.vms]
        click.echo("\n" + tabulate(
            rows, headers=["Nombre", "Power", "vCPU", "RAM(MB)", "Guest OS", "IP"],
            tablefmt="simple"))
    if inv.images:
        click.echo(f"\nImágenes (ISO) disponibles ({len(inv.images)}):")
        for img in inv.images:
            click.echo(f"  - {img}")


@cli.command()
@click.option("-f", "--file", "file_path", default="infra.yaml",
              help="Ruta al manifiesto YAML (def: infra.yaml)")
@click.option("-o", "--out", "out_dir", default="out",
              help="Directorio de salida de Terraform/Ansible (def: out)")
def plan(file_path: str, out_dir: str):
    """Calcula el diff manifest vs actual y muestra el plan (no ejecuta).

    El scan y la generación de artefactos se delegan en las colecciones
    SCAN/PROVISION/CONFIGURE vía el orquestador.
    """
    from infranix.app import InfraNix
    from infranix.core.planner import Planner, ChangeKind
    from infranix.core.registry import get_registry
    from infranix.pluginbase import Capability, PluginContext

    manifest = _load_manifest(file_path)
    config = load_config()

    # Scan via colección SCAN
    scan_provider = get_registry().resolve(Capability.SCAN)
    if scan_provider is None:
        raise click.ClickException("Sin colección SCAN habilitada.")
    sctx = PluginContext(config=config)
    sreport = scan_provider.apply(sctx)
    if not sreport.ok:
        raise click.ClickException("; ".join(sreport.errors or [sreport.message]))
    inventory = sreport.data["inventory"]

    planner = Planner(manifest, inventory)
    plan = planner.plan()
    gate = SafetyGate(manifest.safety)
    report = gate.evaluate(manifest, plan)

    click.echo(f"\nProyecto: {manifest.project}  |  hypervisor: {manifest.hypervisor.value}")
    click.echo("=" * 60)
    click.echo(plan.summary())
    click.echo("-" * 60)

    for c in plan.changes:
        icon = {"create": "[+]", "update": "[~]", "destroy": "[-]",
                "noop": "[=]", "image-missing": "[img]"}[c.kind.value]
        click.echo(f"  {icon} {c.resource_type:13s} {c.name}")
        if c.detail:
            click.echo(f"        {c.detail}")

    if plan.images_missing:
        click.echo("\n⚠  Imágenes faltantes (se descargarán/construirán):")
        for img in plan.images_missing:
            click.echo(f"    - {img}")

    click.echo("\n" + "=" * 60)
    click.echo(report.summary())
    if not report.allowed:
        click.echo("\nEl plan está BLOQUEADO por el Safety Gate. Revisa las políticas "
                   "en el manifiesto antes de continuar.")
        sys.exit(2)

    # Generación de artefactos (delegado a PROVISION/CONFIGURE con apply=False)
    try:
        app = InfraNix(config)
        rep2 = app.run(file_path, out_dir=out_dir, apply=False)
        if rep2.errors:
            for e in rep2.errors:
                click.echo(f"\n⚠  {e}")
        else:
            click.echo("\nArtefactos generados en out/ (terraform + ansible).")
            click.echo("Ejecuta 'infra apply' para desplegar (tras re-validar seguridad).")
    except Exception as e:
        click.echo(f"\n⚠  No se pudieron generar artefactos: {e}")

    click.echo("\nEl plan está APROBADO por el Safety Gate.")


@cli.command()
@click.option("-f", "--file", "file_path", default="infra.yaml")
@click.option("-o", "--out", "out_dir", default="out")
@click.option("--apply", is_flag=True,
              help="Aplica el plan (ejecuta Terraform/Ansible). Sin esto, solo planifica.")
@click.option("--report", "report_format", default="text",
              type=click.Choice(["text", "markdown"]),
              help="Formato del reporte (def: text).")
def run(file_path: str, out_dir: str, apply: bool, report_format: str):
    """Corre el archivo YAML declarativo de principio a fin (la aplicación).

    Valida, escanea, planea, aplica el Safety Gate y, con --apply, genera
    y ejecuta Terraform + Ansible. Produce un reporte.
    """
    from infranix.app import InfraNix
    app = InfraNix()
    report = app.run(file_path, out_dir=out_dir, apply=apply)

    if report_format == "markdown":
        click.echo(report.to_markdown())
    else:
        click.echo(f"\nProyecto: {report.project} | hypervisor: {report.hypervisor}")
        click.echo(f"Plan: {report.plan_summary}")
        click.echo(f"Safety: {'APROBADO' if report.safety_approved else 'BLOQUEADO'}")
        for e in report.errors:
            click.echo(f"  ⚠  {e}")
        if not report.plan_summary:
            click.echo("  (sin cambios)")
        if report.images_ensured:
            click.echo("Imágenes:")
            for i in report.images_ensured:
                click.echo(f"  - {i}")
        if report.provision_log:
            click.echo("Provisión:")
            click.echo(report.provision_log.strip()[-1500:])

    if report.errors and not report.safety_approved:
        sys.exit(2)


@cli.command()
@click.option("-f", "--file", "file_path", default="infra.yaml")
@click.option("--yes", is_flag=True, help="Confirma operaciones destructivas.")
@click.option("-o", "--out", "out_dir", default="out",
              help="Directorio de salida de artefactos (def: out)")
@click.option("--skip-apply", is_flag=True,
              help="Genera artefactos pero NO ejecuta Terraform/Ansible.")
def apply(file_path: str, yes: bool, out_dir: str, skip_apply: bool):
    """Ejecuta el plan (re-valida Safety Gate antes de actuar).

    Delega en el orquestador (app.run), que a su vez delega en las
    colecciones SCAN/IMAGE/PROVISION/CONFIGURE. El fallo de cualquier
    colección queda confinado y el reporte lo señala.
    """
    from infranix.app import InfraNix
    app = InfraNix()

    # Re-validación de operaciones destructivas antes de tocar nada
    manifest = _load_manifest(file_path)
    from infranix.core.planner import Planner, ChangeKind
    from infranix.core.registry import get_registry
    from infranix.pluginbase import Capability, PluginContext
    scan_provider = get_registry().resolve(Capability.SCAN)
    if scan_provider is None:
        raise click.ClickException("Sin colección SCAN habilitada.")
    sctx = PluginContext(config=load_config())
    inv = scan_provider.apply(sctx)
    if not inv.ok:
        raise click.ClickException(inv.message)
    plan = Planner(manifest, inv.data["inventory"]).plan()
    destructive = [c for c in plan.changes if c.kind == ChangeKind.DESTROY]
    if destructive and not yes:
        click.echo("Operaciones destructivas requieren --yes y safety.destroy: true.")
        click.echo("Nada se ejecutó.")
        sys.exit(2)

    report = app.run(file_path, out_dir=out_dir, apply=not skip_apply)
    click.echo(f"\nPlan: {report.plan_summary}")
    click.echo(f"Safety: {'APROBADO' if report.safety_approved else 'BLOQUEADO'}")
    for img in report.images_ensured:
        click.echo(f"  - {img}")
    if report.provision_log:
        click.echo("Provisión:")
        click.echo(f"  {report.provision_log}")
    if report.configure_log:
        click.echo("Configuración:")
        click.echo(f"  {report.configure_log}")
    for e in report.errors:
        click.echo(f"  ⚠  {e}")
    if report.errors and not report.safety_approved:
        sys.exit(2)


@cli.command()
@click.option("-f", "--file", "file_path", default="infra.yaml")
@click.option("--yes", is_flag=True, help="Confirmación obligatoria para destruir.")
def destroy(file_path: str, yes: bool):
    """Destruye recursos declarados con action: destroy. MUY MUY cuidadoso."""
    from infranix.core.planner import Planner, ChangeKind
    from infranix.core.registry import get_registry
    from infranix.pluginbase import Capability, PluginContext

    manifest = _load_manifest(file_path)
    config = load_config()
    scan_provider = get_registry().resolve(Capability.SCAN)
    if scan_provider is None:
        raise click.ClickException("Sin colección SCAN habilitada.")
    sctx = PluginContext(config=config)
    sreport = scan_provider.apply(sctx)
    if not sreport.ok:
        raise click.ClickException("; ".join(sreport.errors or [sreport.message]))
    inventory = sreport.data["inventory"]

    planner = Planner(manifest, inventory)
    plan = planner.plan()
    gate = SafetyGate(manifest.safety)
    report = gate.evaluate(manifest, plan)

    click.echo(report.summary())
    if not report.allowed:
        click.echo("Destrucción BLOQUEADA por Safety Gate. Nada se ejecutó.")
        sys.exit(2)

    destroys = [c for c in plan.changes if c.kind == ChangeKind.DESTROY]
    if not destroys:
        click.echo("No hay recursos que destruir.")
        return
    if not yes:
        click.echo("Para destruir debes confirmar con --yes. Nada se ejecutó.")
        sys.exit(3)
    if not manifest.safety.destroy:
        click.echo("El manifiesto no tiene 'safety.destroy: true'. Nada se ejecutó.")
        sys.exit(4)

    click.echo(f"Destruyendo {len(destroys)} recurso(s)... Fase 0: solo simulado.")
    for c in destroys:
        click.echo(f"  [-] {c.name}")


@cli.group()
def collection():
    """Gestión de colecciones (plugins): listar, habilitar, deshabilitar."""


@collection.command("list")
def collection_list():
    """Lista las colecciones descubiertas y su estado."""
    from infranix.core.registry import get_registry
    registry = get_registry().discover()
    rows = []
    for rec in registry.all():
        mark = "✗" if not registry.is_enabled(rec.name) else "✓"
        source = "entry-point" if rec.installed else "builtin"
        rows.append(click.style(f"{mark}", bold=True) + f" {rec.name} v{rec.version} [{source}]")
        caps = ", ".join(sorted(c.name for c in rec.capabilities))
        click.echo(f"  {rows[-1]}")
        click.echo(f"      capabilities: {caps}")
        if rec.description:
            click.echo(f"      {rec.description}")
    if not rows:
        click.echo("Ninguna colección descubierta.")


@collection.command("requirements")
@click.option("-f", "--file", "file_path", default="infra.yaml")
def collection_requirements(file_path: str):
    """Asegura que las colecciones declaradas en el manifiesto estén listas.

    Como ansible-galaxy: lee la sección 'collections' del YAML y, si falta
    alguna, la instala (pip o tar.gz) antes de que el core la use.
    """
    manifest = _load_manifest(file_path)
    if not manifest.collections:
        click.echo("El manifiesto no declara colecciones en la sección "
                   "'collections'. Nada que asegurar.")
        return
    from infranix.core.registry import get_registry
    registry = get_registry()
    messages = registry.ensure_required(manifest.collections)
    failed = False
    for m in messages:
        if m.startswith("ERROR"):
            failed = True
            click.echo(f"  ⚠  {m}")
        else:
            click.echo(f"  ✓ {m}")
    if failed:
        sys.exit(2)


@collection.command("init")
@click.argument("name", default="my_collection")
@click.option("-o", "--out", "out_dir", default=".")
def collection_init(name: str, out_dir: str):
    """Inicializa una colección, al estilo de ansible-galaxy init.

    Crea un esqueleto con requirements.yml + carpeta infra_declaration/
    (equivalente a tasks/) y un Provider que implementa el protocolo.
    """
    import infranix.skeleton as skeleton
    from pathlib import Path as _P
    skeleton.init_collection(name, _P(out_dir))


@collection.command("install-from-archive")
@click.argument("tarball")
@click.argument("name")
def collection_install_from_archive(tarball: str, name: str):
    """Instala una colección desde un tar.gz local (offline, sin internet).

    Descomprime e instala el paquete python dentro del tarball.
    """
    from infranix.core.registry import CollectionRegistry, get_registry
    get_registry()._install_archive(tarball, name)
    get_registry().discover()
    click.echo(f"Colección '{name}' instalada desde {tarball}.")


@collection.command("enable")
@click.argument("name")
def collection_enable(name: str):
    """Habilita una colección (p.ej. packer)."""
    from infranix.core.registry import get_registry
    registry = get_registry()
    if registry.enable(name):
        click.echo(f"Colección '{name}' habilitada.")
    else:
        raise click.ClickException(f"Colección '{name}' no encontrada. Ver 'infra collection list'.")


@collection.command("disable")
@click.argument("name")
def collection_disable(name: str):
    """Deshabilita una colección sin desinstalarla.

    El core sigue funcionando sin ella (p.ej. si Packer está roto).
    """
    from infranix.core.registry import get_registry
    registry = get_registry()
    if registry.disable(name):
        click.echo(f"Colección '{name}' deshabilitada. Core sigue operativo.")
    else:
        raise click.ClickException(f"Colección '{name}' no encontrada. Ver 'infra collection list'.")


@collection.command("install")
@click.argument("pkg")
def collection_install(pkg: str):
    """Instala una colección desde PyPI/GitHub (pip install).

    La colección debe declarar el entry point 'infranix.collections'.
    """
    import subprocess as sp
    click.echo(f"Instalando colección: {pkg}")
    res = sp.run([sys.executable, "-m", "pip", "install", pkg],
                 capture_output=True, text=True)
    if res.returncode != 0:
        raise click.ClickException(res.stderr[-1200:])
    get_registry().discover()
    click.echo("Instalada. Ver 'infra collection list'.")


@cli.group()
def image():
    """Gestión de imágenes/templates (Image Manager)."""


@image.command("ensure")
@click.option("-f", "--file", "file_path", default="infra.yaml")
@click.option("--name", "name", default=None, help="Nombre de imagen (default: todas del manifest)")
def image_ensure(file_path: str, name: str | None):
    """Asegura que las imágenes del manifiesto estén disponibles en el ESXi.

    Si una imagen no está, la descarga (mirror oficial) y la sube al datastore.
    Delega en la colección con capability IMAGE.
    """
    from infranix.pluginbase import Capability, PluginContext
    from infranix.core.registry import get_registry

    manifest = _load_manifest(file_path)
    config = load_config()

    from infranix.pluginbase import PluginContext as _PC
    scan_provider = get_registry().resolve(Capability.SCAN)
    if scan_provider is None:
        raise click.ClickException("Sin colección SCAN habilitada.")
    sctx = _PC(config=config)
    sreport = scan_provider.apply(sctx)
    if not sreport.ok:
        raise click.ClickException("; ".join(sreport.errors or [sreport.message]))
    inventory = sreport.data["inventory"]

    provider = get_registry().resolve(Capability.IMAGE)
    if provider is None:
        raise click.ClickException(
            "Ninguna colección con capability IMAGE habilitada.")
    for img in manifest.images:
        if name and img.name != name:
            continue
        click.echo(f"Imagen: {img.name} ({img.distro} {img.version})")
        single = Manifest(project=manifest.project,
                          hypervisor=manifest.hypervisor,
                          images=[img])
        ctx = PluginContext(config=config, manifest=single,
                            inventory=inventory)
        report = provider.apply(ctx)
        for line in report.message.splitlines():
            click.echo(f"  {line}")


@image.command("build")
@click.option("-f", "--file", "file_path", default="infra.yaml")
@click.option("--name", "name", default=None, help="Nombre de imagen (default: todas del manifest)")
def image_build(file_path: str, name: str | None):
    """Construye templates clonables con Packer desde el ISO en cache.

    Requiere que el ISO ya esté en la cache local (ver 'image ensure').
    Genera kickstart/preseed, ejecuta Packer y deja un template en el ESXi.
    Delega en la colección con capability BUILD.
    """
    from infranix.pluginbase import Capability, PluginContext
    from infranix.image_manager import ImageManager, ImageRecord
    manifest = _load_manifest(file_path)
    config = load_config()

    builder = get_registry().resolve(Capability.BUILD)
    if builder is None:
        raise click.ClickException(
            "Ninguna colección con capability BUILD habilitada (Packer).")
    im = ImageManager(config)
    for img in manifest.images:
        if name and img.name != name:
            continue
        click.echo(f"Imagen: {img.name} ({img.distro} {img.version})")
        iso = im.cache_dir / im._iso_local_name(img.name, img.distro, img.version)
        if not iso.exists():
            click.echo("  [!] ISO no en cache. Ejecuta 'infra image ensure' primero.")
            continue
        ctx = PluginContext(config=config, manifest=manifest, image=img,
                            extras={"iso_path": str(iso)})
        report = builder.apply(ctx)
        icon = "  [+] " if report.ok else "  [!!] "
        click.echo(f"{icon}{report.message}")


if __name__ == "__main__":
    cli()
