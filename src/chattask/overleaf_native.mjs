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
import CompileManager from './app/src/Features/Compile/CompileManager.mjs'
import ClsiManager from './app/src/Features/Compile/ClsiManager.mjs'

const FRAME = '@@CHATTASK_RESEARCH_NATIVE_V1@@'
const ID = /^[0-9a-f]{32}$/
const OID = /^[0-9a-f]{24}$/
const FILE = /^[A-Za-z][A-Za-z0-9_-]{0,63}\.(tex|bib|md|json)$/
const MAX_PDF = 20 * 1024 * 1024

function fail(code) { throw Object.assign(new Error('research native operation failed'), { safeCode: code }) }
function hash(value) { return crypto.createHash('sha256').update(value).digest('hex') }
function asId(value) { if (typeof value !== 'string' || !OID.test(value)) fail('invalid-id'); return value }
function describe(taskId) { return `chattask-research:${taskId}` }
function projectName(taskId) { return `ChatTask Research ${taskId}` }

async function readInput() {
  let text = ''
  for await (const chunk of stdin) {
    text += chunk
    if (Buffer.byteLength(text) > 5 * 1024 * 1024) fail('request-too-large')
  }
  try { return JSON.parse(text) } catch { fail('invalid-json') }
}

function validate(request) {
  if (!request || typeof request !== 'object' || !['probe', 'publish', 'readback'].includes(request.action)) fail('invalid-action')
  if (request.action === 'probe') return
  if (typeof request.task_id !== 'string' || !ID.test(request.task_id) ||
      typeof request.run_id !== 'string' || !ID.test(request.run_id)) fail('invalid-id')
  if (request.project_id != null) asId(request.project_id)
  if (request.expected_root_doc_id != null) asId(request.expected_root_doc_id)
  if (request.action === 'readback') return
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
  if (typeof files['main.tex'] !== 'string' || !/\\documentclass(?:\[[^\]]*\])?\{ctexart\}/.test(files['main.tex']) ||
      !files['main.tex'].includes('\\begin{document}') || !files['main.tex'].includes('\\end{document}') ||
      /\\(?:input|include|includegraphics|bibliography|addbibresource|write18)\b/.test(files['main.tex'])) fail('invalid-main')
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

async function findProject(taskId, ownerId, boundId, create) {
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

async function pdfFromCompile(projectId, ownerId, result) {
  if (result.status !== 'success' || !result.buildId ||
      !result.outputFiles?.some(file => file.path === 'output.pdf')) fail('compile-failed')
  const userId = Settings.disablePerUserCompiles ? null : ownerId
  const stream = await ClsiManager.promises.getOutputFileStream(projectId, userId,
    result.clsiServerId, result.buildId, 'output.pdf')
  const chunks = []
  let size = 0
  for await (const chunk of stream) {
    size += chunk.length
    if (size > MAX_PDF) fail('pdf-too-large')
    chunks.push(chunk)
  }
  const pdf = Buffer.concat(chunks)
  if (pdf.length < 20 || !pdf.subarray(0, 5).equals(Buffer.from('%PDF-'))) fail('invalid-pdf')
  return pdf
}

async function execute(request) {
  validate(request)
  await mongoose.connectionPromise
  const ownerId = await owner()
  if (request.action === 'probe') return { status: 'ok', owner_verified: true }
  let project = await findProject(request.task_id, ownerId, request.project_id || null,
    request.action === 'publish')
  if (request.action === 'readback') {
    const names = Object.keys(request.file_sha256 || {})
    return { status: 'ok', project_id: String(project._id), owner_verified: true,
      root_doc_id: project.rootDoc_id ? String(project.rootDoc_id) : null,
      source_files: await getRoundFiles(project, request.run_id, names),
      provenance: 'original-service-document-readback' }
  }
  project = await appendDocuments(project, ownerId, request)
  const root = await switchRoot(project, ownerId, request)
  const projectId = String(project._id)
  const compile = await CompileManager.promises.compile(projectId, ownerId, {
    rootResourcePath: `r_${request.run_id}_main.tex`,
    rootDoc_id: root.rootId,
  })
  const pdf = await pdfFromCompile(projectId, ownerId, compile)
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
    compiler: 'xelatex', compile_status: compile.status, build_id: compile.buildId,
    source_files: sourceFiles, pdf_base64: pdf.toString('base64'),
    provenance: 'original-service-document-readback' }
}

// CompileManager's internal request timeout is 12 minutes. This independent
// bound leaves time to frame an unknown outcome and exit normally. Any write
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
