#!/usr/bin/env bash
# Copied from Decision Agent Business' Jev attachment on 2026-09-21.
# Its original job is to route a Keychain-backed classifier call into the
# George engineering repository. NFL Fantasy uses the portable typed contract
# in prediction_events.py instead.
set -euo pipefail

readonly KEYCHAIN_SERVICE="decision-agent.openrouter-api-key"
readonly KEYCHAIN_ACCOUNT="decisionagent"

usage() { printf '%s\n' "usage: jev-test-classifier --repo PATH --base SHA --head SHA --output PATH" >&2; }
fail() { printf 'jev-test-classifier: %s\n' "$1" >&2; exit 64; }

if [[ "${1:-}" == "--help" ]]; then usage; exit 0; fi
repo=''; base=''; head=''; output=''
while [[ $# -gt 0 ]]; do
  case "$1" in
    --repo|--base|--head|--output)
      [[ $# -ge 2 && -n "$2" && "$2" != --* ]] || fail "$1 requires a value"
      case "$1" in
        --repo) [[ -z "$repo" ]] || fail "duplicate --repo"; repo=$2 ;;
        --base) [[ -z "$base" ]] || fail "duplicate --base"; base=$2 ;;
        --head) [[ -z "$head" ]] || fail "duplicate --head"; head=$2 ;;
        --output) [[ -z "$output" ]] || fail "duplicate --output"; output=$2 ;;
      esac
      shift 2 ;;
    *) usage; fail "unsupported argument; this helper accepts classifier inputs only" ;;
  esac
done
[[ "$repo" = /* && -d "$repo" ]] || fail "--repo must be an absolute George worktree"
[[ -x "$repo/scripts/ci/jev-test-classifier.sh" ]] || fail "the George classifier entrypoint is unavailable"
[[ "$base" =~ ^[a-fA-F0-9]{40}$ ]] || fail "--base must be a 40-character commit SHA"
[[ "$head" =~ ^[a-fA-F0-9]{40}$ ]] || fail "--head must be a 40-character commit SHA"
[[ "$output" = /* ]] || fail "--output must be an absolute path"
[[ -d "$(dirname "$output")" ]] || fail "the --output parent directory is unavailable"
openrouter_key=$(/usr/bin/security find-generic-password -w -a "$KEYCHAIN_ACCOUNT" -s "$KEYCHAIN_SERVICE" 2>/dev/null) || fail "the configured Keychain item is unavailable"
[[ -n "$openrouter_key" ]] || fail "the configured Keychain item is empty"
export OPENROUTER_API_KEY="$openrouter_key"
unset openrouter_key
exec /bin/bash "$repo/scripts/ci/jev-test-classifier.sh" --repo "$repo" --base "$base" --head "$head" --output "$output"
