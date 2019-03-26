import sys
import textwrap

import pytest

import pytest_twisted


ASYNC_AWAIT = sys.version_info >= (3, 5)


def assert_outcomes(run_result, outcomes):
    formatted_output = format_run_result_output_for_assert(run_result)

    try:
        result_outcomes = run_result.parseoutcomes()
    except ValueError:
        assert False, formatted_output

    for name, value in outcomes.items():
        assert result_outcomes.get(name) == value, formatted_output


def format_run_result_output_for_assert(run_result):
    tpl = """
    ---- stdout
    {}
    ---- stderr
    {}
    ----
    """
    return textwrap.dedent(tpl).format(
        run_result.stdout.str(), run_result.stderr.str()
    )


def skip_if_reactor_not(request, expected_reactor):
    actual_reactor = request.config.getoption("reactor", "default")
    if actual_reactor != expected_reactor:
        pytest.skip("reactor is {} not {}".format(actual_reactor, expected_reactor))


def skip_if_no_async_await():
    return pytest.mark.skipif(
        not ASYNC_AWAIT,
        reason="async/await syntax not supported on Python <3.5",
    )


@pytest.fixture
def cmd_opts(request):
    reactor = request.config.getoption("reactor", "default")
    return ("--reactor={}".format(reactor),)


@pytest.fixture
def cmd_opts_marked_only(cmd_opts):
    return cmd_opts + ("--twisted-marked-only",)


def test_inline_callbacks_in_pytest():
    assert hasattr(pytest, 'inlineCallbacks')


@pytest.mark.parametrize(
    'decorator, should_warn',
    (
        ('pytest.inlineCallbacks', True),
        ('pytest_twisted.inlineCallbacks', False),
    ),
)
def test_inline_callbacks_in_pytest_deprecation(
        testdir,
        cmd_opts,
        decorator,
        should_warn,
):
    import_path, _, _ = decorator.rpartition('.')
    test_file = """
    import {import_path}

    def test_deprecation():
        @{decorator}
        def f():
            yield 42
    """.format(import_path=import_path, decorator=decorator)
    testdir.makepyfile(test_file)
    rr = testdir.run(sys.executable, "-m", "pytest", "-v", *cmd_opts)

    expected_outcomes = {"passed": 1}
    if should_warn:
        expected_outcomes["warnings"] = 1

    assert_outcomes(rr, expected_outcomes)


def test_blockon_in_pytest():
    assert hasattr(pytest, 'blockon')


@pytest.mark.parametrize(
    'function, should_warn',
    (
        ('pytest.blockon', True),
        ('pytest_twisted.blockon', False),
    ),
)
def test_blockon_in_pytest_deprecation(
        testdir,
        cmd_opts,
        function,
        should_warn,
):
    import_path, _, _ = function.rpartition('.')
    test_file = """
    import warnings

    from twisted.internet import reactor, defer
    import pytest
    import {import_path}

    @pytest.fixture
    def foo(request):
        d = defer.Deferred()
        d.callback(None)
        {function}(d)

    def test_succeed(foo):
        pass
    """.format(import_path=import_path, function=function)
    testdir.makepyfile(test_file)
    rr = testdir.run(sys.executable, "-m", "pytest", "-v", *cmd_opts)

    expected_outcomes = {"passed": 1}
    if should_warn:
        expected_outcomes["warnings"] = 1

    assert_outcomes(rr, expected_outcomes)


def test_fail_later(testdir, cmd_opts):
    test_file = """
    from twisted.internet import reactor, defer

    def test_fail():
        def doit():
            try:
                1 / 0
            except:
                d.errback()

        d = defer.Deferred()
        reactor.callLater(0.01, doit)
        return d
    """
    testdir.makepyfile(test_file)
    rr = testdir.run(sys.executable, "-m", "pytest", *cmd_opts)
    assert_outcomes(rr, {"failed": 1})


