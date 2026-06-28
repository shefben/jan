use hyper::body::Bytes;
use reqwest::Client;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::collections::HashSet;
use std::fs;
use std::path::{Path, PathBuf};
use std::sync::OnceLock;
use tokio::sync::Mutex;
use tauri_plugin_llamacpp::state::LlamacppState;

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct RerankRuntimeConfig {
    pub mode: Option<String>,
    pub model: Option<String>,
    pub fallback_chain: Option<Vec<String>>,
    pub external_base_url: Option<String>,
    pub external_api_key: Option<String>,
    pub external_model: Option<String>,
    pub allow_embedding_similarity_fallback: Option<bool>,
    pub default_top_n: Option<usize>,
    pub max_tokens_per_doc: Option<usize>,
    pub min_relevance_score: Option<f64>,
    pub score_normalization: Option<String>,
    pub evidence_mode: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RerankModelCandidate {
    pub id: String,
    pub name: Option<String>,
    pub size_bytes: Option<u64>,
    pub preferred_for: Vec<String>,
    pub pooling: Option<String>,
    pub score_normalization: Option<String>,
    pub max_tokens_per_doc: Option<usize>,
}

#[derive(Debug, Clone)]
pub struct ExternalRerankTarget {
    pub base_url: String,
    pub api_key: Option<String>,
}

#[derive(Debug, Clone)]
pub struct RerankTrace {
    pub model: String,
    pub profile: String,
    pub provider: String,
    pub fallback_used: bool,
    pub fallback_reason: Option<String>,
    pub candidate_count: usize,
    pub truncated_documents: usize,
    pub return_documents: bool,
    pub normalize_scores: bool,
    pub raw_scores: bool,
    pub evidence_mode: String,
    pub min_relevance_score: Option<f64>,
    pub top_n: Option<usize>,
    pub query: String,
    pub original_documents: Vec<Value>,
}

#[derive(Debug, Clone)]
pub struct PreparedRerankRequest {
    pub body: Bytes,
    pub model_id: String,
    pub trace: RerankTrace,
    pub external: Option<ExternalRerankTarget>,
}

#[derive(Debug, Clone)]
pub struct RerankHttpError {
    pub status: hyper::StatusCode,
    pub kind: String,
    pub message: String,
}

static LAST_RERANK_META: OnceLock<Mutex<Option<Value>>> = OnceLock::new();

fn last_meta() -> &'static Mutex<Option<Value>> {
    LAST_RERANK_META.get_or_init(|| Mutex::new(None))
}

pub async fn record_rerank_observation(meta: Value) {
    let mut guard = last_meta().lock().await;
    *guard = Some(meta);
}

pub fn is_rerank_path(path: &str) -> bool {
    matches!(path, "/rerank" | "/reranking")
}

pub fn is_rerank_status_path(path: &str) -> bool {
    matches!(path, "/rerank/status" | "/reranking/status")
}

pub fn rerank_error_json(kind: &str, message: &str) -> String {
    json!({ "error": { "type": kind, "message": message } }).to_string()
}

fn config_path(jan_data_folder: &str) -> PathBuf {
    PathBuf::from(jan_data_folder).join("llamacpp").join("reranking.json")
}

fn load_runtime_config(jan_data_folder: &str) -> RerankRuntimeConfig {
    let path = config_path(jan_data_folder);
    let raw = match fs::read_to_string(path) {
        Ok(v) => v,
        Err(_) => return RerankRuntimeConfig::default(),
    };
    serde_json::from_str(&raw).unwrap_or_default()
}

fn yaml_bool(v: Option<&Value>) -> bool {
    match v {
        Some(Value::Bool(b)) => *b,
        Some(Value::Object(map)) => map.get("enabled").and_then(Value::as_bool).unwrap_or(true),
        _ => false,
    }
}

fn yaml_string_vec(v: Option<&Value>) -> Vec<String> {
    match v {
        Some(Value::Array(arr)) => arr.iter().filter_map(|x| x.as_str().map(str::to_string)).collect(),
        Some(Value::String(s)) => vec![s.to_string()],
        _ => Vec::new(),
    }
}

fn capability_rerank(v: Option<&Value>) -> bool {
    match v {
        Some(Value::Object(map)) => map.get("rerank").and_then(Value::as_bool).unwrap_or(false),
        Some(Value::Array(arr)) => arr.iter().any(|x| x.as_str() == Some("rerank")),
        _ => false,
    }
}

