#!/usr/bin/env python3
"""Merges FCPXML files into one, with everything in a single event: the logged clips, the edit and the Short import
together, so Final Cut Pro asks for a library once. Formats and sources that the files share are kept once, and every
id is renumbered.
Usage: fcpxml_merge.py a.fcpxml b.fcpxml ... -o merged.fcpxml --event "Name" [--version 1.13]"""
import argparse, os, sys
import xml.etree.ElementTree as ET
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fcp

def merge(paths, event_name, version=None):
    """The merged document as text."""
    resources, keys, items, versions = [], {}, [], []
    for path in paths:
        root = ET.parse(fcp.fcpxml_file(path)).getroot()
        versions.append(root.get('version'))
        rename = {}
        for res in root.find('resources'):
            old = res.get('id')
            if res.tag == 'asset':  # the same source file is one asset
                rep = res.find('media-rep')
                key = ('asset', rep.get('src') if rep is not None else old)
            else:  # the same format (or effect) described the same way is one resource
                key = (res.tag, tuple(sorted((k, v) for k, v in res.attrib.items() if k != 'id')))
            if key not in keys:
                keys[key] = f'r{len(keys) + 1}'
                copy = res
                copy.set('id', keys[key])
                resources.append(copy)
            rename[old] = keys[key]
        for element in root.find('resources').iter():
            for attr in ('format', 'ref'):
                if element.get(attr) in rename:
                    element.set(attr, rename[element.get(attr)])
        for event in root.iter('event'):
            for child in list(event):
                for element in child.iter():
                    for attr in ('ref', 'format'):
                        if element.get(attr) in rename:
                            element.set(attr, rename[element.get(attr)])
                items.append(child)
    doc = ET.Element('fcpxml', version=version or max(versions, key=lambda v: tuple(map(int, v.split('.')))))
    res_el = ET.SubElement(doc, 'resources')
    res_el.extend(resources)
    library = ET.SubElement(doc, 'library')
    event = ET.SubElement(library, 'event', name=event_name)
    event.extend(items)
    ET.indent(doc, space='  ')
    return '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE fcpxml>\n' + ET.tostring(doc, encoding='unicode') + '\n'

def main():
    parser = argparse.ArgumentParser(description='Merges FCPXML files into one event, for a single import.')
    parser.add_argument('files', nargs='+')
    parser.add_argument('-o', required=True, dest='out')
    parser.add_argument('--event', required=True, help='name of the single event')
    parser.add_argument('--version', help='FCPXML version of the result (default: the highest of the inputs)')
    args = parser.parse_args()
    with open(args.out, 'w', encoding='utf-8') as f:
        f.write(merge(args.files, args.event, args.version))
    ok, msg = fcp.check(args.out)
    print(f'{args.out}: {len(args.files)} files in one event "{args.event}"\nDTD check: {msg}')
    if ok is False:
        raise SystemExit('The merged FCPXML does not validate.')

if __name__ == '__main__':
    main()
