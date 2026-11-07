"""CLI entrypoint for chattask."""

from __future__ import annotations

import click
from chatstyle import add_tree_option

from chattask import __version__


@click.group(name="chattask", invoke_without_command=True, no_args_is_help=True)
@click.version_option(__version__, prog_name="chattask")
@add_tree_option(renderer_options={"root_name": "chattask"})
def main() -> None:
    """chattask command line interface."""
    # Add package-specific commands here. Prefer ChatStyle helpers for
    # interactive input when a command needs recoverable user input.
    pass


@main.command()
@click.option('--host', default='127.0.0.1', show_default=True, help='Bind address; prefer loopback behind a trusted reverse proxy.')
@click.option('--port', type=click.IntRange(1,65535), default=8000, show_default=True)
@click.option('-e','--env-profile', default=None, help='Read this ChatEnv profile without globally activating it.')
def serve(host, port, env_profile):
    """Serve the authenticated Task/Sandbox browser and API from this package."""
    try:
        import uvicorn
        from .settings import load_settings
        from . import create_app
        app=create_app(load_settings(profile=env_profile))
    except ImportError as exc:
        raise click.ClickException('Web dependencies are required; install ChatTask[web].') from exc
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc
    uvicorn.run(app,host=host,port=port,access_log=False,proxy_headers=False)


@main.command(name='paths')
@click.option('--json-output',is_flag=True,help='Emit the resolved runtime paths as JSON.')
def paths_command(json_output):
    """Resolve ChatArch-contained state paths without creating them."""
    from . import state_paths
    import json
    paths=state_paths()
    if json_output:
        click.echo(json.dumps(paths,ensure_ascii=False))
    else:
        for key,value in paths.items():click.echo(f'{key}={value}')



@main.group(name='repository')
def repository_group():
    """Manage trusted local source roots for template runs."""


@repository_group.command(name='register')
@click.option('--id', 'repo_id', required=True, help='Stable repository ID shown in the browser.')
@click.option('--path', type=click.Path(path_type=str, exists=True, file_okay=False), required=True,
              help='Absolute clean managed Git root. Only an operator may register it.')
@click.option('--github-repo', required=True, help='GitHub owner/name; no URL or credentials.')
@click.option('--default-branch', default='main', show_default=True)
@click.option('--package-name', default='', help='PyPI distribution name for metadata probe.')
def register_repository(repo_id, path, github_repo, default_branch, package_name):
    """Register an operator-trusted Git root; browser users cannot supply paths."""
    from .settings import state_paths
    from .workflow import TaskStore, WorkflowService
    try:
        flow = WorkflowService(TaskStore(state_paths()['state_dir']))
        record = flow.register_repository(repo_id, path, github_repo, default_branch, package_name)
    except (ValueError, OSError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"Registered {record['id']} at an exact managed Git root.")

