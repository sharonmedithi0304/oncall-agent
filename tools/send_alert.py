import os
import sys
import json
import urllib.request
import argparse

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SAMPLES_DIR = os.path.join(ROOT_DIR, "samples")


def send_alert(source: str, base_url: str = "http://127.0.0.1:8001", secret: str = None) -> dict:
    source = source.lower()
    sample_file = os.path.join(SAMPLES_DIR, f"{source}.json")
    if not os.path.exists(sample_file):
        raise FileNotFoundError(f"Sample file not found: {sample_file}")

    with open(sample_file, "r", encoding="utf-8") as f:
        payload = json.load(f)

    webhook_secret = secret or os.environ.get("WEBHOOK_SECRET", "dev-secret")
    url = f"{base_url.rstrip('/')}/api/ingest/{source}"

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Content-Type": "application/json",
            "X-Webhook-Secret": webhook_secret,
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = resp.read().decode("utf-8")
            return json.loads(body) if body else {"status": resp.status}
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8")
        print(f"Error {e.code}: {err_body}", file=sys.stderr)
        raise


def main():
    parser = argparse.ArgumentParser(description="Send realistic alert webhook to OnCall Agent")
    parser.add_argument("source", choices=["datadog", "sentry", "prometheus", "cloudwatch", "generic"], help="Alert source to simulate")
    parser.add_argument("--url", default=os.environ.get("AGENT_URL", "http://127.0.0.1:8001"), help="Agent base URL")
    parser.add_argument("--secret", default=os.environ.get("WEBHOOK_SECRET", "dev-secret"), help="Webhook secret")

    args = parser.parse_args()
    try:
        print(f"Sending sample alert for '{args.source}' to {args.url}/api/ingest/{args.source}...")
        res = send_alert(args.source, base_url=args.url, secret=args.secret)
        print("Success! Agent response:")
        print(json.dumps(res, indent=2))
    except Exception as e:
        print(f"Failed to send alert: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
