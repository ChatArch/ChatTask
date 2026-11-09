// Trusted operator-side script for the existing Overleaf web service.
// Run by DockerOverleafBridge from fixed packaged source. Request JSON is stdin.
// This is administrative scripting, not a browser or human-login session.
import crypto from 'node:crypto'
import { stdin, stdout } from 'node:process'
import Settings from '@overleaf/settings'
import mongoose from './app/src/infrastructure/Mongoose.mjs'
import { User } from './app/src/models/User.mjs'
import { Project } from './app/src/models/Project.mjs'
import ProjectCreationHandler from './app/src/Features/Project/ProjectCreationHandler.mjs'
import ProjectEntityUpdateHandler from './app/src/Features/Project/ProjectEntityUpdateHandler.mjs'
import DocumentUpdaterHandler from './app/src/Features/DocumentUpdater/DocumentUpdaterHandler.mjs'

const FRAME = '@@CHATTASK_RESEARCH_NATIVE_V1@@'
const ID = /^[0-9a-f]{32}$/
const OID = /^[0-9a-f]{24}$/
const FILE = /^[A-Za-z][A-Za-z0-9_-]{0,63}\.(tex|bib|md|json)$/
const TEX_COMMANDS = new Set(['author', 'begin', 'bibitem', 'cite', 'date', 'documentclass',
  'emph', 'end', 'hline', 'item', 'maketitle', 'section', 'subsection', 'setlength',
  'small', 'tabcolsep', 'texttt', 'title', 'url', 'usepackage', '_', '\\'])
const TEX_ENVIRONMENTS = new Set(['document', 'abstract', 'enumerate', 'thebibliography', 'longtable'])
const TEX_PACKAGES = new Set(['geometry', 'url', 'longtable'])

function fail(code) { throw Object.assign(new Error('research native operation failed'), { safeCode: code }) }
function hash(value) { return crypto.createHash('sha256').update(value).digest('hex') }
function asId(value) { if (typeof value !== 'string' || !OID.test(value)) fail('invalid-id'); return value }
function describe(taskId) { return `chattask-research:${taskId}` }
function projectName(taskId) { return `ChatTask Research ${taskId}` }

