#!/usr/bin/env bash
# Generate the BORICA signing key pair and certificate request.
#
#   ./make-borica-csr.sh V1234567 T          # test terminal
#   ./make-borica-csr.sh V7654321 P          # production terminal
#
# Run this ON THE MACHINE THAT WILL HOLD THE KEY, as the user the web server
# runs as. The private key never leaves that machine: not by e-mail, not through
# a chat window, not into the Merchant Portal (which refuses key uploads by
# design). Only the .zip containing the .csr is uploaded.
#
# You will be prompted for a passphrase. Choose one and keep it in the same
# place as the other box credentials — the application reads it from the
# environment at startup, so an unattended restart needs it available there.
#
# Spec: P-OM-41 v7.0 §5.2–5.6.
set -euo pipefail

TID="${1:-}"
ENV_KIND="${2:-}"

usage() { echo "usage: $0 <TID e.g. V1234567> <T|P>" >&2; exit 2; }
[ -n "$TID" ] && [ -n "$ENV_KIND" ] || usage
[[ "$TID" =~ ^V[0-9]{7}$ ]] || { echo "TID must look like V1234567 (8 chars)" >&2; exit 2; }
[[ "$ENV_KIND" =~ ^[TP]$ ]] || { echo "environment must be T (test) or P (production)" >&2; exit 2; }

# ---------------------------------------------------------------- identity
# Latin only, no special symbols — BORICA rejects quotes and Cyrillic here.
# CN is the bare domain: no scheme, no path, no trailing dot.
CN="${BORICA_CN:-darlinghome.bg}"
ORG="${BORICA_ORG:-Darling 7 OOD}"
LOCALITY="${BORICA_LOCALITY:-Svishtov}"
STATE="${BORICA_STATE:-Veliko Tarnovo}"
COUNTRY="BG"
EMAIL="${BORICA_EMAIL:-orders@darlinghome.bg}"

DATE="$(date -u +%Y%m%d)"
BASE="${TID}_${DATE}_${ENV_KIND}"
OUTDIR="${BORICA_OUTDIR:-$PWD/borica-${ENV_KIND}}"
KEY="${OUTDIR}/${TID}_${ENV_KIND}.key"
CSR="${OUTDIR}/${BASE}.csr"
ZIP="${OUTDIR}/${BASE}.zip"

command -v openssl >/dev/null || { echo "openssl not found" >&2; exit 1; }
mkdir -p "$OUTDIR"
chmod 700 "$OUTDIR"

if [ -e "$KEY" ]; then
  echo "REFUSING: $KEY already exists." >&2
  echo "Overwriting it would orphan any certificate BORICA has already signed." >&2
  echo "Move the old key aside deliberately if you really mean to re-key." >&2
  exit 1
fi

echo "Terminal    : $TID  (${ENV_KIND} = $([ "$ENV_KIND" = T ] && echo test || echo PRODUCTION))"
echo "Common name : $CN"
echo "Organisation: $ORG"
echo "Location    : $LOCALITY, $STATE, $COUNTRY"
echo "E-mail      : $EMAIL"
echo "Output      : $OUTDIR"
echo

# ---------------------------------------------------------------- key + CSR
# 2048-bit RSA, AES-256 encrypted at rest. openssl prompts for the passphrase
# on the terminal so it never reaches the shell history or the process table.
umask 077
openssl genrsa -aes256 -out "$KEY" 2048

SUBJ="/CN=${CN}/OU=${TID}/O=${ORG}/L=${LOCALITY}/ST=${STATE}/C=${COUNTRY}/emailAddress=${EMAIL}"
openssl req -new -key "$KEY" -out "$CSR" -subj "$SUBJ" -sha256

chmod 600 "$KEY"

if command -v zip >/dev/null; then
  ( cd "$OUTDIR" && zip -q "$(basename "$ZIP")" "$(basename "$CSR")" )
else
  echo "NOTE: zip is not installed — archive ${CSR##*/} yourself as ${ZIP##*/} before uploading." >&2
fi

# ---------------------------------------------------------------- verify
echo
echo "--- CSR subject as BORICA will read it -------------------------------"
openssl req -in "$CSR" -noout -subject -verify 2>&1 | sed 's/^/  /'
echo "--- key fingerprint (must match the .cer they send back) -------------"
echo -n "  key modulus md5: "
openssl rsa -noout -modulus -in "$KEY" | openssl md5

cat <<EOF

Done.

  private key  $KEY      <-- stays here, chmod 600, never leaves this machine
  request      $CSR
  upload       $ZIP

Next:
  1. Upload the .zip in the Merchant Portal:
       T -> https://3dsgate-dev.borica.bg/mwp_cert
       P -> https://3dsgate.borica.bg/mwp/static/
  2. When BORICA returns ${BASE}.cer, confirm it belongs to this key:
       openssl x509 -noout -modulus -in ${BASE}.cer | openssl md5
     The md5 printed above must match exactly. If it does not, the certificate
     is for a different key and every request will come back RC -17.
  3. Put the key path and its passphrase in the box environment. Do not paste
     either into a chat, a ticket, or a commit.

Reminder: generate a fresh key whenever the certificate is renewed (§5.1), and
never reuse the test key in production.
EOF
