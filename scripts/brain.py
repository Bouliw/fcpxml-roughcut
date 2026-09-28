#!/usr/bin/env python3
"""The brain: the few decisions that need judgement (which takes to keep, where the B-roll goes, the Short's
extract, the titles), asked to one of three engines chosen once in the settings ("brain.engine"):
- claude: Claude Code's `claude -p`, on the user's Claude subscription;
- local: any OpenAI-compatible server (LM Studio, Ollama), free and private: nothing leaves the Mac;
- api: an Anthropic or OpenAI key, paid per use, kept in the macOS keychain (never in a file).
Transcription, image analysis, sound and the FCPXML are done locally whatever the brain.
When the claude engine does not answer (usage limit reached, not logged in), the local one takes over if it is set
up, and the caller is told so it can say it.
Usage: brain.py choose [--config-dir dir]   (the first-launch question, again)
       brain.py set-key anthropic|openai    (stores an API key in the keychain)
       brain.py test [--config-dir dir]     (asks the brain for a two-word answer)"""
import argparse, json, os, re, shutil, subprocess, sys, tempfile, urllib.error, urllib.request
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import settings

KEYCHAIN = 'fcpxml-roughcut'
ENGINES = ('claude', 'local', 'api')
TEXT = {
    'en': {'claude': 'Claude (Claude Code): best quality, included in your Claude subscription; transcripts go to Anthropic.',
           'local': 'Local (LM Studio, Ollama): free and private, nothing leaves the Mac; a little less sharp, needs a 15-25 GB model.',
           'api': 'API (Claude or OpenAI key): very good quality, paid per use (cents per video); transcripts go to the provider.',
           'choose': 'Which brain should make the editing choices? You can change it later in roughcut.json.',
           'provider': 'Which API?', 'key': 'Paste your {p} API key (it goes to the macOS keychain, never to a file):',
           'model': 'OpenAI model to use (e.g. the one your plan offers):',
           'quota': 'usage limit reached', 'slow': 'too slow', 'missing': 'not installed', 'error': 'an error',
           'handover': 'Claude did not answer ({why}): the {engine} brain made the choices'},
    'fr': {'claude': 'Claude (Claude Code) : la meilleure qualité, compris dans ton abonnement Claude ; les transcriptions partent chez Anthropic.',
           'local': 'Local (LM Studio, Ollama) : gratuit et privé, rien ne quitte le Mac ; un peu moins fin, demande un modèle de 15 à 25 Go.',
           'api': 'API (clé Claude ou OpenAI) : très bonne qualité, payé à l\'usage (quelques centimes par vidéo) ; les transcriptions partent chez le fournisseur.',
           'choose': 'Quel cerveau fait les choix de montage ? Tu pourras le changer ensuite dans roughcut.json.',
           'provider': 'Quelle API ?', 'key': 'Colle ta clé API {p} (elle va dans le trousseau macOS, jamais dans un fichier) :',
           'model': 'Modèle OpenAI à utiliser (celui de ton abonnement) :',
           'quota': 'quota atteint', 'slow': 'trop lent', 'missing': 'pas installé', 'error': 'une erreur',
           'handover': 'Claude n\'a pas répondu ({why}) : le cerveau {engine} a fait les choix'},
}

class BrainError(Exception):
    """The engine did not give an answer; `quota` when it looks like a usage limit, `slow` when it took too long."""
    def __init__(self, message, quota=False, slow=False):
        super().__init__(message)
        self.quota, self.slow = quota, slow

def _limit(text):
    return bool(re.search(r'usage limit|rate limit|limit reached|quota|credit balance|out of credits|overloaded', text or '', re.I))

# ---------------------------------------------------------------- engines

