"""Notice delivery edges an uncapped mutation run found unchecked (batch 252, docs/quality/mutation-testing.md):
a port given as the text "0"."""

import pytest

from fabric_desktop.workloads import deliver


def test_a_port_of_zero_given_as_text_is_refused():
    # A number 0 falls back to 587 (``or 587``); the text "0" doesn't, and 0 isn't a port.
    with pytest.raises(ValueError, match="from 1 to 65535"):
        deliver.MailServer.parse(
            {"host": "smtp.contoso.com", "sender": "kf@contoso.com", "port": "0"}
        )
