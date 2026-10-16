# clevofan on Fedora — build, install, and manage as an akmods kernel module

This documents the working setup used on the Clevo P15SM / Sager NP8230 running Fedora 44.

The goal is to take the `clevofan` source, compile and test it, then package it with `akmods` so Fedora automatically rebuilds the module for new kernels.

## Hardware / platform

The machine reports:

```text
DMI vendor:  Notebook
DMI product: P15SM
```

The upstream `clevofan` source did not match this machine as-is, so the driver was patched so `force_match` can bypass the normal vendor/product check.

The working module option is:

```text
force_match=2
```

## 1. Install build dependencies

```bash
sudo dnf install \
    gcc \
    make \
    kernel-devel \
    kernel-headers \
    akmods \
    kmodtool \
    rpm-build \
    rpmdevtools
```

Verify the running kernel and matching development package:

```bash
uname -r
rpm -q "kernel-devel-$(uname -r)"
```

If needed:

```bash
sudo dnf install "kernel-devel-$(uname -r)"
```

## 2. Build directly from source

```bash
cd ~/repos/clevofan
make clean
make
```

The result should include:

```text
clevofan.ko
```

Inspect it:

```bash
modinfo ./clevofan.ko
```

## 3. Test the freshly compiled module

Stop anything currently using the module:

```bash
sudo systemctl stop clevo-fan-control.service
```

Unload an existing copy if present:

```bash
sudo modprobe -r clevofan
```

Load the newly compiled file directly:

```bash
sudo insmod ./clevofan.ko force_match=2
```

Verify:

```bash
lsmod | grep clevofan
sudo dmesg | tail -100
```

Find the hwmon device:

```bash
for h in /sys/class/hwmon/hwmon*; do
    echo "=== $h ($(cat "$h/name" 2>/dev/null)) ==="
    grep . "$h"/fan*_input "$h"/pwm* "$h"/pwm*_enable 2>/dev/null
done
```

Useful sysfs entries on this machine include:

```text
fan1_input
fan2_input
pwm1
pwm2
pwm1_enable
pwm2_enable
```

Mode values:

```text
1 = manual PWM control
2 = automatic / EC control
```

Example:

```bash
echo 1 | sudo tee /sys/class/hwmon/hwmonX/pwm1_enable
echo 100 | sudo tee /sys/class/hwmon/hwmonX/pwm1
```

Return control to the EC:

```bash
echo 2 | sudo tee /sys/class/hwmon/hwmonX/pwm1_enable
```

Replace `hwmonX` with the actual hwmon directory.

## 4. Configure the module option permanently

Create:

```bash
sudo nano /etc/modprobe.d/clevofan.conf
```

with:

```text
options clevofan force_match=2
```

Optionally load it automatically at boot:

```bash
echo clevofan | sudo tee /etc/modules-load.d/clevofan.conf
```


## 5. Install the Python fan controller

The kernel module only exposes fan control through hwmon. The temperature-to-PWM policy is handled by a Python script installed at:

```text
/usr/local/sbin/clevo-fan-control
```

Create it:

```bash
sudo nano /usr/local/sbin/clevo-fan-control
```

Copy the code in [clevo-fan-control.py](./clevo-fan-control.py) into it. This will be the core logic the systemd service will run for PWM management.

Make it executable:

```bash
sudo chmod 0755 /usr/local/sbin/clevo-fan-control
```

This script use hysteresis to make fan operation smooth across temperature changes.

## 6. Install the systemd service

Create:

```bash
sudo nano /etc/systemd/system/clevo-fan-control.service
```

with:

```ini
[Unit]
Description=Clevo P15SM Fan Controller
After=multi-user.target
ConditionPathExists=/sys/class/hwmon

[Service]
Type=simple
ExecStartPre=/usr/sbin/modprobe clevofan
ExecStart=/usr/local/sbin/clevo-fan-control
Restart=on-failure
RestartSec=3
TimeoutStopSec=5

[Install]
WantedBy=multi-user.target
```

