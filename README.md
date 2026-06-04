# tevind_deploy

Manage Frappe Docker deployments on an Ubuntu 24.04 OVH VPS, entirely over SSH.

This is the successor to the bash/Make-based `frappe_deploy`. The same Docker
runtime, compose stack and image-build approach are kept, but the workflow is now
driven by:

- a **unified Python CLI** (`tevind-deploy <command>`) that wraps a shared core,
- a single **YAML config** (`tevind_deploy.yaml`) instead of `apps.json` +
  hardcoded site mapping in bash,
- a `.env` file for **secrets only** (DB / admin passwords),
- an **MCP server** exposing the same operations so an agent on another app can
  drive deployments over SSH.

CLI and MCP are thin layers; all logic lives in `src/tevind_deploy/core/`.

## Architecture

```
tevind_deploy.yaml  ─┐
.env (secrets)       ─┼─►  core/ (compose, bench, image, sites, assets, sshkeys, deps)
CLI / MCP server     ─┘            └─► docker compose / bench / docker buildx
```

| Path | Purpose |
| ---- | ------- |
| `tevind_deploy.yaml` | Server, image, stack, apps, per-site app mapping, deploy keys |
| `.env` | `DB_ROOT_PASSWORD`, `SITE_ADMIN_PASSWORD`, `BUILD_KNOWN_HOSTS_PATH` |
| `compose/` | Frappe stack + MariaDB + Redis + NPM compose files |
| `containerfiles/` | Image build recipes (SSH-secret mounts injected per deploy key) |
| `resources/core/nginx/` | nginx template/entrypoint (reference copy, aligned with frappe_docker) |
| `src/tevind_deploy/` | The installable package (CLI, MCP server, core logic) |

## Setup (over SSH, on the VPS)

1. SSH in, create `~/tevind_deploy`, and copy/pull this folder there.
2. Bootstrap (creates a venv, installs the CLI, checks host dependencies):

```bash
cd ~/tevind_deploy
python3 tevind_deploy_setup.py
```

3. Create your config and secrets:

```bash
cp tevind_deploy.example.yaml tevind_deploy.yaml   # edit image, apps, sites
cp .env.example .env                               # set DB_ROOT_PASSWORD, SITE_ADMIN_PASSWORD
```

4. Sanity-check and supply any missing git deploy keys:

```bash
./tevind-deploy doctor
./tevind-deploy keys check
# provide a missing key (content from stdin), e.g.:
cat ~/secrets/tevind_deploy_key | ./tevind-deploy keys add tevind_deploy_key
ssh-keyscan github.com >> ~/.ssh/known_hosts
```

`./tevind-deploy` is a wrapper that runs the CLI from the venv, so everything
works as plain SSH commands without activating anything.

## Full provisioning from 0

On a fresh OVH Ubuntu 24.04 VPS the entire server can be set up over SSH. Fill
in `server.ip_address`, `dns`, `proxy` in `tevind_deploy.yaml`, and the OVH/NPM
credentials in `.env`, then:

```bash
./tevind-deploy bootstrap
```

This runs, in order: `provision` (apt base packages, Docker via get.docker.com,
UFW for the configured ports, a swapfile) -> `keys check` -> `build` -> `up` ->
`sites bootstrap` -> `npm up` -> `dns apply` + `dns verify` -> `proxy apply`.
Each stage has a `--skip-*` flag.

Important notes:

- Provisioning needs **passwordless sudo** (or run as root). Set
  `server.use_sudo: true`.
- After provisioning adds your user to the `docker` group, you must **reconnect
  via SSH** (new session) before Docker works without sudo. `bootstrap` detects
  this and stops with instructions; re-run with `--skip-provision` afterwards.
- DNS is applied **before** `proxy apply` so Let's Encrypt validation can
  succeed. Propagation may take a few minutes; re-run `proxy apply` if the first
  certificate request fails.

### Credentials (`.env`)

