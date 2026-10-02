import os
import sqlite3
from datetime import date

from flask import Flask, abort, redirect, render_template, request, url_for

app = Flask(__name__)
app.config["DATABASE"] = os.path.join(app.instance_path, "tasks.sqlite3")

PRIORITIES = ("高", "中", "低")
STATUSES = ("未着手", "進行中", "保留", "完了")
SORT_COLUMNS = {
    "created_at": "created_at",
    "updated_at": "updated_at",
    "due_date": "due_date",
    "priority": "CASE priority WHEN '高' THEN 1 WHEN '中' THEN 2 WHEN '低' THEN 3 ELSE 4 END",
}
SORT_OPTIONS = (
    ("created_at", "作成日"),
    ("updated_at", "更新日"),
    ("due_date", "期限日"),
    ("priority", "優先度"),
)


def validate_task_fields(name, due_date, priority, assignee, status):
    try:
        date.fromisoformat(due_date)
    except ValueError:
        return "期限日を正しく入力してください。"
    if not name or not assignee:
        return "タスク名と担当者を入力してください。"
    if priority not in PRIORITIES or status not in STATUSES:
        return "優先度またはステータスの選択内容が正しくありません。"
    return ""


def get_task_or_404(task_id):
    with get_db() as connection:
        task = connection.execute(
            "SELECT * FROM tasks WHERE id = ?", (task_id,)
        ).fetchone()
    if task is None:
        abort(404)
    return task


def get_db():
    connection = sqlite3.connect(app.config["DATABASE"])
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def init_db():
    os.makedirs(os.path.dirname(app.config["DATABASE"]), exist_ok=True)
    with get_db() as connection:
        connection.execute(
            """CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                detail TEXT NOT NULL,
                due_date TEXT NOT NULL,
                priority TEXT NOT NULL,
                assignee TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                completed_at TEXT
            )"""
        )
        columns = connection.execute("PRAGMA table_info(tasks)").fetchall()
        column_names = {column["name"] for column in columns}
        if "created_at" not in column_names:
            connection.execute(
                "ALTER TABLE tasks ADD COLUMN created_at TEXT NOT NULL DEFAULT ''"
            )
            connection.execute(
                "UPDATE tasks SET created_at = datetime('now', 'localtime') "
                "WHERE created_at = ''"
            )
        if "updated_at" not in column_names:
            connection.execute(
                "ALTER TABLE tasks ADD COLUMN updated_at TEXT NOT NULL DEFAULT ''"
            )
            connection.execute(
                "UPDATE tasks SET updated_at = created_at WHERE updated_at = ''"
            )
        if "completed_at" not in column_names:
            connection.execute("ALTER TABLE tasks ADD COLUMN completed_at TEXT")
            connection.execute(
                "UPDATE tasks SET completed_at = updated_at WHERE status = '完了'"
            )
        connection.execute(
            """CREATE TABLE IF NOT EXISTS task_comments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
                body TEXT NOT NULL,
                created_at TEXT NOT NULL
            )"""
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_task_comments_task_created "
            "ON task_comments(task_id, created_at DESC, id DESC)"
        )


init_db()

