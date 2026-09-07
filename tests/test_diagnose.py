import importlib.util
import pathlib
import unittest
from unittest.mock import patch


ROOT = pathlib.Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('mmwx_diagnose', ROOT / 'src' / 'diagnose.py')
DIAGNOSE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(DIAGNOSE)


SOURCE = b'''proxies:
  - name: HK exit
    dialer-proxy: US relay
proxy-groups:
  - name: Netflix
    type: select
    proxies: [HK exit]
rules:
  - MATCH,DIRECT
'''


class DiagnoseTests(unittest.TestCase):
    @patch.object(DIAGNOSE, 'expected_userinfo', return_value=None)
    def test_complete_output_passes(self, _):
        report = DIAGNOSE.Report('post')
        body = b'''[General]\n\n[Proxy]\nHK exit [land] = Shadowsocks\n\n[Proxy Chain]\nHK exit = US relay, HK exit [land]\n\n[Proxy Group]\nNetflix = select, HK exit\n\n[Rule]\nFINAL,DIRECT\n'''
        DIAGNOSE.validate_conversion(report, SOURCE, 200, {}, body, 'code')
        self.assertEqual([], report.bugs)

    @patch.object(DIAGNOSE, 'expected_userinfo', return_value=None)
    def test_missing_chain_is_reported(self, _):
        report = DIAGNOSE.Report('post')
        body = b'''[General]\n\n[Proxy]\nHK exit = Shadowsocks\n\n[Proxy Group]\nNetflix = select, HK exit\n\n[Rule]\nFINAL,DIRECT\n'''
        DIAGNOSE.validate_conversion(report, SOURCE, 200, {}, body, 'code')
        self.assertIn('proxy-chain-lost', report.bugs)

    @patch.object(DIAGNOSE, 'expected_userinfo', return_value=None)
    def test_missing_group_and_final_are_reported(self, _):
        report = DIAGNOSE.Report('post')
        body = b'''[General]\n\n[Proxy]\nHK exit = Shadowsocks\n\n[Proxy Chain]\nHK exit = US relay, HK exit\n\n[Proxy Group]\nOther = select, HK exit\n\n[Rule]\nMATCH,DIRECT\n'''
        DIAGNOSE.validate_conversion(report, SOURCE, 200, {}, body, 'code')
        self.assertIn('proxy-groups-lost', report.bugs)
        self.assertIn('final-rule-lost', report.bugs)


if __name__ == '__main__':
    unittest.main()
