"""Security contracts for Claude's read-only usage transport."""


def test_the_redirect_guard_refuses_a_cross_origin_hop():
    """A 30x to another host would hand the Authorization header to
    whoever answered it. Ported from CodexBar's ProviderHTTPClient."""
    from types import SimpleNamespace

    from sidepulse.claude_quota import _redirect_guard_class

    guard = _redirect_guard_class().alloc().initWithOrigin_(
        ("https", "api.anthropic.com", None)
    )
    answers = []

    def _request(scheme, host):
        return SimpleNamespace(
            URL=lambda: SimpleNamespace(
                scheme=lambda: scheme, host=lambda: host, port=lambda: None
            )
        )

    same = _request("https", "api.anthropic.com")
    guard.URLSession_task_willPerformHTTPRedirection_newRequest_completionHandler_(
        None, None, None, same, answers.append
    )
    assert answers[-1] is same, "a same-origin redirect is allowed"

    guard.URLSession_task_willPerformHTTPRedirection_newRequest_completionHandler_(
        None, None, None, _request("https", "evil.example.com"), answers.append
    )
    assert answers[-1] is None, "a cross-origin redirect is refused"

    guard.URLSession_task_willPerformHTTPRedirection_newRequest_completionHandler_(
        None, None, None, _request("http", "api.anthropic.com"), answers.append
    )
    assert answers[-1] is None, "a downgrade to http is refused"
