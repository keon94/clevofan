#!/usr/bin/env python3
"""Clevo P15SM CPU fan + GPU blower controller.

Requires the clevofan kernel module with force_match=2 and nvidia-smi.
GPU fan tachometer reports zero; logged GPU RPM is estimated from
manual PWM-versus-RPM measurements when EC tachometer data is absent.
"""

import glob
import os
import signal
import subprocess
import sys
import time

POLL_SECONDS = 2
STATUS_LOG_SECONDS = 15

FAN_ON_TEMP = 55
FAN_OFF_TEMP = 49
KICK_PWM = 100
KICK_SECONDS = 1
EMERGENCY_TEMP = 90
CURVE = [
    (55, 80),
    (60, 100),
    (65, 130),
    (70, 160),
    (75, 190),
    (80, 220),
    (85, 255),
]

# Tesla Nvidia GPU blower curve, calibrated manually on this Clevo P15SM.
GPU_INDEX = "0"  # nvidia-smi index of the GPU
GPU_CURVE = [
    (0, 0),
    (45, 75),
    (50, 80),
    (55, 100),
    (60, 120),
    (65, 140),
    (70, 160),
    (75, 200),
    (80, 255)
]

# Approximate RPM observed during manual testing; not live tachometer data.
GPU_RPM_AT_PWM = {
    80: 1500,
    100: 1860,
    120: 2170,
    140: 2450,
    180: 2950,
    220: 3400,
    255: 3600,
}
GPU_HYSTERESIS_C = 2
GPU_FAILSAFE_PWM = 255
GPU_REASSERT_SECONDS = 30

running = True


def log(message):
    print(f"clevo-fan-control: {message}", flush=True)


def read(path):
    with open(path, "r") as f:
        return f.read().strip()


def write(path, value):
    with open(path, "w") as f:
        f.write(str(value))


def rpm_for_log(path, *, gpu=False):
    """Report EC tachometer RPM without letting telemetry break cooling.

    This setup reports zero for fan2 even when the blower is
    physically spinning; don't present that as a measured GPU fan speed.
    """
    try:
        rpm = int(read(path))
    except (OSError, ValueError):
        return "N/A"
    if gpu and rpm == 0:
        return "N/A (EC tach=0)"
    return str(rpm)


def find_clevo_hwmon():
    for hwmon in glob.glob("/sys/class/hwmon/hwmon*"):
        try:
            name = read(os.path.join(hwmon, "name"))
        except OSError:
            continue
        if name.startswith("P15SM"):
            return hwmon
    raise RuntimeError("Could not find P15SM clevofan hwmon device")


def find_cpu_temp():
    for hwmon in glob.glob("/sys/class/hwmon/hwmon*"):
        try:
            if read(os.path.join(hwmon, "name")) != "coretemp":
                continue
        except OSError:
            continue
        for label_path in glob.glob(os.path.join(hwmon, "temp*_label")):
            try:
                if read(label_path) == "Package id 0":
                    return label_path.replace("_label", "_input")
            except OSError:
                continue
    raise RuntimeError("Could not find coretemp Package id 0")


def pwm_for_temp(temp_c):
    pwm = 0
    for threshold, value in CURVE:
        if temp_c >= threshold:
            pwm = value
        else:
            break
    return pwm


