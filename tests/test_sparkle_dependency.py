from __future__ import annotations

import hashlib
import io
import plistlib
import stat
import tarfile
from pathlib import Path
from types import ModuleType

import pytest


def _module() -> ModuleType:
    try:
        from scripts import prepare_sparkle
    except ImportError:
        pytest.fail("scripts.prepare_sparkle is missing", pytrace=False)
    return prepare_sparkle


def _add_directory(archive: tarfile.TarFile, name: str, mode: int = 0o755) -> None:
    member = tarfile.TarInfo(name)
    member.type = tarfile.DIRTYPE
    member.mode = mode
    archive.addfile(member)


def _add_file(
    archive: tarfile.TarFile,
    name: str,
    payload: bytes,
    mode: int = 0o644,
) -> None:
    member = tarfile.TarInfo(name)
    member.size = len(payload)
    member.mode = mode
    archive.addfile(member, io.BytesIO(payload))


def _add_symlink(archive: tarfile.TarFile, name: str, target: str) -> None:
    member = tarfile.TarInfo(name)
    member.type = tarfile.SYMTYPE
    member.linkname = target
    member.mode = 0o777
    archive.addfile(member)


def _build_archive(
    path: Path,
    *,
    framework_version: str = "2.9.6",
    omit: frozenset[str] = frozenset(),
    extra_member: tarfile.TarInfo | None = None,
) -> str:
    entries: list[tuple[str, str, bytes | str, int]] = [
        ("dir", "./Sparkle.framework", b"", 0o755),
        ("dir", "./Sparkle.framework/Versions", b"", 0o755),
        ("dir", "./Sparkle.framework/Versions/B", b"", 0o755),
        ("dir", "./Sparkle.framework/Versions/B/Resources", b"", 0o755),
        (
            "file",
            "./Sparkle.framework/Versions/B/Resources/Info.plist",
            plistlib.dumps(
                {
                    "CFBundleIdentifier": "org.sparkle-project.Sparkle",
                    "CFBundleShortVersionString": framework_version,
                    "CFBundleVersion": "2061",
                }
            ),
            0o644,
        ),
        ("file", "./Sparkle.framework/Versions/B/Sparkle", b"framework", 0o755),
        ("symlink", "./Sparkle.framework/Versions/Current", "B", 0o777),
        (
            "symlink",
            "./Sparkle.framework/Resources",
            "Versions/Current/Resources",
            0o777,
        ),
        (
            "symlink",
            "./Sparkle.framework/Sparkle",
            "Versions/Current/Sparkle",
            0o777,
        ),
        ("dir", "./bin", b"", 0o755),
        ("file", "./bin/generate_appcast", b"generate_appcast", 0o755),
        ("file", "./bin/generate_keys", b"generate_keys", 0o755),
        ("file", "./bin/sign_update", b"sign_update", 0o755),
        ("file", "./LICENSE", b"MIT License\n", 0o644),
        ("file", "./CHANGELOG", b"not selected\n", 0o644),
    ]
    with tarfile.open(path, "w:xz", format=tarfile.PAX_FORMAT) as archive:
        for kind, name, payload, mode in entries:
            normalized_name = name.removeprefix("./")
            if normalized_name in omit:
                continue
            if kind == "dir":
                _add_directory(archive, name, mode)
            elif kind == "file":
                assert isinstance(payload, bytes)
                _add_file(archive, name, payload, mode)
            else:
                assert isinstance(payload, str)
                _add_symlink(archive, name, payload)
        if extra_member is not None:
            archive.addfile(extra_member)
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_preparer_extracts_only_the_reviewed_distribution_with_metadata_preserved(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepare_sparkle = _module()
    archive = tmp_path / "Sparkle-2.9.6.tar.xz"
    digest = _build_archive(archive)
    output = tmp_path / "prepared"
    monkeypatch.setattr(prepare_sparkle, "SPARKLE_ARCHIVE_SHA256", digest)

    prepared = prepare_sparkle.prepare_sparkle(
        output,
        archive=archive,
    )

    assert prepared == output
    assert {path.name for path in output.iterdir()} == {"Sparkle.framework", "bin", "LICENSE"}
    assert (output / "Sparkle.framework" / "Versions" / "Current").is_symlink()
    assert (output / "Sparkle.framework" / "Versions" / "Current").readlink() == Path("B")
    assert (output / "Sparkle.framework" / "Sparkle").readlink() == Path(
        "Versions/Current/Sparkle"
    )
    assert stat.S_IMODE((output / "bin" / "generate_appcast").stat().st_mode) == 0o755
    assert stat.S_IMODE((output / "LICENSE").stat().st_mode) == 0o644
    assert not (output / "CHANGELOG").exists()
    assert not any(path.name.startswith(".prepare-sparkle") for path in output.iterdir())
    assert {path.name for path in tmp_path.iterdir()} == {archive.name, output.name}


def test_preparer_downloads_the_pinned_archive_inside_the_selected_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepare_sparkle = _module()
    fixture = tmp_path / "fixture.tar.xz"
    digest = _build_archive(fixture)
    payload = fixture.read_bytes()
    output = tmp_path / "prepared"
    monkeypatch.setattr(prepare_sparkle, "SPARKLE_ARCHIVE_SHA256", digest)

    class Response(io.BytesIO):
        def __enter__(self) -> Response:
            return self

        def __exit__(self, *_args: object) -> None:
            self.close()

    def open_pinned_url(url: str, *, timeout: int) -> Response:
        if url != prepare_sparkle.SPARKLE_ARCHIVE_URL or timeout != 60:
            raise AssertionError("the preparer did not request the pinned Sparkle archive")
        return Response(payload)

    monkeypatch.setattr(prepare_sparkle.urllib.request, "urlopen", open_pinned_url)

    prepare_sparkle.prepare_sparkle(output)

    assert (output / "Sparkle.framework").is_dir()
    assert {path.name for path in tmp_path.iterdir()} == {fixture.name, output.name}


def test_preparer_accepts_the_official_archive_without_an_explicit_framework_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepare_sparkle = _module()
    archive = tmp_path / "Sparkle-2.9.6.tar.xz"
    digest = _build_archive(archive, omit=frozenset({"Sparkle.framework"}))
    output = tmp_path / "prepared"
    monkeypatch.setattr(prepare_sparkle, "SPARKLE_ARCHIVE_SHA256", digest)

    prepare_sparkle.prepare_sparkle(output, archive=archive)

    assert (output / "Sparkle.framework" / "Versions" / "B" / "Sparkle").is_file()


def test_preparer_rejects_an_archive_digest_mismatch_before_writing_output(
    tmp_path: Path,
) -> None:
    prepare_sparkle = _module()
    archive = tmp_path / "Sparkle-2.9.6.tar.xz"
    _build_archive(archive)
    output = tmp_path / "prepared"

    with pytest.raises(prepare_sparkle.SparklePreparationError, match="SHA-256 mismatch"):
        prepare_sparkle.prepare_sparkle(output, archive=archive)

    assert not output.exists()


def test_preparer_rejects_a_symlinked_supplied_archive(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepare_sparkle = _module()
    archive = tmp_path / "Sparkle-2.9.6.tar.xz"
    digest = _build_archive(archive)
    archive_link = tmp_path / "Sparkle-current.tar.xz"
    archive_link.symlink_to(archive.name)
    output = tmp_path / "prepared"
    monkeypatch.setattr(prepare_sparkle, "SPARKLE_ARCHIVE_SHA256", digest)

    with pytest.raises(
        prepare_sparkle.SparklePreparationError,
        match="supplied Sparkle archive must not be a symbolic link",
    ):
        prepare_sparkle.prepare_sparkle(output, archive=archive_link)

    assert not output.exists()


def test_preparer_hashes_and_extracts_one_private_snapshot_of_a_supplied_archive(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepare_sparkle = _module()
    archive = tmp_path / "Sparkle-2.9.6.tar.xz"
    digest = _build_archive(archive)
    output = tmp_path / "prepared"
    verified_paths: list[Path] = []
    real_verify = prepare_sparkle._verify_digest
    monkeypatch.setattr(prepare_sparkle, "SPARKLE_ARCHIVE_SHA256", digest)

    def verify_snapshot(path: Path) -> None:
        verified_paths.append(path)
        real_verify(path)
        archive.write_bytes(b"replaced after snapshot")

    monkeypatch.setattr(prepare_sparkle, "_verify_digest", verify_snapshot)

    prepare_sparkle.prepare_sparkle(output, archive=archive)

    assert len(verified_paths) == 1
    assert verified_paths[0] != archive
    assert verified_paths[0].parent.name == ".prepare-sparkle"
    assert (output / "Sparkle.framework" / "Versions" / "B" / "Sparkle").is_file()


@pytest.mark.parametrize(
    ("member", "message"),
    (
        (tarfile.TarInfo("../escaped"), "unsafe archive path"),
        (tarfile.TarInfo("/absolute"), "unsafe archive path"),
        (
            tarfile.TarInfo("Sparkle.framework/unsafe-link"),
            "unsafe symbolic link",
        ),
        (
            tarfile.TarInfo("Sparkle.framework/unsafe-hardlink"),
            "hard link",
        ),
        (tarfile.TarInfo("Sparkle.framework/fifo"), "unsupported archive member"),
    ),
)
def test_preparer_rejects_unsafe_archive_members_without_escaping_output(
    tmp_path: Path,
    member: tarfile.TarInfo,
    message: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepare_sparkle = _module()
    if member.name.endswith("unsafe-link"):
        member.type = tarfile.SYMTYPE
        member.linkname = "../../escaped"
    elif member.name.endswith("unsafe-hardlink"):
        member.type = tarfile.LNKTYPE
        member.linkname = "Sparkle.framework/Versions/B/Sparkle"
    elif member.name.endswith("fifo"):
        member.type = tarfile.FIFOTYPE
    else:
        member.type = tarfile.REGTYPE
        member.size = 0
    archive = tmp_path / "unsafe.tar.xz"
    digest = _build_archive(archive, extra_member=member)
    output = tmp_path / "prepared"
    monkeypatch.setattr(prepare_sparkle, "SPARKLE_ARCHIVE_SHA256", digest)

    with pytest.raises(prepare_sparkle.SparklePreparationError, match=message):
        prepare_sparkle.prepare_sparkle(
            output,
            archive=archive,
        )

    assert not (tmp_path / "escaped").exists()
    assert not output.exists()


@pytest.mark.parametrize(
    ("omitted_member", "message"),
    (
        ("Sparkle.framework/Versions/B/Sparkle", "framework member"),
        ("bin/sign_update", "distribution member"),
        ("LICENSE", "distribution member"),
    ),
)
def test_preparer_fails_closed_when_required_distribution_members_are_missing(
    tmp_path: Path,
    omitted_member: str,
    message: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepare_sparkle = _module()
    archive = tmp_path / "incomplete.tar.xz"
    digest = _build_archive(archive, omit=frozenset({omitted_member}))
    output = tmp_path / "prepared"
    monkeypatch.setattr(prepare_sparkle, "SPARKLE_ARCHIVE_SHA256", digest)

    with pytest.raises(prepare_sparkle.SparklePreparationError, match=message):
        prepare_sparkle.prepare_sparkle(
            output,
            archive=archive,
        )

    assert not output.exists()


def test_preparer_rejects_a_framework_version_mismatch_without_partial_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepare_sparkle = _module()
    archive = tmp_path / "wrong-version.tar.xz"
    digest = _build_archive(archive, framework_version="2.9.5")
    output = tmp_path / "prepared"
    monkeypatch.setattr(prepare_sparkle, "SPARKLE_ARCHIVE_SHA256", digest)

    with pytest.raises(prepare_sparkle.SparklePreparationError, match="version mismatch"):
        prepare_sparkle.prepare_sparkle(
            output,
            archive=archive,
        )

    assert not output.exists()


def test_cli_accepts_output_and_optional_archive_and_prints_one_short_line(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    prepare_sparkle = _module()
    archive = tmp_path / "Sparkle.tar.xz"
    archive.touch()
    output = tmp_path / "prepared"
    calls: list[tuple[Path, Path | None]] = []

    def prepare(selected_output: Path, *, archive: Path | None = None) -> Path:
        calls.append((selected_output, archive))
        return selected_output

    monkeypatch.setattr(prepare_sparkle, "prepare_sparkle", prepare)

    result = prepare_sparkle.main(["--output", str(output), "--archive", str(archive)])

    captured = capsys.readouterr()
    assert result == 0
    assert calls == [(output, archive)]
    assert captured.out == f"Prepared Sparkle 2.9.6 at {output}\n"
    assert captured.err == ""
