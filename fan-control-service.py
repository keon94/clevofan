#!/usr/bin/env python3

import glob
import os
import signal
import sys
import time

POLL_SECONDS = 2
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

running = True


def read(path):
    with open(path, "r") as f:
        return f.read().strip()


def write(path, value):
    with open(path, "w") as f:
        f.write(str(value))


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


def handle_signal(signum, frame):
    global running
    running = False


def main():
    global running

    fan_hwmon = find_clevo_hwmon()
    temp_path = find_cpu_temp()

    pwm_path = os.path.join(fan_hwmon, "pwm1")
    enable_path = os.path.join(fan_hwmon, "pwm1_enable")

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    fan_running = False
    current_pwm = None

    try:
        while running:
            temp_c = int(read(temp_path)) / 1000.0

            if temp_c >= EMERGENCY_TEMP:
                target_pwm = 255
                fan_running = True
            elif fan_running:
                if temp_c <= FAN_OFF_TEMP:
                    target_pwm = 0
                    fan_running = False
                else:
                    target_pwm = max(80, pwm_for_temp(temp_c))
            else:
                if temp_c >= FAN_ON_TEMP:
                    target_pwm = max(80, pwm_for_temp(temp_c))

                    write(enable_path, 1)
                    write(pwm_path, KICK_PWM)
                    time.sleep(KICK_SECONDS)

                    fan_running = True
                else:
                    target_pwm = 0

            write(enable_path, 1)

            if target_pwm != current_pwm:
                write(pwm_path, target_pwm)
                current_pwm = target_pwm

            time.sleep(POLL_SECONDS)

    finally:
        # Return control to the Clevo EC.
        try:
            write(enable_path, 2)
        except OSError:
            pass


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"clevo-fan-control: {exc}", file=sys.stderr)
        sys.exit(1)
