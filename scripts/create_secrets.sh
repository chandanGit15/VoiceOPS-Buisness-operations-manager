#!/usr/bin/env bash
# Create VoiceOps secrets in Google Cloud Secret Manager (Part 9).
#
# NEVER put real key values in git. Run this once locally; the deploy
# workflow then mounts them via secretKeyRef. Requires gcloud auth.
#
# Usage:
#   export PROJECT_ID=your-project
#   export REGION=us-central1
#   ./scripts/create_secrets.sh
set -euo pipefail

read_secret() {
  local name="$1"
  local prompt="$2"
  read -rsp "$prompt" value
  echo
  printf '%s' "$value" | gcloud secrets create "$name" \
    --project="${PROJECT_ID}" \
    --data-file=- \
    --replication-policy="user-managed" \
    --locations="${REGION}"
}

for name in voiceops-gemini-api-key voiceops-app-secret-key voiceops-token-encryption-key voiceops-postgres-password voiceops-database-url voiceops-redis-url; do
  if gcloud secrets describe "$name" --project="${PROJECT_ID}" >/dev/null 2>&1; then
    echo "✓ exists: $name (skipped)"
  else
    case "$name" in
      voiceops-gemini-api-key)       read_secret "$name" "Gemini API key: " ;;
      voiceops-app-secret-key)       echo "APP_SECRET_KEY (blank = generate hex token):"
                                     read -rsp "value (empty to auto-generate): " value; echo
                                     [[ -z "$value" ]] && value=$(python -c 'import secrets; print(secrets.token_hex(32))')
                                     printf '%s' "$value" | gcloud secrets create "$name" --project="${PROJECT_ID}" --data-file=- ;;
      voiceops-token-encryption-key) echo "TOKEN_ENCRYPTION_KEY (blank = generate Fernet key):"
                                     read -rsp "value (empty to auto-generate): " value; echo
                                     [[ -z "$value" ]] && value=$(python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')
                                     printf '%s' "$value" | gcloud secrets create "$name" --project="${PROJECT_ID}" --data-file=- ;;
      *)                             read_secret "$name" "$name: " ;;
    esac
    echo "✓ created: $name"
  fi
done

echo
echo "All secrets ready. Now grant the runtime service account access:"
echo "  gcloud secrets add-iam-policy-binding <SECRET> \\"
echo "    --member='serviceAccount:voiceops-runtime@${PROJECT_ID}.iam.gserviceaccount.com' \\"
echo "    --role='roles/secretmanager.secretAccessor'"
