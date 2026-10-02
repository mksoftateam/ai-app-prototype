import os
import tempfile
import unittest

import app


class TaskRegistrationTest(unittest.TestCase):
    def setUp(self):
        self.original_database = app.app.config["DATABASE"]
        self.temp_dir = tempfile.TemporaryDirectory()
        app.app.config.update(
            TESTING=True,
            DATABASE=os.path.join(self.temp_dir.name, "tasks.sqlite3"),
        )
        app.init_db()
        self.client = app.app.test_client()

    def tearDown(self):
        app.app.config["DATABASE"] = self.original_database
        self.temp_dir.cleanup()

    def test_registers_task_and_shows_it_in_list(self):
        response = self.client.post(
            "/",
            data={
                "action": "create",
                "name": "設計レビュー",
                "detail": "仕様を確認する",
                "due_date": "2026-10-10",
                "priority": "高",
                "assignee": "佐藤",
                "status": "進行中",
            },
        )

        self.assertEqual(response.status_code, 302)
        page = self.client.get("/")
        self.assertIn("設計レビュー".encode(), page.data)
        self.assertIn("佐藤".encode(), page.data)
        self.assertIn("作成日".encode(), page.data)
        self.assertIn(b"/tasks/1", page.data)

        detail_page = self.client.get("/tasks/1")
        self.assertEqual(detail_page.status_code, 200)
        for value in ("設計レビュー", "仕様を確認する", "佐藤", "高", "進行中", "2026-10-10"):
            self.assertIn(value.encode(), detail_page.data)
        self.assertIn("更新日".encode(), detail_page.data)

        with app.get_db() as connection:
            task = connection.execute("SELECT * FROM tasks").fetchone()
        self.assertEqual(
            tuple(task[key] for key in ("name", "detail", "due_date", "priority", "assignee", "status")),
            ("設計レビュー", "仕様を確認する", "2026-10-10", "高", "佐藤", "進行中"),
        )
        self.assertTrue(task["created_at"])
        self.assertTrue(task["updated_at"])
        self.assertIsNone(task["completed_at"])
        self.assertIn(task["created_at"][:10].encode(), page.data)
        self.assertIn(task["created_at"][:16].encode(), detail_page.data)
        self.assertIn(task["updated_at"][:16].encode(), detail_page.data)

    def test_dashboard_counts_all_tasks_independently_of_filters(self):
        task_statuses = ("未着手", "進行中", "進行中", "完了", "保留", "保留")
        for index, status in enumerate(task_statuses, start=1):
            self.client.post(
                "/",
                data={
                    "action": "create",
                    "name": f"集計タスク{index}",
                    "detail": "",
                    "due_date": "2026-10-10",
                    "priority": "中",
                    "assignee": "担当者",
                    "status": status,
                },
            )

        page = self.client.get("/?status=完了")

        for metric, count in (
            ("total", 6),
            ("not-started", 1),
            ("in-progress", 2),
            ("completed", 1),
            ("on-hold", 2),
        ):
            with self.subTest(metric=metric):
                self.assertIn(f'data-metric="{metric}">{count}</strong>'.encode(), page.data)
        self.assertIn("集計タスク4".encode(), page.data)
        self.assertNotIn("集計タスク1".encode(), page.data)

    def test_dashboard_shows_zero_counts_when_no_tasks_exist(self):
        page = self.client.get("/")
        for metric in ("total", "not-started", "in-progress", "completed", "on-hold"):
            with self.subTest(metric=metric):
                self.assertIn(f'data-metric="{metric}">0</strong>'.encode(), page.data)

    def test_missing_task_detail_returns_404(self):
        response = self.client.get("/tasks/999")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.client.get("/tasks/999/edit").status_code, 404)
        self.assertEqual(self.client.post("/tasks/999/delete").status_code, 404)
        self.assertEqual(self.client.post("/tasks/999/comments", data={"body": "メモ"}).status_code, 404)

    def test_task_comments_are_saved_and_shown_newest_first(self):
        self.client.post(
            "/",
            data={
                "action": "create",
                "name": "コメント対象",
                "detail": "仕様確認",
                "due_date": "2026-10-10",
                "priority": "中",
                "assignee": "佐藤",
                "status": "進行中",
            },
        )

        first_response = self.client.post(
            "/tasks/1/comments", data={"body": "作業報告\nレビューを開始しました"}
        )
        self.assertEqual(first_response.status_code, 302)
        self.assertEqual(first_response.headers["Location"], "/tasks/1#comments")
        self.client.post("/tasks/1/comments", data={"body": "引継ぎ事項: API仕様を確認"})

        page = self.client.get("/tasks/1")
        self.assertIn("コメント・作業メモ".encode(), page.data)
        self.assertIn("作業報告\nレビューを開始しました".encode(), page.data)
        self.assertIn("引継ぎ事項: API仕様を確認".encode(), page.data)
        self.assertLess(
            page.data.index("引継ぎ事項: API仕様を確認".encode()),
            page.data.index("作業報告\nレビューを開始しました".encode()),
        )

        empty_response = self.client.post("/tasks/1/comments", data={"body": "   "})
        self.assertEqual(empty_response.status_code, 200)
        self.assertIn("コメントを入力してください".encode(), empty_response.data)
        with app.get_db() as connection:
            comments = connection.execute(
                "SELECT body, created_at FROM task_comments WHERE task_id = 1"
            ).fetchall()
        self.assertEqual(len(comments), 2)
        self.assertTrue(all(comment["created_at"] for comment in comments))

    def test_tasks_and_comments_persist_after_database_reinitialization(self):
        database_path = app.app.config["DATABASE"]
        self.client.post(
            "/",
            data={
                "action": "create",
                "name": "永続化対象",
                "detail": "保存を確認",
                "due_date": "2026-10-10",
                "priority": "高",
                "assignee": "佐藤",
                "status": "進行中",
            },
        )
        self.client.post("/tasks/1/comments", data={"body": "再起動後も残るメモ"})
        self.assertTrue(os.path.isfile(database_path))

        app.init_db()
        restarted_client = app.app.test_client()
        list_page = restarted_client.get("/")
        detail_page = restarted_client.get("/tasks/1")

        self.assertIn("永続化対象".encode(), list_page.data)
        self.assertIn("永続化対象".encode(), detail_page.data)
        self.assertIn("再起動後も残るメモ".encode(), detail_page.data)
        with app.get_db() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM tasks").fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM task_comments").fetchone()[0], 1)

    def test_hold_status_can_be_registered_and_edited(self):
        response = self.client.post(
            "/",
            data={
                "action": "create",
                "name": "確認待ちタスク",
                "detail": "回答待ち",
                "due_date": "2026-10-10",
                "priority": "中",
                "assignee": "佐藤",
                "status": "保留",
            },
        )

        self.assertEqual(response.status_code, 302)
        detail_page = self.client.get("/tasks/1")
        self.assertIn("保留".encode(), detail_page.data)
        edit_page = self.client.get("/tasks/1/edit")
        self.assertIn('value="保留" selected'.encode(), edit_page.data)

        response = self.client.post(
            "/tasks/1/edit",
            data={
                "name": "確認待ちタスク",
                "detail": "回答待ち",
                "assignee": "佐藤",
                "priority": "中",
                "status": "保留",
                "due_date": "2026-10-10",
            },
        )
        self.assertEqual(response.status_code, 302)
        with app.get_db() as connection:
            task = connection.execute("SELECT status FROM tasks WHERE id = 1").fetchone()
        self.assertEqual(task["status"], "保留")

    def test_task_search_filters_by_all_supported_fields(self):
        tasks = (
            ("API設計", "佐藤", "進行中", "高", "2026-10-05"),
            ("APIテスト", "鈴木", "保留", "中", "2026-10-10"),
            ("UI設計", "佐藤", "保留", "高", "2026-10-20"),
        )
        for name, assignee, status, priority, due_date in tasks:
            self.client.post(
                "/",
                data={
                    "action": "create",
                    "name": name,
                    "detail": "検索テスト",
                    "due_date": due_date,
                    "priority": priority,
                    "assignee": assignee,
                    "status": status,
                },
            )

        cases = (
            ({"name": "API"}, ("API設計", "APIテスト")),
            ({"assignee": "佐藤"}, ("API設計", "UI設計")),
            ({"status": "保留"}, ("APIテスト", "UI設計")),
            ({"priority": "高"}, ("API設計", "UI設計")),
            ({"due_from": "2026-10-10"}, ("APIテスト", "UI設計")),
            ({"due_to": "2026-10-10"}, ("API設計", "APIテスト")),
            (
                {"name": "API", "assignee": "佐藤", "status": "進行中", "priority": "高"},
                ("API設計",),
            ),
            (
                {"due_from": "2026-10-06", "due_to": "2026-10-15", "status": "保留"},
                ("APIテスト",),
            ),
        )
        for filters, expected_names in cases:
            with self.subTest(filters=filters):
                response = self.client.get("/", query_string=filters)
                self.assertEqual(response.status_code, 200)
                for field in ("name", "assignee", "status", "priority", "due_from", "due_to"):
                    self.assertIn(f'name="{field}"'.encode(), response.data)
                if filters.get("name"):
                    self.assertIn(f'value="{filters["name"]}"'.encode(), response.data)
                if filters.get("assignee"):
                    self.assertIn(f'value="{filters["assignee"]}"'.encode(), response.data)
                if filters.get("status"):
                    self.assertIn(f'value="{filters["status"]}" selected'.encode(), response.data)
                if filters.get("priority"):
                    self.assertIn(f'value="{filters["priority"]}" selected'.encode(), response.data)
                if filters.get("due_from"):
                    self.assertIn(f'value="{filters["due_from"]}"'.encode(), response.data)
                if filters.get("due_to"):
                    self.assertIn(f'value="{filters["due_to"]}"'.encode(), response.data)
                for name in ("API設計", "APIテスト", "UI設計"):
                    if name in expected_names:
                        self.assertIn(name.encode(), response.data)
                    else:
                        self.assertNotIn(name.encode(), response.data)

    def test_task_list_can_be_sorted_by_supported_fields(self):
        tasks = (
            ("仕様レビュー", "2026-10-10", "高"),
            ("障害対応", "2026-10-01", "低"),
            ("週次報告", "2026-10-05", "中"),
        )
        for name, due_date, priority in tasks:
            self.client.post(
                "/",
                data={
                    "action": "create",
                    "name": name,
                    "detail": "ソートテスト",
                    "due_date": due_date,
                    "priority": priority,
                    "assignee": "担当者",
                    "status": "未着手",
                },
            )
        with app.get_db() as connection:
            connection.executemany(
                "UPDATE tasks SET created_at = ?, updated_at = ? WHERE id = ?",
                (
                    ("2026-10-01 09:00:00", "2026-10-30 09:00:00", 1),
                    ("2026-10-03 09:00:00", "2026-10-01 09:00:00", 2),
                    ("2026-10-02 09:00:00", "2026-10-20 09:00:00", 3),
                ),
            )

        cases = (
            ("created_at", "asc", ("仕様レビュー", "週次報告", "障害対応")),
            ("created_at", "desc", ("障害対応", "週次報告", "仕様レビュー")),
            ("updated_at", "asc", ("障害対応", "週次報告", "仕様レビュー")),
            ("updated_at", "desc", ("仕様レビュー", "週次報告", "障害対応")),
            ("due_date", "asc", ("障害対応", "週次報告", "仕様レビュー")),
            ("due_date", "desc", ("仕様レビュー", "週次報告", "障害対応")),
            ("priority", "asc", ("仕様レビュー", "週次報告", "障害対応")),
            ("priority", "desc", ("障害対応", "週次報告", "仕様レビュー")),
        )
        for sort_by, sort_order, expected_order in cases:
            with self.subTest(sort_by=sort_by, sort_order=sort_order):
                response = self.client.get(
                    "/", query_string={"sort_by": sort_by, "sort_order": sort_order}
                )
                self.assertIn(f'value="{sort_by}" selected'.encode(), response.data)
                self.assertIn(f'value="{sort_order}" selected'.encode(), response.data)
                positions = [response.data.index(name.encode()) for name in expected_order]
                self.assertEqual(positions, sorted(positions))

    def test_completion_time_tracks_completion_and_reopening(self):
        self.client.post(
            "/",
            data={
                "action": "create",
                "name": "完了確認",
                "detail": "対応内容",
                "due_date": "2026-10-10",
                "priority": "高",
                "assignee": "佐藤",
                "status": "未着手",
            },
        )
        with app.get_db() as connection:
            connection.execute(
                "UPDATE tasks SET updated_at = '2000-01-01 00:00:00' WHERE id = 1"
            )

        response = self.client.post(
            "/tasks/1/edit",
            data={
                "name": "完了確認",
                "detail": "対応内容",
                "assignee": "佐藤",
                "priority": "高",
                "status": "完了",
                "due_date": "2026-10-10",
            },
        )
        self.assertEqual(response.status_code, 302)
        with app.get_db() as connection:
            completed = connection.execute("SELECT * FROM tasks WHERE id = 1").fetchone()
        completion_time = completed["completed_at"]
        self.assertTrue(completion_time)
        self.assertNotEqual(completed["updated_at"], "2000-01-01 00:00:00")
        detail_page = self.client.get("/tasks/1")
        self.assertIn("完了日時".encode(), detail_page.data)
        self.assertIn(completion_time[:16].encode(), detail_page.data)

        with app.get_db() as connection:
            connection.execute(
                "UPDATE tasks SET updated_at = '2001-01-01 00:00:00' WHERE id = 1"
            )
        self.client.post(
            "/tasks/1/edit",
            data={
                "name": "完了確認（追記）",
                "detail": "追加情報",
                "assignee": "佐藤",
                "priority": "高",
                "status": "完了",
                "due_date": "2026-10-10",
            },
        )
        with app.get_db() as connection:
            still_completed = connection.execute("SELECT * FROM tasks WHERE id = 1").fetchone()
            connection.execute(
                "UPDATE tasks SET updated_at = '2002-01-01 00:00:00' WHERE id = 1"
            )
        self.assertEqual(still_completed["completed_at"], completion_time)

        self.client.post(
            "/tasks/1/edit",
            data={
                "name": "完了確認（再開）",
                "detail": "再対応",
                "assignee": "佐藤",
                "priority": "高",
                "status": "進行中",
                "due_date": "2026-10-10",
            },
        )
        with app.get_db() as connection:
            reopened = connection.execute("SELECT * FROM tasks WHERE id = 1").fetchone()
        self.assertIsNone(reopened["completed_at"])
        self.assertNotEqual(reopened["updated_at"], "2002-01-01 00:00:00")

    def test_registering_completed_task_records_completion_time(self):
        response = self.client.post(
            "/",
            data={
                "action": "create",
                "name": "登録時完了",
                "detail": "すでに対応済み",
                "due_date": "2026-10-10",
                "priority": "中",
                "assignee": "佐藤",
                "status": "完了",
            },
        )
        self.assertEqual(response.status_code, 302)
        with app.get_db() as connection:
            task = connection.execute("SELECT completed_at FROM tasks WHERE id = 1").fetchone()
        self.assertTrue(task["completed_at"])

    def test_delete_requires_post_and_removes_task_from_list(self):
        self.client.post(
            "/",
            data={
                "action": "create",
                "name": "削除対象",
                "detail": "不要なタスク",
                "due_date": "2026-10-10",
                "priority": "低",
                "assignee": "佐藤",
                "status": "未着手",
            },
        )
        self.client.post("/tasks/1/comments", data={"body": "削除時に一緒に消える"})
        detail_page = self.client.get("/tasks/1")
        self.assertIn("confirm(".encode(), detail_page.data)
        self.assertEqual(self.client.get("/tasks/1/delete").status_code, 405)

        response = self.client.post("/tasks/1/delete")

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/")
        self.assertNotIn("削除対象".encode(), self.client.get("/").data)
        self.assertEqual(self.client.get("/tasks/1").status_code, 404)
        with app.get_db() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM tasks").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM task_comments").fetchone()[0], 0)

    def test_edit_updates_fields_and_updated_date(self):
        self.client.post(
            "/",
            data={
                "action": "create",
                "name": "変更前",
                "detail": "元の詳細",
                "due_date": "2026-10-10",
                "priority": "低",
                "assignee": "佐藤",
                "status": "未着手",
            },
        )
        with app.get_db() as connection:
            original = connection.execute("SELECT * FROM tasks WHERE id = 1").fetchone()
            connection.execute(
                "UPDATE tasks SET updated_at = '2000-01-01 00:00:00' WHERE id = 1"
            )

        edit_page = self.client.get("/tasks/1/edit")
        self.assertEqual(edit_page.status_code, 200)
        self.assertIn("value=\"変更前\"".encode(), edit_page.data)

        response = self.client.post(
            "/tasks/1/edit",
            data={
                "name": "変更後",
                "detail": "更新した詳細",
                "assignee": "鈴木",
                "priority": "高",
                "status": "進行中",
                "due_date": "2026-10-20",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/tasks/1")
        with app.get_db() as connection:
            updated = connection.execute("SELECT * FROM tasks WHERE id = 1").fetchone()
        self.assertEqual(
            tuple(updated[key] for key in ("name", "detail", "assignee", "priority", "status", "due_date")),
            ("変更後", "更新した詳細", "鈴木", "高", "進行中", "2026-10-20"),
        )
        self.assertEqual(updated["created_at"], original["created_at"])
        self.assertNotEqual(updated["updated_at"], "2000-01-01 00:00:00")

    def test_invalid_edit_does_not_change_task(self):
        self.client.post(
            "/",
            data={
                "action": "create",
                "name": "元タスク",
                "detail": "元の詳細",
                "due_date": "2026-10-10",
                "priority": "中",
                "assignee": "佐藤",
                "status": "未着手",
            },
        )
        response = self.client.post(
            "/tasks/1/edit",
            data={
                "name": "変更後",
                "detail": "変更した詳細",
                "assignee": "鈴木",
                "priority": "高",
                "status": "進行中",
                "due_date": "invalid-date",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("期限日を正しく入力".encode(), response.data)
        with app.get_db() as connection:
            task = connection.execute("SELECT * FROM tasks WHERE id = 1").fetchone()
        self.assertEqual(task["name"], "元タスク")
        self.assertEqual(task["detail"], "元の詳細")

    def test_migrates_existing_database(self):
        with app.get_db() as connection:
            connection.execute("DROP TABLE tasks")
            connection.execute(
                """CREATE TABLE tasks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    detail TEXT NOT NULL,
                    due_date TEXT NOT NULL,
                    priority TEXT NOT NULL,
                    assignee TEXT NOT NULL,
                    status TEXT NOT NULL
                )"""
            )
            connection.execute(
                """INSERT INTO tasks
                   (name, detail, due_date, priority, assignee, status)
                   VALUES ('既存タスク', '', '2026-10-10', '中', '佐藤', '未着手')"""
            )
            connection.execute(
                """INSERT INTO tasks
                   (name, detail, due_date, priority, assignee, status)
                   VALUES ('完了済み既存タスク', '', '2026-10-10', '低', '鈴木', '完了')"""
            )

        app.init_db()
        with app.get_db() as connection:
            tasks = connection.execute("SELECT * FROM tasks ORDER BY id").fetchall()
        self.assertTrue(tasks[0]["created_at"])
        self.assertEqual(tasks[0]["updated_at"], tasks[0]["created_at"])
        self.assertIsNone(tasks[0]["completed_at"])
        self.assertEqual(tasks[1]["completed_at"], tasks[1]["updated_at"])

    def test_rejects_invalid_due_date(self):
        response = self.client.post(
            "/",
            data={
                "action": "create",
                "name": "期限不正",
                "due_date": "not-a-date",
                "priority": "中",
                "assignee": "佐藤",
                "status": "未着手",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("期限日を正しく入力".encode(), response.data)
        with app.get_db() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM tasks").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()