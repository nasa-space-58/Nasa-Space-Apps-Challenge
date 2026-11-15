"""Run the preregistered C3 local-only experiment: python -m app.run_stress_discovery."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import platform
import re

import numpy as np
import scipy

from .stress_discovery import campaign


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--revision', required=True, help='Source commit SHA supplied by the host')
    args = parser.parse_args()
    if not re.fullmatch(r'[0-9a-f]{40}', args.revision):
        parser.error('--revision must be a full lowercase commit SHA')
    root = Path(__file__).resolve().parents[2]
    result = campaign()
    files = ['backend/app/stress_discovery.py', 'backend/app/mission_resilience.py',
             'docs/stress-discovery-c3-protocol.md', 'shared/examples/recovery/combined_recovery.json']
    result['provenance'] = dict(
        completed_utc=datetime.now(timezone.utc).isoformat(),
        python=platform.python_version(), platform=platform.platform(), numpy=np.__version__, scipy=scipy.__version__,
        git_revision=args.revision,
        sha256={name: hashlib.sha256((root/name).read_bytes()).hexdigest() for name in files},
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, allow_nan=False)+'\n')
    print(json.dumps({k: result[k] for k in ('decision', 'replay_calls', 'total_s', 'credits_spent', 'summaries')}, indent=2))


if __name__ == '__main__':
    main()
