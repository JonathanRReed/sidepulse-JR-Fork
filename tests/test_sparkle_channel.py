from __future__ import annotations

import base64
import hashlib
import importlib
import json
import plistlib
import subprocess
import sys
import textwrap
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from scripts import package_sparkle_archive

ROOT = Path(__file__).resolve().parents[1]
SPARKLE_NAMESPACE = "http://www.andymatuschak.org/xml-namespaces/sparkle"
PUBLIC_KEY = "IlvZMoPh67naKxN2ZvlnfdHildsgGxPWeEi8IOhVQ+8="
PUBLIC_KEY_FINGERPRINT = "9c134249398dd15c364a29451de3d81436d8eda97a0c706fa59047e6607f59ac"
ITEM_SIGNATURE = base64.b64encode(b"i" * 64).decode("ascii")
FEED_SIGNATURE = base64.b64encode(b"f" * 64).decode("ascii")
CANDIDATE_ID = "c" * 64


def _module():
    return importlib.import_module("scripts.generate_sparkle_channel")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _archive(
    tmp_path: Path,
    *,
    version: str = "0.5.0",
    build: str = "50",
    architecture: str = "arm64",
    public_key: str = PUBLIC_KEY,
) -> Path:
    app = tmp_path / f"app-{version}-{architecture}" / "SidePulse.app"
    executable = app / "Contents" / "MacOS" / "SidePulse"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"signed and notarized app")
    executable.chmod(0o755)
    with (app / "Contents" / "Info.plist").open("wb") as stream:
        plistlib.dump(
            {
                "CFBundleIdentifier": "io.sidepulse.app",
                "CFBundleName": "JR Bar",
                "CFBundleShortVersionString": version,
                "CFBundleVersion": build,
                "LSMinimumSystemVersion": "11.0",
                "SUFeedURL": (
                    "https://github.com/JonathanRReed/sidepulse-JR-Fork/"
                    "releases/download/updates/appcast.xml"
                ),
                "SUPublicEDKey": public_key,
                "SURequireSignedFeed": True,
            },
            stream,
        )
    output_dir = tmp_path / "archives"
    output_dir.mkdir(exist_ok=True)
    output = output_dir / f"SidePulse-{version}-{architecture}.zip"
    package_sparkle_archive.package_archive(app=app, output=output)
    return output


