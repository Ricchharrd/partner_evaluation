import json
import unittest
from io import BytesIO
from urllib.error import HTTPError
from unittest.mock import patch

from partner_finance.ai import extract_facts_from_text
from partner_finance.finance_batch import plan_chunks, merge_facts, CHUNK_BYTES
from partner_finance.openai_provider import OpenAIProvider, APIRequestError, api_error, duration_seconds
from partner_finance.schema import SourceDocument, FinancialFact


class FakeProvider(OpenAIProvider):
    def __init__(self, failure=None):
        self.manifests = []
        super().__init__('test-key', approval=self.manifests.append)
        self.calls = []
        self.failure = failure

    def request(self, body):
        body = self.normalized_body(body)
        self.approval(body)
        self.calls.append(body)
        if self.failure:
            exc = self.failure(len(self.calls))
            if exc:
                raise exc
        return {'status': 'completed', 'usage': {'input_tokens': 10, 'output_tokens': 2},
                'output': [{'type': 'message', 'content': [{'type': 'output_text',
                    'text': json.dumps({'facts': [], 'warnings': []})}]}]}


class FinanceBatchTests(unittest.TestCase):
    def setUp(self):
        self.text = ''.join(f'[PAGE {i}]\n' + 'Public business description. ' * 600 + '\n' for i in range(1, 4))
        self.source = SourceDocument('public.pdf', 'PDF', sha256='doc-hash')

    def run_extract(self, provider, state):
        with patch('partner_finance.finance_batch.time.sleep'):
            return extract_facts_from_text(self.text, 'entity', self.source, provider, batch_state=state)

    def test_full_coverage_unicode_and_priority(self):
        text = '[PAGE 1]\n' + '일반 공개 사업 설명\n' * 3000
        text += '[PAGE 2]\nCompany strategy\n[PAGE 3]\nConsolidated balance sheet\nRevenue 100'
        chunks = plan_chunks(text)
        spans = sorted(span for c in chunks for span in c['spans'])
        self.assertEqual(spans[0][0], 0)
        self.assertEqual(spans[-1][1], len(text))
        self.assertTrue(all(a[1] == b[0] for a, b in zip(spans, spans[1:])))
        self.assertTrue(all(len(c['text'].encode()) <= CHUNK_BYTES for c in chunks))
        self.assertTrue(chunks[0]['priority'])
        self.assertEqual(''.join(text[a:b] for a,b in spans), text)
        self.assertEqual(plan_chunks(text), chunks)

    def test_partial_failure_resumes_only_remaining_chunks(self):
        provider = FakeProvider(lambda i: RuntimeError('timeout: billing uncertain') if i == 2 else None)
        state = {}
        with self.assertRaisesRegex(RuntimeError, '1/3'):
            self.run_extract(provider, state)
        self.assertEqual(state['status'], 'paused')
        self.assertEqual(len(state['completed']), 1)
        provider.failure = None
        _, _, meta = self.run_extract(provider, state)
        self.assertEqual(len(provider.calls), 4)
        self.assertEqual(provider.manifests[-1]['batch']['pending_chunks'], 2)
        self.assertEqual(meta['resumed_chunks'], 1)
        self.assertEqual(meta['usage']['input_tokens'], 20)
        self.assertEqual(meta['cumulative_usage']['input_tokens'], 30)
        self.assertEqual(state['status'], 'completed')
        _, _, cached = self.run_extract(provider, state)
        self.assertTrue(cached['cache_hit'])
        self.assertEqual(len(provider.calls), 4)

    def test_one_retry_for_temporary_limit_only(self):
        provider = FakeProvider(lambda i: APIRequestError('temporary', code='rate_limit_exceeded', retry_after=2) if i == 1 else None)
        state = {}
        self.run_extract(provider, state)
        self.assertEqual(len(provider.calls), 4)
        self.assertEqual(provider.calls[0], provider.calls[1])
        self.assertEqual(provider.manifests[0]['batch']['max_requests'], 6)

    def test_large_failed_chunk_splits_only_after_new_approval(self):
        provider = FakeProvider(lambda i: APIRequestError('large', code='rate_limit_exceeded', too_large=True) if i == 2 else None)
        state = {}
        with self.assertRaises(RuntimeError):
            self.run_extract(provider, state)
        self.assertTrue(state['can_split'])
        kept = set(state['completed'])
        state['split_requested'] = True
        provider.failure = None
        _, _, meta = self.run_extract(provider, state)
        self.assertTrue(kept <= set(state['completed']))
        self.assertGreater(meta['chunks'], 3)
        self.assertEqual(meta['resumed_chunks'], 1)
        self.assertEqual(len(provider.manifests), 2)
        self.assertEqual(provider.manifests[-1]['batch']['completed_chunks'], 1)

    def test_changed_source_or_scope_never_reuses_completed_job(self):
        provider, state = FakeProvider(), {}
        self.run_extract(provider, state)
        self.source.sha256 = 'different-file'
        self.run_extract(provider, state)
        self.assertEqual(len(provider.calls), 6)

    def test_split_page_preserves_fact_locator_validation(self):
        from partner_finance.ai import extract_facts_from_text
        text = '[PAGE 9]\nIncome statement\n' + 'Public filler\n' * 2000 + 'Revenue 100\n'
        chunks = plan_chunks(text)
        tail = next(c['text'] for c in chunks if 'Revenue 100' in c['text'])
        provider = FakeProvider()
        row = {'fiscal_year':2025,'standard_item':'revenue','original_label':'Revenue',
               'original_value':100,'unit_multiplier':1,'currency':'EUR','reporting_scope':'연결',
               'source_locator':'PAGE 9','evidence_quote':'Revenue 100'}
        with patch.object(provider, 'generate_json', return_value=({'facts':[row], 'warnings':[]}, {'model':'test'})):
            facts, _, _ = extract_facts_from_text(tail, 'e', self.source, provider, _chunk=True)
        self.assertEqual(len(facts), 1)

    def test_no_retry_for_quota_large_context_timeout_or_long_delay(self):
        errors = [APIRequestError('quota', code='insufficient_quota'),
                  APIRequestError('large', code='rate_limit_exceeded', too_large=True),
                  APIRequestError('context', code='context_length_exceeded'),
                  APIRequestError('wait', code='rate_limit_exceeded', retry_after=120), RuntimeError('timeout')]
        for error in errors:
            provider = FakeProvider(lambda i: error)
            with self.assertRaises(RuntimeError):
                self.run_extract(provider, {})
            self.assertEqual(len(provider.calls), 1)

    def test_retry_bounded_and_approval_restored(self):
        provider = FakeProvider(lambda i: APIRequestError('temporary', code='rate_limit_exceeded'))
        original = provider.approval
        with self.assertRaises(RuntimeError):
            self.run_extract(provider, {})
        self.assertEqual(len(provider.calls), 2)
        self.assertEqual(provider.approval, original)

    def test_no_calls_before_consent_and_exact_request_enforcement(self):
        provider = FakeProvider()
        def deny(body):
            raise ValueError('approval required')
        provider.approval = deny
        with self.assertRaisesRegex(ValueError, 'approval required'):
            self.run_extract(provider, {})
        self.assertEqual(provider.calls, [])
        provider.approval = provider.manifests.append
        body = provider.json_body('Extract JSON', {'text': 'public'})
        with provider.approved_batch([body], {'max_requests': 2}):
            with self.assertRaises(ValueError):
                provider.request({**body, 'max_output_tokens': 6001})
            provider.request(body)
            provider.request(body)
            with self.assertRaises(ValueError):
                provider.request(body)

    def test_secret_in_later_chunk_blocks_entire_plan(self):
        self.text += '\n[PAGE 4]\nINTERNAL ONLY'
        provider = FakeProvider()
        with self.assertRaisesRegex(ValueError, '보안 차단'):
            self.run_extract(provider, {})
        self.assertEqual(provider.calls, [])
        self.assertEqual(provider.manifests, [])

    def test_merge_removes_duplicates_but_quarantines_conflicts(self):
        def fact(value, locator):
            return FinancialFact('e', 2025, 'revenue', 'Revenue', value, value, 'EUR',
                                 reporting_scope='연결', source_locator=locator)
        merged, conflicts = merge_facts([fact(100,'PAGE 1'), fact(100,'PAGE 2')])
        self.assertEqual(len(merged), 1)
        self.assertIn('PAGE 2', merged[0].source_locator)
        self.assertFalse(conflicts)
        merged, conflicts = merge_facts([fact(100,'PAGE 1'), fact(200,'PAGE 2')])
        self.assertFalse(merged)
        self.assertEqual(len(conflicts[0]['candidates']), 2)

    def test_error_classification_never_echoes_private_text(self):
        for code, message, expected in [('rate_limit_exceeded','Request too large SECRET',True),
                                         ('insufficient_quota','SECRET',False)]:
            exc = HTTPError('https://api.openai.com/v1/responses',429,'limited',{'Retry-After':'45'},
                            BytesIO(json.dumps({'error':{'code':code,'message':message}}).encode()))
            result = api_error(exc)
            self.assertNotIn('SECRET', str(result))
            self.assertEqual(result.code, code)
            self.assertEqual(result.too_large, expected)
            self.assertEqual(result.retry_after, 45)

    def test_pacing_uses_remaining_capacity_and_server_resets(self):
        provider = FakeProvider()
        body = provider.json_body('Extract', {'text':'public'}, 6000)
        self.assertEqual(provider.pause_before(body), 20)
        with patch('partner_finance.openai_provider.time.time', return_value=100):
            provider.record_rate_headers({'x-ratelimit-remaining-tokens':'1','x-ratelimit-reset-tokens':'1m2s'})
            self.assertEqual(provider.pause_before(body), 62)
            provider.record_rate_headers({'x-ratelimit-remaining-tokens':'99999','x-ratelimit-reset-tokens':'1m'})
            self.assertEqual(provider.pause_before(body), 0)
        self.assertIsNone(duration_seconds('SECRET'))
        self.assertEqual(duration_seconds('500ms'), .5)

    def test_http_date_retry_after_is_honored(self):
        exc = HTTPError('https://api.openai.com',429,'limited',{'Retry-After':'Thu, 01 Jan 1970 00:02:00 GMT'},
                        BytesIO(b'{"error":{"code":"rate_limit_exceeded"}}'))
        with patch('partner_finance.openai_provider.time.time', return_value=100):
            self.assertEqual(api_error(exc).retry_after, 20)

    def test_conflicts_are_included_in_internal_handoff_as_unresolved(self):
        from partner_finance.schema import AnalysisProject, EntityProfile
        from partner_finance.handoff import packet_content
        from partner_finance.handoff import build_claude_start
        project = AnalysisProject('Test', EntityProfile('Test'))
        project.narrative['analysis_route'] = 'public_financials'
        conflict = {'year':2025,'item':'revenue','candidates':[]}
        project.narrative['api_usage'] = [{'source_id':'source', 'conflicts':[conflict]}]
        brief, raw = packet_content(project)
        self.assertEqual(json.loads(raw)['extraction_conflicts'], [conflict])
        self.assertIn('임의 선택하지', brief.decode())
        self.assertIn('분할 추출 상충으로 계산에서 제외', build_claude_start(project).decode())
