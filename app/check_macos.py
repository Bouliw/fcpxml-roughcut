#!/usr/bin/env python3
"""Checks that every program and library in a folder runs on the oldest macOS Roughcut supports: each Mach-O file
must have an Apple silicon slice (no Rosetta on a new Mac) whose minimum macOS (LC_BUILD_VERSION minos) is not newer
than the target. build_app.sh runs it on the app, build_runtime.sh on the engine's tools.
Usage: check_macos.py folder [--target 14.0]"""
import argparse, os, re, subprocess, sys

MAGICS = {b'\xcf\xfa\xed\xfe', b'\xce\xfa\xed\xfe', b'\xca\xfe\xba\xbe', b'\xbe\xba\xfe\xca'}  # thin 64/32, fat

def version(text):
    return tuple(int(x) for x in text.split('.'))

def minimum(path):
    """The minimum macOS of the arm64 slice, or None when there is no arm64 slice."""
    r = subprocess.run(['otool', '-arch', 'arm64', '-l', path], capture_output=True, text=True)
    if r.returncode or 'Load command' not in r.stdout:
        return None
    m = re.search(r'cmd LC_BUILD_VERSION.*?minos (\S+)', r.stdout, re.S) or re.search(r'cmd LC_VERSION_MIN_MACOSX.*?version (\S+)', r.stdout, re.S)
    return m.group(1) if m else '0.0'

def main():
    parser = argparse.ArgumentParser(description='Checks the minimum macOS of every binary in a folder.')
    parser.add_argument('folder')
    parser.add_argument('--target', default='14.0')
    args = parser.parse_args()
    target, bad, counts = version(args.target), [], {}
    for root, dirs, files in os.walk(args.folder):
        dirs[:] = [d for d in dirs if not d.endswith('.dSYM')]  # debug symbols never load
        for name in files:
            path = os.path.join(root, name)
            if os.path.islink(path):
                continue
            with open(path, 'rb') as f:
                if f.read(4) not in MAGICS:
                    continue
            m = minimum(path)
            counts[m] = counts.get(m, 0) + 1
            if m is None or version(m) > target:
                bad.append(f'{os.path.relpath(path, args.folder)}: {"no Apple silicon code" if m is None else "needs macOS " + m}')
    print(f'{sum(counts.values())} binaries, minimum macOS: ' + ', '.join(f'{k or "none"} ({v})' for k, v in sorted(counts.items(), key=lambda kv: version(kv[0] or "0"))))
    if bad:
        print('\n'.join(bad))
        raise SystemExit(f'{len(bad)} binaries would not run on macOS {args.target}')
    print(f'all run on macOS {args.target} or later')

if __name__ == '__main__':
    main()