def test_succeed_later(testdir, cmd_opts):
    test_file = """
    from twisted.internet import reactor, defer

    def test_succeed():
        d = defer.Deferred()
        reactor.callLater(0.01, d.callback, 1)
        return d
    """
    testdir.makepyfile(test_file)
    rr = testdir.run(sys.executable, "-m", "pytest", *cmd_opts)
    assert_outcomes(rr, {"passed": 1})


def test_non_deferred(testdir, cmd_opts):
    test_file = """
    from twisted.internet import reactor, defer

    def test_succeed():
        return 42
    """
    testdir.makepyfile(test_file)
    rr = testdir.run(sys.executable, "-m", "pytest", *cmd_opts)
    assert_outcomes(rr, {"passed": 1})


def test_exception(testdir, cmd_opts):
    test_file = """
    def test_more_fail():
        raise RuntimeError("foo")
    """
    testdir.makepyfile(test_file)
    rr = testdir.run(sys.executable, "-m", "pytest", *cmd_opts)
    assert_outcomes(rr, {"failed": 1})


def test_inlineCallbacks(testdir, cmd_opts):
    test_file = """
    from twisted.internet import reactor, defer
    import pytest
    import pytest_twisted

    @pytest.fixture(scope="module", params=["fs", "imap", "web"])
    def foo(request):
        return request.param

    @pytest_twisted.inlineCallbacks
    def test_succeed(foo):
        yield defer.succeed(foo)
        if foo == "web":
            raise RuntimeError("baz")
    """
    testdir.makepyfile(test_file)
    rr = testdir.run(sys.executable, "-m", "pytest", "-v", *cmd_opts)
    assert_outcomes(rr, {"passed": 2, "failed": 1})


@skip_if_no_async_await()
def test_async_await(testdir, cmd_opts):
    test_file = """
    from twisted.internet import reactor, defer
    import pytest
    import pytest_twisted

    @pytest.fixture(scope="module", params=["fs", "imap", "web"])
    def foo(request):
        return request.param

    @pytest_twisted.ensureDeferred
    async def test_succeed(foo):
        await defer.succeed(foo)
        if foo == "web":
            raise RuntimeError("baz")
    """
    testdir.makepyfile(test_file)
    rr = testdir.run(sys.executable, "-m", "pytest", "-v", *cmd_opts)
    assert_outcomes(rr, {"passed": 2, "failed": 1})


def test_twisted_greenlet(testdir, cmd_opts):
    test_file = """
    import pytest, greenlet

    MAIN = None

    @pytest.fixture(scope="session", autouse=True)
    def set_MAIN(request, twisted_greenlet):
        global MAIN
        MAIN = twisted_greenlet

    def test_MAIN():
        assert MAIN is not None
        assert MAIN is greenlet.getcurrent()
    """
    testdir.makepyfile(test_file)
    rr = testdir.run(sys.executable, "-m", "pytest", "-v", *cmd_opts)
    assert_outcomes(rr, {"passed": 1})


def test_blockon_in_fixture(testdir, cmd_opts):
    test_file = """
    from twisted.internet import reactor, defer
    import pytest
    import pytest_twisted

    @pytest.fixture(scope="module", params=["fs", "imap", "web"])
    def foo(request):
        d1, d2 = defer.Deferred(), defer.Deferred()
        reactor.callLater(0.01, d1.callback, 1)
        reactor.callLater(0.02, d2.callback, request.param)
        pytest_twisted.blockon(d1)
        return d2

    @pytest_twisted.inlineCallbacks
    def test_succeed(foo):
        x = yield foo
        if x == "web":
            raise RuntimeError("baz")
    """
    testdir.makepyfile(test_file)
    rr = testdir.run(sys.executable, "-m", "pytest", "-v", *cmd_opts)
    assert_outcomes(rr, {"passed": 2, "failed": 1})


