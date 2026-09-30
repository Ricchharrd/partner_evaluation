import unittest
from streamlit.testing.v1 import AppTest


class HITLUITests(unittest.TestCase):
    def test_prepare_approve_execute_then_reapprove(self):
        app = AppTest.from_string('''
import streamlit as st
from partner_finance.schema import AnalysisProject, EntityProfile
from partner_finance.hitl import render_hitl, authorize_request
if 'project' not in st.session_state:
    st.session_state.project = AnalysisProject('test', EntityProfile('Synthetic'))
p = st.session_state.project
render_hitl(p, lambda: None)
if st.button('Run request'):
    authorize_request(p, {'input':'public synthetic data','model':'gpt-6-luna','max_output_tokens':100})
    st.success('approved execution')
''').run()
        app.button[0].click().run()
        self.assertFalse(app.exception)
        self.assertIn('hitl_pending', app.session_state)
        for field in app.text_input:
            field.set_value('public test scope')
        app.selectbox[0].select('공개자료')
        for checkbox in app.checkbox:
            checkbox.check()
        app.run()
        next(b for b in app.button if b.label == '승인 기록 후 계속').click().run()
        next(b for b in app.button if b.label == 'Run request').click().run()
        self.assertTrue(any(s.value == 'approved execution' for s in app.success))
        next(b for b in app.button if b.label == 'Run request').click().run()
        self.assertFalse(app.exception)
        self.assertTrue(any('외부 AI 호출 전 확인' in s.value for s in app.subheader))
