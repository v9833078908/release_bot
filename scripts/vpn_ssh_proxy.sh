#!/usr/bin/env bash
# SSH ProxyCommand for local production operations. Keys stay on the host.
# Route only the production destination through the shared corporate VPN.
set -euo pipefail

if [ "$#" != 2 ] || [ "$1" != 162.55.137.149 ] || [ "$2" != 1996 ]; then
    echo 'ERROR: VPN proxy accepts only 162.55.137.149:1996.' >&2
    exit 1
fi
command -v docker >/dev/null || { echo 'ERROR: Docker is required for the production VPN.' >&2; exit 1; }
[ "$(docker inspect --format '{{.State.Running}}' hc-vpn-gw)" = true ] || {
    echo 'ERROR: hc-vpn-gw must be running; direct connections are disabled.' >&2
    exit 1
}
[ "$(docker port hc-vpn-gw 1080/tcp)" = '127.0.0.1:11080' ] || {
    echo 'ERROR: hc-vpn-gw SOCKS5 must be bound to 127.0.0.1:11080.' >&2
    exit 1
}

# Do not consume SSH stdin or emit setup output into the SSH byte stream.
# The blackhole survives tunnel deletion, preventing fallback to eth0.
docker exec hc-vpn-gw sh -eu -c '
    ip -4 addr show dev tun0 | grep -q "inet "
    ip link show dev tun0 | grep -Eq "<([^>]*,)?UP(,|>)"
    ip -4 route show 10.39.40.0/24 | grep -Eq "via 10\.0\.0\.1 dev tun0( |$)"
    ip route replace blackhole 162.55.137.149/32 metric 32760
    ip route replace 162.55.137.149/32 via 10.0.0.1 dev tun0 metric 10
    ip route get 162.55.137.149 | grep -Eq "(^|[[:space:]])dev tun0([[:space:]]|$)"
' >&2 || { echo 'ERROR: production VPN route could not be verified; refusing connection.' >&2; exit 1; }

# Use connect-only timeout: -w would terminate quiet long-running deploys.
exec /usr/bin/nc -G 15 -X 5 -x 127.0.0.1:11080 "$1" "$2"
