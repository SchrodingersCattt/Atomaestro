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

On 2026-11-23 the public JSON URLs `https://pypi.org/pypi/atomaestro/json` and `https://pypi.org/pypi/atomestro/json` both returned 404. No release is published under those names. A pending publisher was configured on 2026-11-22 at 13:09 for project `atomaestro`, owner `SchrodingersCattt`, repository `Atomaestro`, workflow filename `release.yml`, and environment `pypi`. That registration matches the workflow. It does not reserve the name until the first successful publish. Those 404 responses are not a trademark search, a domain registration check, or a legal clearance.

## Install

Install the package that is on PyPI today:

```bash
python -m pip install "matter-vis==0.0.6"
mat-vis --help
```

`atomaestro` is not published. No release version has been chosen. After a
confirmed release is actually on PyPI, and not before, the command becomes:

```bash
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

The local forwarder is `.github/scripts/build_matter_vis_forwarder.py`. It reads one built `atomaestro` wheel and writes an empty `matter-vis` wheel and sdist of that same final version. The base requirement is `atomaestro==<version>`. Each extra on the source wheel becomes `atomaestro[<extra>]==<version>`. The forwarder has no packages, no modules, and no console scripts, so it does not own `mat_viewer` or `mat-vis`.

Build it outside `dist/`. The release job uploads every file under `dist/` and the metadata check requires the project name `atomaestro`, so a forwarder placed there fails the check:

```bash
python -m build --no-isolation --outdir /tmp/atomaestro-dist
python .github/scripts/build_matter_vis_forwarder.py \
  /tmp/atomaestro-dist/atomaestro-*.whl \
  --outdir /tmp/matter-vis-forwarder
```

The script refuses a version that is not a final `X.Y.Z` release. No release version is chosen here. The command above is a local build. It does not upload.

Transition after the account owner has published `atomaestro`:

1. Keep using `atomaestro` for new installs.
2. Publish a later empty release of the existing `matter-vis` project from that forwarder, at the same version as the published `atomaestro`. Upload it with the existing `matter-vis` project, not the `atomaestro` publisher.
3. Leave `matter-vis` able to accept that upload. [Archiving](https://docs.pypi.org/project-management/archiving-projects/) blocks new uploads, so archive only after the forwarder policy is finished. Do not delete the project.

Until that upload exists, `pip install -U matter-vis` on PyPI stays on 0.0.6. It does not upgrade to `atomaestro`. Put `atomaestro` in a fresh environment, or uninstall `matter-vis` before installing it.

After both wheels exist locally, pip still deletes overlapping paths when `atomaestro` is installed before the old `matter-vis` record is removed. The old record is what pip deletes. On this fixture those paths are `mat_viewer/__init__.py` and `mat-vis`. The console script is then gone. `import mat_viewer` loads a namespace package and the `capabilities` function from `__init__.py` is absent. Files the predecessor did not record, such as `mat_viewer/cli.py`, stay on disk and can still be imported. Both distributions still show as installed. The same deletion is what a dependency-first `pip install --upgrade matter-vis` does, and that command also follows the MolCrysKit git pin. Checked through pip 26.3. This fixture is not the public 0.0.6 file list, and the test does not download that release.

The local order that keeps the files names `matter-vis` before `atomaestro` and uses `--no-deps`, so pip does not reorder the packages or fetch the git pin:

```bash
python -m pip install --upgrade --no-index --find-links /tmp/wheelhouse \
  --no-deps matter-vis atomaestro
```

The test installs the synthetic predecessor, runs its `mat-vis`, then runs `mat-vis capabilities --json` and imports `mat_viewer` from the virtual environment. It reads the installed `mat_viewer/__init__.py` again and compares those bytes with the `atomaestro` wheel. Uninstalling the forwarder leaves the command and the import in place. Uninstalling `atomaestro` while the forwarder remains removes the command and the modules. None of this is a public upgrade.

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

## PyPI upload is blocked on the MolCrysKit direct reference

`pyproject.toml` still requires:

```text
molcrys-kit[desktop] @ git+https://github.com/SchrodingersCattt/MolCrysKit.git@2a3ee11e4c708b64691181cc2cbc4b2a3625f0c6
```

That commit, dated 2026-11-20, is 30 commits after tag `v0.8.0` (`4aa65179`, 2026-10-23). It is the authorized runtime pin. This change does not replace it.

Public PyPI rejects that requirement. PEP 440 says a public index should not allow direct references in an uploaded distribution, and Warehouse returns `Can't have direct dependency` for `Requires-Dist` values whose requirement URL is set ([warehouse #7136](https://github.com/pypi/warehouse/issues/7136), `warehouse.forklift.metadata`). A green build does not make the upload valid.

