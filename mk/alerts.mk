# Owned by phase 001 step S09: Alertmanager delivering to Slack (docker/alertmanager/, a webhook URL file under ${K2_SECRETS}).
# Only that step edits this file (docs/development/phase-001-interfaces.md).
# Targets it will hold: alert-test. Until S09 lands each is a
# stub that says so and exits 0; a stub never shadows a target the root Makefile defines.

.PHONY: alert-test
alert-test:  ## SKIP until step S09: send K2TestAlert through Alertmanager; the Slack sent counter rises, the failed one does not
	@echo "SKIP alert-test: built in phase 001 step S09"