def ask_claude(prompt, b):
    """`claude -p`, no tools, no MCP servers, run from an empty folder so no project settings or hooks load."""
    exe = os.environ.get('ROUGHCUT_CLAUDE') or shutil.which('claude') or os.path.expanduser('~/.local/bin/claude')  # the app says where
    if not os.path.exists(exe):
        raise BrainError('Claude Code (claude) is not installed')
    cmd = [exe, '-p', '--output-format', 'json', '--no-session-persistence', '--tools', '', '--strict-mcp-config']
    if b.get('claude_model'):
        cmd += ['--model', b['claude_model']]
    with tempfile.TemporaryDirectory() as tmp:
        try:
            r = subprocess.run(cmd, input=prompt, capture_output=True, text=True, cwd=tmp, timeout=b['timeout_seconds'])
        except subprocess.TimeoutExpired:
            raise BrainError(f'claude did not answer within {b["timeout_seconds"]} s', slow=True)
    try:
        out = json.loads(r.stdout)
    except ValueError:
        raise BrainError(f'claude: {(r.stderr or r.stdout).strip()[-300:]}', _limit(r.stderr + r.stdout))
    if r.returncode or out.get('is_error'):
        message = str(out.get('result') or out.get('subtype') or r.stderr)[-300:]
        raise BrainError(f'claude: {message}', _limit(message) or out.get('api_error_status') == 429)
    return out['result']

def _post_json(url, body, headers, timeout):
    req = urllib.request.Request(url, data=json.dumps(body).encode('utf-8'),
                                 headers={'Content-Type': 'application/json', **headers}, method='POST')
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        detail = e.read().decode('utf-8', 'replace')[-300:]
        raise BrainError(f'{url}: HTTP {e.code} {detail}', e.code == 429 or _limit(detail))
    except (urllib.error.URLError, OSError) as e:
        reason = getattr(e, 'reason', e)
        raise BrainError(f'{url}: {reason}', slow=isinstance(reason, TimeoutError) or 'timed out' in str(reason))

def ask_openai_compatible(prompt, url, model, key, timeout):
    """One chat completion from any OpenAI-compatible server (LM Studio, Ollama, OpenAI)."""
    if not model:
        raise BrainError('no model set (brain.local_model or brain.api_model)')
    out = _post_json(url.rstrip('/') + '/chat/completions',
                     {'model': model, 'messages': [{'role': 'user', 'content': prompt}], 'temperature': 0.2},
                     {'Authorization': f'Bearer {key}'} if key else {}, timeout)
    try:
        text = out['choices'][0]['message']['content'] or ''
    except (KeyError, IndexError, TypeError):
        raise BrainError(f'unexpected answer from {url}: {str(out)[:200]}')
    return re.sub(r'<think>.*?</think>', '', text, flags=re.S).strip()  # reasoning models think out loud first

def load_local_model(b):
    """LM Studio loads a model on the first request with a short context: load it first, with room for transcripts."""
    lms = shutil.which('lms') or os.path.expanduser('~/.lmstudio/bin/lms')
    if not (b.get('local_model') and os.path.exists(lms) and ':1234' in b['local_url']):
        return
    loaded = subprocess.run([lms, 'ps'], capture_output=True, text=True).stdout
    if b['local_model'] not in loaded:
        subprocess.run([lms, 'load', b['local_model'], '--context-length', str(b['local_context']), '-y'],
                       capture_output=True, text=True, timeout=600)

def ask_local(prompt, b):
    load_local_model(b)
    return ask_openai_compatible(prompt, b['local_url'], b['local_model'], None, b['timeout_seconds'])

