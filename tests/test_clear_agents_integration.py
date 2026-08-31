from __future__ import annotations

import threading
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

from sidepulse.capacity_types import SourceKey
from sidepulse.clear_agents import (
    ClearAgentsState,
    CompletionPresentationReceipt,
    completion_presentation_key,
)
from sidepulse.clear_agents_popover import ClearAgentsPopoverState
from sidepulse.clear_agents_store import load_clear_agents_state
from sidepulse.models import AgentMode, AgentStatus
from sidepulse.persistence_writer import (
    PersistenceDisposition,
    PersistenceOutcome,
    PersistenceReceipt,
)
from sidepulse.provider_facts import WorkIdentifier, WorkKey
from tests.test_sidepulse import isolate_controller


class ClearAgentsControllerIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        isolate_controller(self)

    @staticmethod
    def _completion(
        session: str,
        *,
        provider: str = "claude",
        source_instance: str = "global",
        updated_at: datetime | None = None,
    ) -> AgentStatus:
        return AgentStatus(
            provider=provider,
            agent_id=f"{provider}:session:{session}",
            display_name=f"{provider.title()} {session}",
            mode=AgentMode.COMPLETED,
            updated_at=updated_at or datetime.now(timezone.utc),
            event_name="Stop",
            session_id=session,
            work_key=WorkKey(
                SourceKey(provider, "hooks", source_instance, "live_agent_events"),
                WorkIdentifier(f"work:{session}"),
            ),
        )

    @staticmethod
    def _snapshot(*statuses: AgentStatus) -> SimpleNamespace:
        return SimpleNamespace(
            statuses=tuple(statuses),
            stale_statuses=(),
            collected_at=datetime.now(timezone.utc),
        )

    @staticmethod
    def _state_for(completed: AgentStatus) -> ClearAgentsState:
        key = completion_presentation_key(completed)
        assert key is not None
        return ClearAgentsState(
            generation=1,
            receipts=(
                CompletionPresentationReceipt(
                    key=key,
                    acknowledged_at_epoch=completed.updated_at.timestamp() + 1.0,
                ),
            ),
        )

    @staticmethod
    def _presenter() -> SimpleNamespace:
        return SimpleNamespace(
            refresh=MagicMock(),
            dismiss=MagicMock(),
            show=MagicMock(return_value=True),
        )

    def _stage_preview(self, completed: AgentStatus) -> SimpleNamespace:
        snapshot = self._snapshot(completed)
        self.controller.last_snapshot = snapshot
        self.controller._clear_agents_preview = self.status_bar.clear_agents_preview(
            snapshot,
            self.controller,
        )
        presenter = self._presenter()
        self.controller._clear_agents_presenter = presenter
        return presenter

    def test_exact_receipt_suppresses_only_the_acknowledged_mailbox_event(self) -> None:
        initial_time = datetime.now(timezone.utc) - timedelta(seconds=2)
        completed = self._completion("same-agent", updated_at=initial_time)
        self.controller.clear_agents_state = self._state_for(completed)

        self.controller.update_attention_projection(self._snapshot(completed))
        visible = {
            row.agent_id
            for section in self.controller.current_mailbox_projection.sections
            for row in section.rows
        }
        self.assertNotIn(completed.agent_id, visible)

        newer = replace(completed, updated_at=initial_time + timedelta(seconds=1))
        self.controller.update_attention_projection(self._snapshot(newer))
        visible = {
            row.agent_id
            for section in self.controller.current_mailbox_projection.sections
            for row in section.rows
        }
        self.assertIn(newer.agent_id, visible)

    def test_menu_visit_does_not_change_clear_receipts_or_clearable_count(self) -> None:
        completed = self._completion("visit-boundaries")
        snapshot = self._snapshot(completed)
        self.controller.last_snapshot = snapshot
        self.assertEqual(
            self.status_bar.clearable_presented_count(snapshot, self.controller),
            1,
        )
        self.assertEqual(
            len(self.status_bar.unseen_completions(snapshot, self.controller)),
            1,
        )

        self.controller.menuWillOpen_(None)

        self.assertEqual(
            self.status_bar.unseen_completions(snapshot, self.controller),
            [],
        )
        self.assertEqual(
            self.status_bar.clearable_presented_count(snapshot, self.controller),
            1,
        )
        self.assertEqual(self.controller.clear_agents_state, ClearAgentsState())

        state = self._state_for(completed)
        self.controller.clear_agents_state = state
        self.controller.menuWillOpen_(None)
        self.assertEqual(self.controller.clear_agents_state, state)

    def test_clear_agents_action_opens_preview_without_mutating_state(self) -> None:
        completed = self._completion("preview")
        self.controller.last_snapshot = self._snapshot(completed)
        self.controller.status_item = None
        original_state = self.controller.clear_agents_state
        presenter = self._presenter()
        built = []

        def build_presenter(presentation, *, on_action, on_close):
            built.append((presentation, on_action, on_close))
            return presenter

        anchor = SimpleNamespace(bounds=lambda: ((0.0, 0.0), (20.0, 20.0)))
        with patch(
            "sidepulse.status_bar_legacy.ClearAgentsPopoverPresenter",
            side_effect=build_presenter,
        ):
            self.controller.clearAgents_(anchor)

        self.assertIs(self.controller.clear_agents_state, original_state)
        self.assertIs(self.controller._clear_agents_presenter, presenter)
        self.assertEqual(built[0][0].state, ClearAgentsPopoverState.PREVIEW)
        self.assertEqual(built[0][0].clearable_count, 1)
        self.assertEqual(
            built[0][1],
            self.controller._handle_clear_agents_popover_action,
        )
        presenter.show.assert_called_once_with(anchor)

    def test_stale_confirmation_refreshes_preview_without_saving(self) -> None:
        initial_time = datetime.now(timezone.utc) - timedelta(seconds=2)
        completed = self._completion("stale-preview", updated_at=initial_time)
        presenter = self._stage_preview(completed)
        self.controller.last_snapshot = self._snapshot(
            replace(completed, updated_at=initial_time + timedelta(seconds=1))
        )

        with patch.object(self.controller._persistence_writer, "submit") as submit:
            self.controller._confirm_clear_agents_preview()

        submit.assert_not_called()
        self.assertEqual(self.controller.clear_agents_state, ClearAgentsState())
        self.assertFalse(self.controller._clear_agents_operation_pending)
        self.assertEqual(
            presenter.refresh.call_args.args[0].state,
            ClearAgentsPopoverState.STALE,
        )

    def test_stale_confirmation_that_empties_uses_truthful_empty_state(self) -> None:
        presenter = self._stage_preview(self._completion("stale-empty"))
        self.controller.last_snapshot = self._snapshot()

        with patch.object(self.controller._persistence_writer, "submit") as submit:
            self.controller._confirm_clear_agents_preview()

        submit.assert_not_called()
        presentation = presenter.refresh.call_args.args[0]
        self.assertEqual(presentation.state, ClearAgentsPopoverState.STALE)
        self.assertEqual(presentation.clearable_count, 0)
        self.assertEqual(presentation.agent_labels, ())

    def test_same_agent_from_distinct_sources_produces_two_exact_targets(self) -> None:
        updated_at = datetime.now(timezone.utc)
        first = self._completion(
            "shared",
            source_instance="local.one",
            updated_at=updated_at,
        )
        second = self._completion(
            "shared",
            source_instance="local.two",
            updated_at=updated_at - timedelta(seconds=1),
        )

        preview = self.status_bar.clear_agents_preview(
            self._snapshot(first, second),
            self.controller,
        )

        self.assertEqual(preview.clearable_count, 2)
        self.assertEqual(
            {key.source_key.source_instance_id for key in preview.clearable_keys},
            {"local.one", "local.two"},
        )

    def test_queue_refusal_retains_state_and_shows_failure(self) -> None:
        presenter = self._stage_preview(self._completion("queue-refused"))
        original_state = self.controller.clear_agents_state

        with patch.object(
            self.controller._persistence_writer,
            "submit",
            return_value=PersistenceDisposition.REFUSED_FULL,
        ):
            self.controller._confirm_clear_agents_preview()

        self.assertIs(self.controller.clear_agents_state, original_state)
        self.assertFalse(self.controller._clear_agents_operation_pending)
        self.assertEqual(
            presenter.refresh.call_args.args[0].state,
            ClearAgentsPopoverState.FAILURE,
        )

    def test_save_failure_retains_state(self) -> None:
        presenter = self._stage_preview(self._completion("save-failed"))
        original_state = self.controller.clear_agents_state
        captured = {}

        def submit(_key, _operation, *, receipt_handler, **_kwargs):
            captured["receipt_handler"] = receipt_handler
            return PersistenceDisposition.STARTED

        self.controller.performSelectorOnMainThread_withObject_waitUntilDone_ = (
            lambda _selector, payload, _wait: self.controller.applyClearAgentsPersistenceResult_(
                payload
            )
        )
        with patch.object(self.controller._persistence_writer, "submit", side_effect=submit):
            self.controller._confirm_clear_agents_preview()

        captured["receipt_handler"](
            PersistenceReceipt(
                1,
                "clear-agents-state",
                PersistenceOutcome.FAILED,
                error_code="operation_failed",
            )
        )

        self.assertIs(self.controller.clear_agents_state, original_state)
        self.assertFalse(self.controller._clear_agents_operation_pending)
        self.assertEqual(
            presenter.refresh.call_args.args[0].state,
            ClearAgentsPopoverState.FAILURE,
        )

    def test_success_persists_before_adopting_exact_receipt(self) -> None:
        completed = self._completion("persisted")
        presenter = self._stage_preview(completed)
        self.controller.mailbox_retained_order = {completed.agent_id: 7}
        self.controller.mailbox_seen_completion_ids = {completed.agent_id}
        applied = threading.Event()

        def apply_result(_selector, payload, _wait):
            self.controller.applyClearAgentsPersistenceResult_(payload)
            applied.set()

        self.controller.performSelectorOnMainThread_withObject_waitUntilDone_ = apply_result
        with patch.object(self.controller, "refresh_") as refresh:
            self.controller._confirm_clear_agents_preview()
            self.assertTrue(applied.wait(2.0))

        self.assertEqual(self.controller.clear_agents_state.generation, 1)
        self.assertEqual(len(self.controller.clear_agents_state.receipts), 1)
        self.assertEqual(
            load_clear_agents_state(self.controller.clear_agents_path).state,
            self.controller.clear_agents_state,
        )
        self.assertEqual(self.controller.mailbox_retained_order, {completed.agent_id: 7})
        self.assertEqual(self.controller.mailbox_seen_completion_ids, {completed.agent_id})
        self.assertEqual(
            presenter.refresh.call_args.args[0].state,
            ClearAgentsPopoverState.RECEIPT,
        )
        refresh.assert_called_once_with(None)

    def test_undo_removes_only_latest_exact_receipts(self) -> None:
        previous = self._completion("previously-cleared")
        previous_state = self._state_for(previous)
        self.controller.clear_agents_state = previous_state
        presenter = self._stage_preview(self._completion("undo"))
        operation_kinds = []
        applied = threading.Event()

        def apply_result(_selector, payload, _wait):
            self.controller.applyClearAgentsPersistenceResult_(payload)
            operation_kinds.append(payload[1])
            applied.set()

        self.controller.performSelectorOnMainThread_withObject_waitUntilDone_ = apply_result
        with patch.object(self.controller, "refresh_") as refresh:
            self.controller._confirm_clear_agents_preview()
            self.assertTrue(applied.wait(2.0))
            applied.clear()
            self.controller._start_clear_agents_undo()
            self.assertTrue(applied.wait(2.0))

        self.assertEqual(operation_kinds, ["commit", "undo"])
        self.assertEqual(self.controller.clear_agents_state.generation, 3)
        self.assertEqual(
            self.controller.clear_agents_state.acknowledged_keys,
            previous_state.acknowledged_keys,
        )
        self.assertTrue(self.controller.clear_agents_state.latest_batch.undone)
        self.assertEqual(
            load_clear_agents_state(self.controller.clear_agents_path).state,
            self.controller.clear_agents_state,
        )
        self.assertEqual(
            presenter.refresh.call_args.args[0].state,
            ClearAgentsPopoverState.UNDONE,
        )
        self.assertEqual(refresh.call_args_list, [call(None), call(None)])

    def test_operation_generation_ignores_late_save_callback(self) -> None:
        self._stage_preview(self._completion("late-callback"))
        original_state = self.controller.clear_agents_state
        captured = {}

        def submit(_key, _operation, *, receipt_handler, **_kwargs):
            captured["receipt_handler"] = receipt_handler
            return PersistenceDisposition.STARTED

        published = []
        self.controller.performSelectorOnMainThread_withObject_waitUntilDone_ = (
            lambda _selector, payload, _wait: published.append(payload)
        )
        with patch.object(self.controller._persistence_writer, "submit", side_effect=submit):
            self.controller._confirm_clear_agents_preview()

        captured["receipt_handler"](
            PersistenceReceipt(
                1,
                "clear-agents-state",
                PersistenceOutcome.SUCCEEDED,
            )
        )
        stale_payload = published.pop()
        self.controller._clear_agents_operation_generation += 1
        self.controller._clear_agents_operation_pending = True

        with patch.object(self.controller, "refresh_") as refresh:
            self.controller.applyClearAgentsPersistenceResult_(stale_payload)

        self.assertIs(self.controller.clear_agents_state, original_state)
        self.assertTrue(self.controller._clear_agents_operation_pending)
        refresh.assert_not_called()

    def test_popover_close_never_runs_calibration_cleanup(self) -> None:
        self._stage_preview(self._completion("dedicated-close"))

        with patch.object(self.controller, "popoverDidClose_") as calibration_close:
            self.controller._clear_agents_popover_closed()

        calibration_close.assert_not_called()
        self.assertIsNone(self.controller._clear_agents_presenter)
        self.assertIsNone(self.controller._clear_agents_preview)
        self.assertIsNone(self.controller._clear_agents_commit_plan)


if __name__ == "__main__":
    unittest.main()
