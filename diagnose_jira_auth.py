"""Read-only Jira Data Center connection and authentication diagnostics."""

from __future__ import annotations

import sys
from typing import Any, Dict, Mapping, Sequence

import requests

from config_loader import ConfigError, load_legacy_config


SAFE_HEADERS = (
    "X-AUSERNAME",
    "WWW-Authenticate",
    "Server",
    "Content-Type",
    "X-AREQUESTID",
)


def load_config() -> Dict[str, Any]:
    return load_legacy_config()


def _authorization_headers(config: Mapping[str, Any]) -> Dict[str, str]:
    return {
        "Authorization": f"Bearer {config['JIRA_TOKEN']}",
        "Accept": "application/json",
    }


def print_response(
    label: str,
    response: requests.Response,
    safe_json_fields: Sequence[str] = (),
) -> None:
    print(f"[{label}] status={response.status_code}")
    for header in SAFE_HEADERS:
        value = response.headers.get(header)
        if value:
            print(f"  {header}: {str(value)[:200]}")
    if response.status_code == 200 and safe_json_fields:
        payload = response.json()
        if isinstance(payload, Mapping):
            for field in safe_json_fields:
                value = payload.get(field)
                if value is not None:
                    print(f"  {field}: {str(value)[:200]}")
    print()


def main() -> None:
    config = load_config()
    token_present = bool(config["JIRA_TOKEN"])

    print("Jira Auth Diagnose (read-only)")
    print(f"URL: {config['JIRA_URL']}")
    print(f"Projekt: {config['PROJECT_KEY']}")
    print(f"Token gesetzt: {'ja' if token_present else 'nein'}")
    print()

    server_info = requests.get(
        f"{config['JIRA_URL']}/rest/api/2/serverInfo",
        headers={"Accept": "application/json"},
        timeout=30,
    )
    print_response(
        "serverInfo ohne Auth",
        server_info,
        ("version", "buildNumber", "deploymentType"),
    )

    myself = requests.get(
        f"{config['JIRA_URL']}/rest/api/2/myself",
        headers=_authorization_headers(config),
        timeout=30,
    )
    print_response("myself mit Bearer", myself, ("displayName",))

    project = requests.get(
        f"{config['JIRA_URL']}/rest/api/2/project/{config['PROJECT_KEY']}",
        headers=_authorization_headers(config),
        timeout=30,
    )
    print_response("project mit Bearer", project, ("key", "name"))


if __name__ == "__main__":
    try:
        main()
    except (ConfigError, OSError, requests.RequestException, ValueError) as error:
        print(f"Diagnose abgebrochen: {error}", file=sys.stderr)
        sys.exit(1)
