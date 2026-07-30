#!/usr/bin/env python3
"""Bootstrap a fresh Granforge host using only the Python standard library."""

import argparse
import ipaddress
import os
import re
import socket
import stat
import sys
import tempfile
from pathlib import Path

import setup_robot_env_support as support


PRIVATE_REPO = "5toe5/robot-deploy"
PUBLIC_REMOTE = f"https://github.com/{PRIVATE_REPO}.git"
SEMVER = support.SEMVER
PACKAGES = ("git", "openssl", "systemd")
COMMANDS = {"systemd": "systemctl"}


BootstrapError = support.BootstrapError
CommandRunner = support.CommandRunner


def log(message):
    print(f"[setup] {message}", file=sys.stderr)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--non-interactive", action="store_true")
    parser.add_argument("--version")
    parser.add_argument("--bundle", type=Path)
    parser.add_argument("--github-app-id")
    parser.add_argument("--github-installation-id")
    parser.add_argument("--env", type=Path)
    parser.add_argument("--pem", type=Path)
    parser.add_argument("--pem-file", type=Path)
    parser.add_argument("--motion-agent-agent-host")
    parser.add_argument("--simulator-only", action="store_true")
    return parser.parse_args(argv)


def detect_lan_address():
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("1.1.1.1", 80))
            return sock.getsockname()[0]
    except OSError:
        return ""


def ask(input_fn, label, default=""):
    suffix = f" [{default}]" if default else ""
    value = input_fn(f"{label}{suffix}: ").strip()
    return value or default


def validate_persisted(name, value, numeric=False):
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise BootstrapError(f"{name} must not contain control characters")
    if numeric and not re.fullmatch(r"[0-9]+", value):
        raise BootstrapError(f"{name} must be numeric")
    if not numeric and not re.fullmatch(r"[A-Za-z0-9._:%-]+", value):
        raise BootstrapError(f"{name} contains unsafe characters")


def validate_agent_host(host, simulator_only):
    try:
        address = ipaddress.ip_address(host)
    except ValueError as error:
        raise BootstrapError(
            "MOTION_AGENT_AGENT_HOST must be a concrete IP address"
        ) from error
    mapped = getattr(address, "ipv4_mapped", None)
    effective = mapped if mapped is not None else address
    if effective.is_unspecified or effective.is_multicast:
        raise BootstrapError(
            "MOTION_AGENT_AGENT_HOST must be a usable unicast IP address"
        )
    loopback = address.is_loopback or effective.is_loopback
    if loopback and not simulator_only:
        raise BootstrapError(
            "loopback MOTION_AGENT_AGENT_HOST requires --simulator-only"
        )


def serialize_env(values):
    lines = []
    for name, value in values.items():
        if not re.fullmatch(r"[A-Z][A-Z0-9_]*", name):
            raise BootstrapError(f"invalid environment key: {name}")
        validate_persisted(name, value, numeric=name in {
            "GITHUB_APP_ID", "GITHUB_INSTALLATION_ID"
        })
        lines.append(f"{name}={value}\n")
    return "".join(lines)


def os_family(root):
    release = root / "etc/os-release"
    if not release.is_file():
        raise BootstrapError(f"cannot detect Linux distribution: missing {release}")
    values = {}
    for raw_line in release.read_text().splitlines():
        if "=" in raw_line:
            key, value = raw_line.split("=", 1)
            values[key] = value.strip().strip('"')
    names = {values.get("ID", ""), *values.get("ID_LIKE", "").split()}
    if names & {"debian", "ubuntu", "raspbian"}:
        return "debian"
    if names & {"arch"}:
        return "arch"
    raise BootstrapError("supported distributions are Debian, Ubuntu, Raspberry Pi OS, and Arch")


def ensure_prerequisites(root, non_interactive, input_fn, runner):
    missing = [package for package in PACKAGES if not runner.which(COMMANDS.get(package, package))]
    if not missing:
        return
    family = os_family(root)
    if family == "debian":
        command = ["apt-get", "install", "-y", *missing]
        display = "apt-get update && " + " ".join(command)
        commands = [["apt-get", "update"], command]
    else:
        command = ["pacman", "-S", "--needed", "--noconfirm", *missing]
        display = " ".join(command)
        commands = [command]
    log(f"Required package command: {display}")
    if non_interactive:
        raise BootstrapError("non-interactive bootstrap requires all prerequisite packages already installed")
    if ask(input_fn, "Run this package command? (yes/no)", "no").lower() != "yes":
        raise BootstrapError("package installation declined")
    for package_command in commands:
        result = runner.run(package_command)
        if result.returncode:
            raise BootstrapError("package installation failed")


