#!/usr/bin/env python3
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path.cwd()


def p(*parts: str) -> Path:
    return ROOT.joinpath(*parts)


def read(path: Path) -> str:
    return path.read_text(encoding='utf-8')


def write(path: Path, data: str) -> None:
    path.write_text(data, encoding='utf-8')


def require(path: Path) -> None:
    if not path.exists():
        raise SystemExit(f'Missing {path}. Run this from the Jan repository root after applying stages 1-6.')


def replace_once(path: Path, old: str, new: str, label: str) -> None:
    s = read(path)
    if new.strip()[:120] in s:
        return
    if old not in s:
        raise SystemExit(f'Could not find target for {label} in {path}')
    write(path, s.replace(old, new, 1))


def insert_after(path: Path, needle: str, insertion: str, label: str, marker = None) -> None:
    s = read(path)
    if (marker and marker in s) or insertion.strip()[:120] in s:
        return
    idx = s.find(needle)
    if idx < 0:
        raise SystemExit(f'Could not find insertion point for {label} in {path}')
    idx += len(needle)
    write(path, s[:idx] + insertion + s[idx:])


def replace_regex(path: Path, pattern: str, repl: str, label: str, flags: int = re.S) -> None:
    s = read(path)
    # Use a callable replacement so literal backslashes in TypeScript/Rust regex
    # strings (for example \s or \-) are not interpreted by Python's re
    # replacement-template parser. Without this, Python 3.9 raises
    # `re.error: bad escape \s` on valid source text. Humanity loses another
    # afternoon to escaping rules, as tradition demands.
    new, n = re.subn(pattern, lambda _m: repl, s, count=1, flags=flags)
    if n == 0:
        if repl.strip()[:120] in s:
            return
        raise SystemExit(f'Could not find regex target for {label} in {path}')
    write(path, new)


required = [
    p('extensions/llamacpp-extension/settings.json'),
    p('extensions/llamacpp-extension/src/util.ts'),
    p('extensions/llamacpp-extension/src/index.ts'),
    p('extensions/rag-extension/src/index.ts'),
    p('src-tauri/src/core/server/proxy.rs'),
    p('src-tauri/static/openapi.json'),
]
for path in required:
    require(path)

# 3. Turn the default reranker setting into a visible dropdown whose options are refreshed from installed rerankers.
settings_path = p('extensions/llamacpp-extension/settings.json')
settings = json.loads(read(settings_path))
for item in settings:
    if item.get('key') == 'default_reranker_model':
        item['controllerType'] = 'dropdown'
        item['description'] = 'Local reranker model to use for /v1/rerank and automatic RAG reranking. Auto selects an installed local reranker when available.'
        item['controllerProps'] = {
            'value': item.get('controllerProps', {}).get('value', 'auto') or 'auto',
            'options': [{'name': 'Auto (recommended)', 'value': 'auto'}],
            'recommended': 'auto',
        }
        break
else:
    insert_idx = next((i + 1 for i, it in enumerate(settings) if it.get('key') == 'models_max'), len(settings))
    settings.insert(insert_idx, {
        'key': 'default_reranker_model',
        'title': 'Default reranker model',
        'description': 'Local reranker model to use for /v1/rerank and automatic RAG reranking. Auto selects an installed local reranker when available.',
        'controllerType': 'dropdown',
        'controllerProps': {
            'value': 'auto',
            'options': [{'name': 'Auto (recommended)', 'value': 'auto'}],
            'recommended': 'auto',
        },
    })
write(settings_path, json.dumps(settings, indent=2) + '\n')

