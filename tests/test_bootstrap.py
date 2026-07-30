import importlib.util
import io
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest import mock


REPO = Path(__file__).resolve().parents[1]
SETUP = REPO / "setup-robot-env.py"
UPDATE = REPO / "update-robot-env.py"
SUPPORT = REPO / "setup_robot_env_support.py"


def command_checkout(command):
    if "-C" not in command:
        return None
    return Path(command[command.index("-C") + 1])


def load_setup():
    spec = importlib.util.spec_from_file_location("setup_robot_env", SETUP)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_update():
    spec = importlib.util.spec_from_file_location("update_robot_env", UPDATE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeRunner:
    def __init__(self):
        self.commands = []
        self.events = []
        self.exec_command = None

    def which(self, command):
        return f"/usr/bin/{command}"

    def trusted_executable(self, command):
        return Path(f"/usr/bin/{command}")

    def run(self, command, **kwargs):
        self.commands.append(command)
        self.events.append(("run", command))
        if command[1:2] == ["clone"]:
            Path(command[-1], ".git").mkdir(parents=True)
        if command[-3:] == ["remote", "get-url", "origin"]:
            repository = (
                "deploy-script"
                if command_checkout(command).name == "deploy-script"
                else "robot-deploy"
            )
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=f"https://github.com/5toe5/{repository}.git\n",
                stderr="",
            )
        if command[-3:] == ["symbolic-ref", "--short", "HEAD"]:
            return subprocess.CompletedProcess(command, 0, stdout="main\n", stderr="")
        if command[-4:] == [
            "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"
        ]:
            return subprocess.CompletedProcess(command, 0, stdout="origin/main\n", stderr="")
        if command[-2:] == ["rev-parse", "HEAD"]:
            return subprocess.CompletedProcess(command, 0, stdout="abc123\n", stderr="")
        if command[-2:] == ["rev-parse", "refs/remotes/origin/main"]:
            return subprocess.CompletedProcess(command, 0, stdout="abc123\n", stderr="")
        if "status" in command:
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        return subprocess.CompletedProcess(command, 0, stdout=b"signature", stderr=b"")

    def exec(self, command, env):
        self.exec_command = (command, env)
        self.events.append(("exec", command))


