"""Settings: config/defaults.json, then every roughcut.json from the filesystem root down to the project folder
(so one file in a parent folder covers all its projects), then an explicit file. Unknown keys are refused, so a
typo never passes silently."""
import json, os

DEFAULTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, 'config', 'defaults.json')

def _read(path):
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError) as e:
        raise SystemExit(f'Cannot read the settings {path}: {e}')

def _merge(base, over, path, where=''):
    out = dict(base)
    for k, v in over.items():
        if k not in base:
            raise SystemExit(f'Unknown setting "{where}{k}" in {path} (known: {", ".join(sorted(base))})')
        out[k] = _merge(base[k], v, path, f'{where}{k}.') if isinstance(base[k], dict) else v
    return out

def load(project_dir=None, path=None):
    cfg = _read(DEFAULTS)
    files = []
    if project_dir:
        d = os.path.abspath(project_dir)
        while True:
            files.insert(0, os.path.join(d, 'roughcut.json'))
            if os.path.dirname(d) == d:
                break
            d = os.path.dirname(d)
    if path:
        files.append(path)
    for p in files:
        if os.path.isfile(p) or p == path:
            cfg = _merge(cfg, _read(p), p)
    return cfg
