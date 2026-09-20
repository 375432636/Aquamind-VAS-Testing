#!/usr/bin/env bash
set -euo pipefail

# Bind the checkout so reports and generated audio stay available to artifact
# upload. Use the runner's uid/gid so later steps can read and clean the files.
options=(--rm --init -i --user "$(id -u):$(id -g)"
    --mount "type=bind,src=${GITHUB_WORKSPACE:-$PWD},dst=/app" --workdir /app)

# Pass values at runtime by name: tokens and dialogue text never enter build
# arguments, Docker layers or the shared build cache.
for name in VAS_ENVIRONMENT VAS_DEVICE_ID VAS_TURNS_JSON VAS_INPUT_MODE \
    VAS_DIAGNOSTICS VAS_CLOCK_SYNC VAS_TURN_TIMEOUT_SECONDS VAS_TOKEN VAS_DEV_URL VAS_MAIN_URL \
    VAS_EVALUATION_JSON CI_REPORT_DIR CI_BATCH_REPORT_DIR CI_PERSONA_REPORT_DIR \
    CI_TIMELINE_REPORT_DIR PIPER_DATA_DIR; do
    if [[ ${!name+x} ]]; then
        options+=(--env "$name")
    fi
done

# GitHub's summary file lives outside the checkout. Mount only that file and
# preserve its per-step identity, so container summaries reach the Actions UI.
if [[ -n "${GITHUB_STEP_SUMMARY:-}" ]]; then
    touch "$GITHUB_STEP_SUMMARY"
    options+=(--mount "type=bind,src=$GITHUB_STEP_SUMMARY,dst=/tmp/github-step-summary"
        --env GITHUB_STEP_SUMMARY=/tmp/github-step-summary)
fi

exec docker run "${options[@]}" "${VAS_TEST_IMAGE:-aquamind-vas-testing:local}" "$@"