def gpu_temp_c():
    result = subprocess.run(
        [
            "/usr/bin/nvidia-smi",
            "-i", GPU_INDEX,
            "--query-gpu=temperature.gpu",
            "--format=csv,noheader,nounits",
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=3,
    )
    temp = float(result.stdout.strip())
    if not 0 <= temp <= 120:
        raise ValueError(f"implausible GPU temperature: {temp}")
    return temp


def gpu_pwm_for_temp(temp_c, last_pwm):
    step = 0
    for i, (threshold, _) in enumerate(GPU_CURVE):
        if temp_c >= threshold:
            step = i
        else:
            break

    target = GPU_CURVE[step][1]
    if last_pwm is None or target >= last_pwm:
        return target

    # Allow immediate fan speed increases, delay decreases until sufficiently
    # below the threshold for the current fan step.
    for threshold, pwm in GPU_CURVE:
        if pwm == last_pwm and temp_c > threshold - GPU_HYSTERESIS_C:
            return last_pwm
    return target



def handle_signal(signum, frame):
    global running
    running = False


def main():
    global running

    fan_hwmon = find_clevo_hwmon()
    cpu_pwm_path = os.path.join(fan_hwmon, "pwm1")
    cpu_enable_path = os.path.join(fan_hwmon, "pwm1_enable")
    gpu_pwm_path = os.path.join(fan_hwmon, "pwm2")
    gpu_enable_path = os.path.join(fan_hwmon, "pwm2_enable")
    cpu_rpm_path = os.path.join(fan_hwmon, "fan1_input")
    gpu_rpm_path = os.path.join(fan_hwmon, "fan2_input")

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    cpu_fan_running = False
    current_cpu_pwm = None
    current_gpu_pwm = None
    last_gpu_write = 0.0
    gpu_sensor_failed = False
    last_status_log = 0.0

    try:
        # Put the GPU blower into a safe state before querying sensors.
        write(gpu_enable_path, 1)
        write(gpu_pwm_path, GPU_FAILSAFE_PWM)
        current_gpu_pwm = GPU_FAILSAFE_PWM
        last_gpu_write = time.monotonic()

        cpu_temp_path = find_cpu_temp()
        log(f"started; GPU fan manual at PWM {GPU_FAILSAFE_PWM}")

        while running:
            cpu_temp = int(read(cpu_temp_path)) / 1000.0

            # Preserve the existing CPU fan behavior.
            if cpu_temp >= EMERGENCY_TEMP:
                cpu_target = 255
                cpu_fan_running = True
            elif cpu_fan_running:
                if cpu_temp <= FAN_OFF_TEMP:
                    cpu_target = 0
                    cpu_fan_running = False
                else:
                    cpu_target = max(80, pwm_for_temp(cpu_temp))
            else:
                if cpu_temp >= FAN_ON_TEMP:
                    cpu_target = max(80, pwm_for_temp(cpu_temp))
                    write(cpu_enable_path, 1)
                    write(cpu_pwm_path, KICK_PWM)
                    time.sleep(KICK_SECONDS)
                    cpu_fan_running = True
                else:
                    cpu_target = 0

            write(cpu_enable_path, 1)
            cpu_changed = cpu_target != current_cpu_pwm
            if cpu_changed:
                write(cpu_pwm_path, cpu_target)
                current_cpu_pwm = cpu_target

            try:
                gpu_temp = gpu_temp_c()
                if gpu_sensor_failed:
                    log("GPU temperature sensor recovered")
                gpu_sensor_failed = False
                gpu_target = gpu_pwm_for_temp(gpu_temp, current_gpu_pwm)
            except (OSError, ValueError, subprocess.SubprocessError) as exc:
                if not gpu_sensor_failed:
                    log(f"GPU temperature unavailable ({exc}); forcing full GPU fan")
                gpu_sensor_failed = True
                gpu_temp = None
                gpu_target = GPU_FAILSAFE_PWM

            now = time.monotonic()
            gpu_changed = gpu_target != current_gpu_pwm
            if (gpu_changed or
                    now - last_gpu_write >= GPU_REASSERT_SECONDS):
                write(gpu_enable_path, 1)
                write(gpu_pwm_path, gpu_target)
                current_gpu_pwm = gpu_target
                last_gpu_write = now

            # Periodic telemetry, plus an immediate report when either PWM
            # target changes. Tachometer read failures must not stop cooling.
            if (cpu_changed or gpu_changed or
                    now - last_status_log >= STATUS_LOG_SECONDS):
                cpu_rpm = rpm_for_log(cpu_rpm_path)
                gpu_rpm = rpm_for_log(gpu_rpm_path, gpu=True)
                if gpu_rpm.startswith("N/A"):
                    estimated_rpm = GPU_RPM_AT_PWM.get(gpu_target)
                    if estimated_rpm is not None:
                        gpu_rpm = f"~{estimated_rpm} (PWM estimate; EC tach unavailable)"
                gpu_temp_str = (f"{gpu_temp:.1f}C" if gpu_temp is not None
                                else "N/A")
                log(f"CPU temp={cpu_temp:.1f}C rpm={cpu_rpm} pwm1={cpu_target} | "
                    f"GPU temp={gpu_temp_str} rpm={gpu_rpm} pwm2={gpu_target}")
                last_status_log = now

            time.sleep(POLL_SECONDS)

    finally:
        # Never deliberately hand this replacement GPU's fan back to the EC:
        # on this machine EC automatic mode was observed to STOP the blower.
        try:
            write(gpu_enable_path, 1)
            write(gpu_pwm_path, GPU_FAILSAFE_PWM)
            log("GPU blower left at full PWM on exit")
        except OSError as exc:
            print(f"clevo-fan-control: FAILED to secure GPU blower: {exc}", file=sys.stderr)

        # Preserve the CPU's original shutdown behavior.
        try:
            write(cpu_enable_path, 2)
        except OSError as exc:
            print(f"clevo-fan-control: failed to restore CPU EC auto: {exc}", file=sys.stderr)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"clevo-fan-control: {exc}", file=sys.stderr)
        sys.exit(1)
