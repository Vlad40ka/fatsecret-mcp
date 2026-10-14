"""OAuth 1.0 request signing."""

from __future__ import annotations

from fatsecret_mcp.fatsecret.oauth import signature


def test_signs_requests_with_hmac_sha1_as_oauth_1_specifies() -> None:
    # The worked example from the OAuth Core 1.0 specification, appendix A.5.
    params = {
        "file": "vacation.jpg",
        "size": "original",
        "oauth_consumer_key": "dpf43f3p2l4k3l03",
        "oauth_token": "nnch734d00sl2jdk",
        "oauth_signature_method": "HMAC-SHA1",
        "oauth_timestamp": "1191242096",
        "oauth_nonce": "kllo9940pd9333jh",
        "oauth_version": "1.0",
    }

    signed = signature(
        "GET", "http://photos.example.net/photos", params, "kd94hf93k423kf44", "pfkkdhi9sl3r4s00"
    )

    assert signed == "tR3+Ty81lMeYAr/Fid0kMTYa/WM="
