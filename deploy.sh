#!/usr/bin/env bash
# Wdrożenie "Domowego bilansu" na Google Cloud Run z danymi w buckecie Cloud Storage.
# Użycie:  PROJECT_ID=moj-projekt ./deploy.sh
set -euo pipefail

PROJECT_ID="${PROJECT_ID:-$(gcloud config get-value project 2>/dev/null)}"
REGION="${REGION:-europe-central2}"          # Warszawa
SERVICE="${SERVICE:-domowy-bilans}"
BUCKET="${BUCKET:-${PROJECT_ID}-domowy-bilans}"

if [[ -z "${PROJECT_ID}" ]]; then
  echo "Ustaw PROJECT_ID (np. PROJECT_ID=moj-projekt ./deploy.sh)" >&2
  exit 1
fi
echo "Projekt: ${PROJECT_ID} | region: ${REGION} | usługa: ${SERVICE} | bucket: gs://${BUCKET}"
gcloud config set project "${PROJECT_ID}" >/dev/null

echo "==> Włączam potrzebne API"
gcloud services enable run.googleapis.com cloudbuild.googleapis.com \
  artifactregistry.googleapis.com storage.googleapis.com

echo "==> Bucket na dane"
if ! gcloud storage buckets describe "gs://${BUCKET}" >/dev/null 2>&1; then
  gcloud storage buckets create "gs://${BUCKET}" --location="${REGION}" \
    --uniform-bucket-level-access
fi

echo "==> Uprawnienia konta usługi Cloud Run do bucketu"
PROJECT_NUMBER="$(gcloud projects describe "${PROJECT_ID}" --format='value(projectNumber)')"
RUN_SA="${RUN_SA:-${PROJECT_NUMBER}-compute@developer.gserviceaccount.com}"
gcloud storage buckets add-iam-policy-binding "gs://${BUCKET}" \
  --member="serviceAccount:${RUN_SA}" --role="roles/storage.objectUser" >/dev/null

echo "==> Budowanie i wdrażanie (kilka minut przy pierwszym razie)"
gcloud run deploy "${SERVICE}" \
  --source . \
  --region="${REGION}" \
  --service-account="${RUN_SA}" \
  --allow-unauthenticated \
  --min-instances=0 \
  --max-instances=1 \
  --concurrency=50 \
  --session-affinity \
  --timeout=3600 \
  --cpu=1 \
  --memory=512Mi \
  --add-volume="name=dane,type=cloud-storage,bucket=${BUCKET}" \
  --add-volume-mount="volume=dane,mount-path=/data" \
  --set-env-vars="DATA_DIR=/data,DATA_PERSISTENT=1,APP_TZ=Europe/Warsaw"

echo
echo "Gotowe! Adres aplikacji:"
gcloud run services describe "${SERVICE}" --region="${REGION}" --format='value(status.url)'
