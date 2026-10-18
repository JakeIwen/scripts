"""The BTT guard alerts only after stable, privacy-preserving audits."""
import unittest
from unittest.mock import patch

from macbook.bettertouchtool.btt_guard.monitor import (
    DEBOUNCE_SECONDS,
    DELIVERY_RETRY_SECONDS,
    INVALID_STATE,
    MonitorStateError,
    initial_state,
    monitor_report,
    select_monitor_state,
    validate_state,
)
from macbook.bettertouchtool.btt_guard.model import AuditStatus
from macbook.bettertouchtool.btt_guard.notify import (
    GuardNotification,
    _warning_url,
    alert_notification,
    finding_counts,
    send_warning_notification,
)


def report(status="healthy", findings=None):
    complete_findings = [
        {"uuid": "fixture-uuid", "detail": "fixture-detail", **finding}
        for finding in (findings or [])
    ]
    return {
        "status": status,
        "checkpoint_id": "private-checkpoint",
        "checked_at": 0.0,
        "findings": complete_findings,
        "btt_version": "private-version",
    }


class RecordingSender:
    def __init__(self, results=None):
        self.results = iter(results or [])
        self.notifications = []

    def __call__(self, notification):
        self.notifications.append(notification)
        return next(self.results, True)


class GuardMonitorTests(unittest.TestCase):
    def setUp(self):
        self.state = initial_state()

    def observe(self, audit, sender, now):
        result = monitor_report(audit, state=self.state, sender=sender, now=now)
        self.state = result.state
        return result

    def test_alert_requires_matching_audits_after_debounce(self):
        sender = RecordingSender()
        drift = report("drift", [{"kind": "missing_item", "uuid": "secret", "detail": "secret"}])
        self.assertEqual(self.observe(drift, sender, 0).action, "pending")
        self.assertEqual(self.observe(drift, sender, DEBOUNCE_SECONDS - 1).action, "pending")
        outcome = self.observe(drift, sender, DEBOUNCE_SECONDS)
        self.assertEqual((outcome.audit_status, outcome.action), ("drift", "sent"))
        self.assertEqual(len(sender.notifications), 1)

    def test_same_counts_for_different_findings_do_not_confirm_drift(self):
        sender = RecordingSender()
        first = report("drift", [{"kind": "missing", "uuid": "one", "detail": "record"}])
        second = report("drift", [{"kind": "missing", "uuid": "two", "detail": "record"}])
        self.observe(first, sender, 0)
        outcome = self.observe(second, sender, DEBOUNCE_SECONDS)
        self.assertEqual(outcome.action, "pending")
        self.assertEqual(sender.notifications, [])

    def test_different_incident_with_same_counts_alerts_after_confirmation(self):
        sender = RecordingSender()
        first = report("drift", [{"kind": "missing", "uuid": "one", "detail": "record"}])
        second = report("drift", [{"kind": "missing", "uuid": "two", "detail": "record"}])
        self.observe(first, sender, 0)
        self.assertEqual(self.observe(first, sender, DEBOUNCE_SECONDS).action, "sent")
        self.assertEqual(self.observe(second, sender, 2 * DEBOUNCE_SECONDS).action, "pending")
        self.assertEqual(self.observe(second, sender, 3 * DEBOUNCE_SECONDS).action, "sent")
        self.assertEqual(len(sender.notifications), 2)

    def test_successful_alert_is_deduplicated(self):
        sender = RecordingSender()
        drift = report("drift", [{"kind": "changed"}])
        self.observe(drift, sender, 0)
        self.observe(drift, sender, DEBOUNCE_SECONDS)
        outcome = self.observe(drift, sender, 10 * DEBOUNCE_SECONDS)
        self.assertEqual(outcome.action, "deduplicated")
        self.assertEqual(len(sender.notifications), 1)

    def test_failed_delivery_retries_only_after_bounded_cooldown(self):
        sender = RecordingSender([False, True])
        unavailable = report("unavailable", [{"kind": "database_error"}])
        self.observe(unavailable, sender, 0)
        failed = self.observe(unavailable, sender, DEBOUNCE_SECONDS)
        self.assertEqual((failed.audit_status, failed.action), ("unavailable", "delivery-failed"))
        self.assertEqual(self.observe(unavailable, sender, DEBOUNCE_SECONDS + DELIVERY_RETRY_SECONDS - 1).action, "cooldown")
        self.assertEqual(self.observe(unavailable, sender, DEBOUNCE_SECONDS + DELIVERY_RETRY_SECONDS).action, "sent")
        self.assertEqual(len(sender.notifications), 2)

    def test_recovery_requires_two_healthy_audits_and_is_sent_once(self):
        sender = RecordingSender()
        drift = report("drift", [{"kind": "unexpected"}])
        self.observe(drift, sender, 0)
        self.observe(drift, sender, DEBOUNCE_SECONDS)
        first = self.observe(report(), sender, DEBOUNCE_SECONDS + 1)
        second = self.observe(report(), sender, 2 * DEBOUNCE_SECONDS + 1)
        third = self.observe(report(), sender, 3 * DEBOUNCE_SECONDS + 1)
        self.assertEqual((first.action, second.action, third.action), ("pending", "sent", "not-needed"))
        self.assertEqual(len(sender.notifications), 2)

    def test_healthy_report_after_failed_alert_does_not_claim_recovery(self):
        sender = RecordingSender([False])
        drift = report("drift", [{"kind": "missing"}])
        self.observe(drift, sender, 0)
        self.assertEqual(self.observe(drift, sender, DEBOUNCE_SECONDS).action, "delivery-failed")
        outcome = self.observe(report(), sender, 2 * DEBOUNCE_SECONDS)
        self.assertEqual(outcome.action, "not-needed")
        self.assertEqual(len(sender.notifications), 1)

    def test_notification_never_contains_private_report_fields(self):
        secret_values = ("uuid-secret", "rm -rf private", "Private Note", "7.9.9")
        findings = [
            {"kind": "checkpoint_missing", "uuid": secret_values[0], "detail": secret_values[1]},
            {"kind": "Private Note", "uuid": "another", "detail": "detail"},
        ]
        notification = alert_notification(AuditStatus.DRIFT, finding_counts(findings))
        rendered = notification.title + notification.message
        for secret in secret_values:
            self.assertNotIn(secret, rendered)

    def test_malformed_state_is_an_error_not_a_healthy_result(self):
        with self.assertRaises(MonitorStateError):
            validate_state({"version": 1, "alert_active": False})
        state = initial_state()
        state["candidate_key"] = "Private Note title"
        state["candidate_since"] = 1
        state["candidate_observations"] = 1
        with self.assertRaises(MonitorStateError):
            validate_state(state)

    def test_invalid_primary_and_fallback_start_new_recovery_state(self):
        selection = select_monitor_state(INVALID_STATE, {"malformed": True})
        self.assertEqual(selection.reason, "both-invalid")
        self.assertEqual(selection.write_target, "recovery")
        self.assertEqual(selection.state, initial_state())

    def test_state_contains_no_report_details(self):
        sender = RecordingSender()
        drift = report("drift", [{"kind": "missing", "uuid": "uuid-secret", "detail": "detail-secret"}])
        result = self.observe(drift, sender, 0)
        rendered = repr(result.state)
        self.assertNotIn("uuid-secret", rendered)
        self.assertNotIn("detail-secret", rendered)
        self.assertEqual(result.state["candidate_observations"], 1)

    @patch("macbook.bettertouchtool.btt_guard.notify.subprocess.run")
    def test_warning_url_is_sourced_without_exposing_it_in_arguments(self, run):
        run.return_value.returncode = 0
        run.return_value.stdout = "https://warning.invalid/private-topic"
        self.assertEqual(_warning_url(), "https://warning.invalid/private-topic")
        command = run.call_args.args[0]
        self.assertNotIn("https://warning.invalid/private-topic", command)
        self.assertEqual(command[:4], ["/bin/bash", "--noprofile", "--norc", "-c"])
        self.assertEqual(run.call_args.kwargs["stderr"], -3)

    @patch("macbook.bettertouchtool.btt_guard.notify.urllib.request.build_opener")
    @patch(
        "macbook.bettertouchtool.btt_guard.notify._warning_url",
        return_value="https://warning.invalid/private-topic",
    )
    def test_sender_posts_redacted_payload_to_warning_topic(self, warning_url, build_opener):
        response = build_opener.return_value.open.return_value.__enter__.return_value
        response.status = 200
        notification = GuardNotification("generic title", "generic message", "high")
        self.assertTrue(send_warning_notification(notification))
        request = build_opener.return_value.open.call_args.args[0]
        self.assertEqual(request.method, "POST")
        self.assertEqual(request.data, b"generic message")
        self.assertEqual(request.get_header("Title"), "generic title")
        self.assertEqual(request.get_header("Priority"), "high")
        warning_url.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