# 6. Tighten reranker detection with more real-world metadata/task signals.
util_path = p('extensions/llamacpp-extension/src/util.ts')
replace_regex(
    util_path,
    r"const RERANKING_NAME_RE =\n\s+/\(\^\|\[\\s\._\\-/\]\).*?\n",
    "const RERANKING_NAME_RE =\n  /(^|[\\s._\\-/])(?:rerank|reranker|re-ranker|ranking|text[\\s._\\-]?ranking|cross[\\s._\\-]?encoder|crossencoder|bge[\\s._\\-]?reranker|jina[\\s._\\-]?reranker|qwen3[\\s._\\-]?reranker|mxbai[\\s._\\-]?rerank|mixedbread[\\s._\\-]?rerank|gte[\\s._\\-]?rerank)([\\s._\\-/]|$)/i\n\nconst RERANKING_TASK_RE =\n  /(^|[\\s._\\-/])(?:rerank|reranking|re-ranking|rank|ranking|text[\\s._\\-]?ranking|cross[\\s._\\-]?encoder|crossencoder)([\\s._\\-/]|$)/i\n",
    'broaden reranker regex'
)
replace_once(
    util_path,
    "  const keys = [\n    'general.name',\n    'general.basename',\n    'general.description',\n    'general.source.url',\n    'general.url',\n    'general.repo_url',\n    'tokenizer.ggml.model',\n  ]\n",
    "  const keys = [\n    'general.name',\n    'general.basename',\n    'general.description',\n    'general.tags',\n    'general.datasets',\n    'general.source.url',\n    'general.url',\n    'general.repo_url',\n    'tokenizer.ggml.model',\n    'pipeline_tag',\n    'task',\n    'tasks',\n    'sentence_transformers.task',\n    'sentence_transformers.model_type',\n    'sentence_transformers.modules',\n  ]\n",
    'reranker metadata haystack keys'
)
replace_once(
    util_path,
    "export function detectRerankingFromGgufMeta(\n  meta: Record<string, unknown> | undefined,\n  modelId: string = ''\n): boolean {\n  const haystack = metaStringHaystack(meta, modelId)\n  if (RERANKING_NAME_RE.test(haystack)) return true\n  if (hasExplicitRankPooling(meta)) return true\n  return false\n}\n",
    "export function detectRerankingFromGgufMeta(\n  meta: Record<string, unknown> | undefined,\n  modelId: string = ''\n): boolean {\n  const haystack = metaStringHaystack(meta, modelId)\n  if (RERANKING_NAME_RE.test(haystack)) return true\n\n  const taskHaystack = [\n    meta?.['pipeline_tag'],\n    meta?.['task'],\n    meta?.['tasks'],\n    meta?.['sentence_transformers.task'],\n    meta?.['sentence_transformers.model_type'],\n    meta?.['sentence_transformers.modules'],\n  ]\n    .map((value) =>\n      Array.isArray(value) ? value.join(' ') : asMetaString(value)\n    )\n    .filter(Boolean)\n    .join(' ')\n  if (RERANKING_TASK_RE.test(taskHaystack)) return true\n\n  if (hasExplicitRankPooling(meta)) return true\n  return false\n}\n",
    'reranker detection task metadata'
)

