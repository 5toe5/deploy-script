#!/usr/bin/env python3
"""Refresh bootstrap/deployer repositories, then hand off release updating."""

import argparse
import os
import sys
from pathlib import Path

import setup_robot_env_support as support


PRIVATE_REPO = "5toe5/robot-deploy"


def log(message):
    print(f"[update] {message}", file=sys.stderr)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release", default="latest")
    parser.add_argument("--bundle", type=Path)
    return parser.parse_args(argv)


def release_args(option, args):
    forwarded = [option, args.release]
    if args.bundle:
        forwarded.extend(["--bundle", str(args.bundle)])
    return forwarded


def deployer_args(args):
    return release_args("--version", args)


def updater_args(args):
    return release_args("--release", args)


def read_env(content):
    values = {}
    if isinstance(content, bytes):
        content = content.decode("utf-8-sig")
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip("'\"")
    return values


def read_credential(path, sandbox=False):
    expected_owner = os.geteuid() if sandbox else 0
    return support.read_secure_file(path, expected_uid=expected_owner, exact_mode=0o600)


def validate_credential(path, sandbox=False):
    read_credential(path, sandbox)


def main(
    argv=None,
    *,
    script_dir=None,
    runner=None,
    token_provider=None,
    environ=None,
    interactive=None,
    sandbox_root=None,
    effective_uid=None,
):
    args = parse_args(argv)
    root = Path("/") if sandbox_root is None else Path(sandbox_root)
    runner = runner or support.CommandRunner()
    token_provider = token_provider or support.installation_token
    environ = os.environ if environ is None else environ
    script_dir = Path(__file__).resolve().parent if script_dir is None else Path(script_dir)
    effective_uid = os.geteuid() if effective_uid is None else effective_uid
    try:
        if args.release != "latest" and not support.SEMVER.fullmatch(args.release):
            raise support.BootstrapError(
                "--release must be an explicit SemVer tag such as v1.2.3 or v1.2.3-rc.1"
            )
        if args.bundle and args.release == "latest":
            raise support.BootstrapError("--bundle requires an explicit SemVer --release tag")
        if sandbox_root is None and effective_uid != 0:
            raise support.BootstrapError("run update as root")

        env_file = root / "etc/granforge/deploy.env"
        pem_file = root / "etc/granforge/github-app.pem"
        sandbox = sandbox_root is not None
        env_content = read_credential(env_file, sandbox)
        pem_content = read_credential(pem_file, sandbox)
        config = read_env(env_content)
        app_id = config.get("GITHUB_APP_ID", "")
        installation_id = config.get("GITHUB_INSTALLATION_ID", "")
        if not app_id or not installation_id:
            raise support.BootstrapError("deployment credential IDs are incomplete")
        support.validate_app_ids(app_id, installation_id)

        deploy_dir = root / "opt/granforge/robot-deploy"
        for checkout in (script_dir, deploy_dir):
            if not (checkout / ".git").is_dir():
                raise support.BootstrapError(f"missing git repository: {checkout}")
        public_git_context = support.checkout_git_context(
            script_dir, environ, effective_uid
        )
        support.verify_main_checkout(deploy_dir, PRIVATE_REPO, runner)
        log("Pulling public deploy-script with --ff-only...")
        before, after = support.refresh_main_checkout(
            script_dir,
            "5toe5/deploy-script",
            runner,
            context=public_git_context,
        )
        if before != after:
            command = [sys.executable, str(script_dir / "update-robot-env.py")]
            command.extend(updater_args(args))
            reexec_env = dict(environ)
            for name in (
                "GITHUB_APP_ID",
                "GITHUB_INSTALLATION_ID",
                "GITHUB_APP_PEM_FILE",
                "GITHUB_APP_PEM",
                "PEM_FILE",
                "GRANFORGE_ROOT",
                "GRANFORGE_ARCH",
                "GRANFORGE_SYSTEMCTL",
                "GRANFORGE_HEALTHCHECK",
                "GRANFORGE_TEST_FAIL_POINT",
                "GRANFORGE_BOOTSTRAP_REEXEC",
                "SUDO_UID",
                "SUDO_GID",
            ):
                reexec_env.pop(name, None)
            reexec_env.update(public_git_context["identity"])
            runner.exec(command, reexec_env)
            return 0

        log("Authenticating as read-only GitHub App...")
        token = token_provider(app_id, installation_id, pem_content, runner)
        log("Pulling private robot-deploy with --ff-only...")
        support.refresh_main_checkout(deploy_dir, PRIVATE_REPO, runner, token)

        command = [str(deploy_dir / "update.sh")]
        command.extend(deployer_args(args))
        clean_env = dict(environ)
        for name in (
            "GITHUB_APP_ID",
            "GITHUB_INSTALLATION_ID",
            "GITHUB_APP_PEM_FILE",
            "GITHUB_APP_PEM",
            "PEM_FILE",
            "GRANFORGE_ROOT",
            "GRANFORGE_ARCH",
            "GRANFORGE_SYSTEMCTL",
            "GRANFORGE_HEALTHCHECK",
            "GRANFORGE_TEST_FAIL_POINT",
            "SUDO_UID",
            "SUDO_GID",
        ):
            clean_env.pop(name, None)
        clean_env.pop("GRANFORGE_BOOTSTRAP_REEXEC", None)
        clean_env.update(public_git_context["identity"])
        if sandbox_root is not None:
            clean_env["GRANFORGE_ROOT"] = str(root)
        runner.exec(command, clean_env)
        return 0
    except support.BootstrapError as error:
        print(f"[update] ERROR: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
