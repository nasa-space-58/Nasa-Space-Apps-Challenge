#!/bin/sh
# Resolve Python 3.12/Linux locks in the same base image as the services.
set -eu
cd "$(dirname "$0")/.."
docker run --rm -v "$PWD:/workspace" -w /workspace python:3.12.12-slim sh -ec '
  pip install --quiet pip-tools==7.5.2
  for service in backend data-ingestion; do
    pip-compile --quiet --generate-hashes --strip-extras --no-header --no-annotate \
      --index-url https://pypi.org/simple --no-emit-index-url --no-emit-trusted-host \
      --output-file "$service/requirements.lock" "$service/requirements.txt"
  done
'
