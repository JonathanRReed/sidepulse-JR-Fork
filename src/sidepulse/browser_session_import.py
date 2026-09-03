"""Import a provider's existing browser session instead of asking for a key.

Reported as "Why do I need an API key? The implementation inside of
CodexBar doesn't require an API key" -- correct, and the staged flow was
worse than it looked: "Enable Devin browser access" only ever flipped a
preference flag. NOTHING in this app read a browser. The single thing
the button promised was the one thing it did not do, so the only way to
reach Devin usage was to go find an API key by hand.

CodexBar reads Devin's own web session out of Chromium local storage
(SweetCookieKit -> leveldb) and sends it as a bearer token. That works,
but Chromium's data directory is TCC-protected on current macOS, so it
costs the user a Full Disk Access grant -- which CodexBar documents.

Firefox-family browsers (Zen, Firefox, LibreWolf, Waterfox) keep local
storage in a plain SQLite file that is NOT TCC-protected, so the same
session imports with no permission grant at all. That is the path
implemented here, and it is the path that matters on this install: the
default browser is Zen, and its profile holds a live app.devin.ai
``auth1_session``.

Read discipline, matching _read_grok_auth: the sqlite file is opened
immutable (never locking or writing to a browser's live database), only
the one requested origin is touched, sizes are bounded, and symlinks are
refused. Nothing here runs in the background -- an import happens only
when the user clicks Import.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

#: Where Firefox-family browsers keep profiles, relative to ~/Library/
#: Application Support. Zen leads deliberately: it is a Firefox fork whose
#: users are exactly the people whose "default browser" is not Chrome.
FIREFOX_FAMILY_PROFILE_ROOTS: tuple[tuple[str, str], ...] = (
    ("Zen", "zen/Profiles"),
    ("Firefox", "Firefox/Profiles"),
    ("LibreWolf", "LibreWolf/Profiles"),
    ("Waterfox", "Waterfox/Profiles"),
)
_FIREFOX_PROFILE_ROOT_BY_BROWSER = {
    label.lower(): relative for label, relative in FIREFOX_FAMILY_PROFILE_ROOTS
}

#: A local-storage database bigger than this is not a session store.
MAX_LOCAL_STORAGE_BYTES = 64 * 1024 * 1024
#: Uncompressed values only. 1 is snappy, which needs a codec we do not
#: ship; a token has never been big enough to be compressed in practice.
COMPRESSION_NONE = 0
MAX_STORAGE_ROWS = 512

DEVIN_ORIGIN = "https://app.devin.ai"
_AUTH1_SUFFIX = "auth1_session"
_AUTH0_MARKER = "auth0spajs@@::"
_EXTERNAL_ORG_PREFIX = "last-internal-org-for-external-org-v1-"
_ORG_NAME_MARKER = "-org_name-"
_FEATURE_FLAG_ORG_PREFIX = "feature-flags-cache:org"
_MIN_TOKEN_LENGTH = 20


@dataclass(frozen=True, slots=True)
class BrowserSession:
    """One provider session lifted from a browser profile."""

    token: str
    organization: str | None
    internal_organization_id: str | None
    source_label: str


def origin_directory_name(origin: str) -> str:
    """``https://app.devin.ai`` -> ``https+++app.devin.ai``.

    Firefox's own origin-to-directory encoding: scheme separator becomes
    ``+++`` and the rest is kept verbatim for an ordinary https origin.
    """
    return origin.replace("://", "+++")


def firefox_profile_directories(home: Path) -> list[tuple[str, Path]]:
    """Every existing Firefox-family profile, labelled by browser."""
    found: list[tuple[str, Path]] = []
    support = Path(home) / "Library" / "Application Support"
    for label, relative in FIREFOX_FAMILY_PROFILE_ROOTS:
        root = support / relative
        try:
            if root.is_symlink() or not root.is_dir():
                continue
            entries = sorted(root.iterdir())
        except OSError:
            continue
        for entry in entries:
            try:
                if entry.is_symlink() or not entry.is_dir():
                    continue
            except OSError:
                continue
            found.append((f"{label} {entry.name}", entry))
    return found


def firefox_profile_directory(
    home: Path,
    *,
    browser: str,
    profile: str,
) -> Path | None:
    """Resolve one named Firefox-family profile without scanning siblings."""
    relative = _FIREFOX_PROFILE_ROOT_BY_BROWSER.get(str(browser).lower())
    if (
        relative is None
        or not isinstance(profile, str)
        or not profile
        or profile in {".", ".."}
        or Path(profile).name != profile
        or "\x00" in profile
    ):
        return None
    root = Path(home) / "Library" / "Application Support" / relative
    candidate = root / profile
    try:
        if root.is_symlink() or not root.is_dir():
            return None
        if candidate.is_symlink() or not candidate.is_dir():
            return None
    except OSError:
        return None
    return candidate


def _devin_database_for_profile(profile_root: Path) -> Path | None:
    """Resolve Devin's Firefox localStorage DB with no symlinked component."""
    parts = (
        "storage",
        "default",
        origin_directory_name(DEVIN_ORIGIN),
        "ls",
        "data.sqlite",
    )
    candidate = Path(profile_root)
    for index, part in enumerate(parts):
        candidate = candidate / part
        try:
            if candidate.is_symlink():
                return None
            if index < len(parts) - 1 and not candidate.is_dir():
                return None
        except OSError:
            return None
    return candidate


