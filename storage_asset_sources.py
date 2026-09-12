"""Host-issued, revocable read pins for cross-project Carousel operations.

Each source is opened by the host under its own existing storage access. Only
fixed catalog/media reads run in that captured context; a destination's write
grant or browser-supplied path is never used to open a neighbouring project.
"""
import copy
from contextlib import contextmanager
from contextvars import copy_context

if __package__:
    from . import storage_runtime as runtime
    from .storage_project_assets import accepted_catalog, accepted_asset_path
    from .project_assets import ProjectAssetStore, audio_track_bindings
else:
    import storage_runtime as runtime
    from storage_project_assets import accepted_catalog, accepted_asset_path
    from project_assets import ProjectAssetStore, audio_track_bindings


class _AssetSource:
    def __init__(self, store, pin):
        if runtime._ACTIVE.get() is not None:
            raise ValueError('The host must open asset source pins before the destination runtime.')
        if not isinstance(pin, dict):
            raise ValueError('An asset source needs its explicit saved runtime pin.')
        # Construction validates the source's own ready gate, root and epoch;
        # this cannot be done under a destination project's test/access scope.
        bound = runtime.ProjectRuntime(store, pin=pin, selected=pin.get('branch_id'))
        self._store, self._pin = store, copy.deepcopy(bound.pin)
        self._context = copy_context()
        self.closed = False

    @property
    def run(self):
        return self._pin['run_name']

    @property
    def pin(self):
        return copy.deepcopy(self._pin)

    def check(self, parent):
        parent.check()
        if self.closed or not any(source is self for source in parent.asset_sources):
            raise ValueError('Asset source read grant is closed or not registered for this request.')
        if self.run == parent.run:
            raise ValueError('Foreign asset sources cannot replace the destination project binding.')

    def read(self, parent, asset_id=None, *, tracks=False):
        self.check(parent)
        # copy() makes simultaneous worker reads independent. The only callable
        # accepted by this boundary is the fixed read-only implementation below.
        value = self._context.copy().run(_read, self, asset_id, tracks)
        self.check(parent)
        return value


@contextmanager
def asset_source_access(store, *, pin):
    """Explicit host scope; never called from a browser/node payload."""
    source = _AssetSource(store, pin)
    try:
        yield source
    finally:
        source.closed = True  # Also revokes copies inherited by threads/tasks.


def validate_sources(values, destination):
    if not isinstance(values, (tuple, list)) or len(values) > 128:
        raise ValueError('Asset source grants require a bounded host-provided sequence.')
    result, names = [], set()
    for source in values:
        if (type(source) is not _AssetSource or source.closed or source.run == destination
                or source.run in names):
            raise ValueError('Invalid, closed or ambiguous host asset source grant.')
        names.add(source.run)
        result.append(source)
    return tuple(result)


def source_for(parent, name, pin):
    parent.check()
    source = next((source for source in parent.asset_sources if source.run == name), None)
    if source is None or pin != source.pin:
        raise ValueError('Asset source requires its own matching host-granted snapshot pin.')
    source.check(parent)
    return source


def _read(source, asset_id, tracks):
    if source.closed:
        raise ValueError('Asset source grant has ended.')
    with runtime.runtime_access(source._store, pin=source.pin,
                                selected=source.pin['branch_id']) as bound:
        catalog = accepted_catalog(bound.reader)
        if catalog is None:
            if asset_id is not None:
                raise FileNotFoundError('Source project has no accepted asset catalog.')
            return None
        catalog = ProjectAssetStore._normalize_catalog(catalog, bound.run)
        by_id = {entry['id']:entry for entry in catalog['assets']}
        bindings = None
        if asset_id is None:
            selected = list(by_id)
        else:
            wanted = str(asset_id or '')
            if wanted not in by_id:
                raise FileNotFoundError('Asset was not found in the selected source snapshot.')
            selected = [wanted]
            if tracks:
                bindings = audio_track_bindings(
                    (by_id[wanted].get('options') or {}).get('audio_tracks'), catalog['assets'])
                selected += [value for value in (bindings or {}).values() if value and value not in selected]
        media = {}
        for identity in selected:
            entry = by_id[identity]
            # Listing validates the indexed identity, confinement and file
            # size, without re-reading every video/image on the network share.
            # An actual import always verifies the selected media's full hash.
            path = accepted_asset_path(bound.reader, entry, verify=asset_id is not None)
            media[identity] = dict(entry=copy.deepcopy(entry), path=str(path))
        return dict(project=source.run, source_pin=source.pin, catalog=catalog,
                    media=media, bindings=bindings)


def catalogs(parent, current_catalog):
    """List only the current pin plus explicit sources; never scan disk."""
    parent.check()
    result = [dict(project=parent.run, source_pin=parent.pin, catalog=current_catalog)]
    for source in parent.asset_sources:
        value = source.read(parent)
        if value is not None:
            result.append(value)
    return result
