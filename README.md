# deploy-script

One-time bootstrap for a fresh device.

## Setup

```bash
curl -fsSL https://raw.githubusercontent.com/5toe5/deploy-script/main/setup-robot-env.sh | bash
```

You will be prompted for:
- GitHub App ID
- GitHub Installation ID
- Agent host (LAN IP this device is reachable at)
- GitHub App private key (PEM)

The script will then clone the necessary repos and ask whether to deploy the sequencer, docs, or both.
