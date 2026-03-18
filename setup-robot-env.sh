#!/usr/bin/env bash
# setup-robot-env.sh — one-time bootstrap for a fresh device.
# Host this publicly so a new machine can: curl -fsSL <url> | bash
set -euo pipefail

DEPLOY_REPO="5toe5/robot-deploy"
DOCS_REPO="5toe5/robot-docs"

log() { printf '[setup] %s\n' "$*"; }
die() { printf '[setup] ERROR: %s\n' "$*" >&2; exit 1; }

prompt() {
    local label=$1 default=${2-} value
    if [[ -n "$default" ]]; then
        read -r -p "$label [$default]: " value < /dev/tty
        printf '%s' "${value:-$default}"
    else
        read -r -p "$label: " value < /dev/tty
        printf '%s' "$value"
    fi
}

require_cmd() { command -v "$1" &>/dev/null || die "'$1' is not installed"; }

b64url() { openssl base64 -A | tr '+/' '-_' | tr -d '='; }

github_app_jwt() {
    local now iat exp header payload sig
    now=$(date +%s)
    iat=$((now - 60))
    exp=$((now + 540))
    header=$(printf '{"alg":"RS256","typ":"JWT"}' | b64url)
    payload=$(printf '{"iat":%s,"exp":%s,"iss":"%s"}' "$iat" "$exp" "$GITHUB_APP_ID" | b64url)
    sig=$(printf '%s.%s' "$header" "$payload" | openssl dgst -sha256 -sign "$PEM_FILE" | b64url)
    printf '%s.%s.%s' "$header" "$payload" "$sig"
}

installation_token() {
    curl -fsSL \
        -X POST \
        -H "Authorization: Bearer ${1}" \
        -H "Accept: application/vnd.github+json" \
        -H "X-GitHub-Api-Version: 2022-11-28" \
        "https://api.github.com/app/installations/${GITHUB_INSTALLATION_ID}/access_tokens" \
        | grep -o '"token":"[^"]*"' | cut -d'"' -f4
}

clone_repo() {
    local repo=$1 dest=$2 token=$3
    if [[ -d "$dest" ]]; then
        log "$dest already exists, skipping clone"
        return
    fi
    git clone "https://x-access-token:${token}@github.com/${repo}.git" "$dest"
    git -C "$dest" remote set-url origin "https://github.com/${repo}.git"
}

main() {
    require_cmd curl
    require_cmd openssl
    require_cmd git

    local env_dir="$HOME/robot-env"
    local env_file="$env_dir/.env"
    PEM_FILE="$env_dir/.github-app.pem"

    umask 077
    mkdir -p "$env_dir"

    GITHUB_APP_ID=$(prompt 'GitHub App ID')
    GITHUB_INSTALLATION_ID=$(prompt 'GitHub Installation ID')
    local agent_host
    agent_host=$(prompt 'Agent host (LAN IP this device is reachable at)' '127.0.0.1')

    printf '\nPaste the GitHub App private key (PEM), then press Ctrl-D:\n' > /dev/tty
    cat < /dev/tty > "$PEM_FILE"

    cat > "$env_file" <<EOF
GITHUB_APP_ID='${GITHUB_APP_ID}'
GITHUB_INSTALLATION_ID='${GITHUB_INSTALLATION_ID}'
AGENT_HOST='${agent_host}'
EOF

    log "Authenticating as GitHub App..."
    local jwt token
    jwt=$(github_app_jwt)
    token=$(installation_token "$jwt")
    [[ -n "$token" ]] || die "Failed to obtain GitHub installation access token"

    log "Cloning repos..."
    clone_repo "$DEPLOY_REPO" "$HOME/robot-deploy" "$token"
    clone_repo "$DOCS_REPO"   "$HOME/robot-docs"   "$token"

    printf '\nDeploy: [s]equencer / [d]ocs / [b]oth? '
    read -r choice < /dev/tty
    local flag
    case "$choice" in
        s|sequencer) flag="--sequencer" ;;
        d|docs)      flag="--docs" ;;
        b|both)      flag="--both" ;;
        *) die "Invalid choice: $choice" ;;
    esac

    exec "$HOME/robot-deploy/deploy.sh" "$flag"
}

main "$@"
