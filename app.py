import os
import json
import uuid
from flask import Flask, render_template, request, redirect, url_for
import gspread
from google.oauth2.service_account import Credentials

app = Flask(__name__)

SPREADSHEET_ID = os.environ.get("SPREADSHEET_ID", "14kl8drxS2YSH4QLKabF0OmkLMmMzDEB9pFzHnctdnXs")

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

def get_sheet():
    creds_json = os.environ.get("GOOGLE_CREDENTIALS_JSON")
    if creds_json:
        info = json.loads(creds_json)
        creds = Credentials.from_service_account_info(info, scopes=SCOPES)
    else:
        creds = Credentials.from_service_account_file("credentials.json", scopes=SCOPES)
    client = gspread.authorize(creds)
    return client.open_by_key(SPREADSHEET_ID).worksheet("todos")

@app.route("/")
def index():
    sheet = get_sheet()
    records = sheet.get_all_records()
    return render_template("index.html", todos=records)

@app.route("/new", methods=["GET", "POST"])
def new():
    if request.method == "POST":
        title = request.form["title"]
        content = request.form["content"]
        due_date = request.form["due_date"]
        todo_id = str(uuid.uuid4())[:8]
        sheet = get_sheet()
        sheet.append_row([todo_id, title, content, due_date])
        return redirect(url_for("index"))
    return render_template("form.html", todo=None)

@app.route("/edit/<todo_id>", methods=["GET", "POST"])
def edit(todo_id):
    sheet = get_sheet()
    records = sheet.get_all_records()
    target_row = None
    target_todo = None

    for idx, row in enumerate(records, start=2):
        if str(row["id"]) == str(todo_id):
            target_row = idx
            target_todo = row
            break

    if request.method == "POST" and target_row:
        title = request.form["title"]
        content = request.form["content"]
        due_date = request.form["due_date"]
        sheet.update(f"A{target_row}:D{target_row}", [[todo_id, title, content, due_date]])
        return redirect(url_for("index"))

    return render_template("form.html", todo=target_todo)

if __name__ == "__main__":
    app.run(debug=True)
