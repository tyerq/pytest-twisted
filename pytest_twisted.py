import functools
import inspect
import sys
import warnings

import decorator
import greenlet
import pytest
from twisted.internet import defer, error
from twisted.internet.threads import blockingCallFromThread
from twisted.python import failure


class WrongReactorAlreadyInstalledError(Exception):
    pass


class _config:
    reactor_installer = None
    external_reactor = False


class _instances:
    _reactor_original = None

    gr_twisted = None
    reactor = None


def _deprecate(deprecated, recommended):
    def decorator(f):
        @functools.wraps(f)
        def wrapper(*args, **kwargs):
            warnings.warn(
                '{deprecated} has been deprecated, use {recommended}'.format(
                    deprecated=deprecated,
                    recommended=recommended,
                ),
                DeprecationWarning,
                stacklevel=2,
            )
            return f(*args, **kwargs)

        return wrapper

    return decorator


def blockon(d):
    if _config.external_reactor:
        return block_from_thread(d)

    return blockon_default(d)


def blockon_default(d):
    current = greenlet.getcurrent()
    assert (
        current is not _instances.gr_twisted
    ), "blockon cannot be called from the twisted greenlet"
    result = []

    def cb(r):
        result.append(r)
        if greenlet.getcurrent() is not current:
            current.switch(result)

    d.addCallbacks(cb, cb)
    if not result:
        _result = _instances.gr_twisted.switch()
        assert _result is result, "illegal switch in blockon"

    if isinstance(result[0], failure.Failure):
        result[0].raiseException()

    return result[0]


def block_from_thread(d):
    return blockingCallFromThread(_instances.reactor, lambda x: x, d)


@decorator.decorator
def inlineCallbacks(fun, *args, **kw):
    return defer.inlineCallbacks(fun)(*args, **kw)


@decorator.decorator
def ensureDeferred(fun, *args, **kw):
    return defer.ensureDeferred(fun(*args, **kw))


def init_twisted_greenlet():
    if _instances.reactor is None or _instances.gr_twisted:
        return

    if not _instances.reactor.running:
        _instances.gr_twisted = greenlet.greenlet(_instances.reactor.run)
        # give me better tracebacks:
        failure.Failure.cleanFailure = lambda self: None
    else:
        _config.external_reactor = True


def stop_twisted_greenlet():
    if _instances.gr_twisted:
        _instances.reactor.stop()
        _instances.gr_twisted.switch()

    _instances.gr_twisted = None
    _instances.reactor = None

    _set_system_reactor(_instances._reactor_original)


def _pytest_pyfunc_call(pyfuncitem):
    testfunction = pyfuncitem.obj
    if pyfuncitem._isyieldedfunction():
        return testfunction(*pyfuncitem._args)
    else:
        funcargs = pyfuncitem.funcargs
        if hasattr(pyfuncitem, "_fixtureinfo"):
            testargs = {}
            for arg in pyfuncitem._fixtureinfo.argnames:
                testargs[arg] = funcargs[arg]
        else:
            testargs = funcargs
        return testfunction(**testargs)


def pytest_collection_modifyitems(config, items):
    if not config.getoption("--twisted-marked-only"):
        twisted_marker = pytest.mark.twisted()
        for item in items:
            item.add_marker(twisted_marker)


def pytest_runtest_setup(item):
    if "twisted" in item.keywords and "twisted_greenlet" not in item.fixturenames:
        # inject a twisted greenlet fixture for all twisted tests
        item.fixturenames.append("twisted_greenlet")


def pytest_pyfunc_call(pyfuncitem):
    if "twisted" not in pyfuncitem.keywords:
        return

    del sys.modules['twisted.internet.reactor']

    _config.reactor_installer()
    import twisted.internet.reactor
    _instances.reactor = twisted.internet.reactor
    _instances.reactor._is_pytest_twisted = True
    _set_system_reactor(_instances.reactor)
    init_twisted_greenlet()

    if _instances.gr_twisted is not None:
        if _instances.gr_twisted.dead:
            raise RuntimeError("twisted reactor has stopped")

        def in_reactor(d, f, *args):
            return defer.maybeDeferred(f, *args).chainDeferred(d)

        d = defer.Deferred()
        _instances.reactor.callLater(
            0.0, in_reactor, d, _pytest_pyfunc_call, pyfuncitem
        )
        blockon_default(d)
    else:
        if not _instances.reactor.running:
            raise RuntimeError("twisted reactor is not running")
        blockingCallFromThread(
            _instances.reactor, _pytest_pyfunc_call, pyfuncitem
        )
    return True


@pytest.fixture
def twisted_greenlet(request):
    request.addfinalizer(stop_twisted_greenlet)
    return _instances.gr_twisted


def init_default_reactor():
    import twisted.internet.default

    module = inspect.getmodule(twisted.internet.default.install)

    module_name = module.__name__.split(".")[-1]
    reactor_type_name, = (x for x in dir(module) if x.lower() == module_name)
    reactor_type = getattr(module, reactor_type_name)

    _install_reactor(
        reactor_installer=twisted.internet.default.install,
        reactor_type=reactor_type,
    )


def init_qt5_reactor():
    import qt5reactor

    _install_reactor(
        reactor_installer=qt5reactor.install, reactor_type=qt5reactor.QtReactor
    )


def init_asyncio_reactor():
    from twisted.internet import asyncioreactor

    _install_reactor(
        reactor_installer=asyncioreactor.install,
        reactor_type=asyncioreactor.AsyncioSelectorReactor,
    )


reactor_installers = {
    "default": init_default_reactor,
    "qt5reactor": init_qt5_reactor,
    "asyncio": init_asyncio_reactor,
}


def _install_reactor(reactor_installer, reactor_type):
    try:
        reactor_installer()
    except error.ReactorAlreadyInstalledError:
        import twisted.internet.reactor

        if not isinstance(twisted.internet.reactor, reactor_type):
            raise WrongReactorAlreadyInstalledError(
                "expected {} but found {}".format(
                    reactor_type, type(twisted.internet.reactor)
                )
            )


def pytest_addoption(parser):
    group = parser.getgroup("twisted")
    group.addoption(
        "--reactor",
        default="default",
        choices=tuple(reactor_installers.keys()),
    )
    group.addoption(
        "--twisted-marked-only",
        action="store_true",
        default=False,
        help="start twisted reactor only for tests marked with `pytest.mark.twisted`",
    )


def pytest_configure(config):
    pytest.inlineCallbacks = _deprecate(
        deprecated='pytest.inlineCallbacks',
        recommended='pytest_twisted.inlineCallbacks',
    )(inlineCallbacks)
    pytest.blockon = _deprecate(
        deprecated='pytest.blockon',
        recommended='pytest_twisted.blockon',
    )(blockon)

    _config.reactor_installer = reactor_installers[config.getoption("reactor")]
    _config.reactor_installer()
    _freeze_reactor()


def _freeze_reactor():

    def __dont__(*args, **kwargs):
        raise ValueError("Don't touch the reactor outside `@pytest.mark.twisted`, "
                         "please!")

    import twisted.internet

    twisted.internet.reactor.run = __dont__
    twisted.internet.reactor.stop = __dont__
    twisted.internet.reactor.crash = __dont__

    _instances._reactor_original = twisted.internet.reactor


def _set_system_reactor(reactor):
    import twisted.internet
    twisted.internet.reactor = reactor
    sys.modules['twisted.internet.reactor'] = twisted.internet.reactor
