#!/usr/bin/env python3
"""Notify recipients when configured interfaces change their global IPv6 addresses."""

from __future__ import annotations

import ipaddress
import json
import os
import re
import shutil
import smtplib
import socket
import subprocess
import sys
from datetime import datetime, timezone
from email.mime.text import MIMEText
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent


def load_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as file:
        return json.load(file)


def resolve_config_path() -> Path:
    configured = os.environ.get("IPV6_MONITOR_CONFIG")
    if configured:
        return Path(configured).expanduser()
    for candidate in (BASE_DIR / "config.json", BASE_DIR / "sendmail.json"):
        if candidate.exists():
            return candidate
    return BASE_DIR / "config.json"


def resolve_path(config: dict[str, Any], key: str, default: str) -> Path:
    value = config.get(key, default)
    path = Path(str(value)).expanduser()
    return path if path.is_absolute() else BASE_DIR / path


def resolve_ifconfig(config: dict[str, Any]) -> str:
    configured = config.get("ifconfig_path")
    candidates = []
    if configured:
        candidates.append(str(configured))
    candidates.extend(
        [
            "/usr/sbin/ifconfig",
            "/data/data/com.termux/files/usr/bin/ifconfig",
            shutil.which("ifconfig") or "",
        ]
    )
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return candidate
    raise FileNotFoundError("ifconfig executable was not found")


def parse_ifconfig(output: str, interfaces: list[str] | None = None) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    selected = set(interfaces or [])
    current_interface: str | None = None
    ipv4: list[dict[str, str]] = []
    ipv6: list[dict[str, str]] = []
    interface_pattern = re.compile(r"^([^\s:]+):\s+flags=")
    ipv4_pattern = re.compile(r"\binet\s+(\d{1,3}(?:\.\d{1,3}){3})\b")
    ipv6_pattern = re.compile(r"\binet6(?: address)?\s+([0-9a-fA-F:]+)(?:/\d+)?\b")

    for line in output.splitlines():
        interface_match = interface_pattern.match(line)
        if interface_match:
            current_interface = interface_match.group(1)
            continue
        if not current_interface or (selected and current_interface not in selected):
            continue

        ipv4_match = ipv4_pattern.search(line)
        if ipv4_match:
            address = ipv4_match.group(1)
            if not address.startswith("127.") and not address.startswith("169.254."):
                ipv4.append({current_interface: address})
            continue

        ipv6_match = ipv6_pattern.search(line)
        if not ipv6_match:
            continue
        address = ipv6_match.group(1).lower()
        try:
            parsed_address = ipaddress.IPv6Address(address)
        except ipaddress.AddressValueError:
            continue
        if not parsed_address.is_global:
            continue
        ipv6.append({current_interface: parsed_address.compressed})

    return sorted(ipv4, key=lambda item: next(iter(item.items()))), sorted(
        ipv6, key=lambda item: next(iter(item.items()))
    )


def get_addresses(config: dict[str, Any]) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    result = subprocess.run(
        [resolve_ifconfig(config)], check=True, capture_output=True, text=True
    )
    interfaces = config.get("interfaces")
    if interfaces is not None and not isinstance(interfaces, list):
        raise ValueError("interfaces must be a JSON array")
    return parse_ifconfig(result.stdout, interfaces)


def format_addresses(addresses: list[dict[str, str]], empty: str) -> list[str]:
    if not addresses:
        return [f"  {empty}"]
    return [f"  {interface}: {address}" for item in addresses for interface, address in item.items()]


def device_name_from_config(config: dict[str, Any]) -> str:
    return str(config.get("device_name", socket.gethostname()))


def subject_from_config(config: dict[str, Any]) -> str:
    return f"{device_name_from_config(config)}{config.get('subject', 'IP地址变化')}"


def format_body(
    config: dict[str, Any], ipv4: list[dict[str, str]], ipv6: list[dict[str, str]]
) -> str:
    device_name = device_name_from_config(config)
    timestamp = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    lines = [
        f"设备标记: {device_name}",
        f"主机名: {socket.gethostname()}",
        f"检测时间: {timestamp}",
        "",
        "IPv4 地址:",
        *format_addresses(ipv4, "未找到 IPv4 地址"),
        "",
        "IPv6 地址:",
        *format_addresses(ipv6, "未找到全局 IPv6 地址"),
    ]
    return "\n".join(lines) + "\n"


def recipients_from_config(config: dict[str, Any]) -> list[str]:
    recipients = config.get("recipients", config.get("address"))
    if not isinstance(recipients, list) or not all(isinstance(item, str) for item in recipients):
        raise ValueError("recipients must be a JSON array of email addresses")
    return recipients


def send_mail(
    config: dict[str, Any], ipv4: list[dict[str, str]], ipv6: list[dict[str, str]]
) -> None:
    recipients = recipients_from_config(config)
    sender = str(config.get("sender", config.get("smtp_user", "")))
    smtp_user = str(config.get("smtp_user", sender))
    smtp_password = config.get("smtp_password")
    if not sender or not smtp_user or not isinstance(smtp_password, str):
        raise ValueError("sender, smtp_user, and smtp_password are required")

    message = MIMEText(format_body(config, ipv4, ipv6), _charset="utf-8")
    message["Subject"] = subject_from_config(config)
    message["From"] = sender
    message["To"] = ", ".join(recipients)

    with smtplib.SMTP_SSL(
        str(config["smtp_host"]), int(config.get("smtp_port", 465)), timeout=20
    ) as smtp:
        smtp.login(smtp_user, smtp_password)
        smtp.sendmail(sender, recipients, message.as_string())


def write_state(path: Path, addresses: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(addresses, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def main() -> int:
    try:
        config_value = load_json(resolve_config_path())
        if not isinstance(config_value, dict):
            raise ValueError("configuration must be a JSON object")
        config = config_value
        ipv4, current_ipv6 = get_addresses(config)
    except Exception as error:
        print(f"IPv6 monitor failed before state update: {error}", file=sys.stderr)
        return 1

    if not current_ipv6:
        print("No global IPv6 address found; keeping previous state.", file=sys.stderr)
        return 0

    state_path = resolve_path(config, "state_file", "ipv6_state.json")
    previous = None
    if state_path.exists():
        try:
            state_value = load_json(state_path)
            if not isinstance(state_value, list):
                raise ValueError("state must be a JSON array")
            previous = state_value
        except (OSError, json.JSONDecodeError, ValueError) as error:
            print(f"Cannot read previous state: {error}", file=sys.stderr)

    if previous == current_ipv6:
        print("IPv6 unchanged.")
        return 0

    if previous is None:
        write_state(state_path, current_ipv6)
        print("Initialized IPv6 baseline without sending mail.")
        return 0

    try:
        send_mail(config, ipv4, current_ipv6)
    except Exception as error:
        print(f"IPv6 changed, but notification failed: {error}", file=sys.stderr)
        return 1

    write_state(state_path, current_ipv6)
    print("IPv6 changed and notification sent.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
