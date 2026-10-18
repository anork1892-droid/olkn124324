"""Documentation topics differ from requests to change or execute software."""
import unittest

from bountyos.connectors import qualify
from test_engine import issue


class DocumentationScopeTests(unittest.TestCase):
    def task(self, description, **changes):
        return qualify(issue(**({"body": "Pays 25 USD. " + description} | changes)))

    def test_existing_technical_topics_do_not_require_running_software(self):
        for body in (
            "Explain how to deploy using the existing README commands. Documentation only; no execution required.",
            "Add a README section describing existing GPU support from the published guide.",
            "Clarify the documented benchmark results without running benchmarks.",
            "Clarify the smart contract terminology using the existing guide.",
            "Write a guide explaining how to run tests using the documented commands.",
            "Describe how to implement the documented interface; use only existing examples.",
            "Document command options without executing commands. No benchmarks are needed.",
            "Do not deploy. Explain the existing README options.",
        ):
            with self.subTest(body=body):
                self.assertEqual(self.task(body)["status"], "qualified")

    def test_explicit_implementation_or_runtime_work_stays_blocked(self):
        for body in (
            "Implement a GPU benchmark and documentation.",
            "Add tests covering the API and update README.",
            "Deploy the application and write a guide.",
            "Run benchmarks and document results.",
            "Execute the integration tests and document failures.",
            "Measure latency and update README with the measured results.",
            "Fix the code and clarify README.",
            "Create a smart contract and write its documentation.",
            "Document deployment and implement feature X.",
            "Explain how to deploy, then run benchmarks.",
            "No need to deploy, but implement the feature.",
            "Do not run tests; deploy the application.",
            "Without executing commands, add tests for the API.",
        ):
            with self.subTest(body=body):
                task = self.task(body)
                self.assertEqual(task["status"], "blocked")
                self.assertIn("kräver kod, mätningar eller körning", task["reason"])

    def test_exact_documentation_labels_supply_scope_when_title_does_not(self):
        for label in ("docs", "documentation", "Documentation"):
            with self.subTest(label=label):
                task = self.task("Clarify the existing command descriptions.", title="Bounty $25: improve installation steps",
                                 labels={"nodes": [{"name": label}]})
                self.assertEqual(task["status"], "qualified")

    def test_incidental_label_text_does_not_supply_documentation_scope(self):
        for label in ("needs-docs", "not-documentation", "bounty"):
            with self.subTest(label=label):
                task = self.task("Clarify the existing options.", title="Bounty $25: improve installation steps",
                                 labels={"nodes": [{"name": label}]})
                self.assertEqual(task["status"], "blocked")
                self.assertEqual(task["kind"], "other")

    def test_documentation_label_does_not_override_mixed_work_or_other_gates(self):
        base = {"title": "Bounty $25: improve installation steps", "labels": {"nodes": [{"name": "docs"}]}}
        for change in (
            {"body": "Pays 25 USD. Implement the feature and update documentation."},
            {"state": "CLOSED"},
            {"authorAssociation": "NONE"},
            {"assignedActors": {"nodes": [{"login": "someone"}]}},
            {"linkedPullRequests": {"nodes": [{"state": "OPEN"}]}},
        ):
            with self.subTest(change=change):
                self.assertEqual(self.task("Explain existing command options.", **(base | change))["status"], "blocked")


if __name__ == "__main__":
    unittest.main()
