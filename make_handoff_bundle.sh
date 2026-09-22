#!/bin/zsh
set -euo pipefail

SCRIPT_DIR=${0:A:h}
session_dir=""
output_path=""

usage() {
  cat <<'USAGE'
Usage:
  ./make_handoff_bundle.sh --session-dir <session-directory> --output <bundle.tar.gz>

The bundle contains DATA_PROTOCOL.md, export_watchhar.py, a README, SHA256SUMS,
and a copy of the selected raw session directory.
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --session-dir)
      [[ $# -ge 2 ]] || { echo "missing value for --session-dir" >&2; exit 2; }
      session_dir="$2"
      shift 2
      ;;
    --output)
      [[ $# -ge 2 ]] || { echo "missing value for --output" >&2; exit 2; }
      output_path="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [[ -z "$session_dir" || -z "$output_path" ]]; then
  usage >&2
  exit 2
fi

session_dir=${session_dir:A}
output_path=${output_path:A}
[[ -d "$session_dir" ]] || { echo "session directory not found: $session_dir" >&2; exit 1; }
[[ -f "$session_dir/session-info.json" ]] || { echo "session-info.json not found in $session_dir" >&2; exit 1; }
[[ -f "$SCRIPT_DIR/DATA_PROTOCOL.md" ]] || { echo "DATA_PROTOCOL.md not found" >&2; exit 1; }
[[ -f "$SCRIPT_DIR/export_watchhar.py" ]] || { echo "export_watchhar.py not found" >&2; exit 1; }

session_name=${session_dir:t}
bundle_name="sensor_read_handoff_${session_name}"
staging_parent=$(mktemp -d -t sensor_read_handoff)
staging="$staging_parent/$bundle_name"
cleanup() { rm -rf "$staging_parent"; }
trap cleanup EXIT

mkdir -p "$staging/raw_session/$session_name"
cp "$SCRIPT_DIR/DATA_PROTOCOL.md" "$staging/DATA_PROTOCOL.md"
cp "$SCRIPT_DIR/export_watchhar.py" "$staging/export_watchhar.py"
cp -R "$session_dir"/* "$staging/raw_session/$session_name/"

cat > "$staging/BUNDLE_README.md" <<EOF
# Sensor Read handoff bundle

This bundle contains the raw session \`$session_name\`, the acquisition/data protocol,
and the WatchHAR export bridge. Read \`DATA_PROTOCOL.md\` before processing.

Typical command:

\`\`\`bash
conda run --no-capture-output -n mobileposer python export_watchhar.py \\
  --events raw_session/$session_name/apple_watch-events-<sessionID>.ndjson \\
  --source apple_watch --participant 1 --context Kitchen \\
  --activity Chopping --trial 1 --output-dir watchhar_raw
\`\`\`

The raw session files are copied byte-for-byte from the source directory. The
export command performs alignment/resampling only in the generated output.
EOF

(cd "$staging" && find . -type f ! -path './SHA256SUMS' -print0 | sort -z | xargs -0 shasum -a 256 > SHA256SUMS)
mkdir -p "${output_path:h}"
tar -czf "$output_path" -C "$staging_parent" "$bundle_name"

echo "Created: $output_path"
echo "Session: $session_name"
echo "Size: $(du -h "$output_path" | awk '{print $1}')"
