# Owned by phase 001 step S09: Alertmanager delivering to Slack (docker/alertmanager/, a webhook URL file under ${K2_SECRETS}).
# Only that step edits this file (docs/development/phase-001-interfaces.md).
# The procedure around these targets: docs/runbooks/where-an-alert-goes.md.

.PHONY: alert-test
alert-test:  ## Send a labelled K2TestAlert through k2-alertmanager: PASS when Slack took it, SKIP while no webhook is installed
	bash scripts/alerts.sh test

.PHONY: alert-test-standin
alert-test-standin:  ## The whole alert path on a throwaway Alertmanager and Prometheus, to a local stand-in for Slack: nothing live touched
	bash scripts/alerts.sh standin

.PHONY: alerts-install-webhook
alerts-install-webhook:  ## Install or rotate the Slack webhook URL, read from stdin (never argv), into ${K2_SECRETS}/alertmanager/
	@bash scripts/alerts.sh install-webhook
