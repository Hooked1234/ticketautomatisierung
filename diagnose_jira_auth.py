import os
from typing import Dict

import requests
from dotenv import load_dotenv


SAFE_HEADERS = [
    "X-AUSERNAME",
    "WWW-Authenticate",
    "Server",
    "Content-Type",
]


def load_config() -> Dict[str, str]:
    load_dotenv()
    return {
        "JIRA_URL": os.getenv("JIRA_URL", "").rstrip("/"),
        "JIRA_TOKEN": os.getenv("JIRA_TOKEN", ""),
        "PROJECT_KEY": os.getenv("PROJECT_KEY", ""),
    }


def print_response(label: str, response: requests.Response) -> None:
    print(f"[{label}] status={response.status_code}")
    for header in SAFE_HEADERS:
        value = response.headers.get(header)
        if value:
            print(f"  {header}: {value}")
    body = response.text.strip().replace("\n", " ")
    print(f"  body: {body[:300]}")
    print()


def main() -> None:
    config = load_config()
    token = config["JIRA_TOKEN"]

    print("Jira Auth Diagnose")
    print(f"URL: {config['JIRA_URL']}")
    print(f"Projekt: {config['PROJECT_KEY']}")
    print(f"Token gesetzt: {'ja' if token else 'nein'}")
    print(f"Token-Laenge: {len(token)}")
    print()

    server_info = requests.get(
        f"{config['JIRA_URL']}/rest/api/2/serverInfo",
        headers={"Accept": "application/json"},
        timeout=30,
    )
    print_response("serverInfo ohne Auth", server_info)

    myself = requests.get(
        f"{config['JIRA_URL']}/rest/api/2/myself",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        },
        timeout=30,
    )
    print_response("myself mit Bearer", myself)

    project = requests.get(
        f"{config['JIRA_URL']}/rest/api/2/project/{config['PROJECT_KEY']}",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        },
        timeout=30,
    )
    print_response("project mit Bearer", project)


if __name__ == "__main__":
    main()
