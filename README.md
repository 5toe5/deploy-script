# deploy-script

One-time bootstrap for a fresh device.

## Setup
## Quick Start

### Option 1: Run with uv (recommended)

Create a `.env` file in the same directory as the script:

```bash
GITHUB_APP_ID='your-app-id'
GITHUB_INSTALLATION_ID='your-installation-id'
AGENT_HOST='192.168.1.100'
```

And place your `.github-app.pem` file in the same directory.

Then run:

```bash
uv run --python 3.11 https://raw.githubusercontent.com/5toe5/deploy-script/main/setup-robot-env.py
```

Or if you have the files locally:

```bash
uv run setup-robot-env.py
```

### Option 2: Run locally
You will be prompted for:
- GitHub App ID
- GitHub Installation ID
- Agent host (LAN IP robots use to reach this device; auto-detected by default)
- GitHub App private key (PEM)

The script will then clone the necessary repos and ask whether to deploy the sequencer, docs, or both.

## Configuration

The script looks for configuration in this order:
1. Environment variables (highest priority)
2. `.env` file in the current working directory
3. `.env` file in the script's directory
4. Interactive prompts (fallback)

### Environment Variables

- `GITHUB_APP_ID` - Your GitHub App ID
- `GITHUB_INSTALLATION_ID` - Your GitHub App Installation ID  
- `AGENT_HOST` - LAN IP robots use to reach the agent (default: auto-detected, fallback `127.0.0.1`)
- `PEM_FILE` - Path to your GitHub App private key (default: `~/.robot-env/.github-app.pem` or `./.github-app.pem`)

`GITHUB_APP_ID` and `GITHUB_INSTALLATION_ID` are required. If they are not set in the environment or a local `.env` file, the script will prompt for them.

`AGENT_HOST` is still needed even when the web app and the agent run on the same machine. The sequencer talks to the agent over localhost, but the robot must dial back to the agent over the network for ring-buffer playback, so it needs a reachable LAN IP or hostname.

### Files

- `.env` - Environment variables file
- `.github-app.pem` - GitHub App private key

The script will also look for `.env` and `.github-app.pem` in your current working directory before falling back to the script directory.

## What It Does

1. Authenticates with GitHub using your App credentials
2. Clones the necessary repositories:
    - `5toe5/deploy-script`
    - `5toe5/robot-deploy`
    - `5toe5/robot-docs`
3. Stores configuration in `~/robot-env/.env` and the private key in `~/robot-env/.github-app.pem` by default
4. Prompts for deployment choice: sequencer, docs, or both
5. Runs the deployment script

## Updating

After setup, update the local deployment tooling and pull the latest app release with:

```bash
~/deploy-script/update.sh
```

Or from a local checkout:

```bash
./update.sh
```

`update.sh` is a small wrapper around the Python updater, so the simplest local command remains `./update.sh`.

If you want to run the Python updater directly:

```bash
uv run update-robot-env.py
```

This script:

1. Authenticates with the GitHub App
2. Pulls the latest `deploy-script` and `robot-deploy` repositories
3. Runs `~/robot-deploy/update.sh` to install the latest sequencer release

## Notes

- Existing clone directories are reused only if they are already git repositories.
- The script writes `~/robot-env` with restricted permissions and keeps the PEM and `.env` files private.
- If `PEM_FILE` points somewhere else, its parent directory will be created automatically.
