"""CLI de InfraNix.

Comandos:
  init    - genera ~/.infranix/.env con plantilla (para poner credenciales)
  scan    - describe la infraestructura actual del hypervisor (solo lectura)
  plan    - calcula el diff manifest vs actual, muestra el plan (no ejecuta)
  apply   - ejecuta el plan (re-valida Safety Gate antes)
  destroy - operación destructiva; requiere --yes + safety.destroy
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import click
import yaml

from infranix.config import load_config, write_config_template
from infranix.adapters.discovery import make_scanner
from infranix.core.planner import Planner, ChangeKind
from infranix.core.safety import SafetyGate
from infranix.models import Manifest
from infranix.terraform_gen import TerraformGenerator
from infranix.ansible_gen import AnsibleGenerator
from infranix.image_manager import ImageManager


def _load_manifest(path: str) -> Manifest:
    try:
        with open(path, "r") as f:
            data = yaml.safe_load(f)
    except FileNotFoundError:
        raise click.ClickException(f"Manifiesto no encontrado: {path}")
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

    scanner = make_scanner(config)
    click.echo(f"Escaneando {config.hypervisor} @ {config.host or 'mock'}...")
    try:
        inv = scanner.scan()
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
    """Calcula el diff manifest vs actual y muestra el plan (no ejecuta)."""
    manifest = _load_manifest(file_path)
    config = load_config()
    scanner = make_scanner(config)
    try:
        inventory = scanner.scan()
    except Exception as e:
        raise click.ClickException(f"Error escaneando: {e}")

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

    # Generación de artefactos (solo si el plan es aprobado)
    try:
        datastore = inventory.datastores[0].name if inventory.datastores else "delldatastore"
        cluster_host = inventory.compute_cluster_host or "dellserver01.itmco.local"
        tf_gen = TerraformGenerator(
            manifest,
            Path(out_dir) / "terraform",
            datastore=datastore,
            compute_cluster="/ha-datacenter/host/" + cluster_host,
        )
        tf_dir = tf_gen.generate()
        ans_dir = AnsibleGenerator(manifest, Path(out_dir)).generate()
        click.echo(f"\nArtefactos generados:")
        click.echo(f"  Terraform: {tf_dir}")
        click.echo(f"  Ansible:   {ans_dir}")
        click.echo("\nEjecuta 'infra apply' para desplegar (tras re-validar seguridad).")
    except Exception as e:
        click.echo(f"\n⚠  No se pudieron generar artefactos: {e}")

    click.echo("\nEl plan está APROBADO por el Safety Gate.")


@cli.command()
@click.option("-f", "--file", "file_path", default="infra.yaml")
@click.option("-o", "--out", "out_dir", default="out")
@click.option("--apply", is_flag=True,
              help="Aplica el plan (ejecuta Terraform/Ansible). Sin esto, solo planifica.")
@click.option("--yes", is_flag=True, help="Confirma operaciones destructivas.")
@click.option("--report", "report_format", default="text",
              type=click.Choice(["text", "markdown"]),
              help="Formato del reporte (def: text).")
def run(file_path: str, out_dir: str, apply: bool, yes: bool, report_format: str):
    """Corre el archivo YAML declarativo de principio a fin (la aplicación).

    Valida, escanea, planea, aplica el Safety Gate y, con --apply, genera
    y ejecuta Terraform + Ansible. Produce un reporte.
    """
    from infranix.app import InfraNix
    app = InfraNix()
    report = app.run(file_path, out_dir=out_dir, apply=apply, yes=yes)

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
        if report.terraform_log:
            click.echo("Terraform ejecutado (últimas líneas):")
            click.echo(report.terraform_log.strip()[-1500:])

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
    """Ejecuta el plan (re-valida Safety Gate antes de actuar)."""
    manifest = _load_manifest(file_path)
    config = load_config()
    scanner = make_scanner(config)
    try:
        inventory = scanner.scan()
    except Exception as e:
        raise click.ClickException(f"Error escaneando: {e}")

    planner = Planner(manifest, inventory)
    plan = planner.plan()
    gate = SafetyGate(manifest.safety)
    report = gate.evaluate(manifest, plan)

    if not report.allowed:
        click.echo("BLOQUEADO por Safety Gate. Nada se ejecutó.")
        click.echo(report.summary())
        sys.exit(2)

    destructive = [c for c in plan.changes if c.kind == ChangeKind.DESTROY]
    if destructive and not yes:
        click.echo("Operaciones destructivas requieren --yes y safety.destroy: true.")
        click.echo("Nada se ejecutó.")
        sys.exit(2)

    click.echo("Safety Gate APROBADO. Aplicando plan...")

    # 1) Asegurar imágenes (Image Manager)
    if manifest.images:
        click.echo("\n[1/3] Asegurando imágenes...")
        im = ImageManager(config)
        for img in manifest.images:
            result = im.ensure(img.name, img.distro, img.version,
                               available_remotes=inventory.images)
            click.echo(f"  - {img.name}: {result.message}")

    # 2) Generar Terraform + Ansible
    datastore = inventory.datastores[0].name if inventory.datastores else "delldatastore"
    cluster_host = inventory.compute_cluster_host or "dellserver01.itmco.local"
    tf_dir = Path(out_dir) / "terraform"
    TerraformGenerator(
        manifest, tf_dir,
        datastore=datastore,
        compute_cluster="/ha-datacenter/host/" + cluster_host,
    ).generate()
    AnsibleGenerator(manifest, Path(out_dir)).generate()
    click.echo(f"\n[2/3] Artefactos generados: {tf_dir} y {Path(out_dir)}/ansible")

    if skip_apply:
        click.echo("\n[3/3] --skip-apply indicado: no se ejecuta Terraform/Ansible.")
        click.echo("Revisa los artefactos en out/ y ejecuta manualmente:")
        click.echo(f"  cd {tf_dir} && terraform plan/apply")
        click.echo(f"  ansible-playbook -i {Path(out_dir)}/ansible/inventory/hosts.yml "
                   f"{Path(out_dir)}/ansible/playbooks/site.yml")
        return

    # 3) Terraform init + apply
    click.echo("\n[3/3] Ejecutando Terraform...")
    _run_terraform(tf_dir, config)
    click.echo("\nTerraform completado.")



def _run_terraform(tf_dir: Path, config):
    """Ejecuta terraform init + apply con las credenciales del .env."""
    env = dict(os.environ)
    env["TF_VAR_vsphere_user"] = config.user or ""
    env["TF_VAR_vsphere_password"] = (config.password or "").replace("%29", ")")
    env["TF_VAR_vsphere_server"] = config.host or ""
    env["TF_VAR_vsphere_insecure"] = "true"

    def _run(cmd):
        click.echo(f"\n$ {' '.join(cmd)}")
        res = subprocess.run(cmd, cwd=str(tf_dir), env=env,
                             capture_output=True, text=True, timeout=600)
        click.echo(res.stdout[-3000:] if res.stdout else "")
        if res.returncode != 0:
            click.echo(res.stderr[-2000:])
            raise click.ClickException(
                f"Terraform falló ({cmd[1]}):\n{res.stderr[-800:]}")
        return res

    _run(["terraform", "init", "-input=false", "-upgrade"])
    # apply con auto-approve (ya validamos Safety Gate)
    _run(["terraform", "apply", "-auto-approve",
          f"-var=vsphere_user={env['TF_VAR_vsphere_user']}",
          f"-var=vsphere_password={env['TF_VAR_vsphere_password']}",
          f"-var=vsphere_server={env['TF_VAR_vsphere_server']}"])


@cli.command()
@click.option("-f", "--file", "file_path", default="infra.yaml")
@click.option("--yes", is_flag=True, help="Confirmación obligatoria para destruir.")
def destroy(file_path: str, yes: bool):
    """Destruye recursos declarados con action: destroy. MUY MUY cuidadoso."""
    manifest = _load_manifest(file_path)
    config = load_config()
    scanner = make_scanner(config)
    inventory = scanner.scan()

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
def image():
    """Gestión de imágenes/templates (Image Manager)."""


@image.command("ensure")
@click.option("-f", "--file", "file_path", default="infra.yaml")
@click.option("--name", "name", default=None, help="Nombre de imagen (default: todas del manifest)")
def image_ensure(file_path: str, name: str | None):
    """Asegura que las imágenes del manifiesto estén disponibles en el ESXi.

    Si una imagen no está, la descarga (mirror oficial) y la sube al datastore.
    """
    manifest = _load_manifest(file_path)
    config = load_config()

    from infranix.adapters.discovery import make_scanner
    scanner = make_scanner(config)
    inventory = scanner.scan()

    im = ImageManager(config)
    for img in manifest.images:
        if name and img.name != name:
            continue
        click.echo(f"Imagen: {img.name} ({img.distro} {img.version})")
        result = im.ensure(img.name, img.distro, img.version,
                           available_remotes=inventory.images)
        icon = {"none": "[=]", "uploaded": "[+]", "template-required": "[★]",
                "downloading": "[↓]"}.get(result.action, "[?]")
        click.echo(f"  {icon} {result.message}")


if __name__ == "__main__":
    cli()