def _decoded(value: object) -> str | None:
    if isinstance(value, bytes):
        for encoding in ("utf-8", "utf-16-le"):
            try:
                return value.decode(encoding)
            except (UnicodeDecodeError, ValueError):
                continue
        return None
    return value if isinstance(value, str) else None


def read_local_storage(database: Path) -> dict[str, str]:
    """Only Devin session and organization fields from one origin.

    Opened ``immutable=1``: no lock is taken and no journal is written,
    so a running browser is never disturbed and a read can never corrupt
    the profile.
    """
    path = Path(database)
    try:
        if path.is_symlink() or not path.is_file():
            return {}
        if path.stat().st_size > MAX_LOCAL_STORAGE_BYTES:
            return {}
    except OSError:
        return {}
    entries: dict[str, str] = {}
    try:
        connection = sqlite3.connect(f"file:{path}?immutable=1", uri=True, timeout=1.0)
    except sqlite3.Error:
        return {}
    try:
        with connection:
            rows = connection.execute(
                """
                SELECT key, value, compression_type
                FROM data
                WHERE key GLOB ?
                   OR key GLOB ?
                   OR key GLOB ?
                   OR key GLOB ?
                   OR key GLOB ?
                LIMIT ?
                """,
                (
                    f"*{_AUTH1_SUFFIX}",
                    f"*{_AUTH0_MARKER}*",
                    f"{_EXTERNAL_ORG_PREFIX}*",
                    f"*{_ORG_NAME_MARKER}*",
                    f"{_FEATURE_FLAG_ORG_PREFIX}*",
                    MAX_STORAGE_ROWS,
                ),
            ).fetchall()
    except sqlite3.Error:
        return {}
    finally:
        connection.close()
    for key, value, compression in rows:
        if not isinstance(key, str):
            continue
        # A snappy-compressed VALUE is unreadable without a codec we do
        # not ship -- but the KEY still is, and Devin encodes the
        # organization slug and id into key names
        # ("...-org_name-<slug>", "feature-flags-cache:org-<id>"), which
        # are exactly the compressed rows. Dropping the whole row lost
        # the slug and forced the internal-id URL form.
        decoded = _decoded(value) if compression == COMPRESSION_NONE else None
        entries[key] = decoded if decoded is not None else ""
    return entries


def _json_object(raw: str) -> object | None:
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return None


