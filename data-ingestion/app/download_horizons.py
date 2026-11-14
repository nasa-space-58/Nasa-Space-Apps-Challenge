"""Command-line entry point for the ingestion container."""
import argparse
import json
import os
from pathlib import Path

import httpx
from shared.contracts import AnalysisRequest
from app.horizons import download


def main() -> None:
    """Read a validated request JSON file and print its completed cache reference.

    Invalid input, HTTP errors and invalid/corrupt data exit nonzero with details.
    DATA_DIR defaults to the ingestion container's shared /data volume.
    """
    parser = argparse.ArgumentParser(description='Download and cache lunar Sun/Earth ephemerides')
    parser.add_argument('request', type=Path, help='AnalysisRequest JSON file')
    parser.add_argument('--data-dir', type=Path, default=Path(os.environ.get('DATA_DIR', '/data')))
    args = parser.parse_args()
    request = AnalysisRequest.model_validate_json(args.request.read_text())
    with httpx.Client() as client:
        result = download(request, args.data_dir, client)
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
