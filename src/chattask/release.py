"""Explicit GitHub merged-PR/check/tag gate. A tag is never a publish receipt."""
from __future__ import annotations

import asyncio


class ReleaseBlocked(RuntimeError):
    pass


class ChatGHReleaseAdapter:
    def __init__(self, *, provider=None):
        self.provider = provider

    async def tag_after_merge(self, run: dict) -> dict:
        return await asyncio.to_thread(self._tag_sync, run)

    def _tag_sync(self, run: dict) -> dict:
        if not run.get('pull_request'):
            raise ReleaseBlocked('pull request number required before tag approval')
        if self.provider is None:
            try:
                from chatgh.github.api import credential_path_from_repo, get_client, resolve_token
            except ImportError as exc:
                raise ReleaseBlocked('ChatGH release dependency unavailable') from exc
            target = credential_path_from_repo(run['github_repo'])
            token = resolve_token(None, credential_path=target, exact_only=True,
                                  cwd=run.get('repository_path'))
            if not token:
                raise ReleaseBlocked('repo-scoped ChatGH credential unavailable')
            github = get_client(token, require_token=True)
            repo = github.get_repo(run['github_repo'])
        else:
            repo = self.provider.get_repo(run['github_repo'])
        pr = repo.get_pull(run['pull_request'])
        if not pr.merged or pr.base.ref != run['default_branch'] or pr.head.sha != run['source_sha']:
            raise ReleaseBlocked('PR must be merged to the registered default branch with the exact source SHA')
        commit_sha = pr.merge_commit_sha
        if not commit_sha:
            raise ReleaseBlocked('merged PR has no verified merge commit')
        checks = list(repo.get_commit(run['source_sha']).get_check_runs())
        if not checks or any(c.status != 'completed' or c.conclusion != 'success' for c in checks):
            raise ReleaseBlocked('exact source HEAD checks are missing or not green')
        default_sha = repo.get_branch(run['default_branch']).commit.sha
        compare = repo.compare(commit_sha, default_sha)
        if compare.status not in {'identical', 'ahead'}:
            raise ReleaseBlocked('merge commit is not on the default branch')
        try:
            workflow = repo.get_contents('.github/workflows/publish.yml', ref=commit_sha).decoded_content.decode('utf-8')
        except Exception as exc:
            raise ReleaseBlocked('tag-only OIDC PyPI workflow missing at merge commit') from exc
        if 'id-token: write' not in workflow or 'pypa/gh-action-pypi-publish' not in workflow or 'on:\n  push:\n    tags:' not in workflow or 'v*' not in workflow:
            raise ReleaseBlocked('tag-only OIDC PyPI workflow prerequisites missing')
        tag = 'v' + run['version']
        refname = 'tags/' + tag
        try:
            existing = repo.get_git_ref(refname)
        except Exception as exc:
            if getattr(exc, 'status', None) != 404:
                raise
            existing = None
        if existing is not None:
            if existing.object.sha != commit_sha:
                raise ReleaseBlocked('release tag exists at a different target')
            return {'tag': tag, 'target_sha': commit_sha, 'tag_verified': True,
                    'publication': 'awaiting OIDC workflow and PyPI artifact verification', 'reconciled': True}
        try:
            repo.create_git_ref(ref='refs/' + refname, sha=commit_sha)
        except Exception:
            # A lost response is not permission to retry an irreversible write.
            try:
                observed = repo.get_git_ref(refname)
                if observed.object.sha == commit_sha:
                    return {'tag': tag, 'target_sha': commit_sha, 'tag_verified': True,
                            'publication': 'awaiting OIDC workflow and PyPI artifact verification', 'reconciled': True}
            except Exception:
                pass
            raise RuntimeError('tag write outcome unknown; inspect remote target before retry')
        observed = repo.get_git_ref(refname)
        if observed.object.sha != commit_sha:
            raise RuntimeError('tag target verification failed')
        return {'tag': tag, 'target_sha': commit_sha, 'tag_verified': True,
                'publication': 'awaiting OIDC workflow and PyPI artifact verification', 'reconciled': False}
