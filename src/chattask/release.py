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
            token = resolve_token(None, credential_path=target, exact_only=False,
                                  cwd=run.get('repository_path'))
            if not token:
                raise ReleaseBlocked('ChatGH repository/default credential unavailable')
            github = get_client(token, require_token=True)
            repo = github.get_repo(run['github_repo'])
        else:
            repo = self.provider.get_repo(run['github_repo'])
        pr = repo.get_pull(run['pull_request'])
        if not pr.merged or pr.base.ref != run['default_branch']:
            raise ReleaseBlocked('PR must be merged to the registered default branch')
        pr_head = pr.head.sha
        if not isinstance(run.get('source_tree'), str) or len(run['source_tree']) != 40:
            raise ReleaseBlocked('run is missing the immutable preflight source tree')
        pr_tree = repo.get_git_commit(pr_head).tree.sha
        if pr_tree != run['source_tree']:
            raise ReleaseBlocked('PR head tree differs from the preflight source tree')
        commit_sha = pr.merge_commit_sha
        if not commit_sha:
            raise ReleaseBlocked('merged PR has no verified merge commit')
        checks = list(repo.get_commit(pr_head).get_check_runs())
        if (not checks or any(c.status != 'completed' or c.conclusion != 'success' for c in checks)
                or not any(c.name == 'test' and c.status == 'completed' and c.conclusion == 'success' for c in checks)):
            raise ReleaseBlocked('exact PR HEAD project CI check is missing or not green')
        ci_runs = list(repo.get_workflow_runs(head_sha=pr_head, event='pull_request'))
        if not any(workflow_run.name == 'CI' and workflow_run.status == 'completed' and workflow_run.conclusion == 'success' for workflow_run in ci_runs):
            raise ReleaseBlocked('exact PR HEAD CI workflow is missing or not green')
        default_sha = repo.get_branch(run['default_branch']).commit.sha
        compare = repo.compare(commit_sha, default_sha)
        if compare.status not in {'identical', 'ahead'}:
            raise ReleaseBlocked('merge commit is not on the default branch')
        merge_tree = repo.get_git_commit(commit_sha).tree.sha
        if merge_tree != run['source_tree']:
            raise ReleaseBlocked('merged default-branch tree differs from the preflight source tree')
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
        def resolved_target(ref):
            target = ref.object
            if target.type == 'commit':
                return target.sha
            if target.type == 'tag':
                annotation = repo.get_git_tag(target.sha)
                if annotation.object.type == 'commit':
                    return annotation.object.sha
            raise ReleaseBlocked('release tag has unsupported target type')

        if existing is not None:
            if resolved_target(existing) != commit_sha:
                raise ReleaseBlocked('release tag exists at a different target')
            return {'tag': tag, 'target_sha': commit_sha, 'source_tree': run['source_tree'],
                    'pr_head': pr_head, 'merge_tree': merge_tree, 'tag_verified': True,
                    'publication': 'awaiting OIDC workflow and PyPI artifact verification', 'reconciled': True}
        try:
            annotation = repo.create_git_tag(tag, 'Release ' + run['github_repo'] + ' ' + tag,
                                             commit_sha, 'commit')
            repo.create_git_ref(ref='refs/' + refname, sha=annotation.sha)
        except Exception:
            # A lost response is not permission to retry an irreversible write.
            try:
                observed = repo.get_git_ref(refname)
                if resolved_target(observed) == commit_sha:
                    return {'tag': tag, 'target_sha': commit_sha, 'source_tree': run['source_tree'],
                    'pr_head': pr_head, 'merge_tree': merge_tree, 'tag_verified': True,
                            'publication': 'awaiting OIDC workflow and PyPI artifact verification', 'reconciled': True}
            except Exception:
                pass
            raise RuntimeError('tag write outcome unknown; inspect remote target before retry')
        observed = repo.get_git_ref(refname)
        if resolved_target(observed) != commit_sha:
            raise RuntimeError('tag target verification failed')
        return {'tag': tag, 'target_sha': commit_sha, 'source_tree': run['source_tree'],
                'pr_head': pr_head, 'merge_tree': merge_tree, 'tag_verified': True,
                'publication': 'awaiting OIDC workflow and PyPI artifact verification', 'reconciled': False}
