import unittest
from uuid import UUID
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from jobagent.mobile.observability import WorkflowTelemetry
from jobagent.privacy_logging import PrivacyFormatter


class WorkflowTelemetryTests(unittest.TestCase):
    def test_logs_server_template_status_and_duration_not_private_data(self):
        app = FastAPI()
        app.add_middleware(WorkflowTelemetry)
        @app.post('/api/mobile/jobs/{job_id}/prepare')
        def prepare(job_id: str):
            raise HTTPException(503, 'Provider unavailable; retry later.')
        with TestClient(app) as client, self.assertLogs('jobagent.mobile.workflow', level='INFO') as log:
            result = client.post('/api/mobile/jobs/private-job/prepare?token=private-query',
                json={'resume':'private-resume'}, headers={'Authorization':'Bearer private-bearer','X-Request-ID':'private-client-id'})
        event = PrivacyFormatter().format(log.records[0])
        self.assertIn('operation=document_generation', event)
        self.assertIn('status=503', event)
        self.assertIn('duration_ms=', event)
        self.assertIn('request_id=' + str(UUID(result.headers['x-request-id'])), event)
        self.assertNotIn('private-', event)
        self.assertNotIn('/api/', event)

    def test_unknown_paths_are_not_logged(self):
        app = FastAPI()
        app.add_middleware(WorkflowTelemetry)
        with TestClient(app) as client, self.assertLogs('jobagent.mobile.workflow', level='INFO') as log:
            self.assertEqual(client.get('/secret-email-or-token').status_code, 404)
        event = PrivacyFormatter().format(log.records[0])
        self.assertIn('operation=workspace', event)
        self.assertNotIn('secret-email', event)

    def test_private_formatter_rejects_injected_workflow_fields(self):
        import logging
        for operation, request_id in [('private-resume', '00000000-0000-4000-8000-000000000001'), ('assessment', 'private-secret')]:
            record = logging.LogRecord('jobagent.mobile.workflow', 30, '', 1,
                'workflow_request operation=%s method=%s status=%s duration_ms=%s request_id=%s',
                (operation,'POST',500,1,request_id), None)
            event = PrivacyFormatter().format(record)
            self.assertNotIn('private-', event)
            self.assertIn('event=diagnostic', event)


if __name__ == '__main__': unittest.main()