def _fake_sparkle(
    tmp_path: Path,
    *,
    overrides: dict[str, object] | None = None,
) -> Path:
    distribution = tmp_path / "Sparkle-2.9.6"
    bin_dir = distribution / "bin"
    framework_resources = distribution / "Sparkle.framework" / "Versions" / "B" / "Resources"
    bin_dir.mkdir(parents=True, exist_ok=True)
    framework_resources.mkdir(parents=True, exist_ok=True)
    with (framework_resources / "Info.plist").open("wb") as stream:
        plistlib.dump(
            {
                "CFBundleIdentifier": "org.sparkle-project.Sparkle",
                "CFBundleShortVersionString": "2.9.6",
            },
            stream,
        )
    behavior = {
        "current_archive": "SidePulse-0.5.0-arm64.zip",
        "item_signature": ITEM_SIGNATURE,
        "feed_signature": FEED_SIGNATURE,
        "signed_feed_marker": True,
        "generate_exit": 0,
        "sign_exit": 0,
        "verify_exit": 0,
        "public_key": PUBLIC_KEY,
        "require_previous": False,
    }
    behavior.update(overrides or {})
    (distribution / "behavior.json").write_text(json.dumps(behavior), encoding="utf-8")
    (distribution / "LICENSE").write_text("Sparkle test license\n", encoding="utf-8")

    generator = bin_dir / "generate_appcast"
    generator.write_text(
        textwrap.dedent(
            """\
            #!/usr/bin/env python3
            import json
            import plistlib
            import sys
            import zipfile
            from pathlib import Path
            from xml.sax.saxutils import escape, quoteattr

            root = Path(__file__).resolve().parent.parent
            behavior = json.loads((root / "behavior.json").read_text(encoding="utf-8"))
            args = sys.argv[1:]
            (root / "generate-argv.json").write_text(json.dumps(args), encoding="utf-8")
            print("PRIVATE-KEY-MATERIAL-MUST-NOT-LEAK")
            print("PRIVATE-KEY-MATERIAL-MUST-NOT-LEAK", file=sys.stderr)
            if behavior["generate_exit"]:
                raise SystemExit(behavior["generate_exit"])
            stage = Path(args[-1])
            output = Path(args[args.index("-o") + 1])
            archive = stage / behavior["current_archive"]
            if behavior["require_previous"]:
                assert (stage / "appcast.xml").is_file()
                assert (stage / "SidePulse-0.4.0-arm64.zip").is_file()
            with zipfile.ZipFile(archive) as source:
                info = plistlib.loads(source.read("SidePulse.app/Contents/Info.plist"))
            version = str(info["CFBundleShortVersionString"])
            build = str(info["CFBundleVersion"])
            assert args[args.index("--versions") + 1] == build
            prefix = args[args.index("--download-url-prefix") + 1]
            channel = args[args.index("--channel") + 1] if "--channel" in args else None
            phased = args[args.index("--phased-rollout-interval") + 1] if "--phased-rollout-interval" in args else None
            version = str(behavior.get("version", version))
            build = str(behavior.get("build", build))
            url = str(behavior.get("url", prefix + archive.name))
            length = str(behavior.get("length", archive.stat().st_size))
            signature = str(behavior.get("item_signature", ""))
            channel = behavior.get("channel", channel)
            phased = behavior.get("phased", phased)
            minimum_system_version = str(
                behavior.get("minimum_system_version", info["LSMinimumSystemVersion"])
            )
            item_parts = [
                "<item>",
                f"<title>JR Bar {escape(version)}</title>",
                f"<sparkle:version>{escape(build)}</sparkle:version>",
                f"<sparkle:shortVersionString>{escape(version)}</sparkle:shortVersionString>",
                f"<sparkle:minimumSystemVersion>{escape(minimum_system_version)}</sparkle:minimumSystemVersion>",
                "<pubDate>Sun, 30 Aug 2026 12:00:00 +0000</pubDate>",
            ]
            if channel is not None:
                item_parts.append(f"<sparkle:channel>{escape(str(channel))}</sparkle:channel>")
            if phased is not None:
                item_parts.append(f"<sparkle:phasedRolloutInterval>{escape(str(phased))}</sparkle:phasedRolloutInterval>")
            item_parts.append(
                f"<enclosure url={quoteattr(url)} length={quoteattr(length)} "
                f'type="application/octet-stream" sparkle:edSignature={quoteattr(signature)}/>'
            )
            item_parts.append("</item>")
            previous = ""
            if behavior["require_previous"]:
                previous = (
                    "<item><sparkle:version>40</sparkle:version>"
                    "<sparkle:shortVersionString>0.4.0</sparkle:shortVersionString>"
                    f'<enclosure url="https://example.invalid/SidePulse-0.4.0-arm64.zip" length="1" '
                    f'sparkle:edSignature="{signature}"/></item>'
                )
            xml = (
                '<?xml version="1.0" encoding="utf-8"?>'
                '<rss xmlns:sparkle="http://www.andymatuschak.org/xml-namespaces/sparkle" version="2.0">'
                '<channel><title>JR Bar Updates</title>' + "".join(item_parts) + previous + "</channel></rss>"
            )
            output.write_text(xml, encoding="utf-8")
            """
        ),
        encoding="utf-8",
    )
    generator.chmod(0o755)

    key_reader = bin_dir / "generate_keys"
    key_reader.write_text(
        textwrap.dedent(
            """\
            #!/usr/bin/env python3
            import json
            import sys
            from pathlib import Path

            root = Path(__file__).resolve().parent.parent
            behavior = json.loads((root / "behavior.json").read_text(encoding="utf-8"))
            args = sys.argv[1:]
            (root / "key-argv.json").write_text(json.dumps(args), encoding="utf-8")
            print(behavior["public_key"])
            """
        ),
        encoding="utf-8",
    )
    key_reader.chmod(0o755)

    signer = bin_dir / "sign_update"
    signer.write_text(
        textwrap.dedent(
            """\
            #!/usr/bin/env python3
            import json
            import sys
            from pathlib import Path

            root = Path(__file__).resolve().parent.parent
            behavior = json.loads((root / "behavior.json").read_text(encoding="utf-8"))
            args = sys.argv[1:]
            (root / "sign-argv.json").write_text(json.dumps(args), encoding="utf-8")
            calls_path = root / "sign-calls.json"
            calls = json.loads(calls_path.read_text(encoding="utf-8")) if calls_path.exists() else []
            calls.append(args)
            calls_path.write_text(json.dumps(calls), encoding="utf-8")
            print("PRIVATE-KEY-MATERIAL-MUST-NOT-LEAK")
            print("PRIVATE-KEY-MATERIAL-MUST-NOT-LEAK", file=sys.stderr)
            if "--verify" in args:
                raise SystemExit(behavior["verify_exit"])
            if behavior["sign_exit"]:
                raise SystemExit(behavior["sign_exit"])
            appcast = Path(args[-1])
            if behavior["signed_feed_marker"]:
                text = appcast.read_text(encoding="utf-8")
                marker = (
                    "<!-- sparkle-signatures:\\n"
                    f"edSignature: {behavior['feed_signature']}\\n"
                    f"length: {len(text.encode('utf-8'))}\\n"
                    "-->"
                )
                appcast.write_text(text + marker, encoding="utf-8")
            """
        ),
        encoding="utf-8",
    )
    signer.chmod(0o755)
    sparkle_channel = _module()
    sparkle_channel.EXPECTED_SPARKLE_DISTRIBUTION_SHA256 = (
        sparkle_channel._sparkle_distribution_digest(distribution)
    )
    return distribution


