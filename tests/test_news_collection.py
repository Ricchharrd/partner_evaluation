import json
import tempfile
import unittest
from datetime import date
from unittest.mock import patch

from partner_finance.market_news import (NewsUpdateError, add_company, article_specific_source, collect_news,
                                          parse_news, saved_articles)
from partner_finance.openai_provider import APIRequestError
from partner_finance.storage import ProjectStore


URL = 'https://www.example.com/news/project'


def response(*, annotations=True, sources=False, prose=False, articles=None, roles=None):
    rows = articles if articles is not None else [{
        'title': '신규 사업 발표', 'summary': '회사가 신규 사업 계획을 발표했다.',
        'published_at': date.today().isoformat(), 'source_name': 'Company',
        'source_url': URL, 'topic': '수주 및 사업',
    }]
    raw = json.dumps({'articles': rows, 'roles': roles or []}, ensure_ascii=False)
    if prose:
        raw = '검색 결과입니다.\n```json\n' + raw + '\n```\n출처를 확인하세요.'
    payload = {'status': 'completed', 'output': [
        {'type': 'web_search_call', 'status': 'completed', 'action': {
            'sources': [{'url': URL + '?utm_source=search#article'}] if sources else []}},
        {'type': 'message', 'content': [{'type': 'output_text', 'text': raw,
            'annotations': [{'type': 'url_citation', 'url': URL}] if annotations else []}]}]}
    return payload


