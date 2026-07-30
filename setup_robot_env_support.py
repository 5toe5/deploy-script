"""Shared bootstrap command and GitHub App authentication support."""

import base64
import json
import os
import pwd
import re
import shutil
import subprocess
import stat
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path


SEMVER = re.compile(
    r"v(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)"
    r"(?:-(?:0|[1-9]\d*|[0-9A-Za-z-]*[A-Za-z-][0-9A-Za-z-]*)"
    r"(?:\.(?:0|[1-9]\d*|[0-9A-Za-z-]*[A-Za-z-][0-9A-Za-z-]*))*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
)


class BootstrapError(Exception):
    pass


class CommandRunner:
    def which(self, command):
        return shutil.which(command)

    def run(self, command, **kwargs):
        return subprocess.run(command, **kwargs)

    def exec(self, command, env):
        os.execve(command[0], command, env)

    def trusted_executable(self, command):
        executable = shutil.which(command, path=os.defpath)
        if executable is None:
            raise BootstrapError(f"'{command}' is not installed in the system path")
        return Path(executable).resolve()


def validate_app_ids(app_id, installation_id):
    for name, value in (
        ("GITHUB_APP_ID", app_id),
        ("GITHUB_INSTALLATION_ID", installation_id),
    ):
        if not re.fullmatch(r"[0-9]+", value):
            raise BootstrapError(f"{name} must be numeric")


def read_secure_file(path, expected_uid=None, exact_mode=None):
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise BootstrapError(f"cannot safely open credential file: {path}") from error
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise BootstrapError(f"credential file is not regular: {path}")
        if expected_uid is not None and metadata.st_uid != expected_uid:
            raise BootstrapError(f"credential file has the wrong owner: {path}")
        if exact_mode is not None and stat.S_IMODE(metadata.st_mode) != exact_mode:
            raise BootstrapError(f"credential file must have mode {exact_mode:04o}: {path}")
        with os.fdopen(descriptor, "rb") as credential:
            descriptor = -1
            return credential.read()
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def checkout_git_context(path, environ, effective_uid=None):
    path = Path(path)
    effective_uid = os.geteuid() if effective_uid is None else effective_uid
    try:
        resolved = path.resolve(strict=True)
        metadata = path.lstat()
    except (FileNotFoundError, OSError) as error:
        raise BootstrapError(f"checkout path is missing or unsafe: {path}") from error
    if path != resolved or not path.is_dir():
        raise BootstrapError(f"checkout path must be an exact real directory: {path}")

    sudo_uid = environ.get("SUDO_UID")
    sudo_gid = environ.get("SUDO_GID")
    if effective_uid != 0:
        if sudo_uid is not None or sudo_gid is not None:
            raise BootstrapError("SUDO_UID/SUDO_GID are only valid for a root sudo process")
        if metadata.st_uid != effective_uid:
            raise BootstrapError(f"checkout is not owned by the invoking user: {path}")
        return {"safe_directory": None, "identity": {}}

    if sudo_uid is None and sudo_gid is None:
        if metadata.st_uid != 0:
            raise BootstrapError(
                f"root must use sudo identity for a non-root-owned checkout: {path}"
            )
        return {"safe_directory": None, "identity": {}}
    if sudo_uid is None or sudo_gid is None:
        raise BootstrapError("SUDO_UID and SUDO_GID must be supplied together")
    if not re.fullmatch(r"[0-9]+", sudo_uid) or not re.fullmatch(r"[0-9]+", sudo_gid):
        raise BootstrapError("SUDO_UID and SUDO_GID must be numeric")
    uid, gid = int(sudo_uid), int(sudo_gid)
    if uid == 0 or gid == 0:
        raise BootstrapError("sudo checkout identity must be non-root")
    try:
        account = pwd.getpwuid(uid)
    except KeyError as error:
        raise BootstrapError("SUDO_UID does not identify a local user") from error
    if account.pw_gid != gid:
        raise BootstrapError("SUDO_GID is not the invoking user's primary group")
    if metadata.st_uid != uid:
        raise BootstrapError(f"checkout ownership does not match sudo identity: {path}")
    return {
        "safe_directory": str(resolved),
        "identity": {"SUDO_UID": str(uid), "SUDO_GID": str(gid)},
    }


def git_environment(token=None, identity=None):
    settings = [
        ("core.hooksPath", "/dev/null"),
        ("core.fsmonitor", "false"),
        ("credential.helper", ""),
    ]
    if token is not None:
        basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
        settings.append(("http.extraHeader", f"Authorization: Basic {basic}"))
    environment = {
        "PATH": os.defpath,
        "HOME": "/",
        "LC_ALL": "C",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_SYSTEM": "/dev/null",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_ALLOW_PROTOCOL": "https",
        "GIT_PROTOCOL_FROM_USER": "0",
        "GIT_CONFIG_COUNT": str(len(settings)),
    }
    for index, (key, value) in enumerate(settings):
        environment[f"GIT_CONFIG_KEY_{index}"] = key
        environment[f"GIT_CONFIG_VALUE_{index}"] = value
    environment.update(identity or {})
    return environment


