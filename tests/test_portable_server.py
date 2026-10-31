import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from gate_schedule.server import create_server_app


ROOT = Path(__file__).resolve().parents[1]


class PortableServerTests(unittest.TestCase):
    def test_built_frontend_and_api_are_served_by_one_app(self):
        with tempfile.TemporaryDirectory() as directory:
            client = TestClient(create_server_app(ROOT / "web" / "dist", Path(directory) / "local.sqlite3"))

            page = client.get("/")
            schedule = client.get("/api/schedule")

        self.assertEqual(200, page.status_code)
        self.assertIn("登机口保障排班", page.text)
        self.assertEqual(200, schedule.status_code)


if __name__ == "__main__":
    unittest.main()