```ini
NPM_ADMIN_EMAIL=admin@tevind.com
NPM_ADMIN_PASSWORD=...
OVH_ENDPOINT=ovh-eu
OVH_APPLICATION_KEY=...
OVH_APPLICATION_SECRET=...
OVH_CONSUMER_KEY=...
```

OVH API credentials: create at https://api.ovh.com/createToken/ with
GET/POST/PUT/DELETE on `/domain/*`.

NPM ships with a default admin (`admin@example.com` / `changeme`) that it forces
you to change on first UI login (`http://<vps-ip>:81`). Do that once, then put
the new credentials in `.env` so `proxy apply` can authenticate.

### Granular commands

```bash
./tevind-deploy provision          # host packages, Docker, UFW, swap
./tevind-deploy dns apply          # create/update OVH A records -> server IP
./tevind-deploy dns verify         # dig check against server.ip_address
./tevind-deploy proxy apply        # NPM proxy hosts + Let's Encrypt + force SSL
./tevind-deploy proxy list         # show configured proxy hosts
```

## CLI commands

| Command | Old equivalent | Description |
| ------- | -------------- | ----------- |
| `setup [--install]` | README prereqs | Check (and optionally install) host deps |
| `provision` | README host hardening | Full host setup: packages, Docker, UFW, swap (sudo) |
| `bootstrap` | — | End-to-end setup from 0 (provision -> stack -> DNS -> proxy) |
| `doctor` | — | Validate config + deps + keys |
| `config show \| validate \| set KEY VALUE` | edit files | Inspect/modify config |
| `keys list \| check \| add NAME` | manual `ssh-add` | Manage git deploy keys |
| `build` | `make build-image` | Build the custom image |
| `up \| down \| ps \| logs \| restart` | `make up/down/ps/logs` | Stack lifecycle |
| `sites bootstrap \| add DOMAIN \| migrate \| list` | `make bootstrap-sites/add-site/migrate` | Site provisioning |
| `apps list --site X \| install --site X --app Y` | manual bench | Per-site app ops |
| `deploy-refresh` | `make deploy-refresh` | Build + up + migrate + sync assets |
| `sync-assets` | `make sync-assets` | Re-sync assets from current image |
| `npm up \| down \| ps \| logs` | `make npm-*` | Nginx Proxy Manager stack |
| `dns apply \| verify \| list` | manual `dig` | OVH DNS A/AAAA records |
| `proxy apply \| list` | manual NPM UI | NPM proxy hosts + Let's Encrypt (REST API) |
| `mcp serve` | — | Start the MCP server (stdio) |

Global options: `--root`, `--config`, `--env` (default to the current directory
/ `tevind_deploy.yaml` / `.env`).

## Config: choosing which site gets which apps

`apps:` lists every app baked into the image (rendered to `apps.json` at build
time). `sites:` maps each domain to the apps to install on it. Every app a site
references must exist in `apps:` (and therefore in the built image).

You can edit the YAML by hand **or** use the programmatic API (CLI / MCP), which
validates and saves automatically.

### Config CRUD (CLI)

| Resource | Commands | Key |
| -------- | -------- | --- |
| Image apps | `config apps list\|get\|add\|set\|remove` | app `name` |
| Sites | `config sites list\|get\|add\|set\|remove` | `domain` |
| Site install list | `config site-apps list\|get\|add\|set\|remove` | `domain` + `--app` |
| Deploy key metadata | `config ssh-keys list\|get\|add\|set\|remove` | key `name` |
| NPM overrides | `config proxy-hosts list\|get\|add\|set\|remove` | `domain` |
| Scalar settings | `config section get\|set`, or `config set image.custom_tag …` | `server`, `image`, `dns`, `proxy`, … |

Add `--json` on read commands for machine-readable output. Top-level `keys add`
still writes PEM files to disk; `config ssh-keys add` only updates YAML metadata.

**Example: only `dev.tevind.com` with two private apps**

