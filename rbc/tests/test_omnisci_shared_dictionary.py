import pytest
from rbc.tests import omnisci_fixture


@pytest.fixture(scope="module")
def omnisci():

    for o in omnisci_fixture(globals(), minimal_version=(5, 5)):
        define(o)
        yield o


def define(omnisci):
    @omnisci("int32(Column<int32>, RowMultiplier, OutputColumn<int32>)")
    def test_shared_dict(x, m, y):
        sz = len(x)
        for i in range(sz):
            y[i] = 1000 * x.get_dict_id() + x[i]
        return m * sz

    @omnisci("int32(Column<int32>, RowMultiplier, OutputColumn<bool>)")
    def test_shared_dict_is_dict_encoded(x, m, y):
        sz = len(x)
        for i in range(sz):
            y[i] = x.is_dict_encoded()
        return m * sz


@pytest.fixture(scope="function")
def create_columns(omnisci):

    for size in (8, 16, 32):
        table_name = f"dict_{size}"
        base = f"base_{size}"
        derived = f"derived_{size}"

        omnisci.sql_execute(
            f"""
            CREATE TABLE IF NOT EXISTS {table_name} (
                {base} TEXT ENCODING DICT({size}),
                {derived} TEXT,
                SHARED DICTIONARY ({derived}) REFERENCES {table_name}({base})
            );
        """
        )

        data = {
            base: ["hello", "foo", "foofoo", "world", "bar", "foo", "foofoo"],
            derived: ["world", "bar", "hello", "foo", "baz", "hello", "foo"],
        }

        omnisci.load_table_columnar(table_name, **data)

    yield omnisci

    for size in (8, 16, 32):
        table_name = f"dict_{size}"
        omnisci.sql_execute(f"DROP TABLE IF EXISTS {table_name}")


@pytest.mark.usefixtures("create_columns")
@pytest.mark.parametrize("size", (8, 16, 32))
def test_table_function_shared_dict(omnisci, size):

    fn = "test_shared_dict"
    table = f"dict_{size}"
    base = f"base_{size}"

    _, expected = omnisci.sql_execute(
        f"SELECT key_for_string(base_{size}) FROM {table};"
    )

    query = f"SELECT * FROM table({fn}(cursor(SELECT {base} FROM {table}), 1));"
    _, result = omnisci.sql_execute(query)

    assert list(expected) == [(r[0] % 1000,) for r in list(result)]


@pytest.mark.usefixtures("create_columns")
@pytest.mark.parametrize("size", (32,))
def test_table_function_is_shared_dict(omnisci, size):

    fn = "test_shared_dict_is_dict_encoded"
    table = f"dict_{size}"
    base = f"base_{size}"

    query = f"SELECT * FROM table({fn}(cursor(SELECT {base} FROM {table}), 1));"
    _, result = omnisci.sql_execute(query)

    assert all(list(map(lambda x: x[0], result)))


@pytest.mark.usefixtures("create_columns")
def test_table_function_is_not_shared_dict(omnisci):

    fn = "test_shared_dict_is_dict_encoded"
    table = f"{omnisci.table_name}"
    col = "i4"

    query = f"SELECT * FROM table({fn}(cursor(SELECT {col} FROM {table}), 1));"
    _, result = omnisci.sql_execute(query)

    assert all(list(map(lambda x: x[0], result))) == False  # noqa: E712
