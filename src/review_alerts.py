import json
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_FILE = PROJECT_ROOT / "config" / "config.json"


def load_config() -> dict:
    with CONFIG_FILE.open(encoding="utf-8") as file:
        return json.load(file)


def send_review_alert(alert: dict, api_url: str, timeout: int) -> None:
    payload = json.dumps(alert).encode("utf-8")
    request = Request(
        api_url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST"
    )

    with urlopen(request, timeout=timeout) as response:
        response.read()


def main() -> None:
    config = load_config()
    api_config = config["review_api"]
    api_url = api_config["url"]
    if not api_url:
        raise RuntimeError("Set review_api.url in config.json before running this script")

    alerts_file = PROJECT_ROOT / config["files"]["predictions"]
    with alerts_file.open(encoding="utf-8") as file:
        alerts = json.load(file)

    review_alerts = [
        alert for alert in alerts
        if alert.get("answer", {}).get("label") == "NEEDS_REVIEW"
    ]

    for alert in review_alerts:
        try:
            send_review_alert(alert, api_url, api_config["timeout_seconds"])
            print(f"Sent for review: {alert['subject']}")
        except (HTTPError, URLError, TimeoutError) as error:
            print(f"Failed to send '{alert['subject']}': {error}")

    print(f"Processed {len(review_alerts)} NEEDS_REVIEW alert(s)")


if __name__ == "__main__":
    main()
