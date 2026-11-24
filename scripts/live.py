#!/usr/bin/env python3
"""Live Mode: the edit sent straight into the running Final Cut Pro, with no library question, by fcp-mcp-server
(https://github.com/DareDev256/fcp-mcp-server, MIT), an MCP server run on demand with uvx and pinned to one version
("live.package"). It writes a copy of the FCPXML that names the library, sends it with Apple's Open Document event and
checks that the project arrived. Optional ("live.enabled"): without uv, or when the push is refused, the caller opens
the file the usual way.
Usage: live.py push file.fcpxml [--library name or .fcpbundle]   |   live.py libraries   |   live.py tools"""
import argparse, json, os, re, shutil, subprocess, sys, threading
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import settings

class LiveError(Exception):
    """The push did not happen; the message says why, in plain words."""

def uvx():
    for p in (os.environ.get('ROUGHCUT_UVX'), shutil.which('uvx'), os.path.expanduser('~/.local/bin/uvx'),
              '/opt/homebrew/bin/uvx', '/usr/local/bin/uvx'):
        if p and os.path.exists(p):
            return p
    return None

class Server:
    """One conversation with the server over stdio: JSON-RPC, one message per line (the MCP stdio transport)."""
    def __init__(self, package, folder, timeout=180):
        exe = uvx()
        if not exe:
            raise LiveError('uv is not installed (https://docs.astral.sh/uv/): it runs fcp-mcp-server')
        if not re.fullmatch(r'fcp-mcp-server==\d+(\.\d+)*', package):  # one known release, never another package or a URL
            raise LiveError(f'live.package must be fcp-mcp-server==<version>, not {package!r}')
        # the server only reads and writes inside the folders it is given: the project's; and it is given no key
        env = {k: v for k, v in os.environ.items() if not re.search(r'KEY|TOKEN|SECRET|PASSWORD', k, re.I)}
        env.update(FCP_PROJECTS_DIR=folder, FCP_PROJECTS_DIRS=folder)
        try:
            self.p = subprocess.Popen([exe, '--from', package, 'fcp-mcp-server'], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                      stderr=subprocess.DEVNULL, text=True, env=env)
        except OSError as e:
            raise LiveError(f'cannot run uvx ({e})')
        self.n = 0
        self.timer = threading.Timer(timeout, self.p.kill)  # a server that never answers does not hold the edit
        self.timer.daemon = True
        self.timer.start()
        try:
            self.ask('initialize', {'protocolVersion': '2025-06-18', 'capabilities': {},
                                    'clientInfo': {'name': 'roughcut', 'version': os.environ.get('ROUGHCUT_VERSION', '0')}})
            self.send({'jsonrpc': '2.0', 'method': 'notifications/initialized'})
        except LiveError:
            self.close()
            raise

    def send(self, message):
        try:
            self.p.stdin.write(json.dumps(message) + '\n')
            self.p.stdin.flush()
        except OSError:
            raise LiveError('fcp-mcp-server stopped')

    def ask(self, method, params=None):
        self.n += 1
        self.send({'jsonrpc': '2.0', 'id': self.n, 'method': method, 'params': params or {}})
        for line in self.p.stdout:
            try:
                m = json.loads(line)
            except ValueError:
                continue
            if not isinstance(m, dict) or m.get('id') != self.n or 'method' in m:  # its own notifications and requests
                continue
            if 'error' in m:
                e = m['error']
                raise LiveError(str(e.get('message', e) if isinstance(e, dict) else e)[:400])
            return m['result'] if isinstance(m.get('result'), dict) else {}
        raise LiveError('fcp-mcp-server gave no answer (not downloaded yet and offline, or too slow)')

    def call(self, tool, **arguments):
        """The text a tool answers; LiveError when the tool says it failed."""
        r = self.ask('tools/call', {'name': tool, 'arguments': arguments})
        content = r.get('content') if isinstance(r.get('content'), list) else []
        text = '\n'.join(str(c.get('text', '')) for c in content if isinstance(c, dict) and c.get('type') == 'text').strip()
        if r.get('isError'):
            raise LiveError(text[:400] or f'{tool} failed')
        return text

    def close(self):
        self.timer.cancel()
        try:
            self.p.stdin.close()
            self.p.wait(timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            self.p.kill()
        self.p.stdout.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

def push(fcpxml, library, package):
    """The file imported by fcp-mcp-server's push_to_fcp; returns what it reports. With no library, the server takes
    the one library open in Final Cut Pro, and refuses when there are several or none."""
    path = os.path.abspath(fcpxml)
    # the edit was watched by visual_review.py or by nobody: the server's own preview gate is not Roughcut's
    args = {'filepath': path, 'confirm_unreviewed': True}
    if library:
        args['library_location'] = library
    with Server(package, os.path.dirname(path)) as s:
        text = s.call('deliver', action='push_to_fcp', args=args)
    if not text.startswith(('Imported into', 'Sent to')):
        raise LiveError(text[:400] or 'no answer')
    return ' '.join(text.splitlines()[:2])

def deliver(path, cfg, say=print):
    """Puts the edit in Final Cut Pro, the surest way first: by fcp-mcp-server (checked import), else by a copy that
    names the library (fcp.import_copy: no question either, with any Final Cut Pro), else opened as it is (Final Cut
    Pro asks which library). Returns 'pushed' when the edit was sent with its library named, 'opened' otherwise."""
    import fcp
    ok, msg = fcp.check(path)
    if ok is False:
        raise SystemExit(f'Not sent, the FCPXML is invalid:\n{msg}')
    library = fcp.library_path(cfg['auto']['library'])
    app = fcp.find_app()
    if app and os.path.basename(app) == 'Final Cut Pro.app':
        try:
            say('live: ' + push(path, library, cfg['live']['package']))
            return 'pushed'
        except Exception as e:  # whatever the server does, the edit is still delivered
            say(f'live: fcp-mcp-server did not push ({e})')
    elif app:  # the server addresses the app by that one name
        say(f'live: fcp-mcp-server only drives "Final Cut Pro", not {os.path.basename(app)[:-4]}')
    fcp.open_in_fcp(path, library)
    say(f'live: opened with the library {library}' if library else 'live: opened, no library set (auto.library)')
    return 'pushed' if library else 'opened'

def main():
    parser = argparse.ArgumentParser(description='Sends an FCPXML straight into the running Final Cut Pro (fcp-mcp-server).')
    parser.add_argument('action', choices=['push', 'libraries', 'tools'])
    parser.add_argument('file', nargs='?', help='.fcpxml to push')
    parser.add_argument('--library', help='name of the library, or path of its .fcpbundle (default: auto.library)')
    parser.add_argument('--config', help='settings file on top of config/defaults.json and the roughcut.json files found')
    args = parser.parse_args()
    folder = os.path.dirname(os.path.abspath(args.file)) if args.file else os.getcwd()
    cfg = settings.load(folder, args.config)
    try:
        if args.action == 'push':
            if not args.file:
                raise SystemExit('Which file?')
            if args.library:
                cfg['auto']['library'] = args.library
            print(deliver(args.file, cfg))
            return
        with Server(cfg['live']['package'], folder) as s:
            if args.action == 'tools':  # proves the server starts, without touching Final Cut Pro
                print('\n'.join(t['name'] for t in s.ask('tools/list').get('tools', [])))
            else:
                print(s.call('deliver', action='list_fcp_libraries', args={}))
    except LiveError as e:
        raise SystemExit(f'Live Mode: {e}')

if __name__ == '__main__':
    main()
