"""Immutable, no-overwrite commits for explicitly opted-in rehearsal copies.

storage.json is a write-once bootstrap. Numbered records form a checksum-bound
chain; publishing one complete record is the visibility point. An acknowledgement
witness detects a lost/corrupt committed record, including the last acknowledged
record. Readers and writers use the same project lock, never newest-file guesses.

This does not promise survival of server power loss or detect deletion of an
entire tail AND all its witnesses. Such guarantees require external storage.
"""
from dataclasses import dataclass
import re

if __package__:
    from . import storage_state as state
    from .processing_persistence import sync_directory, sync_file
else:
    import storage_state as state
    from processing_persistence import sync_directory, sync_file

PROTOCOL = 'immutable_slots_v1'
FORMAT = 'h3_immutable_commit_v1'
ACK = 'h3_immutable_commit_ack_v1'
DIRECTORY = 'project/commits'
_NAME = re.compile(r'([0-9]{12})(\.ack)?\.json')


@dataclass(frozen=True)
class Head:
    value: dict
    reference: dict | None
    sequence: int
    witness: bytes
    acknowledged: bool


class CommitLog:
    """Internal backend; the owning ControlStore enforces copy-only access."""

    def __init__(self, project, bootstrap):
        self.project = project
        self.bootstrap = bootstrap
        self.initial = state._decode(bootstrap)
        if self.initial.get('commit_protocol') != PROTOCOL:
            raise ValueError('Unsupported immutable commit protocol.')
        self.digest = state._hash(bootstrap)
        self.budget = self.initial['path_budget']

    @staticmethod
    def _address(sequence, *, ack=False):
        if type(sequence) is not int or not 1 <= sequence <= 999999999999:
            raise ValueError('Immutable commit sequence exhausted or invalid.')
        return f'{DIRECTORY}/{sequence:012d}'+('.ack' if ack else '')+'.json'

    def _read(self, address):
        raw = state._read_bytes(state.resolver.confined(self.project, address))
        return state._decode(raw), {'path': address, 'sha256': state._hash(raw), 'size': len(raw)}

    def read(self):
        """Read a contiguous history while the project's reentrant lock is held."""
        directory = state.resolver.confined(self.project, DIRECTORY)
        records = {}
        if directory.exists():
            for path in directory.iterdir():
                name = path.name
                if re.fullmatch(r'\.tmp-[0-9a-f]{32}', name):
                    # Unaccepted, independently named publication staging.
                    state.resolver.confined(self.project, DIRECTORY+'/'+name)
                    if not path.is_file():
                        raise ValueError('Invalid immutable commit staging entry.')
                    continue
                match = _NAME.fullmatch(name)
                if not match or not path.is_file() or int(match[1]) == 0:
                    raise ValueError('Unknown immutable commit namespace entry; no fallback.')
                records.setdefault(int(match[1]), set()).add('ack' if match[2] else 'commit')
        previous, value, acknowledged, last = None, self.initial, True, 0
        for sequence, kinds in sorted(records.items()):
            if sequence != last+1 or 'commit' not in kinds:
                raise ValueError('Missing immutable commit record; no older-root fallback.')
            if not acknowledged:
                raise ValueError('Unacknowledged commit before a later record; history is inconsistent.')
            record, reference = self._read(self._address(sequence))
            if (set(record) != {'format', 'bootstrap_sha256', 'sequence', 'previous', 'value'}
                    or record['format'] != FORMAT or record['bootstrap_sha256'] != self.digest
                    or type(record['sequence']) is not int or record['sequence'] != sequence
                    or record['previous'] != previous or not isinstance(record['value'], dict)
                    or record['value'].get('commit_protocol') != PROTOCOL):
                raise ValueError('Invalid immutable commit chain or bootstrap witness.')
            acknowledged = 'ack' in kinds
            if acknowledged:
                ack, _ = self._read(self._address(sequence, ack=True))
                if ack != {'format': ACK, 'commit': reference}:
                    raise ValueError('Immutable commit acknowledgement checksum mismatch.')
            previous, value, last = reference, record['value'], sequence
        sequence = len(records)
        witness = state._encode({'bootstrap_sha256': self.digest, 'head': previous})
        return Head(value, previous, sequence, witness, acknowledged)

    def acknowledge(self, head):
        """Re-flush a visible commit after a lost/failed acknowledgement."""
        if head.reference is None:
            return
        # Even a duplicate operation must not turn an uncertain flush into an
        # unqualified success. No root, commit or witness is ever overwritten.
        sync_file(state.resolver.confined(self.project, head.reference['path']))
        state._immutable(self.project, self._address(head.sequence, ack=True),
                         state._encode({'format': ACK, 'commit': head.reference}), self.budget)
        sync_directory(state.resolver.confined(self.project, DIRECTORY))

    def append(self, value, expected_witness, *, after_stage=None):
        head = self.read()
        if head.witness != expected_witness:
            raise state.StateConflict('Immutable commit head changed outside the writer lock.')
        if not head.acknowledged:
            self.acknowledge(head)
        if value.get('commit_protocol') != PROTOCOL:
            raise ValueError('Cannot remove the immutable commit protocol.')
        sequence = head.sequence+1
        record = {'format': FORMAT, 'bootstrap_sha256': self.digest, 'sequence': sequence,
                  'previous': head.reference, 'value': value}
        state._immutable(self.project, self._address(sequence), state._encode(record), self.budget)
        if after_stage:
            after_stage('commit')
        accepted = self.read()
        self.acknowledge(accepted)
        if after_stage:
            after_stage('acknowledged')