@skip_if_no_async_await()
def test_blockon_in_fixture_async(testdir, cmd_opts):
    test_file = """
    from twisted.internet import reactor, defer
    import pytest
    import pytest_twisted

    @pytest.fixture(scope="module", params=["fs", "imap", "web"])
    def foo(request):
        d1, d2 = defer.Deferred(), defer.Deferred()
        reactor.callLater(0.01, d1.callback, 1)
        reactor.callLater(0.02, d2.callback, request.param)
        pytest_twisted.blockon(d1)
        return d2

    @pytest_twisted.ensureDeferred
    async def test_succeed(foo):
        x = await foo
        if x == "web":
            raise RuntimeError("baz")
    """
    testdir.makepyfile(test_file)
    rr = testdir.run(sys.executable, "-m", "pytest", "-v", *cmd_opts)
    assert_outcomes(rr, {"passed": 2, "failed": 1})


def test_blockon_in_hook(testdir, cmd_opts, request):
    skip_if_reactor_not(request, "default")
    conftest_file = """
    import pytest_twisted as pt
    from twisted.internet import reactor, defer

    def pytest_configure(config):
        pt.init_default_reactor()
        d1, d2 = defer.Deferred(), defer.Deferred()
        reactor.callLater(0.01, d1.callback, 1)
        reactor.callLater(0.02, d2.callback, 1)
        pt.blockon(d1)
        pt.blockon(d2)
    """
    testdir.makeconftest(conftest_file)
    test_file = """
    from twisted.internet import reactor, defer

    def test_succeed():
        d = defer.Deferred()
        reactor.callLater(0.01, d.callback, 1)
        return d
    """
    testdir.makepyfile(test_file)
    rr = testdir.run(sys.executable, "-m", "pytest", "-v", *cmd_opts)
    assert_outcomes(rr, {"passed": 1})


def test_wrong_reactor(testdir, cmd_opts, request):
    skip_if_reactor_not(request, "default")
    conftest_file = """
    def pytest_addhooks():
        import twisted.internet.reactor
        twisted.internet.reactor = None
    """
    testdir.makeconftest(conftest_file)
    test_file = """
    def test_succeed():
        pass
    """
    testdir.makepyfile(test_file)
    rr = testdir.run(sys.executable, "-m", "pytest", "-v", *cmd_opts)
    assert "WrongReactorAlreadyInstalledError" in rr.stderr.str()


def test_blockon_in_hook_with_qt5reactor(testdir, cmd_opts, request):
    skip_if_reactor_not(request, "qt5reactor")
    conftest_file = """
    import pytest_twisted as pt
    import pytestqt
    from twisted.internet import defer

    def pytest_configure(config):
        pt.init_qt5_reactor()
        d = defer.Deferred()

        from twisted.internet import reactor

        reactor.callLater(0.01, d.callback, 1)
        pt.blockon(d)
    """
    testdir.makeconftest(conftest_file)
    test_file = """
    from twisted.internet import reactor, defer

    def test_succeed():
        d = defer.Deferred()
        reactor.callLater(0.01, d.callback, 1)
        return d
    """
    testdir.makepyfile(test_file)
    rr = testdir.run(sys.executable, "-m", "pytest", "-v", *cmd_opts)
    assert_outcomes(rr, {"passed": 1})


def test_wrong_reactor_with_qt5reactor(testdir, cmd_opts, request):
    skip_if_reactor_not(request, "qt5reactor")
    conftest_file = """
    def pytest_addhooks():
        import twisted.internet.default
        twisted.internet.default.install()
    """
    testdir.makeconftest(conftest_file)
    test_file = """
    def test_succeed():
        pass
    """
    testdir.makepyfile(test_file)
    rr = testdir.run(sys.executable, "-m", "pytest", "-v", *cmd_opts)
    assert "WrongReactorAlreadyInstalledError" in rr.stderr.str()


