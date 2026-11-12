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


@task_group.command(name='submit')
@click.argument('instructions',required=False)
@click.option('--instructions-file',type=click.Path(exists=True,dir_okay=False,path_type=str))
@click.option('--template',type=click.Choice(['research-report','python-package']),default=None)
@click.option('--parameters-file',type=click.Path(exists=True,dir_okay=False,path_type=str))
@click.option('--image-profile',default=None)
@click.option('--request-id',required=True,help='Stable originating message ID; reuse unchanged after an uncertain response.')
@click.option('-e','--env-profile',default=None)
def task_submit(instructions,instructions_file,template,parameters_file,image_profile,request_id,env_profile):
    """Submit natural language over authenticated HTTP and return its Web URL."""
    import json
    from pathlib import Path
    from .intake import submit_task,IntakeUnknown
    from .settings import load_settings
    body={'request_id':request_id}
    try:
        if template:
            if instructions or instructions_file or not parameters_file:raise ValueError('templates require --parameters-file, not free instructions')
            body.update(template_id=template,template_revision=1,parameters=json.loads(Path(parameters_file).read_text()))
        else:
            if parameters_file or bool(instructions)==bool(instructions_file):raise ValueError('provide instructions or --instructions-file, exactly one')
            body['instructions']=Path(instructions_file).read_text() if instructions_file else instructions
        if image_profile:body['image_profile']=image_profile
        result=submit_task(load_settings(profile=env_profile),body)
    except (ValueError,OSError,IntakeUnknown) as exc:raise click.ClickException(str(exc)) from None
    click.echo(json.dumps(result,ensure_ascii=False))


@task_group.command(name='create')
@click.argument('instructions', required=False)
@click.option('--definition-dir', type=click.Path(exists=True, file_okay=False, path_type=str), help='Import a bounded UTF-8 task definition directory.')
@click.option('--image-profile', required=True)
@click.option('--workflow', type=click.Choice(['development','research']), default='development', show_default=True)
@click.option('--sources-file', type=click.Path(exists=True, dir_okay=False, path_type=str), help='JSON array of caller-verified research source records.')
@click.option('--request-id', required=True)
@click.option('--owner', required=True, help='Trusted local principal identity.')
@click.option('-e', '--env-profile', default=None)
def durable_create(instructions, definition_dir, image_profile, workflow, sources_file, request_id, owner, env_profile):
    """Queue the first round of a long-term Task."""
    import json
    from pathlib import Path
    definition = None
    if definition_dir:
        from .task_packages import read_definition_directory
        try:definition=read_definition_directory(definition_dir)
        except (OSError,ValueError) as exc:raise click.ClickException(str(exc)) from exc
        if instructions is None:instructions=definition['instruction.md']
    if not instructions:
        raise click.UsageError('Provide instructions or --definition-dir.')
    body={'instructions': instructions, 'image_profile': image_profile, 'request_id': request_id, 'workflow': workflow}
    if definition is not None:body['definition']=definition
    if sources_file:
        try:body['source_records']=json.loads(Path(sources_file).read_text(encoding='utf-8'))
        except (OSError,ValueError) as exc:raise click.ClickException('invalid research sources JSON file') from exc
    _durable_call('create', env_profile, owner, body)


def _task_actor_options(function):
    function=click.option('-e','--env-profile',default=None)(function)
    return click.option('--owner',required=True,help='Trusted local principal identity.')(function)


@task_group.group(name='package')
def task_package_group():
    """Initialize, validate, version, export and reuse portable task definitions."""


@task_package_group.command(name='init')
@click.argument('directory',type=click.Path(path_type=str))
@click.argument('instructions')
@click.option('--image',required=True,help='Image reference for the draft, not permission to pull or execute it.')
def task_package_init(directory,instructions,image):
    """Create a new task.toml, instruction.md and verifier draft without model calls."""
    import json
    from .task_packages import initialize_definition_directory
    try:result=initialize_definition_directory(directory,instructions,image)
    except (OSError,ValueError) as exc:raise click.ClickException(str(exc)) from exc
    click.echo(json.dumps(result,ensure_ascii=False))


@task_package_group.command(name='validate')
@click.argument('directory',type=click.Path(exists=True,file_okay=False,path_type=str))
@click.option('--official',is_flag=True,help='Additionally require an installed Harbor 0.25.0 schema validator.')
def task_package_validate(directory,official):
    """Validate the supported file contract; does not execute a task or verifier."""
    import json
    from .task_packages import read_definition_directory,validate_files,summary,tomllib
    try:
        files=read_definition_directory(directory);result=summary(validate_files(files))
        result['official_schema_checked']=False
        if official:
            from importlib.metadata import version
            from harbor.models.task.config import TaskConfig
            if version('harbor')!='0.25.0':raise ValueError('official validation is pinned to Harbor 0.25.0')
            TaskConfig.model_validate(tomllib.loads(files['task.toml']))
            result['official_schema_checked']=True
    except ImportError as exc:
        raise click.ClickException('Optional Harbor 0.25.0 validator is not installed; normal validation needs no framework install.') from exc
    except (OSError,ValueError) as exc:raise click.ClickException(str(exc)) from exc
    click.echo(json.dumps(result,ensure_ascii=False))