# 3, 8, 9. Refresh the dropdown dynamically and normalize/validate internal rerank requests.
index_path = p('extensions/llamacpp-extension/src/index.ts')
insert_after(
    index_path,
    "    await this.migratePersistedModelSettingsToYaml()\n",
    "\n    await this.refreshDefaultRerankerModelOptions().catch((e) =>\n      logger.warn('Failed to refresh reranker model options:', e)\n    )\n",
    'refresh reranker dropdown on load',
    'refreshDefaultRerankerModelOptions().catch'
)
insert_after(
    index_path,
    "    events.emit(AppEvent.onModelImported, {\n      modelId,\n      modelPath,\n      mmprojPath,\n      size_bytes,\n      model_sha256: opts.modelSha256,\n      model_size_bytes: opts.modelSize,\n      mmproj_sha256: opts.mmprojSha256,\n      mmproj_size_bytes: opts.mmprojSize,\n      embedding: isEmbedding,\n    })\n",
    "\n    await this.refreshDefaultRerankerModelOptions().catch((e) =>\n      logger.warn('Failed to refresh reranker model options after import:', e)\n    )\n",
    'refresh reranker dropdown after import',
    'refresh reranker model options after import'
)
insert_after(
    index_path,
    "  private configuredDefaultRerankingModelId(): string | undefined {\n    const value = (this.config as LlamacppConfig & {\n      default_reranker_model?: string\n    })?.default_reranker_model\n    if (typeof value !== 'string') return undefined\n    const trimmed = value.trim()\n    if (!trimmed || trimmed === 'auto' || trimmed === '*') return undefined\n    return trimmed\n  }\n",
    r'''

  private async refreshDefaultRerankerModelOptions(): Promise<void> {
    const settings = await this.getSettings()
    const idx = settings.findIndex((s) => s.key === 'default_reranker_model')
    if (idx < 0) return

    const models = await this.installedRerankingModels().catch(() => [])
    const options = [
      { name: 'Auto (recommended)', value: 'auto' },
      ...models.map((m) => ({ name: m.name || m.id, value: m.id })),
    ]
    const allowed = new Set(options.map((o) => o.value))
    const current = String(
      settings[idx].controllerProps?.value ??
        (this.config as LlamacppConfig & { default_reranker_model?: string })
          ?.default_reranker_model ??
        'auto'
    )
    const value = allowed.has(current) ? current : 'auto'

    const nextSetting = {
      ...settings[idx],
      controllerType: 'dropdown',
      controllerProps: {
        ...settings[idx].controllerProps,
        value,
        options,
        recommended: 'auto',
      },
    }

    await this.updateSettings(
      settings.map((setting, settingIdx) =>
        settingIdx === idx ? nextSetting : setting
      )
    )
    ;(this.config as LlamacppConfig & { default_reranker_model?: string }).default_reranker_model = value
  }

  private extractRerankDocumentText(document: unknown, index: number): string {
    if (typeof document === 'string') return document
    if (!document || typeof document !== 'object') {
      throw new Error(`documents[${index}] must be a string or an object with a text/content field`)
    }

    const obj = document as Record<string, unknown>
    for (const key of ['text', 'content']) {
      if (typeof obj[key] === 'string') return obj[key] as string
    }

    const nested = obj.document
    if (typeof nested === 'string') return nested
    if (nested && typeof nested === 'object') {
      const nestedObj = nested as Record<string, unknown>
      for (const key of ['text', 'content']) {
        if (typeof nestedObj[key] === 'string') return nestedObj[key] as string
      }
    }

    throw new Error(`documents[${index}] must contain a string text/content/document field`)
  }

  private normalizeRerankRequest(req: RerankRequest): RerankRequest & { documents: string[] } {
    if (!req || typeof req.query !== 'string' || req.query.trim().length === 0) {
      throw new Error('rerank requires a non-empty query string')
    }

    const rawDocuments = Array.isArray((req as any).documents)
      ? (req as any).documents
      : Array.isArray((req as any).texts)
        ? (req as any).texts
        : undefined

    if (!Array.isArray(rawDocuments) || rawDocuments.length === 0) {
      throw new Error('rerank requires a non-empty documents or texts array')
    }

    const documents = rawDocuments.map((document, index) =>
      this.extractRerankDocumentText(document, index)
    )
    const topN = (req as any).top_n ?? (req as any).top_k
    if (topN !== undefined) {
      const parsedTopN = Number(topN)
      if (!Number.isInteger(parsedTopN) || parsedTopN < 1 || parsedTopN > documents.length) {
        throw new Error(`top_n/top_k must be an integer between 1 and ${documents.length}`)
      }
      return { ...(req as any), top_n: parsedTopN, documents }
    }

    return { ...(req as any), documents }
  }
''',
    'reranker dropdown refresh + request normalization',
    'normalizeRerankRequest(req: RerankRequest)'
)
replace_once(
    index_path,
    "  async rerank(req: RerankRequest): Promise<RerankResponse> {\n    if (!req || typeof req.query !== 'string' || req.query.trim().length === 0) {\n      throw new Error('rerank requires a non-empty query string')\n    }\n\n    const documents = req.documents ?? req.texts\n    if (!Array.isArray(documents) || documents.length === 0) {\n      throw new Error('rerank requires a non-empty documents or texts array')\n    }\n\n    const installedReranking = await this.installedRerankingModels()\n",
    "  async rerank(req: RerankRequest): Promise<RerankResponse> {\n    const normalized = this.normalizeRerankRequest(req)\n    const documents = normalized.documents\n\n    const installedReranking = await this.installedRerankingModels()\n",
    'use normalized rerank request'
)
replace_once(
    index_path,
    "    const body = JSON.stringify({\n      ...req,\n      model: targetModelId,\n      documents,\n    })\n",
    "    const body = JSON.stringify({\n      ...normalized,\n      model: targetModelId,\n      documents,\n    })\n",
    'send normalized rerank request'
)