def _current_item(appcast: Path) -> ET.Element:
    root = ET.fromstring(appcast.read_bytes())
    items = root.findall("./channel/item")
    assert items
    return items[0]


def test_generate_stable_channel_signs_exact_feed_and_writes_candidate_metadata(
    tmp_path: Path,
) -> None:
    sparkle_channel = _module()
    archive = _archive(tmp_path)
    distribution = _fake_sparkle(tmp_path)
    output_dir = tmp_path / "dist"

    outputs = sparkle_channel.generate_channel(
        sparkle_distribution=distribution,
        archive=archive,
        output_dir=output_dir,
        candidate_id=CANDIDATE_ID,
    )

    assert outputs.appcast == output_dir / "appcast.xml"
    assert outputs.metadata == output_dir / "jr-bar-update-channel.json"
    item = _current_item(outputs.appcast)
    enclosure = item.find("enclosure")
    assert enclosure is not None
    assert item.find(f"{{{SPARKLE_NAMESPACE}}}channel") is None
    assert item.findtext(f"{{{SPARKLE_NAMESPACE}}}phasedRolloutInterval") == "86400"
    assert item.findtext(f"{{{SPARKLE_NAMESPACE}}}minimumSystemVersion") == "11.0"
    assert enclosure.attrib["url"] == (
        "https://github.com/JonathanRReed/sidepulse-JR-Fork/"
        "releases/download/v0.5.0/SidePulse-0.5.0-arm64.zip"
    )
    assert enclosure.attrib[f"{{{SPARKLE_NAMESPACE}}}edSignature"] == ITEM_SIGNATURE

    metadata = json.loads(outputs.metadata.read_text(encoding="utf-8"))
    assert metadata == {
        "document": "jr-bar-update-channel",
        "schema_version": 1,
        "candidate_id": CANDIDATE_ID,
        "channel": "stable",
        "version": "0.5.0",
        "build": "50",
        "architecture": "arm64",
        "feed_url": (
            "https://github.com/JonathanRReed/sidepulse-JR-Fork/"
            "releases/download/updates/appcast.xml"
        ),
        "download_url": enclosure.attrib["url"],
        "phased_rollout_interval_seconds": 86400,
        "public_key_fingerprint_sha256": PUBLIC_KEY_FINGERPRINT,
        "archive": {
            "name": archive.name,
            "bytes": archive.stat().st_size,
            "sha256": _sha256(archive),
            "ed_signature": ITEM_SIGNATURE,
        },
        "appcast": {
            "name": "appcast.xml",
            "bytes": outputs.appcast.stat().st_size,
            "sha256": _sha256(outputs.appcast),
        },
    }
    generate_args = json.loads((distribution / "generate-argv.json").read_text(encoding="utf-8"))
    sign_args = json.loads((distribution / "sign-argv.json").read_text(encoding="utf-8"))
    key_args = json.loads((distribution / "key-argv.json").read_text(encoding="utf-8"))
    for args in (generate_args, sign_args, key_args):
        assert args[args.index("--account") + 1] == "io.sidepulse.app"
        assert "--ed-key-file" not in args
    sign_calls = json.loads((distribution / "sign-calls.json").read_text(encoding="utf-8"))
    assert ["--verify" in args for args in sign_calls] == [False, True, True, True, True]
    assert Path(sign_calls[-1][-2]).name == archive.name
    assert sign_calls[-1][-1] == ITEM_SIGNATURE


