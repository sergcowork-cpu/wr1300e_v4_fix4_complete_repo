#!/usr/bin/env python3
"""Add board LED defaults without modifying network/port mapping or kernel sources.

LEDs are described in DTS. 01_leds supplies default link/activity triggers
for a fresh install; restored /etc/config/system can override these defaults.
"""
from pathlib import Path
import sys

if len(sys.argv) != 2:
    sys.exit('Usage: patch_led_defaults.py /path/to/openwrt')
root = Path(sys.argv[1]).resolve()
p = root / 'target/linux/ramips/mt7621/base-files/etc/board.d/01_leds'
if not p.is_file():
    sys.exit(f'ERROR: expected OpenWrt board defaults file not found: {p}')
s = p.read_text()
case = '''cudy,wr1300e-v2)
    # V4-derived LED names. Network trigger module supplied by V3 image.
    ucidef_set_led_netdev "internet" "Internet link" "green:wan-online" "wan" "link"
    ucidef_set_led_netdev "wan" "WAN activity" "green:wan" "wan" "link tx rx"
    ucidef_set_led_netdev "lan" "LAN activity" "green:lan" "br-lan" "link tx rx"
    # PCIe0 is 2.4 GHz, PCIe1 is 5 GHz; verify PHY order on hardware.
    ucidef_set_led_wlan "wifi2" "Wi-Fi 2.4 GHz" "green:wlan-2ghz" "phy0tpt"
    ucidef_set_led_wlan "wifi5" "Wi-Fi 5 GHz" "green:wlan-5ghz" "phy1tpt"
    ;;
'''
if 'cudy,wr1300e-v2)' in s:
    if case.strip() in s:
        print('LED defaults already patched')
        sys.exit(0)
    sys.exit('ERROR: existing different WR1300E v2 LED defaults found; manual review required')
anchor='case $board in\n'
if s.count(anchor) != 1 or 'board_config_flush' not in s:
    sys.exit('ERROR: unexpected upstream 01_leds structure; refusing to patch')
s=s.replace(anchor,anchor+case,1)
p.write_text(s)
print('Added WR1300E v2 default LAN/WAN/Internet/WiFi LED triggers')