fn candidate_from_model_yaml(model_id: String, raw: &str) -> Option<RerankModelCandidate> {
    let cfg: Value = serde_yaml::from_str(raw).ok()?;
    let reranking = yaml_bool(cfg.get("reranking")) || capability_rerank(cfg.get("capabilities"));
    let pooling_rank = cfg.get("pooling").and_then(Value::as_str).map(|s| s.eq_ignore_ascii_case("rank")).unwrap_or(false);
    if !reranking && !pooling_rank { return None; }
    let preferred_for = yaml_string_vec(cfg.get("preferred_for"));
    Some(RerankModelCandidate {
        id: model_id,
        name: cfg.get("name").and_then(Value::as_str).map(str::to_string),
        size_bytes: cfg.get("size_bytes").and_then(Value::as_u64),
        preferred_for,
        pooling: cfg.get("pooling").and_then(Value::as_str).map(str::to_string),
        score_normalization: cfg.get("score_normalization").and_then(Value::as_str).map(str::to_string),
        max_tokens_per_doc: cfg.get("max_tokens_per_doc").and_then(Value::as_u64).map(|n| n as usize),
    })
}

fn path_to_model_id(models_root: &Path, model_dir: &Path) -> Option<String> {
    let rel = model_dir.strip_prefix(models_root).ok()?;
    let value = rel.components().map(|c| c.as_os_str().to_string_lossy().to_string()).collect::<Vec<_>>().join("/");
    if value.is_empty() { None } else { Some(value) }
}

fn collect_reranking_models_inner(models_root: &Path, current: &Path, out: &mut Vec<RerankModelCandidate>) {
    let model_yml = current.join("model.yml");
    if model_yml.exists() {
        if let Some(id) = path_to_model_id(models_root, current) {
            if let Ok(raw) = fs::read_to_string(&model_yml) {
                if let Some(candidate) = candidate_from_model_yaml(id, &raw) { out.push(candidate); }
            }
        }
        return;
    }
    let Ok(entries) = fs::read_dir(current) else { return; };
    for entry in entries.flatten() {
        let path = entry.path();
        if path.is_dir() { collect_reranking_models_inner(models_root, &path, out); }
    }
}

pub fn collect_reranking_models(jan_data_folder: &str) -> Vec<RerankModelCandidate> {
    let root = PathBuf::from(jan_data_folder).join("llamacpp").join("models");
    let mut out = Vec::new();
    if root.exists() { collect_reranking_models_inner(&root, &root, &mut out); }
    out.sort_by(|a, b| a.id.cmp(&b.id));
    out
}

async fn router_loaded_ids(llama_state: &LlamacppState, client: &Client) -> HashSet<String> {
    let (url, key) = {
        let guard = llama_state.router.lock().await;
        match guard.as_ref() {
            Some(h) => (format!("http://127.0.0.1:{}/v1/models", h.port), h.api_key.clone()),
            None => return HashSet::new(),
        }
    };
    let Ok(resp) = client.get(url).bearer_auth(key).send().await else { return HashSet::new(); };
    let Ok(json) = resp.json::<Value>().await else { return HashSet::new(); };
    let mut ids = HashSet::new();
    if let Some(arr) = json.get("data").and_then(Value::as_array) {
        for item in arr {
            let loaded = item.get("status").and_then(|s| s.get("value")).and_then(Value::as_str).map(|s| s == "loaded").unwrap_or(false);
            if loaded {
                if let Some(id) = item.get("id").and_then(Value::as_str) { ids.insert(id.to_string()); }
            }
        }
    }
    ids
}

fn value_text(v: &Value) -> String {
    match v {
        Value::String(s) => s.clone(),
        Value::Object(map) => {
            for key in ["text", "content", "page_content", "body"] {
                if let Some(s) = map.get(key).and_then(Value::as_str) { if !s.trim().is_empty() { return s.to_string(); } }
            }
            v.to_string()
        }
        _ => v.to_string(),
    }
}

fn yaml_scalar(v: &Value) -> String {
    match v {
        Value::String(s) => s.replace(['\r', '\n'], " ").trim().to_string(),
        Value::Number(_) | Value::Bool(_) => v.to_string(),
        Value::Null => String::new(),
        _ => v.to_string(),
    }
}