def test_generate_channel_uses_the_exact_staged_appcast_output_path(
    tmp_path: Path,
) -> None:
    sparkle_channel = _module()
    archive = _archive(tmp_path)
    distribution = _fake_sparkle(tmp_path)

    sparkle_channel.generate_channel(
        sparkle_distribution=distribution,
        archive=archive,
        output_dir=tmp_path / "dist",
        candidate_id=CANDIDATE_ID,
    )

    generate_args = json.loads(
        (distribution / "generate-argv.json").read_text(encoding="utf-8")
    )
    staged_appcast = Path(generate_args[generate_args.index("-o") + 1])
    staging_directory = Path(generate_args[-1])
    assert staged_appcast == staging_directory / "appcast.xml"
    assert staged_appcast.is_absolute()


def test_generate_channel_rejects_a_keychain_account_with_another_public_key(
    tmp_path: Path,
) -> None:
    sparkle_channel = _module()
    archive = _archive(tmp_path)
    distribution = _fake_sparkle(
        tmp_path,
        overrides={"public_key": "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="},
    )

    with pytest.raises(
        sparkle_channel.SparkleChannelError,
        match="pinned Sparkle key",
    ):
        sparkle_channel.generate_channel(
            sparkle_distribution=distribution,
            archive=archive,
            output_dir=tmp_path / "dist",
            candidate_id=CANDIDATE_ID,
        )

    assert not (distribution / "generate-argv.json").exists()


def test_generate_beta_channel_encodes_beta_without_phased_rollout(tmp_path: Path) -> None:
    sparkle_channel = _module()
    archive = _archive(tmp_path)
    distribution = _fake_sparkle(tmp_path)

    outputs = sparkle_channel.generate_channel(
        sparkle_distribution=distribution,
        archive=archive,
        output_dir=tmp_path / "dist",
        candidate_id=CANDIDATE_ID,
        channel="beta",
    )

    item = _current_item(outputs.appcast)
    assert item.findtext(f"{{{SPARKLE_NAMESPACE}}}channel") == "beta"
    assert item.find(f"{{{SPARKLE_NAMESPACE}}}phasedRolloutInterval") is None
    metadata = json.loads(outputs.metadata.read_text(encoding="utf-8"))
    assert metadata["channel"] == "beta"
    assert metadata["phased_rollout_interval_seconds"] is None


