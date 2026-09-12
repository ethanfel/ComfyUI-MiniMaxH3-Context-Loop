"""Reproducible asset previews, separate from accepted media/catalog authority.

Only a host preview-cache grant permits generation. Reads remain tied to the
accepted source. Optional files never become source assets or recovery payloads.
"""
from pathlib import Path
import tempfile
import uuid

if __package__:
    from . import storage_state as state
    from .storage_layout import OrganizedStorageLayout
    from .storage_resolver import confined
    from .storage_project import _hash_file
    from .processing_persistence import atomic_json, publish_new_file, sync_file, sync_directory
else:
    import storage_state as state
    from storage_layout import OrganizedStorageLayout
    from storage_resolver import confined
    from storage_project import _hash_file
    from processing_persistence import atomic_json, publish_new_file, sync_file, sync_directory


FORMAT = 'h3_asset_preview_cache_v1'
VARIANTS = {'poster':('previews','.jpg','ensure_poster'),
            'thumbnail':('thumbnails','.thumb.jpg','ensure_thumbnail'),
            'preview':('previews','.mp4','ensure_browser_media')}


def _cached(directory, pointer, request):
    if not pointer.is_file():
        return None
    try:
        value = state._decode(state._read_bytes(pointer))
        if value.get('request') != request:
            return None
        record = value['file']
        name = record['name']
        if (not isinstance(name,str) or '/' in name or '\\' in name
                or name != state._token(name.split('.')[0])+request['suffix']):
            return None
        path = confined(directory,name)
        digest,signature = _hash_file(path)
        if digest != record['sha256'] or signature[2] != record['size'] or signature[2] <= 0:
            return None
        return path
    except (ValueError,TypeError,KeyError,OSError):
        return None  # Rebuild only with the separate cache grant below.


def preview(runtime, assets, project, asset_id, variant):
    if __package__:
        from .project_assets import ProjectAssetStore, _catalog_lock, _catalog_file_lock
    else:
        from project_assets import ProjectAssetStore, _catalog_lock, _catalog_file_lock
    runtime.check()
    if project != runtime.run or assets._read_view(project) is not runtime.reader or variant not in VARIANTS:
        raise ValueError('Preview escaped its pinned asset reader.')
    entry,source = assets.asset(project,asset_id)
    if variant=='preview' and (entry['kind']!='video' or assets._browser_video(entry,source)):
        return source
    if entry['kind']=='audio':
        raise ValueError('Audio assets do not have poster frames.')
    group,suffix,method = VARIANTS[variant]
    request = dict(format=FORMAT,sha256=entry['sha256'],size=entry['size'],kind=entry['kind'],
                   variant=variant,suffix=suffix)
    identity = uuid.uuid5(uuid.NAMESPACE_URL,state._hash(state._encode(request))).hex
    layout = OrganizedStorageLayout(str(runtime.project),runtime.store._marker()[0]['path_budget'])
    directory = confined(runtime.project,layout.optional(group))
    reservation_address = layout.optional(group,identity+'.request.json')
    reservation = confined(runtime.project,reservation_address)
    pointer = confined(runtime.project,layout.optional(group,identity+'.json'))
    layout.check_budget(layout.optional(group,identity+'.json.lock'))

    def check_grant():
        runtime.check()
        if not runtime.asset_previews:
            raise ValueError('Missing or damaged preview requires an explicit preview-cache grant.')

    def accepted():
        if not reservation.is_file():
            return None
        if state._read_bytes(reservation)!=state._encode(request):
            raise state.StateConflict('Preview request reservation is occupied; original retained.')
        result = _cached(directory,pointer,request)
        runtime.check()
        return result

    cached = accepted()
    if cached is not None:
        return str(cached)
    check_grant()
    directory.mkdir(parents=True,exist_ok=True)
    # Cache-local serialization, not a project/catalog/ownership lock across
    # decoding. The catalog and accepted root are never written by this path.
    with _catalog_lock(str(pointer)), _catalog_file_lock(str(pointer)):
        cached = accepted()
        if cached is not None:
            return str(cached)
        check_grant()
        if not reservation.exists() and pointer.exists():
            raise state.StateConflict('Unowned preview pointer is occupied; original retained.')
        state._immutable(runtime.project,reservation_address,state._encode(request),layout.path_budget)
        temporary = Path(tempfile.mkdtemp(prefix='r-',dir=directory))
        target = temporary/('p'+suffix)
        try:
            layout.check_budget(target.relative_to(runtime.project).as_posix(),staging_suffix='.'+'f'*32+'.tmp'+('.mp4' if variant=='preview' else '.jpg'))
        except BaseException:
            temporary.rmdir()  # Empty, just-created private staging directory.
            raise
        staged = ProjectAssetStore(assets.input_root,assets.output_root)
        closed = False

        def destination(run,record,requested_suffix):
            check_grant()
            if closed or run!=project or record!=entry or requested_suffix!=suffix:
                raise ValueError('Preview renderer escaped its exact cache request.')
            return str(target)

        staged._preview_target = destination
        try:
            rendered = Path(getattr(staged,method)(project,asset_id))
            if rendered != target:
                # The legacy browser-media method falls back to the verified
                # source when ffmpeg is unavailable; do not cache it as MP4.
                if variant=='preview' and rendered==Path(source):
                    runtime.check()
                    return str(rendered)
                raise ValueError('Preview generator returned an unexpected file.')
            if variant!='preview':
                from PIL import Image
                with Image.open(target) as image:
                    image.verify()
            digest,signature = _hash_file(target)
            if signature[2]<=0:
                raise ValueError('Preview generator returned an empty file.')
            # Revalidate immutable source and operation after lengthy decoding.
            if assets.asset(project,asset_id)!=(entry,source):
                raise state.StateConflict('Preview source changed during rendering.')
            check_grant()
            name = uuid.uuid4().hex+suffix
            final = confined(runtime.project,layout.optional(group,name))
            sync_file(target)
            publish_new_file(target,final)
            sync_directory(directory)
            check_grant()
            atomic_json(pointer,dict(request=request,file=dict(name=name,sha256=digest,size=signature[2])))
            runtime.check()
            return str(final)
        finally:
            closed = True
            if target.exists():
                target.unlink()  # Only this invocation's private rendered file.
            temporary.rmdir()  # Domain renderers clean their own temporary file.
