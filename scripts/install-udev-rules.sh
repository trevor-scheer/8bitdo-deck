#!/bin/bash
set -euo pipefail

script_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
exec /bin/bash "$script_root/eightbitdo/resources/install-udev-rules.sh" "$@"