fn structured_doc_to_text(v: &Value) -> String {
    if v.is_string() { return value_text(v); }
    let Some(map) = v.as_object() else { return value_text(v); };
    let text = value_text(v);
    let mut meta = serde_json::Map::new();
    if let Some(m) = map.get("metadata").and_then(Value::as_object) {
        for (k, val) in m { meta.insert(k.clone(), val.clone()); }
    }
    for (k, val) in map {
        if ["text", "content", "page_content", "body", "metadata"].contains(&k.as_str()) { continue; }
        meta.entry(k.clone()).or_insert_with(|| val.clone());
    }
    if meta.is_empty() { return text; }
    let mut lines = Vec::new();
    for (k, val) in meta { if !val.is_null() { lines.push(format!("{}: {}", k, yaml_scalar(&val))); } }
    lines.push("content: |".to_string());
    for line in text.lines() { lines.push(format!("  {}", line)); }
    lines.join("\n")
}

fn truncate_approx_tokens(text: &str, max_tokens: Option<usize>) -> (String, bool) {
    let Some(max_tokens) = max_tokens else { return (text.to_string(), false); };
    if max_tokens == 0 { return (text.to_string(), false); }
    let max_chars = max_tokens.saturating_mul(4).max(1);
    if text.len() <= max_chars { (text.to_string(), false) } else { (text.chars().take(max_chars).collect(), true) }
}

fn looks_like_code(s: &str) -> bool {
    let lower = s.to_lowercase();
    lower.contains("function ") || lower.contains("class ") || lower.contains("#include") || lower.contains("0x") || lower.contains("stack trace") || s.matches('{').count() + s.matches(';').count() > 6 || s.contains("\\") || s.contains("/src/")
}

fn has_non_ascii(s: &str) -> bool { s.chars().any(|c| !c.is_ascii()) }

fn classify_profile(query: &str, docs: &[Value]) -> String {
    let mut sample = query.to_string();
    for d in docs.iter().take(8) { sample.push('\n'); sample.push_str(&value_text(d)); }
    let avg_len = if docs.is_empty() { 0 } else { docs.iter().map(|d| value_text(d).len()).sum::<usize>() / docs.len() };
    if looks_like_code(&sample) { "code".to_string() }
    else if has_non_ascii(&sample) { "multilingual".to_string() }
    else if avg_len > 6000 { "long".to_string() }
    else { "default".to_string() }
}

fn score_candidate(c: &RerankModelCandidate, profile: &str, loaded: &HashSet<String>, preferred: Option<&str>) -> i64 {
    let hay = format!("{} {} {}", c.id, c.name.clone().unwrap_or_default(), c.preferred_for.join(" ")).to_lowercase();
    let mut score = 0i64;
    if preferred == Some(c.id.as_str()) { score += 10_000; }
    if loaded.contains(&c.id) { score += 80; }
    if c.preferred_for.iter().any(|p| p == profile) { score += 90; }
    if profile == "code" && ["code", "coder", "qwen", "asm", "disassembl", "deobfusc"].iter().any(|x| hay.contains(x)) { score += 60; }
    if profile == "multilingual" && ["multi", "jina", "bge", "qwen", "xlm", "m3"].iter().any(|x| hay.contains(x)) { score += 60; }
    if profile == "long" && ["large", "4b", "7b", "8b", "long", "m3"].iter().any(|x| hay.contains(x)) { score += 30; }
    if hay.contains("rerank") || hay.contains("cross") { score += 50; }
    if let Some(size) = c.size_bytes { if size > 0 { score += (30.0 - (size as f64).log10()).max(0.0) as i64; } }
    score
}

fn normalize_score(score: f64, normalize: bool) -> f64 {
    if !normalize { return score; }
    if (0.0..=1.0).contains(&score) { score } else { 1.0 / (1.0 + (-score).exp()) }
}

fn query_terms(query: &str) -> Vec<String> {
    let mut seen = HashSet::new();
    query.to_lowercase().split(|c: char| !c.is_alphanumeric() && c != '_').filter(|s| s.len() >= 3).filter_map(|s| if seen.insert(s.to_string()) { Some(s.to_string()) } else { None }).take(16).collect()
}

