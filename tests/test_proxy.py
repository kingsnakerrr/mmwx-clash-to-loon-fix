import importlib.util
import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('mmwx_proxy', ROOT / 'src' / 'proxy.py')
PROXY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PROXY)


class ProxyTests(unittest.TestCase):
    def test_query_type_is_replaced_without_losing_other_parameters(self):
        result = PROXY.with_query_type('/x/code?a=1&t=clash-to-loon', 'clash')
        self.assertIn('a=1', result)
        self.assertIn('t=clash', result)
        self.assertNotIn('clash-to-loon', result)

    def test_dialer_proxy_becomes_loon_proxy_chain(self):
        config = {
            'proxies': [
                {'name': 'HK exit', 'dialer-proxy': 'US relay'},
                {'name': 'US relay'},
            ]
        }
        sections = PROXY.build_loon_proxies_and_chains(
            config,
            'HK exit = Shadowsocks, host, 443, aes-128-gcm, pass\n'
            'US relay = Shadowsocks, relay, 443, aes-128-gcm, pass',
        )
        rendered = '\n\n'.join(sections)
        self.assertIn('HK exit [落地] = Shadowsocks', rendered)
        self.assertIn('[Proxy Chain]', rendered)
        self.assertIn('HK exit = US relay, HK exit [落地], udp=true', rendered)

    def test_plain_proxy_does_not_create_chain_section(self):
        sections = PROXY.build_loon_proxies_and_chains(
            {'proxies': [{'name': 'HK'}]},
            'HK = Shadowsocks, host, 443, aes-128-gcm, pass',
        )
        self.assertEqual(1, len(sections))

    def test_match_becomes_final(self):
        rendered = PROXY.build_loon_rules(['DOMAIN,example.com,Proxy', 'MATCH,DIRECT'], {})
        self.assertIn('DOMAIN,example.com,Proxy', rendered)
        self.assertIn('FINAL,DIRECT', rendered)


if __name__ == '__main__':
    unittest.main()