@main.command(name='run')
@click.argument('instructions', required=False)
@click.option('--instructions-file',type=click.Path(exists=True,dir_okay=False,path_type=str),help='Read task instructions from a local UTF-8 file.')
@click.option('--image-profile',required=True,help='Operator-configured image profile ID, not an arbitrary image.')
@click.option('--request-id',required=True,help='Stable task create/reconciliation identity.')
@click.option('--input-task-id',default=None,help='Use this owner\'s verified prior result archive as input.')
@click.option('-e','--env-profile',default=None,help='Named ChatEnv runtime configuration.')
@click.option('--json-output',is_flag=True)
def run_command(instructions,instructions_file,image_profile,request_id,input_task_id,env_profile,json_output):
    """Run a task using Codex inside an ephemeral Sandbox and return its result."""
    import asyncio,json
    from pathlib import Path
    from .settings import load_settings
    from .agent_tasks import run_task
    if bool(instructions)==bool(instructions_file):
        raise click.UsageError('Provide task instructions or --instructions-file, exactly one.')
    try:
        text=Path(instructions_file).read_text(encoding='utf-8') if instructions_file else instructions
        result=asyncio.run(run_task(load_settings(profile=env_profile),text,image_profile=image_profile,request_id=request_id,input_task_id=input_task_id))
    except (OSError,ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(json.dumps(result,ensure_ascii=False) if json_output else f"{result['id']} · {result['status']}\n{result.get('result') or result.get('error') or ''}")
    if result['status']!='completed':raise click.exceptions.Exit(1)


def _durable_service(env_profile):
    from .settings import load_settings
    from .durable import configured_service
    return configured_service(load_settings(profile=env_profile))


def _durable_call(operation, env_profile, owner, *args):
    import json
    from .durable import DurableConflict
    try:
        result = getattr(_durable_service(env_profile), operation)(owner, *args)
    except (KeyError, ValueError, OSError, DurableConflict) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(json.dumps(result, ensure_ascii=False))


@main.group(name='task')
def task_group():
    """Control private long-term Tasks; rounds execute in a separate worker."""


@task_group.command(name='create')
@click.argument('instructions')
@click.option('--image-profile', required=True)
@click.option('--workflow', type=click.Choice(['development','research']), default='development', show_default=True)
@click.option('--sources-file', type=click.Path(exists=True, dir_okay=False, path_type=str), help='JSON array of caller-verified research source records.')
@click.option('--request-id', required=True)
@click.option('--owner', required=True, help='Trusted local principal identity.')
@click.option('-e', '--env-profile', default=None)
def durable_create(instructions, image_profile, workflow, sources_file, request_id, owner, env_profile):
    """Queue the first round of a long-term Task."""
    import json
    from pathlib import Path
    body={'instructions': instructions, 'image_profile': image_profile, 'request_id': request_id, 'workflow': workflow}
    if sources_file:
        try:body['source_records']=json.loads(Path(sources_file).read_text(encoding='utf-8'))
        except (OSError,ValueError) as exc:raise click.ClickException('invalid research sources JSON file') from exc
    _durable_call('create', env_profile, owner, body)


@task_group.command(name='list')
@click.option('--owner', required=True)
@click.option('-e', '--env-profile', default=None)
def durable_list(owner, env_profile):
    """List Tasks visible to a trusted local principal."""
    _durable_call('list', env_profile, owner)


@task_group.command(name='show')
@click.argument('task_id')
@click.option('--owner', required=True)
@click.option('-e', '--env-profile', default=None)
def durable_show(task_id, owner, env_profile):
    """Show a Task and its immutable round history."""
    import json
    try:
        service = _durable_service(env_profile)
        click.echo(json.dumps({'task': service.get(owner, task_id), 'runs': service.runs(owner, task_id)}, ensure_ascii=False))
    except (KeyError, ValueError, OSError) as exc:
        raise click.ClickException(str(exc)) from exc


@task_group.command(name='resume')
@click.argument('task_id')
@click.argument('instructions')
@click.option('--request-id', required=True)
@click.option('--acknowledge-unknown', is_flag=True, help='Confirm inspection of interrupted round and external side effects.')
@click.option('--sources-file', type=click.Path(exists=True, dir_okay=False, path_type=str), help='Optional new caller-verified research source records.')
@click.option('--owner', required=True)
@click.option('-e', '--env-profile', default=None)
def durable_resume(task_id, instructions, request_id, acknowledge_unknown, sources_file, owner, env_profile):
    """Queue an explicit new native Codex round from a verified checkpoint."""
    import json
    from pathlib import Path
    body={'request_id': request_id, 'instructions': instructions, 'acknowledge_unknown': acknowledge_unknown}
    if sources_file:
        try:body['source_records']=json.loads(Path(sources_file).read_text(encoding='utf-8'))
        except (OSError,ValueError) as exc:raise click.ClickException('invalid research sources JSON file') from exc
    _durable_call('resume', env_profile, owner, task_id, body)


@task_group.command(name='pause')
@click.argument('task_id')
@click.option('--request-id', required=True)
@click.option('--owner', required=True)
@click.option('-e', '--env-profile', default=None)
def durable_pause(task_id, request_id, owner, env_profile):
    """Pause queued work now or request a cooperative end-of-round pause."""
    _durable_call('pause', env_profile, owner, task_id, {'request_id': request_id})


@task_group.command(name='complete')
@click.argument('task_id')
@click.option('--request-id', required=True)
@click.option('--owner', required=True)
@click.option('-e', '--env-profile', default=None)
def durable_complete(task_id, request_id, owner, env_profile):
    """Mark an inactive long-term Task complete; history remains available."""
    _durable_call('complete', env_profile, owner, task_id, {'request_id': request_id})


@task_group.command(name='reconcile-research')
@click.argument('task_id')
@click.option('--request-id', required=True)
@click.option('--acknowledge-unknown', is_flag=True,
              help='Confirm inspection of the original project before retrying the same operation identity.')
@click.option('--owner', required=True)
@click.option('-e', '--env-profile', default=None)
def durable_reconcile_research(task_id, request_id, acknowledge_unknown, owner, env_profile):
    """Queue explicit same-round Research readback/reconciliation."""
    _durable_call('reconcile_research', env_profile, owner, task_id,
                  {'request_id': request_id, 'acknowledge_unknown': acknowledge_unknown})


@main.command(name='worker')
@click.option('--once', is_flag=True, help='Claim at most one queued round, then exit.')
@click.option('--operator', required=True, help='Trusted local operator starting this single writer.')
@click.option('-e', '--env-profile', required=True, help='Named ChatEnv runtime configuration.')
def durable_worker(once, operator, env_profile):
    """Run the independent durable queue executor under a process-held lock."""
    import asyncio, json
    from .settings import load_settings
    from .durable import run_worker
    if not operator.strip():
        raise click.UsageError('nonempty --operator required')
    try:
        result = asyncio.run(run_worker(load_settings(profile=env_profile), once=once))
    except (ValueError, OSError, RuntimeError) as exc:
        raise click.ClickException(str(exc)) from exc
    if once:
        click.echo(json.dumps(result, ensure_ascii=False))



@main.group(name='package')
def package_group():
    """Inspect and run the versioned Python package template."""

@package_group.command(name='schema')
def package_schema_command():
    """Print the channel-neutral package form schema."""
    import json
    from .package_template import form_schema
    click.echo(json.dumps(form_schema(),ensure_ascii=False,indent=2))

@package_group.command(name='plan')
@click.option('--spec-file',type=click.Path(exists=True,dir_okay=False,path_type=str),required=True)
def package_plan_command(spec_file):
    """Validate a JSON spec and show exact prepare policy/instructions without executing."""
    import json
    from pathlib import Path
    from .package_template import validate_spec,fingerprint,policy,prepare_instructions
    try:
        spec=validate_spec(json.loads(Path(spec_file).read_text(encoding='utf-8')))
    except (ValueError,OSError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(json.dumps({'spec':spec,'fingerprint':fingerprint(spec),'prepare_policy':policy(spec),
                           'prepare_instructions':prepare_instructions(spec)},ensure_ascii=False,indent=2))

@package_group.command(name='draft-create')
@click.option('--spec-file',type=click.Path(exists=True,dir_okay=False,path_type=str),required=True)
@click.option('--request-id',required=True)
def package_draft_create_command(spec_file,request_id):
    """Create an owner-bound draft in the private ChatTask store."""
    import asyncio,json
    from pathlib import Path
    from .settings import state_paths
    from .workflow import TaskStore
    from .agent_tasks import AgentTaskService
    from .package_service import PackageService
    try:
        spec=json.loads(Path(spec_file).read_text(encoding='utf-8'))
        store=TaskStore(state_paths()['state_dir'])
        result=asyncio.run(PackageService(store,AgentTaskService(store)).create('operator',{'request_id':request_id,'spec':spec}))
    except (ValueError,OSError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(json.dumps(result,ensure_ascii=False,indent=2))

@package_group.command(name='draft-show')
@click.option('--id','draft_id',required=True)
def package_draft_show_command(draft_id):
    """Read a draft and its verified preparation preview."""
    import json
    from .settings import state_paths
    from .workflow import TaskStore
    from .agent_tasks import AgentTaskService
    from .package_service import PackageService
    try:
        store=TaskStore(state_paths()['state_dir'])
        result=PackageService(store,AgentTaskService(store)).get('operator',draft_id)
    except (ValueError,KeyError,OSError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(json.dumps(result,ensure_ascii=False,indent=2))

async def _package_run(profile,draft_id,body,kind):
    from .settings import load_settings,state_paths
    from .workflow import TaskStore
    from .service import TaskService
    from .agent_tasks import AgentTaskService
    from .codex_tasks import configured_executor
    from .package_service import PackageService
    settings=load_settings(profile=profile)
    store=TaskStore(state_paths(home=settings.home)['state_dir'])
    sandbox=TaskService(settings)
    tasks=AgentTaskService(store,configured_executor(settings,sandbox))
    packages=PackageService(store,tasks,broker_required=True)
    try:
        result=await (packages.prepare('operator',draft_id,body) if kind=='prepare' else packages.confirm('operator',draft_id,body))
        task_id=result['prepare_task_id' if kind=='prepare' else 'publish_task_id']
        await tasks.wait(task_id)
        return packages.get('operator',draft_id)
    finally:
        await tasks.shutdown()
        await sandbox.shutdown()

@package_group.command(name='prepare')
@click.option('--id','draft_id',required=True)
@click.option('--revision',type=int,required=True)
@click.option('--image-profile',required=True)
@click.option('--request-id',required=True)
@click.option('-e','--env-profile',default=None)
def package_prepare_command(draft_id,revision,image_profile,request_id,env_profile):
    """Run and verify the read-only Sandbox preflight for a saved draft."""
    import asyncio,json
    try:
        result=asyncio.run(_package_run(env_profile,draft_id,{'request_id':request_id,'revision':revision,
            'image_profile':image_profile},'prepare'))
    except (ValueError,KeyError,OSError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(json.dumps(result,ensure_ascii=False,indent=2))

@package_group.command(name='confirm')
@click.option('--id','draft_id',required=True)
@click.option('--revision',type=int,required=True)
@click.option('--preview-sha256',required=True)
@click.option('--confirmation',required=True)
@click.option('--request-id',required=True)
@click.option('-e','--env-profile',default=None)
def package_confirm_command(draft_id,revision,preview_sha256,confirmation,request_id,env_profile):
    """Submit the exact verified preview and wait for its publish task."""
    import asyncio,json
    try:
        result=asyncio.run(_package_run(env_profile,draft_id,{'request_id':request_id,'revision':revision,
            'preview_sha256':preview_sha256,'confirmation':confirmation},'confirm'))
    except (ValueError,KeyError,OSError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(json.dumps(result,ensure_ascii=False,indent=2))

if __name__ == "__main__":
    main()