def write_private_temp(directory, prefix, content):
    descriptor, name = tempfile.mkstemp(dir=directory, prefix=prefix)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w") as private_file:
            descriptor = -1
            private_file.write(content)
            private_file.flush()
            os.fsync(private_file.fileno())
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    return Path(name)


def read_pem_nofollow(path):
    content = support.read_secure_file(path)
    if not content.strip():
        raise BootstrapError(f"GitHub App private key is empty: {path}")
    return content


def install_credentials(
    env_file,
    pem_file,
    env_content,
    pem_content,
    replace_fn=os.replace,
    expected_owner=None,
):
    directory = env_file.parent
    if pem_file.parent != directory:
        raise BootstrapError("credential files must share one directory")
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    if directory.is_symlink() or not directory.is_dir():
        raise BootstrapError(f"credential directory is not a real directory: {directory}")
    directory.chmod(0o700)

    expected_owner = os.geteuid() if expected_owner is None else expected_owner
    metadata = []
    for path in (env_file, pem_file):
        try:
            info = path.lstat()
        except FileNotFoundError:
            info = None
        if info is not None and (
            not stat.S_ISREG(info.st_mode)
            or stat.S_IMODE(info.st_mode) != 0o600
            or info.st_uid != expected_owner
        ):
            raise BootstrapError(
                f"installed credential must have the expected owner and mode 0600: {path}"
            )
        metadata.append(info)
    if (metadata[0] is None) != (metadata[1] is None):
        raise BootstrapError("installed deployment credentials are an incomplete pair")

    env_temp = None
    pem_temp = None
    backups = []
    replaced = 0
    try:
        env_temp = write_private_temp(directory, ".deploy.env.", env_content)
        pem_temp = write_private_temp(directory, ".github-app.pem.", pem_content)
        if metadata[0] is not None:
            for path in (env_file, pem_file):
                descriptor, backup_name = tempfile.mkstemp(
                    dir=directory, prefix=f".{path.name}.backup."
                )
                os.close(descriptor)
                os.unlink(backup_name)
                os.link(path, backup_name, follow_symlinks=False)
                backups.append(Path(backup_name))
        replace_fn(env_temp, env_file)
        replaced = 1
        env_temp = None
        replace_fn(pem_temp, pem_file)
        replaced = 2
        pem_temp = None
    except OSError as error:
        if len(backups) == 2 and replaced:
            os.replace(backups[0], env_file)
            os.replace(backups[1], pem_file)
            backups = []
        elif not backups and replaced:
            for path in (env_file, pem_file):
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass
        raise BootstrapError("failed to atomically install deployment credentials") from error
    finally:
        for path in (env_temp, pem_temp, *backups):
            if path is not None:
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass


def clone_private_repo(destination, token, runner):
    if destination.exists() and not (destination / ".git").is_dir():
        raise BootstrapError(f"{destination} exists but is not a git repository")
    if not destination.exists():
        result = support.git_run(
            runner,
            ["clone", PUBLIC_REMOTE, str(destination)],
            token=token,
        )
        if result.returncode:
            raise BootstrapError("failed to clone private robot-deploy repository")
    refresh_private_repo(destination, token, runner)


def refresh_private_repo(destination, token, runner):
    if not (destination / ".git").is_dir():
        raise BootstrapError(f"{destination} is not a git repository")
    support.refresh_main_checkout(destination, PRIVATE_REPO, runner, token)


