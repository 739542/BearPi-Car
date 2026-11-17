#!/usr/bin/env python3
"""Run inside the existing Linux SDK environment. Never export a failed build."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys


def main():
    import fcntl
    package = Path(__file__).resolve().parent
    sdk = Path(sys.argv[1] if len(sys.argv) > 1 else '~/project/bearpi-hm_nano').expanduser().resolve()
    if not (sdk / 'build.py').is_file():
        raise SystemExit('SDK not found: ' + str(sdk))
    sample = sdk / 'applications/BearPi/BearPi-HM_Nano/sample'
    target = sample / 'my_tasks/car_motor_test'
    env = os.environ.copy()
    env['PATH'] = ':'.join([str(Path.home() / p) for p in ('gcc_riscv32/bin', 'gn', 'ninja')] + [env.get('PATH', '')])
    for tool in ('riscv32-unknown-elf-gcc', 'gn', 'ninja'):
        if not shutil.which(tool, path=env['PATH']):
            raise SystemExit('Missing build tool: ' + tool)
    output = sdk / 'out/car_stage1'
    output.mkdir(parents=True, exist_ok=True)
    with (sdk / 'out/task_lab_build.lock').open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        build = sample / 'BUILD.gn'
        original = build.read_bytes()
        names = re.findall(r'lite_component\s*\(\s*"([^"]+)"\s*\)', original.decode('utf-8'))
        if len(names) != 1:
            raise SystemExit('Expected one lite_component in sample/BUILD.gn; left unchanged.')
        target.mkdir(parents=True, exist_ok=True)
        for name in ('motor_test.c', 'wireless.c', 'wifi_local.h', 'web_assets.h', 'BUILD.gn'):
            source = package / name
            dest = target / name
            if source.resolve() != dest.resolve():
                if dest.exists() and dest.read_bytes() != source.read_bytes():
                    shutil.copy2(str(dest), str(output / (name + '.previous')))
                shutil.copy2(str(source), str(dest))
        (output / 'sample_BUILD.gn.before').write_bytes(original)
        artifact = sdk / 'out/BearPi-HM_Nano/Hi3861_wifiiot_app_allinone.bin'
        exported = output / artifact.name
        if exported.exists():
            exported.replace(output / (artifact.name + '.previous'))
        manifest = output / 'manifest.json'
        if manifest.exists():
            manifest.replace(output / 'manifest.previous.json')
        if artifact.exists():
            artifact.replace(output / (artifact.name + '.sdk_previous'))
        try:
            build.write_text('import("//build/lite/config/component/lite_component.gni")\n'
                             'lite_component("' + names[0] + '") {\n'
                             '  features = [ "my_tasks/car_motor_test:car_motor_test" ]\n}\n', encoding='utf-8')
            with (output / 'build.log').open('w', encoding='utf-8') as log:
                process = subprocess.Popen([sys.executable, 'build.py', 'BearPi-HM_Nano'], cwd=str(sdk), env=env,
                                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)
                try:
                    for line in process.stdout:
                        print(line, end='', flush=True)
                        log.write(line)
                    result = process.wait()
                except BaseException:
                    process.terminate()
                    process.wait()
                    raise
            if result != 0 or not artifact.is_file() or artifact.stat().st_size == 0:
                raise SystemExit('BUILD FAILED. No new firmware exported. See ' + str(output / 'build.log'))
            shutil.copy2(str(artifact), str(exported))
            manifest.write_text(json.dumps({'program': 'car-stage1-v12',
                'sha256': hashlib.sha256(exported.read_bytes()).hexdigest(),
                'source_sha256': hashlib.sha256((target / 'motor_test.c').read_bytes()).hexdigest(),
                'hardware_test': 'NOT_RUN'}, indent=2), encoding='utf-8')
            print('\nBUILD OK: ' + str(exported))
        finally:
            build.write_bytes(original)
            print('Original sample/BUILD.gn restored.')


if __name__ == '__main__':
    main()