# 8, 9. Normalize /v1/rerank requests and return structured JSON errors in the Rust proxy.
proxy_path = p('src-tauri/src/core/server/proxy.rs')
replace_once(
    proxy_path,
    "fn ensure_rerank_model_in_body(\n    json_body: &mut serde_json::Value,\n    jan_data_folder: &str,\n) -> Result<String, String> {\n    let requested = json_body.get(\"model\").and_then(|v| v.as_str()).map(str::trim);\n    let needs_auto = requested.is_none() || requested == Some(\"\") || requested == Some(\"auto\");\n    let model = if needs_auto {\n        find_default_reranking_model_id(jan_data_folder).ok_or_else(|| {\n            \"No local reranking model is available. Import a GGUF reranker or mark a model.yml with reranking: true and pooling: rank.\".to_string()\n        })?\n    } else {\n        requested.unwrap().to_string()\n    };\n\n    json_body[\"model\"] = serde_json::Value::String(model.clone());\n    Ok(model)\n}\n",
    r'''type RerankProxyError = (StatusCode, &'static str, String);

fn extract_rerank_document_text(value: &serde_json::Value) -> Option<String> {
    if let Some(s) = value.as_str() {
        return Some(s.to_string());
    }
    let obj = value.as_object()?;
    for key in ["text", "content"] {
        if let Some(s) = obj.get(key).and_then(|v| v.as_str()) {
            return Some(s.to_string());
        }
    }
    if let Some(document) = obj.get("document") {
        if let Some(s) = document.as_str() {
            return Some(s.to_string());
        }
        if let Some(nested) = document.as_object() {
            for key in ["text", "content"] {
                if let Some(s) = nested.get(key).and_then(|v| v.as_str()) {
                    return Some(s.to_string());
                }
            }
        }
    }
    None
}

fn normalize_rerank_body(json_body: &mut serde_json::Value) -> Result<(), RerankProxyError> {
    let query = json_body
        .get("query")
        .and_then(|v| v.as_str())
        .map(str::trim)
        .unwrap_or("");
    if query.is_empty() {
        return Err((
            StatusCode::BAD_REQUEST,
            "invalid_request_error",
            "rerank requires a non-empty query string".to_string(),
        ));
    }

    let raw_documents = json_body
        .get("documents")
        .or_else(|| json_body.get("texts"))
        .and_then(|v| v.as_array())
        .ok_or_else(|| {
            (
                StatusCode::BAD_REQUEST,
                "invalid_request_error",
                "rerank requires a non-empty documents or texts array".to_string(),
            )
        })?;

    if raw_documents.is_empty() {
        return Err((
            StatusCode::BAD_REQUEST,
            "invalid_request_error",
            "rerank requires at least one document".to_string(),
        ));
    }

    let mut documents = Vec::with_capacity(raw_documents.len());
    for (idx, item) in raw_documents.iter().enumerate() {
        let Some(text) = extract_rerank_document_text(item) else {
            return Err((
                StatusCode::BAD_REQUEST,
                "invalid_request_error",
                format!("documents[{idx}] must be a string or object with text/content/document"),
            ));
        };
        documents.push(serde_json::Value::String(text));
    }

    let top_n_value = json_body.get("top_n").or_else(|| json_body.get("top_k"));
    if let Some(value) = top_n_value {
        let parsed = value.as_i64().or_else(|| value.as_str().and_then(|s| s.parse::<i64>().ok()));
        let Some(top_n) = parsed else {
            return Err((
                StatusCode::BAD_REQUEST,
                "invalid_request_error",
                "top_n/top_k must be an integer".to_string(),
            ));
        };
        if top_n < 1 || top_n as usize > documents.len() {
            return Err((
                StatusCode::BAD_REQUEST,
                "invalid_request_error",
                format!("top_n/top_k must be between 1 and {}", documents.len()),
            ));
        }
        json_body["top_n"] = serde_json::Value::Number(top_n.into());
    }

    if let Some(obj) = json_body.as_object_mut() {
        obj.remove("texts");
        obj.remove("top_k");
        obj.insert("documents".to_string(), serde_json::Value::Array(documents));
    }
    Ok(())
}

fn ensure_rerank_model_in_body(
    json_body: &mut serde_json::Value,
    jan_data_folder: &str,
) -> Result<String, RerankProxyError> {
    normalize_rerank_body(json_body)?;

    let requested = json_body.get("model").and_then(|v| v.as_str()).map(str::trim);
    let needs_auto = requested.is_none() || requested == Some("") || requested == Some("auto");
    let model = if needs_auto {
        find_default_reranking_model_id(jan_data_folder).ok_or_else(|| {
            (
                StatusCode::SERVICE_UNAVAILABLE,
                "model_not_available",
                "No local reranking model is available. Import a GGUF reranker or mark a model.yml with reranking: true and pooling: rank.".to_string(),
            )
        })?
    } else {
        requested.unwrap().to_string()
    };

    json_body["model"] = serde_json::Value::String(model.clone());
    Ok(model)
}
''',
    'proxy rerank request normalization and structured errors'
)
replace_once(
    proxy_path,
    "            if let Err(e) = ensure_rerank_model_in_body(&mut json_body, &jan_data_folder) {\n                let mut error_response = Response::builder().status(StatusCode::SERVICE_UNAVAILABLE);\n                error_response = add_cors_headers_with_host_and_origin(\n                    error_response,\n                    &host_header,\n                    &origin_header,\n                    &config.trusted_hosts,\n                );\n                let payload = serde_json::json!({\n                    \"error\": {\n                        \"type\": \"model_not_available\",\n                        \"message\": e\n                    }\n                });\n                return Ok(error_response\n                    .header(hyper::header::CONTENT_TYPE, \"application/json\")\n                    .body(full(payload.to_string()))\n                    .unwrap());\n            }\n",
    "            if let Err((status, error_type, message)) = ensure_rerank_model_in_body(&mut json_body, &jan_data_folder) {\n                let mut error_response = Response::builder().status(status);\n                error_response = add_cors_headers_with_host_and_origin(\n                    error_response,\n                    &host_header,\n                    &origin_header,\n                    &config.trusted_hosts,\n                );\n                let payload = serde_json::json!({\n                    \"error\": {\n                        \"type\": error_type,\n                        \"message\": message\n                    }\n                });\n                return Ok(error_response\n                    .header(hyper::header::CONTENT_TYPE, \"application/json\")\n                    .body(full(payload.to_string()))\n                    .unwrap());\n            }\n",
    'proxy structured rerank errors'
)

