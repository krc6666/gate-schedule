"""The cloud launcher must open the cloud entry and own only its own server."""
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase, mock
import importlib.util
import socket
import subprocess

ROOT = Path(__file__).resolve().parents[1]


class DevLauncherTests(TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location('gate_dev', ROOT / 'dev.py')
        self.dev = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.dev)

    def test_default_is_cloud_development_with_current_browser_python(self):
        process = mock.Mock()
        process.wait.return_value = 0
        with mock.patch.object(self.dev, 'ensure_dependencies') as deps, \
             mock.patch.object(self.dev, 'ensure_cloud') as cloud, \
             mock.patch.object(self.dev, 'prepare_runtime') as prepare, \
             mock.patch.object(self.dev, 'find_npm', return_value='npm.cmd'), \
             mock.patch.object(self.dev, 'available_port', return_value=18788), \
             mock.patch.object(self.dev, 'wait_until_ready') as ready, \
             mock.patch.object(self.dev, 'open_browser') as browser, \
             mock.patch.object(self.dev, 'stop_process') as stop, \
             mock.patch.object(self.dev.subprocess, 'Popen', return_value=process) as spawn:
            self.assertEqual(0, self.dev.main([]))
        deps.assert_called_once()
        cloud.assert_called_once()
        prepare.assert_called_once()
        command = spawn.call_args.args[0]
        self.assertIn('dev:cloud', command)
        self.assertIn('--strictPort', command)
        self.assertIn('18788', command)
        ready.assert_called_once_with(process, 'http://127.0.0.1:18788/')
        browser.assert_called_once_with('http://127.0.0.1:18788/')
        self.assertEqual(str(ROOT / '.tmp'), spawn.call_args.kwargs['env']['TEMP'])
        self.assertEqual(ROOT / 'web', spawn.call_args.kwargs['cwd'])
        stop.assert_called_once_with(process)

    def test_preview_uses_cloud_build_and_does_not_open_when_disabled(self):
        process = mock.Mock()
        process.wait.return_value = 0
        with mock.patch.object(self.dev, 'ensure_dependencies'), \
             mock.patch.object(self.dev, 'ensure_cloud'), \
             mock.patch.object(self.dev, 'prepare_runtime'), \
             mock.patch.object(self.dev, 'find_npm', return_value='npm.cmd'), \
             mock.patch.object(self.dev, 'available_port', return_value=18789), \
             mock.patch.object(self.dev, 'wait_until_ready') as ready, \
             mock.patch.object(self.dev, 'open_browser') as browser, \
             mock.patch.object(self.dev, 'stop_process'), \
             mock.patch.object(self.dev.subprocess, 'run', return_value=mock.Mock(returncode=0)) as run, \
             mock.patch.object(self.dev.subprocess, 'Popen', return_value=process) as spawn:
            self.assertEqual(0, self.dev.main(['--preview', '--no-browser']))
        self.assertEqual(['npm.cmd', 'run', 'build:cloud'], run.call_args.args[0])
        self.assertIn('preview:cloud', spawn.call_args.args[0])
        ready.assert_called_once_with(process, 'http://127.0.0.1:18789/')
        browser.assert_not_called()

    def test_failed_build_does_not_launch_or_claim_ready(self):
        with mock.patch.object(self.dev, 'ensure_dependencies'), \
             mock.patch.object(self.dev, 'ensure_cloud'), \
             mock.patch.object(self.dev, 'find_npm', return_value='npm.cmd'), \
             mock.patch.object(self.dev, 'available_port', return_value=18789), \
             mock.patch.object(self.dev.subprocess, 'run', return_value=mock.Mock(returncode=2)), \
             mock.patch.object(self.dev.subprocess, 'Popen') as spawn, \
             mock.patch.object(self.dev, 'open_browser') as browser:
            self.assertEqual(2, self.dev.main(['--preview']))
        spawn.assert_not_called()
        browser.assert_not_called()

    def test_occupied_port_refused_without_stopping_existing_listener(self):
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            listener.listen()
            port = listener.getsockname()[1]
            with self.assertRaisesRegex(RuntimeError, '端口'):
                self.dev.available_port(port)
            with socket.create_connection(('127.0.0.1', port), timeout=1):
                pass

    def test_missing_dependency_bootstraps_from_existing_lock_file(self):
        with TemporaryDirectory(dir=ROOT / '.tmp', prefix='dev-launch-deps-') as directory:
            with mock.patch.object(self.dev, 'WEB', Path(directory)), \
                 mock.patch.object(self.dev.subprocess, 'run', return_value=mock.Mock(returncode=0)) as run:
                self.dev.ensure_dependencies('npm.cmd', {})
            self.assertEqual(['npm.cmd', 'ci'], run.call_args.args[0])

    def test_missing_cloud_schema_runs_only_gate_upgrader_and_checks_again(self):
        with mock.patch.object(self.dev.os, 'name', 'nt'), \
             mock.patch.object(self.dev.shutil, 'which', return_value='node.exe'), \
             mock.patch.object(self.dev.subprocess, 'run', side_effect=[
                 mock.Mock(returncode=2), mock.Mock(returncode=0), mock.Mock(returncode=0)]) as run:
            self.dev.ensure_cloud({'PATH': 'test'})
        self.assertEqual(3, run.call_count)
        self.assertEqual(str(ROOT / 'scripts/upgrade-database.ps1'), run.call_args_list[1].args[0][-1])
        self.assertEqual(run.call_args_list[0].args[0], run.call_args_list[2].args[0])

    def test_failed_upgrade_stops_before_opening_cloud_page(self):
        with mock.patch.object(self.dev.os, 'name', 'nt'), \
             mock.patch.object(self.dev.shutil, 'which', return_value='node.exe'), \
             mock.patch.object(self.dev.subprocess, 'run', side_effect=[
                 mock.Mock(returncode=2), mock.Mock(returncode=1)]) as run:
            with self.assertRaisesRegex(RuntimeError, '云端升级尚未完成'):
                self.dev.ensure_cloud({'PATH': 'test'})
        self.assertEqual(2, run.call_count)
