"""The actual app factory owns the new replay service and exposes its routes."""
from fastapi.testclient import TestClient

from app.analysis_api import create_app
from test_mission_resilience_api import command, result


def test_real_app_registers_resilience_and_preserves_other_endpoints(tmp_path):
    app = create_app(state_dir=tmp_path / 'state', data_dir=tmp_path / 'data', start_worker=False)
    with TestClient(app) as client:
        examples = client.get('/resilience/examples')
        assert examples.status_code == 200
        assert len(examples.json()) >= 5
        job = client.post('/resilience/runs', json=command().model_dump(mode='json')).json()
        assert app.state.resilience.tick(runner=result)
        assert client.get('/resilience/runs/' + job['id']).json()['status'] == 'completed'
        assert client.get('/health').status_code == 200
        assert client.get('/unified/sessions').status_code == 200
