# Python dependency locks

`backend/requirements.txt` and `data-ingestion/requirements.txt` declare direct
requirements. The adjacent `requirements.lock` files pin the full dependency
graph and SHA-256 distribution hashes. Docker installs with `--require-hashes`
and runs `pip check`. No unpinned transitive application packages are resolved
at image build time. Python and bundled pip come from the base image.

Locks target Python 3.12 on Linux (the Docker runtime), not native macOS Python.
They were initially constrained to the previously tested container versions:
22 backend packages and 23 ingestion packages at the Step 0 baseline. Task 1 adds the planned messaging/terrain dependencies; current package counts are represented by the checked-in locks. Generating hashes for multiple
wheels does not establish that other platforms have been tested.

Regenerate after editing direct requirements:

```bash
./scripts/lock-python.sh
```

The disposable resolver container uses Python 3.12.12 and pip-tools 7.5.2. Existing
lock pins are reused when compatible. Deliberate transitive upgrades require
passing `--upgrade` or `--upgrade-package NAME` to pip-compile and reviewing the
diff. Regenerate both locks, rebuild both images, then run the full suite.
Do not hand-edit hashes. The script requires Docker and access to PyPI.

A lock makes dependency selection repeatable; it does not mean the packages
have undergone a Python security audit. Base image tags and operating-system
packages are outside these application locks.
