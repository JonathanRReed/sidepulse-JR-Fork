from __future__ import annotations

import json
import os
import stat
import tempfile
import threading
from pathlib import Path

import pytest

from sidepulse import hook_client
from sidepulse.hook_ingress import (
    HookIngressOutcome,
    HookIngressReceipt,
    HookIngressService,
)
from sidepulse.hook_ingress_protocol import (
    HookIngressDisposition,
    HookIngressRequest,
    submit_hook_ingress,
)


def _request(name: str) -> HookIngressRequest:
    return HookIngressRequest(
        "claude",
        "/tmp/state/claude.jsonl",
        json.dumps({"hook_event_name": "PreToolUse", "session_id": name}),
    )


def test_fifo_preserves_acceptance_order_with_one_worker() -> None:
    first_started = threading.Event()
    release_first = threading.Event()
    completed: list[str] = []
    worker_ids: set[int] = set()

    def process(request: HookIngressRequest) -> str:
        name = json.loads(request.payload_text)["session_id"]
        worker_ids.add(threading.get_ident())
        if name == "first":
            first_started.set()
            assert release_first.wait(1.0)
        completed.append(name)
        return name

    service = HookIngressService(process=process, maximum_accepted=4)
    assert service.submit(_request("first")) is HookIngressDisposition.ACCEPTED
    assert first_started.wait(1.0)
    assert service.submit(_request("second")) is HookIngressDisposition.ACCEPTED
    assert service.submit(_request("third")) is HookIngressDisposition.ACCEPTED

    release_first.set()
    assert service.wait_idle(timeout_seconds=1.0)

    assert completed == ["first", "second", "third"]
    assert len(worker_ids) == 1
    assert service.close(timeout_seconds=1.0)


def test_bound_counts_running_plus_pending_and_records_full_refusal() -> None:
    started = threading.Event()
    release = threading.Event()
    receipts: list[HookIngressReceipt] = []

    def process(_request_value: HookIngressRequest) -> None:
        started.set()
        assert release.wait(1.0)

    service = HookIngressService(
        process=process,
        maximum_accepted=2,
        receipt_handler=receipts.append,
        rejection_recorder=receipts.append,
    )
    assert service.submit(_request("first")) is HookIngressDisposition.ACCEPTED
    assert started.wait(1.0)
    assert service.submit(_request("second")) is HookIngressDisposition.ACCEPTED
    assert service.submit(_request("third")) is HookIngressDisposition.REFUSED_FULL

    snapshot = service.snapshot()
    assert snapshot.running
    assert snapshot.pending_count == 1
    assert snapshot.accepted_outstanding == 2
    assert snapshot.refused_full == 1
    refusal = next(receipt for receipt in receipts if receipt.outcome is HookIngressOutcome.REFUSED_FULL)
    assert "third" not in repr(refusal)

    release.set()
    assert service.close(timeout_seconds=1.0)


def test_processing_failure_has_content_free_receipt_and_does_not_stop_fifo() -> None:
    completed: list[str] = []
    receipts: list[HookIngressReceipt] = []

    def process(request: HookIngressRequest) -> None:
        name = json.loads(request.payload_text)["session_id"]
        if name == "fail-private-session":
            raise RuntimeError("private failure detail")
        completed.append(name)

    service = HookIngressService(
        process=process,
        receipt_handler=receipts.append,
        rejection_recorder=receipts.append,
    )
    service.submit(_request("fail-private-session"))
    service.submit(_request("after"))

    assert service.wait_idle(timeout_seconds=1.0)

    assert completed == ["after"]
    failed = next(receipt for receipt in receipts if receipt.outcome is HookIngressOutcome.FAILED)
    assert failed.error_code == "processing_failed"
    assert "private" not in repr(failed)
    assert service.snapshot().failed == 1
    assert service.close(timeout_seconds=1.0)


def test_close_drains_every_accepted_request_and_then_refuses_new_work() -> None:
    completed: list[str] = []
    service = HookIngressService(
        process=lambda request: completed.append(json.loads(request.payload_text)["session_id"]),
    )
    for name in ("one", "two", "three"):
        assert service.submit(_request(name)) is HookIngressDisposition.ACCEPTED

    assert service.close(timeout_seconds=1.0)

    assert completed == ["one", "two", "three"]
    assert service.submit(_request("late")) is HookIngressDisposition.REFUSED_CLOSED
    snapshot = service.snapshot()
    assert not snapshot.accepting
    assert not snapshot.running
    assert snapshot.pending_count == 0
    assert not snapshot.thread_alive