Checked again on 2026-11-23, the newest PyPI release and the newest final GitHub tag are still `molcrys-kit` 0.8.0 (`v0.8.0`, 2026-10-23). The published wheel's `BondRecord` has no `role` field. The wheel has no `get_connectivity_report`, no `read_mryx`, and no `desktop` extra. Its extras are `vis`, `test`, and `dev`. That release does not provide the pinned contract:

| API | `v0.8.0` | commit `2a3ee11` |
|---|---|---|
| `BondRecord.role` | absent | present |
| `MolecularCrystal.get_connectivity_report` | absent | present |
| `read_mryx` / `read_mryx_atoms` | absent | present |
| `desktop` extra (`defusedxml`, used by `read_mryx`) | absent | present |

There is no released version, and therefore no normal `molcrys-kit>=X,<Y` range, that can replace the pin. Do not fall back to 0.8.0.

`.github/scripts/reject_direct_dependencies.py` fails the release workflow when a built wheel or sdist contains a direct reference, when the canonical metadata lists no `Requires-Dist` at all, when the tag is not `vX.Y.Z`, and when `--project atomaestro` does not match the artifact name. The release commands pass that project name. The wheel built with the pinned toolchain carries the git URL above in `METADATA`. The matching sdist records that same URL in `PKG-INFO` and in `requires.txt`. All three fail the check. The release build job runs it before uploading artifacts. That job already installs `build`, `setuptools`, and `setuptools-scm` under `requirements/ci-constraints.txt`, which pins `packaging==26.3`, before the script. The PyPI job and the GitHub Release job install that same pinned `packaging` before they run the script again, then publish. A tag push of this tree fails that check. That is the release block. Pull-request CI feeds the wheel and sdist it just built to the same script and expects rejection, so the blocked release does not fail the pull request. The test also builds those artifacts and accepts a wheel whose `Requires-Dist` is a normal version requirement.

Replacing the pin requires a MolCrysKit release that the user confirms by version. This pull request does not create that release. The confirmed release has to include the contract above and install on Windows as a normal PyPI requirement, because Atomaestro's Windows job and a public install cannot keep a git URL. Until that release exists, do not publish `atomaestro`.

## Version

The published Atomaestro predecessor is `matter-vis` 0.0.6. No next version is approved. `0.1.0` remains the proposal for the first `atomaestro` release because PyPI treats that name as a new project, not as a patch of `matter-vis` 0.0.6. It is not the chosen tag. The forwarder, when it is later published, has to use that same version string. Do not tag from this change.

## Still open

- The pending publisher is configured. The GitHub environment `pypi` reviewer and tag limits are unchanged.
- A user-confirmed MolCrysKit release replaces the direct URL, and the metadata check then passes.
- A chosen tag publishes `atomaestro` and is verified by install.
- Separate publication of the empty `matter-vis` forwarder, only after that publish. The forwarder can be built locally. It is not on PyPI.
- No second command. `atomestro` in issue #316 is a stale spelling and is not a console script. The command remains `mat-vis`. The help banner still says `MatterVis`; changing that string is a separate decision.
- Name, domain, and trademark review. The PyPI 404s above are not that review and are not a commercial signoff.
- Dependent-pin updates after `atomaestro` is actually on PyPI.
- The Discussions announcement. A draft is below. It has not been posted.

## Announcement draft

This draft is not posted.

The GitHub repository is SchrodingersCattt/Atomaestro. The command remains `mat-vis`. Python imports remain `mat_viewer`. The package on PyPI today is still `matter-vis` 0.0.6. This repository builds `atomaestro`, which is not published yet. An empty `matter-vis` forwarder can be built from a local wheel and is not published. No new command was added. This note is not a release announcement.
