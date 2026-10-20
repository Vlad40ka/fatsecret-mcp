"""Everything the build and the release run is pinned, so a moved tag can't change what gets published."""

from __future__ import annotations

import re
from pathlib import Path

REPOSITORY = Path(__file__).resolve().parents[1]
WORKFLOWS = sorted((REPOSITORY / ".github" / "workflows").glob("*.yml"))
DOCKERFILE = (REPOSITORY / "Dockerfile").read_text()

ACTION = re.compile(r"uses: (.+)$", re.MULTILINE)
PINNED_ACTION = re.compile(r"[\w.-]+/[\w./-]+@[0-9a-f]{40} # v\d+\.\d+\.\d+")
CHECKOUT = re.compile(r"uses: actions/checkout@")
CHECKOUT_WITHOUT_TOKEN = re.compile(r"uses: actions/checkout@.*\n +with:\n +persist-credentials: false\n")
IMAGE_SOURCE = re.compile(r"^(?:FROM |COPY --from=)(\S+)", re.MULTILINE)
STAGE = re.compile(r"^FROM \S+ AS (\S+)$", re.MULTILINE)
PINNED_IMAGE = re.compile(r"[\w./-]+:[\w.-]+@sha256:[0-9a-f]{64}")
IMAGE_UV_VERSION = re.compile(r"ghcr\.io/astral-sh/uv:([\d.]+)@")
WORKFLOW_UV_VERSION = re.compile(r'version: "([\d.]+)"')


def test_every_action_is_pinned_to_a_commit() -> None:
    actions = ACTION.findall(_workflows_text())
    unpinned = [action for action in actions if not PINNED_ACTION.fullmatch(action)]

    assert unpinned == []


def test_every_checkout_leaves_no_token_behind() -> None:
    workflows = _workflows_text()

    checkouts = CHECKOUT.findall(workflows)
    checkouts_without_token = CHECKOUT_WITHOUT_TOKEN.findall(workflows)

    assert len(checkouts_without_token) == len(checkouts)


def test_every_base_image_is_pinned_by_digest() -> None:
    stages = set(STAGE.findall(DOCKERFILE))
    sources = IMAGE_SOURCE.findall(DOCKERFILE)

    images = [source for source in sources if source not in stages]
    unpinned = [image for image in images if not PINNED_IMAGE.fullmatch(image)]

    assert unpinned == []


def test_the_image_and_the_workflows_build_with_one_uv() -> None:
    image_uv_version = _image_uv_version()
    workflow_uv_versions = set(WORKFLOW_UV_VERSION.findall(_workflows_text()))

    assert workflow_uv_versions == {image_uv_version}


def _image_uv_version() -> str:
    found = IMAGE_UV_VERSION.search(DOCKERFILE)

    assert found is not None

    return found[1]


def _workflows_text() -> str:
    texts = [workflow.read_text() for workflow in WORKFLOWS]

    return "\n".join(texts)
