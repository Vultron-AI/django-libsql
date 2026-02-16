from unittest.mock import MagicMock

from django.test import TestCase

from django_libsql.db.backends.sqlite3.operations import DatabaseOperations
from testapp.models import Company


def _make_ops_with_broken_cursor(error_class=ValueError, error_msg="stream not found"):
    """Create a DatabaseOperations instance with a mock connection whose
    underlying cursor raises on execute."""
    mock_raw_cursor = MagicMock()
    mock_raw_cursor.execute.side_effect = error_class(error_msg)

    mock_raw_conn = MagicMock()
    mock_raw_conn.cursor.return_value = mock_raw_cursor

    mock_wrapper = MagicMock()
    mock_wrapper.connection = mock_raw_conn

    ops = DatabaseOperations.__new__(DatabaseOperations)
    ops.connection = mock_wrapper

    return ops, mock_raw_cursor


def _make_ops_with_working_cursor():
    """Create a DatabaseOperations instance backed by a real in-memory SQLite
    connection so QUOTE(?) actually works."""
    import sqlite3

    real_conn = sqlite3.connect(":memory:")

    mock_wrapper = MagicMock()
    mock_wrapper.connection = real_conn
    mock_wrapper.timezone_name = "UTC"

    ops = DatabaseOperations.__new__(DatabaseOperations)
    ops.connection = mock_wrapper

    return ops


class QuoteParamsNormalTest(TestCase):
    """Test that QUOTE(?) works under normal conditions."""

    def test_normal_quoting_returns_quoted_values(self):
        ops = _make_ops_with_working_cursor()
        result = ops._quote_params_for_last_executed_query(("hello", 42))
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0], "'hello'")
        self.assertEqual(result[1], "42")

    def test_normal_quoting_null(self):
        ops = _make_ops_with_working_cursor()
        result = ops._quote_params_for_last_executed_query((None,))
        self.assertEqual(result, ("NULL",))


class QuoteParamsFallbackTest(TestCase):
    """Test that _quote_params_for_last_executed_query gracefully falls back
    when the underlying database cursor raises an exception (e.g., Hrana HTTP
    stream expired on Turso after idle time)."""

    def test_fallback_on_cursor_error(self):
        """When the raw cursor raises, fall back to repr-style quoting."""
        ops, mock_cursor = _make_ops_with_broken_cursor()
        result = ops._quote_params_for_last_executed_query(("hello", 42, None))
        self.assertEqual(result, ("'hello'", "42", "NULL"))
        mock_cursor.close.assert_called()

    def test_fallback_on_hrana_stream_not_found(self):
        """Simulates the exact Hrana error from Turso."""
        ops, _ = _make_ops_with_broken_cursor(
            ValueError,
            'Hrana: `api error: `status=404 Not Found, '
            'body={"error":"stream not found: 638f590d:019c63b3"}``',
        )
        result = ops._quote_params_for_last_executed_query(("test_value",))
        self.assertEqual(result, ("'test_value'",))

    def test_fallback_quotes_strings_with_apostrophes(self):
        """Strings containing single quotes are properly escaped in fallback."""
        ops, _ = _make_ops_with_broken_cursor()
        result = ops._quote_params_for_last_executed_query(("it's a test",))
        self.assertEqual(result, ("'it''s a test'",))

    def test_fallback_handles_various_types(self):
        """Fallback handles int, float, bool, and None correctly."""
        ops, _ = _make_ops_with_broken_cursor()
        result = ops._quote_params_for_last_executed_query(
            (123, 3.14, True, None, "text")
        )
        self.assertEqual(result, ("123", "3.14", "True", "NULL", "'text'"))

    def test_fallback_with_empty_params(self):
        """Empty params should return empty tuple."""
        ops, _ = _make_ops_with_broken_cursor()
        result = ops._quote_params_for_last_executed_query(())
        self.assertEqual(result, ())

    def test_cursor_is_closed_on_fallback(self):
        """Cursor.close() is always called even when falling back."""
        ops, mock_cursor = _make_ops_with_broken_cursor()
        ops._quote_params_for_last_executed_query(("x",))
        mock_cursor.close.assert_called_once()


class LastExecutedQueryFallbackTest(TestCase):
    """Test last_executed_query end-to-end with the fallback."""

    def test_last_executed_query_with_list_params(self):
        """last_executed_query with tuple params doesn't crash on stale streams."""
        ops, _ = _make_ops_with_broken_cursor()
        sql = "INSERT INTO testapp_company (name) VALUES (%s)"
        result = ops.last_executed_query(None, sql, ("Acme",))
        self.assertIn("Acme", result)
        self.assertIn("INSERT", result)

    def test_last_executed_query_with_dict_params(self):
        """last_executed_query with dict params doesn't crash on stale streams."""
        ops, _ = _make_ops_with_broken_cursor()
        sql = "SELECT * FROM t WHERE name = %(name)s"
        result = ops.last_executed_query(None, sql, {"name": "test"})
        self.assertIn("test", result)

    def test_last_executed_query_no_params(self):
        """last_executed_query with no params just returns the SQL."""
        ops, _ = _make_ops_with_broken_cursor()
        sql = "SELECT 1"
        result = ops.last_executed_query(None, sql, None)
        self.assertEqual(result, "SELECT 1")

    def test_last_executed_query_empty_params(self):
        """last_executed_query with empty params just returns the SQL."""
        ops, _ = _make_ops_with_broken_cursor()
        sql = "SELECT 1"
        result = ops.last_executed_query(None, sql, ())
        self.assertEqual(result, "SELECT 1")


class QuoteParamsBatchTest(TestCase):
    """Test that batching still works correctly with the fallback."""

    def test_large_params_batch_with_fallback(self):
        """Params > 999 are batched, and fallback applies to each batch."""
        ops, _ = _make_ops_with_broken_cursor(Exception, "stale")
        params = tuple(str(i) for i in range(1500))
        result = ops._quote_params_for_last_executed_query(params)
        self.assertEqual(len(result), 1500)
        self.assertEqual(result[0], "'0'")
        self.assertEqual(result[999], "'999'")
        self.assertEqual(result[1499], "'1499'")

    def test_large_params_batch_normal(self):
        """Params > 999 are batched correctly with a real connection."""
        ops = _make_ops_with_working_cursor()
        params = tuple(str(i) for i in range(1500))
        result = ops._quote_params_for_last_executed_query(params)
        self.assertEqual(len(result), 1500)
        self.assertEqual(result[0], "'0'")
        self.assertEqual(result[999], "'999'")


class NormalOperationsTest(TestCase):
    """Verify that normal ORM operations still work (uses default backend)."""

    def test_create_and_query(self):
        """Basic CRUD works (exercises the standard last_executed_query path)."""
        Company.objects.create(
            name="Test Corp", address="123 Test St",
            established_date="2024-01-01"
        )
        company = Company.objects.get(name="Test Corp")
        self.assertEqual(company.address, "123 Test St")

    def test_filter_with_params(self):
        """Filter queries with params work correctly."""
        Company.objects.create(
            name="Alpha", address="A St",
            established_date="2020-01-01"
        )
        Company.objects.create(
            name="Beta", address="B St",
            established_date="2021-01-01"
        )
        results = Company.objects.filter(name__in=["Alpha", "Beta"])
        self.assertEqual(results.count(), 2)
