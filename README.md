# Email Alert Classification Project

This project classifies incoming emails as operational alerts or non-alert emails using the Laya model. It keeps previously classified emails in a local prediction store, writes a daily prediction log, and automatically sends uncertain predictions to a configured review API.

The classifier supports both simple email records and Outlook/Graph-style email records with nested sender and body objects.

## Project structure

```text
Laya-use-cases/
├── config/
│   └── config.json
├── data/
│   ├── mails.json
│   └── prediction.json
├── logs/
│   └── MM-DD-YYYY.txt
├── src/
│   ├── email_alert_classifier.py
│   └── review_alerts.py
├── .venv/
└── README.md
```

### `src/email_alert_classifier.py`

This is the main classifier. It:

1. Loads the central configuration.
2. Normalizes the incoming email.
3. Checks known emails in `data/mails.json`.
4. Checks saved model predictions in `data/prediction.json`.
5. Runs the Laya model only when no saved answer exists.
6. Stores the model result in `data/prediction.json`.
7. Writes one line to the current daily log file.
8. Sends `NEEDS_REVIEW` results to the configured review API.

The Laya package is imported lazily. Known emails can therefore be answered from local data without loading the model.

### `src/review_alerts.py`

This is a manual resend utility. It reads all records in `data/prediction.json` whose label is `NEEDS_REVIEW` and sends them to the configured review API.

Automatic API notification is handled directly by `email_alert_classifier.py` when a new model prediction receives the `NEEDS_REVIEW` label.

### `config/config.json`

This is the central configuration file for the project. File locations, thresholds, model questions, router settings, body length limits, and review API settings belong here.

### `data/mails.json`

This contains the known/raw email dataset. It is checked before the model is called. A matching record can include an `answer` object; when it does, that saved answer is returned immediately.

The classifier can read both the original flat format and the nested Outlook/Graph format.

### `data/prediction.json`

This is the local prediction cache. Every new model result is saved here with:

- The original subject
- Sender address
- Original body
- Prediction date in `MM-DD-YYYY` format
- Classification answer
- Alert probability
- Model name

Both `ALERT`, `NON_ALERT`, and `NEEDS_REVIEW` model results are stored.

### `logs/`

The logger creates one text file per calendar day. For example:

```text
logs/09-27-2026.txt
logs/09-28-2026.txt
```

Every new model prediction is written as one line. Cached results are not logged again because the model was not called for them.

## Classification flow

For an incoming email, the lookup order is:

```text
Incoming email
      │
      ▼
Check data/mails.json
      │
      ├── Known answer found ──► Return saved answer
      │
      ▼
Check data/prediction.json
      │
      ├── Prediction found ──► Return cached prediction
      │
      ▼
Run Laya model
      │
      ├── Save result to prediction.json
      ├── Append one line to logs/MM-DD-YYYY.txt
      └── If NEEDS_REVIEW, call review API
```

Matching uses the subject, sender address, and cleaned body. HTML tags and extra whitespace are normalized before matching and classification.

## Supported email input formats

### Flat format

```json
{
  "subject": "Backup job FAILED",
  "from": "monitoring@example.com",
  "body": "The backup job failed because the target was unreachable."
}
```

### Outlook/Graph-style format

```json
{
  "@odata.etag": "W/\"example\"",
  "subject": "EM Event: Critical database alert",
  "body": {
    "contentType": "html",
    "content": "<html><body><b>Out of memory detected</b></body></html>"
  },
  "from": {
    "emailAddress": {
      "name": "KDPOEM",
      "address": "oracle@example.com"
    }
  }
}
```

The classifier extracts:

- `subject` from `subject`
- Sender address from `from.emailAddress.address`
- Body text from `body.content`

Additional fields such as `@odata.etag` are accepted and ignored by the classifier.

## Model decision rules

The model returns two signals:

1. A categorical choice from `email_type`: `alert` or `non_alert`.
2. A probability from `is_alert`.

The final label is calculated as follows:

```text
If probability >= alert_threshold and choice == alert:
    ALERT

Else if probability <= non_alert_threshold and choice == non_alert:
    NON_ALERT

Else:
    NEEDS_REVIEW
```

The current defaults are:

```json
{
  "alert_threshold": 0.75,
  "non_alert_threshold": 0.3,
  "non_alert_storage_probability": 0.12
}
```

When the final label is `NON_ALERT`, the stored probability is normalized to `0.12` as configured. The model's original probability is used for `ALERT` and `NEEDS_REVIEW` records.

## Prediction record format

`data/prediction.json` contains records similar to:

```json
{
  "subject": "Critical database alert",
  "from": "oracle@example.com",
  "body": "Out of memory detected in the database alert log.",
  "timestamp": "09-27-2026",
  "answer": {
    "subject": "Critical database alert",
    "label": "NEEDS_REVIEW",
    "choice": "alert",
    "alert_probability": 0.643,
    "model_used": "english"
  }
}
```

## Automatic review API

When a new model prediction has the label `NEEDS_REVIEW`, the complete prediction record is sent as a JSON `POST` request.

Configure the endpoint in `config/config.json`:

```json
"review_api": {
  "url": "https://your-api.example.com/alerts",
  "timeout_seconds": 30
}
```

The request body contains the subject, sender, body, timestamp, label, choice, probability, and model name.

If `review_api.url` is empty, the prediction is still stored and logged, but the API request is skipped with a warning. Network and HTTP errors are reported without deleting the prediction.

## Daily prediction logs

Each model prediction is appended as one line to the current date's file:

```text
09-27-2026 14:30:12 | subject=Critical database alert | label=NEEDS_REVIEW | choice=alert | alert_probability=0.643 | model=english
```

The logger removes line breaks and tabs from the subject so every prediction remains on exactly one line.

## Setup

The project uses a Python virtual environment so the Laya dependency stays isolated from the system Python installation.

Run these commands from the project root in PowerShell:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install laya
```

Verify that Laya is installed in the active virtual environment:

```powershell
python -c "from laya import Router; print('Laya installed successfully')"
```

If PowerShell blocks script activation, run the project using the virtual environment's Python executable directly:

```powershell
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install laya
```

## Running the project

Run commands from the project root:

```powershell
python src/email_alert_classifier.py
```

This processes the records in `data/mails.json`. Known records are returned from local data; unknown records use the model.

To manually resend all saved `NEEDS_REVIEW` records to the review API:

```powershell
python src/review_alerts.py
```

The review API URL must be configured before running the resend script.

If the virtual environment is not active, activate it first on Windows:

```powershell
.venv\Scripts\Activate.ps1
```

## Router preload setting

The model router setting is controlled by `config/config.json`:

```json
"router": {
  "preload": true
}
```

With `preload: true`, model checkpoints are loaded when the router starts. This increases startup time but reduces the delay before the first prediction. With `preload: false`, the required model is loaded only when the first uncached email needs classification.

The router is created lazily by the project, so cached emails do not need to initialize Laya.

## Important operational notes

- `data/mails.json` and `data/prediction.json` are local JSON stores and should be backed up if their history matters.
- Do not place secrets directly in `config/config.json` if the project is committed to source control. Use environment variables or a secret manager for API credentials.
- The review API currently uses a JSON `POST` request and a `Content-Type: application/json` header. Add authentication headers in the API request code if the endpoint requires authentication.
- The Laya model warning about uncalibrated confidence values comes from the checkpoint. `alert_probability` should be treated as a model signal, not a guaranteed statistical probability.
- A cached result will not call the model or the review API again. Use `src/review_alerts.py` when previously saved review records need to be resent.
