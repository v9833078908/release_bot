#!/usr/bin/env bash
# Hermetic transport tests: no real Docker, SSH or network calls.
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
[ -f "$ROOT/scripts/vpn_ssh_proxy.sh" ] || { echo 'FAIL: VPN helper missing' >&2; exit 1; }
WORK=$(mktemp -d "${TMPDIR:-/tmp}/gp_vpn_proxy.XXXXXX")
trap 'rm -rf "$WORK"' EXIT
mkdir -p "$WORK/bin" "$WORK/repo with spaces/scripts"
export VPN_TEST_STATE="$WORK"
export PATH="$WORK/bin:$PATH"
# Replace only the fixed macOS executable in this isolated fixture.
sed "s|/usr/bin/nc|$WORK/bin/nc|g" "$ROOT/scripts/vpn_ssh_proxy.sh" > "$WORK/repo with spaces/scripts/vpn_ssh_proxy.sh"
HELPER="$WORK/repo with spaces/scripts/vpn_ssh_proxy.sh"
cat > "$WORK/bin/docker" <<'EOF'
#!/usr/bin/env bash
printf 'docker %s\n' "$*" >> "$VPN_TEST_STATE/calls"
case "$1" in
  inspect) [ "$VPN_TEST_CASE" != stopped ] && echo true || echo false ;;
  port) [ "$VPN_TEST_CASE" != wrong_port ] && echo '127.0.0.1:11080' || echo '0.0.0.0:11080' ;;
  exec) shift 2; exec "$@" ;;
  *) exit 99 ;;
esac
EOF
cat > "$WORK/bin/ip" <<'EOF'
#!/usr/bin/env bash
printf 'ip %s\n' "$*" >> "$VPN_TEST_STATE/calls"
case "$*" in
  '-4 addr show dev tun0') [ "$VPN_TEST_CASE" = missing_tunnel ] || echo 'inet 10.0.0.127/24' ;;
  'link show dev tun0')
    [ "$VPN_TEST_CASE" = down_tunnel ] && echo '17: tun0: <POINTOPOINT> mtu 1100' || echo '17: tun0: <POINTOPOINT,UP,LOWER_UP> mtu 1100' ;;
  '-4 route show 10.39.40.0/24')
    [ "$VPN_TEST_CASE" = wrong_peer ] && echo '10.39.40.0/24 via 10.0.0.2 dev tun0' || echo '10.39.40.0/24 via 10.0.0.1 dev tun0' ;;
  'route replace blackhole 162.55.137.149/32 metric 32760') [ "$VPN_TEST_CASE" != blackhole_failure ] ;;
  'route replace 162.55.137.149/32 via 10.0.0.1 dev tun0 metric 10') [ "$VPN_TEST_CASE" != route_failure ] ;;
  'route get 162.55.137.149')
    [ "$VPN_TEST_CASE" = wrong_route ] && echo '162.55.137.149 via 172.17.0.1 dev eth0' || echo '162.55.137.149 via 10.0.0.1 dev tun0 src 10.0.0.127' ;;
  *) exit 98 ;;
esac
EOF
cat > "$WORK/bin/nc" <<'EOF'
#!/usr/bin/env bash
printf 'nc %s\n' "$*" >> "$VPN_TEST_STATE/calls"
[ "$VPN_TEST_CASE" != proxy_failure ] || exit 27
cat
EOF
chmod +x "$WORK/bin/"*
for args in '83.219.159.2 1996' '162.55.137.149 22' '162.55.137.149 1996 extra'; do
  : > "$WORK/calls"
  if VPN_TEST_CASE=success bash "$HELPER" $args > "$WORK/out" 2> "$WORK/err"; then echo "FAIL: accepted $args"; exit 1; fi
  [ ! -s "$WORK/calls" ] || { echo 'FAIL: invalid target reached Docker'; exit 1; }
done
for case_ in stopped wrong_port missing_tunnel down_tunnel wrong_peer blackhole_failure route_failure wrong_route proxy_failure; do
  : > "$WORK/calls"
  if printf 'ssh bytes' | VPN_TEST_CASE="$case_" bash "$HELPER" 162.55.137.149 1996 > "$WORK/out" 2> "$WORK/err"; then echo "FAIL: accepted $case_"; exit 1; fi
  [ ! -s "$WORK/out" ] || { echo "FAIL: setup contaminated stdout ($case_)"; exit 1; }
  if [ "$case_" != proxy_failure ] && grep -q '^nc ' "$WORK/calls"; then echo "FAIL: failure reached proxy ($case_)"; exit 1; fi
done
# Repeat to model a new connection after a gateway restart: routes are re-established.
for attempt in 1 2; do
  : > "$WORK/calls"
  printf 'ssh bytes' | VPN_TEST_CASE=success bash "$HELPER" 162.55.137.149 1996 > "$WORK/out" 2> "$WORK/err"
  [ "$(cat "$WORK/out")" = 'ssh bytes' ]
  grep -qx 'nc -G 15 -X 5 -x 127.0.0.1:11080 162.55.137.149 1996' "$WORK/calls"
  python3 - "$WORK/calls" <<'PY'
import sys
calls = open(sys.argv[1]).read().splitlines()
blackhole = calls.index('ip route replace blackhole 162.55.137.149/32 metric 32760')
tunnel = calls.index('ip route replace 162.55.137.149/32 via 10.0.0.1 dev tun0 metric 10')
assert blackhole < tunnel < next(i for i, line in enumerate(calls) if line.startswith('nc '))
assert not any('docker cp' in line or 'ssh ' in line for line in calls)
PY
done
echo 'PASS: 14 VPN transport cases (no network or credentials)'