def test_pytest_from_reactor_thread(testdir, request):
    skip_if_reactor_not(request, "default")
    test_file = """
    import pytest
    import pytest_twisted
    from twisted.internet import reactor, defer

    @pytest.fixture
    def fix():
        d = defer.Deferred()
        reactor.callLater(0.01, d.callback, 42)
        return pytest_twisted.blockon(d)

    def test_simple(fix):
        assert fix == 42

    @pytest_twisted.inlineCallbacks
    def test_fail():
        d = defer.Deferred()
        reactor.callLater(0.01, d.callback, 1)
        yield d
        assert False
    """
    testdir.makepyfile(test_file)
    runner_file = """
    import pytest

    from twisted.internet import reactor
    from twisted.internet.defer import inlineCallbacks
    from twisted.internet.threads import deferToThread

    codes = []

    @inlineCallbacks
    def main():
        try:
            codes.append((yield deferToThread(pytest.main, ['-k simple'])))
            codes.append((yield deferToThread(pytest.main, ['-k fail'])))
        finally:
            reactor.stop()

    if __name__ == '__main__':
        reactor.callLater(0, main)
        reactor.run()
        codes == [0, 1] or exit(1)
    """
    testdir.makepyfile(runner=runner_file)
    # check test file is ok in standalone mode:
    rr = testdir.run(sys.executable, "-m", "pytest", "-v")
    assert_outcomes(rr, {"passed": 1, "failed": 1})
    # test embedded mode:
    assert testdir.run(sys.executable, "runner.py").ret == 0


def test_blockon_in_hook_with_asyncio(testdir, cmd_opts, request):
    skip_if_reactor_not(request, "asyncio")
    conftest_file = """
    import pytest_twisted as pt
    from twisted.internet import defer

    def pytest_configure(config):
        pt.init_asyncio_reactor()
        d = defer.Deferred()

        from twisted.internet import reactor

        reactor.callLater(0.01, d.callback, 1)
        pt.blockon(d)
    """
    testdir.makeconftest(conftest_file)
    test_file = """
    from twisted.internet import reactor, defer

    def test_succeed():
        d = defer.Deferred()
        reactor.callLater(0.01, d.callback, 1)
        return d
    """
    testdir.makepyfile(test_file)
    rr = testdir.run(sys.executable, "-m", "pytest", "-v", *cmd_opts)
    assert_outcomes(rr, {"passed": 1})


def test_wrong_reactor_with_asyncio(testdir, cmd_opts, request):
    skip_if_reactor_not(request, "asyncio")
    conftest_file = """
    def pytest_addhooks():
        import twisted.internet.default
        twisted.internet.default.install()
    """
    testdir.makeconftest(conftest_file)
    test_file = """
    def test_succeed():
        pass
    """
    testdir.makepyfile(test_file)
    rr = testdir.run(sys.executable, "-m", "pytest", "-v", *cmd_opts)
    assert "WrongReactorAlreadyInstalledError" in rr.stderr.str()


def test_twisted_for_all_tests_by_default(testdir, cmd_opts):
    test_file = """
    import pytest
    from twisted.internet import reactor, defer

    def test_reactor_running():
        assert reactor.running
    
    @pytest.mark.twisted
    def test_reactor_running_with_mark():
        assert reactor.running
    """
    testdir.makepyfile(test_file)
    rr = testdir.run(sys.executable, "-m", "pytest", *cmd_opts)
    assert_outcomes(rr, {"passed": 2})


def test_twisted_for_marked_only_tests_with_cmdopt(testdir, cmd_opts_marked_only):
    test_file = """
    import pytest
    from twisted.internet import reactor, defer

    def test_reactor_running():
        assert not reactor.running
    
    @pytest.mark.twisted
    def test_reactor_running_with_mark():
        assert reactor.running
    """
    testdir.makepyfile(test_file)
    rr = testdir.run(sys.executable, "-m", "pytest", *cmd_opts_marked_only)
    assert_outcomes(rr, {"passed": 2})
