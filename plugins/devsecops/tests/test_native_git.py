"""A portable Git selection must retain explicit identity and reject shims."""
import hashlib

import pytest

from tests.native_git import git_exec_path, selected_git


@pytest.fixture
def isolated_selection(monkeypatch):
    tool = selected_git()
    selected_git.cache_clear()
    git_exec_path.cache_clear()
    monkeypatch.delenv('DEVSECOPS_TEST_GIT', raising=False)
    monkeypatch.delenv('DEVSECOPS_TEST_GIT_SHA256', raising=False)
    yield tool
    selected_git.cache_clear()
    git_exec_path.cache_clear()


def test_explicit_git_pin_mismatch_fails_before_execution(isolated_selection, monkeypatch):
    monkeypatch.setenv('DEVSECOPS_TEST_GIT', str(isolated_selection.path))
    monkeypatch.setenv('DEVSECOPS_TEST_GIT_SHA256', '0' * 64)
    with pytest.raises(AssertionError, match='identity changed'):
        selected_git()


def test_explicit_routing_script_is_not_accepted(isolated_selection, tmp_path, monkeypatch):
    path = tmp_path / 'git'
    path.write_text('#!/bin/sh\nexit 0\n')
    path.chmod(0o700)
    monkeypatch.setenv('DEVSECOPS_TEST_GIT', str(path))
    monkeypatch.setenv('DEVSECOPS_TEST_GIT_SHA256', hashlib.sha256(path.read_bytes()).hexdigest())
    with pytest.raises(RuntimeError, match='native Git executable'):
        selected_git()


def test_selected_git_bytes_cannot_change(isolated_selection, tmp_path, monkeypatch):
    path = tmp_path / 'git'
    path.write_bytes(isolated_selection.path.read_bytes())
    path.chmod(0o700)
    monkeypatch.setenv('DEVSECOPS_TEST_GIT', str(path))
    monkeypatch.setenv('DEVSECOPS_TEST_GIT_SHA256', isolated_selection.sha256)
    tool = selected_git()
    path.write_bytes(path.read_bytes() + b'changed-after-selection')
    with pytest.raises(AssertionError, match='identity changed'):
        tool.verify()


def test_git_alias_to_native_multicall_router_is_rejected(isolated_selection, tmp_path, monkeypatch):
    router = tmp_path / 'router'
    router.write_bytes(isolated_selection.path.read_bytes())
    router.chmod(0o700)
    alias = tmp_path / 'git'
    alias.symlink_to(router)
    monkeypatch.setenv('DEVSECOPS_TEST_GIT', str(alias))
    monkeypatch.setenv('DEVSECOPS_TEST_GIT_SHA256', isolated_selection.sha256)
    with pytest.raises(RuntimeError, match='native Git executable'):
        selected_git()
