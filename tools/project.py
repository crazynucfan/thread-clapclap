#!/usr/bin/env python3
"""Build, test and flash Thread ClapClap using the pinned Arduino ESP32 core."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
CORE = "3.3.12"
FQBN = "esp32:esp32:XIAO_ESP32C6:PartitionScheme=huge_app,CDCOnBoot=cdc"
INDEX = "https://espressif.github.io/arduino-esp32/package_esp32_index.json"
SKETCH = ROOT / "firmware" / "ThreadClapClap"
OUTPUT = ROOT / ".build" / "firmware"


def run(command):
    print("+", " ".join(map(str, command)), flush=True)
    subprocess.run(list(map(str, command)), cwd=ROOT, check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cli", default=os.environ.get("ARDUINO_CLI", "arduino-cli"))
    parser.add_argument("--cli-config", type=Path, help="optional isolated Arduino CLI configuration")
    parser.add_argument("--build-dir", type=Path, default=ROOT / ".build" / "arduino")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("test", help="run hardware-independent C++ detector tests")
    sub.add_parser("setup", help=f"install Arduino ESP32 {CORE} (downloads the toolchain)")
    sub.add_parser("build", help="compile firmware")
    sub.add_parser("ports", help="list connected boards")
    flash = sub.add_parser("flash", help="upload a previously built firmware")
    flash.add_argument("--port", required=True, help="e.g. COM7 or /dev/ttyACM0")
    monitor = sub.add_parser("monitor", help="open the 115200 baud serial console")
    monitor.add_argument("--port", required=True)
    args = parser.parse_args()

    if args.command == "test":
        compiler = os.environ.get("CXX", "g++")
        if not shutil.which(compiler):
            parser.error(f"{compiler} not found; install a C++ compiler or set CXX=clang++")
        for source in sorted((ROOT / "tests").glob("*_test.cpp")):
            binary = ROOT / ".build" / source.stem
            binary.parent.mkdir(parents=True, exist_ok=True)
            run([compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror", "-pedantic",
                 source, "-o", binary])
            run([binary])
        run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-p", "*_test.py"])
        return

    if not shutil.which(args.cli):
        parser.error("Arduino CLI not found; install it from https://arduino.github.io/arduino-cli/")
    cli = [args.cli]
    if args.cli_config:
        cli += ["--config-file", str(args.cli_config.resolve())]

    if args.command == "setup":
        run(cli + ["core", "update-index", "--additional-urls", INDEX])
        run(cli + ["core", "install", f"esp32:esp32@{CORE}", "--additional-urls", INDEX])
    elif args.command == "ports":
        run(cli + ["board", "list"])
    elif args.command == "monitor":
        run(cli + ["monitor", "--port", args.port, "--config", "baudrate=115200"])
    else:
        installed = json.loads(subprocess.check_output(cli + ["core", "list", "--format", "json"], text=True))
        platforms = installed.get("platforms", [])
        if not any(p.get("id") == "esp32:esp32" and p.get("installed_version") == CORE for p in platforms):
            parser.error(f"Arduino ESP32 {CORE} is required; run tools/project.py setup first")
        if args.command == "build":
            digest = hashlib.sha256()
            for name in ("ImpulseClapDetector.h", "Config.h"):
                digest.update((SKETCH / name).read_bytes())
            detector_id = int(digest.hexdigest()[:8], 16)
            run(cli + ["compile", "--fqbn", FQBN,
                       "--build-property", f"compiler.cpp.extra_flags=-DCLAP_DETECTOR_BUILD_ID={detector_id}u",
                       "--build-path", args.build_dir.resolve(),
                       "--output-dir", OUTPUT, SKETCH])
        elif args.command == "flash":
            if not (OUTPUT / "ThreadClapClap.ino.bin").is_file():
                parser.error("no firmware found; run tools/project.py build first")
            run(cli + ["upload", "--fqbn", FQBN, "--port", args.port, "--input-dir", OUTPUT, SKETCH])


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as error:
        sys.exit(error.returncode)
    except KeyboardInterrupt:
        sys.exit(130)