def git_run(runner, arguments, token=None, context=None, **kwargs):
    executable = runner.trusted_executable("git")
    context = context or {"safe_directory": None, "identity": {}}
    scoped = []
    if context["safe_directory"] is not None:
        scoped.extend(["-c", f"safe.directory={context['safe_directory']}"])
    kwargs["env"] = git_environment(token, context["identity"])
    return runner.run([str(executable), *scoped, *arguments], **kwargs)


def is_github_repo_remote(url, repository):
    value = url.strip()
    if value.startswith("git@github.com:"):
        path = value[len("git@github.com:"):]
    else:
        parsed = urllib.parse.urlsplit(value)
        if parsed.scheme not in {"https", "ssh"} or parsed.hostname != "github.com":
            return False
        path = parsed.path.lstrip("/")
    path = path.rstrip("/")
    if path.endswith(".git"):
        path = path[:-4]
    return path == repository


def _git_output(runner, path, arguments, context=None):
    result = git_run(
        runner,
        ["-C", str(path), *arguments],
        context=context,
        capture_output=True,
        text=True,
    )
    output = result.stdout.decode() if isinstance(result.stdout, bytes) else result.stdout
    if result.returncode:
        raise BootstrapError(f"git {' '.join(arguments)} failed for {path}")
    return output.strip()


def verify_main_checkout(path, repository, runner, context=None):
    remote = _git_output(
        runner, path, ["remote", "get-url", "origin"], context
    )
    if not is_github_repo_remote(remote, repository):
        raise BootstrapError(f"repository is not {repository}: {path}")
    if _git_output(
        runner, path, ["symbolic-ref", "--short", "HEAD"], context
    ) != "main":
        raise BootstrapError(f"repository is not on main: {path}")
    upstream = _git_output(
        runner,
        path,
        ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"],
        context,
    )
    if upstream != "origin/main":
        raise BootstrapError(f"main does not track origin/main: {path}")
    if _git_output(runner, path, ["status", "--short"], context):
        raise BootstrapError(f"repository has local changes: {path}")
    public_remote = f"https://github.com/{repository}.git"
    if git_run(runner, [
        "-C", str(path), "remote", "set-url", "origin", public_remote
    ], context=context).returncode:
        raise BootstrapError(f"failed to sanitize origin for {path}")


def require_remote_main_tip(path, runner, context=None):
    head = _git_output(runner, path, ["rev-parse", "HEAD"], context)
    remote_main = _git_output(
        runner, path, ["rev-parse", "refs/remotes/origin/main"], context
    )
    if head != remote_main:
        raise BootstrapError(f"HEAD does not equal origin/main after pull: {path}")
    return head


def refresh_main_checkout(path, repository, runner, token=None, context=None):
    verify_main_checkout(path, repository, runner, context)
    before = _git_output(runner, path, ["rev-parse", "HEAD"], context)
    if git_run(
        runner,
        ["-C", str(path), "pull", "--ff-only"],
        token=token,
        context=context,
    ).returncode:
        raise BootstrapError(f"repository cannot be fast-forwarded: {path}")
    after = require_remote_main_tip(path, runner, context)
    return before, after


def _b64url(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def installation_token(app_id, installation_id, pem_content, runner):
    now = datetime.now(timezone.utc)
    header = _b64url(json.dumps({"alg": "RS256", "typ": "JWT"}).encode())
    payload = _b64url(json.dumps({
        "iat": int((now - timedelta(seconds=60)).timestamp()),
        "exp": int((now + timedelta(minutes=9)).timestamp()),
        "iss": app_id,
    }).encode())
    unsigned = f"{header}.{payload}".encode()
    with tempfile.TemporaryFile() as pinned_key:
        pinned_key.write(pem_content)
        pinned_key.flush()
        pinned_key.seek(0)
        descriptor = pinned_key.fileno()
        signed = runner.run(
            [
                "openssl",
                "dgst",
                "-sha256",
                "-sign",
                f"/proc/self/fd/{descriptor}",
            ],
            input=unsigned,
            capture_output=True,
            pass_fds=(descriptor,),
        )
    if signed.returncode:
        raise BootstrapError("failed to sign GitHub App authentication request")
    jwt = f"{header}.{payload}.{_b64url(signed.stdout)}"
    request = urllib.request.Request(
        f"https://api.github.com/app/installations/{installation_id}/access_tokens",
        data=(
            b'{"repositories":["robot-deploy"],'
            b'"permissions":{"contents":"read"}}'
        ),
        method="POST",
        headers={
            "Authorization": f"Bearer {jwt}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with urllib.request.urlopen(request) as response:
            token = json.loads(response.read()).get("token", "")
    except (OSError, urllib.error.HTTPError, json.JSONDecodeError) as error:
        raise BootstrapError("failed to obtain GitHub installation token") from error
    if not token:
        raise BootstrapError("GitHub returned an empty installation token")
    return token
