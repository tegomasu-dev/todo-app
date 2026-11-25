import os
import json
import uuid
from datetime import datetime, timedelta, timezone
from urllib import request as urlrequest, error as urlerror
from flask import Flask, render_template, request, redirect, url_for
import gspread
from google.oauth2.service_account import Credentials
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)

SPREADSHEET_ID = os.environ.get("SPREADSHEET_ID", "")
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")
LINE_USER_ID = os.environ.get("LINE_USER_ID", "")

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

PRIORITY_ORDER = {"高": 1, "中": 2, "低": 3}
JST = timezone(timedelta(hours=9))


def get_sheet():
    creds_json = os.environ.get("GOOGLE_CREDENTIALS_JSON")
    if creds_json:
        info = json.loads(creds_json)
        creds = Credentials.from_service_account_info(info, scopes=SCOPES)
    else:
        creds = Credentials.from_service_account_file("credentials.json", scopes=SCOPES)
    client = gspread.authorize(creds)
    return client.open_by_key(SPREADSHEET_ID).worksheet("todos")


def normalize_due_date(val):
    if not val:
        return ""
    return str(val).strip().replace("/", "-")


def normalize_todo(row):
    row["due_date"] = normalize_due_date(row.get("due_date", ""))
    row["priority"] = row.get("priority") or "中"
    row["status"] = row.get("status") or "未完了"
    return row


def find_todo_row(sheet, todo_id):
    records = sheet.get_all_records()
    for idx, row in enumerate(records, start=2):
        if str(row.get("id", "")) == str(todo_id):
            return idx, normalize_todo(row)
    return None, None


def send_line_message(text):
    token = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "") or LINE_CHANNEL_ACCESS_TOKEN
    user_id = os.environ.get("LINE_USER_ID", "") or LINE_USER_ID
    if not token or not user_id:
        print("LINE通知スキップ: LINE_CHANNEL_ACCESS_TOKEN または LINE_USER_ID が未設定です。")
        return False

    url = "https://api.line.me/v2/bot/message/push"
    payload = {
        "to": user_id,
        "messages": [{"type": "text", "text": text}],
    }
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}",
    }
    req = urlrequest.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urlrequest.urlopen(req, timeout=10) as res:
            return 200 <= res.status < 300
    except urlerror.HTTPError as e:
        print(f"LINE送信エラー (HTTP {e.code}): {e.read().decode('utf-8', errors='ignore')}")
        return False
    except Exception as e:
        print(f"LINE送信エラー: {e}")
        return False


@app.route("/")
def index():
    sheet = get_sheet()
    records = [normalize_todo(r) for r in sheet.get_all_records()]

    status_filter = request.args.get("status", "all")
    if status_filter == "incomplete":
        records = [r for r in records if r["status"] != "完了"]

    sort_by = request.args.get("sort", "default")
    if sort_by == "due_date":
        records.sort(key=lambda x: (x["due_date"] == "", str(x["due_date"])))
    elif sort_by == "priority":
        records.sort(
            key=lambda x: (
                PRIORITY_ORDER.get(x["priority"], 2),
                x["due_date"] == "",
                str(x["due_date"]),
            )
        )

    notified = request.args.get("notified")
    return render_template(
        "index.html",
        todos=records,
        current_sort=sort_by,
        current_status=status_filter,
        notified=notified,
    )


@app.route("/new", methods=["GET", "POST"])
def new():
    if request.method == "POST":
        title = request.form["title"]
        content = request.form["content"]
        due_date = normalize_due_date(request.form["due_date"])
        priority = request.form.get("priority", "中")
        status = "未完了"
        todo_id = str(uuid.uuid4())[:8]

        sheet = get_sheet()
        sheet.append_row([todo_id, title, content, due_date, priority, status])

        msg = (
            "【新規タスク登録】\n"
            f"・タイトル：{title}\n"
            f"・期日：{due_date or '未設定'}\n"
            f"・重要度：{priority}"
        )
        send_line_message(msg)
        return redirect(url_for("index"))

    return render_template("form.html", todo=None, mode="new")


@app.route("/edit/<todo_id>", methods=["GET", "POST"])
def edit(todo_id):
    sheet = get_sheet()
    target_row, target_todo = find_todo_row(sheet, todo_id)

    if request.method == "POST" and target_row:
        title = request.form["title"]
        content = request.form["content"]
        due_date = normalize_due_date(request.form["due_date"])
        priority = request.form.get("priority", "中")
        status = target_todo.get("status") or "未完了"

        sheet.update(
            f"A{target_row}:F{target_row}",
            [[todo_id, title, content, due_date, priority, status]],
        )
        return redirect(url_for("index"))

    return render_template("form.html", todo=target_todo, mode="edit")


@app.route("/toggle/<todo_id>", methods=["POST"])
def toggle_status(todo_id):
    sheet = get_sheet()
    target_row, target_todo = find_todo_row(sheet, todo_id)
    if target_row and target_todo:
        new_status = "完了" if target_todo["status"] != "完了" else "未完了"
        sheet.update_cell(target_row, 6, new_status)
    return redirect(
        url_for(
            "index",
            sort=request.args.get("sort", "default"),
            status=request.args.get("status", "all"),
        )
    )


@app.route("/delete/<todo_id>", methods=["POST"])
def delete_todo(todo_id):
    sheet = get_sheet()
    target_row, _ = find_todo_row(sheet, todo_id)
    if target_row:
        sheet.delete_rows(target_row)
    return redirect(
        url_for(
            "index",
            sort=request.args.get("sort", "default"),
            status=request.args.get("status", "all"),
        )
    )


@app.route("/notify", methods=["POST"])
def notify_due_tasks():
    sheet = get_sheet()
    records = [normalize_todo(r) for r in sheet.get_all_records()]

    today_str = datetime.now(JST).strftime("%Y-%m-%d")
    tomorrow_str = (datetime.now(JST) + timedelta(days=1)).strftime("%Y-%m-%d")
    target_dates = {today_str, tomorrow_str}

    target_todos = [
        r for r in records
        if r.get("status") != "完了" and normalize_due_date(r.get("due_date", "")) in target_dates
    ]

    if target_todos:
        lines = ["【今日・明日が期日の未完了タスク】"]
        for t in target_todos:
            due_val = normalize_due_date(t.get("due_date", ""))
            due_label = "今日" if due_val == today_str else "明日"
            lines.append(f"・[{due_label}・重要度:{t.get('priority', '中')}] {t.get('title', '')}（{due_val}）")
        msg = "\n".join(lines)
    else:
        msg = "【タスク通知】\n今日・明日が期日の未完了タスクはありません！"

    ok = send_line_message(msg)
    return redirect(
        url_for(
            "index",
            sort=request.args.get("sort", "default"),
            status=request.args.get("status", "all"),
            notified="sent" if ok else "error",
        )
    )


if __name__ == "__main__":
    app.run(debug=True, port=5001)