def test_close_timeout_records_running_and_pending_as_not_drained() -> None:
    started = threading.Event()
    release = threading.Event()
    receipts: list[HookIngressReceipt] = []

    def process(_request_value: HookIngressRequest) -> None:
        started.set()
        release.wait(2.0)

    service = HookIngressService(
        process=process,
        maximum_accepted=3,
        receipt_handler=receipts.append,
        rejection_recorder=receipts.append,
    )
    service.submit(_request("running-private"))
    assert started.wait(1.0)
    service.submit(_request("pending-private"))

    assert not service.close(timeout_seconds=0.01)

    timed_out = [
        receipt
        for receipt in receipts
        if receipt.outcome is HookIngressOutcome.REJECTED_SHUTDOWN_TIMEOUT
    ]
    assert [receipt.sequence for receipt in timed_out] == [1, 2]
    assert "private" not in repr(timed_out)
    assert service.snapshot().shutdown_timeout == 2

    release.set()
    assert service.wait_stopped(timeout_seconds=1.0)


def test_rejection_recorder_failure_never_breaks_admission_contract() -> None:
    started = threading.Event()
    release = threading.Event()
    service = HookIngressService(
        process=lambda _request_value: (started.set(), release.wait(1.0)),
        maximum_accepted=1,
        rejection_recorder=lambda _receipt: (_ for _ in ()).throw(OSError("private")),
    )
    assert service.submit(_request("first")) is HookIngressDisposition.ACCEPTED
    assert started.wait(1.0)

    assert service.submit(_request("second")) is HookIngressDisposition.REFUSED_FULL

    release.set()
    assert service.close(timeout_seconds=1.0)


def test_socket_admits_on_private_same_uid_path_and_processes_request() -> None:
    with tempfile.TemporaryDirectory(prefix="jrbar-hi-", dir="/tmp") as directory:
        socket_path = Path(directory) / "hook-ingress.sock"
        completed = threading.Event()
        seen: list[HookIngressRequest] = []
        service = HookIngressService(
            process=lambda request: (seen.append(request), completed.set()),
            socket_path=socket_path,
        )
        assert service.start() == socket_path
        try:
            mode = stat.S_IMODE(socket_path.lstat().st_mode)
            assert mode == 0o600
            assert socket_path.lstat().st_uid == os.geteuid()

            assert (
                submit_hook_ingress(
                    _request("socket"),
                    socket_path=socket_path,
                    timeout_seconds=0.5,
                )
                is HookIngressDisposition.ACCEPTED
            )
            assert completed.wait(1.0)
            assert seen == [_request("socket")]
        finally:
            assert service.close(timeout_seconds=1.0)
        assert not socket_path.exists()


def test_lost_ack_after_acceptance_never_runs_synchronous_fallback() -> None:
    ack_started = threading.Event()
    release_ack = threading.Event()

    class SlowAckIngress(HookIngressService):
        @staticmethod
        def _send_response(connection, disposition) -> None:
            ack_started.set()
            assert release_ack.wait(1.0)
            HookIngressService._send_response(connection, disposition)

    with tempfile.TemporaryDirectory(prefix="jrbar-hi-", dir="/tmp") as directory:
        socket_path = Path(directory) / "hook-ingress.sock"
        completed = threading.Event()
        processed: list[HookIngressRequest] = []
        fallback: list[object] = []
        disposition: list[HookIngressDisposition] = []

        def submit_slow_ack(
            request: HookIngressRequest,
        ) -> HookIngressDisposition:
            result = submit_hook_ingress(
                request,
                socket_path=socket_path,
                timeout_seconds=0.02,
            )
            disposition.append(result)
            return result

        service = SlowAckIngress(
            process=lambda request: (processed.append(request), completed.set()),
            socket_path=socket_path,
        )
        service.start()
        try:
            assert (
                hook_client.run_hook_client(
                    "claude",
                    Path("/tmp/state/claude.jsonl"),
                    _request("slow-ack").payload_text,
                    submit=submit_slow_ack,
                    fallback=lambda *_args: fallback.append(object()),
                )
                == 0
            )
            assert disposition == [HookIngressDisposition.SUBMISSION_AMBIGUOUS]
            assert ack_started.wait(1.0)
            release_ack.set()
            assert completed.wait(1.0)
            assert len(processed) == 1
            assert fallback == []
            assert service.snapshot().accepted == 1
        finally:
            assert service.close(timeout_seconds=1.0)


def test_socket_rejects_cross_uid_peer_as_ambiguous_without_processing() -> None:
    with tempfile.TemporaryDirectory(prefix="jrbar-hi-", dir="/tmp") as directory:
        socket_path = Path(directory) / "hook-ingress.sock"
        seen: list[HookIngressRequest] = []
        service = HookIngressService(
            process=seen.append,
            socket_path=socket_path,
            peer_uid_reader=lambda _connection: os.geteuid() + 1,
        )
        service.start()
        try:
            assert (
                submit_hook_ingress(
                    _request("foreign"),
                    socket_path=socket_path,
                    timeout_seconds=0.2,
                )
                is HookIngressDisposition.SUBMISSION_AMBIGUOUS
            )
            assert service.close(timeout_seconds=1.0)
            assert seen == []
        finally:
            service.close(timeout_seconds=1.0)