@app.route("/", methods=["GET", "POST"])
def index():
    comment = ""
    error = ""

    if request.method == "POST":
        if request.form.get("action") == "comment":
            api_key = os.environ.get("GEMINI_API_KEY")
            if not api_key:
                error = "AIコメント機能には GEMINI_API_KEY の設定が必要です。"
            else:
                from google import genai

                task = request.form.get("task", "").strip()
                with open("prompts/system_prompt.txt", "r", encoding="utf-8") as f:
                    prompt_template = f.read()
                prompt = prompt_template.replace("{TASK}", task)
                response = genai.Client(api_key=api_key).models.generate_content(
                    model="gemini-3.6-flash",
                    contents=prompt,
                )
                comment = response.text
        else:

            name = request.form.get("name", "").strip()
            detail = request.form.get("detail", "").strip()
            due_date = request.form.get("due_date", "").strip()
            priority = request.form.get("priority", "")
            assignee = request.form.get("assignee", "").strip()
            status = request.form.get("status", "")

            error = validate_task_fields(name, due_date, priority, assignee, status)
            if not error:
                with get_db() as connection:
                    connection.execute(
                        """INSERT INTO tasks
                                    (name, detail, due_date, priority, assignee, status,
                                     created_at, updated_at, completed_at)
                                    VALUES (?, ?, ?, ?, ?, ?, datetime('now', 'localtime'),
                                              datetime('now', 'localtime'),
                                              CASE WHEN ? = '完了' THEN datetime('now', 'localtime') END)""",
                        (name, detail, due_date, priority, assignee, status, status),
                    )
                return redirect(url_for("index"))

    filters = {
        "name": request.args.get("name", "").strip(),
        "assignee": request.args.get("assignee", "").strip(),
        "status": request.args.get("status", ""),
        "priority": request.args.get("priority", ""),
        "due_from": request.args.get("due_from", ""),
        "due_to": request.args.get("due_to", ""),
    }
    if filters["status"] not in STATUSES:
        filters["status"] = ""
    if filters["priority"] not in PRIORITIES:
        filters["priority"] = ""
    for field in ("due_from", "due_to"):
        try:
            if filters[field]:
                date.fromisoformat(filters[field])
        except ValueError:
            filters[field] = ""
    sort_by = request.args.get("sort_by", "due_date")
    if sort_by not in SORT_COLUMNS:
        sort_by = "due_date"
    sort_order = request.args.get("sort_order", "asc")
    if sort_order not in ("asc", "desc"):
        sort_order = "asc"

    conditions = []
    parameters = []
    for field in ("name", "assignee"):
        if filters[field]:
            escaped = filters[field].replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            conditions.append(f"{field} LIKE ? ESCAPE '\\'")
            parameters.append(f"%{escaped}%")
    for field in ("status", "priority"):
        if filters[field]:
            conditions.append(f"{field} = ?")
            parameters.append(filters[field])
    if filters["due_from"]:
        conditions.append("due_date >= ?")
        parameters.append(filters["due_from"])
    if filters["due_to"]:
        conditions.append("due_date <= ?")
        parameters.append(filters["due_to"])

    query = "SELECT * FROM tasks"
    if conditions:
        query += " WHERE " + " AND ".join(conditions)
    query += f" ORDER BY {SORT_COLUMNS[sort_by]} {sort_order.upper()}, id DESC"
    status_counts = {status: 0 for status in STATUSES}
    with get_db() as connection:
        total_tasks = connection.execute(
            "SELECT COUNT(*) AS total FROM tasks"
        ).fetchone()["total"]
        for row in connection.execute(
            "SELECT status, COUNT(*) AS total FROM tasks GROUP BY status"
        ):
            if row["status"] in status_counts:
                status_counts[row["status"]] = row["total"]
        tasks = connection.execute(query, parameters).fetchall()

    return render_template(
        "index.html",
        comment=comment,
        error=error,
        tasks=tasks,
        priorities=PRIORITIES,
        statuses=STATUSES,
        filters=filters,
        sort_options=SORT_OPTIONS,
        sort_by=sort_by,
        sort_order=sort_order,
        dashboard={"total": total_tasks, "statuses": status_counts},
    )


@app.route("/tasks/<int:task_id>")
def task_detail(task_id):
    return render_task_detail(task_id)


def render_task_detail(task_id, comment_error="", comment_value=""):
    task = get_task_or_404(task_id)
    with get_db() as connection:
        comments = connection.execute(
            "SELECT * FROM task_comments WHERE task_id = ? "
            "ORDER BY created_at DESC, id DESC",
            (task_id,),
        ).fetchall()
    return render_template(
        "task_detail.html",
        task=task,
        comments=comments,
        comment_error=comment_error,
        comment_value=comment_value,
    )


@app.route("/tasks/<int:task_id>/comments", methods=["POST"])
def add_task_comment(task_id):
    get_task_or_404(task_id)
    body = request.form.get("body", "").strip()
    if not body:
        return render_task_detail(
            task_id,
            comment_error="コメントを入力してください。",
            comment_value=request.form.get("body", ""),
        )

    with get_db() as connection:
        connection.execute(
            "INSERT INTO task_comments (task_id, body, created_at) "
            "VALUES (?, ?, datetime('now', 'localtime'))",
            (task_id, body),
        )
    return redirect(url_for("task_detail", task_id=task_id) + "#comments")


@app.route("/tasks/<int:task_id>/edit", methods=["GET", "POST"])
def edit_task(task_id):
    task = get_task_or_404(task_id)
    values = {
        "name": task["name"],
        "detail": task["detail"],
        "assignee": task["assignee"],
        "priority": task["priority"],
        "status": task["status"],
        "due_date": task["due_date"],
    }
    error = ""

    if request.method == "POST":
        values = {field: request.form.get(field, "").strip() for field in values}
        error = validate_task_fields(
            values["name"],
            values["due_date"],
            values["priority"],
            values["assignee"],
            values["status"],
        )
        if not error:
            with get_db() as connection:
                connection.execute(
                    """UPDATE tasks
                       SET name = ?, detail = ?, assignee = ?, priority = ?,
                           status = ?, due_date = ?,
                           updated_at = datetime('now', 'localtime'),
                           completed_at = CASE
                               WHEN ? = '完了' THEN COALESCE(completed_at, datetime('now', 'localtime'))
                               ELSE NULL
                           END
                       WHERE id = ?""",
                    (
                        values["name"],
                        values["detail"],
                        values["assignee"],
                        values["priority"],
                        values["status"],
                        values["due_date"],
                        values["status"],
                        task_id,
                    ),
                )
            return redirect(url_for("task_detail", task_id=task_id))

    return render_template(
        "task_edit.html",
        task=task,
        values=values,
        error=error,
        priorities=PRIORITIES,
        statuses=STATUSES,
    )


@app.route("/tasks/<int:task_id>/delete", methods=["POST"])
def delete_task(task_id):
    get_task_or_404(task_id)
    with get_db() as connection:
        connection.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
    return redirect(url_for("index"))


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)