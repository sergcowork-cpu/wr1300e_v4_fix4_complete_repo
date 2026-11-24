#!/usr/bin/env bash
set -Eeuo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
SRC="$HERE/openwrt"
D="$HERE/diagnostics"
TARGET_ABI='aa81475ddbdbcc69877f69037ae40cd8'
JOBS="${JOBS:-2}"
mkdir -p "$D"
trap 'echo "FAIL at line $LINENO; see diagnostics" >&2' ERR
[[ ! -e "$SRC" ]] || { echo 'Use fresh checkout without openwrt/'; exit 1; }
printf '%s  %s\n' '2bf0e139ed76a866636dab406cc37ae8fc419e7e303ab33dd2876f4d3c6f08b3' "$HERE/config/v3-effective.config" | sha256sum -c -
printf '%s  %s\n' 'a806cbb8394c4014cae202faabb9914bbc058b22b0ee1c0cc3bbbe307d2b5a5b' "$HERE/config/official-config.buildinfo" | sha256sum -c -
git clone --depth 1 --branch v25.12.6 https://github.com/openwrt/openwrt.git "$SRC"
[[ "$(git -C "$SRC" describe --tags --exact-match HEAD)" == v25.12.6 ]]
python3 "$HERE/apply_wr1300e.py" "$SRC"
python3 "$HERE/patch_led_defaults.py" "$SRC"
cp "$SRC/target/linux/ramips/dts/mt7621_cudy_wr1300e-v2.dts" "$D/patched-device.dts"
cp "$SRC/target/linux/ramips/mt7621/base-files/etc/board.d/01_leds" "$D/patched-01_leds"
cd "$SRC"
./scripts/feeds update -a 2>&1 | tee "$D/feeds-update.log"
while read -r feed expected; do
  [[ -n "$feed" ]] || continue
  actual="$(git -C "feeds/$feed" rev-parse HEAD)"
  printf '%s %s\n' "$feed" "$actual" >> "$D/feeds-revisions.txt"
  [[ "$actual" == "$expected" ]] || { echo "Feed revision mismatch: $feed"; exit 1; }
done < "$HERE/config/feeds-revisions.txt"
./scripts/feeds install -a 2>&1 | tee "$D/feeds-install.log"
# Previously observed unused WeeChat frontend creates a recursive Kconfig dependency.
[[ ! -e package/feeds/luci/luci-app-weechat || -L package/feeds/luci/luci-app-weechat ]] || exit 1
rm -f package/feeds/luci/luci-app-weechat
find package/feeds -type l | sort > "$D/installed-feed-package-links.txt"
while IFS= read -r path; do
  [[ "$path" == package/feeds/luci/luci-app-weechat || -z "$path" ]] && continue
  [[ -e "$path" ]] || { echo "Missing V3 feed definition: $path"; exit 1; }
done < "$HERE/config/installed-feed-package-links.txt"
python3 "$HERE/prepare_config.py" "$HERE/config/v3-effective.config" .config
cp .config "$D/requested.config"
make defconfig 2>&1 | tee "$D/defconfig.log"
cp .config "$D/final.config"
if grep -q 'recursive dependency detected' "$D/defconfig.log"; then exit 1; fi
for key in CONFIG_TARGET_MULTI_PROFILE CONFIG_TARGET_PER_DEVICE_ROOTFS CONFIG_TARGET_DEVICE_ramips_mt7621_DEVICE_cudy_wr1300e-v2 CONFIG_ALL_KMODS CONFIG_PACKAGE_luci; do
  grep -qxF "$key=y" .config || { echo "Missing $key"; exit 1; }
done
grep -qxF 'CONFIG_PACKAGE_kmod-ledtrig-network=m' .config
[[ "$(grep -c '^CONFIG_TARGET_DEVICE_ramips_mt7621_DEVICE_.*=y$' .config)" == 1 ]] || { echo 'Unexpected selected profiles'; exit 1; }
python3 - "$HERE/config/v3-effective.config" .config <<'PY' | tee "$D/kmod-diff.txt"
import re,sys,pathlib
pat=re.compile(r'^CONFIG_PACKAGE_(kmod-[^=]+)=([my])$',re.M)
a,b=[dict(pat.findall(pathlib.Path(p).read_text())) for p in sys.argv[1:]]
for k in sorted(a.keys()|b.keys()):
    if a.get(k)!=b.get(k): print(k,a.get(k,'n'),'->',b.get(k,'n'))
print('V3 kmods:',len(a),'Current kmods:',len(b))
PY
make download -j4 2>&1 | tee "$D/download.log"
make -j"$JOBS" tools/install V=s 2>&1 | tee "$D/host-tools.log"
make -j"$JOBS" toolchain/install V=s 2>&1 | tee "$D/toolchain-build.log"
[[ -x staging_dir/host/bin/m4 ]]
make -j"$JOBS" target/linux/compile V=s 2>&1 | tee "$D/kernel-build.log"
check_abi() {
  local actual
  local -a files
  mapfile -t files < <(find build_dir -type f -name .vermagic -path '*linux-6.12.112*' | sort)
  [[ ${#files[@]} -eq 1 ]] || { echo "Expected one kernel .vermagic: ${#files[@]}"; return 1; }
  actual="$(tr -d '\r\n' < "${files[0]}")"
  printf 'EXPECTED_ABI=%s\nACTUAL_ABI=%s\n' "$TARGET_ABI" "$actual" | tee -a "$D/ABI_RESULT.txt"
  [[ "$actual" == "$TARGET_ABI" ]] || { echo 'ABI_MISMATCH — STOP'; return 30; }
}
check_abi
make -j"$JOBS" V=s 2>&1 | tee "$D/firmware-build.log"
check_abi
shopt -s nullglob
imgs=(bin/targets/ramips/mt7621/*cudy_wr1300e-v2*squashfs-sysupgrade.bin)
[[ ${#imgs[@]} -eq 1 ]]
python3 "$HERE/verify_image.py" "${imgs[0]}" | tee "$D/image-validation.txt"
cd "$HERE"
cp "$SRC/bin/targets/ramips/mt7621/profiles.json" "$D/profiles.json"
python3 -u "$HERE/verify_v3_led_abi.py" "$SRC" 2>&1 | tee "$D/final-validation.txt"
echo 'STATIC_VALIDATION_OK — hardware not yet tested'
