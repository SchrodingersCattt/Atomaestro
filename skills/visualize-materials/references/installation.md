# Install Atomaestro

The command is `mat-vis`. Python imports stay `mat_viewer`.

Install the renamed release in a fresh environment:

```bash
python -m pip install atomaestro
```

Do not install published `matter-vis` 0.0.6 and `atomaestro` into the same
environment. That release owns `mat_viewer` and the `mat-vis` script. After
`atomaestro` is published, uninstall `matter-vis` or use a new environment.
`pip install -U matter-vis` is not the migration. The migration notes are in
the repository file `docs/rebranding.md`. Add optional extras
only when required; see [capabilities and installation](./capabilities-and-install.md).

For requested Atomaestro source changes, install a checkout instead:

```bash
git clone https://github.com/SchrodingersCattt/Atomaestro.git
cd Atomaestro
python -m pip install .
```
