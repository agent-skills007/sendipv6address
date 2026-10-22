import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import ipv6_monitor


IFCONFIG_OUTPUT = """eth0: flags=4163<UP,BROADCAST,RUNNING,MULTICAST>  mtu 1500
        inet 192.168.2.60  netmask 255.255.255.0  broadcast 192.168.2.255
        inet 127.0.0.1  netmask 255.0.0.0
        inet6 fe80::1  prefixlen 64  scopeid 0x20<link>
        inet6 2408:8256::2  prefixlen 64  scopeid 0x0<global>
        inet6 2408:8256::1  prefixlen 64  scopeid 0x0<global>
lo: flags=73<UP,LOOPBACK,RUNNING>  mtu 65536
        inet 10.0.0.1  netmask 255.255.255.0
        inet6 ::1  prefixlen 128  scopeid 0x10<host>
"""


class MonitorTests(unittest.TestCase):
    def test_parse_filters_interfaces_and_non_global_addresses(self):
        ipv4, ipv6 = ipv6_monitor.parse_ifconfig(IFCONFIG_OUTPUT, ["eth0"])
        self.assertEqual(ipv4, [{"eth0": "192.168.2.60"}])
        self.assertEqual(
            ipv6,
            [
                {"eth0": "2408:8256::1"},
                {"eth0": "2408:8256::2"},
            ],
        )

    def test_format_body_contains_device_and_both_address_families(self):
        body = ipv6_monitor.format_body(
            {"device_name": "测试设备"},
            [{"wlan0": "192.168.2.230"}],
            [{"wlan0": "2408:8256::1"}],
        )
        self.assertIn("设备标记: 测试设备", body)
        self.assertIn("wlan0: 192.168.2.230", body)
        self.assertIn("wlan0: 2408:8256::1", body)

    def test_empty_address_sections_are_explicit(self):
        body = ipv6_monitor.format_body({}, [], [])
        self.assertIn("未找到 IPv4 地址", body)
        self.assertIn("未找到全局 IPv6 地址", body)

    def test_ipv4_only_change_does_not_send_mail(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = root / "state.json"
            config = root / "config.json"
            config.write_text(json.dumps({
                "device_name": "test",
                "interfaces": ["eth0"],
                "ifconfig_path": "/bin/true",
                "state_file": str(state),
                "smtp_host": "smtp.invalid",
                "smtp_user": "u",
                "smtp_password": "p",
                "sender": "u@example.com",
                "recipients": ["r@example.com"],
                "subject": "subject",
            }), encoding="utf-8")
            old_config = os.environ.get("IPV6_MONITOR_CONFIG")
            try:
                os.environ["IPV6_MONITOR_CONFIG"] = str(config)
                original = ipv6_monitor.get_addresses
                sent = []
                ipv6_monitor.send_mail = lambda *args: sent.append(args)
                ipv6_monitor.get_addresses = lambda _config: ([{"eth0": "192.168.2.2"}], [{"eth0": "2408::1"}])
                self.assertEqual(ipv6_monitor.main(), 0)
                ipv6_monitor.get_addresses = lambda _config: ([{"eth0": "192.168.2.3"}], [{"eth0": "2408::1"}])
                self.assertEqual(ipv6_monitor.main(), 0)
                self.assertEqual(sent, [])
                ipv6_monitor.get_addresses = original
            finally:
                if old_config is None:
                    os.environ.pop("IPV6_MONITOR_CONFIG", None)
                else:
                    os.environ["IPV6_MONITOR_CONFIG"] = old_config


if __name__ == "__main__":
    unittest.main()
