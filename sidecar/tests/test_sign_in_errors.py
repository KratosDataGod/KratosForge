"""Sign-in failures name Entra's code, and a closed sign-in window isn't a failure (UX-003)."""

from fabric_desktop.auth import SignInCancelled, SignInError, sign_in_error


def test_the_entra_code_leads_the_message():
    e = sign_in_error({"error": "interaction_required",
                       "error_description": "AADSTS50076: Due to a configuration change made by your administrator ..."})
    assert type(e) is SignInError and e.code == "AADSTS50076"
    assert str(e) == "AADSTS50076: Due to a configuration change made by your administrator ..."
    assert "Sign-in failed" not in str(e)  # the app adds its own label once


def test_a_code_inside_the_text_is_found_and_no_code_keeps_msals_error():
    e = sign_in_error({"error": "broker_error", "error_description": "Request failed. AADSTS53003: Access has been blocked"})
    assert e.code == "AADSTS53003" and "Access has been blocked" in str(e)
    e = sign_in_error({"error": "invalid_client", "error_description": ""})
    assert e.code == "invalid_client" and str(e) == "invalid_client"


def test_a_cancelled_window_is_its_own_kind():
    # The exact payloads are unverified on a live tenant: the Windows sign-in's UserCanceled status
    # and Entra's error_subcode=cancel from the browser.
    assert isinstance(sign_in_error({"error": "broker_error", "error_description": "...",
                                     "_broker_status": "ResponseStatus.UserCanceled"}), SignInCancelled)
    assert isinstance(sign_in_error({"error": "access_denied", "error_subcode": "cancel"}), SignInCancelled)
    assert not isinstance(sign_in_error({"error": "access_denied", "error_description": "AADSTS65004"}), SignInCancelled)
