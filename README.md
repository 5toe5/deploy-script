# Granforge fresh-machine bootstrap

This public repository is the sole fresh-machine bootstrap for Granforge. It
installs prerequisites with approval, stores deployment-only GitHub App
credentials, clones the private `5toe5/robot-deploy` repository, and hands off
release installation. Runtime configuration and services are owned by
`robot-deploy`.

## Safe fresh-machine flow

Install system Python 3, clone this repository over HTTPS, review it, and run the
checked-out script locally:

```bash
git clone https://github.com/5toe5/deploy-script.git
cd deploy-script
git pull --ff-only
sudo python3 setup-robot-env.py
```

Do not use curl-pipe commands. No `uv` or third-party Python package is needed.

The interactive bootstrap:

1. detects Debian, Ubuntu, Raspberry Pi OS, or Arch;
2. shows the exact package-manager command and asks before installing missing
   `git`, `openssl`, or `systemd` tools required by the current deployer;
3. asks for a read-only GitHub App ID, installation ID, and PEM path;
4. suggests a detected LAN IP for `MOTION_AGENT_AGENT_HOST` and requires you to
   confirm a concrete IP address; and
5. accepts an explicit SemVer tag such as `vX.Y.Z`, `vX.Y.Z-rc.1`, or
   `vX.Y.Z-rc.1+build.5`, or lets you leave it blank so `robot-deploy` can offer
   a tag or latest release interactively.

Hostnames are not accepted. Loopback IPs, including IPv4-mapped IPv6 loopback,
are rejected unless `--simulator-only` is explicitly supplied. Unspecified and
multicast addresses, including IPv4-mapped forms, are always rejected.

## Unattended bootstrap

All prerequisites must already be installed. Supply every value and an explicit
SemVer tag:

```bash
sudo python3 setup-robot-env.py \
  --non-interactive \
  --github-app-id 12345 \
  --github-installation-id 67890 \
  --pem-file /secure/input/granforge-read-only.pem \
  --motion-agent-agent-host 192.0.2.10 \
  --version v1.2.3
```

The current deployer also accepts a local release bundle and adjacent checksum:

```bash
sudo python3 setup-robot-env.py [credential and host options] \
  --version v1.2.3 --bundle ./granforge-linux-amd64.tar.gz
```

The bootstrap writes root-only files:

- `/etc/granforge/deploy.env` — App/installation IDs and the initial
  `MOTION_AGENT_AGENT_HOST` for first install;
- `/etc/granforge/github-app.pem` — the private key; and
- `/opt/granforge/robot-deploy` — private deployment tooling with a sanitized
  public-form remote URL.

The short-lived installation token is never written to disk. Deployment
credentials are authenticated before replacing an existing credential pair and
are not forwarded in the runtime process environment.

## Updating

Run from the public checkout:

```bash
sudo ./update.sh --version v1.2.4
sudo ./update.sh --version v1.2.4 --bundle ./granforge-linux-amd64.tar.gz
```

This requires clean public and private repositories, pulls both with
`--ff-only`, re-executes the public updater once only when that pull changes its
HEAD, and forwards `--version` and optional `--bundle` to
`/opt/granforge/robot-deploy/update.sh`. With a terminal, omitting `--version`
allows `robot-deploy` to offer tag/latest. Without a terminal, an explicit
version is mandatory; latest is never selected implicitly.

When invoked with `sudo`, the updater validates `SUDO_UID`/`SUDO_GID` against
the public checkout owner and marks only that exact checkout as a command-scoped
Git `safe.directory`; it never changes global Git configuration.

## Tests

```bash
make test
```

Tests use temporary roots and command/token seams; they do not run sudo, package
managers, network calls, or touch real `/etc` or `/opt`.
