import json
import re
from datetime import datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_FILE = PROJECT_ROOT / "config" / "config.json"


def load_model_config() -> dict:
    """Load model settings and classification questions from JSON."""
    with CONFIG_FILE.open(encoding="utf-8") as file:
        config = json.load(file)

    if not isinstance(config, dict):
        raise ValueError(f"{CONFIG_FILE.name} must contain a JSON object")

    return config


MODEL_CONFIG = load_model_config()
MAILS_FILE = PROJECT_ROOT / MODEL_CONFIG["files"]["mails"]
PREDICTIONS_FILE = PROJECT_ROOT / MODEL_CONFIG["files"]["predictions"]
LOG_DIRECTORY = PROJECT_ROOT / MODEL_CONFIG["files"]["log_directory"]
ALERT_THRESHOLD = MODEL_CONFIG["alert_threshold"]
NON_ALERT_THRESHOLD = MODEL_CONFIG["non_alert_threshold"]
NON_ALERT_STORAGE_PROBABILITY = MODEL_CONFIG["non_alert_storage_probability"]
MAX_BODY_CHARS = MODEL_CONFIG["max_body_chars"]
QUESTIONS = MODEL_CONFIG["questions"]

# The model is initialized lazily only when a mail is not already known.
router = None


def clean_body(text: str, max_chars: int | None = None) -> str:
    """Strip HTML tags and extra whitespace, and trim long bodies."""
    max_chars = max_chars or MAX_BODY_CHARS
    text = re.sub(r"<[^>]+>", " ", text or "")
    text = re.sub(r"\s+", " ", text).strip()
    return text[:max_chars]


def extract_email_fields(mail: dict) -> tuple[str, str, str]:
    """Normalize flat or Outlook-style nested email records."""
    subject = mail.get("subject", "")

    sender = mail.get("from", "")
    if isinstance(sender, dict):
        sender = sender.get("emailAddress", {}).get("address", "")

    body = mail.get("body", "")
    if isinstance(body, dict):
        body = body.get("content", "")

    return str(subject), str(sender), str(body)


def _email_key(subject: str, sender: str, body: str) -> tuple[str, str, str]:
    return subject.strip(), sender.strip().lower(), clean_body(body)


def _load_json_array(file_path: Path) -> list[dict]:
    with file_path.open(encoding="utf-8") as file:
        records = json.load(file)

    if not isinstance(records, list):
        raise ValueError(f"{file_path.name} must contain a JSON array")

    return records


def load_mails() -> list[dict]:
    """Load known mails and their saved answers."""
    return _load_json_array(MAILS_FILE)


def load_alerts() -> list[dict]:
    """Load saved model classifications."""
    return _load_json_array(PREDICTIONS_FILE)


def save_alert(alert: dict) -> None:
    alerts = load_alerts()
    key = _email_key(alert["subject"], alert["from"], alert["body"])

    if not any(_email_key(item["subject"], item["from"], item["body"]) == key for item in alerts):
        alert_with_timestamp = {
            "subject": alert["subject"],
            "from": alert["from"],
            "body": alert["body"],
            "timestamp": datetime.now().strftime("%m-%d-%Y"),
            "answer": alert["answer"]
        }
        alerts.append(alert_with_timestamp)
        with PREDICTIONS_FILE.open("w", encoding="utf-8") as file:
            json.dump(alerts, file, indent=2)
            file.write("\n")


def send_review_alert(alert: dict) -> None:
    """Send a NEEDS_REVIEW prediction to the configured review API."""
    api_config = MODEL_CONFIG["review_api"]
    api_url = api_config["url"]
    if not api_url:
        print("Review API is not configured; set review_api.url in config/config.json")
        return

    request = Request(
        api_url,
        data=json.dumps(alert).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST"
    )

    try:
        with urlopen(request, timeout=api_config["timeout_seconds"]) as response:
            response.read()
        print(f"Sent NEEDS_REVIEW alert to review API: {alert['subject']}")
    except (HTTPError, URLError, TimeoutError) as error:
        print(f"Could not send NEEDS_REVIEW alert to review API: {error}")


