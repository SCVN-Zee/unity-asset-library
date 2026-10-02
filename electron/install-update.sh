#!/bin/sh
# Arguments come from the main process, never release metadata or shell interpolation.
set -eu
pid=$1
target=$2
workspace=$3
log=$4
backup="$workspace/previous.app"
staged="$workspace/Unity Asset Library.app"
exec >>"$log" 2>&1
rollback() {
  status=$?
  trap - EXIT HUP INT TERM
  if [ "$status" -ne 0 ]; then
    echo "Update failed; restoring previous application."
    if [ -d "$backup" ]; then
      if [ -e "$target" ]; then /bin/mv "$target" "$workspace/failed.app"; fi
      /bin/mv "$backup" "$target"
    fi
    if ! /bin/kill -0 "$pid" 2>/dev/null; then /usr/bin/open -n "$target" || true; fi
  fi
  exit "$status"
}
trap rollback EXIT
trap 'exit 1' HUP INT TERM
# Do not touch the bundle until the application's graceful shutdown has finished.
echo "Waiting for application to exit."
i=0
while /bin/kill -0 "$pid" 2>/dev/null; do
  i=$((i + 1))
  if [ "$i" -ge 120 ]; then echo "Application did not exit; update cancelled."; exit 1; fi
  /bin/sleep 1
done
/usr/bin/codesign --verify --deep --strict "$staged"
/bin/mv "$target" "$backup"
/bin/mv "$staged" "$target"
/usr/bin/open -n "$target"
echo "Update installed."
# Retain previous.app for manual recovery after a successful launch.