def _auth1_token(entries: dict[str, str]) -> str | None:
    for key, value in entries.items():
        if not key.endswith(_AUTH1_SUFFIX):
            continue
        payload = _json_object(value)
        if not isinstance(payload, dict):
            continue
        token = payload.get("token")
        if (
            isinstance(token, str)
            and token.startswith("auth1_")
            and len(token) > _MIN_TOKEN_LENGTH
        ):
            return token
    return None


def _find_access_token(payload: object, depth: int = 0) -> str | None:
    """Devin's older Auth0 shape, where the token is nested somewhere in
    an opaque cache entry."""
    if depth > 6:
        return None
    if isinstance(payload, dict):
        for key in ("access_token", "accessToken"):
            candidate = payload.get(key)
            if (
                isinstance(candidate, str)
                and len(candidate) > _MIN_TOKEN_LENGTH
                and ("." in candidate or candidate.startswith("eyJ"))
            ):
                return candidate
        for value in payload.values():
            found = _find_access_token(value, depth + 1)
            if found is not None:
                return found
    if isinstance(payload, list):
        for value in payload[:32]:
            found = _find_access_token(value, depth + 1)
            if found is not None:
                return found
    return None


def _auth0_token(entries: dict[str, str]) -> str | None:
    for key, value in entries.items():
        if _AUTH0_MARKER not in key:
            continue
        found = _find_access_token(_json_object(value))
        if found is not None:
            return found
    return None


def is_internal_organization_id(value: str) -> bool:
    """Devin's internal ids look like ``org-<hex>`` / ``org_<hex>``."""
    cleaned = value.strip()
    if len(cleaned) < 8 or len(cleaned) > 128:
        return False
    if not (cleaned.startswith("org-") or cleaned.startswith("org_")):
        return False
    return cleaned[4:].replace("-", "").replace("_", "").isalnum()


def _organization_from_entries(
    entries: dict[str, str],
) -> tuple[str | None, str | None]:
    """(organization, internal_organization_id) -- whichever are present.

    The organization is what the usage endpoint keys on, and Devin writes
    it three different ways depending on how the account is shaped, so
    all three are read (same precedence CodexBar uses).
    """
    internal_id: str | None = None
    slug: str | None = None

    for key, value in entries.items():
        if key.startswith(_EXTERNAL_ORG_PREFIX):
            candidate = value.strip().strip('"')
            if internal_id is None and is_internal_organization_id(candidate):
                internal_id = candidate
            suffix = key[len(_EXTERNAL_ORG_PREFIX) :]
            if slug is None and suffix and suffix != "null":
                slug = suffix

    if slug is None:
        for key in entries:
            marker = key.find(_ORG_NAME_MARKER)
            if marker != -1:
                candidate = key[marker + len(_ORG_NAME_MARKER) :].strip()
                if candidate and candidate != "null":
                    slug = candidate
                    break

    if internal_id is None:
        for key in entries:
            if not key.startswith(_FEATURE_FLAG_ORG_PREFIX):
                continue
            candidate = key.split(":", 1)[-1].strip()
            if is_internal_organization_id(candidate):
                internal_id = candidate
                break

    if slug is not None:
        return f"org/{slug}", internal_id
    if internal_id is not None:
        return f"organizations/{internal_id}", internal_id
    return None, internal_id


def devin_session_from_entries(
    entries: dict[str, str], *, source_label: str
) -> BrowserSession | None:
    token = _auth1_token(entries) or _auth0_token(entries)
    if token is None:
        return None
    organization, internal_id = _organization_from_entries(entries)
    return BrowserSession(
        token=token,
        organization=organization,
        internal_organization_id=internal_id,
        source_label=source_label,
    )


def _session_rank(session: BrowserSession) -> int:
    return (0 if session.organization is None else 1) + (
        0 if session.internal_organization_id is None else 2
    )


CHROMIUM_FAMILY_PROFILE_ROOTS: tuple[tuple[str, str], ...] = (
    ("Chrome", "Google/Chrome"),
    ("Brave", "BraveSoftware/Brave-Browser"),
    ("Arc", "Arc/User Data"),
    ("Edge", "Microsoft Edge"),
    ("Vivaldi", "Vivaldi"),
)


