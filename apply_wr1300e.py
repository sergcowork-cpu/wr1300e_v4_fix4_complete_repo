#!/usr/bin/env python3
"""Add WR1300E v2 to the official OpenWrt v25.12.6 source tree.
Stops rather than guessing when upstream anchors are different.
"""
from pathlib import Path
import sys

if len(sys.argv) != 2:
    sys.exit('Usage: python3 apply_wr1300e.py /path/to/openwrt')
root = Path(sys.argv[1]).resolve()
kit = Path(__file__).resolve().parent
mk = root / 'target/linux/ramips/image/mt7621.mk'
net = root / 'target/linux/ramips/mt7621/base-files/etc/board.d/02_network'
dtsi = root / 'target/linux/ramips/dts/mt7621_cudy_wr1300-v2v3.dtsi'
if not all(x.is_file() for x in [mk, net, dtsi]):
    sys.exit('Missing official OpenWrt v25.12.6 source files')
base = dtsi.read_text()
for required in ['partition@50000', 'reg = <0x50000 0xf80000>', 'eeprom_factory_8000', '&switch0']:
    if required not in base:
        sys.exit('Unexpected official DTSI structure: ' + required)

# Precheck all three modifications before writing.
s = mk.read_text()
if 'TARGET_DEVICES += cudy_wr1300e-v2' not in s and s.count('define Device/cudy_wr1300-v2\n') != 1:
    sys.exit('Unexpected mt7621.mk device anchor')
n = net.read_text()
if 'cudy,wr1300e-v2|\\' not in n and n.count('glinet,gl-mt1300|\\') != 1:
    sys.exit('Unexpected 02_network two-LAN anchor')
dst = root / 'target/linux/ramips/dts/mt7621_cudy_wr1300e-v2.dts'
content = (kit / 'device/mt7621_cudy_wr1300e-v2.dts').read_text()
if dst.exists() and dst.read_text() != content:
    sys.exit('A different WR1300E board definition already exists')

block = '''define Device/cudy_wr1300e-v2
  $(Device/dsa-migration)
  IMAGE_SIZE := 15872k
  DEVICE_VENDOR := Cudy
  DEVICE_MODEL := WR1300E
  DEVICE_VARIANT := v2
  DEVICE_PACKAGES := kmod-mt7603 kmod-mt7615e kmod-mt7663-firmware-ap kmod-ledtrig-network \\
    -uboot-envtools
  SUPPORTED_DEVICES += cudy,wr1300e R60
endef
TARGET_DEVICES += cudy_wr1300e-v2
'''
dst.write_text(content)
if 'TARGET_DEVICES += cudy_wr1300e-v2' not in s:
    s = s.replace('define Device/cudy_wr1300-v2\n', block + '\n' + 'define Device/cudy_wr1300-v2\n', 1)
    mk.write_text(s)
if 'cudy,wr1300e-v2|\\' not in n:
    n = n.replace('glinet,gl-mt1300|\\', 'cudy,wr1300e-v2|\\\n\tglinet,gl-mt1300|\\', 1)
    net.write_text(n)
assert 'TARGET_DEVICES += cudy_wr1300e-v2' in mk.read_text()
assert 'cudy,wr1300e-v2|\\' in net.read_text()
print('WR1300E v2 support added to OpenWrt 25.12.6 source: UNTESTED on physical hardware.')