def collect_config(args, environ, input_fn):
    if (args.env is None) != (args.pem is None):
        raise BootstrapError("--env and --pem are required together")
    env_mode = args.env is not None
    if env_mode and args.pem_file is not None:
        raise BootstrapError("--pem and --pem-file cannot be used together")

    file_values = support.read_env_file(args.env) if env_mode else {}
    if env_mode:
        app_id = args.github_app_id if args.github_app_id is not None else file_values.get("GITHUB_APP_ID", "")
        installation_id = (
            args.github_installation_id
            if args.github_installation_id is not None
            else file_values.get("GITHUB_INSTALLATION_ID", "")
        )
        pem_file = args.pem
        host = (
            args.motion_agent_agent_host
            if args.motion_agent_agent_host is not None
            else file_values.get("MOTION_AGENT_AGENT_HOST", "")
        )
    else:
        app_id = args.github_app_id or environ.get("GITHUB_APP_ID", "").strip()
        installation_id = args.github_installation_id or environ.get("GITHUB_INSTALLATION_ID", "").strip()
        pem_file = args.pem_file
        if pem_file is None and environ.get("GITHUB_APP_PEM_FILE"):
            pem_file = Path(environ["GITHUB_APP_PEM_FILE"])
        host = args.motion_agent_agent_host or environ.get("MOTION_AGENT_AGENT_HOST", "").strip()

    if not env_mode and not args.non_interactive:
        app_id = app_id or ask(input_fn, "GitHub App ID")
        installation_id = installation_id or ask(input_fn, "GitHub App installation ID")
        pem_file = pem_file or Path(ask(input_fn, "Path to GitHub App private key (PEM)"))
        host = ask(input_fn, "MOTION_AGENT_AGENT_HOST (confirm LAN address)", host or detect_lan_address())
        if args.version is None:
            args.version = ask(input_fn, "Release version (blank lets robot-deploy offer tag/latest)")

    missing = []
    if not app_id:
        missing.append("GITHUB_APP_ID is required")
    if not installation_id:
        missing.append("GITHUB_INSTALLATION_ID is required")
    if pem_file is None:
        missing.append("--pem-file is required")
    if not host:
        missing.append("MOTION_AGENT_AGENT_HOST is required")
    if not env_mode and args.non_interactive and not args.version:
        missing.append("--version is required")
    if missing:
        raise BootstrapError("; ".join(missing))
    validate_persisted("GITHUB_APP_ID", app_id, numeric=True)
    validate_persisted("GITHUB_INSTALLATION_ID", installation_id, numeric=True)
    validate_persisted("MOTION_AGENT_AGENT_HOST", host)
    requested_version = args.version
    if args.bundle and (
        not requested_version
        or (env_mode and not SEMVER.fullmatch(requested_version))
    ):
        raise BootstrapError("--bundle requires an explicit SemVer --version tag")
    if requested_version and not SEMVER.fullmatch(requested_version):
        raise BootstrapError(
            "--version must be an explicit SemVer tag such as v1.2.3 or v1.2.3-rc.1"
        )
    if env_mode and args.version is None:
        args.version = "latest"
    validate_agent_host(host, args.simulator_only)
    return app_id, installation_id, pem_file, host


def main(
    argv=None,
    *,
    runner=None,
    token_provider=None,
    environ=None,
    input_fn=input,
    sandbox_root=None,
):
    args = parse_args(argv)
    root = Path("/") if sandbox_root is None else Path(sandbox_root)
    runner = runner or CommandRunner()
    environ = os.environ if environ is None else environ
    token_provider = token_provider or support.installation_token
    try:
        app_id, installation_id, pem_source, host = collect_config(args, environ, input_fn)
        if sandbox_root is None and os.geteuid() != 0:
            raise BootstrapError("run bootstrap as root (for example, sudo python3 setup-robot-env.py)")
        ensure_prerequisites(root, args.non_interactive or args.env is not None, input_fn, runner)

        pem_content = read_pem_nofollow(pem_source)
        log("Authenticating as read-only GitHub App...")
        token = token_provider(app_id, installation_id, pem_content, runner)
        etc_dir = root / "etc/granforge"
        env_file = etc_dir / "deploy.env"
        pem_file = etc_dir / "github-app.pem"
        install_credentials(
            env_file,
            pem_file,
            serialize_env({
                "GITHUB_APP_ID": app_id,
                "GITHUB_INSTALLATION_ID": installation_id,
                "MOTION_AGENT_AGENT_HOST": host,
            }),
            pem_content.decode("utf-8"),
            expected_owner=os.geteuid() if sandbox_root is not None else 0,
        )
        deploy_dir = root / "opt/granforge/robot-deploy"
        deploy_dir.parent.mkdir(parents=True, exist_ok=True)
        if deploy_dir.exists():
            refresh_private_repo(deploy_dir, token, runner)
        else:
            clone_private_repo(deploy_dir, token, runner)

        deploy_script = deploy_dir / "deploy.sh"
        command = [str(deploy_script)]
        if args.version:
            command.extend(["--version", args.version])
        if args.bundle:
            command.extend(["--bundle", str(args.bundle)])
        deploy_environ = dict(environ)
        for name in (
            "GITHUB_APP_ID",
            "GITHUB_INSTALLATION_ID",
            "GITHUB_APP_PEM_FILE",
            "GITHUB_APP_PEM",
            "PEM_FILE",
            "AGENT_HOST",
            "MOTION_AGENT_AGENT_HOST",
            "GRANFORGE_ROOT",
            "GRANFORGE_ARCH",
            "GRANFORGE_SYSTEMCTL",
            "GRANFORGE_HEALTHCHECK",
            "GRANFORGE_TEST_FAIL_POINT",
            "GRANFORGE_BOOTSTRAP_REEXEC",
        ):
            deploy_environ.pop(name, None)
        deploy_environ["MOTION_AGENT_AGENT_HOST"] = host
        if sandbox_root is not None:
            deploy_environ["GRANFORGE_ROOT"] = str(root)
        log("Handing off installation to robot-deploy...")
        runner.exec(command, deploy_environ)
        return 0
    except BootstrapError as error:
        print(f"[setup] ERROR: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
