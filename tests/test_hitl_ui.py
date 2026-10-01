import unittest
from streamlit.testing.v1 import AppTest


HARNESS = '''
import streamlit as st
from partner_finance.schema import AnalysisProject, EntityProfile
from partner_finance.hitl import render_hitl, authorize_request, clear_action_tickets
if 'project' not in st.session_state:
    st.session_state.project = AnalysisProject('test', EntityProfile('Synthetic'))
p = st.session_state.project
body = st.session_state.get('body', {'input':'public synthetic data','model':'test-model','max_output_tokens':100})
action = render_hitl(p, lambda: None, allowed_actions=st.session_state.get('allowed', ['test']))
if st.button('Run request') or action == 'test':
    try:
        authorize_request(p, body, action='test')
        st.session_state.calls = st.session_state.get('calls', 0) + 1
        if st.session_state.get('fail'):
            raise ValueError('simulated failure')
        st.success('approved execution')
    except ValueError as exc:
        st.error(str(exc))
    finally:
        clear_action_tickets(p)
'''


def click(app, label):
    return next(b for b in app.button if b.label == label).click().run()


class HITLUITests(unittest.TestCase):
    def test_one_click_executes_once_without_text_fields(self):
        app = AppTest.from_string(HARNESS).run()
        click(app, 'Run request')
        self.assertFalse(app.exception)
        self.assertFalse(app.text_input)
        self.assertFalse(app.selectbox)
        self.assertFalse(app.checkbox)
        self.assertNotIn('calls', app.session_state)
        click(app, '공개자료로 승인하고 실행')
        self.assertFalse(app.exception)
        self.assertEqual(app.session_state.calls, 1)
        self.assertTrue(any(s.value == 'approved execution' for s in app.success))
        self.assertFalse(app.session_state.hitl_tickets)
        self.assertFalse(app.session_state.project.narrative.get('hitl_review'))
        app.run()
        self.assertEqual(app.session_state.calls, 1)
        click(app, 'Run request')
        self.assertEqual(app.session_state.calls, 1)
        self.assertIn('hitl_pending', app.session_state)

    def test_large_request_needs_extra_confirmation_and_cancel(self):
        app = AppTest.from_string(HARNESS).run()
        app.session_state.body = {'input': 'x' * 21000}
        click(app, 'Run request')
        self.assertTrue(next(b for b in app.button if b.label == '공개자료로 승인하고 실행').disabled)
        self.assertEqual(len(app.checkbox), 1)
        app.checkbox[0].check().run()
        click(app, '공개자료로 승인하고 실행')
        self.assertEqual(app.session_state.calls, 1)
        click(app, 'Run request')
        click(app, '취소')
        self.assertNotIn('hitl_pending', app.session_state)
        self.assertEqual(app.session_state.calls, 1)

    def test_changed_request_requires_new_consent(self):
        app = AppTest.from_string(HARNESS).run()
        click(app, 'Run request')
        app.session_state.body = {'input': 'changed public request'}
        click(app, '공개자료로 승인하고 실행')
        self.assertNotIn('calls', app.session_state)
        self.assertIn('hitl_pending', app.session_state)
        self.assertFalse(app.session_state.hitl_tickets)
        click(app, '공개자료로 승인하고 실행')
        self.assertEqual(app.session_state.calls, 1)

    def test_failure_is_not_automatically_retried(self):
        app = AppTest.from_string(HARNESS).run()
        app.session_state.fail = True
        click(app, 'Run request')
        click(app, '공개자료로 승인하고 실행')
        self.assertEqual(app.session_state.calls, 1)
        self.assertTrue(app.error)
        app.run()
        self.assertEqual(app.session_state.calls, 1)
        self.assertFalse(app.session_state.hitl_tickets)

    def test_wrong_stage_and_sensitive_input_cannot_run(self):
        app = AppTest.from_string(HARNESS).run()
        click(app, 'Run request')
        app.session_state.allowed = []
        app.run()
        self.assertTrue(next(b for b in app.button if b.label == '공개자료로 승인하고 실행').disabled)
        click(app, '취소')
        app.session_state.body = {'input': '대외비'}
        click(app, 'Run request')
        self.assertTrue(app.error)
        self.assertNotIn('calls', app.session_state)