def test_default_rejection_record_contains_no_payload_or_path(
    tmp_path: Path,
) -> None:
    rejection_path = tmp_path / "rejections.jsonl"
    started = threading.Event()
    release = threading.Event()
    service = HookIngressService(
        process=lambda _request_value: (started.set(), release.wait(1.0)),
        maximum_accepted=1,
        rejection_path=rejection_path,
    )
    service.submit(_request("running"))
    assert started.wait(1.0)
    assert service.submit(_request("private-payload")) is HookIngressDisposition.REFUSED_FULL

    stored = rejection_path.read_text()
    document = json.loads(stored)
    assert document["reason"] == HookIngressOutcome.REFUSED_FULL.value
    assert document["provider"] == "claude"
    assert frozenset(document) == {
        "recorded_at",
        "provider",
        "reason",
        "sequence",
        "version",
    }
    assert "private-payload" not in stored
    assert "/tmp/state" not in stored

    release.set()
    assert service.close(timeout_seconds=1.0)


def test_direct_and_queued_paths_write_the_same_minimized_record(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sidepulse.hook import process_hook_payload

    # This contract compares the durable minimized records. Refresh delivery
    # is covered by the app-owned handler tests below and must not make this
    # byte-equivalence check depend on whichever installed app owns the live
    # event socket while the source suite runs.
    monkeypatch.setattr("sidepulse.hook.send_refresh_hint", lambda *_args, **_kwargs: False)

    payload = json.dumps(
        {
            "hook_event_name": "PermissionRequest",
            "session_id": "same-session",
            "request_id": "same-request",
            "logged_at": "2026-08-29T12:00:00Z",
            "prompt": "private prompt",
            "tool_input": {"command": "private command"},
        }
    )
    direct = tmp_path / "direct.jsonl"
    queued = tmp_path / "queued.jsonl"
    assert process_hook_payload("claude", direct, payload) is not None
    service = HookIngressService()
    assert (
        service.submit(HookIngressRequest("claude", str(queued), payload))
        is HookIngressDisposition.ACCEPTED
    )
    assert service.close(timeout_seconds=1.0)

    assert direct.read_bytes() == queued.read_bytes()
    stored = direct.read_text()
    assert "private prompt" not in stored
    assert "private command" not in stored


def test_close_waits_until_app_owned_refresh_handler_finishes(
    tmp_path: Path,
) -> None:
    from sidepulse.hook import process_hook_payload

    payload = json.dumps(
        {
            "hook_event_name": "PermissionRequest",
            "session_id": "shutdown-tail",
            "request_id": "permission:shutdown-tail",
            "logged_at": "2026-08-29T12:00:00Z",
        }
    )
    log_path = tmp_path / "claude.jsonl"
    refresh_started = threading.Event()
    release_refresh = threading.Event()
    close_started = threading.Event()
    timeline: list[str] = []

    def apply_refresh(_hint: object) -> None:
        timeline.append("refresh_started")
        refresh_started.set()
        assert release_refresh.wait(1.0)
        timeline.append("refresh_finished")

    def process(request: HookIngressRequest) -> object:
        return process_hook_payload(
            request.provider,
            Path(request.log_path),
            request.payload_text,
            refresh_hint_handler=apply_refresh,
        )

    service = HookIngressService(process=process)
    assert (
        service.submit(HookIngressRequest("claude", str(log_path), payload))
        is HookIngressDisposition.ACCEPTED
    )
    assert refresh_started.wait(1.0)

    close_result: list[bool] = []

    def close_service() -> None:
        timeline.append("close_started")
        close_started.set()
        close_result.append(service.close(timeout_seconds=1.0))
        timeline.append("close_finished")

    close_thread = threading.Thread(target=close_service)
    close_thread.start()
    assert close_started.wait(1.0)

    release_refresh.set()
    close_thread.join(timeout=1.0)
    assert not close_thread.is_alive()
    assert close_result == [True]
    assert timeline == [
        "refresh_started",
        "close_started",
        "refresh_finished",
        "close_finished",
    ]
    assert log_path.is_file()


@pytest.mark.parametrize("timeout", [-1, float("nan"), True, None])
def test_waits_reject_invalid_timeouts(timeout: object) -> None:
    service = HookIngressService(process=lambda _request_value: None)
    with pytest.raises(ValueError, match="invalid hook ingress timeout"):
        service.wait_idle(timeout_seconds=timeout)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="invalid hook ingress timeout"):
        service.close(timeout_seconds=timeout)  # type: ignore[arg-type]
