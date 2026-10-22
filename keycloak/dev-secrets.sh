#!/usr/bin/env bash
# P25a: the development Keycloak's secrets, made once into var/keycloak/dev.env (git-ignored,
# owner-only), the way var/oidc.key was made in DEBUG. Nothing here is used outside a laptop:
# production takes every one of these from the server's environment (notes).
#
# The portal reads the two client secrets from this file in DEBUG when its own environment does
# not set them (portal/core/keycloak/config.py); Compose and keycloak-config-cli read the rest.
# The drafting tool's client secret (P25b) is the fork's SCLU_KEYCLOAK_CLIENT_SECRET in development.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
out="${here}/../var/keycloak/dev.env"
mkdir -p "$(dirname "${out}")"
secret() { openssl rand -hex 24; }
umask 077
if [[ -s "${out}" ]]; then
  # P25b: a secret added after the file was made is appended once; the others stay as they are.
  if ! grep -q '^KEYCLOAK_DRAFTING_CLIENT_SECRET=' "${out}"; then
    echo "KEYCLOAK_DRAFTING_CLIENT_SECRET=$(secret)" >> "${out}"
    echo "keycloak: the drafting tool's client secret added to var/keycloak/dev.env"
  fi
  exit 0
fi
cat > "${out}" <<EOF
# Made by keycloak/dev-secrets.sh on $(date -u +%Y-%m-%dT%H:%M:%SZ). Development only.
KEYCLOAK_DB_PASSWORD=$(secret)
KC_BOOTSTRAP_ADMIN_USERNAME=admin
KC_BOOTSTRAP_ADMIN_PASSWORD=$(secret)
KC_BOOTSTRAP_ADMIN_CLIENT_ID=sclu-config
KC_BOOTSTRAP_ADMIN_CLIENT_SECRET=$(secret)
KEYCLOAK_PORTAL_CLIENT_SECRET=$(secret)
KEYCLOAK_ADMIN_CLIENT_SECRET=$(secret)
KEYCLOAK_DRAFTING_CLIENT_SECRET=$(secret)
EOF
chmod 600 "${out}"
echo "keycloak: development secrets written to var/keycloak/dev.env"
