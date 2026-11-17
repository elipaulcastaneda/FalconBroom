"""Select a new desktop workspace without moving or deleting an older one."""

import os
import re
import uuid
from pathlib import Path

from .desktop_vault import DesktopVaultError


_PROFILE = re.compile(r"[0-9a-f]{32}\Z")
_LEGACY = "legacy"


def _redirected(path: Path) -> bool:
    return path.is_symlink() or bool(getattr(path, "is_junction", lambda: False)())


def _check_path(path: Path) -> None:
    if _redirected(path):
        raise DesktopVaultError("Desktop profile storage is redirected")


def selected_profile(base: Path) -> str:
    base = Path(base)
    _check_path(base / "data")
    pointer = base / "data" / ".desktop-active-profile"
    _check_path(pointer)
    try:
        with pointer.open("r", encoding="ascii") as file:
            profile = file.read(64)
    except FileNotFoundError:
        return _LEGACY
    if profile != _LEGACY and not _PROFILE.fullmatch(profile):
        raise DesktopVaultError("Invalid desktop profile selection")
    return profile


def profile_directory(base: Path, profile: str) -> Path:
    base = Path(base)
    if profile == _LEGACY:
        return base
    if not _PROFILE.fullmatch(profile):
        raise DesktopVaultError("Invalid desktop profile selection")
    profiles = base / "desktop-profiles"
    result = profiles / profile
    _check_path(profiles)
    _check_path(result)
    if not result.is_dir():
        raise DesktopVaultError("Selected desktop profile is unavailable")
    return result


def list_profiles(base: Path) -> list[str]:
    profiles = Path(base) / "desktop-profiles"
    _check_path(profiles)
    if not profiles.exists():
        return [_LEGACY]
    result = [_LEGACY]
    for path in profiles.iterdir():
        _check_path(path)
        if not _PROFILE.fullmatch(path.name) or not path.is_dir():
            raise DesktopVaultError("Unexpected desktop profile entry requires review")
        result.append(path.name)
    return result[:1] + sorted(result[1:])


def select_profile(base: Path, expected: str, profile: str) -> None:
    base = Path(base)
    if selected_profile(base) != expected:
        raise DesktopVaultError("Desktop profile changed; restart and try again")
    profile_directory(base, profile)
    data = base / "data"
    _check_path(data)
    data.mkdir(parents=True, exist_ok=True)
    pointer = data / ".desktop-active-profile"
    _check_path(pointer)
    temp = data / f".desktop-active-profile.{uuid.uuid4().hex}.tmp"
    try:
        with temp.open("x", encoding="ascii") as file:
            file.write(profile)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temp, pointer)
    finally:
        temp.unlink(missing_ok=True)


def create_fresh_profile(base: Path, expected: str) -> str:
    base = Path(base)
    if selected_profile(base) != expected:
        raise DesktopVaultError("Desktop profile changed; restart and try again")
    profiles = base / "desktop-profiles"
    _check_path(profiles)
    profiles.mkdir(parents=True, exist_ok=True)
    profile = uuid.uuid4().hex
    (profiles / profile).mkdir()
    select_profile(base, expected, profile)
    return profile
