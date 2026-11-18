#!/usr/bin/env python3
"""Keep one FAIL and one SUCCESS Telegram message across GitHub Actions runs.

Persist message IDs as two GitHub Actions repository variables. Requires a
fine-grained PAT with Variables: Read and Write on this repository. Never
prints or saves the Telegram bot token. No Oracle API calls are performed.
"""

import datetime as dt
import hashlib
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

REGION = "ap-singapore-2"
OCPUS = 1.0
MEMORY_GB = 6.0

STATE_VARIABLES = {
    "FAIL": "OCI_A1_TELEGRAM_LAST_FAIL",
    "SUCCESS": "OCI_A1_TELEGRAM_LAST_SUCCESS",
}


class SafeApiError(Exception):
    def __init__(self, service, status, detail):
        self.service = service
        self.status = status
        self.detail = detail
        super().__init__(f"{service} HTTP {status}: {detail}")


def request_json(service, method, url, payload=None, auth_token=None, as_form=False):
    """Make an API request; never include a Telegram token in exceptions/logs."""
    headers = {"Accept": "application/json"}
    data = None
    if auth_token:
        headers.update({
            "Authorization": f"Bearer {auth_token}",
            "X-GitHub-Api-Version": "2022-11-28",
        })
    if payload is not None:
        if as_form:
            data = urllib.parse.urlencode(payload).encode("utf-8")
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        else:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            body = response.read()
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as exc:
        try:
            error_body = json.loads(exc.read().decode("utf-8", errors="replace"))
            detail = error_body.get("description") or error_body.get("message") or "API rejected request"
        except (ValueError, UnicodeError, AttributeError):
            detail = "API rejected request"
        raise SafeApiError(service, exc.code, str(detail)[:200]) from None
    except urllib.error.URLError:
        raise SafeApiError(service, 0, "Network request failed") from None


def telegram(method, token, data):
    response = request_json(
        "Telegram", "POST", f"https://api.telegram.org/bot{token}/{method}",
        payload=data, as_form=True,
    )
    if not response.get("ok"):
        raise SafeApiError("Telegram", response.get("error_code", 0),
                           str(response.get("description", "Unknown error"))[:200])
    return response.get("result")


def github_variable_api(owner_repo, pat, method, variable_name=None, payload=None):
    owner, repo = owner_repo.split("/", 1)
    base = ("https://api.github.com/repos/"
            f"{urllib.parse.quote(owner, safe='')}/"
            f"{urllib.parse.quote(repo, safe='')}/actions/variables")
    url = base if variable_name is None else base + "/" + urllib.parse.quote(variable_name, safe="")
    return request_json("GitHub", method, url, payload=payload, auth_token=pat)


def last_state(owner_repo, pat, name):
    try:
        value = github_variable_api(owner_repo, pat, "GET", name)["value"]
    except SafeApiError as exc:
        if exc.status == 404:
            return None
        raise
    try:
        state = json.loads(value)
        if not isinstance(state, dict):
            raise ValueError("not an object")
        return state
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"Invalid state in GitHub variable {name}") from exc


def save_state(owner_repo, pat, name, state, exists):
    payload = {"name": name, "value": json.dumps(state, separators=(",", ":"))}
    if exists:
        github_variable_api(owner_repo, pat, "PATCH", name, payload)
    else:
        github_variable_api(owner_repo, pat, "POST", payload=payload)


def message_text(result):
    when = dt.datetime.now(ZoneInfo("Asia/Ho_Chi_Minh")).strftime("%H:%M:%S · %d/%m/%Y (VN)")
    kinds = {
        "INSTANCE_CREATED": "✅ SUCCESS — Đã gửi lệnh tạo VPS A1",
        "ALREADY_EXISTS": "✅ SUCCESS — VPS A1 đã tồn tại (không tạo thêm)",
        "NO_CAPACITY": "❌ FAIL — Oracle đang hết dung lượng A1",
        "RATE_LIMITED": "❌ FAIL — Oracle giới hạn tần suất",
        "ORACLE_ERROR": "❌ FAIL — Lỗi Oracle API",
        "SETUP_ERROR": "❌ FAIL — Lỗi cấu hình hoặc chương trình",
    }
    repo, run_id = os.environ.get("GITHUB_REPOSITORY", ""), os.environ.get("GITHUB_RUN_ID", "")
    run_url = f"https://github.com/{repo}/actions/runs/{run_id}" if repo and run_id else ""
    lines = [kinds.get(result, f"❌ FAIL — {result}"),
             f"Thời gian mới nhất: {when}",
             f"Khu vực: {REGION} | A1.Flex {OCPUS:g} OCPU / {MEMORY_GB:g} GB",
             f"Kết quả: {result}"]
    if run_url:
        lines.append(f"Chi tiết: {run_url}")
    return "\n".join(lines)