class SupportTests(unittest.TestCase):
    def test_sudo_checkout_uses_only_exact_command_scoped_safe_directory(self):
        import setup_robot_env_support as support

        class Runner:
            def __init__(self):
                self.call = None

            def trusted_executable(self, command):
                return Path("/usr/bin/git")

            def run(self, command, **kwargs):
                self.call = (command, kwargs["env"])
                return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

        with tempfile.TemporaryDirectory() as root:
            checkout = (Path(root) / "deploy-script").resolve()
            checkout.mkdir()
            sudo_env = {
                "SUDO_UID": str(os.getuid()),
                "SUDO_GID": str(os.getgid()),
            }
            context = support.checkout_git_context(
                checkout, sudo_env, effective_uid=0
            )
            runner = Runner()
            support.git_run(runner, ["status", "--short"], context=context)

            command, git_env = runner.call
            self.assertEqual(
                command,
                [
                    "/usr/bin/git",
                    "-c",
                    f"safe.directory={checkout}",
                    "status",
                    "--short",
                ],
            )
            self.assertEqual(git_env["SUDO_UID"], str(os.getuid()))
            self.assertEqual(git_env["SUDO_GID"], str(os.getgid()))
            self.assertNotIn("--global", command)

            hostile_environments = (
                {"SUDO_UID": str(os.getuid())},
                {"SUDO_UID": "not-a-number", "SUDO_GID": str(os.getgid())},
                {"SUDO_UID": "0", "SUDO_GID": "0"},
                {"SUDO_UID": str(os.getuid() + 1), "SUDO_GID": str(os.getgid())},
            )
            for hostile in hostile_environments:
                with self.subTest(hostile=hostile), self.assertRaises(
                    support.BootstrapError
                ):
                    support.checkout_git_context(
                        checkout, hostile, effective_uid=0
                    )

            link = Path(root) / "checkout-link"
            link.symlink_to(checkout, target_is_directory=True)
            with self.assertRaises(support.BootstrapError):
                support.checkout_git_context(link, sudo_env, effective_uid=0)

    def test_git_uses_trusted_executable_minimal_environment_and_private_auth_only(self):
        import setup_robot_env_support as support

        class Runner:
            def __init__(self):
                self.calls = []

            def trusted_executable(self, command):
                self.assert_command = command
                return Path("/usr/bin/git")

            def run(self, command, **kwargs):
                self.calls.append((command, kwargs.get("env", {})))
                return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

        hostile = {
            "PATH": "/hostile",
            "GIT_DIR": "/hostile/repo",
            "GIT_WORK_TREE": "/hostile/tree",
            "GIT_CONFIG": "/hostile/config",
            "GIT_CONFIG_GLOBAL": "/hostile/global",
            "GIT_CONFIG_SYSTEM": "/hostile/system",
            "GIT_EXEC_PATH": "/hostile/exec",
            "GIT_TEMPLATE_DIR": "/hostile/templates",
            "GIT_ASKPASS": "/hostile/askpass",
            "SSH_ASKPASS": "/hostile/ssh-askpass",
            "GIT_TRACE_CURL": "1",
        }
        runner = Runner()
        with mock.patch.dict(os.environ, hostile, clear=True):
            support.git_run(runner, ["status", "--short"])
            support.git_run(runner, ["pull", "--ff-only"], token="secret-token")

        public_command, public_env = runner.calls[0]
        private_command, private_env = runner.calls[1]
        self.assertEqual(public_command[0], "/usr/bin/git")
        self.assertEqual(private_command[0], "/usr/bin/git")
        for name, hostile_value in hostile.items():
            self.assertNotEqual(public_env.get(name), hostile_value)
            self.assertNotEqual(private_env.get(name), hostile_value)
        self.assertNotIn("secret-token", " ".join(private_command))
        self.assertNotIn("secret-token", repr(private_env))
        self.assertFalse(any("Authorization" in value for value in public_env.values()))
        self.assertTrue(any("Authorization" in value for value in private_env.values()))
        self.assertEqual(private_env["GIT_CONFIG_NOSYSTEM"], "1")

    def test_ff_refresh_requires_exact_origin_main_upstream_and_remote_tip(self):
        import setup_robot_env_support as support

        class GitRunner:
            def __init__(self, overrides=None):
                self.overrides = overrides or {}
                self.commands = []

            def run(self, command, **kwargs):
                self.commands.append(command)
                key = tuple(command[3:])
                defaults = {
                    ("remote", "get-url", "origin"): "https://github.com/5toe5/deploy-script.git\n",
                    ("symbolic-ref", "--short", "HEAD"): "main\n",
                    ("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"): "origin/main\n",
                    ("status", "--short"): "",
                    ("rev-parse", "HEAD"): "abc123\n",
                    ("rev-parse", "refs/remotes/origin/main"): "abc123\n",
                }
                value = self.overrides.get(key, defaults.get(key, ""))
                code = 1 if value is None else 0
                return subprocess.CompletedProcess(command, code, stdout=value or "", stderr="")

            def trusted_executable(self, command):
                return Path(f"/usr/bin/{command}")

        repo = Path("/sandbox/deploy-script")
        runner = GitRunner()
        support.refresh_main_checkout(repo, "5toe5/deploy-script", runner)
        self.assertIn(["/usr/bin/git", "-C", str(repo), "pull", "--ff-only"], runner.commands)

        failures = (
            {("remote", "get-url", "origin"): "https://github.com/example/other.git\n"},
            {("symbolic-ref", "--short", "HEAD"): "feature\n"},
            {("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"): "origin/develop\n"},
            {("rev-parse", "HEAD"): "local-ahead\n"},
            {("pull", "--ff-only"): None},
        )
        for overrides in failures:
            with self.subTest(overrides=overrides), self.assertRaises(support.BootstrapError):
                support.refresh_main_checkout(
                    repo, "5toe5/deploy-script", GitRunner(overrides)
                )

    def test_app_token_is_python_310_compatible_and_downscoped_to_contents_read(self):
        import setup_robot_env_support as support

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

            def read(self):
                return b'{"token":"short-token"}'

        runner = FakeRunner()
        with mock.patch.object(support.urllib.request, "urlopen", return_value=Response()) as urlopen:
            token = support.installation_token("123", "456", b"private key", runner)

        self.assertEqual(token, "short-token")
        self.assertEqual(
            urlopen.call_args.args[0].data,
            b'{"repositories":["robot-deploy"],"permissions":{"contents":"read"}}',
        )
        self.assertNotIn("datetime import UTC", SUPPORT.read_text())
        git_env = support.git_environment("short-token")
        self.assertNotIn("GIT_TRACE_CURL", git_env)


