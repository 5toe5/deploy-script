#!/usr/bin/env python3
"""Update deploy tooling and install the latest app release."""

import base64
import json
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path

DEPLOY_SCRIPT_REPO = "5toe5/deploy-script"
DEPLOY_REPO = "5toe5/robot-deploy"


def log(msg: str) -> None:
    print(f"[update] {msg}", file=sys.stderr)


def die(msg: str) -> None:
    print(f"[update] ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def check_cmd(cmd: str) -> None:
    if shutil.which(cmd) is None:
        die(f"'{cmd}' is not installed")


def load_env(env_path: Path) -> None:
    log(f"Loading environment from {env_path}")
    with env_path.open(encoding="utf-8-sig") as f:
        for raw_line in f:
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[7:].lstrip()
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip()
            if not key or key in os.environ:
                continue
            if (value.startswith('"') and value.endswith('"')) or (
                value.startswith("'") and value.endswith("'")
            ):
                value = value[1:-1]
            os.environ[key] = value


def require_value(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        die(f"{name} not set in ~/robot-env/.env")
    return value


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def generate_jwt(app_id: str, pem_path: str) -> str:
    now = datetime.now(UTC)
    iat = now - timedelta(seconds=60)
    exp = now + timedelta(minutes=9)

    header = b64url(json.dumps({"alg": "RS256", "typ": "JWT"}).encode())
    payload = b64url(
        json.dumps(
            {"iat": int(iat.timestamp()), "exp": int(exp.timestamp()), "iss": app_id}
        ).encode()
    )

    result = subprocess.run(
        ["openssl", "dgst", "-sha256", "-sign", pem_path],
        input=f"{header}.{payload}".encode(),
        capture_output=True,
    )
    if result.returncode != 0:
        die(f"Failed to sign JWT: {result.stderr.decode().strip()}")

    return f"{header}.{payload}.{b64url(result.stdout)}"


def get_installation_token(jwt: str, installation_id: str) -> str:
    req = urllib.request.Request(
        f"https://api.github.com/app/installations/{installation_id}/access_tokens",
        method="POST",
        headers={
            "Authorization": f"Bearer {jwt}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with urllib.request.urlopen(req) as response:
            data = json.loads(response.read())
    except urllib.error.HTTPError as e:
        die(f"GitHub API error: {e.code} - {e.read().decode()}")
    except Exception as e:
        die(f"Failed to get installation token: {e}")

    token = data.get("token", "")
    if not token:
        die("Failed to obtain GitHub installation access token")
    return token


def ensure_clean_repo(repo_dir: Path) -> None:
    if not (repo_dir / ".git").exists():
        die(f"Missing git repository at {repo_dir} - run setup-robot-env.py first")
    result = subprocess.run(
        ["git", "-C", str(repo_dir), "status", "--short"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        die(f"Failed to inspect git status for {repo_dir}")
    if result.stdout.strip():
        die(f"Repository has local changes: {repo_dir}")


def pull_repo(repo_dir: Path, repo_name: str, token: str) -> None:
    ensure_clean_repo(repo_dir)
    log(f"Pulling latest {repo_name}...")
    result = subprocess.run(
        [
            "git",
            "-C",
            str(repo_dir),
            "-c",
            f"url.https://x-access-token:{token}@github.com/.insteadOf=https://github.com/",
            "pull",
            "--ff-only",
        ]
    )
    if result.returncode != 0:
        die(f"Failed to pull {repo_name}")


def main() -> None:
    script_dir = Path(__file__).parent.resolve()
    home_dir = script_dir.parent
    env_file = home_dir / "robot-env" / ".env"
    pem_file = home_dir / "robot-env" / ".github-app.pem"
    deploy_dir = home_dir / "robot-deploy"

    check_cmd("git")
    check_cmd("openssl")

    if not env_file.exists():
        die(f"Missing {env_file} - run setup-robot-env.py first")
    if not pem_file.exists():
        die(f"Missing {pem_file} - run setup-robot-env.py first")
    if not deploy_dir.exists():
        die(f"Missing {deploy_dir} - run setup-robot-env.py first")

    load_env(env_file)
    github_app_id = require_value("GITHUB_APP_ID")
    installation_id = require_value("GITHUB_INSTALLATION_ID")

    log("Authenticating as GitHub App...")
    jwt = generate_jwt(github_app_id, str(pem_file))
    token = get_installation_token(jwt, installation_id)

    pull_repo(script_dir, DEPLOY_SCRIPT_REPO, token)
    pull_repo(deploy_dir, DEPLOY_REPO, token)

    deploy_update = deploy_dir / "update.sh"
    if not deploy_update.exists():
        die(f"Missing update script: {deploy_update}")
    if not os.access(deploy_update, os.X_OK):
        die(f"Update script is not executable: {deploy_update}")

    log("Running robot-deploy update...")
    os.execv(str(deploy_update), [str(deploy_update)])


if __name__ == "__main__":
    main()
