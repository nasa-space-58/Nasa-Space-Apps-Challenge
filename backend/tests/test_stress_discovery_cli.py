"""The reproducible runner must work in the locked backend image without Git."""
import json
import sys

import pytest

from app import run_stress_discovery as cli


def test_explicit_revision_and_provenance_without_git(tmp_path, monkeypatch):
    output = tmp_path / 'nested' / 'evidence.json'
    monkeypatch.setattr(sys, 'argv', ['runner', '--revision', 'a'*40, '--output', str(output)])
    monkeypatch.setenv('PATH', '')
    monkeypatch.setattr(cli, 'campaign', lambda: dict(decision='NO_GO', replay_calls=0,
                                                    total_s=0, credits_spent=0, summaries={}))
    cli.main()
    data = json.loads(output.read_text())
    assert data['provenance']['git_revision'] == 'a'*40
    assert len(data['provenance']['sha256']['backend/app/stress_discovery.py']) == 64
    assert data['credits_spent'] == 0


@pytest.mark.parametrize('revision', ['invalid', 'a'*39])
def test_invalid_revision_fails_before_campaign(tmp_path, monkeypatch, revision):
    monkeypatch.setattr(sys, 'argv', ['runner', '--revision', revision, '--output', str(tmp_path/'out.json')])
    monkeypatch.setattr(cli, 'campaign', lambda: pytest.fail('must validate before running campaign'))
    with pytest.raises(SystemExit):
        cli.main()