class BootstrapCLITests(unittest.TestCase):
    def test_explicit_semver_allows_prerelease_and_build_metadata_but_rejects_near_misses(self):
        setup = load_setup()
        valid = (
            "v1.2.3-alpha.1",
            "v1.2.3+build.5",
            "v1.2.3-rc.1+build.5",
        )
        invalid = (
            "1.2.3",
            "v1.2",
            "v1.2.3-",
            "v1.2.3+",
            "v1.2.3-alpha..1",
            "v1.02.3",
        )
        with tempfile.TemporaryDirectory() as root:
            pem = Path(root) / "app.pem"
            pem.write_text("key\n")
            base = [
                "--non-interactive",
                "--github-app-id", "123",
                "--github-installation-id", "456",
                "--pem-file", str(pem),
                "--motion-agent-agent-host", "192.0.2.10",
            ]
            for version in valid:
                with self.subTest(valid=version):
                    args = setup.parse_args([*base, "--version", version])
                    setup.collect_config(args, {}, lambda _: "")
            for version in invalid:
                with self.subTest(invalid=version), self.assertRaises(setup.BootstrapError):
                    args = setup.parse_args([*base, "--version", version])
                    setup.collect_config(args, {}, lambda _: "")

    def test_credentials_authenticate_before_nofollow_atomic_pair_replacement(self):
        setup = load_setup()
        runner = FakeRunner()
        with tempfile.TemporaryDirectory() as root:
            root_path = Path(root)
            credentials = root_path / "etc/granforge"
            credentials.mkdir(parents=True)
            env_file = credentials / "deploy.env"
            pem_file = credentials / "github-app.pem"
            old_env = "GITHUB_APP_ID=1\nGITHUB_INSTALLATION_ID=2\nMOTION_AGENT_AGENT_HOST=192.0.2.1\n"
            env_file.write_text(old_env)
            pem_file.write_text("OLD-KEY\n")
            env_file.chmod(0o600)
            pem_file.chmod(0o600)
            source = root_path / "new.pem"
            source.write_text("NEW-KEY\n")
            argv = [
                "--non-interactive",
                "--github-app-id", "123",
                "--github-installation-id", "456",
                "--pem-file", str(source),
                "--motion-agent-agent-host", "192.0.2.10",
                "--version", "v1.2.3",
            ]

            failed = setup.main(
                argv,
                runner=runner,
                token_provider=lambda *_: (_ for _ in ()).throw(
                    setup.BootstrapError("authentication failed")
                ),
                sandbox_root=root,
            )
            self.assertEqual(failed, 1)
            self.assertEqual(env_file.read_text(), old_env)
            self.assertEqual(pem_file.read_text(), "OLD-KEY\n")

            def authenticate(app_id, installation_id, supplied_pem, _runner):
                self.assertEqual(supplied_pem, b"NEW-KEY\n")
                self.assertEqual(env_file.read_text(), old_env)
                self.assertEqual(pem_file.read_text(), "OLD-KEY\n")
                return "token"

            succeeded = setup.main(
                argv,
                runner=runner,
                token_provider=authenticate,
                sandbox_root=root,
            )
            self.assertEqual(succeeded, 0)
            self.assertIn("GITHUB_APP_ID=123", env_file.read_text())
            self.assertEqual(pem_file.read_text(), "NEW-KEY\n")

            target = root_path / "symlink-target"
            target.write_text("untouched")
            env_file.unlink()
            env_file.symlink_to(target)
            with self.assertRaises(setup.BootstrapError):
                setup.install_credentials(env_file, pem_file, "NEW-ENV\n", "NEWER-KEY\n")
            self.assertEqual(target.read_text(), "untouched")

        with tempfile.TemporaryDirectory() as root:
            root_path = Path(root)
            env_file = root_path / "deploy.env"
            pem_file = root_path / "github-app.pem"
            env_file.write_text("OLD-ENV\n")
            pem_file.write_text("OLD-PEM\n")
            env_file.chmod(0o600)
            pem_file.chmod(0o600)
            replacements = 0

            def fail_second_replace(source, destination):
                nonlocal replacements
                replacements += 1
                if replacements == 2:
                    raise OSError("simulated partial failure")
                os.replace(source, destination)

            with self.assertRaises(setup.BootstrapError):
                setup.install_credentials(
                    env_file,
                    pem_file,
                    "NEW-ENV\n",
                    "NEW-PEM\n",
                    replace_fn=fail_second_replace,
                )
            self.assertEqual(env_file.read_text(), "OLD-ENV\n")
            self.assertEqual(pem_file.read_text(), "OLD-PEM\n")

    def test_root_override_is_not_a_production_cli_option(self):
        setup = load_setup()
        with self.assertRaises(SystemExit):
            setup.parse_args(["--root", "/tmp/sandbox"])

    def test_existing_private_repo_is_verified_clean_sanitized_and_ff_only_refreshed(self):
        setup = load_setup()

        class CredentialedRemoteRunner(FakeRunner):
            def run(self, command, **kwargs):
                result = super().run(command, **kwargs)
                if command[-3:] == ["remote", "get-url", "origin"]:
                    return subprocess.CompletedProcess(
                        command, 0,
                        stdout="https://x-access-token:stale@gitHub.com/5toe5/robot-deploy.git\n",
                        stderr="",
                    )
                return result

        with tempfile.TemporaryDirectory() as root:
            repo = Path(root) / "robot-deploy"
            (repo / ".git").mkdir(parents=True)
            runner = CredentialedRemoteRunner()

            setup.refresh_private_repo(repo, "short-token", runner)

        self.assertFalse(any("clone" in command for command in runner.commands))
        self.assertIn(["/usr/bin/git", "-C", str(repo), "pull", "--ff-only"], runner.commands)
        set_url = [
            "/usr/bin/git", "-C", str(repo), "remote", "set-url", "origin",
            "https://github.com/5toe5/robot-deploy.git",
        ]
        self.assertIn(set_url, runner.commands)
        self.assertNotIn("short-token", " ".join(sum(runner.commands, [])))

        class BadRepoRunner(FakeRunner):
            def __init__(self, failure):
                super().__init__()
                self.failure = failure

            def run(self, command, **kwargs):
                result = super().run(command, **kwargs)
                if self.failure == "unrelated" and command[-3:] == ["remote", "get-url", "origin"]:
                    return subprocess.CompletedProcess(command, 0, stdout="https://github.com/example/other.git\n", stderr="")
                if self.failure == "dirty" and "status" in command:
                    return subprocess.CompletedProcess(command, 0, stdout=" M deploy.py\n", stderr="")
                if self.failure == "non-ff" and command[-2:] == ["pull", "--ff-only"]:
                    return subprocess.CompletedProcess(command, 1, stdout="", stderr="non-fast-forward")
                return result

        for failure in ("unrelated", "dirty", "non-ff"):
            runner = BadRepoRunner(failure)
            with self.subTest(failure=failure), self.assertRaises(setup.BootstrapError):
                setup.refresh_private_repo(repo, "short-token", runner)

    def test_persisted_values_and_resolved_loopback_hosts_are_strictly_validated(self):
        setup = load_setup()
        with tempfile.TemporaryDirectory() as root:
            pem = Path(root) / "app.pem"
            pem.write_text("key\n")
            base = [
                "--non-interactive",
                "--github-app-id", "123",
                "--github-installation-id", "456",
                "--pem-file", str(pem),
                "--motion-agent-agent-host", "192.0.2.10",
                "--version", "v1.2.3",
            ]
            for option, invalid in (
                ("--github-app-id", "12x"),
                ("--github-installation-id", "45\n6"),
                ("--motion-agent-agent-host", "robot\nINJECTED=value"),
                ("--motion-agent-agent-host", "robot.example"),
            ):
                argv = list(base)
                argv[argv.index(option) + 1] = invalid
                with self.subTest(option=option), self.assertRaises(setup.BootstrapError):
                    setup.collect_config(setup.parse_args(argv), {}, lambda _: "")

        mapped_v4 = mock.Mock(
            is_loopback=True, is_unspecified=False, is_multicast=False
        )
        mapped_v6 = mock.Mock(is_loopback=False, ipv4_mapped=mapped_v4)
        with mock.patch.object(setup.ipaddress, "ip_address", return_value=mapped_v6):
            with self.assertRaises(setup.BootstrapError):
                setup.validate_agent_host("::ffff:127.0.0.1", simulator_only=False)
            setup.validate_agent_host("::ffff:127.0.0.1", simulator_only=True)
        for unusable in (
            "0.0.0.0",
            "::",
            "224.0.0.1",
            "ff02::1",
            "::ffff:0.0.0.0",
            "::ffff:224.0.0.1",
        ):
            with self.subTest(unusable=unusable), self.assertRaises(setup.BootstrapError):
                setup.validate_agent_host(unusable, simulator_only=True)
        self.assertEqual(
            setup.serialize_env({
                "GITHUB_APP_ID": "123",
                "MOTION_AGENT_AGENT_HOST": "192.0.2.10",
            }),
            "GITHUB_APP_ID=123\nMOTION_AGENT_AGENT_HOST=192.0.2.10\n",
        )

    def test_noninteractive_requires_configuration_and_version(self):
        with tempfile.TemporaryDirectory() as root:
            result = subprocess.run(
                [
                    sys.executable,
                    str(SETUP),
                    "--non-interactive",
                ],
                capture_output=True,
                text=True,
            )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("GITHUB_APP_ID is required", result.stderr)
        self.assertIn("--version is required", result.stderr)

    def test_loopback_agent_host_is_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            pem = Path(root) / "app.pem"
            pem.write_text("private key")
            result = subprocess.run(
                [
                    sys.executable,
                    str(SETUP),
                    "--non-interactive",
                    "--github-app-id",
                    "123",
                    "--github-installation-id",
                    "456",
                    "--pem-file",
                    str(pem),
                    "--motion-agent-agent-host",
                    "127.0.0.1",
                    "--version",
                    "v1.2.3",
                ],
                capture_output=True,
                text=True,
            )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("loopback", result.stderr)
        self.assertIn("--simulator-only", result.stderr)

    def test_noninteractive_bootstrap_separates_credentials_and_deploys_version(self):
        setup = load_setup()
        runner = FakeRunner()
        with tempfile.TemporaryDirectory() as root:
            root_path = Path(root)
            (root_path / "etc").mkdir()
            pem = root_path / "source.pem"
            pem.write_text("SECRET-PRIVATE-KEY\n")
            bundle = root_path / "granforge-linux-amd64.tar.gz"

            setup.main(
                [
                    "--non-interactive",
                    "--github-app-id",
                    "123",
                    "--github-installation-id",
                    "456",
                    "--pem-file",
                    str(pem),
                    "--motion-agent-agent-host",
                    "192.0.2.10",
                    "--version",
                    "v1.2.3",
                    "--bundle",
                    str(bundle),
                ],
                runner=runner,
                token_provider=lambda *_: "short-lived-token",
                environ={
                    "GRANFORGE_ROOT": "/hostile",
                    "GRANFORGE_ARCH": "hostile",
                    "GRANFORGE_SYSTEMCTL": "/hostile/systemctl",
                    "GRANFORGE_HEALTHCHECK": "/hostile/health",
                    "GRANFORGE_TEST_FAIL_POINT": "hostile",
                    "GRANFORGE_BOOTSTRAP_REEXEC": "1",
                },
                sandbox_root=root,
            )

            env_file = root_path / "etc/granforge/deploy.env"
            installed_pem = root_path / "etc/granforge/github-app.pem"
            env_text = env_file.read_text()
            self.assertIn("GITHUB_APP_ID=123", env_text)
            self.assertIn("MOTION_AGENT_AGENT_HOST=192.0.2.10", env_text)
            self.assertNotIn("PRIVATE", env_text)
            self.assertEqual(installed_pem.read_text(), "SECRET-PRIVATE-KEY\n")
            self.assertEqual(env_file.stat().st_mode & 0o777, 0o600)
            self.assertEqual(installed_pem.stat().st_mode & 0o777, 0o600)
            self.assertEqual(env_file.parent.stat().st_mode & 0o777, 0o700)

        self.assertIn(
            [
                "/usr/bin/git",
                "-C",
                os.path.join(root, "opt/granforge/robot-deploy"),
                "remote",
                "set-url",
                "origin",
                "https://github.com/5toe5/robot-deploy.git",
            ],
            runner.commands,
        )
        clone = next(command for command in runner.commands if "clone" in command)
        self.assertEqual(clone[-2], "https://github.com/5toe5/robot-deploy.git")
        self.assertNotIn("short-lived-token", " ".join(clone))
        command, deploy_env = runner.exec_command
        self.assertEqual(
            command[-4:], ["--version", "v1.2.3", "--bundle", str(bundle)]
        )
        self.assertNotIn("GITHUB_APP_ID", deploy_env)
        self.assertNotIn("GITHUB_INSTALLATION_ID", deploy_env)
        self.assertEqual(deploy_env["GRANFORGE_ROOT"], root)
        for hostile in (
            "GRANFORGE_ARCH",
            "GRANFORGE_SYSTEMCTL",
            "GRANFORGE_HEALTHCHECK",
            "GRANFORGE_TEST_FAIL_POINT",
            "GRANFORGE_BOOTSTRAP_REEXEC",
        ):
            self.assertNotIn(hostile, deploy_env)

    def test_simulator_only_override_allows_loopback(self):
        setup = load_setup()
        with tempfile.TemporaryDirectory() as root:
            pem = Path(root) / "app.pem"
            pem.write_text("key\n")
            args = setup.parse_args([
                "--non-interactive",
                "--github-app-id", "123",
                "--github-installation-id", "456",
                "--pem-file", str(pem),
                "--motion-agent-agent-host", "127.0.0.1",
                "--simulator-only",
                "--version", "v1.2.3",
            ])

            config = setup.collect_config(args, {}, lambda _: "")

        self.assertEqual(config[-1], "127.0.0.1")

    def test_package_installation_shows_command_and_honors_decision(self):
        setup = load_setup()
        self.assertEqual(setup.PACKAGES, ("git", "openssl", "systemd"))

        class MissingToolsRunner(FakeRunner):
            def which(self, command):
                return None if command == "openssl" else f"/usr/bin/{command}"

        with tempfile.TemporaryDirectory() as root:
            root_path = Path(root)
            (root_path / "etc").mkdir()
            (root_path / "etc/os-release").write_text("ID=ubuntu\n")
            declined = MissingToolsRunner()
            stderr = io.StringIO()
            with redirect_stderr(stderr), self.assertRaises(setup.BootstrapError):
                setup.ensure_prerequisites(
                    root_path, False, lambda _: "no", declined
                )
            self.assertIn("apt-get update && apt-get install -y openssl", stderr.getvalue())
            self.assertEqual(declined.commands, [])

            approved = MissingToolsRunner()
            setup.ensure_prerequisites(root_path, False, lambda _: "yes", approved)
            self.assertEqual(
                approved.commands,
                [["apt-get", "update"], ["apt-get", "install", "-y", "openssl"]],
            )

            unattended = MissingToolsRunner()
            with self.assertRaises(setup.BootstrapError):
                setup.ensure_prerequisites(
                    root_path, True, lambda _: self.fail("must not prompt"), unattended
                )
            self.assertEqual(unattended.commands, [])