# 7. Preserve vector score/rank metadata and expose rerank provenance in RAG citations.
rag_path = p('extensions/rag-extension/src/index.ts')
replace_once(
    rag_path,
    "            vector_score: r.vector_score,\n            rerank_score: r.rerank_score,\n            file_id: r.file_id,\n",
    "            vector_score: r.vector_score ?? r.score,\n            rerank_score: r.rerank_score,\n            rank_source: r.rank_source ?? (reranked.applied ? 'reranker' : 'vector'),\n            original_rank: r.original_rank,\n            file_id: r.file_id,\n",
    'RAG citation score provenance'
)
replace_once(
    rag_path,
    "          first_stage_top_k: firstStageTopK,\n        },\n",
    "          first_stage_top_k: firstStageTopK,\n          candidate_count: results?.length ?? 0,\n          returned_count: reranked.results?.length ?? 0,\n        },\n",
    'RAG reranking metadata counts'
)
replace_regex(
    rag_path,
    r"    const fallback = \{\n      results: \(candidates \|\| \[\]\)\.slice\(0, finalK\),\n      applied: false,\n      model: undefined,\n    \}\n",
    "    const fallbackResults = (candidates || [])\n      .slice(0, finalK)\n      .map((candidate, index) => ({\n        ...candidate,\n        vector_score: candidate?.vector_score ?? candidate?.score,\n        rank_source: 'vector',\n        original_rank: candidate?.original_rank ?? index,\n      }))\n    const fallback = {\n      results: fallbackResults,\n      applied: false,\n      model: undefined,\n    }\n",
    'RAG fallback vector provenance'
)
replace_once(
    rag_path,
    "          return {\n            ...base,\n            vector_score: base?.score,\n            rerank_score: rerankScore,\n            score: rerankScore,\n          }\n",
    "          return {\n            ...base,\n            vector_score: base?.vector_score ?? base?.score,\n            rerank_score: rerankScore,\n            score: rerankScore,\n            rank_source: 'reranker',\n            original_rank: item.index,\n          }\n",
    'RAG reranked provenance'
)

