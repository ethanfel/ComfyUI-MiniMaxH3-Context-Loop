"""Opt-in durability/locking probe in a NEW .h3-storage-test-* directory.

Retains all tiny test files and a JSON report; never touches existing chains.
This tests the mounted filesystem, not Windows APIs or power-loss durability.
"""
import argparse
import ctypes
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import time
import uuid
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import storage_state as state
from storage_resolver import confined
from processing_persistence import atomic_json, sync_directory, sync_file, publish_new_file
from checkpoint_manager import _RunMutationLock
import processing_persistence as persistence


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('directory')
    parser.add_argument('--child', choices=('lock', 'read'))
    parser.add_argument('--diagnose-renames', action='store_true')
    args = parser.parse_args()
    root = Path(args.directory).absolute()
    if not root.name.startswith('.h3-storage-test-') or root.resolve() != root or not root.is_dir():
        raise ValueError('Use a freshly created explicit .h3-storage-test-* directory, without links.')
    confined(root, 'probe-owner.json')
    if args.child:
        owner = json.loads((root/'probe-owner.json').read_text())
        if owner.get('format') != 'h3_filesystem_probe_v1':
            raise ValueError('Child probe requires the exact parent-owned directory.')
        if args.child == 'lock':
            with _RunMutationLock(str(root/'coordination.lock')):
                print('acquired', flush=True)
        else:
            count, deadline = 0, time.monotonic()+25
            atomic_json(root/'reader-ready.json', {'ready': True})
            while not (root/'reader-done.json').exists() and time.monotonic() < deadline:
                value = json.loads((root/'atomic.json').read_text())
                if value['left'] != value['right'] or value['seed'] != 18446744073709551613:
                    raise AssertionError('Observed a partial or mismatched JSON publication')
                count += 1
            print(count, flush=True)
        return
    if any(root.iterdir()):
        raise FileExistsError('Probe must not adopt an occupied directory.')
    atomic_json(root/'probe-owner.json', {'format': 'h3_filesystem_probe_v1', 'operation': uuid.uuid4().hex})
    summary = {'platform': platform.system(), 'python': platform.python_version(),
               'directory': str(root), 'checks': {}, 'errors': {}, 'native_windows_test': os.name == 'nt',
               'power_loss_test': False, 'live_chains_modified': False}
    def check(name, function):
        try:
            result = function()
            summary['checks'][name] = result is not False
            if result is False:
                raise AssertionError(name)
            print('PASS', name, flush=True)
        except Exception as exc:
            summary['checks'][name] = False
            summary['errors'][name] = type(exc).__name__+': '+str(exc)
            print('FAIL', name, type(exc).__name__, str(exc), flush=True)
        atomic_json(root/'probe-report.json', summary)
    if args.diagnose_renames:
        def run_case(name, operation):
            source, target = root/(name+'-source.bin'), root/(name+'-target.bin')
            with source.open('xb') as handle:
                handle.write(b'private probe bytes')
                handle.flush()
                os.fsync(handle.fileno())
            operation(source, target)
            return target.read_bytes() == b'private probe bytes'
        check('rename_new_plain_file', lambda: run_case('rename', os.rename))
        check('replace_new_plain_file', lambda: run_case('replace', os.replace))
        check('hardlink_plain_file', lambda: run_case('hardlink', os.link))
        def exclusive_rename(source, target):
            call = ctypes.CDLL(None, use_errno=True).renameat2
            call.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint)
            call.restype = ctypes.c_int
            # Preserve Python audit guards for this libc-backed filesystem call.
            sys.audit('os.rename', str(source), str(target), -1, -1)
            if call(-100, os.fsencode(source), -100, os.fsencode(target), 1):
                err = ctypes.get_errno()
                raise OSError(err, os.strerror(err), str(target))
        check('linux_rename_noreplace', lambda: run_case('noreplace', exclusive_rename))
        check('atomic_json_fresh_name', lambda: atomic_json(root/'probe-atomic-diagnostic.json', {'test': 1}))
        check('atomic_json_same_name_as_initial_failure', lambda: atomic_json(root/'atomic.json', {'test': 1}))
        summary['finished'] = True
        atomic_json(root/'probe-report.json', summary)
        print('Report:', root/'probe-report.json', flush=True)
        return
    raw = state._encode({'seed': 18446744073709551613, 'prompt': 'Filesystem probe only. 雪'})
    address = 'project/branches/'+'a'*32+'.json'
    def immutable():
        ref = state._immutable(root, address, raw, 240)
        return (root/ref['path']).read_bytes() == raw
    check('immutable_no_replace_publication', immutable)
    check('same_bytes_retry_and_directory_sync', immutable)
    def collision():
        try:
            state._immutable(root, address, b'foreign replacement', 240)
        except ValueError:
            return (root/address).read_bytes() == raw
        return False
    check('immutable_collision_preserves_existing_bytes', collision)
    def independent():
        original, staged, final = root/'source.bin', root/'staged.bin', root/'copied.bin'
        with original.open('xb') as handle:
            handle.write(b'small independent source payload'*512)
        sync_file(original)
        with staged.open('xb') as handle:
            handle.write(original.read_bytes())
            handle.flush()
            os.fsync(handle.fileno())
        publish_new_file(staged, final)
        sync_directory(root)
        return original.read_bytes() == final.read_bytes() and original.stat().st_ino != final.stat().st_ino
    check('independent_copy_then_no_replace_publication', independent)
    def raw_directory_flush():
        if os.name == 'nt':
            summary['raw_directory_fsync'] = 'not available through portable Windows Python'
            return True
        try:
            fd = os.open(root, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
            summary['raw_directory_fsync'] = 'supported by mounted filesystem'
        except OSError as exc:
            summary['raw_directory_fsync'] = {'errno': exc.errno, 'error': str(exc)}
            raise
    check('raw_directory_fsync_capability', raw_directory_flush)
    def atomic_reads():
        trace = []
        original_replace = os.replace
        def token(path):
            try:
                stat = Path(path).stat()
                return [stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns]
            except FileNotFoundError:
                return None
        def traced_replace(src, dst):
            row = {'source': Path(src).name, 'target': Path(dst).name,
                   'before_source': token(src), 'before_target': token(dst)}
            trace.append(row)
            try:
                original_replace(src, dst)
                row['returned'] = 'success'
            except OSError as exc:
                row['errno'] = exc.errno
                raise
            finally:
                row['after_source'], row['after_target'] = token(src), token(dst)
        try:
            with patch.object(persistence.os, 'replace', traced_replace):
                return atomic_reads_run()
        finally:
            # Keep evidence even if the target disappeared on a failed SMB rename.
            with (root/'atomic-rename-trace.json').open('x') as handle:
                json.dump(trace, handle, indent=2)
                handle.flush()
                os.fsync(handle.fileno())

    def atomic_reads_run():
        atomic_json(root/'atomic.json', {'left': 0, 'right': 0, 'seed': 18446744073709551613})
        child = subprocess.Popen([sys.executable, __file__, str(root), '--child', 'read'],
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            deadline = time.monotonic()+15
            while not (root/'reader-ready.json').is_file():
                if child.poll() is not None or time.monotonic() >= deadline:
                    raise AssertionError('Reader did not start within 15 seconds.')
                time.sleep(0.01)
            for value in range(1, 41):
                atomic_json(root/'atomic.json', {'left': value, 'right': value, 'seed': 18446744073709551613})
            atomic_json(root/'reader-done.json', {'done': True})
            out, err = child.communicate(timeout=30)
            summary['concurrent_reads'] = int(out.strip()) if out.strip().isdigit() else 0
            if child.returncode:
                raise AssertionError(err)
            return summary['concurrent_reads'] > 0
        finally:
            if child.poll() is None:
                child.terminate()
                out, err = child.communicate(timeout=5)
                summary['interrupted_reader_stdout'] = out
                summary['interrupted_reader_stderr'] = err
    check('atomic_json_concurrent_reader_never_sees_mixed_values', atomic_reads)
    def locking():
        command = [sys.executable, __file__, str(root), '--child', 'lock']
        blocked = False
        with _RunMutationLock(str(root/'coordination.lock')):
            try:
                subprocess.run(command, capture_output=True, text=True, timeout=2, check=True)
            except subprocess.TimeoutExpired:
                blocked = True
        result = subprocess.run(command, capture_output=True, text=True, timeout=15, check=True)
        return blocked and result.stdout.strip() == 'acquired'
    check('separate_process_lock_exclusion_and_release', locking)
    summary['finished'] = True
    summary['all_passed'] = all(summary['checks'].values())
    atomic_json(root/'probe-report.json', summary)
    print('Report:', root/'probe-report.json', flush=True)
    if not summary['all_passed']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
