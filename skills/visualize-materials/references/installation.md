# Install Atomaestro

The command is `mat-vis`. Python imports stay `mat_viewer`.

Install the release that is on PyPI today:

```bash
python -m pip install "matter-vis==0.0.6"
```

`atomaestro` is the distribution name in this repository. It is not on PyPI.
After a confirmed release is actually published, and not before, the command
becomes:

```bash
python -m pip install atomaestro
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
