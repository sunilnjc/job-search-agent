"""Owner-scoped generation evidence through the actual API; synthetic cloud only."""
import copy
import unittest
from contextlib import ExitStack
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

from jobagent.mobile import studio
from jobagent.mobile.app import create_app
import test_document_review as evidence_tests
from test_mobile_api import FakeSupabase, SETTINGS, USER_A, USER_B, fake_studio


class DocumentReviewAPITests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.remote = FakeSupabase()
        self.adapter = fake_studio()
        self.client = self.stack.enter_context(TestClient(create_app(
            settings=SETTINGS, transport=httpx.MockTransport(self.remote), studio=self.adapter)))
        self.client.headers['Authorization'] = 'Bearer session-a'
        self.remote.add('profiles', USER_A, display_name='Synthetic Candidate')
        self.remote.add('candidate_context', USER_A, career_text='Designed accessible tools.', career_background={})
        self.job = self.remote.job(USER_A)
        _, documents = evidence_tests.DocumentReviewTests().prepared()
        self.documents = documents
        self.row = self.remote.add('model_runs', USER_A, job_id=self.job['id'],
            operation='prepare_documents', status='succeeded',
            output_summary={'document_review': copy.deepcopy(documents.review)})

    def read(self, job=None):
        return self.client.get('/api/mobile/jobs/' + (job or self.job)['id'] + '/document-reviews')

    def test_saved_snapshot_not_current_profile_is_returned_without_AI(self):
        self.remote.tables['candidate_context'][0]['career_text'] = 'A newer fact that must not replace old evidence.'
        result = self.read()
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json()['reviews'][0]['review'], self.documents.review)
        self.assertEqual(result.json()['reviews'][0]['model_run_id'], self.row['id'])
        self.assertEqual(result.json()['unavailable_count'], 0)
        self.adapter.prepare_documents.assert_not_called()
        self.adapter.rank_job.assert_not_called()

    def test_foreign_job_and_account_cannot_read_evidence(self):
        foreign = self.remote.job(USER_B)
        self.assertEqual(self.read(foreign).status_code, 404)
        self.client.headers['Authorization'] = 'Bearer session-b'
        self.assertEqual(self.read().status_code, 404)

    def test_legacy_or_corrupt_evidence_is_unavailable_not_all_clear(self):
        for summary in (None, {}, {'document_review': {}}, {'document_review': {'private': 'SENTINEL'}}):
            with self.subTest(summary=summary):
                self.row['output_summary'] = summary
                result = self.read()
                self.assertEqual(result.status_code, 200)
                self.assertEqual(result.json()['reviews'], [])
                self.assertEqual(result.json()['unavailable_count'], 1)
                self.assertNotIn('SENTINEL', result.text)

    def test_present_invalid_evidence_blocks_before_artifact_storage(self):
        source = self.remote.resume(USER_A)
        self.documents.review['snapshot']['sources'][0]['text'] = 'Uncaptured replacement'
        self.adapter.prepare_documents.return_value = self.documents
        result = self.client.post('/api/mobile/jobs/' + self.job['id'] + '/prepare', json={'resume_id': source['id']})
        self.assertEqual(result.status_code, 502, result.text)
        self.assertEqual(self.remote.tables['artifacts'], [])
        self.assertNotIn('Uncaptured replacement', result.text)


if __name__ == '__main__':
    unittest.main()
