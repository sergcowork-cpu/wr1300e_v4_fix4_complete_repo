#!/usr/bin/env python3
import sys,re
from pathlib import Path
src,dst=map(Path,sys.argv[1:]); lines=src.read_text().splitlines()
selected='CONFIG_TARGET_DEVICE_ramips_mt7621_DEVICE_cudy_wr1300e-v2'
for i,line in enumerate(lines):
    m=re.match(r'^(?:# )?(CONFIG_TARGET_DEVICE_ramips_mt7621_DEVICE_[^= ]+)(?:=y| is not set)$',line)
    if m:
        k=m.group(1); lines[i]=k+'=y' if k==selected else '# '+k+' is not set'
    elif line=='CONFIG_TARGET_ALL_PROFILES=y': lines[i]='# CONFIG_TARGET_ALL_PROFILES is not set'
assert selected+'=y' in lines and 'CONFIG_TARGET_MULTI_PROFILE=y' in lines
assert 'CONFIG_TARGET_PER_DEVICE_ROOTFS=y' in lines and 'CONFIG_ALL_KMODS=y' in lines
assert 'CONFIG_PACKAGE_kmod-ledtrig-network=m' in lines
dst.write_text('\n'.join(lines)+'\n')
