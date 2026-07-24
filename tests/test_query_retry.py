import unittest

import pandas as pd

from src.query_retry import (
    is_transient_query_error,
    transient_retry_delay,
    warning_frame_has_transient_error,
)


class QueryRetryTests(unittest.TestCase):
    def test_reported_winerror_and_ssl_eof_are_retryable(self):
        messages = (
            "无法连接 EPI Web Suite: [WinError 10054] 远程主机强迫关闭了一个现有的连接。",
            "无法连接 EPI Web Suite: [SSL: UNEXPECTED_EOF_WHILE_READING] EOF occurred in violation of protocol (_ssl.c:1010)",
            "SSLEOFError: EOF occurred in violation of protocol",
            "connection forcibly closed by remote host",
        )
        for message in messages:
            with self.subTest(message=message):
                self.assertTrue(is_transient_query_error(message))

    def test_transient_retry_delay_is_exponential_with_bounded_jitter(self):
        self.assertEqual(
            transient_retry_delay(1, random_value=0.0),
            1.0,
        )
        self.assertEqual(
            transient_retry_delay(2, random_value=0.0),
            2.0,
        )
        self.assertEqual(
            transient_retry_delay(2, random_value=1.0),
            2.4,
        )

    def test_transient_statuses_and_network_failures_are_retryable(self):
        for text in (
            "HTTP 408: request timeout",
            "HTTP 425: too early",
            "HTTP 429: rate limited",
            "HTTP 500: server error",
            "HTTP 502: bad gateway",
            "HTTP 503: unavailable",
            "HTTP 504: gateway timeout",
            "timed out",
            "connection reset by peer",
            "temporary failure in name resolution",
        ):
            with self.subTest(text=text):
                self.assertTrue(is_transient_query_error(text))

    def test_validation_400_and_not_found_are_not_retryable(self):
        self.assertFalse(is_transient_query_error("HTTP 400: Invalid CAS ID"))
        self.assertFalse(is_transient_query_error("HTTP 404: not found"))

    def test_warning_frame_reads_message_and_error_columns(self):
        self.assertTrue(
            warning_frame_has_transient_error(
                pd.DataFrame({"message": ["HTTP 503: unavailable"]})
            )
        )
        self.assertTrue(
            warning_frame_has_transient_error(
                pd.DataFrame({"error": ["connection reset by peer"]})
            )
        )
        self.assertFalse(
            warning_frame_has_transient_error(
                pd.DataFrame({"message": ["HTTP 400: bad input"]})
            )
        )


if __name__ == "__main__":
    unittest.main()
