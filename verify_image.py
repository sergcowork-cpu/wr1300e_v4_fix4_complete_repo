#!/usr/bin/env python3
"""Static firmware sanity checks; NOT proof of safe hardware operation."""
import sys, re, json, struct, binascii, hashlib
from pathlib import Path
if len(sys.argv) != 2:
    sys.exit('Usage: python3 verify_image.py openwrt-...bin')
p = Path(sys.argv[1]); data = p.read_bytes()
if not (0x400000 < len(data) <= 0xf80000):
    sys.exit(f'Unexpected image size: {len(data)}')
if data[:4] != bytes.fromhex('27051956'):
    sys.exit('Invalid U-Boot uImage header')
magic, hcrc, timestamp, size, load, entry, dcrc = struct.unpack_from('>7I', data)
hdr = bytearray(data[:64]); hdr[4:8] = bytes(4)
if binascii.crc32(hdr) & 0xffffffff != hcrc:
    sys.exit('Invalid header CRC')
if size > len(data)-64 or (binascii.crc32(data[64:64+size]) & 0xffffffff) != dcrc:
    sys.exit('Invalid kernel CRC or size')
tail = data[-8192:].decode(errors='ignore')
match = re.search(r'\{\s*"metadata_version"\s*:\s*"1\.1".*?"board"\s*:\s*"cudy_wr1300e-v2"\s*\}\s*\}', tail, re.S)
if not match:
    sys.exit('OpenWrt metadata for cudy_wr1300e-v2 missing')
meta = json.loads(match.group(0))
if meta['version']['version'] != '25.12.6' or meta['version']['target'] != 'ramips/mt7621':
    sys.exit('Wrong OpenWrt version or target')
if not {'cudy,wr1300e-v2','cudy,wr1300e','R60'}.issubset(meta.get('new_supported_devices', [])):
    sys.exit('Wrong supported_devices')
print('Static checks OK:', p.name, len(data), hashlib.sha256(data).hexdigest())
print('Image remains UNTESTED on hardware; do not flash without backups.')
