# Atomaestro name and package migration

**Current status:** The GitHub repository is **[Atomaestro](https://github.com/SchrodingersCattt/Atomaestro)** (formerly MatterVis). This checkout builds the PyPI distribution `atomaestro`. The command remains `mat-vis`. Python imports remain `mat_viewer`. Machine-facing contracts such as `mattervis.capabilities/v1`, `mattervis.tui.renderers`, and `mattervis.native-v1` are unchanged.

Check [`atomaestro` on PyPI](https://pypi.org/project/atomaestro/) for release availability. The old [`matter-vis` 0.0.6](https://pypi.org/project/matter-vis/) remains available under its original name.

| Surface | Previous distribution and interfaces | What this repository now builds |
|---|---|---|
| GitHub | [`SchrodingersCattt/Atomaestro`](https://github.com/SchrodingersCattt/Atomaestro) | No further repository move |
| PyPI distribution | [`matter-vis` 0.0.6](https://pypi.org/project/matter-vis/) | [`atomaestro`](https://pypi.org/project/atomaestro/) |
| CLI | `mat-vis` | `mat-vis` only. No second command is added |
| Python imports | `mat_viewer` | `mat_viewer` |
| Plugin contract | `mattervis.tui.renderers` / `mattervis.native-v1` | Same entry-point group and name |

Tracked by [issue #316](https://github.com/SchrodingersCattt/Atomaestro/issues/316). That issue stays open until a release is published and the remaining checklist items are done. Its draft spelling `atomestro` is not the distribution name; the project name is `atomaestro`.

## Why this is a new PyPI project

PyPI does not rename `matter-vis` to `atomaestro`.

- [PEP 503](https://peps.python.org/pep-0503/#normalized-names) normalizes a name by lowercasing it and collapsing runs of `.`, `-`, and `_` to a single `-`. `matter-vis` and `atomaestro` stay different names.
- PyPI can retitle a project only when the new spelling has the **same** normalized name ([warehouse #1919](https://github.com/pypi/warehouse/issues/1919), maintainer conclusion on the normalization-only case). An arbitrary rename is not supported.
- [Choosing a project name](https://docs.pypi.org/project-management/choosing-a-name/) treats a different name as a new project. [Troubleshooting](https://docs.pypi.org/project-management/troubleshooting/) says deleting a project is permanent and releases the name. Do not delete `matter-vis`.
- A [pending trusted publisher](https://docs.pypi.org/trusted-publishers/creating-a-project-through-oidc/) creates the new project on first publish. It does not reserve the name before that upload. If another user registers `atomaestro` first, the pending publisher is invalidated.

On 2026-11-23 the public JSON URLs `https://pypi.org/pypi/atomaestro/json` and `https://pypi.org/pypi/atomestro/json` both returned 404 at that time. A pending publisher was configured on 2026-11-22 at 13:09 for project `atomaestro`, owner `SchrodingersCattt`, repository `Atomaestro`, workflow filename `release.yml`, and environment `pypi`. That registration matches the workflow. It does not reserve the name until the first successful publish. Those 404 responses are not a trademark search, a domain registration check, or a legal clearance.

## Install

The legacy package remains available:

```bash
python -m pip install "matter-vis==0.0.6"
mat-vis --help
```

For the renamed release, use a new environment or first uninstall the old
package:

```bash
python -m pip uninstall -y matter-vis
python -m pip install atomaestro
mat-vis --help
```

Source checkout of this repository:

```bash
git clone https://github.com/SchrodingersCattt/Atomaestro.git
cd Atomaestro
python -m pip install -e .
```

`mat-vis capabilities` prints install commands for `atomaestro`, because that is the distribution name of this checkout.

Existing clones should use the canonical remote:

```bash
git remote set-url origin https://github.com/SchrodingersCattt/Atomaestro.git
```

GitHub redirects the old repository path as long as that path is not reused. The old PyPI project URL does not redirect.

## One tree must not publish two copies of the same files

`atomaestro` owns `mat_viewer`, the `mat-vis` console script, and the `mattervis.native-v1` entry point. The release workflow uploads only that distribution.

Do not also build a full `matter-vis` wheel from this tree. Pip would then have two projects recording the same `mat_viewer` files and the same `mat-vis` script. Installing both into one environment overwrites those files and leaves two owners.

Do not publish an empty `matter-vis` wheel that depends on `atomaestro`. Pip installs dependencies before it removes the old distribution. The old `matter-vis` record lists the installed `mat_viewer` files, and pip also removes the `mat-vis` console script from that distribution's entry points. Those paths are deleted after `atomaestro` has written them. No package metadata or constraint changes that order. Naming `matter-vis` before `atomaestro` on one `pip install --no-deps` command avoids the deletion only because `--no-deps` stops the resolver from installing the dependency first. That command is not a supported upgrade, and it skips the MolCrysKit requirement.

`.github/scripts/build_matter_vis_forwarder.py` refuses to write the wheel. The release check rejects an artifact whose name is `matter-vis`, including when `--project matter-vis` is passed. [Archiving](https://docs.pypi.org/project-management/archiving-projects/) the existing project is a separate decision. Do not delete `matter-vis`.

Until `atomaestro` is on PyPI, `pip install -U matter-vis` stays on 0.0.6. After it is published, install it in a new environment, or uninstall the old distribution first:

```bash
python -m pip uninstall -y matter-vis
python -m pip install atomaestro
mat-vis --help
```

The repository test uses a synthetic predecessor. Its `mat_viewer` paths are the 320 paths in the public 0.0.6 wheel record, and the bytes are not that wheel. It does not download 0.0.6. Uninstalling that predecessor and then installing the local `atomaestro` wheel leaves a working `mat-vis` and a `mat_viewer` tree whose bytes match the new wheel. A new environment with only that wheel does the same. Installing `atomaestro` and then upgrading `matter-vis` deletes the recorded paths and the command. Uninstalling both and installing `atomaestro` again restores them. A second test installs pip 24.0 and pip 26.3 and sees the same deletion and the same restore. It does not cover the releases between those two versions.

## Release workflow and trusted publisher

The publish job is `.github/workflows/release.yml`. It runs only for tags `v*` on `SchrodingersCattt/Atomaestro`, and `release-checks` rejects any tag that is not `vX.Y.Z`. The job uses the GitHub environment named `pypi` and `id-token: write` on that job only. `pypa/gh-action-pypi-publish@release/v1` is called with `attestations: true` (PEP 740). No PyPI token or password is stored in the workflow.

The pending publisher is configured ([creating a project through OIDC](https://docs.pypi.org/trusted-publishers/creating-a-project-through-oidc/)). The form fields match the workflow ([adding a publisher](https://docs.pypi.org/trusted-publishers/adding-a-publisher/)):

| PyPI field | Value |
|---|---|
| PyPI project name | `atomaestro` |
| Owner | `SchrodingersCattt` |
| Repository name | `Atomaestro` |
| Workflow filename | `release.yml` |
| Environment name | `pypi` |

The workflow file on disk is `.github/workflows/release.yml`. PyPI asks for the filename `release.yml`, not the directory. The GitHub environment `pypi` already exists. This change does not add a required reviewer and does not limit the environment to release tags. Those settings stay a separate decision. This repository change does not publish, tag, or bump a version.

[Publishing with a trusted publisher](https://docs.pypi.org/trusted-publishers/using-a-publisher/) requires `id-token: write`. [Attestations](https://docs.pypi.org/attestations/) are the PEP 740 publish attestation produced by the same OIDC identity. The action enables them by default; the workflow sets the input explicitly.

## MolCrysKit dependency and release gate

The Atomaestro 0.0.7 release candidate declares
`molcrys-kit[desktop]>=0.9.0,<0.10.0`. CI tests against `molcrys-kit==0.9.0` and
the release workflow rejects direct references in the metadata of the wheel
and sdist. The upload mechanism is unchanged.

Historically the project used MolCrysKit commit
`2a3ee11e4c708b64691181cc2cbc4b2a3625f0c6` before the API was released.
That entry records the original integration and is not a runtime
dependency. Do not use MolCrysKit 0.8.0 for this release.

## Version

The predecessor is `matter-vis` 0.0.6. The renamed release candidate is `atomaestro` 0.0.7; verify upstream compatibility and the package build before tagging.

## Still open

- The pending publisher is configured. The GitHub environment `pypi` reviewer and tag limits are unchanged.
- Verify the published versions and a clean installation after release.
- A chosen tag publishes `atomaestro` and is verified by install.
- An empty `matter-vis` forwarder is not published. Automatic `pip install -U matter-vis` is not a supported migration.
- No second command. `atomestro` in issue #316 is a stale spelling and is not a console script. The command remains `mat-vis`. The help banner still says `MatterVis`; changing that string is a separate decision.
- Name, domain, and trademark review. The PyPI 404s above are not that review and are not a commercial signoff.
- Dependent-pin updates after `atomaestro` is actually on PyPI.
- The Discussions announcement. A draft is below. It has not been posted.

## Announcement draft

This draft is not posted.

The GitHub repository is SchrodingersCattt/Atomaestro. The command remains `mat-vis`. Python imports remain `mat_viewer`. The preceding distribution was `matter-vis` 0.0.6. This repository builds `atomaestro`. Move an existing install by uninstalling `matter-vis` and then installing `atomaestro`, or by using a new environment. No new command was added. This note is not a release announcement.
