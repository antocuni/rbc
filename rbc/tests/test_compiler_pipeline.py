import pytest
import contextlib
import warnings
from rbc.remotejit import RemoteJIT
from rbc.omnisci_backend.omnisci_buffer import free_buffer
from rbc.omnisci_backend.omnisci_pipeline import MissingFreeWarning
from rbc.stdlib import array_api as xp


@pytest.fixture
def rjit():
    return RemoteJIT(local=True)


@contextlib.contextmanager
def no_warnings(warning_cls):
    with pytest.warns(None) as wlist:
        yield
    wlist = [w.message for w in wlist if isinstance(w.message, warning_cls)]
    if wlist:
        raise AssertionError(
            "Warnings were raised: " + ", ".join([str(w) for w in wlist])
        )


def test_no_warnings_decorator():
    with no_warnings(MissingFreeWarning):
        pass

    with no_warnings(MissingFreeWarning):
        warnings.warn('hello')

    with pytest.raises(AssertionError, match='Warnings were raised'):
        with no_warnings(MissingFreeWarning):
            c = test_no_warnings_decorator.__code__
            warnings.warn(MissingFreeWarning(c.co_name, c.co_filename, c.co_firstlineno))


class TestDetectMissingFree:

    def test_missing_free(self, rjit):
        # basic case: we are creating an array but we don't call .free()
        @rjit('int32(int32)')
        def fn(size):
            a = xp.Array(size, xp.float64)  # noqa: F841
            return size

        with pytest.warns(MissingFreeWarning):
            res = fn(10)
            assert res == 10

    def test_disable_leak_warnings(self, rjit):
        @rjit('int32(int32)', disable_leak_warnings=True)
        def fn(size):
            a = xp.Array(size, xp.float64)  # noqa: F841
            return size

        with no_warnings(MissingFreeWarning):
            res = fn(10)
            assert res == 10

    def test_detect_call_to_free_buffer(self, rjit):
        @rjit('int32(int32)')
        def fn(size):
            a = xp.Array(size, xp.float64)
            free_buffer(a)
            return size

        with no_warnings(MissingFreeWarning):
            res = fn(10)
            assert res == 10

    def test_detect_call_to_free_buffer_non_global(self, rjit):
        # note, this is not a typo: we are aware that free_buffer is already
        # imported globally, but here we want to check that we detect the call
        # to free_buffer even when it's imported locally
        from rbc.omnisci_backend.omnisci_buffer import free_buffer

        @rjit('int32(int32)')
        def fn(size):
            a = xp.Array(size, xp.float64)
            free_buffer(a)
            return size

        with no_warnings(MissingFreeWarning):
            res = fn(10)
            assert res == 10

    def test_detect_call_to_method_free(self, rjit):
        @rjit('int32(int32)')
        def fn(size):
            a = xp.Array(size, xp.float64)
            a.free()
            return size

        with no_warnings(MissingFreeWarning):
            res = fn(10)
            assert res == 10
