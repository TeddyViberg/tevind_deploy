"""Build the custom Frappe image.

Port of ``scripts/build-image.sh``, but made generic:

* ``apps.json`` is rendered from ``config.apps`` (not a static file).
* the build SSH config and the per-key ``--secret`` mounts are generated from
  ``config.ssh_keys`` (instead of hardcoded tevind/autospine keys), by patching
  the Containerfile's ``# >>> TEVIND_SSH_SECRET_MOUNTS <<<`` marker.
"""

from __future__ import annotations

import base64
import os
from pathlib import Path

from ..config import Config, SSHKeyConfig
from .runner import OutputCollector, run

SECRET_MARKER = "# >>> TEVIND_SSH_SECRET_MOUNTS <<<"
GENERATED_DIR = ".generated"


def _keys_in_use(cfg: Config) -> list[SSHKeyConfig]:
    """Deploy keys actually referenced by apps, plus any with a host alias."""
    names: list[str] = []
    for app in cfg.apps:
        if app.deploy_key and app.deploy_key not in names:
            names.append(app.deploy_key)
    keys: list[SSHKeyConfig] = []
    for name in names:
        key = cfg.key_by_name(name)
        if key is None:
            raise ValueError(
                f"App references unknown deploy_key '{name}'. "
                f"Add it under ssh_keys in the config."
            )
        keys.append(key)
    return keys


def render_build_ssh_config(cfg: Config, keys: list[SSHKeyConfig]) -> str:
    """SSH config used inside the build, mapping host aliases to mounted keys."""
    blocks: list[str] = []
    for key in keys:
        if not key.host_alias:
            continue
        blocks.append(
            "\n".join(
                [
                    f"Host {key.host_alias}",
                    f"  HostName {key.hostname}",
                    f"  HostKeyAlias {key.hostname}",
                    "  User git",
                    f"  IdentityFile /run/secrets/{key.name}",
                    "  IdentitiesOnly yes",
                ]
            )
        )
    return "\n\n".join(blocks) + ("\n" if blocks else "")


def render_containerfile(cfg: Config, keys: list[SSHKeyConfig]) -> str:
    template_path = cfg.root / cfg.image.containerfile
    if not template_path.exists():
        raise FileNotFoundError(f"Containerfile not found: {template_path}")
    template = template_path.read_text()
    if SECRET_MARKER not in template:
        raise ValueError(
            f"Containerfile {template_path} is missing marker '{SECRET_MARKER}'"
        )
    mount_lines = [
        f"  --mount=type=secret,id={key.name},required=true,uid=1000,gid=1000,mode=0400 \\"
        for key in keys
    ]
    replacement = "\n".join(mount_lines) if mount_lines else ""
    out_lines = []
    for line in template.splitlines():
        if line.strip() == SECRET_MARKER:
            if replacement:
                out_lines.append(replacement)
            # else: drop the marker line entirely
        else:
            out_lines.append(line)
    return "\n".join(out_lines) + "\n"


def build_image(cfg: Config, capture: bool = False) -> str:
    out = OutputCollector(capture=capture)
    keys = _keys_in_use(cfg)

    # 1) validate key files + known_hosts exist.
    known_hosts = Path(
        os.path.expanduser(cfg.env.get("BUILD_KNOWN_HOSTS_PATH", "~/.ssh/known_hosts"))
    )
    if not known_hosts.exists():
        raise FileNotFoundError(
            f"Missing known_hosts: {known_hosts}. "
            f"Run: ssh-keyscan github.com >> ~/.ssh/known_hosts"
        )
    for key in keys:
        if not key.resolved_path().exists():
            raise FileNotFoundError(
                f"Missing deploy key '{key.name}': {key.resolved_path()}. "
                f"Add it with: tevind-deploy keys add {key.name}"
            )

    gen_dir = cfg.root / GENERATED_DIR
    gen_dir.mkdir(parents=True, exist_ok=True)

    # 2) render apps.json from config.
    cfg.apps_json_path.write_text(cfg.render_apps_json())
    out.status(f"Wrote {cfg.apps_json_path} ({len(cfg.apps)} apps)")

    # 3) render the build ssh config.
    build_ssh_config = gen_dir / "build_ssh_config"
    build_ssh_config.write_text(render_build_ssh_config(cfg, keys))

    # 4) render the Containerfile with per-key secret mounts.
    rendered_containerfile = gen_dir / "Containerfile.rendered"
    rendered_containerfile.write_text(render_containerfile(cfg, keys))

    apps_json_b64 = base64.b64encode(cfg.apps_json_path.read_bytes()).decode("ascii")

    # 5) vendor the frappe_docker build context.
    vendor_dir = cfg.root / ".vendor" / "frappe_docker"
    if not (vendor_dir / ".git").exists():
        vendor_dir.parent.mkdir(parents=True, exist_ok=True)
        out.status(f"Cloning {cfg.image.frappe_docker_repo} -> {vendor_dir}")
        out.add(run(["git", "clone", cfg.image.frappe_docker_repo, str(vendor_dir)],
                    capture=capture))
    out.add(run(["git", "-C", str(vendor_dir), "fetch", "--tags", "--prune"], capture=capture))
    out.add(run(["git", "-C", str(vendor_dir), "checkout", cfg.image.frappe_docker_ref],
                capture=capture))

    # 6) assemble the buildx command.
    cmd = ["docker", "buildx", "build", "--load"]
    if cfg.image.force_no_cache:
        cmd.append("--no-cache")
    if cfg.image.force_pull:
        cmd.append("--pull")
    # Forward the SSH agent when available (keys are also mounted as secrets).
    if os.environ.get("SSH_AUTH_SOCK"):
        cmd += ["--ssh", "default"]
    cmd += [
        "--secret", f"id=build_ssh_config,src={build_ssh_config}",
        "--secret", f"id=build_known_hosts,src={known_hosts}",
    ]
    for key in keys:
        cmd += ["--secret", f"id={key.name},src={key.resolved_path()}"]
    cmd += [
        "--build-arg", f"FRAPPE_PATH={cfg.image.frappe_path}",
        "--build-arg", f"FRAPPE_BRANCH={cfg.image.frappe_branch}",
        "--build-arg", f"PYTHON_VERSION={cfg.image.python_version}",
        "--build-arg", f"NODE_VERSION={cfg.image.node_version}",
        "--build-arg", f"APPS_JSON_BASE64={apps_json_b64}",
        "-f", str(rendered_containerfile),
        "-t", f"{cfg.image.custom_image}:{cfg.image.custom_tag}",
        str(vendor_dir),
    ]

    out.status(f"Building {cfg.image.custom_image}:{cfg.image.custom_tag} ...")
    out.add(run(cmd, env={**os.environ}, capture=capture))
    out.status(f"Built image: {cfg.image.custom_image}:{cfg.image.custom_tag}")
    return out.text()
