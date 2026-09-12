"""Portable chapter publication order; never infer order from copied blob mtimes."""
import copy
import hashlib
import json
import re

FORMAT = 'h3_storage_chapter_seal_v1'
PATTERN = re.compile(r'(?P<prefix>branches/[0-9a-f]{32}/)?chapters/(?P<number>[0-9]{2,})_[^/]+/manifests/(?P<token>[0-9a-f]{32})\.json')
HEAD_PATTERN = re.compile(r'(?P<prefix>branches/[0-9a-f]{32}/)?chapter_heads/(?P<number>[0-9]{4,})\.json')


def parts(address):
    match = PATTERN.fullmatch(address) if isinstance(address, str) else None
    if match is None or int(match['number']) < 1:
        raise ValueError('Invalid chapter snapshot address.')
    return match['prefix'] or '', int(match['number']), match['token']


def head(prefix, number):
    return prefix+'chapter_heads/%04d.json' % number


def entry(address, raw):
    parts(address)
    return dict(manifest=address, sha256=hashlib.sha256(raw).hexdigest())


def record(number, entries):
    entries = copy.deepcopy(entries)
    latest = entries[-1] if entries else dict(manifest=None, sha256=None)
    return dict(format=FORMAT, chapter=number, history=entries, **latest)


def history(value, prefix, number):
    if (not isinstance(value, dict) or value.get('format') != FORMAT
            or type(value.get('chapter')) is not int or value['chapter'] != number
            or set(value) not in ({'format','chapter','manifest','sha256'},
                                  {'format','chapter','manifest','sha256','history'})):
        raise ValueError('Chapter latest selector has an invalid identity.')
    entries = value.get('history', [dict(manifest=value['manifest'], sha256=value['sha256'])])
    if not isinstance(entries, list):
        raise ValueError('Chapter publication history must be a list.')
    seen = set()
    for item in entries:
        if (not isinstance(item, dict) or set(item) != {'manifest','sha256'}
                or not isinstance(item['sha256'], str)
                or re.fullmatch('[0-9a-f]{64}', item['sha256']) is None):
            raise ValueError('Chapter publication entry is invalid.')
        selected_prefix, selected_number, _ = parts(item['manifest'])
        if selected_prefix != prefix or selected_number != number or item['manifest'] in seen:
            raise ValueError('Chapter publication history crosses branches/chapters or repeats a snapshot.')
        seen.add(item['manifest'])
    last = entries[-1] if entries else dict(manifest=None, sha256=None)
    if any(value.get(key) != last[key] for key in ('manifest','sha256')):
        raise ValueError('Chapter latest selector differs from its publication history.')
    return copy.deepcopy(entries)


def validate(raw, address, run):
    prefix, number, token = parts(address)
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('Chapter snapshot has duplicate JSON keys.')
            result[key] = value
        return result
    def nonfinite(value):
        raise ValueError('Chapter snapshot has non-finite JSON: '+value)
    document = json.loads(raw, object_pairs_hook=pairs, parse_constant=nonfinite)
    if (not isinstance(document, dict) or document.get('format') != 'h3_chain_chapter_manifest_v1'
            or document.get('run_name') != run or not isinstance(document.get('chapter'), dict)
            or type(document['chapter'].get('number')) is not int or document['chapter']['number'] != number
            or document.get('chapter_manifest_id') != token
            or document.get('chapter_manifest_path') != 'h3_chains/'+run+'/'+address):
        raise ValueError('Sealed chapter failed its identity check.')
    branch = prefix.split('/')[1] if prefix else 'main'
    if document.get('_branch_id', branch) != branch:
        raise ValueError('Chapter snapshot belongs to another branch.')
    identity = {key:value for key,value in document.items()
                if key not in ('sealed_at','chapter_manifest_id','chapter_manifest_path')}
    if identity.get('storage_chapter_version') == 1:
        identity.pop('_storage_pin', None)
        identity.pop('_project_ownership', None)
    raw_identity = json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
    if hashlib.sha256(raw_identity).hexdigest()[:32] != token:
        raise ValueError('Sealed chapter failed its content identity check.')
    return document


def select(value, prefix, number, run, read):
    """A missing snapshot is skippable only with its exact archived tombstone."""
    for item in reversed(history(value, prefix, number)):
        address = item['manifest']
        try:
            raw = read(address)
        except FileNotFoundError:
            retired = address.replace('/manifests/', '/retired_manifests/')
            try:
                raw = read(retired)
            except FileNotFoundError:
                raise FileNotFoundError('Chapter publication is missing without a retirement record: '+address) from None
            if entry(address, raw) != item:
                raise ValueError('Retired chapter differs from its publication history.')
            validate(raw, address, run)
            continue
        if entry(address, raw) != item:
            raise ValueError('Chapter latest selector differs from its snapshot.')
        return address, validate(raw, address, run)
    raise FileNotFoundError('No unretired sealed Chapter %d exists for this branch.' % number)


def verified_history(value, prefix, number, run, read):
    """Validate the entire imported index, not only its currently selected seal."""
    entries = history(value, prefix, number)
    for item in entries:
        address = item['manifest']
        try:
            raw = read(address)
        except FileNotFoundError:
            try:
                raw = read(address.replace('/manifests/', '/retired_manifests/'))
            except FileNotFoundError:
                raise FileNotFoundError('Chapter publication is missing without a retirement record: '+address) from None
        if entry(address, raw) != item:
            raise ValueError('Chapter publication history differs from its snapshot or retirement record.')
        validate(raw, address, run)
    return entries