Reload systemd and enable the service:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now clevo-fan-control.service
```

Check it:

```bash
systemctl status clevo-fan-control.service
journalctl -u clevo-fan-control.service -b
```

`ExecStart` points directly at the script because it has a Python shebang.

Before unloading or replacing the kernel module:

```bash
sudo systemctl stop clevo-fan-control.service
sudo modprobe -r clevofan
```

After loading the module again:

```bash
sudo modprobe clevofan
sudo systemctl start clevo-fan-control.service
```


## 7. Package it for akmods

The source repo is:

```text
~/repos/clevofan
```

The akmods SRPM lives under:

```text
/usr/src/akmods/
```

The working package used here was:

```text
/usr/src/akmods/clevofan-kmod-1.0-2.fc44.src.rpm
```

with:

```text
/usr/src/akmods/clevofan-kmod.latest
```

The important `%prep` fix was to avoid copying the source directory into itself. Use a clean per-kernel build directory and copy only the files needed by the module build, e.g.:

```spec
%prep
rm -rf %{_kmod_build_$kver}
mkdir -p %{_kmod_build_$kver}
cp %{SOURCE1} %{_kmod_build_$kver}/Makefile
cp %{SOURCE2} %{_kmod_build_$kver}/clevofan.c
```

Each kernel build directory should contain:

```text
Makefile
clevofan.c
```

After building the SRPM and placing/installing it under `/usr/src/akmods`, build for the current kernel:

```bash
sudo akmods --force --kernels "$(uname -r)"
sudo depmod -a
```

## 8. Verify the installed module

The final module should land under a path like:

```text
/usr/lib/modules/$(uname -r)/extra/clevofan/clevofan.ko.xz
```

Verify which copy `modprobe` will load:

```bash
modinfo -n clevofan
```

Expected shape:

```text
/usr/lib/modules/<kernel>/extra/clevofan/clevofan.ko.xz
```

Inspect metadata:

```bash
modinfo clevofan
```

## 9. Remove stale manually installed copies

Old manually copied modules can shadow the akmods-managed copy.

Find all copies:

```bash
find "/usr/lib/modules/$(uname -r)" -type f \
    \( -name 'clevofan.ko' -o -name 'clevofan.ko.xz' -o -name 'clevofan.ko.zst' \) \
    -print
```

Keep the akmods-managed module under:

```text
extra/clevofan/
```

Remove obsolete manually installed copies elsewhere, then:

```bash
sudo depmod -a
modinfo -n clevofan
```

## 10. Reload the installed module

Use this order because the fan-control service depends on the driver:

```bash
sudo systemctl stop clevo-fan-control.service
sudo modprobe -r clevofan
sudo modprobe clevofan
sudo systemctl start clevo-fan-control.service
```

Verify:

```bash
lsmod | grep clevofan
modinfo -n clevofan
systemctl status clevo-fan-control.service
```

The `force_match=2` option should now come from:

```text
/etc/modprobe.d/clevofan.conf
```

## 11. After a Fedora kernel update

Normally akmods should rebuild the module automatically.

Verify after booting the new kernel:

```bash
uname -r
modinfo -n clevofan
```

If it was not built:

```bash
sudo akmods --force --kernels "$(uname -r)"
sudo depmod -a
sudo modprobe clevofan
```

Check:

```bash
find "/usr/lib/modules/$(uname -r)/extra" -type f -name 'clevofan*' -print
```

## 12. Diagnostics

Loaded module:

```bash
lsmod | grep clevofan
```

Selected module file:

```bash
modinfo -n clevofan
```

Parameters:

```bash
modinfo clevofan | grep -i parm
```

Kernel log:

```bash
sudo dmesg | grep -i clevo
```

Kernel/devel match:

```bash
uname -r
rpm -q "kernel-devel-$(uname -r)"
```

hwmon state:

```bash
for h in /sys/class/hwmon/hwmon*; do
    echo "=== $h ($(cat "$h/name" 2>/dev/null)) ==="
    grep . "$h"/fan*_input "$h"/pwm* "$h"/pwm*_enable 2>/dev/null
done
```

## Known-good P15SM fan behavior

Observed approximate PWM behavior:

| PWM | Approx. fan speed |
|---:|---:|
| 255 | ~4060 RPM |
| 220 | ~3750 RPM |
| 180 | ~3260 RPM |
| 140 | ~2730 RPM |
| 100 | ~2080 RPM |
| 80 | ~1680 RPM |
| 79 | ~1660 RPM |
| 78 | ~1630 RPM |
| 77 | ~1560 RPM |
| 76 | ~1410 RPM |
| 0–74 | generally stopped / unable to sustain |

Practical minimum sustained PWM:

```text
80
```

For reliable startup from stopped, kick the fan at about:

```text
100
```

for roughly one second, then drop to the desired PWM.

## Current file layout

```text
~/repos/clevofan/
├── clevofan.c
├── Makefile
└── ...

/etc/modprobe.d/clevofan.conf
/etc/modules-load.d/clevofan.conf

/usr/src/akmods/
├── clevofan-kmod-1.0-2.fc44.src.rpm
└── clevofan-kmod.latest

/usr/lib/modules/<kernel>/extra/clevofan/
└── clevofan.ko.xz
```

The fan controller and service are:

```text
/usr/local/sbin/clevo-fan-control
/etc/systemd/system/clevo-fan-control.service
```

The Python controller depends on the `clevofan` hwmon interface. Stop the service before unloading or replacing the module.

## Short rebuild/install checklist

For a source change:

```bash
cd ~/repos/clevofan

make clean
make

sudo systemctl stop clevo-fan-control.service
sudo modprobe -r clevofan

sudo insmod ./clevofan.ko force_match=2

lsmod | grep clevofan
sudo dmesg | tail -100
```

Once the source is confirmed working, rebuild/update the akmods SRPM, install it under `/usr/src/akmods`, then:

```bash
sudo akmods --force --kernels "$(uname -r)"
sudo depmod -a

sudo modprobe -r clevofan
sudo modprobe clevofan

sudo systemctl start clevo-fan-control.service

modinfo -n clevofan
```

At that point the module is managed by akmods and should follow future Fedora kernel updates automatically.