def test_generate_channel_stages_previous_feed_and_retained_archive(tmp_path: Path) -> None:
    sparkle_channel = _module()
    archive = _archive(tmp_path)
    previous_archive = _archive(tmp_path, version="0.4.0", build="40")
    previous_appcast = tmp_path / "previous-appcast.xml"
    previous_length = previous_archive.stat().st_size
    previous_appcast.write_text(
        '<?xml version="1.0"?>'
        f'<rss xmlns:sparkle="{SPARKLE_NAMESPACE}"><channel><item>'
        f'<enclosure url="https://github.com/JonathanRReed/sidepulse-JR-Fork/releases/download/v0.4.0/{previous_archive.name}" '
        f'length="{previous_length}" sparkle:edSignature="{ITEM_SIGNATURE}"/>'
        "</item></channel></rss>"
        f"<!-- sparkle-signatures: edSignature: {FEED_SIGNATURE} length: 42 -->",
        encoding="utf-8",
    )
    distribution = _fake_sparkle(tmp_path, overrides={"require_previous": True})

    outputs = sparkle_channel.generate_channel(
        sparkle_distribution=distribution,
        archive=archive,
        output_dir=tmp_path / "dist",
        candidate_id=CANDIDATE_ID,
        previous_appcast=previous_appcast,
        previous_archives=(previous_archive,),
    )

    root = ET.fromstring(outputs.appcast.read_bytes())
    assert [item.findtext(f"{{{SPARKLE_NAMESPACE}}}shortVersionString") for item in root.findall("./channel/item")] == [
        "0.5.0",
        "0.4.0",
    ]
    sign_calls = json.loads((distribution / "sign-calls.json").read_text(encoding="utf-8"))
    assert ["--verify" in args for args in sign_calls] == [
        True,
        True,
        False,
        True,
        True,
        True,
        True,
    ]


@pytest.mark.parametrize(
    ("overrides", "message"),
    (
        ({"version": "9.9.9"}, "current version"),
        ({"build": "999"}, "current build"),
        ({"url": "https://example.invalid/substituted.zip"}, "download URL"),
        ({"length": 1}, "length"),
        ({"item_signature": "not-base64"}, "EdDSA signature"),
        ({"channel": "beta"}, "stable channel"),
        ({"phased": "60"}, "phased rollout"),
        ({"minimum_system_version": "10.13"}, "minimum system version"),
        ({"signed_feed_marker": False}, "signed-feed marker"),
    ),
)
def test_generate_channel_rejects_tampered_or_misencoded_feed(
    tmp_path: Path,
    overrides: dict[str, object],
    message: str,
) -> None:
    sparkle_channel = _module()
    archive = _archive(tmp_path)
    distribution = _fake_sparkle(tmp_path, overrides=overrides)
    output_dir = tmp_path / "dist"

    with pytest.raises(sparkle_channel.SparkleChannelError, match=message):
        sparkle_channel.generate_channel(
            sparkle_distribution=distribution,
            archive=archive,
            output_dir=output_dir,
            candidate_id=CANDIDATE_ID,
        )

    assert not (output_dir / "appcast.xml").exists()
    assert not (output_dir / "jr-bar-update-channel.json").exists()


