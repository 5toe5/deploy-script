#!/usr/bin/env python3
# /// script
# dependencies = []
# ///
"""Setup script for robot deployment environment."""

import os
import sys
import json
import base64
import subprocess
import urllib.request
from pathlib import Path
from datetime import datetime, timedelta

DEPLOY_REPO = "5toe5/robot-deploy"
DOCS_REPO = "5toe5/robot-docs"


def log(msg: str) -> None:
    """Print a log message."""
    print(f"[setup] {msg}", file=sys.stderr)


def die(msg: str) -> None:
    """Print an error and exit."""
    print(f"[setup] ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def prompt(label: str, default: str = "", env_var: str = "") -> str:
    """Prompt for input, checking environment first."""
    if env_var and env_var in os.environ:
        value = os.environ[env_var]
        log(f"Using {env_var} from environment")
        return value
    
    if default:
        prompt_text = f"{label} [{default}]: "
    else:
        prompt_text = f"{label}: "
    
    try:
        value = input(prompt_text).strip()
    except EOFError:
        value = ""
    
    return value if value else default


def check_cmd(cmd: str) -> None:
    """Verify a command exists."""
    if subprocess.run(["which", cmd], capture_output=True).returncode != 0:
        die(f"'{cmd}' is not installed")


def b64url(data: bytes) -> str:
    """Base64 URL-safe encoding without padding."""
    return base64.urlsafe_b64encode(data).rstrip(b'=').decode('ascii')


def generate_jwt(app_id: str, pem_path: str) -> str:
    """Generate a GitHub App JWT."""
    now = datetime.utcnow()
    iat = now - timedelta(seconds=60)
    exp = now + timedelta(minutes=9)
    
    header = b64url(json.dumps({"alg": "RS256", "typ": "JWT"}).encode())
    payload = b64url(json.dumps({
        "iat": int(iat.timestamp()),
        "exp": int(exp.timestamp()),
        "iss": app_id
    }).encode())
    
    to_sign = f"{header}.{payload}".encode()
    
    # Sign with OpenSSL
    result = subprocess.run(
        ["openssl", "dgst", "-sha256", "-sign", pem_path],
        input=to_sign,
        capture_output=True
    )
    if result.returncode != 0:
        die(f"Failed to sign JWT: {result.stderr.decode()}")
    
    sig = b64url(result.stdout)
    return f"{header}.{payload}.{sig}"


def get_installation_token(jwt: str, installation_id: str):
    """Get an installation access token."""
    url = f"https://api.github.com/app/installations/{installation_id}/access_tokens"
    req = urllib.request.Request(
        url,
        method="POST",
        headers={
            "Authorization": f"Bearer {jwt}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28"
        }
    )
    
    try:
        with urllib.request.urlopen(req) as response:
            data = json.loads(response.read())
            return data["token"]
    except urllib.error.HTTPError as e:
        error_body = e.read().decode()
        die(f"GitHub API error: {e.code} - {error_body}")
    except Exception as e:
        die(f"Failed to get installation token: {e}")


def clone_repo(repo: str, dest: str, token: str) -> None:
    """Clone a repository using the installation token."""
    dest_path = Path(dest)
    if dest_path.exists():
        log(f"{dest} already exists, skipping clone")
        return
    
    clone_url = f"https://x-access-token:{token}@github.com/{repo}.git"
    result = subprocess.run(["git", "clone", clone_url, dest])
    if result.returncode != 0:
        die(f"Failed to clone {repo}")
    
    # Update remote URL to remove token
    subprocess.run(
        ["git", "-C", dest, "remote", "set-url", "origin", f"https://github.com/{repo}.git"],
        capture_output=True
    )


def load_local_env(script_dir: Path) -> None:
    """Load .env and PEM from script directory or current working directory."""
    # Check both script directory and current working directory
    search_dirs = [Path.cwd(), script_dir]
    
    # First pass: find .env files
    for search_dir in search_dirs:
        env_path = search_dir / ".env"
        if env_path.exists() and 'GITHUB_APP_ID' not in os.environ:
            log(f"Loading environment from {env_path}")
            with open(env_path) as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith('#') and '=' in line:
                        key, value = line.split('=', 1)
                        # Remove quotes if present
                        if (value.startswith('"') and value.endswith('"')) or \
                           (value.startswith("'") and value.endswith("'")):
                            value = value[1:-1]
                        if key not in os.environ:
                            os.environ[key] = value
    
    # Second pass: find PEM file (prefer current directory)
    for search_dir in search_dirs:
        pem_path = search_dir / ".github-app.pem"
        if pem_path.exists() and 'PEM_FILE' not in os.environ:
            log(f"Found PEM file at {pem_path}")
            os.environ['PEM_FILE'] = str(pem_path)
            break


def main():
    # Get script directory for loading local files
    script_dir = Path(__file__).parent.resolve()
    load_local_env(script_dir)
    
    # Check dependencies
    check_cmd("curl")
    check_cmd("openssl")
    check_cmd("git")
    
    # Setup paths
    env_dir = Path.home() / "robot-env"
    env_file = env_dir / ".env"
    pem_file = Path(os.environ.get("PEM_FILE", env_dir / ".github-app.pem"))
    
    # Create env directory with proper permissions
    env_dir.mkdir(parents=True, exist_ok=True)
    os.umask(0o077)
    
    # Get configuration
    github_app_id = prompt("GitHub App ID", env_var="GITHUB_APP_ID")
    github_installation_id = prompt("GitHub Installation ID", env_var="GITHUB_INSTALLATION_ID")
    agent_host = prompt("Agent host (LAN IP this device is reachable at)", "127.0.0.1", "AGENT_HOST")
    
    # Handle PEM file
    log(f"Checking for PEM file at: {pem_file}")
    log(f"PEM file exists: {pem_file.exists()}")
    if pem_file.exists() and pem_file.stat().st_size > 0:
        log(f"PEM file already exists at {pem_file}, skipping key input")
    else:
        log("\nPaste the GitHub App private key (PEM), then press Ctrl-D:")
        pem_content = sys.stdin.read()
        pem_file.write_text(pem_content)
        if not pem_content.endswith('\n'):
            with open(pem_file, 'a') as f:
                f.write('\n')
    
    # Write environment file
    env_file.write_text(f"""GITHUB_APP_ID='{github_app_id}'
GITHUB_INSTALLATION_ID='{github_installation_id}'
AGENT_HOST='{agent_host}'
""")
    
    # Authenticate with GitHub
    log("Authenticating as GitHub App...")
    jwt = generate_jwt(github_app_id, str(pem_file))
    token = get_installation_token(jwt, github_installation_id)
    log("Successfully obtained GitHub installation token")
    
    # Clone repositories
    log("Cloning repos...")
    clone_repo(DEPLOY_REPO, str(Path.home() / "robot-deploy"), token)
    clone_repo(DOCS_REPO, str(Path.home() / "robot-docs"), token)
    
    # Prompt for deployment choice
    print('\nDeploy: [s]equencer / [d]ocs / [b]oth? ', end='')
    try:
        choice = input().strip().lower()
    except EOFError:
        choice = 'b'
    
    flag_map = {
        's': '--sequencer',
        'sequencer': '--sequencer',
        'd': '--docs',
        'docs': '--docs',
        'b': '--both',
        'both': '--both'
    }
    
    if choice not in flag_map:
        die(f"Invalid choice: {choice}")
    
    # Execute deploy script
    deploy_script = Path.home() / "robot-deploy" / "deploy.sh"
    os.execv(str(deploy_script), [str(deploy_script), flag_map[choice]])


if __name__ == "__main__":
    main()