def log_prediction(answer: dict) -> None:
    """Append one single-line record to the log for the current day."""
    now = datetime.now()
    timestamp = now.strftime("%m-%d-%Y %H:%M:%S")
    log_file = LOG_DIRECTORY / f"{now.strftime('%m-%d-%Y')}.txt"
    subject = re.sub(r"[\r\n\t]+", " ", answer["subject"]).strip()
    line = (
        f"{timestamp} | subject={subject} | label={answer['label']} | "
        f"choice={answer['choice']} | alert_probability={answer['alert_probability']} | "
        f"model={answer['model_used']}\n"
    )
    LOG_DIRECTORY.mkdir(parents=True, exist_ok=True)
    with log_file.open("a", encoding="utf-8") as file:
        file.write(line)


def _known_mail_answer(mail: dict) -> dict | None:
    answer = mail.get("answer")
    if not isinstance(answer, dict):
        return None

    subject, _, _ = extract_email_fields(mail)

    return {
        "subject": subject,
        "label": answer["label"],
        "choice": answer.get("choice"),
        "alert_probability": answer.get("alert_probability"),
        "model_used": "mails.json"
    }


def _find_saved_alert(subject: str, sender: str, body: str) -> dict | None:
    key = _email_key(subject, sender, body)
    for alert in load_alerts():
        if _email_key(alert["subject"], alert["from"], alert["body"]) == key:
            answer = dict(alert["answer"])
            answer["model_used"] = "prediction.json"
            return answer
    return None


def classify_with_model(subject: str, sender: str, body: str) -> dict:
    from laya import Router

    global router
    if router is None:
        router = Router(**MODEL_CONFIG.get("router", {"preload": True}))

    state = {
        "subject": subject,
        "from": sender,
        "body": clean_body(body)
    }

    result = router.predict(state, QUESTIONS)
    answers = result["answers"]

    choice = answers["email_type"]["choice"]
    p_alert = answers["is_alert"]["noul"]

    # Final decision: both signals must agree
    if p_alert >= ALERT_THRESHOLD and choice == "alert":
        label = "ALERT"
    elif p_alert <= NON_ALERT_THRESHOLD and choice == "non_alert":
        label = "NON_ALERT"
    else:
        label = "NEEDS_REVIEW"          # the two answers disagree, or confidence is low

    stored_probability = (
        NON_ALERT_STORAGE_PROBABILITY if label == "NON_ALERT" else round(p_alert, 3)
    )
    answer = {
        "subject": subject,
        "label": label,
        "choice": choice,
        "alert_probability": stored_probability,
        "model_used": result["routing"]["model"]
    }

    prediction = {
        "subject": subject,
        "from": sender,
        "body": body,
        "timestamp": datetime.now().strftime("%m-%d-%Y"),
        "answer": answer
    }
    log_prediction(answer)
    save_alert(prediction)

    if label == "NEEDS_REVIEW":
        send_review_alert(prediction)

    return answer


def classify_email(
    subject_or_mail: str | dict,
    sender: str | None = None,
    body: str | None = None
) -> dict:
    """Return a saved answer or classify an unknown mail with the model."""
    if isinstance(subject_or_mail, dict):
        subject, sender, body = extract_email_fields(subject_or_mail)
    else:
        subject = subject_or_mail
        if sender is None or body is None:
            raise ValueError("sender and body are required for a flat email input")

    key = _email_key(subject, sender, body)

    for mail in load_mails():
        known_subject, known_sender, known_body = extract_email_fields(mail)
        if _email_key(known_subject, known_sender, known_body) == key:
            answer = _known_mail_answer(mail)
            if answer is not None:
                return answer

    saved_alert = _find_saved_alert(subject, sender, body)
    if saved_alert is not None:
        return saved_alert

    return classify_with_model(subject, sender, body)


if __name__ == "__main__":
    for mail in load_mails():
        print(json.dumps(classify_email(mail), indent=2))
