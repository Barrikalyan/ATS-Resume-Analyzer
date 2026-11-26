import os
import tempfile

from backend.persistence import configure_supabase, delete_application, read_applications, save_application, write_applications


class FakeResponse:
    def __init__(self, data):
        self.data = data


class FakeTable:
    def __init__(self, rows):
        self.rows = rows
        self.operation = None
        self.selection = None
        self.removed_ids = []

    def select(self, fields):
        self.operation = "select"
        self.selection = fields
        return self

    def upsert(self, rows, on_conflict=None):
        self.operation = "upsert"
        self.upsert_rows = rows
        return self

    def delete(self):
        self.operation = "delete"
        return self

    def eq(self, field, value):
        assert field == "id"
        self.removed_ids = [value]
        return self

    def execute(self):
        if self.operation == "select":
            if self.selection == "id":
                return FakeResponse([{"id": key} for key in self.rows])
            return FakeResponse([{"data": value["data"]} for value in self.rows.values()])
        if self.operation == "upsert":
            rows = self.upsert_rows if isinstance(self.upsert_rows, list) else [self.upsert_rows]
            for row in rows:
                self.rows[row["id"]] = row
            return FakeResponse([])
        if self.operation == "delete":
            for candidate_id in self.removed_ids:
                self.rows.pop(candidate_id, None)
            return FakeResponse([])
        raise AssertionError(f"Unexpected operation: {self.operation}")


class FakeSupabase:
    def __init__(self):
        self.rows = {}

    def table(self, name):
        assert name == "applications"
        return FakeTable(self.rows)


def test_supabase_application_persistence_uses_remote_rows():
    client = FakeSupabase()
    configure_supabase(client)
    local_path = os.path.join(tempfile.gettempdir(), "supabase-applications-test.json")
    if os.path.exists(local_path):
        os.remove(local_path)

    try:
        apps = [{"id": "REF-1", "name": "Example", "resume_path": "FullStackDeveloper/file.pdf"}]
        write_applications(local_path, apps)
        assert read_applications(local_path) == apps
        assert not os.path.exists(local_path)

        write_applications(local_path, [{"id": "REF-2", "name": "Second"}])
        assert read_applications(local_path) == apps + [{"id": "REF-2", "name": "Second"}]
        save_application(local_path, {"id": "REF-2", "name": "Updated"})
        assert read_applications(local_path) == apps + [{"id": "REF-2", "name": "Updated"}]
        delete_application(local_path, "REF-1")
        assert read_applications(local_path) == [{"id": "REF-2", "name": "Updated"}]
    finally:
        configure_supabase(None)