def chromium_devin_sessions(home: Path) -> list[BrowserSession]:
    """Every Devin session found across Chromium-family profiles."""
    try:
        import rleveldb
    except ImportError:
        return []

    sessions: list[BrowserSession] = []
    support = Path(home) / "Library" / "Application Support"
    for label, relative in CHROMIUM_FAMILY_PROFILE_ROOTS:
        root = support / relative
        try:
            if not root.is_dir():
                continue
            candidates = [root / "Default" / "Local Storage" / "leveldb"]
            try:
                for entry in root.iterdir():
                    if entry.name.startswith("Profile ") and entry.is_dir():
                        candidates.append(entry / "Local Storage" / "leveldb")
            except OSError:
                pass
        except OSError:
            continue

        for ldb_path in candidates:
            if not ldb_path.is_dir():
                continue
            try:
                profile_name = ldb_path.parent.parent.name
                source_label = f"{label} {profile_name}"
                entries: dict[str, str] = {}
                with rleveldb.RawLevelDb(str(ldb_path)) as db:
                    for key, val in db.iterate_records_raw():
                        try:
                            key_str = key.decode("utf-8", errors="ignore")
                        except Exception:
                            continue
                        if "app.devin.ai" not in key_str:
                            continue
                        idx = key_str.rfind("")
                        clean_key = key_str[idx + 1:] if idx != -1 else key_str
                        val_str = ""
                        if val:
                            if val.startswith(b""):
                                val_str = val[1:].decode("utf-8", errors="ignore")
                            else:
                                val_str = val.decode("utf-8", errors="ignore")
                        entries[clean_key] = val_str
                if entries:
                    session = devin_session_from_entries(entries, source_label=source_label)
                    if session is not None:
                        sessions.append(session)
            except Exception:
                continue
    return sessions


def import_devin_sessions(home: Path) -> list[BrowserSession]:
    """Every Devin session found across Firefox-family profiles, best
    first -- "best" meaning the one that knows the most about which
    organization it belongs to, since usage is keyed on that."""
    sessions: list[BrowserSession] = []
    directory = origin_directory_name(DEVIN_ORIGIN)
    for label, profile in firefox_profile_directories(home):
        database = profile / "storage" / "default" / directory / "ls" / "data.sqlite"
        entries = read_local_storage(database)
        if not entries:
            continue
        session = devin_session_from_entries(entries, source_label=label)
        if session is not None:
            sessions.append(session)
    sessions.extend(chromium_devin_sessions(home))
    return sorted(sessions, key=_session_rank, reverse=True)


def import_devin_session_from_profile(
    *,
    home: Path,
    browser: str,
    profile: str,
) -> BrowserSession | None:
    """Read exactly one consented Firefox-family browser profile."""
    profile_root = firefox_profile_directory(
        Path(home),
        browser=browser,
        profile=profile,
    )
    if profile_root is None:
        return None
    database = _devin_database_for_profile(profile_root)
    if database is None:
        return None
    entries = read_local_storage(database)
    if not entries:
        return None
    label = next(
        (
            display
            for display, _relative in FIREFOX_FAMILY_PROFILE_ROOTS
            if display.lower() == str(browser).lower()
        ),
        str(browser).title(),
    )
    return devin_session_from_entries(entries, source_label=f"{label} {profile}")


def import_devin_session(home: Path) -> BrowserSession | None:
    sessions = import_devin_sessions(Path(home))
    return sessions[0] if sessions else None


__all__ = [
    "DEVIN_ORIGIN",
    "FIREFOX_FAMILY_PROFILE_ROOTS",
    "BrowserSession",
    "devin_session_from_entries",
    "firefox_profile_directories",
    "firefox_profile_directory",
    "import_devin_session",
    "import_devin_session_from_profile",
    "import_devin_sessions",
    "is_internal_organization_id",
    "origin_directory_name",
    "read_local_storage",
]
