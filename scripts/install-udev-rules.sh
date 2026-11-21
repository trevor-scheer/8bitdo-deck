#!/bin/bash
set -euo pipefail

if [[ $(id -u) != 0 ]]; then
    exec pkexec /bin/bash "$(realpath "$0")" "$@"
fi

rule=/etc/udev/rules.d/70-8bitdo-deck.rules
receipt=/etc/udev/rules.d/70-8bitdo-deck.installed
case "${1:-}" in
    '') ;;
    --uninstall)
        rm -f -- "$rule" "$receipt"
        udevadm control --reload-rules
        echo '8BitDo Deck recovery access rules removed. Reconnect receivers to apply.'
        exit 0
        ;;
    *) echo 'Usage: install-udev-rules.sh [--uninstall]' >&2; exit 2 ;;
esac

temporary=$(mktemp)
trap 'rm -f -- "$temporary"' EXIT
cat > "$temporary" <<'RULES'
# 8BitDo Deck: recovery interfaces only; access belongs to the active local user.
SUBSYSTEM=="hidraw", ATTRS{idVendor}=="2dc8", ATTRS{idProduct}=="3208", MODE="0660", TAG+="uaccess"
SUBSYSTEM=="hidraw", ATTRS{idVendor}=="2dc8", ATTRS{idProduct}=="3002", MODE="0660", TAG+="uaccess"
RULES
install -d -m 0755 /etc/udev/rules.d
rm -f -- "$receipt"
install -o root -g root -m 0644 "$temporary" "$rule"
udevadm control --reload-rules

# Trigger only the two recovery personalities, including a receiver already in recovery.
for node in /sys/class/hidraw/hidraw*; do
    [[ -r "$node/device/uevent" ]] || continue
    while IFS='=' read -r key value; do
        [[ "$key" == HID_ID ]] || continue
        case "$value" in
            0003:00002DC8:00003208|0003:00002DC8:00003002)
                udevadm trigger --action=change "$node"
                ;;
        esac
    done < "$node/device/uevent"
done
udevadm settle --timeout=10
sha256sum "$rule" | cut -d' ' -f1 > "$temporary"
install -o root -g root -m 0644 "$temporary" "$receipt"
echo 'Receiver recovery access configured. No firmware was changed.'
