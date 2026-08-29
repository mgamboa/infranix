"""InfraNix CLI.

Commands:
  init    - generates ~/.infranix/.env with a template (to put credentials in)
  scan    - describes the current hypervisor infrastructure (read-only)
  plan    - computes the manifest vs current diff, shows the plan (no execution)
  apply   - executes the plan (re-validates the Safety Gate first)
  destroy - destructive operation; requires --yes + safety.destroy
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
        raise click.ClickException(f"Manifest not found: {path}")
    data = resolve_vars(data)
    return Manifest(**data)


@click.group()
def cli():
    """InfraNix — Declarative infrastructure orchestrator."""
    pass


@cli.command()
def init():
    """Generates ~/.infranix/.env with credentials (secure template)."""
    path = write_config_template()
    click.echo(f"Config template created at {path}")
    click.echo("Edit it with your ESXi credentials and then run 'infra scan'.")


@cli.command()
def scan():
    """Describe the current hypervisor infrastructure (read-only)."""
    config = load_config()
    if not config.configured and config.hypervisor != "mock":
        click.echo("No credentials. Run 'infra init' and edit ~/.infranix/.env, "
                   "or use INFRA_HYPERVISOR=mock for development.")
        sys.exit(1)

    from infranix.core.registry import get_registry
    from infranix.pluginbase import Capability, PluginContext
    scanner = get_registry().resolve(Capability.SCAN)
    if scanner is None:
        raise click.ClickException("No SCAN collection enabled.")
    click.echo(f"Scanning {config.hypervisor} @ {config.host or 'mock'}...")
    try:
        ctx = PluginContext(config=config)
        sreport = scanner.apply(ctx)
        if not sreport.ok:
            raise click.ClickException("; ".join(sreport.errors or [sreport.message]))
        inv = sreport.data["inventory"]
    except click.ClickException:
        raise
    except Exception as e:
        raise click.ClickException(f"Error scanning: {e}")

    click.echo(f"\nHost:  {inv.host}  ({inv.os_version})")
    click.echo(f"VMs:   {len(inv.vms)}")
    click.echo(f"Networks: {', '.join(inv.networks) or '-'}")
    click.echo(f"Datastores: {len(inv.datastores)}")
    for d in inv.datastores:
        click.echo(f"  - {d.name}: {d.free_bytes/1e9:.0f}GB free / "
                   f"{d.capacity_bytes/1e9:.0f}GB total")

    from tabulate import tabulate
    if inv.vms:
        rows = [[v.name, v.power_state, v.cpu, v.mem_mb, v.guest_os,
                 v.ip or "-"] for v in inv.vms]
        click.echo("\n" + tabulate(
            rows, headers=["Name", "Power", "vCPU", "RAM(MB)", "Guest OS", "IP"],
            tablefmt="simple"))
    if inv.images:
        click.echo(f"\nAvailable (ISO) images ({len(inv.images)}):")
        for img in inv.images:
            click.echo(f"  - {img}")


@cli.command()
@click.option("-f", "--file", "file_path", default="infra.yaml",
              help="Path to the YAML manifest (default: infra.yaml)")
@click.option("-o", "--out", "out_dir", default="out",
              help="Terraform/Ansible output directory (default: out)")
def plan(file_path: str, out_dir: str):
    """Compute the manifest vs current diff and show the plan (no execution).

    The scan and artifact generation are delegated to the
    SCAN/PROVISION/CONFIGURE collections via the orchestrator.
    """
    from infranix.app import InfraNix
    from infranix.core.planner import Planner, ChangeKind
    from infranix.core.registry import get_registry
    from infranix.pluginbase import Capability, PluginContext

    manifest = _load_manifest(file_path)
    config = load_config()

    # Scan via a SCAN collection
    scan_provider = get_registry().resolve(Capability.SCAN)
    if scan_provider is None:
        raise click.ClickException("No SCAN collection enabled.")
    sctx = PluginContext(config=config)
    sreport = scan_provider.apply(sctx)
    if not sreport.ok:
        raise click.ClickException("; ".join(sreport.errors or [sreport.message]))
    inventory = sreport.data["inventory"]

    planner = Planner(manifest, inventory)
    plan = planner.plan()
    gate = SafetyGate(manifest.safety)
    report = gate.evaluate(manifest, plan)

    click.echo(f"\nProject: {manifest.project}  |  hypervisor: {manifest.hypervisor.value}")
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
        click.echo("\n⚠  Missing images (will be downloaded/built):")
        for img in plan.images_missing:
            click.echo(f"    - {img}")

    click.echo("\n" + "=" * 60)
    click.echo(report.summary())
    if not report.allowed:
        click.echo("\nThe plan is BLOCKED by the Safety Gate. Review the policies "
                   "in the manifest before continuing.")
        sys.exit(2)

    # Artifact generation (delegated to PROVISION/CONFIGURE with apply=False)
    try:
        app = InfraNix(config)
        rep2 = app.run(file_path, out_dir=out_dir, apply=False)
        if rep2.errors:
            for e in rep2.errors:
                click.echo(f"\n⚠  {e}")
        else:
            click.echo("\nArtifacts generated in out/ (terraform + ansible).")
            click.echo("Run 'infra apply' to deploy (after re-validating safety).")
    except Exception as e:
        click.echo(f"\n⚠  Could not generate artifacts: {e}")

    click.echo("\nThe plan is APPROVED by the Safety Gate.")


@cli.command()
@click.option("-f", "--file", "file_path", default="infra.yaml")
@click.option("-o", "--out", "out_dir", default="out")
@click.option("--apply", is_flag=True,
              help="Apply the plan (runs Terraform/Ansible). Without it, only plans.")
@click.option("--report", "report_format", default="text",
              type=click.Choice(["text", "markdown"]),
              help="Report format (default: text).")
def run(file_path: str, out_dir: str, apply: bool, report_format: str):
    """Run the declarative YAML file end to end (the application).

    Validates, scans, plans, applies the Safety Gate and, with --apply, generates
    and runs Terraform + Ansible. Produces a report.
    """
    from infranix.app import InfraNix
    app = InfraNix()
    report = app.run(file_path, out_dir=out_dir, apply=apply)

    if report_format == "markdown":
        click.echo(report.to_markdown())
    else:
        click.echo(f"\nProject: {report.project} | hypervisor: {report.hypervisor}")
        click.echo(f"Plan: {report.plan_summary}")
        click.echo(f"Safety: {'APPROVED' if report.safety_approved else 'BLOCKED'}")
        for e in report.errors:
            click.echo(f"  ⚠  {e}")
        if not report.plan_summary:
            click.echo("  (no changes)")
        if report.images_ensured:
            click.echo("Images:")
            for i in report.images_ensured:
                click.echo(f"  - {i}")
        if report.provision_log:
            click.echo("Provision:")
            click.echo(report.provision_log.strip()[-1500:])

    if report.errors and not report.safety_approved:
        sys.exit(2)


@cli.command()
@click.option("-f", "--file", "file_path", default="infra.yaml")
@click.option("--yes", is_flag=True, help="Confirms destructive operations.")
@click.option("-o", "--out", "out_dir", default="out",
              help="Artifact output directory (default: out)")
@click.option("--skip-apply", is_flag=True,
              help="Generates artifacts but does NOT run Terraform/Ansible.")
def apply(file_path: str, yes: bool, out_dir: str, skip_apply: bool):
    """Execute the plan (re-validates the Safety Gate before acting).

    Delegates to the orchestrator (app.run), which in turn delegates to the
    SCAN/IMAGE/PROVISION/CONFIGURE collections. A failure in any collection
    stays confined and the report flags it.
    """
    from infranix.app import InfraNix
    app = InfraNix()

    # Re-validation of destructive operations before touching anything
    manifest = _load_manifest(file_path)
    from infranix.core.planner import Planner, ChangeKind
    from infranix.core.registry import get_registry
    from infranix.pluginbase import Capability, PluginContext
    scan_provider = get_registry().resolve(Capability.SCAN)
    if scan_provider is None:
        raise click.ClickException("No SCAN collection enabled.")
    sctx = PluginContext(config=load_config())
    inv = scan_provider.apply(sctx)
    if not inv.ok:
        raise click.ClickException(inv.message)
    plan = Planner(manifest, inv.data["inventory"]).plan()
    destructive = [c for c in plan.changes if c.kind == ChangeKind.DESTROY]
    if destructive and not yes:
        click.echo("Destructive operations require --yes and safety.destroy: true.")
        click.echo("Nothing was executed.")
        sys.exit(2)

    report = app.run(file_path, out_dir=out_dir, apply=not skip_apply)
    click.echo(f"\nPlan: {report.plan_summary}")
    click.echo(f"Safety: {'APPROVED' if report.safety_approved else 'BLOCKED'}")
    for img in report.images_ensured:
        click.echo(f"  - {img}")
    if report.provision_log:
        click.echo("Provision:")
        click.echo(f"  {report.provision_log}")
    if report.configure_log:
        click.echo("Configuration:")
        click.echo(f"  {report.configure_log}")
    for e in report.errors:
        click.echo(f"  ⚠  {e}")
    if report.errors and not report.safety_approved:
        sys.exit(2)


@cli.command()
@click.option("-f", "--file", "file_path", default="infra.yaml")
@click.option("--yes", is_flag=True, help="Mandatory confirmation to destroy.")
def destroy(file_path: str, yes: bool):
    """Destroy resources declared with action: destroy. VERY VERY careful."""
    from infranix.core.planner import Planner, ChangeKind
    from infranix.core.registry import get_registry
    from infranix.pluginbase import Capability, PluginContext

    manifest = _load_manifest(file_path)
    config = load_config()
    scan_provider = get_registry().resolve(Capability.SCAN)
    if scan_provider is None:
        raise click.ClickException("No SCAN collection enabled.")
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
        click.echo("Destruction BLOCKED by the Safety Gate. Nothing was executed.")
        sys.exit(2)

    destroys = [c for c in plan.changes if c.kind == ChangeKind.DESTROY]
    if not destroys:
        click.echo("There are no resources to destroy.")
        return
    if not yes:
        click.echo("To destroy you must confirm with --yes. Nothing was executed.")
        sys.exit(3)
    if not manifest.safety.destroy:
        click.echo("The manifest does not have 'safety.destroy: true'. Nothing was executed.")
        sys.exit(4)

    click.echo(f"Destroying {len(destroys)} resource(s)... Phase 0: simulated only.")
    for c in destroys:
        click.echo(f"  [-] {c.name}")


@cli.group()
def collection():
    """Collection management (plugins): list, enable, disable."""


@collection.command("list")
def collection_list():
    """List discovered collections and their state."""
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
        click.echo("No collections discovered.")


@collection.command("requirements")
@click.option("-f", "--file", "file_path", default="infra.yaml")
def collection_requirements(file_path: str):
    """Ensure the collections declared in the manifest are ready.

    Like ansible-galaxy: reads the 'collections' section of the YAML and, if
    any is missing, installs it (pip or tar.gz) before the core uses it.
    """
    manifest = _load_manifest(file_path)
    if not manifest.collections:
        click.echo("The manifest does not declare collections in the "
                   "'collections' section. Nothing to ensure.")
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
    """Initialize a collection, in the style of ansible-galaxy init.

    Creates a skeleton with requirements.yml + infra_declaration/ folder
    (equivalent to tasks/) and a Provider that implements the protocol.
    """
    import infranix.skeleton as skeleton
    from pathlib import Path as _P
    skeleton.init_collection(name, _P(out_dir))


@collection.command("install-from-archive")
@click.argument("tarball")
@click.argument("name")
def collection_install_from_archive(tarball: str, name: str):
    """Install a collection from a local tar.gz (offline, no internet).

    Unpacks and installs the python package inside the tarball.
    """
    from infranix.core.registry import CollectionRegistry, get_registry
    get_registry()._install_archive(tarball, name)
    get_registry().discover()
    click.echo(f"Collection '{name}' installed from {tarball}.")


@collection.command("enable")
@click.argument("name")
def collection_enable(name: str):
    """Enable a collection (e.g. packer)."""
    from infranix.core.registry import get_registry
    registry = get_registry()
    if registry.enable(name):
        click.echo(f"Collection '{name}' enabled.")
    else:
        raise click.ClickException(f"Collection '{name}' not found. See 'infra collection list'.")


@collection.command("disable")
@click.argument("name")
def collection_disable(name: str):
    """Disable a collection without uninstalling it.

    The core keeps working without it (e.g. if Packer is broken).
    """
    from infranix.core.registry import get_registry
    registry = get_registry()
    if registry.disable(name):
        click.echo(f"Collection '{name}' disabled. Core keeps operating.")
    else:
        raise click.ClickException(f"Collection '{name}' not found. See 'infra collection list'.")


@collection.command("install")
@click.argument("pkg")
def collection_install(pkg: str):
    """Install a collection from PyPI/GitHub (pip install).

    The collection must declare the 'infranix.collections' entry point.
    """
    import subprocess as sp
    click.echo(f"Installing collection: {pkg}")
    res = sp.run([sys.executable, "-m", "pip", "install", pkg],
                 capture_output=True, text=True)
    if res.returncode != 0:
        raise click.ClickException(res.stderr[-1200:])
    get_registry().discover()
    click.echo("Installed. See 'infra collection list'.")


@cli.group()
def image():
    """Image/template management (Image Manager)."""


@image.command("ensure")
@click.option("-f", "--file", "file_path", default="infra.yaml")
@click.option("--name", "name", default=None, help="Image name (default: all from the manifest)")
def image_ensure(file_path: str, name: str | None):
    """Ensure the manifest images are available on the ESXi.

    If an image is missing it downloads it (official mirror) and uploads it to
    the datastore. Delegates to the collection with the IMAGE capability.
    """
    from infranix.pluginbase import Capability, PluginContext
    from infranix.core.registry import get_registry

    manifest = _load_manifest(file_path)
    config = load_config()

    from infranix.pluginbase import PluginContext as _PC
    scan_provider = get_registry().resolve(Capability.SCAN)
    if scan_provider is None:
        raise click.ClickException("No SCAN collection enabled.")
    sctx = _PC(config=config)
    sreport = scan_provider.apply(sctx)
    if not sreport.ok:
        raise click.ClickException("; ".join(sreport.errors or [sreport.message]))
    inventory = sreport.data["inventory"]

    provider = get_registry().resolve(Capability.IMAGE)
    if provider is None:
        raise click.ClickException(
            "No collection with the IMAGE capability enabled.")
    for img in manifest.images:
        if name and img.name != name:
            continue
        click.echo(f"Image: {img.name} ({img.distro} {img.version})")
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
@click.option("--name", "name", default=None, help="Image name (default: all from the manifest)")
def image_build(file_path: str, name: str | None):
    """Build cloneable templates with Packer from the cached ISO.

    Requires the ISO to already be in the local cache (see 'image ensure').
    Generates kickstart/preseed, runs Packer and leaves a template on the ESXi.
    Delegates to the collection with the BUILD capability.
    """
    from infranix.pluginbase import Capability, PluginContext
    from infranix.image_manager import ImageManager, ImageRecord
    manifest = _load_manifest(file_path)
    config = load_config()

    builder = get_registry().resolve(Capability.BUILD)
    if builder is None:
        raise click.ClickException(
            "No collection with the BUILD capability enabled (Packer).")
    im = ImageManager(config)
    for img in manifest.images:
        if name and img.name != name:
            continue
        click.echo(f"Image: {img.name} ({img.distro} {img.version})")
        iso = im.cache_dir / im._iso_local_name(img.name, img.distro, img.version)
        if not iso.exists():
            click.echo("  [!] ISO not in cache. Run 'infra image ensure' first.")
            continue
        ctx = PluginContext(config=config, manifest=manifest, image=img,
                            extras={"iso_path": str(iso)})
        report = builder.apply(ctx)
        icon = "  [+] " if report.ok else "  [!!] "
        click.echo(f"{icon}{report.message}")


if __name__ == "__main__":
    cli()