```bash
# 1) SSH key entries (then: keys add <name> with PEM from stdin)
./tevind-deploy config ssh-keys add myapp1_deploy_key --host-alias github.com-myapp1
./tevind-deploy config ssh-keys add myapp2_deploy_key --host-alias github.com-myapp2

# 2) Apps in the image
./tevind-deploy config apps add tevind_app \
  --url git@github.com-myapp1:Org/repo_one.git --branch main --deploy-key myapp1_deploy_key
./tevind-deploy config apps add other_app \
  --url git@github.com-myapp2:Org/repo_two.git --branch main --deploy-key myapp2_deploy_key

# 3) Single site (replace any old sites first)
./tevind-deploy config sites remove 137.74.114.233   # if present
./tevind-deploy config sites add dev.tevind.com --apps tevind_app,other_app

# 4) Server IP + new image tag
./tevind-deploy config section set server ip_address '"137.74.114.233"'
./tevind-deploy config set image.custom_tag f16-dev-2026-06-04

# 5) Deploy
./tevind-deploy build && ./tevind-deploy up
./tevind-deploy sites add dev.tevind.com
./tevind-deploy dns apply && ./tevind-deploy proxy apply
```

Python API (same logic as CLI/MCP):

```python
from tevind_deploy.core.config_store import ConfigStore

store = ConfigStore.load("/home/ubuntu/tevind_deploy")
store.apps_add("tevind_app", "git@github.com-tevind:Org/repo.git", "main", "myapp1_deploy_key")
store.sites_add("dev.tevind.com", ["tevind_app", "other_app"])
store.save()
```

## Release flow (new app code, keep DB + sites)

```bash
./tevind-deploy config set image.custom_tag f16-$(date +%F)   # new tag per release
./tevind-deploy deploy-refresh                                 # build + up + migrate + assets
./tevind-deploy ps
```

`deploy-refresh` builds the image, recreates containers, migrates all configured
sites, rsyncs `sites/assets` from the in-image snapshot, clears per-site caches,
flushes **redis-cache only** (never redis-queue), and restarts the web services.

Guardrails (carried over from `frappe_deploy`):

- Never run `down --volumes` unless you intend to wipe DB / sites / redis.
- Migrate before stopping the stack.
- New app added to `apps:`? Rebuild the image first, then `apps install` (or
  re-run `sites bootstrap`) per site that needs it.

## Reverse proxy (NPM)

```bash
./tevind-deploy npm up        # start the NPM container stack
./tevind-deploy proxy apply   # create proxy hosts + Let's Encrypt via the API
```

`proxy apply` creates/updates one proxy host per domain forwarding to
`http://<server.ip_address>:<frontend_port>` with websocket upgrade enabled,
then requests a Let's Encrypt certificate and forces SSL. The first run requires
the NPM admin credentials in `.env` (see "Full provisioning from 0").

You can still manage hosts manually in the NPM UI if you prefer; `proxy apply`
is idempotent and updates existing hosts in place.

## MCP server (agent control)

```bash
./tevind-deploy mcp serve
```

Exposes tools backed by the same core functions: `get_config`, `list_sites`,
`list_apps`, `check_keys`, `add_key`, `build_image`, `deploy_refresh`,
`sync_assets`, `bootstrap_sites`, `add_site`, `migrate_all`, `stack_up`,
`stack_down`, `ps`, provisioning tools (`provision_host`, `dns_apply`, `proxy_apply`,
`bootstrap_all`), and **config CRUD** tools (`config_apps_*`, `config_sites_*`,
`config_site_apps_*`, `config_ssh_keys_*`, `config_section_*`). Missing deploy
keys can be supplied at runtime via `add_key`.

## Migration notes from frappe_deploy

- `apps.json` + the `apps_for_site()` case statements in `bootstrap-sites.sh` /
  `add-site.sh` are replaced by the `apps:` / `sites:` sections of the YAML.
- `.env` previously held image tags, versions and key paths; those now live in
  the YAML. `.env` keeps only secrets.
- The build's SSH deploy keys are no longer hardcoded in the Containerfile; they
  are injected from `ssh_keys:` / each app's `deploy_key`.
- `make` targets map 1:1 to CLI commands (see the table above).
