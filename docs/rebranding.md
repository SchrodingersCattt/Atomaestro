# Atomaestro name and package migration

**Current status:** The GitHub repository is **[Atomaestro](https://github.com/SchrodingersCattt/Atomaestro)** (formerly MatterVis). This checkout builds the PyPI distribution `atomaestro`. The command remains `mat-vis`. Python imports remain `mat_viewer`. Machine-facing contracts such as `mattervis.capabilities/v1`, `mattervis.tui.renderers`, and `mattervis.native-v1` are unchanged.

`atomaestro` is not published yet. The release on PyPI today is still [`matter-vis` 0.0.6](https://pypi.org/project/matter-vis/). Do not describe `atomaestro` as an installable release until that upload exists.

| Surface | What works today | What this repository now builds |
|---|---|---|
| GitHub | [`SchrodingersCattt/Atomaestro`](https://github.com/SchrodingersCattt/Atomaestro) | No further repository move |
| PyPI distribution | [`matter-vis` 0.0.6](https://pypi.org/project/matter-vis/) | `atomaestro`, after the next confirmed tag |
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

On 2026-11-22 the public JSON URLs `https://pypi.org/pypi/atomaestro/json` and `https://pypi.org/pypi/atomestro/json` both returned 404. That means no release is published under those names. It is not a reservation. The account owner should add the pending publisher before the first tag.

## Install

New installs, after `atomaestro` is on PyPI:

```bash
python -m pip install atomaestro
mat-vis --help
```

Until that release exists, the published package is:

```bash
python -m pip install "matter-vis==0.0.6"
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

Transition after the account owner has published `atomaestro`:

1. Keep using `atomaestro` for new installs.
2. Publish a later **empty** release of the existing `matter-vis` project whose only requirement is `atomaestro` at the same version. That release must ship no packages and no scripts.
3. Build that shim separately. Do not drop it into `dist/` beside the `atomaestro` wheel; the publish job uploads every distribution it finds, and the `atomaestro` publisher rejects a different project name.
4. Leave `matter-vis` able to accept that upload. [Archiving](https://docs.pypi.org/project-management/archiving-projects/) blocks new uploads, so archive only after the shim policy is finished. Do not delete the project.

Until the shim exists, `pip install -U matter-vis` stays on 0.0.6. It does not upgrade to `atomaestro`. Put `atomaestro` in a fresh environment, or uninstall `matter-vis` before installing it.

The shim itself is not in this change and is not published.

## Release workflow and trusted publisher

The publish job is `.github/workflows/release.yml`. It runs only for tags `v*` on `SchrodingersCattt/Atomaestro`, and `release-checks` rejects any tag that is not `vX.Y.Z`. The job uses the GitHub environment named `pypi` and `id-token: write` on that job only. `pypa/gh-action-pypi-publish@release/v1` is called with `attestations: true` (PEP 740). No PyPI token or password is stored in the workflow.

Register a **pending** publisher while creating the project ([creating a project through OIDC](https://docs.pypi.org/trusted-publishers/creating-a-project-through-oidc/)). The form fields, which must match the workflow exactly ([adding a publisher](https://docs.pypi.org/trusted-publishers/adding-a-publisher/)):

| PyPI field | Value |
|---|---|
| PyPI project name | `atomaestro` |
| Owner | `SchrodingersCattt` |
| Repository name | `Atomaestro` |
| Workflow filename | `release.yml` |
| Environment name | `pypi` |

The workflow file on disk is `.github/workflows/release.yml`. PyPI asks for the filename `release.yml`, not the directory. The GitHub environment `pypi` already exists on this repository and currently has no protection rules. Add a required reviewer, and limit the environment to the protected release tags, before the first publish. This repository change does not publish, tag, or bump a version.

[Publishing with a trusted publisher](https://docs.pypi.org/trusted-publishers/using-a-publisher/) requires `id-token: write`. [Attestations](https://docs.pypi.org/attestations/) are the PEP 740 publish attestation produced by the same OIDC identity. The action enables them by default; the workflow sets the input explicitly.

## Proposed next version

The published version is `0.0.6` (tag `v0.0.6`). No tag is created here. The proposed next release is **0.0.7**, because `mat-vis` and `mat_viewer` do not change. Confirm that version before tagging. If the distribution cut should be a minor boundary instead, the alternative is **0.1.0**.

## Still open

- Account owner creates the pending publisher and the GitHub environment reviewer gate.
- Confirmed tag publishes `atomaestro` and is verified by install.
- Separate empty `matter-vis` shim, only after that publish.
- Issue #316 items that are outside this change: a second CLI command, trademark review, dependent-pin updates, and the Discussions announcement.