@task_package_group.command(name='show')
@click.argument('task_id')
@click.option('--revision',default=None)
@_task_actor_options
def task_package_show(task_id,revision,owner,env_profile):
    """Read the selected immutable definition visible to this principal."""
    _durable_call('package',env_profile,owner,task_id,revision)


@task_package_group.command(name='revise')
@click.argument('task_id')
@click.option('--definition-dir',required=True,type=click.Path(exists=True,file_okay=False,path_type=str))
@click.option('--expected-revision',required=True)
@click.option('--request-id',required=True)
@_task_actor_options
def task_package_revise(task_id,definition_dir,expected_revision,request_id,owner,env_profile):
    """Publish a CAS-checked definition revision without rewriting old rounds."""
    from .task_packages import read_definition_directory
    try:files=read_definition_directory(definition_dir)
    except (OSError,ValueError) as exc:raise click.ClickException(str(exc)) from exc
    _durable_call('revise_package',env_profile,owner,task_id,
                  {'request_id':request_id,'expected_revision':expected_revision,'definition':files})


@task_package_group.command(name='export')
@click.argument('task_id')
@click.option('--revision',default=None)
@click.option('--output',required=True,type=click.Path(path_type=str),help='New private ZIP file; existing files are never overwritten.')
@_task_actor_options
def task_package_export(task_id,revision,output,owner,env_profile):
    """Export a real definition ZIP, excluding session state and credentials."""
    import hashlib,json,os
    from pathlib import Path
    try:
        data=_durable_service(env_profile).export_package(owner,task_id,revision)
        target=Path(output)
        fd=os.open(target,os.O_CREAT|os.O_EXCL|os.O_WRONLY|getattr(os,'O_NOFOLLOW',0),0o600)
        with os.fdopen(fd,'wb') as stream:stream.write(data)
    except (KeyError,ValueError,OSError) as exc:raise click.ClickException(str(exc)) from exc
    click.echo(json.dumps({'file':str(target),'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()}))


@task_package_group.command(name='reuse')
@click.argument('task_id')
@click.option('--request-id',required=True)
@_task_actor_options
def task_package_reuse(task_id,request_id,owner,env_profile):
    """Create and queue a new private Task without inheriting review or grants."""
    _durable_call('reuse_package',env_profile,owner,task_id,{'request_id':request_id})


@task_group.command(name='evaluate')
@click.argument('task_id')
@click.argument('run_id')
@click.option('--request-id',required=True)
@_task_actor_options
def task_evaluate(task_id,run_id,request_id,owner,env_profile):
    """Request independent verification; unavailable is never a passing score."""
    import asyncio,json
    try:result=asyncio.run(_durable_service(env_profile).evaluate_package(owner,task_id,run_id,{'request_id':request_id}))
    except (KeyError,ValueError,OSError) as exc:raise click.ClickException(str(exc)) from exc
    click.echo(json.dumps(result,ensure_ascii=False))


@task_group.command(name='evaluations')
@click.argument('task_id')
@click.argument('run_id')
@_task_actor_options
def task_evaluations(task_id,run_id,owner,env_profile):
    """Read persisted evaluation history for one exact round."""
    _durable_call('evaluations',env_profile,owner,task_id,run_id)


@task_group.command(name='review')
@click.argument('task_id')
@click.argument('run_id')
@click.option('--decision',required=True,type=click.Choice(['accepted','changes_requested']))
@click.option('--expected-revision',required=True)
@click.option('--criteria-file',required=True,type=click.Path(exists=True,dir_okay=False,path_type=str),help='JSON object mapping criterion IDs to explicit boolean decisions.')
@click.option('--notes',required=True)
@click.option('--request-id',required=True)
@_task_actor_options
def task_review(task_id,run_id,decision,expected_revision,criteria_file,notes,request_id,owner,env_profile):
    """Record an explicit owner acceptance decision and its evidence notes."""
    import json
    from pathlib import Path
    try:criteria=json.loads(Path(criteria_file).read_text(encoding='utf-8'))
    except (OSError,ValueError) as exc:raise click.ClickException('invalid criterion decisions JSON') from exc
    _durable_call('review_package',env_profile,owner,task_id,{'request_id':request_id,'run_id':run_id,
                  'decision':decision,'expected_revision':expected_revision,'criteria':criteria,'notes':notes})


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