class UpdateTests(unittest.TestCase):
    def test_updater_authenticates_pinned_pem_when_path_is_replaced_or_swapped_to_symlink(self):
        update = load_update()
        for swap_kind in ("regular", "symlink"):
            with self.subTest(swap_kind=swap_kind), tempfile.TemporaryDirectory() as root:
                root_path = Path(root)
                public = root_path / "src/deploy-script"
                private = root_path / "opt/granforge/robot-deploy"
                (public / ".git").mkdir(parents=True)
                (private / ".git").mkdir(parents=True)
                config = root_path / "etc/granforge"
                config.mkdir(parents=True)
                env_file = config / "deploy.env"
                pem_file = config / "github-app.pem"
                env_file.write_text("GITHUB_APP_ID=123\nGITHUB_INSTALLATION_ID=456\n")
                pem_file.write_text("ORIGINAL-KEY\n")
                env_file.chmod(0o600)
                pem_file.chmod(0o600)
                replacement = root_path / "replacement.pem"
                replacement.write_text("REPLACEMENT-KEY\n")
                replacement.chmod(0o600)
                swap = root_path / "swap"
                if swap_kind == "regular":
                    swap.write_text("REPLACEMENT-KEY\n")
                    swap.chmod(0o600)
                else:
                    swap.symlink_to(replacement)

                real_open = os.open
                swapped = False

                def open_then_swap(path, flags, *args, **kwargs):
                    nonlocal swapped
                    descriptor = real_open(path, flags, *args, **kwargs)
                    if Path(path) == pem_file and not swapped:
                        swapped = True
                        os.replace(swap, pem_file)
                    return descriptor

                def authenticate(app_id, installation_id, pem_content, runner):
                    self.assertEqual(pem_content, b"ORIGINAL-KEY\n")
                    self.assertEqual(pem_file.read_text(), "REPLACEMENT-KEY\n")
                    raise update.support.BootstrapError("stop after pinned authentication")

                with mock.patch.object(update.os, "open", side_effect=open_then_swap):
                    result = update.main(
                        ["--version", "v1.2.3"],
                        script_dir=public,
                        runner=FakeRunner(),
                        token_provider=authenticate,
                        sandbox_root=root,
                    )

                self.assertEqual(result, 1)

    def test_update_accepts_extended_semver_and_rejects_near_misses(self):
        update = load_update()
        runner = FakeRunner()
        self.assertEqual(
            update.main(["--version", "v1.2.3-rc.1+build.5"], runner=runner, interactive=False),
            1,
        )
        self.assertEqual(runner.commands, [])
        self.assertEqual(
            update.main(["--version", "v1.2.3-"], runner=FakeRunner(), interactive=False),
            1,
        )
    def test_updater_rejects_unrelated_private_repository_before_pulling(self):
        update = load_update()

        class UnrelatedRunner(FakeRunner):
            def run(self, command, **kwargs):
                result = super().run(command, **kwargs)
                if command[-3:] == ["remote", "get-url", "origin"]:
                    return subprocess.CompletedProcess(
                        command, 0,
                        stdout="https://github.com/example/unrelated.git\n",
                        stderr="",
                    )
                return result

        runner = UnrelatedRunner()
        with tempfile.TemporaryDirectory() as root:
            root_path = Path(root)
            public = root_path / "src/deploy-script"
            private = root_path / "opt/granforge/robot-deploy"
            (public / ".git").mkdir(parents=True)
            (private / ".git").mkdir(parents=True)
            config = root_path / "etc/granforge"
            config.mkdir(parents=True)
            env_file = config / "deploy.env"
            pem_file = config / "github-app.pem"
            env_file.write_text("GITHUB_APP_ID=123\nGITHUB_INSTALLATION_ID=456\n")
            pem_file.write_text("key\n")
            env_file.chmod(0o600)
            pem_file.chmod(0o600)

            result = update.main(
                ["--version", "v2.3.4"],
                script_dir=public,
                runner=runner,
                token_provider=lambda *_: self.fail("must fail before auth"),
                sandbox_root=root,
            )

        self.assertEqual(result, 1)
        self.assertFalse(any("pull" in command for command in runner.commands))

    def test_updater_rejects_invalid_app_ids_before_authentication(self):
        update = load_update()
        runner = FakeRunner()
        with tempfile.TemporaryDirectory() as root:
            root_path = Path(root)
            public = root_path / "src/deploy-script"
            private = root_path / "opt/granforge/robot-deploy"
            (public / ".git").mkdir(parents=True)
            (private / ".git").mkdir(parents=True)
            config = root_path / "etc/granforge"
            config.mkdir(parents=True)
            env_file = config / "deploy.env"
            pem_file = config / "github-app.pem"
            env_file.write_text("GITHUB_APP_ID=12x\nGITHUB_INSTALLATION_ID=456\n")
            pem_file.write_text("key\n")
            env_file.chmod(0o600)
            pem_file.chmod(0o600)

            result = update.main(
                ["--version", "v2.3.4"],
                script_dir=public,
                runner=runner,
                token_provider=lambda *_: self.fail("invalid IDs must not authenticate"),
                environ={"GRANFORGE_BOOTSTRAP_REEXEC": "1"},
                sandbox_root=root,
            )

        self.assertEqual(result, 1)

    def test_updater_credentials_must_be_regular_private_and_root_owned_in_production(self):
        update = load_update()
        with tempfile.TemporaryDirectory() as root:
            root_path = Path(root)
            credential = root_path / "credential"
            credential.write_text("secret")
            credential.chmod(0o600)

            update.validate_credential(credential, sandbox=True)
            if os.geteuid() != 0:
                with self.assertRaises(update.support.BootstrapError):
                    update.validate_credential(credential, sandbox=False)

            credential.chmod(0o640)
            with self.assertRaises(update.support.BootstrapError):
                update.validate_credential(credential, sandbox=True)
            credential.chmod(0o600)
            link = root_path / "credential-link"
            link.symlink_to(credential)
            with self.assertRaises(update.support.BootstrapError):
                update.validate_credential(link, sandbox=True)

    def test_update_reexecs_once_only_when_public_head_changes_and_pid_marker_cannot_bypass(self):
        update = load_update()

        class ChangingHeadRunner(FakeRunner):
            def __init__(self):
                super().__init__()
                self.public_heads = iter(("old-head", "new-head", "new-head", "new-head"))

            def run(self, command, **kwargs):
                result = super().run(command, **kwargs)
                checkout = command_checkout(command)
                public = checkout is not None and checkout.name == "deploy-script"
                if public and command[-2:] == ["rev-parse", "HEAD"]:
                    return subprocess.CompletedProcess(
                        command, 0, stdout=next(self.public_heads) + "\n", stderr=""
                    )
                if public and command[-2:] == ["rev-parse", "refs/remotes/origin/main"]:
                    return subprocess.CompletedProcess(
                        command, 0, stdout="new-head\n", stderr=""
                    )
                return result

        runner = ChangingHeadRunner()
        with tempfile.TemporaryDirectory() as root:
            root_path = Path(root)
            public = root_path / "src/deploy-script"
            private = root_path / "opt/granforge/robot-deploy"
            (public / ".git").mkdir(parents=True)
            (private / ".git").mkdir(parents=True)
            config = root_path / "etc/granforge"
            config.mkdir(parents=True)
            (config / "deploy.env").write_text(
                "GITHUB_APP_ID=123\nGITHUB_INSTALLATION_ID=456\n"
            )
            (config / "github-app.pem").write_text("key\n")
            (config / "deploy.env").chmod(0o600)
            (config / "github-app.pem").chmod(0o600)
            bundle = root_path / "granforge-linux-amd64.tar.gz"

            first = update.main(
                [
                    "--version", "v2.3.4",
                    "--bundle", str(bundle),
                ],
                script_dir=public,
                runner=runner,
                token_provider=lambda *_: self.fail("auth must follow re-exec"),
                environ={
                    "GRANFORGE_ROOT": "/hostile",
                    "GRANFORGE_ARCH": "hostile",
                    "GRANFORGE_SYSTEMCTL": "/hostile/systemctl",
                    "GRANFORGE_HEALTHCHECK": "/hostile/health",
                    "GRANFORGE_TEST_FAIL_POINT": "hostile",
                    "GRANFORGE_BOOTSTRAP_REEXEC": str(os.getpid()),
                    "SUDO_UID": str(os.getuid()),
                    "SUDO_GID": str(os.getgid()),
                },
                sandbox_root=root,
                effective_uid=0,
            )
            first_exec = runner.exec_command[0]
            first_env = runner.exec_command[1]
            second = update.main(
                [
                    "--version", "v2.3.4",
                    "--bundle", str(bundle),
                ],
                script_dir=public,
                runner=runner,
                token_provider=lambda *_: "token",
                environ={
                    "GRANFORGE_ROOT": "/hostile",
                    "GRANFORGE_ARCH": "hostile",
                    "GRANFORGE_SYSTEMCTL": "/hostile/systemctl",
                    "GRANFORGE_HEALTHCHECK": "/hostile/health",
                    "GRANFORGE_TEST_FAIL_POINT": "hostile",
                    "GRANFORGE_BOOTSTRAP_REEXEC": str(os.getpid()),
                    "SUDO_UID": str(os.getuid()),
                    "SUDO_GID": str(os.getgid()),
                },
                sandbox_root=root,
                effective_uid=0,
            )

        self.assertEqual((first, second), (0, 0))
        pulls = [command for command in runner.commands if "pull" in command]
        self.assertEqual(len(pulls), 3)
        self.assertTrue(all(command[-2:] == ["pull", "--ff-only"] for command in pulls))
        self.assertEqual(first_exec[0], sys.executable)
        self.assertEqual(first_exec[1], str(public / "update-robot-env.py"))
        self.assertNotIn("GRANFORGE_BOOTSTRAP_REEXEC", first_env)
        for hostile in (
            "GRANFORGE_ROOT",
            "GRANFORGE_ARCH",
            "GRANFORGE_SYSTEMCTL",
            "GRANFORGE_HEALTHCHECK",
            "GRANFORGE_TEST_FAIL_POINT",
        ):
            self.assertNotIn(hostile, first_env)
        self.assertEqual(first_exec[2:6], [
            "--version", "v2.3.4", "--bundle", str(bundle)
        ])
        self.assertEqual(
            runner.exec_command[0][-4:],
            ["--version", "v2.3.4", "--bundle", str(bundle)],
        )
        self.assertEqual(runner.exec_command[1]["GRANFORGE_ROOT"], root)
        for hostile in (
            "GRANFORGE_ARCH",
            "GRANFORGE_SYSTEMCTL",
            "GRANFORGE_HEALTHCHECK",
            "GRANFORGE_TEST_FAIL_POINT",
            "GRANFORGE_BOOTSTRAP_REEXEC",
        ):
            self.assertNotIn(hostile, runner.exec_command[1])
        self.assertEqual(sum(event[0] == "exec" for event in runner.events), 2)
        for command in runner.commands:
            checkout = command_checkout(command)
            if checkout == public:
                self.assertEqual(
                    command[1:3], ["-c", f"safe.directory={public}"]
                )
            elif checkout == private:
                self.assertNotIn("safe.directory=", " ".join(command))

    def test_update_missing_repository_fails_before_pulling_either_repo(self):
        update = load_update()
        runner = FakeRunner()
        with tempfile.TemporaryDirectory() as root:
            root_path = Path(root)
            public = root_path / "src/deploy-script"
            (public / ".git").mkdir(parents=True)
            config = root_path / "etc/granforge"
            config.mkdir(parents=True)
            (config / "deploy.env").write_text(
                "GITHUB_APP_ID=123\nGITHUB_INSTALLATION_ID=456\n"
            )
            (config / "github-app.pem").write_text("key\n")
            (config / "deploy.env").chmod(0o600)
            (config / "github-app.pem").chmod(0o600)

            result = update.main(
                ["--version", "v2.3.4"],
                script_dir=public,
                runner=runner,
                token_provider=lambda *_: "token",
                sandbox_root=root,
            )

        self.assertEqual(result, 1)
        self.assertFalse(any("pull" in command for command in runner.commands))
        self.assertIsNone(runner.exec_command)

    def test_update_dirty_repository_fails_before_pulling(self):
        update = load_update()

        class DirtyRunner(FakeRunner):
            def run(self, command, **kwargs):
                result = super().run(command, **kwargs)
                checkout = command_checkout(command)
                if "status" in command and checkout is not None and checkout.name == "deploy-script":
                    return subprocess.CompletedProcess(command, 0, stdout=" M README.md\n", stderr="")
                return result

        runner = DirtyRunner()
        with tempfile.TemporaryDirectory() as root:
            root_path = Path(root)
            public = root_path / "src/deploy-script"
            private = root_path / "opt/granforge/robot-deploy"
            (public / ".git").mkdir(parents=True)
            (private / ".git").mkdir(parents=True)
            config = root_path / "etc/granforge"
            config.mkdir(parents=True)
            (config / "deploy.env").write_text(
                "GITHUB_APP_ID=123\nGITHUB_INSTALLATION_ID=456\n"
            )
            (config / "github-app.pem").write_text("key\n")
            (config / "deploy.env").chmod(0o600)
            (config / "github-app.pem").chmod(0o600)

            result = update.main(
                ["--version", "v2.3.4"],
                script_dir=public,
                runner=runner,
                token_provider=lambda *_: "token",
                sandbox_root=root,
            )

        self.assertEqual(result, 1)
        self.assertFalse(any("pull" in command for command in runner.commands))

    def test_noninteractive_update_never_selects_latest_implicitly(self):
        update = load_update()
        runner = FakeRunner()

        result = update.main([], runner=runner, interactive=False)

        self.assertEqual(result, 1)
        self.assertEqual(runner.commands, [])
        self.assertIsNone(runner.exec_command)


if __name__ == "__main__":
    unittest.main()
