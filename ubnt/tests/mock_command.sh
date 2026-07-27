#!/bin/sh
set -eu

command_name=${0##*/}
case $command_name in
    iwlist)
        selected_fixture=$MOCK_FIXTURE
        if [ -n "${MOCK_IWLIST_COUNT_FILE:-}" ]; then
            invocation_count=$(sed -n '1p' "$MOCK_IWLIST_COUNT_FILE" 2>/dev/null || true)
            case $invocation_count in
                ''|*[!0-9]*) invocation_count=0 ;;
            esac
            invocation_count=$((invocation_count + 1))
            printf '%s\n' "$invocation_count" > "$MOCK_IWLIST_COUNT_FILE"
            if [ "$invocation_count" -eq 1 ] && [ -n "${MOCK_FIXTURE_FIRST:-}" ]; then
                selected_fixture=$MOCK_FIXTURE_FIRST
            fi
        fi
        cat "$selected_fixture"
        ;;
    iwgetid)
        cat "$MOCK_ASSOCIATED"
        ;;
    mca-status)
        printf 'ccq=%s\n' "${MOCK_CCQ:-900}"
        ;;
    ip)
        case " $* " in
            *' addr '*) printf '    inet 192.0.2.2/24 scope global ath0\n' ;;
            *' route '*) printf 'default via 192.0.2.1 dev ath0\n' ;;
        esac
        ;;
    ping)
        exit "${MOCK_PING_STATUS:-0}"
        ;;
    softrestart)
        printf 'aaa.1.wpa.psk=sensitive-test-value\n'
        if [ -n "${UBNT_AUTHORIZED_KEYS:-}" ]; then
            printf '%s\n' 'admin-key' > "$UBNT_AUTHORIZED_KEYS"
        fi
        target=$(awk -F= '
            $1 == "wpasupplicant.status" && $2 == "enabled" { wpa = 1 }
            $1 == "wpasupplicant.profile.1.network.1.ssid" { wpa_ssid = $2 }
            $1 == "wireless.1.ssid" { wireless_ssid = $2 }
            END { if (wpa) print wpa_ssid; else print wireless_ssid }
        ' "$UBNT_SYSTEM_CFG")
        if [ "$target" != "${MOCK_FAIL_SSID:-}" ]; then
            printf '%s\n' "$target" > "$MOCK_ASSOCIATED"
        fi
        ;;
    cfgmtd)
        :
        ;;
    *)
        printf 'Unknown mock command: %s\n' "$command_name" >&2
        exit 1
        ;;
esac