def test_generate_channel_preserves_existing_outputs_when_a_tool_fails(tmp_path: Path) -> None:
    sparkle_channel = _module()
    archive = _archive(tmp_path)
    distribution = _fake_sparkle(tmp_path, overrides={"sign_exit": 17})
    output_dir = tmp_path / "dist"
    output_dir.mkdir()
    appcast = output_dir / "appcast.xml"
    metadata = output_dir / "jr-bar-update-channel.json"
    appcast.write_bytes(b"prior signed feed")
    metadata.write_bytes(b"prior metadata")

    with pytest.raises(sparkle_channel.SparkleChannelError, match=r"sign_update.*17") as failure:
        sparkle_channel.generate_channel(
            sparkle_distribution=distribution,
            archive=archive,
            output_dir=output_dir,
            candidate_id=CANDIDATE_ID,
        )

    assert "PRIVATE-KEY-MATERIAL-MUST-NOT-LEAK" not in str(failure.value)
    assert appcast.read_bytes() == b"prior signed feed"
    assert metadata.read_bytes() == b"prior metadata"


def test_channel_cli_uses_keychain_without_leaking_captured_tool_output(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    sparkle_channel = _module()
    archive = _archive(tmp_path)
    distribution = _fake_sparkle(tmp_path)
    output_dir = tmp_path / "dist"

    result = sparkle_channel.main(
        [
            "--sparkle-distribution",
            str(distribution),
            "--archive",
            str(archive),
            "--output-dir",
            str(output_dir),
            "--candidate-id",
            CANDIDATE_ID,
        ]
    )
    captured = capsys.readouterr()

    assert result == 0, captured.err
    assert captured.out.splitlines() == [
        str(output_dir / "appcast.xml"),
        str(output_dir / "jr-bar-update-channel.json"),
    ]
    assert "PRIVATE-KEY-MATERIAL-MUST-NOT-LEAK" not in captured.out
    assert "PRIVATE-KEY-MATERIAL-MUST-NOT-LEAK" not in captured.err
    assert "PRIVATE-KEY-MATERIAL-MUST-NOT-LEAK" not in (output_dir / "jr-bar-update-channel.json").read_text(
        encoding="utf-8"
    )
    for record_name in ("generate-argv.json", "sign-argv.json", "key-argv.json"):
        args = json.loads((distribution / record_name).read_text(encoding="utf-8"))
        assert args[args.index("--account") + 1] == "io.sidepulse.app"
        assert "--ed-key-file" not in args


def test_channel_cli_rejects_private_key_file_options(tmp_path: Path) -> None:
    archive = _archive(tmp_path)
    distribution = _fake_sparkle(tmp_path)

    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "generate_sparkle_channel.py"),
            "--sparkle-distribution",
            str(distribution),
            "--archive",
            str(archive),
            "--output-dir",
            str(tmp_path / "dist"),
            "--candidate-id",
            CANDIDATE_ID,
            "--ed-key-file",
            str(tmp_path / "private-key"),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 2
    assert "unrecognized arguments: --ed-key-file" in result.stderr
    assert not (tmp_path / "dist" / "appcast.xml").exists()


def test_generate_channel_rejects_unpinned_tools_and_candidate_identity(
    tmp_path: Path,
) -> None:
    sparkle_channel = _module()
    archive = _archive(tmp_path)
    distribution = _fake_sparkle(tmp_path)
    framework_plist = distribution / "Sparkle.framework" / "Versions" / "B" / "Resources" / "Info.plist"
    with framework_plist.open("wb") as stream:
        plistlib.dump(
            {
                "CFBundleIdentifier": "org.sparkle-project.Sparkle",
                "CFBundleShortVersionString": "2.9.5",
            },
            stream,
        )
    with pytest.raises(sparkle_channel.SparkleChannelError, match=r"2\.9\.6"):
        sparkle_channel.generate_channel(
            sparkle_distribution=distribution,
            archive=archive,
            output_dir=tmp_path / "dist",
            candidate_id=CANDIDATE_ID,
        )

    distribution = _fake_sparkle(tmp_path)
    with pytest.raises(sparkle_channel.SparkleChannelError, match="candidate ID"):
        sparkle_channel.generate_channel(
            sparkle_distribution=distribution,
            archive=archive,
            output_dir=tmp_path / "dist",
            candidate_id="../candidate",
        )


