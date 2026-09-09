use serde::Serialize;

#[derive(Debug, Clone, Serialize)]
pub struct Identity {
    pub source: String,
    pub instance: String,
    pub label: Option<String>,
}

#[derive(Debug, Clone, Serialize)]
pub struct Message {
    pub id: i64,
    pub created_at: i64,
    pub channel: String,
    pub source: String,
    pub instance: String,
    pub kind: String,
    pub body: String,
    pub reply_to: Option<i64>,
}

#[derive(Debug, Clone, Serialize)]
pub struct ChannelSummary {
    pub channel: String,
    pub message_count: i64,
    pub last_id: i64,
}
