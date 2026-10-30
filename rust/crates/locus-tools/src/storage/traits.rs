//! MemoryBackend trait for all storage backends.

use locus_core::{LocusError, RetrievalResult};
use serde_json::Value;

pub trait MemoryBackend: Send + Sync {
    fn backend_id(&self) -> &str;
    fn store(
        &self,
        content: &str,
        source: &str,
        metadata: Option<&Value>,
    ) -> Result<String, LocusError>;
    fn retrieve(
        &self,
        query: &str,
        top_k: usize,
    ) -> Result<Vec<RetrievalResult>, LocusError>;
    fn delete(&self, doc_id: &str) -> Result<bool, LocusError>;
    fn clear(&self) -> Result<(), LocusError>;
    fn count(&self) -> Result<usize, LocusError>;
}
