"""Test-lane classification follows test and fixture calls to the guarded PostgreSQL boundary."""
import inspect
from functools import lru_cache

CORPUS_MODULES = {'test_impact_exante_swing.py', 'test_impact_reconstruction.py',
                  'test_kill_order_stage_c0.py', 'test_kill_order_leverage_gates.py'}


@lru_cache(maxsize=None)
def calls_postgres(function):
    return _walk(function, set())


def _walk(function, seen):
    function = inspect.unwrap(function)
    code = getattr(function, '__code__', None)
    if code is None: return False
    if code in seen: return False
    seen.add(code)
    if {'postgres_url_or_skip', 'postgres_session_or_skip'} & set(code.co_names): return True
    namespace = getattr(function, '__globals__', {})
    for name in code.co_names:
        value = namespace.get(name)
        if inspect.isfunction(value) and '/tests/' in inspect.getfile(value).replace('\\', '/'):
            if _walk(value, seen): return True
    return False


def item_uses_postgres(item):
    if item.path.name == 'test_postgres_test_guard.py': return False
    if calls_postgres(item.obj): return True
    for definitions in item._fixtureinfo.name2fixturedefs.values():
        if definitions and calls_postgres(definitions[-1].func): return True
    return False
