import articles


class DummyCursor:
    def __init__(self, rows):
        self._rows = rows
        self.closed = False
        self.executed = None

    def execute(self, query, params=None):
        self.executed = (query, params)

    def fetchall(self):
        return list(self._rows)

    def close(self):
        self.closed = True


class DummyConnection:
    def __init__(self, rows):
        self._rows = rows
        self.closed = False
        self.last_cursor = None

    def cursor(self, dictionary=True):
        assert dictionary is True
        cursor = DummyCursor(self._rows)
        self.last_cursor = cursor
        return cursor

    def close(self):
        self.closed = True


def test_sanitize_lookup_rows_filters_invalid_entries():
    rows = [
        {"id": 1, "name": " AWS "},
        {"id": "two", "name": "Azure"},
        {"id": 3, "name": ""},
        {"id": None, "name": "Google"},
        {"id": 4, "name": "  GCP  "},
    ]

    result = articles._sanitize_lookup_rows(rows)

    assert result == [
        {"id": 1, "name": "AWS"},
        {"id": 4, "name": "GCP"},
    ]


def test_fetch_providers_list_uses_dictionary_cursor(monkeypatch):
    rows = [{"id": 11, "name": " AWS ", "extra": "ignored"}]
    connections = []

    def fake_connect(**_):
        conn = DummyConnection(rows)
        connections.append(conn)
        return conn

    monkeypatch.setattr(articles.mysql.connector, "connect", fake_connect)

    providers = articles._fetch_providers_list()

    assert providers == [{"id": 11, "name": "AWS"}]
    assert connections, "A database connection should have been created."
    connection = connections[0]
    assert connection.closed is True
    cursor = connection.last_cursor
    assert cursor is not None and cursor.closed is True
    assert cursor.executed == ("SELECT id, name FROM provs ORDER BY name", None)


def test_fetch_certifications_list_filters_empty_names(monkeypatch):
    rows = [
        {"id": 21, "name": " Associate "},
        {"id": 22, "name": ""},
        {"id": "23", "name": "Professional"},
    ]
    connections = []

    def fake_connect(**_):
        conn = DummyConnection(rows)
        connections.append(conn)
        return conn

    monkeypatch.setattr(articles.mysql.connector, "connect", fake_connect)

    certifications = articles._fetch_certifications_list(7)

    assert certifications == [
        {"id": 21, "name": "Associate"},
        {"id": 23, "name": "Professional"},
    ]
    assert connections, "A database connection should have been created."
    connection = connections[0]
    cursor = connection.last_cursor
    assert cursor.executed == (
        "SELECT id, name FROM courses WHERE prov = %s ORDER BY name",
        (7,),
    )
    assert cursor.closed is True
    assert connection.closed is True
