from __future__ import annotations

import pytest

from sidepulse.provider_account_identity import project_provider_account_identity


def test_user_alias_precedes_safe_account_label() -> None:
    identity = project_provider_account_identity(
        provider_id="claude",
        source_instance_id="internal:profile-42",
        account_label="person@example.com",
        user_alias="Client Claude",
    )

    assert identity.primary_label == "Client Claude"
    assert identity.account_detail is None
    assert identity.full_label == "Client Claude · person@example.com"
    assert "internal:profile-42" not in repr(identity)


@pytest.mark.parametrize(
    "unsafe_label",
    (
        "org-7535461b-1234-4abc-9def-0123456789ab",
        "d3a51c1c-2b9a-4371-b335-3928397be5cd",
        "acct_8f14e45fceea167a5a36dedd4bea2543",
        "/Users/person/.codex/profiles/work",
        "4e07408562bedb8b60ce05c1decfe3ad16b722309",
        "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9",
    ),
)
def test_opaque_or_private_account_labels_use_a_stable_safe_suffix(
    unsafe_label: str,
) -> None:
    first = project_provider_account_identity(
        provider_id="codex",
        source_instance_id="profile:private-workspace",
        account_label=unsafe_label,
    )
    second = project_provider_account_identity(
        provider_id="codex",
        source_instance_id="profile:private-workspace",
        account_label=unsafe_label,
    )

    assert first == second
    assert first.primary_label.startswith("Codex #")
    assert first.account_detail is None
    assert unsafe_label not in repr(first)
    assert "private-workspace" not in repr(first)


def test_privacy_mode_suppresses_alias_and_account_detail() -> None:
    identity = project_provider_account_identity(
        provider_id="claude",
        source_instance_id="work",
        account_label="person@example.com",
        user_alias="Client Claude",
        privacy_mode=True,
    )

    assert identity.primary_label == "Claude"
    assert identity.account_detail is None
    assert identity.full_label == identity.primary_label
    assert identity.collision_suffix == "private"
    assert "person@example.com" not in repr(identity)
    assert "Client Claude" not in repr(identity)


def test_privacy_mode_strings_do_not_depend_on_private_account_label() -> None:
    first = project_provider_account_identity(
        provider_id="grok",
        source_instance_id="default",
        account_label="first.private@example.com",
        user_alias="Personal",
        privacy_mode=True,
    )
    second = project_provider_account_identity(
        provider_id="grok",
        source_instance_id="default",
        account_label="second.private@example.com",
        user_alias="Work",
        privacy_mode=True,
    )

    assert first == second
    assert first.primary_label == "Grok"
    assert first.full_label == "Grok"
