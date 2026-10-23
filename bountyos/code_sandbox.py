"""Bounded, disposable Docker runs for dependency-free Python unittest projects.

Repository Python never runs on the host. The image must already be installed;
there is no image pull, package installation, shell command or network access in
the test container. Passing means the supplied tests ran successfully, not that
untrusted tests prove the code correct. Python tests can deliberately manipulate
their own runner; the independent review and baseline/regression comparison are
still required. A printed "passed" message alone is never a successful result.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import tempfile
import threading
import uuid


IMAGE = "python:3.12-slim"
MAX_OUTPUT_BYTES = 64 * 1024
MAX_FILES = 60
MAX_SOURCE_BYTES = 500 * 1024
_REPORT_PREFIX = "BOUNTYOS_UNITTEST_REPORT:"
_COUNTERS = ("tests_run", "failures", "errors", "skipped", "expected_failures", "unexpected_successes")

# The harness is supplied by the application as a fixed -c argument, outside
# repository files. Import stdlib before putting repository paths on sys.path.
# A random per-run marker separates the report from ordinary printed test data;
# it is not a security boundary against intentionally adversarial Python tests.
_HARNESS = r'''
import json, os, sys, unittest
nonce = sys.argv[1]
sys.argv = ["bountyos-unittest"]
emit = os.write
finish = os._exit
encode = json.dumps
# Use one container stream: Docker may otherwise deliver stderr after a final
# stdout report even when Python flushes both before writing that report.
os.dup2(2, 1)
report_fd = os.dup(2)
loader = unittest.TestLoader()
runner = unittest.TextTestRunner(stream=sys.stderr, verbosity=2)
sys.path[:0] = ["/work", "/work/src"]
report = {"tests_run": 0, "failures": 0, "errors": 0, "skipped": 0,
          "expected_failures": 0, "unexpected_successes": 0}
exit_code = 2
try:
    suite = loader.discover("/work/tests", pattern="test_*.py")
    result = runner.run(suite)
    report.update(tests_run=result.testsRun, failures=len(result.failures),
                  errors=len(result.errors), skipped=len(result.skipped),
                  expected_failures=len(result.expectedFailures),
                  unexpected_successes=len(result.unexpectedSuccesses))
    ordinary_tests = result.testsRun - len(result.skipped) - len(result.expectedFailures)
    exit_code = 0 if result.wasSuccessful() and ordinary_tests > 0 else 1
except BaseException as exc:
    report["errors"] = max(1, report["errors"])
    try:
        sys.stderr.write("Test runner error: " + type(exc).__name__ + ": " + str(exc)[:1000] + "\n")
    except BaseException:
        pass
try:
    sys.stdout.flush()
    sys.stderr.flush()
    emit(report_fd, ("\nBOUNTYOS_UNITTEST_REPORT:" + nonce + ":" + encode(report, separators=(",", ":")) + "\n").encode("utf-8"))
finally:
    finish(exit_code)
'''


@dataclass
class _CommandResult:
    exit_code: int
    output: bytes = b""
    tail: bytes = b""
    timed_out: bool = False
    truncated: bool = False


def _bounded_process(argv: list[str], timeout: float, limit: int = MAX_OUTPUT_BYTES) -> _CommandResult:
    """Continuously drain pipes while retaining at most limit + 8 KiB bytes."""
    process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, shell=False)
    head, tail = bytearray(), bytearray()
    total = 0
    lock = threading.Lock()

    def drain():
        nonlocal total
        try:
            while True:
                chunk = process.stdout.read(4096)
                if not chunk:
                    break
                with lock:
                    total += len(chunk)
                    remaining = max(0, limit - len(head))
                    head.extend(chunk[:remaining])
                    tail.extend(chunk)
                    if len(tail) > 8192:
                        del tail[:-8192]
        finally:
            process.stdout.close()

    reader = threading.Thread(target=drain, name="bountyos-docker-output", daemon=True)
    reader.start()
    timed_out = False
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        process.kill()
        process.wait(timeout=5)
    reader.join(timeout=2)
    with lock:
        return _CommandResult(process.returncode if process.returncode is not None else -1,
                              bytes(head), bytes(tail), timed_out, total > limit)


def _display_output(raw: bytes) -> str:
    # Keep newlines/tabs, remove terminal control characters and escape codes.
    text = raw.decode("utf-8", errors="replace")
    text = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", text)
    return "".join(char for char in text if char in "\n\t" or ord(char) >= 32 and ord(char) != 127)


def _validated_files(files: dict[str, str]) -> dict[str, str]:
    if not isinstance(files, dict) or not files or len(files) > MAX_FILES:
        raise ValueError("Testunderlaget måste innehålla 1–60 filer.")
    checked, casefolded, total = {}, set(), 0
    extensions = {".py", ".md", ".txt", ".toml", ".json", ".cfg", ".ini"}
    devices = {"CON", "PRN", "AUX", "NUL"} | {f"{name}{number}" for name in ("COM", "LPT") for number in range(1, 10)}
    for path, content in files.items():
        if not isinstance(path, str) or not isinstance(content, str):
            raise ValueError("Testunderlaget måste vara filsökvägar med textinnehåll.")
        parts = path.split("/")
        if (not path or len(path) > 180 or len(parts) > 8 or
                any(not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,79}", part) or
                    part.endswith(".") or part.split(".")[0].upper() in devices for part in parts) or
                PurePosixPath(path).suffix.lower() not in extensions):
            raise ValueError("Otillåten filsökväg i testunderlaget: " + path[:180])
        folded = path.casefold()
        if folded in casefolded:
            raise ValueError("Testunderlaget innehåller filnamn som krockar på Windows.")
        casefolded.add(folded)
        total += len(content.encode("utf-8"))
        if total > MAX_SOURCE_BYTES:
            raise ValueError("Testunderlaget är större än 500 KiB.")
        checked[path] = content
    for path in checked:
        parts = path.split("/")
        if any("/".join(parts[:index]).casefold() in casefolded for index in range(1, len(parts))):
            raise ValueError("En fil i testunderlaget används också som mapp.")
    if not any(path.startswith("tests/") and PurePosixPath(path).name.startswith("test_") and path.endswith(".py") for path in checked):
        raise ValueError("Inga Python-unittest-filer hittades under tests/test_*.py.")
    return checked


class DockerSandbox:
    """Use an existing Linux Docker engine and local python:3.12-slim image."""

    def __init__(self, work_root: str | Path | None = None):
        self.work_root = Path(work_root) if work_root is not None else Path("data/work/code-tests")

    def check(self) -> dict:
        docker = shutil.which("docker")
        if not docker:
            return self._availability(False, "Docker saknas. Installera och starta Docker Desktop med Linux-containrar för kodagentens tester.")
        try:
            engine = _bounded_process([docker, "info", "--format", "{{.OSType}}"], 8, 4096)
            if engine.timed_out or engine.exit_code != 0:
                return self._availability(False, "Docker svarar inte. Starta Docker Desktop och välj Linux-containrar.")
            if engine.output.decode("utf-8", errors="replace").strip() != "linux":
                return self._availability(False, "Kodagenten kräver Docker med Linux-containrar.")
            inspected = _bounded_process([docker, "image", "inspect", IMAGE, "--format", "{{.Id}}"], 8, 4096)
            image_id = inspected.output.decode("utf-8", errors="replace").strip()
            if inspected.timed_out or inspected.exit_code != 0 or not re.fullmatch(r"sha256:[a-f0-9]{64}", image_id):
                return self._availability(False, "Testimagen saknas lokalt. Kör docker pull python:3.12-slim i Terminal en gång.")
            return self._availability(True, "Python-unittest körs i en begränsad Docker-container utan nätverk.") | {"image_id": image_id}
        except (OSError, subprocess.SubprocessError):
            return self._availability(False, "Docker kunde inte kontrolleras. Starta Docker Desktop och försök igen.")

    @staticmethod
    def _availability(available: bool, detail: str) -> dict:
        return {"configured": available, "status": "connected" if available else "error", "detail": detail, "image": IMAGE}

    @staticmethod
    def _result(detail: str, **overrides) -> dict:
        result = {"passed": False, "exit_code": -1, "output": "", "test_count": 0, "tests_run": 0,
                  "failures": 0, "errors": 0, "skipped": 0, "expected_failures": 0, "unexpected_successes": 0,
                  "timed_out": False, "status": "blocked", "detail": detail, "image": IMAGE}
        result.update(overrides)
        for source, alias in (("tests_run", "test_count"), ("failures", "failure_count"), ("errors", "error_count"),
                              ("skipped", "skipped_count"), ("expected_failures", "expected_failure_count"),
                              ("unexpected_successes", "unexpected_success_count")):
            result[alias] = result[source]
        return result

    def _stage(self, root: Path, files: dict[str, str]):
        root.chmod(0o755)
        for name, content in files.items():
            target = root.joinpath(*name.split("/"))
            target.parent.mkdir(parents=True, exist_ok=True)
            for parent in target.parents:
                if parent == root.parent:
                    break
                if parent.is_symlink():
                    raise ValueError("Testunderlaget får inte innehålla symboliska länkar.")
                parent.chmod(0o755)
            with target.open("x", encoding="utf-8", newline="") as handle:
                handle.write(content)
            target.chmod(0o644)

    def _work_directory(self) -> Path:
        root = Path(os.path.abspath(self.work_root))
        if "," in str(root):
            raise ValueError("Testmappens sökväg får inte innehålla kommatecken.")
        for path in (*reversed(root.parents), root):
            if path.is_symlink():
                raise ValueError("Testmappen får inte gå genom symboliska länkar.")
        root.mkdir(parents=True, exist_ok=True)
        return root

    def run(self, files: dict[str, str], *, timeout: float = 45) -> dict:
        try:
            checked = _validated_files(files)
            if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 1 <= timeout <= 120:
                raise ValueError("Testtiden måste vara mellan 1 och 120 sekunder.")
            root = self._work_directory()
        except (ValueError, OSError, UnicodeError) as exc:
            return self._result(str(exc))
        availability = self.check()
        if not availability["configured"]:
            return self._result(availability["detail"])
        docker = shutil.which("docker")
        if not docker:
            return self._result("Docker saknas. Starta om Docker Desktop och försök igen.")
        name = "bountyos-test-" + uuid.uuid4().hex
        nonce = uuid.uuid4().hex
        result = self._result("Testkörningen kunde inte slutföras.")
        try:
            with tempfile.TemporaryDirectory(prefix="run-", dir=root) as directory:
                self._stage(Path(directory), checked)
                argv = [docker, "run", "--name", name, "--pull=never", "--network=none", "--read-only", "--log-driver=none",
                        "--cap-drop=ALL", "--security-opt=no-new-privileges", "--user=65534:65534",
                        "--memory=256m", "--memory-swap=256m", "--pids-limit=64", "--cpus=1",
                        "--ulimit=nofile=128:128", "--ulimit=core=0", "--tmpfs=/tmp:rw,noexec,nosuid,nodev,size=32m,mode=1777",
                        "--mount", f"type=bind,source={directory},target=/work,readonly", "--workdir=/work",
                        availability["image_id"], "python", "-I", "-B", "-u", "-c", _HARNESS, nonce]
                try:
                    execution = _bounded_process(argv, timeout)
                    output = self._test_output(execution, nonce)
                    if execution.timed_out:
                        result = self._result("Testerna överskred tidsgränsen och containern stoppades.",
                                              output=output, status="timeout", timed_out=True)
                    else:
                        result = self._interpret(docker, name, nonce, execution, output)
                finally:
                    # A timed-out Docker CLI does not stop its container. Removal
                    # is explicit on every exit, including parse errors/exceptions.
                    cleanup = _bounded_process([docker, "rm", "--force", name], 10, 4096)
                    if cleanup.timed_out or cleanup.exit_code != 0:
                        result = result | {"passed": False, "status": "error",
                                           "detail": "Docker-containern kunde inte bekräftas borttagen. Kontrollera Docker Desktop.",
                                           "container_name": name}
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            return self._result("Testmiljön kunde inte köras: " + str(exc)[:300], status="error")
        return result

    @staticmethod
    def _test_output(execution: _CommandResult, nonce: str) -> str:
        prefix = (_REPORT_PREFIX + nonce + ":").encode()
        filtered = b"\n".join(line for line in execution.output.splitlines() if not line.startswith(prefix))
        output = _display_output(filtered)
        if execution.truncated:
            output += "\n[Utdata begränsades till 64 KiB.]"
        return output

    def _interpret(self, docker: str, name: str, nonce: str, execution: _CommandResult, output: str) -> dict:
        state_result = _bounded_process([docker, "container", "inspect", name, "--format", "{{json .State}}"], 8, 8192)
        try:
            state = json.loads(state_result.output)
            if (state_result.timed_out or state_result.exit_code != 0 or not isinstance(state, dict) or
                    state.get("Running") is not False or type(state.get("ExitCode")) is not int or
                    state.get("ExitCode") != execution.exit_code or state.get("OOMKilled") is not False):
                raise ValueError("Docker-processens slutstatus kunde inte verifieras.")
            prefix = (_REPORT_PREFIX + nonce + ":").encode()
            # The trusted runner writes the report last, so retain a bounded tail
            # even when earlier test output exceeds the display limit.
            lines = execution.tail.splitlines()
            reports = [line[len(prefix):] for line in lines if line.startswith(prefix)]
            if len(reports) != 1 or not lines or not lines[-1].startswith(prefix):
                raise ValueError("En fullständig testrapport saknas.")
            report = json.loads(reports[0])
            if not isinstance(report, dict) or set(report) != set(_COUNTERS):
                raise ValueError("Testrapportens format är ogiltigt.")
            if any(type(report[key]) is not int or not 0 <= report[key] <= 100000 for key in _COUNTERS):
                raise ValueError("Testrapportens räknare är ogiltiga.")
            excluded = report["skipped"] + report["expected_failures"]
            if excluded > report["tests_run"]:
                raise ValueError("Testrapportens räknare stämmer inte överens.")
            passed = (execution.exit_code == 0 and report["tests_run"] > excluded and
                      report["failures"] == report["errors"] == report["unexpected_successes"] == 0)
            detail = "Python-unittest godkändes." if passed else "Python-unittest underkändes."
            if report["tests_run"] == 0:
                detail = "Inga tester kördes; leveransen är inte godkänd."
            elif report["tests_run"] == excluded:
                detail = "Inga vanliga tester kördes utan att hoppas över eller förväntas misslyckas."
            return self._result(detail, passed=passed, exit_code=execution.exit_code, output=output,
                                status="passed" if passed else "failed", **report)
        except (ValueError, TypeError, UnicodeError) as exc:
            return self._result(str(exc), exit_code=execution.exit_code, output=output, status="error")
