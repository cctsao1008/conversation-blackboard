#![cfg_attr(not(test), allow(dead_code))]

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct AdapterProfile {
    pub name: &'static str,
    pub transport: &'static str,
    pub capabilities: &'static [&'static str],
    pub asynchronous: bool,
}

pub const REST: AdapterProfile = AdapterProfile {
    name: "rest",
    transport: "http",
    capabilities: &[
        "read_messages",
        "post_message",
        "access_context",
        "execution_receipt",
        "manage_channels",
    ],
    asynchronous: false,
};

pub const MCP: AdapterProfile = AdapterProfile {
    name: "mcp",
    transport: "streamable-http",
    capabilities: &[
        "read_messages",
        "post_message",
        "access_context",
        "execution_receipt",
    ],
    asynchronous: false,
};

pub const GITHUB_MAILBOX: AdapterProfile = AdapterProfile {
    name: "github-mailbox",
    transport: "github-issues-webhook",
    capabilities: &["post_message"],
    asynchronous: true,
};

pub const CLI: AdapterProfile = AdapterProfile {
    name: "cli",
    transport: "native-process",
    capabilities: &["participant_admin", "database_admin", "client_projection"],
    asynchronous: false,
};

pub const PROFILES: &[AdapterProfile] = &[REST, MCP, GITHUB_MAILBOX, CLI];

pub fn profile(name: &str) -> Option<&'static AdapterProfile> {
    PROFILES.iter().find(|profile| profile.name == name)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn required_adapter_profiles_are_explicit() {
        assert_eq!(profile("rest"), Some(&REST));
        assert_eq!(profile("mcp"), Some(&MCP));
        assert_eq!(profile("github-mailbox"), Some(&GITHUB_MAILBOX));
    }

    #[test]
    fn semantic_parity_does_not_require_transport_parity() {
        assert!(REST.capabilities.contains(&"post_message"));
        assert!(MCP.capabilities.contains(&"post_message"));
        assert!(GITHUB_MAILBOX.capabilities.contains(&"post_message"));
        assert!(REST.capabilities.contains(&"access_context"));
        assert!(MCP.capabilities.contains(&"access_context"));
        assert!(!GITHUB_MAILBOX.capabilities.contains(&"access_context"));
        let github = profile("github-mailbox").unwrap();
        assert!(github.asynchronous);
    }
}