fn evidence_for(query: &str, text: &str) -> (String, String) {
    let terms = query_terms(query);
    let mut best = text.lines().next().unwrap_or(text).trim().to_string();
    let mut best_hits = -1i32;
    for s in text.split(|c| c == '.' || c == '!' || c == '?' || c == '\n').take(100) {
        let st = s.trim();
        if st.is_empty() { continue; }
        let lower = st.to_lowercase();
        let hits = terms.iter().filter(|t| lower.contains(t.as_str())).count() as i32;
        if hits > best_hits { best = st.to_string(); best_hits = hits; }
    }
    if best.len() > 600 { best.truncate(600); }
    let contribution = if best_hits > 0 { format!("Matches {} query term{} in the selected passage.", best_hits, if best_hits == 1 { "" } else { "s" }) } else { "Highest-scoring candidate from the reranker.".to_string() };
    (best, contribution)
}

pub async fn prepare_rerank_request(body_bytes: Bytes, jan_data_folder: &str, client: &Client, llama_state: &LlamacppState) -> Result<PreparedRerankRequest, RerankHttpError> {
    let mut body: Value = serde_json::from_slice(&body_bytes).map_err(|e| RerankHttpError { status: hyper::StatusCode::BAD_REQUEST, kind: "invalid_request_error".into(), message: format!("Invalid JSON body: {e}") })?;
    let cfg = load_runtime_config(jan_data_folder);
    if cfg.mode.as_deref() == Some("off") {
        return Err(RerankHttpError { status: hyper::StatusCode::SERVICE_UNAVAILABLE, kind: "reranking_disabled".into(), message: "Reranking is disabled in llamacpp/reranking.json".into() });
    }
    let query = body.get("query").and_then(Value::as_str).map(str::trim).filter(|s| !s.is_empty()).ok_or_else(|| RerankHttpError { status: hyper::StatusCode::BAD_REQUEST, kind: "invalid_request_error".into(), message: "rerank requires a non-empty query string".into() })?.to_string();
    let docs_value = body.get("documents").or_else(|| body.get("texts")).and_then(Value::as_array).cloned().ok_or_else(|| RerankHttpError { status: hyper::StatusCode::BAD_REQUEST, kind: "invalid_request_error".into(), message: "rerank requires a non-empty documents or texts array".into() })?;
    if docs_value.is_empty() {
        return Err(RerankHttpError { status: hyper::StatusCode::BAD_REQUEST, kind: "invalid_request_error".into(), message: "documents/texts must not be empty".into() });
    }
    let profile = body.get("profile").and_then(Value::as_str).map(str::to_string).unwrap_or_else(|| classify_profile(&query, &docs_value));
    let requested_model = body.get("model").and_then(Value::as_str).map(str::trim).filter(|s| !s.is_empty() && *s != "auto").map(str::to_string);
    if cfg.mode.as_deref() == Some("external") {
        let external_base = cfg.external_base_url
            .clone()
            .map(|s| s.trim().trim_end_matches('/').to_string())
            .filter(|s| !s.is_empty())
            .ok_or_else(|| RerankHttpError {
                status: hyper::StatusCode::SERVICE_UNAVAILABLE,
                kind: "external_reranker_not_configured".into(),
                message: "Reranking mode is external, but external_base_url is missing in llamacpp/reranking.json".into(),
            })?;
        let external_model = requested_model
            .clone()
            .or_else(|| cfg.external_model.clone())
            .or_else(|| cfg.model.clone().filter(|m| m != "auto"))
            .unwrap_or_else(|| "rerank".to_string());

        let max_tokens = body
            .get("max_tokens_per_doc")
            .and_then(Value::as_u64)
            .map(|n| n as usize)
            .or(cfg.max_tokens_per_doc);
        let mut docs = Vec::with_capacity(docs_value.len());
        let mut truncated = 0usize;
        for d in &docs_value {
            let formatted = structured_doc_to_text(d);
            let (t, was) = truncate_approx_tokens(&formatted, max_tokens);
            if was { truncated += 1; }
            docs.push(Value::String(t));
        }
        let top_n = body
            .get("top_n")
            .or_else(|| body.get("top_k"))
            .and_then(Value::as_u64)
            .map(|n| (n as usize).max(1).min(docs.len()))
            .or(cfg.default_top_n.map(|n| n.max(1).min(docs.len())));
        let return_documents = body.get("return_documents").and_then(Value::as_bool).unwrap_or(true);
        let normalize_scores = body.get("normalize_scores").or_else(|| body.get("normalize")).and_then(Value::as_bool).unwrap_or(true);
        let raw_scores = body.get("raw_scores").and_then(Value::as_bool).unwrap_or(false);
        let evidence_mode = body
            .get("evidence_mode")
            .and_then(Value::as_str)
            .or(cfg.evidence_mode.as_deref())
            .filter(|m| matches!(*m, "off" | "top_n" | "all"))
            .unwrap_or("off")
            .to_string();
        let min_score = body.get("min_relevance_score").and_then(Value::as_f64).or(cfg.min_relevance_score);

        body["model"] = Value::String(external_model.clone());
        body["documents"] = Value::Array(docs);
        if let Some(n) = top_n { body["top_n"] = json!(n); }
        body["return_documents"] = Value::Bool(false);
        body.as_object_mut().map(|m| { m.remove("texts"); });

        let trace = RerankTrace {
            model: external_model.clone(),
            profile,
            provider: "external".into(),
            fallback_used: false,
            fallback_reason: None,
            candidate_count: docs_value.len(),
            truncated_documents: truncated,
            return_documents,
            normalize_scores,
            raw_scores,
            evidence_mode,
            min_relevance_score: min_score,
            top_n,
            query,
            original_documents: docs_value,
        };

        return Ok(PreparedRerankRequest {
            body: Bytes::from(serde_json::to_vec(&body).unwrap_or_else(|_| b"{}".to_vec())),
            model_id: external_model,
            trace,
            external: Some(ExternalRerankTarget {
                base_url: external_base,
                api_key: cfg.external_api_key.clone(),
            }),
        });
    }

    let preferred = requested_model.clone().or_else(|| cfg.model.clone().filter(|m| m != "auto"));
    let candidates = collect_reranking_models(jan_data_folder);
    let loaded = router_loaded_ids(llama_state, client).await;
    let selected = if let Some(model) = requested_model.clone() {
        candidates.iter().find(|c| c.id == model).cloned().ok_or_else(|| RerankHttpError { status: hyper::StatusCode::NOT_FOUND, kind: "model_capability_error".into(), message: format!("Requested model '{model}' is not marked as a reranker") })?
    } else {
        candidates.iter().max_by_key(|c| score_candidate(c, &profile, &loaded, preferred.as_deref())).cloned().ok_or_else(|| RerankHttpError { status: hyper::StatusCode::SERVICE_UNAVAILABLE, kind: "model_not_available".into(), message: "No local reranking model is available. Import a GGUF reranker or mark model.yml with reranking: true and pooling: rank.".into() })?
    };
    let max_tokens = body.get("max_tokens_per_doc").and_then(Value::as_u64).map(|n| n as usize).or(selected.max_tokens_per_doc).or(cfg.max_tokens_per_doc);
    let mut docs = Vec::with_capacity(docs_value.len());
    let mut truncated = 0usize;
    for d in &docs_value {
        let formatted = structured_doc_to_text(d);
        let (t, was) = truncate_approx_tokens(&formatted, max_tokens);
        if was { truncated += 1; }
        docs.push(Value::String(t));
    }
    let top_n = body.get("top_n").or_else(|| body.get("top_k")).and_then(Value::as_u64).map(|n| n as usize).or(cfg.default_top_n).map(|n| n.max(1).min(docs.len()));
    let return_documents = body.get("return_documents").and_then(Value::as_bool).unwrap_or(true);
    let normalize_scores = body.get("normalize_scores").or_else(|| body.get("normalize")).and_then(Value::as_bool).unwrap_or(true);
    let raw_scores = body.get("raw_scores").and_then(Value::as_bool).unwrap_or(false);
    let evidence_mode = body.get("evidence_mode").and_then(Value::as_str).or(cfg.evidence_mode.as_deref()).filter(|m| matches!(*m, "off" | "top_n" | "all")).unwrap_or("off").to_string();
    let min_score = body.get("min_relevance_score").and_then(Value::as_f64).or(cfg.min_relevance_score);
    body["model"] = Value::String(selected.id.clone());
    body["documents"] = Value::Array(docs);
    if let Some(n) = top_n { body["top_n"] = json!(n); }
    body["return_documents"] = Value::Bool(false);
    body.as_object_mut().map(|m| { m.remove("texts"); });
    let trace = RerankTrace { model: selected.id.clone(), profile, provider: "local_gguf".into(), fallback_used: false, fallback_reason: None, candidate_count: docs_value.len(), truncated_documents: truncated, return_documents, normalize_scores, raw_scores, evidence_mode, min_relevance_score: min_score, top_n, query, original_documents: docs_value };
    Ok(PreparedRerankRequest { body: Bytes::from(serde_json::to_vec(&body).unwrap_or_else(|_| b"{}".to_vec())), model_id: selected.id, trace, external: None })
}

