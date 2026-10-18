"""Source metadata tables must not turn old mirrored ads into new work."""
import tempfile
import unittest
from unittest.mock import Mock

from bountyos.connectors import original_issue_reference, qualify
from bountyos.engine import Engine
from bountyos.store import Store
from test_engine import issue


MIRROR = "https://github.com/Vikingr2023/awesome-agent-bounties/issues/253"
ORIGINAL = "https://github.com/auscaster/frantic-board/issues/299"
OTHER = "https://github.com/owner/project/issues/7"


def mirrored_issue():
    # Public issue #253's source metadata and description, without tracking data.
    return issue(
        url=MIRROR, repo="Vikingr2023/awesome-agent-bounties", number=253,
        title="[auscaster/frantic-board] Frantic bounty #113: Get Sourcey docs adopted upstream",
        body=("## 外部 Bounty 任务镜像\n\n"
              "| 字段 | 值 |\n|---|---|\n"
              "| 源仓库 | auscaster/frantic-board |\n| 原 issue | #299 |\n"
              "| 作者 | auscaster |\n| 赏金 | $18 |\n"
              f"| 原 URL | {ORIGINAL} |\n\n---\n\n"
              "### 任务描述\n\nFrantic bounty #113\n\n"
              "Get Sourcey docs adopted upstream\n\n"
              "Worker price: $18\nSlots: 1\nStatus: Available\n"
              "Claim: https://gofrantic.com/bounties/113\n"
              "Funding receipt: https://gofrantic.com/r/d1a9b770\n\n"
              "Frantic is the source of truth. This GitHub issue is a mirrored board thread."),
    )


class SourceTableParsingTests(unittest.TestCase):
    def test_recognized_source_fields_support_plain_and_markdown_urls(self):
        for label in ("原 URL", "原始链接", "Source URL", "Original URL", "source url",
                      "原始链接 / Source URL", "**Original URL**"):
            for link in (ORIGINAL, f"[original issue]({ORIGINAL})", f"[{ORIGINAL}]({ORIGINAL})"):
                with self.subTest(label=label, link=link):
                    body = f"| Field | Value |\n|---|---|\n| {label} | {link} |\n"
                    self.assertEqual(original_issue_reference(body), ORIGINAL)

    def test_heading_and_table_with_same_source_agree(self):
        body = f"| Source URL | {ORIGINAL} |\n\n### Original URL\n{ORIGINAL}\n"
        self.assertEqual(original_issue_reference(body), ORIGINAL)

    def test_invalid_missing_multiple_and_conflicting_sources_block(self):
        values = ("", "#299", "https://evil.example/issues/299", "http://127.0.0.1:11434",
                  ORIGINAL + "#issuecomment-1", ORIGINAL + " " + OTHER,
                  f"[{ORIGINAL}]({OTHER})")
        for value in values:
            with self.subTest(value=value):
                self.assertEqual(original_issue_reference(f"| Source URL | {value} |\n"), "")
        for body in (f"| Source URL | {ORIGINAL} |\n| Original URL | {OTHER} |\n",
                     f"| 原 URL | {ORIGINAL} |\n\n### Source URL\n{OTHER}\n",
                     f"| Original URL | |\n\n### Source URL\n{ORIGINAL}\n"):
            with self.subTest(body=body):
                self.assertEqual(original_issue_reference(body), "")

    def test_ordinary_reference_fields_and_prose_are_not_redirects(self):
        for body in (f"See also {ORIGINAL}", f"| See also | {ORIGINAL} |\n",
                     f"| Funding receipt | {ORIGINAL} |\n", f"| Claim | {ORIGINAL} |\n",
                     f"| Not the Source URL | {ORIGINAL} |\n"):
            with self.subTest(body=body):
                self.assertIsNone(original_issue_reference(body))

    def test_self_reference_is_not_followed(self):
        result = qualify(issue(url=MIRROR, body=f"Pays $18.\n| Source URL | {MIRROR} |\n"))
        self.assertEqual(result["status"], "blocked")
        self.assertNotIn("original_issue_url", result)

    def test_actual_mirror_source_excludes_claim_and_funding_links(self):
        result = qualify(mirrored_issue())
        self.assertEqual(result["status"], "skipped")
        self.assertEqual(result["original_issue_url"], ORIGINAL)


class ClosedTableSourceTests(unittest.TestCase):
    def test_open_mirror_of_closed_paid_job_is_not_claimed_or_written(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(directory)
            github = Mock()
            github.search.return_value = [MIRROR]
            github.issue.side_effect = {
                MIRROR: mirrored_issue(),
                ORIGINAL: issue(
                    url=ORIGINAL, repo="auscaster/frantic-board", number=299,
                    title="Frantic bounty #113: Get Sourcey docs adopted upstream",
                    state="CLOSED",
                    body="Worker price: $18\nSlots: 1 (filled)\nStatus: paid\n"
                         "Claim: https://gofrantic.com/bounties/113\n"
                         "Frantic is the source of truth. This GitHub issue is a mirrored board thread.",
                ),
            }.__getitem__
            engine = Engine(store, github, Mock(), Mock(configured=False))
            try:
                engine._scan_lock.acquire()
                engine._scan()
                tasks = {task["url"]: task for task in store.list_tasks()}
                self.assertEqual(tasks[MIRROR]["status"], "skipped")
                self.assertEqual(tasks[ORIGINAL]["status"], "blocked")
                self.assertIn("inte öppet", tasks[ORIGINAL]["reason"])
                self.assertIsNone(store.claim_task("worker-1"))
                github.repository_context.assert_not_called()
                engine.model.generate.assert_not_called()
                self.assertEqual(github.issue.call_count, 2)
            finally:
                engine.stop()


if __name__ == "__main__":
    unittest.main()