def notify_telegram(result):
    """Keep a maximum of one recorded FAIL and one recorded SUCCESS message."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    pat = os.environ.get("GH_VARIABLES_TOKEN", "").strip()
    owner_repo = os.environ.get("GITHUB_REPOSITORY", "").strip()
    if not all((token, chat_id, pat, owner_repo)) or owner_repo.count("/") != 1:
        raise RuntimeError("Notification needs TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, "
                           "GH_VARIABLES_TOKEN and GITHUB_REPOSITORY")

    chat_key = hashlib.sha256(chat_id.encode("utf-8")).hexdigest()
    kind = "SUCCESS" if result in ("INSTANCE_CREATED", "ALREADY_EXISTS") else "FAIL"
    variable_name = STATE_VARIABLES[kind]
    old = last_state(owner_repo, pat, variable_name)
    # A subsequent ALREADY_EXISTS poll is not a new instance creation.
    # Keep the original SUCCESS notification without repeatedly pinging the user.
    if result == "ALREADY_EXISTS" and old and str(old.get("chat_key")) == chat_key:
        print("TELEGRAM_SUCCESS: already reported; no repeat notification")
        return
    message = message_text(result)
    now_utc = dt.datetime.now(dt.timezone.utc)
    old_id = None
    if old and str(old.get("chat_key")) == chat_key:
        try:
            old_id = int(old["message_id"])
        except (ValueError, TypeError, KeyError):
            raise RuntimeError(f"Invalid message_id in {variable_name}") from None
        if old_id <= 0:
            raise RuntimeError(f"Invalid message_id in {variable_name}")

    # Prefer delete + send for a *fresh bottom-of-chat notification*.
    # Telegram restricts deletion to the first 48 hours; use a 47-hour margin.
    if old_id is not None:
        age = None
        try:
            sent_at = dt.datetime.fromisoformat(old["sent_at_utc"])
            age = now_utc - sent_at
        except (KeyError, TypeError, ValueError):
            pass
        deleted = False
        missing = False
        if age is not None and dt.timedelta(0) <= age < dt.timedelta(hours=47):
            try:
                telegram("deleteMessage", token, {"chat_id": chat_id, "message_id": old_id})
                deleted = True
            except SafeApiError as exc:
                if exc.status == 400 and "message to delete not found" in exc.detail.lower():
                    missing = True
                else:
                    print(f"NOTICE: Old {kind} message cannot be deleted; editing instead.", file=sys.stderr)
        if not deleted and not missing:
            try:
                telegram("editMessageText", token, {
                    "chat_id": chat_id, "message_id": old_id, "text": message,
                    "link_preview_options": json.dumps({"is_disabled": True}),
                })
                old["updated_at_utc"] = now_utc.isoformat()
                save_state(owner_repo, pat, variable_name, old, exists=True)
                print(f"TELEGRAM_{kind}: updated existing message {old_id}")
                return
            except SafeApiError as exc:
                if exc.status == 400 and "message is not modified" in exc.detail.lower():
                    print(f"TELEGRAM_{kind}: no change")
                    return
                if exc.status == 400 and "message to edit not found" in exc.detail.lower():
                    missing = True  # User already removed the message manually.
                else:
                    raise  # Never send duplicates when an existing message is not editable.

    posted = telegram("sendMessage", token, {
        "chat_id": chat_id, "text": message, "link_preview_options": json.dumps({"is_disabled": True}),
    })
    new_id = posted["message_id"]
    try:
        save_state(owner_repo, pat, variable_name, {
            "chat_key": chat_key,
            "message_id": new_id,
            "sent_at_utc": now_utc.isoformat(),
            "updated_at_utc": now_utc.isoformat(),
        }, exists=old is not None)
    except Exception:
        # Avoid orphan/duplicate messages if GitHub fails to remember the ID.
        try:
            telegram("deleteMessage", token, {"chat_id": chat_id, "message_id": new_id})
        except SafeApiError:
            pass
        raise
    print(f"TELEGRAM_{kind}: new message {new_id}; previous one removed if possible")


def main():
    outcome = os.environ.get("A1_RESULT", "").strip() or "WORKFLOW_ERROR"
    notify_telegram(outcome)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        # Do not print request URLs or tokens. SafeApiError has sanitized messages.
        print(f"TELEGRAM_NOTIFY_ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(1)