def keychain_get(provider):
    r = subprocess.run(['security', 'find-generic-password', '-s', KEYCHAIN, '-a', provider, '-w'], capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else None

def keychain_set(provider, key):
    subprocess.run(['security', 'add-generic-password', '-s', KEYCHAIN, '-a', provider, '-w', key, '-U'], check=True,
                   capture_output=True)

def ask_anthropic(prompt, b, key):
    try:
        import anthropic
    except ImportError:
        raise BrainError('the Anthropic SDK is not installed: pip install anthropic')
    client = anthropic.Anthropic(api_key=key, base_url=b.get('api_url') or None, timeout=b['timeout_seconds'])
    try:
        # streaming for a long input; refusals fall back to another model on the server side
        with client.beta.messages.stream(model=b.get('api_model') or 'claude-opus-5', max_tokens=64000,
                                         betas=['server-side-fallback-2026-07-01'], fallbacks='default',
                                         thinking={'type': 'adaptive'}, output_config={'effort': 'high'},
                                         messages=[{'role': 'user', 'content': prompt}]) as stream:
            message = stream.get_final_message()
    except anthropic.AuthenticationError:
        raise BrainError('the Anthropic API key was refused')
    except anthropic.RateLimitError as e:
        raise BrainError(f'Anthropic API rate limit: {e.message}', quota=True)
    except anthropic.APIStatusError as e:
        raise BrainError(f'Anthropic API error {e.status_code}: {e.message}', _limit(e.message))
    except anthropic.APIConnectionError:
        raise BrainError('cannot reach the Anthropic API')
    if message.stop_reason == 'refusal':
        raise BrainError('the model declined the request')
    return ''.join(block.text for block in message.content if block.type == 'text')

def ask_api(prompt, b):
    provider = b['api_provider']
    key = os.environ.get('ROUGHCUT_API_KEY') or keychain_get(provider)  # the app reads the keychain and passes it on
    if not key:
        raise BrainError(f'no {provider} key in the keychain: brain.py set-key {provider}')
    if provider == 'anthropic':
        return ask_anthropic(prompt, b, key)
    return ask_openai_compatible(prompt, b.get('api_url') or 'https://api.openai.com/v1', b['api_model'], key, b['timeout_seconds'])

RUN = {'claude': ask_claude, 'local': ask_local, 'api': ask_api}

def handover_note(first, engine, lang='en'):
    """What to tell the user when another engine answered for Claude."""
    t = TEXT.get(lang, TEXT['en'])  # words anyone understands; the technical reason goes to the log
    why = 'quota' if first.quota else 'slow' if first.slow else 'missing' if 'not installed' in str(first) else 'error'
    return t['handover'].format(why=t[why], engine=engine)

def ask(prompt, cfg):
    """(answer, engine that answered, note for the user or None). The configured engine first; when claude does not
    answer, the fallback engine (local) if one is set up; the note says so, in the language of the settings."""
    b = cfg['brain']
    engine = b['engine'] or 'claude'
    try:
        return RUN[engine](prompt, b), engine, None
    except BrainError as first:
        fallback = b.get('fallback')
        if engine != 'claude' or not fallback or fallback == engine:
            raise
        try:
            answer = RUN[fallback](prompt, b)
        except BrainError as second:
            raise BrainError(f'{first}; then {fallback}: {second}', first.quota)
        return answer, fallback, handover_note(first, fallback, cfg.get('ui', {}).get('language', 'en'))

def extract_json(text):
    """The JSON object in an answer, even wrapped in a code fence or a sentence."""
    text = re.sub(r'^```(?:json)?\s*|\s*```$', '', text.strip(), flags=re.M)
    start, end = text.find('{'), text.rfind('}')
    if start < 0 or end < start:
        raise BrainError('no JSON object in the answer')
    try:
        return json.loads(text[start:end + 1])
    except ValueError as e:
        raise BrainError(f'the answer is not valid JSON: {e}')

def ask_json(prompt, cfg):
    text, engine, note = ask(prompt, cfg)
    return extract_json(text), engine, note

# ---------------------------------------------------------------- first launch

DIALOG_ERRORS = []  # why a dialog could not be shown (not a cancel), for the log

def applescript(text):
    """An AppleScript string literal. Only backslashes and double quotes are escaped: accented letters go as they
    are (JSON's \\u00e9 is a syntax error in AppleScript, so no French question or notification would show)."""
    return '"' + str(text).replace('\\', '\\\\').replace('"', '\\"') + '"'

def _dialog(script, timeout=None):
    """Runs an AppleScript dialog in front of the other windows: ('ok', answer), ('cancel', None), ('timeout', None)
    or ('error', message)."""
    try:
        r = subprocess.run(['osascript', '-e', 'activate', '-e', script], capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return 'timeout', None
    except OSError as e:
        DIALOG_ERRORS.append(str(e))
        return 'error', str(e)
    if r.returncode == 0:
        return 'ok', r.stdout.strip()
    if '-128' in r.stderr:  # the user cancelled
        return 'cancel', None
    DIALOG_ERRORS.append(r.stderr.strip() or f'osascript exit {r.returncode}')
    return 'error', DIALOG_ERRORS[-1]

def dialog_choose(items, prompt, default=None, timeout=None):
    """macOS list dialog; returns the chosen item, None when cancelled or impossible, and the default when nobody
    answers within `timeout` seconds (an edit started from the Finder must not wait forever)."""
    script = (f'choose from list {{{", ".join(applescript(i) for i in items)}}} with prompt {applescript(prompt)}'
              + (f' default items {{{applescript(default)}}}' if default else ''))
    status, out = _dialog(script, timeout)
    if status == 'timeout':
        return default
    return out if status == 'ok' and out != 'false' else None

def dialog_folder(prompt, timeout=None):
    """macOS folder dialog: (status, POSIX path or None), status as in _dialog."""
    return _dialog(f'POSIX path of (choose folder with prompt {applescript(prompt)})', timeout)

def dialog_text(prompt, hidden=False):
    status, out = _dialog(f'text returned of (display dialog {applescript(prompt)} default answer ""'
                          + (' with hidden answer' if hidden else '') + ')')
    return out if status == 'ok' else None

def save_setting(config_dir, section, values):
    """Writes values into <config_dir>/roughcut.json, keeping everything else in it."""
    path = os.path.join(config_dir, 'roughcut.json')
    data = {}
    if os.path.exists(path):
        with open(path, encoding='utf-8') as f:
            data = json.load(f)
    data.setdefault(section, {}).update(values)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write('\n')
    return path

def choose(config_dir, lang='en'):
    """The first-launch question: three engines, one line each. Saves the choice; returns the engine or None."""
    t = TEXT.get(lang, TEXT['en'])
    items = [t['claude'], t['local'], t['api']]
    picked = dialog_choose(items, t['choose'], items[0])
    if not picked:
        return None
    engine = ENGINES[items.index(picked)]
    values = {'engine': engine}
    if engine == 'api':
        provider = dialog_choose(['Claude (Anthropic)', 'OpenAI'], t['provider'], 'Claude (Anthropic)')
        if not provider:
            return None
        provider = 'anthropic' if provider.startswith('Claude') else 'openai'
        key = dialog_text(t['key'].format(p='Claude' if provider == 'anthropic' else 'OpenAI'), hidden=True)
        if not key:
            return None
        keychain_set(provider, key)
        values['api_provider'] = provider
        if provider == 'openai':
            values['api_model'] = dialog_text(t['model']) or ''
    save_setting(config_dir, 'brain', values)
    return engine

def main():
    parser = argparse.ArgumentParser(description='The brain that makes the editing choices: choose it, store a key, test it.')
    parser.add_argument('action', choices=['choose', 'set-key', 'test'])
    parser.add_argument('provider', nargs='?', choices=['anthropic', 'openai'])
    parser.add_argument('--config-dir', default='.', help='folder of the roughcut.json to write (default: here)')
    args = parser.parse_args()
    cfg = settings.load(os.path.abspath(args.config_dir))
    if args.action == 'choose':
        print(choose(os.path.abspath(args.config_dir), cfg['ui']['language']) or 'nothing chosen')
    elif args.action == 'set-key':
        import getpass
        if not args.provider:
            raise SystemExit('Which provider: anthropic or openai?')
        keychain_set(args.provider, getpass.getpass(f'{args.provider} API key (not shown): ').strip())
        print(f'Stored in the keychain ({KEYCHAIN} / {args.provider})')
    else:
        answer, engine, note = ask_json('Answer with this JSON only: {"ok": true, "word": "<one word>"}', cfg)
        print(f'{engine}: {answer}' + (f' ({note})' if note else ''))

if __name__ == '__main__':
    main()
