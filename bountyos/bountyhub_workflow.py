"""BountyHub discovery and specialist routing, sharing the local worker capacity."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path, PurePosixPath
import threading
import time
import uuid

from .connectors import ConnectorError, HTTPFailure, RateLimited, issue_parts, qualify


class BountyHubWorkflow:
    def _init_bountyhub(self, bountyhub, code_worker, use_default):
        from .bountyhub import BountyHub
        from .code_sandbox import DockerSandbox
        from .code_worker import CodeWorker
        self.bountyhub = bountyhub or (BountyHub(http=self.github.http, state_store=self.store) if use_default else None)
        self.code_sandbox = DockerSandbox(Path(self.store.data_dir) / "work" / "code-tests")
        self.code_worker = code_worker or CodeWorker(self.github, self._generate, self.code_sandbox)
        self._bountyhub_scan_lock = threading.Lock()
        self._bountyhub_scan_thread = None
        self._next_bountyhub_scan = 0.0
        if self.bountyhub:
            self._connection("bountyhub", {"configured": True, "status": "unknown", "detail": "BountyHubs katalog har inte kontrollerats. Publika GitHub-annonser används som reservkälla."})
            self._connection("code_tests", {"configured": False, "status": "unknown", "detail": "Kodagentens isolerade Python-tester har inte kontrollerats."})

    def _register_bountyhub_agents(self):
        if not self.bountyhub:
            return
        for key, name, role, detail in (
            ("bountyhub-scout", "BountyHub · sökare", "bountyhub_scout", "Söker BountyHubs katalog och använder publika GitHub-annonser som reservkälla."),
            ("bountyhub-docs", "BountyHub · dokumentation", "bountyhub_docs", "Granskar underlag, planerar, skriver och förbättrar dokumentation."),
            ("bountyhub-code", "BountyHub · små kodändringar", "bountyhub_code", "Små Python-ändringar med isolerade tester. Delar modell och arbetskö med dokumentationsagenten."),
        ):
            self.store.set_agent(key, name=name, role=role, status="idle", task=None, detail=detail)

    def _qualify_task(self, issue, task):
        if task.get("source") == "bountyhub":
            if not self.bountyhub:
                raise ConnectorError("BountyHub-källan är inte tillgänglig.")
            settings = self.store.get_settings()
            return self.bountyhub.qualify(issue, max_sek=settings["bountyhub_max_sek"], assume_usd=settings["bountyhub_assume_usd"])
        return qualify(issue)

    @staticmethod
    def _source_work_issue(issue, candidate):
        from .bountyhub import BountyHub
        return BountyHub.work_issue(issue, candidate) if candidate.get("bountyhub_requirements") else issue

    @staticmethod
    def _source_signature(issue):
        evidence = {key: issue.get(key, "") for key in ("url", "title", "body")}
        return hashlib.sha256(json.dumps(evidence, sort_keys=True, ensure_ascii=False).encode()).hexdigest()

    def _bountyhub_scan_now(self):
        if (not self.bountyhub or not self.store.get_settings()["bountyhub_enabled"]
                or self._stop.is_set() or not self._bountyhub_scan_lock.acquire(blocking=False)):
            return
        self._bountyhub_scan_thread = threading.Thread(target=self._scan_bountyhub, name="bountyhub-scout", daemon=True)
        self._bountyhub_scan_thread.start()

    def _scan_bountyhub(self):
        self.store.set_agent("bountyhub-scout", status="scouting", task=None, detail="Kontrollerar BountyHub-annonser och originalärenden.")
        checked = set()
        counts = {"qualified": 0, "blocked": 0, "skipped": 0}
        new_count = 0
        read_failures = 0
        error = None

        def assess(task):
            nonlocal read_failures
            if task["url"] in checked or len(checked) >= 12 or self._stop.is_set():
                return
            checked.add(task["url"])
            try:
                candidate = self._qualify_task(self.bountyhub.issue(task["url"]), task)
            except ConnectorError as exc:
                read_failures += 1
                with self._task_lock:
                    current = self.store.get_task(task["id"])
                    if current and current["status"] == "discovered":
                        if isinstance(exc, HTTPFailure) and exc.status_code in {404, 410}:
                            self.store.update_task(task["id"], status="blocked", reason="Originalärendet är borttaget eller kan inte längre läsas offentligt (HTTP " + str(exc.status_code) + ").")
                        else:
                            self.store.update_task(task["id"], reason="BountyHub-granskningen väntar: " + str(exc))
                if isinstance(exc, RateLimited):
                    raise
                return
            with self._task_lock:
                current = self.store.get_task(task["id"])
                if not current or current["status"] != "discovered":
                    return
                self.store.upsert_task(candidate)
                self.store.update_task(task["id"], status=candidate["status"], reason=candidate["reason"])
            counts[candidate["status"]] = counts.get(candidate["status"], 0) + 1
            if candidate.get("original_issue_url"):
                self._queue_original(candidate)
                self._next_scan = 0

        try:
            existing = {task["url"]: task for task in self.store.list_tasks()}
            pending = sorted((task for task in existing.values() if task.get("source") == "bountyhub" and task["status"] == "discovered"), key=lambda t: (t["updated_at"], t["id"]))
            for task in pending[:6]:
                assess(task)
            if not self._stop.is_set():
                urls = self.bountyhub.search(limit=20)
                queued = []
                for url in dict.fromkeys(urls):
                    issue_parts(url)
                    with self._task_lock:
                        prior = next((task for task in self.store.list_tasks() if task["url"] == url), None)
                        if prior:
                            # Promote only untouched discoveries/rejections; never restart actual work or a delivery.
                            if prior.get("source") != "bountyhub" and prior["status"] in {"discovered", "blocked", "skipped"} and not any(prior.get(key) for key in ("attempts", "worker_id", "artifact_path", "submission_url")):
                                self.store.update_task(prior["id"], source="bountyhub", status="discovered", reason="BountyHub-annons hittad; väntar på källspecifik granskning.")
                                prior = self.store.get_task(prior["id"])
                        else:
                            repo, number = issue_parts(url)
                            task_id = self.store.upsert_task({"url": url, "repo": repo, "title": f"{repo} #{number}", "issue_number": number,
                                                              "source": "bountyhub", "status": "discovered", "reason": "Väntar på granskning av BountyHub-annons."})
                            prior = self.store.get_task(task_id)
                            new_count += 1
                    if prior.get("source") == "bountyhub" and prior["status"] == "discovered":
                        queued.append(prior)
                # Persist the whole successful page before any fallible issue read.
                for task in queued:
                    assess(task)
                source_status = getattr(self.bountyhub, "last_status", None)
                if not isinstance(source_status, dict):
                    source_status = {"configured": True, "status": "connected", "detail": "BountyHub-annonser via GitHubs index. Direkt plattformsstatus och utbetalning är inte verifierade."}
                self._connection("bountyhub", source_status)
        except ConnectorError as exc:
            error = str(exc)
            self._connection("bountyhub", {"configured": True, "status": "error", "detail": error})
        except Exception:
            error = "BountyHub-sökningen fick ett oväntat svar och försöker igen senare."
            self._connection("bountyhub", {"configured": True, "status": "error", "detail": error})
        finally:
            interval = max(60, self.store.get_settings()["poll_interval_minutes"] * 60)
            cooldown = self.bountyhub.http.retry_after_seconds()
            delay = max(interval, cooldown if isinstance(cooldown, (float, int)) else 0)
            self._next_bountyhub_scan = time.monotonic() + delay
            detail = f"BountyHub: {new_count} nya träffar, {sum(counts.values())} kontrollerade, {counts['qualified']} utvalda, {counts['skipped']} bortvalda, {counts['blocked']} blockerade."
            if error:
                detail += " " + error
            if read_failures:
                detail += f" {read_failures} ärenden kunde inte läsas; övriga uppdrag hanteras separat."
            self.store.log(detail, level="warning" if error else "info")
            self.store.set_agent("bountyhub-scout", status="idle", task=None, detail=detail,
                                 next_scan_at=(datetime.now(timezone.utc) + timedelta(seconds=delay)).isoformat())
            self._bountyhub_scan_lock.release()

    def _worker_eligible(self, task, settings):
        if not self._specialist_enabled(task, settings):
            return False
        if task.get("source") != "bountyhub":
            return True
        if task.get("specialist") == "code":
            return self.connection_status().get("code_tests", {}).get("status") == "connected"
        return True

    @staticmethod
    def _specialist_enabled(task, settings):
        return task.get("source") != "bountyhub" or (settings["bountyhub_enabled"] and settings["bountyhub_code_enabled" if task.get("specialist") == "code" else "bountyhub_docs_enabled"])

    def _next_ready_task(self, settings):
        eligible = (task for task in self.store.list_tasks() if task["status"] == "ready" and self._specialist_enabled(task, settings))
        return min(eligible, key=lambda task: (task["created_at"], task["id"]), default=None)

    @staticmethod
    def _task_priority(task):
        estimate = task.get("price_estimate") or {}
        preference = estimate.get("within_preference")
        # A soft preference never removes larger or unconverted offers from the queue.
        return (0 if preference is True else 2 if preference is False else 1,
                task.get("created_at", ""), task["id"])

    def _work_code(self, worker_id, task):
        stage = "preparation"
        self.store.set_agent(worker_id, name="BountyHub · små kodändringar", role="bountyhub_code", status="preparing", task=task["id"], detail="Kontrollerar omfattning och testmiljö.")
        try:
            if self._work_paused(task):
                return
            issue = self.github.issue(task["url"])
            eligible = self._qualify_task(issue, task)
            if eligible["status"] != "qualified":
                self.store.update_task(task["id"], status=eligible["status"], reason=eligible["reason"])
                return
            if eligible.get("specialist") != "code":
                self.store.update_task(task["id"], status="discovered", reason="Uppdragets omfattning har ändrats och granskas på nytt.")
                return
            issue = self._source_work_issue(issue, eligible)
            self.store.update_task(task["id"], work_plan=None, test_report=None)

            def stopped():
                return self._work_paused(task)

            def progress(current_stage, payload):
                nonlocal stage
                stage = current_stage
                status = {"preparation": "preparing", "writing": "working", "testing": "testing", "review": "reviewing"}.get(stage, "working")
                detail = {"preparation": "Läser en bestämd projektversion och kontrollerar arbetsplanen.", "writing": "Skriver en liten kodändring och ett regressionstest.",
                          "testing": "Kör automatiska tester i en isolerad miljö.", "review": "Granskar ändringen mot uppdraget och testresultaten."}.get(stage, "Arbetar med uppdraget.")
                fields = {key: payload[key] for key in ("work_plan", "test_report") if isinstance(payload, dict) and key in payload}
                self.store.update_task(task["id"], status=status, reason=detail, **fields)
                self.store.set_agent(worker_id, status=status, detail=detail)

            result = self.code_worker.execute(issue, should_stop=stopped, on_stage=progress)
            if self._work_paused(task):
                return
            review = result["review"]
            if review.get("accepted") is not True or review.get("findings"):
                raise ConnectorError("Kodleveransen saknar en godkänd separat granskning.")
            result["context"]["source_issue_signature"] = self._source_signature(issue)
            artifact = self._save_code_artifact(task, result)
            self.store.update_task(task["id"], status="ready", artifact_path=str(artifact), test_report=result["test_report"], work_plan=result["work_plan"],
                                   delivery_summary=result["delivery"]["summary"], reason="Kodändring och automatiska tester är granskade. Väntar på inlämning när den är aktiverad och GitHub är anslutet.")
            self.store.log("BountyHub-kodleverans klar med automatiska tester: " + task["title"], level="success")
            self._submit_ready(self.store.get_task(task["id"]))
        except ConnectorError as exc:
            if not self._work_paused(task):
                self.store.update_task(task["id"], status="blocked", reason=str(exc), blocked_stage=stage)
                self.store.log("Kodagenten stoppade ett uppdrag: " + str(exc), level="warning")
        except Exception:
            if not self._work_paused(task):
                self.store.update_task(task["id"], status="failed", blocked_stage=stage, reason="Kodagenten fick ett oväntat fel. Ingen inlämning gjordes.")
        finally:
            self.store.set_agent(worker_id, status="idle", task=None, detail=None)

    def _save_code_artifact(self, task, result):
        delivery = result["delivery"]
        for item in delivery["files"]:
            path = item["path"]
            parts = PurePosixPath(path).parts
            if (not isinstance(path, str) or not path or path.startswith("/") or "\\" in path or ":" in path
                    or any(part in {"..", "."} or part.startswith(".") for part in parts) or not path.endswith(".py")):
                raise ConnectorError("Kodleveransen innehåller en otillåten sökväg.")
        key = hashlib.sha256(str(task["id"]).encode()).hexdigest()[:24]
        directory = Path(self.store.data_dir) / "artifacts" / key / uuid.uuid4().hex
        directory.mkdir(parents=True, exist_ok=False)
        for item in delivery["files"]:
            target = directory / "files" / item["path"]
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(item["content"], encoding="utf-8")
        envelope = {"task_url": task["url"], "kind": "code", **result}
        (directory / "delivery.json").write_text(json.dumps(envelope, ensure_ascii=False, indent=2), encoding="utf-8")
        manifest = "# Kodleverans\n\n" + task["url"] + "\n\n" + delivery["summary"] + "\n\n"
        manifest += "Automatiska testresultat:\n\n" + json.dumps(result["test_report"], ensure_ascii=False, indent=2) + "\n\n"
        manifest += "Separat modellgranskning godkänd. Detta är inte en accepterad bounty eller mottagen betalning. Automatisk inlämning kräver GitHub-anslutning och oförändrad testad projektversion.\n\n"
        for item in delivery["files"]:
            manifest += "## " + item["path"] + "\n\n" + item["content"] + "\n\n"
        (directory / "delivery.md").write_text(manifest, encoding="utf-8")
        return directory / "delivery.md"