class NewsCollectionTests(unittest.TestCase):
    def test_accepts_search_sources_even_without_inline_annotation(self):
        rows = parse_news(response(annotations=False, sources=True, prose=True))
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]['source_url'].startswith(URL))

    def test_inline_citation_still_works(self):
        self.assertEqual(len(parse_news(response())), 1)

    def test_company_overview_is_not_marked_as_a_verified_article(self):
        overview = 'https://www.ferrovial.com/en/business-lines/construction/'
        self.assertFalse(article_specific_source(overview))
        self.assertFalse(article_specific_source('https://www.ferrovial.com/en/'))
        self.assertTrue(article_specific_source(URL))
        payload = response()
        data = json.loads(payload['output'][1]['content'][0]['text'])
        data['articles'][0]['source_url'] = overview
        payload['output'][1]['content'][0]['text'] = json.dumps(data)
        payload['output'][1]['content'][0]['annotations'][0]['url'] = overview
        self.assertFalse(parse_news(payload)[0]['source_verified'])

    def test_uncited_public_url_is_retained_for_review_but_future_date_is_rejected(self):
        rows = parse_news(response(annotations=False))
        self.assertEqual(len(rows), 1)
        self.assertFalse(rows[0]['source_verified'])
        payload = response()
        text = json.loads(payload['output'][1]['content'][0]['text'])
        text['articles'][0]['published_at'] = '2099-01-01'
        payload['output'][1]['content'][0]['text'] = json.dumps(text)
        with self.assertRaises(NewsUpdateError):
            parse_news(payload)

    def test_no_articles_requires_real_search(self):
        self.assertEqual(parse_news(response(articles=[])), [])
        payload = response(articles=[], annotations=False)
        payload['output'] = payload['output'][1:]
        with self.assertRaisesRegex(NewsUpdateError, '실제 웹 검색'):
            parse_news(payload)

    def test_invalid_response_never_echoes_model_content(self):
        payload = response()
        payload['output'][1]['content'][0]['text'] = 'SECRET_NOT_JSON'
        with self.assertRaises(NewsUpdateError) as caught:
            parse_news(payload)
        self.assertNotIn('SECRET', str(caught.exception))

    def test_actual_request_contract_and_persistence(self):
        with tempfile.TemporaryDirectory() as root:
            store = ProjectStore(root)
            project = add_company(store, 'test', 'Ferrovial')
            bodies = []

            def request(provider, body):
                body = provider.normalized_body(body)
                provider.approval(body)
                bodies.append(body)
                return response(annotations=False, sources=True)

            with patch('partner_finance.market_news.authorize_request') as approve, patch(
                    'partner_finance.market_news.OpenAIProvider.request', request):
                collect_news(project, store, 'test', 'test-key', 'test-model')
            approve.assert_called_once()
            self.assertEqual(bodies[0]['tool_choice'], 'required')
            self.assertEqual(bodies[0]['include'], ['web_search_call.action.sources'])
            self.assertEqual(len(saved_articles(store.load(project.project_id, 'test'))), 1)
            self.assertGreater(store.news_refresh_remaining(project.project_id, 'test'), 0)

    def test_news_refresh_updates_roles_only_with_search_evidence(self):
        roles = [
            {'role': 'EPC', 'source_url': URL, 'evidence': '직접 건설을 수행한다고 명시'},
            {'role': '투자', 'source_url': 'https://unsearched.example/investment',
             'evidence': '검색되지 않은 투자 주장'},
        ]
        with tempfile.TemporaryDirectory() as root:
            store = ProjectStore(root)
            project = add_company(store, 'test', 'Ferrovial')

            def request(provider, body):
                provider.approval(provider.normalized_body(body))
                return response(annotations=False, sources=True, roles=roles)

            with patch('partner_finance.market_news.authorize_request'), patch(
                    'partner_finance.market_news.OpenAIProvider.request', request):
                collect_news(project, store, 'test', 'test-key', 'test-model')
            saved = store.load(project.project_id, 'test')
            self.assertEqual(saved.narrative['market_roles'], ['EPC'])
            self.assertEqual(saved.narrative['market_roles_source'], URL + '?utm_source=search#article')

    def test_rejected_api_request_can_be_retried_after_configuration_fix(self):
        with tempfile.TemporaryDirectory() as root:
            store = ProjectStore(root)
            project = add_company(store, 'test', 'Ferrovial')

            def request(provider, body):
                provider.approval(provider.normalized_body(body))
                raise APIRequestError('모델 설정 오류', http_status=400)

            with patch('partner_finance.market_news.authorize_request'), patch(
                    'partner_finance.market_news.OpenAIProvider.request', request):
                with self.assertRaisesRegex(NewsUpdateError, '모델 설정'):
                    collect_news(project, store, 'test', 'test-key', 'test-model')
            self.assertEqual(store.news_refresh_remaining(project.project_id, 'test'), 0)
            self.assertFalse(saved_articles(store.load(project.project_id, 'test')))

    def test_parse_failure_keeps_old_articles_and_paid_cooldown(self):
        with tempfile.TemporaryDirectory() as root:
            store = ProjectStore(root)
            project = add_company(store, 'test', 'Ferrovial')

            def request(provider, body):
                provider.approval(provider.normalized_body(body))
                payload = response(annotations=False)
                data = json.loads(payload['output'][1]['content'][0]['text'])
                data['articles'][0]['source_url'] = 'http://unusable.example/news'
                payload['output'][1]['content'][0]['text'] = json.dumps(data)
                return payload

            with patch('partner_finance.market_news.authorize_request'), patch(
                    'partner_finance.market_news.OpenAIProvider.request', request):
                with self.assertRaises(NewsUpdateError):
                    collect_news(project, store, 'test', 'test-key', 'test-model')
            self.assertGreater(store.news_refresh_remaining(project.project_id, 'test'), 0)
            self.assertNotIn('market_last_checked', store.load(project.project_id, 'test').narrative)

    def test_no_key_does_not_claim_a_refresh(self):
        with tempfile.TemporaryDirectory() as root:
            store = ProjectStore(root)
            project = add_company(store, 'test', 'Ferrovial')
            with self.assertRaisesRegex(NewsUpdateError, 'API 키'):
                collect_news(project, store, 'test', '', 'test-model')
            self.assertEqual(store.news_refresh_remaining(project.project_id, 'test'), 0)


if __name__ == '__main__':
    unittest.main()