@pytest.mark.parametrize(
    "relative_path",
    [
        Path("bin/sign_update"),
        Path("Sparkle.framework/Versions/B/Sparkle"),
    ],
)
def test_generate_channel_rejects_post_preparation_distribution_tampering(
    tmp_path: Path,
    relative_path: Path,
) -> None:
    sparkle_channel = _module()
    archive = _archive(tmp_path)
    distribution = _fake_sparkle(tmp_path)
    target = distribution / relative_path
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"framework")
        target.chmod(0o755)
        sparkle_channel.EXPECTED_SPARKLE_DISTRIBUTION_SHA256 = (
            sparkle_channel._sparkle_distribution_digest(distribution)
        )
    target.write_bytes(target.read_bytes() + b"\ntampered\n")

    with pytest.raises(
        sparkle_channel.SparkleChannelError,
        match="distribution bytes do not match the pinned Sparkle release",
    ):
        sparkle_channel.generate_channel(
            sparkle_distribution=distribution,
            archive=archive,
            output_dir=tmp_path / "dist",
            candidate_id=CANDIDATE_ID,
        )

    assert not (distribution / "generate-argv.json").exists()
    assert not (distribution / "key-argv.json").exists()
    assert not (distribution / "sign-calls.json").exists()


def test_validate_channel_outputs_rehashes_and_rejects_byte_drift(tmp_path: Path) -> None:
    sparkle_channel = _module()
    archive = _archive(tmp_path)
    distribution = _fake_sparkle(tmp_path)
    outputs = sparkle_channel.generate_channel(
        sparkle_distribution=distribution,
        archive=archive,
        output_dir=tmp_path / "dist",
        candidate_id=CANDIDATE_ID,
    )
    outputs.appcast.write_bytes(outputs.appcast.read_bytes() + b"\n<!-- tampered -->\n")

    with pytest.raises(sparkle_channel.SparkleChannelError, match=r"appcast.*hash"):
        sparkle_channel.validate_channel_outputs(
            archive=archive,
            appcast=outputs.appcast,
            metadata=outputs.metadata,
            candidate_id=CANDIDATE_ID,
            sparkle_distribution=distribution,
        )


def test_validate_channel_outputs_cryptographically_rejects_matching_tampered_feed(
    tmp_path: Path,
) -> None:
    sparkle_channel = _module()
    archive = _archive(tmp_path)
    distribution = _fake_sparkle(tmp_path)
    outputs = sparkle_channel.generate_channel(
        sparkle_distribution=distribution,
        archive=archive,
        output_dir=tmp_path / "dist",
        candidate_id=CANDIDATE_ID,
    )
    replacement_signature = base64.b64encode(b"g" * 64).decode("ascii")
    outputs.appcast.write_text(
        outputs.appcast.read_text(encoding="utf-8").replace(
            FEED_SIGNATURE,
            replacement_signature,
        ),
        encoding="utf-8",
    )
    metadata = json.loads(outputs.metadata.read_text(encoding="utf-8"))
    metadata["appcast"] = {
        "name": "appcast.xml",
        "bytes": outputs.appcast.stat().st_size,
        "sha256": _sha256(outputs.appcast),
    }
    outputs.metadata.write_text(json.dumps(metadata), encoding="utf-8")
    behavior_path = distribution / "behavior.json"
    behavior = json.loads(behavior_path.read_text(encoding="utf-8"))
    behavior["verify_exit"] = 23
    behavior_path.write_text(json.dumps(behavior), encoding="utf-8")

    with pytest.raises(
        sparkle_channel.SparkleChannelError,
        match=r"final appcast verification.*23",
    ):
        sparkle_channel.validate_channel_outputs(
            archive=archive,
            appcast=outputs.appcast,
            metadata=outputs.metadata,
            candidate_id=CANDIDATE_ID,
            sparkle_distribution=distribution,
        )
