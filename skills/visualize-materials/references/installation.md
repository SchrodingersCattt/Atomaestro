# Install Atomaestro

The command is `mat-vis`. Python imports stay `mat_viewer`.

This repository's distribution name is `atomaestro`:

```bash
python -m pip install atomaestro
```

That project is not on PyPI until the next confirmed release. The published
release today is still [`matter-vis` 0.0.6](https://pypi.org/project/matter-vis/):

```bash
python -m pip install "matter-vis==0.0.6"
```

Do not install `matter-vis` and `atomaestro` into the same environment. Both
full distributions own `mat_viewer` and the `mat-vis` script. See
[the migration guide](../../../docs/rebranding.md). Add optional extras only
when required; see [capabilities and installation](./capabilities-and-install.md).

For requested Atomaestro source changes, install a checkout instead:

```bash
git clone https://github.com/SchrodingersCattt/Atomaestro.git
cd Atomaestro
python -m pip install .
```
