"""Portable addresses for saved artifacts, distinct from absolute OS paths."""

from pathlib import PurePosixPath, PureWindowsPath


def artifact_address(value):
    """Accept legacy Windows separators, not roots, traversal or drive/ADS paths.

    Normalize only addresses used for lookup/comparison. Never rewrite saved
    documents: their original bytes and hashes remain the recovery authority.
    """
    if not isinstance(value, str) or not value:
        raise ValueError("A saved artifact address is required.")
    address = value.replace("\\", "/")
    path = PurePosixPath(address)
    if (not path.parts or path.is_absolute() or PureWindowsPath(address).drive
            or ".." in path.parts or str(path) != address
            or ":" in address or "\0" in address):
        raise ValueError("Invalid saved artifact address.")
    return address


def is_link_or_junction(path):
    # Junctions are a separate Windows reparse-point type, not symlinks.
    return path.is_symlink() or getattr(path, "is_junction", lambda: False)()
