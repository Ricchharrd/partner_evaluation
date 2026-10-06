import unittest
from unittest.mock import patch

from partner_finance.company_lookup import CompanyLookupError, lookup_companies


class CompanyLookupTests(unittest.TestCase):
    def test_search_uses_public_candidate_and_official_name(self):
        search = {'search': [{'id': 'Q123', 'label': 'Ferrovial',
                              'description': 'Spanish construction company'}]}
        details = {'entities': {'Q123': {'claims': {
            'P1448': [{'mainsnak': {'datavalue': {'value': {'text': 'Ferrovial SE'}}}}],
            'P856': [{'mainsnak': {'datavalue': {'value': 'https://www.ferrovial.com/'}}}],
        }}}}
        with patch('partner_finance.company_lookup._api', side_effect=[search, details]) as api:
            result = lookup_companies('Ferrovial')
        self.assertEqual(result[0]['name'], 'Ferrovial SE')
        self.assertEqual(result[0]['source'], 'https://www.wikidata.org/wiki/Q123')
        self.assertEqual(api.call_args_list[0].args[0]['language'], 'en')

    def test_invalid_query_does_not_call_public_api(self):
        with patch('partner_finance.company_lookup._api') as api:
            with self.assertRaises(CompanyLookupError):
                lookup_companies('a')
            api.assert_not_called()

    def test_no_match_returns_empty_list(self):
        with patch('partner_finance.company_lookup._api', return_value={'search': []}):
            self.assertEqual(lookup_companies('없는기업'), [])

    def test_disambiguation_pages_are_not_company_choices(self):
        search = {'search': [{'id': 'Q1', 'label': 'Ferrovial',
                              'description': 'Wikimedia disambiguation page'}]}
        with patch('partner_finance.company_lookup._api', return_value=search) as api:
            self.assertEqual(lookup_companies('Ferrovial'), [])
            api.assert_called_once()


if __name__ == '__main__':
    unittest.main()
