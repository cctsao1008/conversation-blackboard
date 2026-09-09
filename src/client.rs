use std::{error::Error, fmt, time::Duration};

use reqwest::blocking::{Client, Response};
use serde::{de::DeserializeOwned, Deserialize, Serialize};
use url::Url;

use crate::model::{ChannelSummary, Identity, Message};

#[derive(Debug)]
pub struct ClientError {
    status: Option<u16>,
    code: String,
}

impl ClientError {
    fn local(code: impl Into<String>) -> Self {
        Self {
            status: None,
            code: code.into(),
        }
    }

    fn http(status: u16, code: impl Into<String>) -> Self {
        Self {
            status: Some(status),
            code: code.into(),
        }
    }

    pub fn status(&self) -> Option<u16> {
        self.status
    }

    pub fn code(&self) -> &str {
        &self.code
    }
}

impl fmt::Display for ClientError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self.status {
            Some(status) => write!(f, "blackboard HTTP {status}: {}", self.code),
            None => write!(f, "blackboard client: {}", self.code),
        }
    }
}

impl Error for ClientError {}

pub struct BlackboardClient {
    base_url: Url,
    token: String,
    http: Client,
}

impl BlackboardClient {
    pub fn new(base_url: &str, token: String, timeout: Duration) -> Result<Self, ClientError> {
        if token.is_empty() {
            return Err(ClientError::local("empty bearer token"));
        }
        let mut normalized = base_url.trim().trim_end_matches('/').to_owned();
        normalized.push('/');
        let base_url = Url::parse(&normalized)
            .map_err(|_| ClientError::local("invalid endpoint URL"))?;
        if !matches!(base_url.scheme(), "http" | "https") {
            return Err(ClientError::local("endpoint URL must use http or https"));
        }
        let http = Client::builder()
            .timeout(timeout)
            .build()
            .map_err(|_| ClientError::local("failed to build HTTP client"))?;
        Ok(Self {
            base_url,
            token,
            http,
        })
    }

    pub fn health(&self) -> Result<(), ClientError> {
        #[derive(Deserialize)]
        struct HealthResponse {
            status: String,
        }
        let response: HealthResponse = self.request_json(
            self.http.get(self.url("api/health")?).send(),
            false,
        )?;
        if response.status == "ok" {
            Ok(())
        } else {
            Err(ClientError::local("unexpected health response"))
        }
    }

    pub fn whoami(&self) -> Result<Identity, ClientError> {
        self.request_json(
            self.http
                .get(self.url("api/whoami")?)
                .bearer_auth(&self.token)
                .send(),
            true,
        )
    }

    pub fn channels(&self) -> Result<Vec<ChannelSummary>, ClientError> {
        #[derive(Deserialize)]
        struct ChannelsResponse {
            channels: Vec<ChannelSummary>,
        }
        let response: ChannelsResponse = self.request_json(
            self.http
                .get(self.url("api/channels")?)
                .bearer_auth(&self.token)
                .send(),
            true,
        )?;
        Ok(response.channels)
    }

    pub fn messages(
        &self,
        after: i64,
        channel: Option<&str>,
        limit: usize,
    ) -> Result<Vec<Message>, ClientError> {
        if after < 0 {
            return Err(ClientError::local("after must be >= 0"));
        }
        if !(1..=200).contains(&limit) {
            return Err(ClientError::local("limit must be between 1 and 200"));
        }
        let mut url = self.url("api/messages")?;
        {
            let mut query = url.query_pairs_mut();
            query.append_pair("after", &after.to_string());
            query.append_pair("limit", &limit.to_string());
            if let Some(channel) = channel {
                query.append_pair("channel", channel);
            }
        }
        #[derive(Deserialize)]
        struct MessagesResponse {
            messages: Vec<Message>,
        }
        let response: MessagesResponse = self.request_json(
            self.http.get(url).bearer_auth(&self.token).send(),
            true,
        )?;
        Ok(response.messages)
    }

    pub fn post(
        &self,
        channel: &str,
        kind: &str,
        body: &str,
        reply_to: Option<i64>,
    ) -> Result<Message, ClientError> {
        #[derive(Serialize)]
        struct PostRequest<'a> {
            channel: &'a str,
            kind: &'a str,
            body: &'a str,
            reply_to: Option<i64>,
        }
        #[derive(Deserialize)]
        struct PostResponse {
            message: Message,
        }
        let response: PostResponse = self.request_json(
            self.http
                .post(self.url("api/messages")?)
                .bearer_auth(&self.token)
                .json(&PostRequest {
                    channel,
                    kind,
                    body,
                    reply_to,
                })
                .send(),
            true,
        )?;
        Ok(response.message)
    }

    fn url(&self, path: &str) -> Result<Url, ClientError> {
        self.base_url
            .join(path)
            .map_err(|_| ClientError::local("failed to build endpoint URL"))
    }

    fn request_json<T: DeserializeOwned>(
        &self,
        response: Result<Response, reqwest::Error>,
        authenticated: bool,
    ) -> Result<T, ClientError> {
        let response = response.map_err(|_| {
            if authenticated {
                ClientError::local("authenticated request failed")
            } else {
                ClientError::local("request failed")
            }
        })?;
        let status = response.status();
        if !status.is_success() {
            #[derive(Deserialize)]
            struct ErrorResponse {
                error: Option<String>,
            }
            let code = response
                .json::<ErrorResponse>()
                .ok()
                .and_then(|body| body.error)
                .unwrap_or_else(|| "http_error".to_owned());
            return Err(ClientError::http(status.as_u16(), code));
        }
        response
            .json::<T>()
            .map_err(|_| ClientError::local("invalid JSON response"))
    }
}
