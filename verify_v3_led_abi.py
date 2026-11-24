#!/usr/bin/env python3
"""Fail closed before publishing: official kernel ABI + model + compiled LED GPIO DTB.
This does NOT replace sysupgrade -T on the actual current router.
"""
from __future__ import annotations
import hashlib
import lzma
import re
import json
from pathlib import Path
import struct
import subprocess
import sys
import time
import urllib.request

TARGET = 'ramips/mt7621'
PROFILE = 'cudy_wr1300e-v2'
LED_GPIO = {'sys': 15, 'red': 3, 'internet': 13, 'wan': 14, 'lan': 42, 'wifi2': 47, 'wifi5': 45}
OFFICIAL_URL = 'https://downloads.openwrt.org/releases/25.12.6/targets/ramips/mt7621/profiles.json'


def u32(buf: bytes, off: int) -> int:
    return struct.unpack_from('>I', buf, off)[0]


def parse_fdt(blob: bytes) -> dict[str, dict[str, bytes]]:
    if blob[:4] != bytes.fromhex('d00dfeed'):
        raise ValueError('Not a flattened device tree (FDT)')
    total = u32(blob, 4)
    if total > len(blob) or total < 40:
        raise ValueError('Invalid FDT length')
    off_struct = u32(blob, 8)
    off_strings = u32(blob, 12)
    size_struct = u32(blob, 36)
    if off_struct + size_struct > total or off_strings >= total:
        raise ValueError('Invalid FDT offsets')
    strings = blob[off_strings:total]
    pos = off_struct
    stop = off_struct + size_struct
    path: list[str] = []
    nodes: dict[str, dict[str, bytes]] = {}
    while pos + 4 <= stop:
        tok = u32(blob, pos)
        pos += 4
        if tok == 1:  # BEGIN_NODE
            end = blob.index(0, pos, stop)
            path.append(blob[pos:end].decode('utf-8'))
            pos = (end + 4) & ~3
            nodes['/' + '/'.join(x for x in path if x)] = {}
        elif tok == 2:  # END_NODE
            path.pop()
        elif tok == 3:  # PROP
            length = u32(blob, pos)
            name_off = u32(blob, pos + 4)
            pos += 8
            value = blob[pos:pos + length]
            pos = (pos + length + 3) & ~3
            name_end = strings.index(0, name_off)
            name = strings[name_off:name_end].decode('utf-8')
            nodes['/' + '/'.join(x for x in path if x)][name] = value
        elif tok == 4:  # NOP
            pass
        elif tok == 9:  # END
            return nodes
        else:
            raise ValueError(f'Invalid FDT token {tok} at {pos}')
    raise ValueError('Unterminated FDT')


def fetch_official() -> dict:
    err = None
    for n in range(3):
        try:
            req = urllib.request.Request(OFFICIAL_URL, headers={'User-Agent': 'WR1300E-v3-ABI-verifier/1.0'})
            with urllib.request.urlopen(req, timeout=30) as response:
                return json.load(response)
        except Exception as exc:
            err = exc
            if n < 2:
                time.sleep(3)
    raise RuntimeError(f'Could not fetch official ABI manifest; STOP (fail closed): {err}')


def device_manifest(root: Path, manifest_path: Path, data: bytes) -> tuple[Path, Path]:
    # OpenWrt 25.12.6 emits a target-wide manifest before installing
    # per-device packages. Never use it to verify this device's packages.
    # Match the exact SquashFS bytes appended to the finished sysupgrade BIN.
    matches = []
    for squashfs in root.glob('build_dir/target-*/linux-ramips_mt7621/root.squashfs+pkg=*'):
        blob = squashfs.read_bytes()
        if blob.startswith(b'hsqs') and data.find(blob, 64 + u32(data, 12)) >= 0:
            suffix = squashfs.name.split('=', 1)[1]
            matches.append((squashfs, squashfs.parent / ('target-dir-' + suffix)))
    if len(matches) != 1:
        raise RuntimeError(f'Expected one device SquashFS matching BIN, found {len(matches)}')
    squashfs, device_root = matches[0]
    if not device_root.is_dir():
        raise RuntimeError(f'Missing matching device root: {device_root}')
    print(f'BIN SquashFS matches {squashfs.name}; reading package DB from {device_root.name}', flush=True)
    # Use the same host APK manifest command as OpenWrt's build, but on the
    # device-specific root directory rather than root.orig-ramips.
    host = root / 'staging_dir/host/bin'
    cmd = [str(host / 'fakeroot'), str(host / 'apk'), '--root', str(device_root),
           '--keys-dir', str(root), '--no-logfile', '--preserve-env', 'list',
           '--quiet', '--manifest', '--no-network', '--repositories-file', '/dev/null']
    try:
        proc = subprocess.run(cmd, check=True, capture_output=True, text=True,
                              cwd=root)
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f'Device APK manifest failed: {exc.stderr}') from exc
    if proc.stderr:
        print(proc.stderr, file=sys.stderr, end='')
    rows = []
    for line in proc.stdout.splitlines():
        fields = line.split(maxsplit=1)
        if len(fields) != 2:
            raise RuntimeError(f'Unexpected APK manifest line: {line!r}')
        rows.append(f'{fields[0]} - {fields[1]}')
    if not rows:
        raise RuntimeError('Device APK manifest empty')
    manifest = manifest_path.parent / f'openwrt-25.12.6-ramips-mt7621-{PROFILE}.manifest'
    manifest.write_text('\n'.join(sorted(rows)) + '\n')
    return manifest, device_root


