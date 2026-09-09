#!/bin/bash
cd "$(dirname "$0")" || exit 1
/bin/bash ./start.sh "$@"
task_status=$?
if [ "$task_status" -ne 0 ]; then
    printf '\nStartup failed. Press Return to close this window...'
    read -r task_reply
fi
exit "$task_status"
