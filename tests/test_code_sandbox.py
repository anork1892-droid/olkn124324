"""Container boundary tests; repository/model Python is never run on the host."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from bountyos.code_sandbox import DockerSandbox, IMAGE, _CommandResult, _bounded_process


IMAGE_ID = 'sha256:' + 'a' * 64
COUNTS = {'tests_run': 2, 'failures': 0, 'errors': 0, 'skipped': 0,
          'expected_failures': 0, 'unexpected_successes': 0}
FILES = {'maths.py': 'def add(a, b): return a + b\n',
         'tests/test_maths.py': 'import unittest\n'}


def command_result(text='', code=0, timed_out=False):
    raw = text.encode() if isinstance(text, str) else text
    return _CommandResult(code, raw, raw[-8192:], timed_out)


class SandboxBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.sandbox = DockerSandbox(Path(self.temp.name) / 'work')
        self.which = patch('bountyos.code_sandbox.shutil.which', return_value='/trusted/docker')
        self.which.start()
        self.addCleanup(self.which.stop)
        self.calls = []
        self.counts = dict(COUNTS)
        self.code = 0
        self.output = 'Ordinary test output\n'
        self.report = True
        self.duplicate = False
        self.timeout = False
        self.inspect_override = None
        self.cleanup_code = 0
        self.staged = None

    def fake_process(self, argv, timeout, limit=65536):
        self.calls.append(argv)
        if argv[1] == 'info':
            return command_result('linux\n')
        if argv[1:3] == ['image', 'inspect']:
            return command_result(IMAGE_ID + '\n')
        if argv[1] == 'run':
            self.assertFalse(any('GH_TOKEN' in part or 'GITHUB_TOKEN' in part for part in argv))
            mount = argv[argv.index('--mount') + 1]
            stage = Path(mount.removeprefix('type=bind,source=').split(',target=')[0])
            self.staged = stage
            self.assertEqual({path.relative_to(stage).as_posix(): path.read_text()
                              for path in stage.rglob('*') if path.is_file()}, FILES)
            output = self.output
            if self.report:
                line = 'BOUNTYOS_UNITTEST_REPORT:' + argv[-1] + ':' + json.dumps(self.counts) + '\n'
                output += line * (2 if self.duplicate else 1)
            return command_result(output, self.code, self.timeout)
        if argv[1:3] == ['container', 'inspect']:
            state = {'Running': False, 'ExitCode': self.code, 'OOMKilled': False}
            state.update(self.inspect_override or {})
            return command_result(json.dumps(state))
        if argv[1:3] == ['rm', '--force']:
            return command_result('removed', self.cleanup_code)
        self.fail('Unexpected Docker command: ' + repr(argv))

    def run_fixture(self, **kwargs):
        with patch('bountyos.code_sandbox._bounded_process', side_effect=self.fake_process):
            return self.sandbox.run(FILES, **kwargs)

    def test_success_requires_complete_report_and_restrictive_command(self):
        result = self.run_fixture()
        self.assertTrue(result['passed'])
        self.assertEqual(result['tests_run'], 2)
        self.assertEqual(result['test_count'], 2)
        self.assertEqual(result['failures'], result['failure_count'])
        self.assertNotIn('BOUNTYOS_UNITTEST_REPORT:', result['output'])
        command = next(call for call in self.calls if call[1] == 'run')
        for flag in ('--pull=never', '--network=none', '--read-only', '--log-driver=none', '--cap-drop=ALL',
                     '--security-opt=no-new-privileges', '--user=65534:65534', '--memory=256m',
                     '--memory-swap=256m', '--pids-limit=64', '--cpus=1', '--workdir=/work'):
            self.assertIn(flag, command)
        self.assertIn('--tmpfs=/tmp:rw,noexec,nosuid,nodev,size=32m,mode=1777', command)
        self.assertIn(IMAGE_ID, command)
        self.assertNotIn(IMAGE, command)  # Pin the already inspected local image ID.
        self.assertIn('python', command)
        self.assertIn('-I', command)
        self.assertIn('-B', command)
        self.assertNotIn('sh', command)
        self.assertNotIn('/var/run/docker.sock', ' '.join(command))
        self.assertFalse(self.staged.exists())
        self.assertEqual(self.calls[-1][1:3], ['rm', '--force'])

    def test_missing_docker_explains_requirement_without_running_commands(self):
        with patch('bountyos.code_sandbox.shutil.which', return_value=None), patch('bountyos.code_sandbox._bounded_process') as process:
            availability = self.sandbox.check()
        self.assertFalse(availability['configured'])
        self.assertIn('Docker Desktop', availability['detail'])
        process.assert_not_called()

    def test_image_absence_does_not_pull_automatically(self):
        responses = [command_result('linux'), command_result('No such image', 1)]
        with patch('bountyos.code_sandbox._bounded_process', side_effect=responses) as process:
            availability = self.sandbox.check()
        self.assertFalse(availability['configured'])
        self.assertIn('docker pull python:3.12-slim', availability['detail'])
        self.assertEqual([call.args[0][1] for call in process.call_args_list], ['info', 'image'])

    def test_windows_engine_is_not_used(self):
        with patch('bountyos.code_sandbox._bounded_process', return_value=command_result('windows')) as process:
            availability = self.sandbox.check()
        self.assertFalse(availability['configured'])
        self.assertIn('Linux', availability['detail'])
        self.assertEqual(process.call_count, 1)

    def test_unsafe_paths_rejected_before_docker_or_file_staging(self):
        for name in ('../escape.py', '/tmp/escape.py', 'C:/escape.py', 'tests\\test_evil.py',
                     'tests/.hidden.py', 'tests/a:stream.py', 'tests/CON.py', 'tests/NUL.txt',
                     'tests/test_bad.py.', 'tests/a\n.py', 'tests//test.py', './tests/test.py',
                     'tests/test_💥.py', 'tests/test.sh', 'tests/test.py/child.py'):
            with self.subTest(name=name), patch.object(self.sandbox, 'check') as check:
                invalid = FILES | {name: ''}
                if name == 'tests/test.py/child.py':
                    invalid['tests/test.py'] = ''
                result = self.sandbox.run(invalid)
                self.assertFalse(result['passed'])
                self.assertEqual(result['status'], 'blocked')
                check.assert_not_called()
        self.assertFalse(self.sandbox.work_root.exists())

    def test_windows_case_collisions_and_non_text_files_rejected(self):
        for files in (FILES | {'MATHS.py': ''}, FILES | {'binary.py': b'\0'}, [], {},
                      {f'tests/test_{index}.py': '' for index in range(61)},
                      FILES | {'large.py': 'x' * (500 * 1024)}):
            with self.subTest(files_type=type(files)), patch.object(self.sandbox, 'check') as check:
                self.assertFalse(self.sandbox.run(files)['passed'])
                check.assert_not_called()

    def test_symlink_work_root_is_rejected(self):
        actual = Path(self.temp.name) / 'actual'
        actual.mkdir()
        link = Path(self.temp.name) / 'linked'
        try:
            link.symlink_to(actual, target_is_directory=True)
        except OSError:
            self.skipTest('Host cannot create directory symlinks')
        with patch.object(self.sandbox, 'check') as check:
            self.sandbox.work_root = link / 'work'
            result = self.sandbox.run(FILES)
        self.assertFalse(result['passed'])
        self.assertIn('symboliska', result['detail'])
        check.assert_not_called()
        self.assertEqual(list(actual.iterdir()), [])

    def test_printing_success_without_runner_report_does_not_pass(self):
        self.report = False
        self.output = 'PASSED\n{"passed":true,"tests_run":99}\nBOUNTYOS_UNITTEST_REPORT:fake:' + json.dumps(COUNTS) + '\n'
        result = self.run_fixture()
        self.assertFalse(result['passed'])
        self.assertEqual(result['status'], 'error')
        self.assertEqual(result['tests_run'], 0)

    def test_failing_process_cannot_pass_with_good_counts(self):
        self.code = 1
        result = self.run_fixture()
        self.assertFalse(result['passed'])
        self.assertEqual(result['exit_code'], 1)

    def test_failure_and_error_counts_preserved_for_regression_checks(self):
        self.code = 1
        self.counts.update(failures=1, errors=1)
        result = self.run_fixture()
        self.assertFalse(result['passed'])
        self.assertEqual(result['failures'], 1)
        self.assertEqual(result['errors'], 1)
        self.assertEqual(result['failure_count'], 1)
        self.assertEqual(result['error_count'], 1)

    def test_zero_skip_only_and_expected_failure_only_do_not_pass(self):
        for counts in ({'tests_run': 0}, {'tests_run': 2, 'skipped': 2},
                       {'tests_run': 2, 'expected_failures': 2}):
            with self.subTest(counts=counts):
                self.counts = COUNTS | counts
                self.assertFalse(self.run_fixture()['passed'])

    def test_corrupt_counters_or_duplicate_reports_do_not_pass(self):
        for counts in ({'tests_run': True}, {'tests_run': -1}, {'tests_run': '2'},
                       {'skipped': 3}, {'extra': 1}):
            with self.subTest(counts=counts):
                self.counts = COUNTS | counts
                self.assertEqual(self.run_fixture()['status'], 'error')
        self.counts = COUNTS
        self.duplicate = True
        self.assertFalse(self.run_fixture()['passed'])

    def test_container_state_must_match_process_and_not_be_oom_killed(self):
        for state in ({'Running': True}, {'ExitCode': 1}, {'OOMKilled': True}, {'ExitCode': False}):
            with self.subTest(state=state):
                self.inspect_override = state
                self.assertFalse(self.run_fixture()['passed'])

    def test_timeout_forces_container_removal_without_trusting_report(self):
        self.timeout = True
        result = self.run_fixture(timeout=1)
        self.assertFalse(result['passed'])
        self.assertTrue(result['timed_out'])
        self.assertEqual(result['status'], 'timeout')
        self.assertEqual(self.calls[-1][1:3], ['rm', '--force'])
        self.assertFalse(any(call[1:3] == ['container', 'inspect'] for call in self.calls))
        self.assertFalse(self.staged.exists())

    def test_unconfirmed_cleanup_prevents_success(self):
        self.cleanup_code = 1
        result = self.run_fixture()
        self.assertFalse(result['passed'])
        self.assertEqual(result['status'], 'error')
        self.assertTrue(result['container_name'].startswith('bountyos-test-'))

    def test_subprocess_output_capture_is_bounded_while_pipe_is_drained(self):
        # This is a fixed test of our I/O helper, not repository/model code.
        result = _bounded_process([sys.executable, '-c', "import sys; sys.stdout.write('x' * 200000 + 'TAIL')"], 5, 1024)
        self.assertEqual(result.exit_code, 0)
        self.assertTrue(result.truncated)
        self.assertEqual(len(result.output), 1024)
        self.assertLessEqual(len(result.tail), 8192)
        self.assertTrue(result.tail.endswith(b'TAIL'))


if __name__ == '__main__':
    unittest.main()