# 4. Add OpenAPI docs for rerank/reranking and structured error schemas.
openapi_path = p('src-tauri/static/openapi.json')
api = json.loads(read(openapi_path))
paths = api.setdefault('paths', {})
rerank_path_spec = {
    'post': {
        'summary': 'Rerank documents',
        'description': 'Scores documents for relevance to a query using a local reranker model. Accepts documents or texts; model may be auto.',
        'operationId': 'createRerank',
        'tags': ['Inference'],
        'requestBody': {
            'required': True,
            'content': {
                'application/json': {
                    'schema': {'$ref': '#/components/schemas/RerankRequestDto'},
                    'example': {
                        'model': 'auto',
                        'query': 'what is rank pooling?',
                        'documents': [
                            'Rank pooling scores query-document pairs.',
                            'Bananas are yellow.'
                        ],
                        'top_n': 2,
                        'return_documents': True
                    }
                }
            }
        },
        'responses': {
            '200': {
                'description': 'Rerank result',
                'content': {'application/json': {'schema': {'$ref': '#/components/schemas/RerankResponseDto'}}}
            },
            '400': {
                'description': 'Invalid rerank request',
                'content': {'application/json': {'schema': {'$ref': '#/components/schemas/ErrorResponseDto'}}}
            },
            '503': {
                'description': 'No local reranker is available',
                'content': {'application/json': {'schema': {'$ref': '#/components/schemas/ErrorResponseDto'}}}
            }
        }
    }
}
paths['/rerank'] = rerank_path_spec
paths['/reranking'] = rerank_path_spec
schemas = api.setdefault('components', {}).setdefault('schemas', {})
schemas['RerankDocumentDto'] = {
    'oneOf': [
        {'type': 'string'},
        {
            'type': 'object',
            'properties': {
                'id': {'type': 'string'},
                'text': {'type': 'string'},
                'content': {'type': 'string'},
                'metadata': {'type': 'object', 'additionalProperties': True}
            },
            'additionalProperties': True
        }
    ]
}
schemas['RerankRequestDto'] = {
    'type': 'object',
    'properties': {
        'model': {'type': 'string', 'default': 'auto', 'description': 'Reranker model id, or auto.'},
        'query': {'type': 'string'},
        'documents': {'type': 'array', 'items': {'$ref': '#/components/schemas/RerankDocumentDto'}},
        'texts': {'type': 'array', 'items': {'$ref': '#/components/schemas/RerankDocumentDto'}, 'description': 'Alias for documents.'},
        'top_n': {'type': 'integer', 'minimum': 1},
        'top_k': {'type': 'integer', 'minimum': 1, 'description': 'Alias for top_n.'},
        'return_documents': {'type': 'boolean', 'default': True},
        'normalize': {'type': 'boolean', 'default': True}
    },
    'required': ['query']
}
schemas['RerankResultDto'] = {
    'type': 'object',
    'properties': {
        'index': {'type': 'integer'},
        'relevance_score': {'type': 'number'},
        'document': {'$ref': '#/components/schemas/RerankDocumentDto'}
    },
    'required': ['index', 'relevance_score']
}
schemas['RerankResponseDto'] = {
    'type': 'object',
    'properties': {
        'object': {'type': 'string', 'default': 'list'},
        'model': {'type': 'string'},
        'results': {'type': 'array', 'items': {'$ref': '#/components/schemas/RerankResultDto'}},
        'usage': {'type': 'object', 'additionalProperties': True}
    },
    'required': ['results']
}
schemas['ErrorResponseDto'] = {
    'type': 'object',
    'properties': {
        'error': {
            'type': 'object',
            'properties': {
                'type': {'type': 'string'},
                'message': {'type': 'string'}
            },
            'required': ['type', 'message']
        }
    },
    'required': ['error']
}
write(openapi_path, json.dumps(api, indent=2) + '\n')

print('Stage 7 reranking completion patch applied.')
