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
uv run --with cryptography --python 3.11 https://raw.githubusercontent.com/5toe5/deploy-script/main/setup-robot-env.py
```

Or if you have the files locally:

```bash
uv run setup-robot-env.py
```

### Option 2: Run with curl (legacy bash script)
You will be prompted for:
- GitHub App ID
- GitHub Installation ID
- Agent host (LAN IP this device is reachable at)
- GitHub App private key (PEM)

The script will then clone the necessary repos and ask whether to deploy the sequencer, docs, or both.

## Configuration

The script looks for configuration in this order:
1. Environment variables (highest priority)
2. `.env` file in the script's directory
3. Interactive prompts (fallback)

### Environment Variables

- `GITHUB_APP_ID` - Your GitHub App ID
- `GITHUB_INSTALLATION_ID` - Your GitHub App Installation ID  
- `AGENT_HOST` - LAN IP this device is reachable at (default: 127.0.0.1)
- `PEM_FILE` - Path to your GitHub App private key (default: `~/.robot-env/.github-app.pem` or `./.github-app.pem`)

### Files

- `.env` - Environment variables file
- `.github-app.pem` - GitHub App private key

## What It Does

1. Authenticates with GitHub using your App credentials
2. Clones the necessary repositories:
   - `5toe5/robot-deploy`
   - `5toe5/robot-docs`
3. Prompts for deployment choice: sequencer, docs, or both
4. Runs the deployment script
