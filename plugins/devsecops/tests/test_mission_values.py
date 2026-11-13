import time

import pytest

from devsecops.adapters.mission_values import replace_mission_image

REPO = 'registry.example/demo/mission'
DIGEST = 'sha256:' + 'a'*64


@pytest.mark.parametrize('before', [
    'image:\n  repository: "" # retain 🚀\n  digest: \'\'\nother: café\n',
    'image: {repository: "", digest: ""} # retain\r\nother: café\r\n',
    'image:\n  digest: ""\n  repository: ""',
])
def test_two_scalar_spans_preserve_every_other_byte(before):
    expected = before.replace('repository: ""', 'repository: "' + REPO + '"').replace('digest: \'\'', 'digest: "' + DIGEST + '"').replace('digest: ""', 'digest: "' + DIGEST + '"')
    after, changes = replace_mission_image(before.encode(), repository=REPO, digest=DIGEST, deadline=time.monotonic()+5)
    assert after == expected.encode() and all(item['changed'] for item in changes.values())


def test_noop_preserves_existing_quote_styles_and_comments():
    raw = f"image: {{repository: '{REPO}', digest: {DIGEST}}} # unchanged\n".encode()
    after, changes = replace_mission_image(raw, repository=REPO, digest=DIGEST, deadline=time.monotonic()+5)
    assert after == raw and not any(item['changed'] for item in changes.values())


@pytest.mark.parametrize('raw', [
    b'image: {repository: "", digest: "", digest: ""}\n',
    b'image: &image {repository: "", digest: ""}\n',
    b'x: &image {repository: "", digest: ""}\nimage: *image\n',
    b'x: &image {repository: "", digest: ""}\nimage: {<<: *image}\n',
    b'image: {repository: ""}\n', b'image: {repository: null, digest: ""}\n',
    b'image: {repository: [], digest: ""}\n', b'image: {repository: !str "", digest: ""}\n',
    b'image: {repository: "", digest: ""}\n---\nother: 1\n',
    b'image:\n  repository: >\n    registry.example/demo/mission\n  digest: ""\n',
    b'image:\n  repository: "registry.example/\n    demo"\n  digest: ""\n',
    b'image: {repository: "image:latest", digest: ""}\n',
    b'image: {repository: "", digest: "latest"}\n', b'[]', b'\xff', b'x'*64001,
])
def test_unsupported_or_ambiguous_values_are_rejected(raw):
    with pytest.raises((ValueError, UnicodeError)):
        replace_mission_image(raw, repository=REPO, digest=DIGEST, deadline=time.monotonic()+5)


def test_expired_request_does_not_parse():
    with pytest.raises(ValueError):
        replace_mission_image(b'image: {}', repository=REPO, digest=DIGEST, deadline=time.monotonic()-1)
