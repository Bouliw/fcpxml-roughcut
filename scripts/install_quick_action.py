#!/usr/bin/env python3
"""Installs a Finder Quick Action (right click on a folder of footage > Quick Actions > the name you give): it starts
auto_edit.py on that folder in the background, so the Finder is free at once; a notification says when the project is
ready and it opens in Final Cut Pro.
With --app, the Quick Action runs the engine of the Roughcut app installed (its launcher, its tools, its settings and
projects folder), so the Finder and the app always edit with the same engine.
Usage: install_quick_action.py [--name "Edit this footage"] [--app /Applications/Roughcut.app]
                               [--work-dir ~/Movies/Roughcut] [--python python3]"""
import argparse, os, plistlib, shlex, subprocess, sys, uuid

HERE = os.path.dirname(os.path.abspath(__file__))

def app_command(app):
    """The Quick Action of the Roughcut app: its launcher sets up everything as the app does."""
    launcher = os.path.join(app, 'Contents', 'Resources', 'engine', 'roughcut')
    return (f'L={shlex.quote(launcher)}\n'
            'for f in "$@"; do\n'
            '  if [ -f "$L" ]; then nohup /bin/bash "$L" auto_edit.py "$f" >/dev/null 2>&1 &\n'
            '  else osascript -e \'display notification "Install Roughcut in Applications, then try again." with title "Roughcut"\'; fi\n'
            'done\n')

def command(python, work):
    # Quick Actions start with a bare PATH: give the tools the pipeline calls (ffmpeg, whisper-cli, claude, lms)
    return ('export PATH="/opt/homebrew/bin:/usr/local/bin:$HOME/.local/bin:$HOME/.lmstudio/bin:/usr/bin:/bin:/usr/sbin:/sbin"\n'
            'for f in "$@"; do\n'
            f'  nohup {shlex.quote(python)} {shlex.quote(os.path.join(HERE, "auto_edit.py"))} "$f" --work-dir {shlex.quote(work)} '
            '>/dev/null 2>&1 &\n'
            'done\n')

def shell_action(script):
    arg = lambda n, name, default: {'default value': default, 'name': name, 'required': '0', 'type': '0', 'uuid': str(n)}  # noqa: E731
    return {'action': {
        'AMAccepts': {'Container': 'List', 'Optional': True, 'Types': ['com.apple.cocoa.string']},
        'AMActionVersion': '2.0.3', 'AMApplication': ['Automator'],
        'AMParameterProperties': {k: {} for k in ('COMMAND_STRING', 'CheckedForUserDefaultShell', 'inputMethod', 'shell', 'source')},
        'AMProvides': {'Container': 'List', 'Types': ['com.apple.cocoa.string']},
        'ActionBundlePath': '/System/Library/Automator/Run Shell Script.action', 'ActionName': 'Run Shell Script',
        'ActionParameters': {'COMMAND_STRING': script, 'CheckedForUserDefaultShell': True, 'inputMethod': 1,  # input as arguments
                             'shell': '/bin/zsh', 'source': ''},
        'BundleIdentifier': 'com.apple.RunShellScript', 'CFBundleVersion': '2.0.3',
        'CanShowSelectedItemsWhenRun': False, 'CanShowWhenRun': True, 'Category': ['AMCategoryUtilities'],
        'Class Name': 'RunShellScriptAction', 'InputUUID': str(uuid.uuid4()).upper(), 'OutputUUID': str(uuid.uuid4()).upper(),
        'UUID': str(uuid.uuid4()).upper(), 'UnlocalizedApplications': ['Automator'],
        'arguments': {str(n): arg(n, name, default) for n, (name, default) in enumerate(
            [('inputMethod', 0), ('CheckedForUserDefaultShell', False), ('source', ''), ('COMMAND_STRING', ''), ('shell', '/bin/sh')])},
        'conversionLabel': 0, 'isViewVisible': 1, 'location': '309.000000:305.000000',
        'nibPath': '/System/Library/Automator/Run Shell Script.action/Contents/Resources/Base.lproj/main.nib'},
        'isViewVisible': 1}

def install(name, python, work, services=os.path.expanduser('~/Library/Services'), app=None):
    wf = os.path.join(services, f'{name}.workflow', 'Contents')
    os.makedirs(wf, exist_ok=True)
    finder = '/System/Library/CoreServices/Finder.app'
    doc = {'AMApplicationBuild': '534', 'AMApplicationVersion': '2.10', 'AMDocumentVersion': '2',
           'actions': [shell_action(app_command(app) if app else command(python, work))], 'connectors': {},
           'workflowMetaData': {
               'applicationBundleID': 'com.apple.finder', 'applicationBundleIDsByPath': {finder: 'com.apple.finder'},
               'applicationPath': finder, 'applicationPaths': [finder],
               'inputTypeIdentifier': 'com.apple.Automator.fileSystemObject.folder',
               'outputTypeIdentifier': 'com.apple.Automator.nothing', 'presentationMode': 15, 'processesInput': False,
               'serviceApplicationBundleID': 'com.apple.finder', 'serviceApplicationPath': finder,
               'serviceInputTypeIdentifier': 'com.apple.Automator.fileSystemObject.folder',
               'serviceOutputTypeIdentifier': 'com.apple.Automator.nothing', 'serviceProcessesInput': False,
               'systemImageName': 'NSActionTemplate', 'useAutomaticInputType': False,
               'workflowTypeIdentifier': 'com.apple.Automator.servicesMenu'}}
    info = {'NSServices': [{'NSBackgroundColorName': 'background', 'NSIconName': 'NSActionTemplate',
                            'NSMenuItem': {'default': name}, 'NSMessage': 'runWorkflowAsService',
                            'NSRequiredContext': {'NSApplicationIdentifier': 'com.apple.finder'},
                            'NSSendFileTypes': ['public.folder']}]}
    with open(os.path.join(wf, 'document.wflow'), 'wb') as f:
        plistlib.dump(doc, f)
    with open(os.path.join(wf, 'Info.plist'), 'wb') as f:
        plistlib.dump(info, f)
    if os.path.exists('/System/Library/CoreServices/pbs'):
        subprocess.run(['/System/Library/CoreServices/pbs', '-update'], capture_output=True)  # refresh the Services menu
    return os.path.dirname(wf)

def main():
    parser = argparse.ArgumentParser(description='Installs the Finder Quick Action that edits a folder of footage.')
    parser.add_argument('--name', default='Edit this footage', help='name in the Finder menu')
    parser.add_argument('--app', help="use the engine of this Roughcut app (e.g. /Applications/Roughcut.app): its tools, settings and projects folder")
    parser.add_argument('--work-dir', default='~/Movies/Roughcut', help='where the projects go (and its roughcut.json), without --app')
    parser.add_argument('--python', default=sys.executable, help='Python with numpy (and Vision) for the analysis')
    args = parser.parse_args()
    path = install(args.name, os.path.abspath(args.python) if os.sep in args.python else args.python,
                   os.path.abspath(os.path.expanduser(args.work_dir)), app=args.app and os.path.abspath(args.app))
    print(f'{path}\nRight click on a folder of footage in the Finder > Quick Actions > {args.name}')

if __name__ == '__main__':
    main()
