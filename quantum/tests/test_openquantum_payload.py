"""The reproducibility hash covers the bytes actually sent by SDK 1.2.1."""

import hashlib

import pytest
from openquantum_sdk.utils import strip_gate_definitions

from quantum.tests.test_openquantum_bridge import gateway, prepare


def test_manifest_hash_matches_sdk_uploaded_payload(tmp_path):
    service, bridge = gateway(tmp_path)
    ticket = prepare(bridge)
    uploaded = service.uploads[0]
    assert uploaded == strip_gate_definitions(uploaded)
    assert ticket.circuit_sha256 == hashlib.sha256(uploaded).hexdigest()
    assert (tmp_path / (ticket.circuit_sha256 + ".qasm")).read_bytes() == uploaded


def test_provider_circuit_validation_precedes_upload(tmp_path):
    service, bridge = gateway(tmp_path)
    backend = service.return_backend("test:qpu")

    def reject(circuit):
        raise ValueError("Provider rejected circuit")

    backend.validate_circuit = reject
    service.return_backend = lambda code: backend
    with pytest.raises(ValueError, match="Provider rejected circuit"):
        prepare(bridge)
    assert service.uploads == []
    assert service.created == []