def check(root: Path) -> dict:
    root = root.resolve()
    manifest_path = root / 'bin/targets/ramips/mt7621/profiles.json'
    if not manifest_path.is_file():
        raise RuntimeError(f'Build manifest not found: {manifest_path}')
    local = json.loads(manifest_path.read_text())
    remote = fetch_official()
    if local.get('target') != TARGET or remote.get('target') != TARGET:
        raise RuntimeError('Target is not ramips/mt7621')
    if local.get('version_number') != '25.12.6' or remote.get('version_number') != '25.12.6':
        raise RuntimeError('Wrong OpenWrt release number')
    actual = local.get('linux_kernel', {})
    expected = remote.get('linux_kernel', {})
    for key in ('version', 'release', 'vermagic'):
        if not actual.get(key) or actual.get(key) != expected.get(key):
            raise RuntimeError(f'Official kernel ABI mismatch: {key}: actual={actual.get(key)!r}, official={expected.get(key)!r}')
    if actual.get('vermagic') != 'aa81475ddbdbcc69877f69037ae40cd8' or actual.get('version') != '6.12.112' or str(actual.get('release')) != '1':
        raise RuntimeError('Unexpected pinned ABI')
    profile = local.get('profiles', {}).get(PROFILE)
    if not profile:
        raise RuntimeError('Missing cudy_wr1300e-v2 profile')
    if not {'cudy,wr1300e-v2', 'cudy,wr1300e', 'R60'}.issubset(profile.get('supported_devices', [])):
        raise RuntimeError('Image will not support board cudy,wr1300e-v2 without force: supported_devices mismatch')
    if not any(t.get('vendor') == 'Cudy' and t.get('model') == 'WR1300E' and t.get('variant') == 'v2' for t in profile.get('titles', [])):
        raise RuntimeError('Incorrect model title in image metadata')
    images = [x for x in profile.get('images', []) if x.get('type') == 'sysupgrade' and x.get('filesystem') == 'squashfs']
    if len(images) != 1:
        raise RuntimeError('Expected one squashfs sysupgrade image in profiles.json')
    bin_path = manifest_path.parent / images[0]['name']
    if not bin_path.is_file():
        raise RuntimeError(f'Sysupgrade image does not exist: {bin_path}')
    data = bin_path.read_bytes()
    if len(data) != images[0].get('size') or not 0x400000 < len(data) <= 0xf80000 or profile.get('file_size_limits',{}).get('image') != 0xf80000:
        raise RuntimeError('Image size mismatch')
    sha256 = hashlib.sha256(data).hexdigest()
    if images[0].get('sha256') != sha256:
        raise RuntimeError('Firmware SHA256 differs from profiles.json')
    # Verify package selections in the actual per-device root filesystem.
    manifest, device_root = device_manifest(root, manifest_path, data)
    defaults = device_root / 'etc/board.d/01_leds'
    if not defaults.is_file() or 'cudy,wr1300e-v2)' not in defaults.read_text():
        raise RuntimeError('WR1300E V2 LED defaults absent from device root 01_leds')
    pkg_names = {line.split()[0] for line in manifest.read_text().splitlines() if line.strip()}
    for package in ('kmod-ledtrig-network','luci','dropbear','kmod-mt7603','kmod-mt7615e'):
        if package not in pkg_names:
            raise RuntimeError(f'{package} absent from device manifest')
    print('Device package manifest and LED defaults verified', flush=True)
    # Check the actual DTB inside the published image, not a possibly stale build file.
    kernel = lzma.decompress(data[64:64 + u32(data, 12)])
    candidates = []
    for match in re.finditer(bytes.fromhex('d00dfeed'), kernel):
        try:
            candidate = parse_fdt(kernel[match.start():])
            if candidate.get('/', {}).get('model', b'').rstrip(b'\0') == b'Cudy WR1300E v2':
                candidates.append(candidate)
        except (ValueError, IndexError, UnicodeError, struct.error):
            continue
    if len(candidates) != 1:
        raise RuntimeError(f'Expected one WR1300E DTB in BIN, found {len(candidates)}')
    nodes = candidates[0]
    machine = nodes.get('/', {})
    compat = machine.get('compatible', b'').split(b'\x00')[0]
    model = machine.get('model', b'').rstrip(b'\x00')
    if compat != b'cudy,wr1300e-v2' or model != b'Cudy WR1300E v2':
        raise RuntimeError(f'Wrong DTB board identity: compatible={compat!r}, model={model!r}')
    observed = {}
    for led, gpio in LED_GPIO.items():
        props = nodes.get(f'/leds/{led}', {})
        data = props.get('gpios', b'')
        if len(data) != 12:
            raise RuntimeError(f'LED {led}: expected 3 cells in gpios, got {len(data)} bytes')
        cells = struct.unpack('>3I', data)
        if cells[1] != gpio or cells[2] != 1:
            raise RuntimeError(f'LED {led}: expected GPIO{gpio} ACTIVE_LOW, got {cells}')
        label = {'sys':'green:sys','red':'red:status','internet':'green:wan-online','wan':'green:wan','lan':'green:lan','wifi2':'green:wlan-2ghz','wifi5':'green:wlan-5ghz'}[led]
        if props.get('label',b'').rstrip(b'\0').decode() != label:
            raise RuntimeError(f'Wrong LED label for {led}')
        if led not in ('sys','red') and f'"{label}"' not in defaults.read_text():
            raise RuntimeError(f'LED defaults missing for {led}')
        observed[led] = {'gpio':gpio,'active_low':True,'label':label}
    if '/leds/wps' in nodes:
        raise RuntimeError('Inherited WPS LED still present: GPIO13 conflict with Internet LED')
    groups = nodes.get('/pinctrl/pinctrl0/gpio', {}).get('groups', b'')
    if b'uart3\x00' not in groups:
        raise RuntimeError('UART3 GPIO pinmux missing; Wi-Fi LEDs may not work')
    switch = '/ethernet@1e100000/mdio-bus/switch@1f/ports/'
    for port,status,label in [(0,'disabled',None),(1,'disabled',None),(2,'okay','lan2'),(3,'okay','lan1'),(4,'disabled',None)]:
        props = nodes.get(switch+f'port@{port}', {})
        if props.get('status',b'').rstrip(b'\0').decode()!=status or (label and props.get('label',b'').rstrip(b'\0').decode()!=label):
            raise RuntimeError(f'Unexpected switch port {port}')
    for port,freq in [(0,(2400000,2500000)),(1,(5000000,6000000))]:
        props=nodes.get(f'/pcie@1e140000/pcie@{port},0/wifi@0,0',{})
        if props.get('ieee80211-freq-limit')!=struct.pack('>2I',*freq):
            raise RuntimeError(f'Wrong PCIe Wi-Fi mapping {port}')
    expected_parts={'u-boot':(0,0x30000),'u-boot-env':(0x30000,0x10000),'factory':(0x40000,0x10000),'firmware':(0x50000,0xf80000),'debug':(0xfd0000,0x10000),'backup':(0xfe0000,0x10000),'bdinfo':(0xff0000,0x10000)}
    found_parts={v.get('label',b'').rstrip(b'\0').decode(errors='replace'):v.get('reg') for k,v in nodes.items() if '/partitions/partition@' in k}
    for part,bounds in expected_parts.items():
        if found_parts.get(part)!=struct.pack('>2I',*bounds):
            raise RuntimeError(f'Wrong flash partition {part}')
    for name,led in [('led-boot','sys'),('led-running','sys'),('led-failsafe','red'),('led-upgrade','red')]:
        if nodes.get('/aliases',{}).get(name,b'').rstrip(b'\0')!=('/leds/'+led).encode():
            raise RuntimeError(f'Wrong LED alias {name}')
    return {
        'version': local['version_number'],
        'target': local['target'],
        'official_abi': expected,
        'built_abi': actual,
        'profile': PROFILE,
        'supported_devices': profile['supported_devices'],
        'model': model.decode(),
        'compiled_leds': observed,
        'device_manifest': manifest.name,
        'device_rootfs': device_root.name,
        'sysupgrade_sha256': sha256,
        'image_filename': bin_path.name,
        'status': 'STATIC BUILD VALIDATION PASSED; HARDWARE NOT YET TESTED',
    }


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit('Usage: python3 verify_v3_led_abi.py ./openwrt')
    try:
        out = check(Path(sys.argv[1]))
        report = Path('diagnostics/led-abi-validation.json')
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(json.dumps(out, indent=2, ensure_ascii=False) + '\n')
        print('V4 FIX4 ABI + model + embedded DTB VERIFIED; image is UNTESTED on physical hardware')
        print(json.dumps(out, indent=2, ensure_ascii=False))
    except Exception as exc:
        sys.exit(f'ERROR (no firmware artifact will be published): {exc}')

if __name__ == '__main__':
    main()