function validateTex(main) {
  // Strict first-slice syntax. This filter is not a claim of TeX engine isolation.
  if (main.includes('^^') || /[%#$\r]/.test(main)) fail('unsafe-tex-syntax')
  const tokens = [...main.matchAll(/\\([A-Za-z@]+|[\s\S])/g)].map(match => match[1])
  if (tokens.some(token => !TEX_COMMANDS.has(token))) fail('unsupported-tex-command')
  if (!/^\s*\\documentclass(?:\[UTF8,11pt,a4paper\])?\{ctexart\}/.test(main) ||
      tokens.filter(token => token === 'documentclass').length !== 1) fail('invalid-main')
  const packages = [...main.matchAll(/\\usepackage(?:\[([^\]\n]*)\])?\{([^{}\n]+)\}/g)]
  if (packages.length !== tokens.filter(token => token === 'usepackage').length) fail('invalid-package')
  for (const match of packages) {
    const [, options, name] = match
    if (!TEX_PACKAGES.has(name) || (options !== undefined &&
        (name !== 'geometry' || !/^margin=[1-9][0-9]{0,2}(?:mm|cm|in)$/.test(options)))) fail('invalid-package')
  }
  const urls = [...main.matchAll(/\\url\{([^{}\\\n]+)\}/g)]
  if (urls.length !== tokens.filter(token => token === 'url').length) fail('invalid-url')
  for (const match of urls) {
    let url
    try { url = new URL(match[1]) } catch { fail('invalid-url') }
    if (!['http:', 'https:'].includes(url.protocol) || !url.hostname || url.username || url.password) fail('invalid-url')
  }
  const lengths = [...main.matchAll(/\\setlength\{\\tabcolsep\}\{[1-9][0-9]?pt\}/g)]
  if (lengths.length !== tokens.filter(token => token === 'setlength').length) fail('invalid-length')
  let depth = 0
  for (const char of main) {
    if (char === '{') depth++
    if (char === '}' && --depth < 0) fail('unbalanced-braces')
  }
  if (depth !== 0) fail('unbalanced-braces')
  const environments = [...main.matchAll(/\\(begin|end)\{([^{}\n]+)\}/g)]
  if (environments.length !== tokens.filter(token => token === 'begin' || token === 'end').length) fail('invalid-environment')
  const stack = []
  for (const match of environments) {
    const [, kind, name] = match
    if (!TEX_ENVIRONMENTS.has(name)) fail('invalid-environment')
    if (kind === 'begin') {
      if ((!stack.length && name !== 'document') || (stack.length && name === 'document')) fail('invalid-environment')
      stack.push(name)
    } else if (!stack.length || stack.pop() !== name) fail('unbalanced-environment')
  }
  if (stack.length || !environments.length || environments[0][1] !== 'begin' ||
      environments[0][2] !== 'document') fail('invalid-main')
  const lastEnvironment = environments[environments.length - 1]
  if (lastEnvironment[1] !== 'end' || lastEnvironment[2] !== 'document' ||
      main.slice(lastEnvironment.index + lastEnvironment[0].length).trim()) fail('invalid-main')
}

async function readInput() {
  let text = ''
  for await (const chunk of stdin) {
    text += chunk
    if (Buffer.byteLength(text) > 5 * 1024 * 1024) fail('request-too-large')
  }
  try { return JSON.parse(text) } catch { fail('invalid-json') }
}

function validate(request) {
  if (!request || typeof request !== 'object' || !['probe', 'publish_source', 'readback'].includes(request.action)) fail('invalid-action')
  if (request.action === 'probe') return
  if (typeof request.task_id !== 'string' || !ID.test(request.task_id) ||
      typeof request.run_id !== 'string' || !ID.test(request.run_id)) fail('invalid-id')
  if (request.project_id != null) asId(request.project_id)
  if (request.expected_root_doc_id != null) asId(request.expected_root_doc_id)
  if (request.action === 'readback') {
    const hashes = request.file_sha256
    if (!hashes || typeof hashes !== 'object' || Array.isArray(hashes) ||
        Object.keys(hashes).length < 1 || Object.keys(hashes).length > 20 ||
        !Object.hasOwn(hashes, 'main.tex')) fail('invalid-readback')
    for (const [name, digest] of Object.entries(hashes)) {
      if (!FILE.test(name) || typeof digest !== 'string' || !/^[0-9a-f]{64}$/.test(digest)) fail('invalid-readback')
    }
    return
  }
  const files = request.files
  if (!files || typeof files !== 'object' || Array.isArray(files) ||
      Object.keys(files).length < 1 || Object.keys(files).length > 20) fail('invalid-files')
  let total = 0
  for (const [name, body] of Object.entries(files)) {
    if (!FILE.test(name) || /(auth|token|secret|password|credential|cookie|session|config|settings)/i.test(name) ||
        typeof body !== 'string' || body.includes('\0')) fail('invalid-files')
    const length = Buffer.byteLength(body, 'utf8')
    if (length > 1024 * 1024) fail('file-too-large')
    total += length
    if (request.file_sha256?.[name] !== hash(Buffer.from(body))) fail('digest-mismatch')
  }
  if (total > 4 * 1024 * 1024) fail('files-too-large')
  if (typeof files['main.tex'] !== 'string') fail('invalid-main')
  validateTex(files['main.tex'])
  if (files['references.bib'] != null && !files['main.tex'].includes('\\begin{thebibliography}')) fail('invalid-main')
}

async function owner() {
  // The configured admin email selects exactly one existing native admin user.
  const email = Settings.adminEmail
  if (typeof email !== 'string' || !email) fail('owner-unconfigured')
  const users = await User.find({ email, isAdmin: true }, { _id: 1 }).limit(2).lean().exec()
  if (users.length !== 1) fail('owner-not-unique')
  return users[0]._id
}

async function findProject(taskId, ownerId, boundId, create, allowMissing = false) {
  const name = projectName(taskId)
  const projects = await Project.find({ name, owner_ref: ownerId },
    { _id: 1, name: 1, description: 1, owner_ref: 1, compiler: 1,
      publicAccesLevel: 1, rootDoc_id: 1, rootFolder: 1 }).limit(2).exec()
  if (projects.length > 1) fail('project-name-conflict')
  if (projects.length === 1) {
    const project = projects[0]
    if (project.description !== describe(taskId) ||
        project.publicAccesLevel !== 'private' || project.compiler !== 'xelatex' ||
        (boundId && String(project._id) !== boundId)) fail('project-binding-conflict')
    return project
  }
  if (!create && allowMissing) return null
  if (boundId || !create) fail('project-missing')
  const created = await ProjectCreationHandler.promises.createBlankProject(ownerId, name, {
    compiler: 'xelatex', publicAccesLevel: 'private', description: describe(taskId),
  })
  // Re-read by the same unique owner/name binding. A concurrent creator means
  // conflict, and the operator should inspect the two projects before retry.
  return await findProject(taskId, ownerId, String(created._id), false)
}

function findFlatDoc(project, name) {
  const docs = (project.rootFolder?.[0]?.docs || []).filter(doc => doc.name === name)
  if (docs.length > 1) fail('duplicate-document')
  return docs[0] || null
}

async function contents(projectId, docId) {
  // Docstore may lag a collaborator's live edits. The document updater is the
  // original service's current source of truth for conflict and receipt data.
  const result = await DocumentUpdaterHandler.promises.getDocument(projectId, docId, -1)
  if (!Array.isArray(result.lines) || result.lines.some(line => typeof line !== 'string')) fail('invalid-document-readback')
  return result.lines.join('\n')
}

async function getRoundFiles(project, runId, requested) {
  const sourceFiles = {}
  for (const name of requested) {
    const nativeName = `r_${runId}_${name}`
    const doc = findFlatDoc(project, nativeName)
    if (!doc) fail('document-missing')
    sourceFiles[nativeName] = await contents(String(project._id), String(doc._id))
  }
  return sourceFiles
}

async function appendDocuments(project, ownerId, request) {
  const projectId = String(project._id)
  for (const [name, content] of Object.entries(request.files)) {
    const nativeName = `r_${request.run_id}_${name}`
    // Recheck the native project on every file to reconcile partial previous
    // attempts without overwriting any existing document.
    project = await Project.findById(projectId, { rootFolder: 1, rootDoc_id: 1, owner_ref: 1,
      name: 1, description: 1, compiler: 1, publicAccesLevel: 1 }).exec()
    if (!project || String(project.owner_ref) !== String(ownerId) ||
        project.description !== describe(request.task_id)) fail('project-binding-conflict')
    const existing = findFlatDoc(project, nativeName)
    if (existing) {
      if (hash(Buffer.from(await contents(projectId, String(existing._id)))) !== request.file_sha256[name]) fail('document-conflict')
      continue
    }
    await ProjectEntityUpdateHandler.promises.addDoc(projectId, project.rootFolder[0]._id,
      nativeName, content.split('\n'), ownerId, 'chattask-research')
  }
  return await Project.findById(projectId, { rootFolder: 1, rootDoc_id: 1,
    owner_ref: 1, name: 1, description: 1, compiler: 1, publicAccesLevel: 1 }).exec()
}

async function switchRoot(project, ownerId, request) {
  const rootName = `r_${request.run_id}_main.tex`
  const doc = findFlatDoc(project, rootName)
  if (!doc) fail('document-missing')
  const rootId = String(doc._id)
  const current = project.rootDoc_id ? String(project.rootDoc_id) : null
  const expected = request.expected_root_doc_id || null
  if (current === rootId) return { rootId, previous: expected }
  if (current !== expected) fail('root-conflict')
  // The pointer is the only direct metadata update. Document mutations above
  // always use upstream business handlers. The root CAS preserves collaborators.
  const update = await Project.updateOne({ _id: project._id, owner_ref: ownerId,
    description: describe(request.task_id), rootDoc_id: expected },
  { $set: { rootDoc_id: doc._id } }).exec()
  if (update.matchedCount !== 1) fail('root-conflict')
  return { rootId, previous: current }
}

async function execute(request) {
  validate(request)
  await mongoose.connectionPromise
  const ownerId = await owner()
  if (request.action === 'probe') return { status: 'ok', owner_verified: true }
  let project = await findProject(request.task_id, ownerId, request.project_id || null,
    request.action === 'publish_source', request.action === 'readback')
  if (request.action === 'readback') {
    const names = Object.keys(request.file_sha256 || {})
    if (!project) return { status: 'ok', project_exists: false, owner_verified: true,
      source_files: {}, missing_files: names, root_doc_id: null, round_root_doc_id: null,
      provenance: 'original-service-document-readback' }
    const sourceFiles = {}
    const missingFiles = []
    for (const name of names) {
      const doc = findFlatDoc(project, `r_${request.run_id}_${name}`)
      if (doc) sourceFiles[name] = await contents(String(project._id), String(doc._id))
      else missingFiles.push(name)
    }
    const root = findFlatDoc(project, `r_${request.run_id}_main.tex`)
    return { status: 'ok', project_exists: true, project_id: String(project._id),
      owner_verified: true, root_doc_id: project.rootDoc_id ? String(project.rootDoc_id) : null,
      round_root_doc_id: root ? String(root._id) : null, source_files: sourceFiles,
      missing_files: missingFiles, provenance: 'original-service-document-readback' }
  }
  project = await appendDocuments(project, ownerId, request)
  const root = await switchRoot(project, ownerId, request)
  const projectId = String(project._id)
  project = await Project.findById(projectId, { rootDoc_id: 1, rootFolder: 1,
    owner_ref: 1, description: 1, compiler: 1, publicAccesLevel: 1 }).exec()
  if (!project || String(project.rootDoc_id) !== root.rootId ||
      String(project.owner_ref) !== String(ownerId) ||
      project.description !== describe(request.task_id)) fail('root-conflict')
  const sourceFiles = await getRoundFiles(project, request.run_id, Object.keys(request.files))
  for (const [name, content] of Object.entries(request.files)) {
    if (sourceFiles[`r_${request.run_id}_${name}`] !== content) fail('document-conflict')
  }
  return { status: 'ok', project_id: projectId, owner_verified: true,
    root_doc_id: root.rootId, previous_root_doc_id: root.previous,
    compiler: 'xelatex', source_files: sourceFiles,
    provenance: 'original-service-document-readback' }
}

// This bound leaves time to frame an unknown outcome and exit normally. Any write
// may already have happened; the Python journal must remain pending.
const NATIVE_DEADLINE_MS = 13 * 60 * 1000
let deadlineTimer
let response
try {
  response = await Promise.race([
    (async () => execute(await readInput()))(),
    new Promise(resolve => {
      deadlineTimer = setTimeout(() => resolve({ status: 'unknown', code: 'native-deadline' }), NATIVE_DEADLINE_MS)
    }),
  ])
} catch (error) {
  const code = error?.safeCode || 'native-operation-unknown'
  response = { status: code.endsWith('conflict') ? 'conflict' : 'error', code }
} finally {
  clearTimeout(deadlineTimer)
}
stdout.write(`${FRAME}${JSON.stringify(response)}\n`, () => process.exit(0))