pub fn postprocess_rerank_response(mut raw: Value, trace: RerankTrace, latency_ms: u64) -> Value {
    let arr = raw.get_mut("results").and_then(Value::as_array_mut).map(|a| std::mem::take(a)).or_else(|| raw.get_mut("data").and_then(Value::as_array_mut).map(|a| std::mem::take(a))).unwrap_or_default();
    let mut results = Vec::new();
    for (fallback_index, item) in arr.into_iter().enumerate() {
        let index = item.get("index").and_then(Value::as_u64).map(|n| n as usize).unwrap_or(fallback_index);
        let raw_score = item.get("relevance_score").or_else(|| item.get("score")).or_else(|| item.get("logit")).and_then(Value::as_f64).unwrap_or(0.0);
        let score = normalize_score(raw_score, trace.normalize_scores && !trace.raw_scores);
        if let Some(min) = trace.min_relevance_score { if score < min { continue; } }
        let mut obj = serde_json::Map::new();
        obj.insert("index".into(), json!(index));
        obj.insert("relevance_score".into(), json!(score));
        if trace.raw_scores { obj.insert("raw_relevance_score".into(), json!(raw_score)); }
        if trace.return_documents { if let Some(doc) = trace.original_documents.get(index) { obj.insert("document".into(), doc.clone()); } }
        if trace.evidence_mode != "off" {
            if let Some(doc) = trace.original_documents.get(index) {
                let text = value_text(doc);
                let (evidence, contribution) = evidence_for(&trace.query, &text);
                obj.insert("evidence".into(), Value::String(evidence));
                obj.insert("contribution".into(), Value::String(contribution));
            }
        }
        results.push(Value::Object(obj));
    }
    results.sort_by(|a, b| b.get("relevance_score").and_then(Value::as_f64).unwrap_or(0.0).partial_cmp(&a.get("relevance_score").and_then(Value::as_f64).unwrap_or(0.0)).unwrap_or(std::cmp::Ordering::Equal));
    if let Some(n) = trace.top_n { results.truncate(n); }
    let meta = json!({
        "model": trace.model,
        "profile": trace.profile,
        "provider": trace.provider,
        "fallback_used": trace.fallback_used,
        "fallback_reason": trace.fallback_reason,
        "candidate_count": trace.candidate_count,
        "returned_count": results.len(),
        "truncated_documents": trace.truncated_documents,
        "normalize_scores": trace.normalize_scores,
        "raw_scores": trace.raw_scores,
        "latency_ms": latency_ms,
    });
    json!({ "object": "list", "model": meta.get("model").cloned().unwrap_or(json!("")), "results": results, "usage": raw.get("usage").cloned().unwrap_or(Value::Null), "meta": meta })
}

pub async fn build_rerank_status_json(jan_data_folder: &str, llama_state: &LlamacppState, client: &Client) -> Value {
    let cfg = load_runtime_config(jan_data_folder);
    let models = collect_reranking_models(jan_data_folder);
    let loaded = router_loaded_ids(llama_state, client).await;
    let last = last_meta().lock().await.clone();
    json!({
        "enabled": cfg.mode.as_deref() != Some("off"),
        "mode": cfg.mode.unwrap_or_else(|| "auto".to_string()),
        "configured_model": cfg.model.unwrap_or_else(|| "auto".to_string()),
        "available_models": models.iter().map(|m| json!({ "id": m.id, "name": m.name, "loaded": loaded.contains(&m.id), "pooling": m.pooling, "preferred_for": m.preferred_for })).collect::<Vec<_>>(),
        "fallback_chain": cfg.fallback_chain.unwrap_or_else(|| vec!["local_gguf".into(), "disabled".into()]),
        "external_configured": cfg.external_base_url.is_some(),
        "last_request": last,
    })
}
