from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

import build_excel_macro
import diagnose_jira_auth
from build_excel_macro import (
    COLUMNS,
    EXAMPLE_VALUES,
    OFFLINE_ISSUE_TYPES,
    build_button_module,
    build_macro,
)
from ticket_schema import SUPPORTED_ISSUE_TYPES


ROOT = Path(__file__).resolve().parents[1]


class BuildCompatibilityTests(unittest.TestCase):
    def test_vba_entry_point_and_python_launcher_are_preserved(self):
        module = build_button_module()
        self.assertIn("Sub Tickets_Erstellen()", module)
        self.assertIn("create_tickets.py", module)
        self.assertIn("ThisWorkbook.Save", module)
        self.assertIn("Private Function FindPython", module)
        self.assertIn("Sub Jira_Daten_Aktualisieren()", module)
        self.assertIn("Worksheet_Change", build_macro(COLUMNS.index("Components") + 1))

    def test_button_names_remain_in_builder_source(self):
        source = (ROOT / "build_excel_macro.py").read_text(encoding="utf-8")
        self.assertIn('"btnTicketsErstellen"', source)
        self.assertIn('"Tickets_Erstellen"', source)
        self.assertIn('"btnJiraDatenAktualisieren"', source)

    def test_offline_issue_types_match_supported_schema(self):
        self.assertEqual(tuple(OFFLINE_ISSUE_TYPES), SUPPORTED_ISSUE_TYPES)
        self.assertEqual(COLUMNS[:4], ["Action", "Project", "IssueType", "Summary"])
        self.assertTrue(hasattr(EXAMPLE_VALUES["DueDate"], "strftime"))

    def test_existing_output_is_refused_before_config_or_jira_access(self):
        with TemporaryDirectory() as directory:
            target = Path(directory) / "tickets.xlsm"
            target.write_bytes(b"existing")
            with patch("build_excel_macro.load_config") as load:
                with self.assertRaisesRegex(RuntimeError, "nicht ueberschrieben"):
                    build_excel_macro.main(["--output", str(target)])
            load.assert_not_called()
            self.assertEqual(target.read_bytes(), b"existing")

    @patch("build_excel_macro.requests.get")
    def test_jira_issue_type_metadata_is_filtered_to_supported_types(self, get):
        response = Mock(status_code=200)
        response.headers = {}
        response.json.return_value = {
            "issueTypes": [
                {"name": "Task", "subtask": False},
                {"name": "Bug", "subtask": False},
                {"name": "Story", "subtask": False},
                {"name": "Sub-task", "subtask": True},
            ]
        }
        get.return_value = response
        result = build_excel_macro.fetch_issue_types(
            {
                "JIRA_URL": "https://jira.example.test",
                "JIRA_TOKEN": "offline-token",
                "PROJECT_KEY": "DAH",
            }
        )
        self.assertEqual(result, ["Story", "Bug"])

    @patch("build_excel_macro.requests.get")
    def test_metadata_error_does_not_include_response_body(self, get):
        response = Mock(status_code=500, text="sensitive-body")
        response.headers = {}
        get.return_value = response
        with self.assertRaises(RuntimeError) as context:
            build_excel_macro.fetch_components(
                {
                    "JIRA_URL": "https://jira.example.test",
                    "JIRA_TOKEN": "offline-token",
                    "PROJECT_KEY": "DAH",
                }
            )
        self.assertNotIn("sensitive-body", str(context.exception))

    def test_diagnostic_output_never_prints_response_body(self):
        response = Mock(status_code=500, text="secret response body")
        response.headers = {"Content-Type": "application/json"}
        output = StringIO()
        with redirect_stdout(output):
            diagnose_jira_auth.print_response("failure", response, ("version",))
        self.assertNotIn("secret response body", output.getvalue())


class CustomizationDiscoveryTests(unittest.TestCase):
    REQUIRED = (
        "docs/PROJECT_REQUIREMENTS.md",
        ".github/copilot-instructions.md",
        ".github/agents/excel-jira-reviewer.agent.md",
        ".github/instructions/python.instructions.md",
        ".github/instructions/excel-vba.instructions.md",
        ".github/instructions/tests.instructions.md",
        ".github/skills/jira-data-center-api/SKILL.md",
        ".github/skills/excel-xlsm-automation/SKILL.md",
        ".github/skills/jira-excel-sync/SKILL.md",
        ".github/skills/jira-excel-testing/SKILL.md",
    )

    def test_all_required_customizations_exist(self):
        for relative in self.REQUIRED:
            with self.subTest(path=relative):
                self.assertTrue((ROOT / relative).is_file())

    def test_frontmatter_has_descriptions_and_discovery_fields(self):
        frontmatter_files = [
            relative
            for relative in self.REQUIRED
            if relative.endswith((".agent.md", ".instructions.md", "SKILL.md"))
        ]
        for relative in frontmatter_files:
            text = (ROOT / relative).read_text(encoding="utf-8")
            with self.subTest(path=relative):
                self.assertTrue(text.startswith("---\n"))
                frontmatter = text.split("---", 2)[1]
                self.assertIn("description:", frontmatter)
                if relative.endswith(".instructions.md"):
                    self.assertIn("applyTo:", frontmatter)
                if relative.endswith("SKILL.md"):
                    expected = Path(relative).parent.name
                    self.assertIn(f"name: {expected}", frontmatter)

    def test_reviewer_agent_is_read_only(self):
        agent = (ROOT / ".github/agents/excel-jira-reviewer.agent.md").read_text(
            encoding="utf-8"
        )
        frontmatter = agent.split("---", 2)[1]
        self.assertIn("tools: [read, search]", frontmatter)
        self.assertNotIn("edit", frontmatter)
        self.assertNotIn("execute", frontmatter)

    def test_skills_have_no_executable_assets(self):
        skill_root = ROOT / ".github/skills"
        extras = [
            path
            for path in skill_root.rglob("*")
            if path.is_file() and path.name != "SKILL.md"
        ]
        self.assertEqual(extras, [])

    def test_requirements_are_authoritative_and_cover_all_sections(self):
        requirements = (ROOT / "docs/PROJECT_REQUIREMENTS.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("verbindliche fachliche Quelle", requirements)
        for section in range(1, 21):
            self.assertIn(f"## {section}.", requirements)
        instructions = (ROOT / ".github/copilot-instructions.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("docs/PROJECT_REQUIREMENTS.md", instructions)


if __name__ == "__main__":
    unittest.main()
