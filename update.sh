#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOME_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
ENV_FILE="$HOME_DIR/robot-env/.env"
PEM_FILE="$HOME_DIR/robot-env/.github-app.pem"
ROBOT_DEPLOY_DIR="$HOME_DIR/robot-deploy"

DEPLOY_SCRIPT_REPO="5toe5/deploy-script"
ROBOT_DEPLOY_REPO="5toe5/robot-deploy"

log() { printf '[update] %s\n' "$*"; }
die() {
	printf '[update] ERROR: %s\n' "$*" >&2
	exit 1
}

require_cmd() { command -v "$1" >/dev/null 2>&1 || die "'$1' is not installed"; }

load_env() {
	local line key value
	log "Loading environment from $1"
	while IFS= read -r line || [[ -n "$line" ]]; do
		line=${line%$'\r'}
		line="${line#${line%%[![:space:]]*}}"
		[[ "$line" =~ ^[[:space:]]*# ]] && continue
		[[ -z "${line//[[:space:]]/}" ]] && continue
		if [[ "$line" =~ ^export[[:space:]]+ ]]; then
			line=${line#export}
			line="${line#${line%%[![:space:]]*}}"
		fi
		key="${line%%=*}"
		value="${line#*=}"
		key="${key#${key%%[![:space:]]*}}"
		key="${key%${key##*[![:space:]]}}"
		value="${value#${value%%[![:space:]]*}}"
		value="${value%${value##*[![:space:]]}}"
		value="${value#\'}"
		value="${value%\'}"
		value="${value#\"}"
		value="${value%\"}"
		declare -x "$key=$value"
	done <"$1"
}

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
		-H "Authorization: Bearer $1" \
		-H "Accept: application/vnd.github+json" \
		-H "X-GitHub-Api-Version: 2022-11-28" \
		"https://api.github.com/app/installations/${GITHUB_INSTALLATION_ID}/access_tokens" |
		jq -r '.token'
}

acquire_token() {
	log "Authenticating as GitHub App..."
	local jwt token
	jwt=$(github_app_jwt)
	token=$(installation_token "$jwt")
	[[ -n "$token" && "$token" != "null" ]] || die "Failed to obtain GitHub installation access token"
	printf '%s' "$token"
}

ensure_clean_repo() {
	local repo_dir=$1
	[[ -d "$repo_dir/.git" ]] || die "Missing git repository at $repo_dir — run setup-robot-env.py first"
	if [[ -n "$(git -C "$repo_dir" status --short)" ]]; then
		die "Repository has local changes: $repo_dir"
	fi
}

pull_repo() {
	local repo_dir=$1
	local repo_name=$2
	local token=$3
	ensure_clean_repo "$repo_dir"
	log "Pulling latest $repo_name..."
	git -C "$repo_dir" \
		-c "url.https://x-access-token:${token}@github.com/.insteadOf=https://github.com/" \
		pull --ff-only
}

require_cmd curl
require_cmd git
require_cmd jq
require_cmd openssl

[[ -f "$ENV_FILE" ]] || die "Missing $ENV_FILE — run setup-robot-env.py first"
[[ -f "$PEM_FILE" ]] || die "Missing $PEM_FILE — run setup-robot-env.py first"
[[ -d "$ROBOT_DEPLOY_DIR" ]] || die "Missing $ROBOT_DEPLOY_DIR — run setup-robot-env.py first"

load_env "$ENV_FILE"

[[ -n "${GITHUB_APP_ID:-}" ]] || die "GITHUB_APP_ID not set in $ENV_FILE"
[[ -n "${GITHUB_INSTALLATION_ID:-}" ]] || die "GITHUB_INSTALLATION_ID not set in $ENV_FILE"

TOKEN=$(acquire_token)
pull_repo "$SCRIPT_DIR" "$DEPLOY_SCRIPT_REPO" "$TOKEN"
pull_repo "$ROBOT_DEPLOY_DIR" "$ROBOT_DEPLOY_REPO" "$TOKEN"

log "Running robot-deploy update..."
exec "$ROBOT_DEPLOY_DIR/update.sh"
