use std::time::{SystemTime, UNIX_EPOCH};

use base64::{engine::general_purpose::URL_SAFE_NO_PAD, Engine as _};
use rand::{rngs::OsRng, RngCore};

use crate::signed_auth;

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct GeneratedKeyMaterial {
    pub scheme: &'static str,
    pub public_material: Option<String>,
    pub private_key: String,
    pub note: &'static str,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum KeyScheme {
    Random,
    MiniRsa,
    Ed25519,
}

pub fn generate(scheme: KeyScheme) -> GeneratedKeyMaterial {
    match scheme {
        KeyScheme::Random => GeneratedKeyMaterial {
            scheme: "random",
            public_material: None,
            private_key: generate_random_private_key(),
            note: "URL-friendly random prompt key generated with the operating-system RNG.",
        },
        KeyScheme::MiniRsa => {
            let rsa = generate_mini_rsa();
            GeneratedKeyMaterial {
                scheme: "mini-rsa",
                public_material: Some(format!("mrsa_e{}_n{}", rsa.e, rsa.n)),
                private_key: format!("mrsa_d{}_n{}", rsa.d, rsa.n),
                note:
                    "Educational/test Mini-RSA material only; not production-strength cryptography.",
            }
        }
        KeyScheme::Ed25519 => {
            let (private_key, public_key) = signed_auth::generate_keypair();
            GeneratedKeyMaterial {
                scheme: signed_auth::SIGNATURE_SCHEME,
                public_material: Some(public_key),
                private_key,
                note: "Production Ed25519 signing material. Keep the private key with the participant; register only the public key with Conversation Blackboard.",
            }
        }
    }
}

pub fn generate_random_private_key() -> String {
    let mut bytes = [0_u8; 24];
    OsRng.fill_bytes(&mut bytes);
    format!("wk_{}", URL_SAFE_NO_PAD.encode(bytes))
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
struct MiniRsaMaterial {
    p: u64,
    q: u64,
    n: u64,
    phi: u64,
    e: u64,
    d: u64,
}

fn generate_mini_rsa() -> MiniRsaMaterial {
    let seed = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos();
    generate_mini_rsa_from_seed(seed)
}

fn generate_mini_rsa_from_seed(seed: u128) -> MiniRsaMaterial {
    let primes: Vec<u64> = (10..100).filter(|value| is_prime(*value)).collect();
    debug_assert!(primes.len() >= 2);

    let p_index = (seed % primes.len() as u128) as usize;
    let q_offset = ((seed / primes.len() as u128) % (primes.len() - 1) as u128) as usize + 1;
    let q_index = (p_index + q_offset) % primes.len();

    build_mini_rsa(primes[p_index], primes[q_index])
}

fn build_mini_rsa(p: u64, q: u64) -> MiniRsaMaterial {
    debug_assert!(p != q && is_prime(p) && is_prime(q));

    let n = p * q;
    let phi = (p - 1) * (q - 1);
    let mut e = 3_u64;
    while e < phi && gcd(e, phi) != 1 {
        e += 2;
    }
    let d = mod_inverse(e, phi).expect("coprime exponent must have a modular inverse");

    MiniRsaMaterial { p, q, n, phi, e, d }
}

fn is_prime(n: u64) -> bool {
    if n < 2 {
        return false;
    }
    if n.is_multiple_of(2) {
        return n == 2;
    }

    let mut divisor = 3_u64;
    while divisor <= n / divisor {
        if n.is_multiple_of(divisor) {
            return false;
        }
        divisor += 2;
    }
    true
}

fn gcd(mut a: u64, mut b: u64) -> u64 {
    while b != 0 {
        (a, b) = (b, a % b);
    }
    a
}

fn mod_inverse(value: u64, modulus: u64) -> Option<u64> {
    if modulus == 0 {
        return None;
    }
    let (gcd_value, coefficient, _) = extended_gcd(value as i128, modulus as i128);
    if gcd_value != 1 {
        return None;
    }
    Some(coefficient.rem_euclid(modulus as i128) as u64)
}

fn extended_gcd(a: i128, b: i128) -> (i128, i128, i128) {
    if b == 0 {
        return (a, 1, 0);
    }
    let (gcd_value, x1, y1) = extended_gcd(b, a % b);
    (gcd_value, y1, x1 - (a / b) * y1)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn integer_primality_handles_small_values() {
        assert!(!is_prime(0));
        assert!(!is_prime(1));
        assert!(is_prime(2));
        assert!(is_prime(97));
        assert!(!is_prime(99));
    }

    #[test]
    fn euclid_and_extended_euclid_match_rsa_relation() {
        assert_eq!(gcd(17, 3120), 1);
        assert_eq!(mod_inverse(17, 3120), Some(2753));
        assert_eq!((17_u64 * 2753_u64) % 3120_u64, 1);
    }

    #[test]
    fn deterministic_mini_rsa_uses_distinct_primes_and_valid_inverse() {
        for seed in [0_u128, 1, 2, 17, 1234, u64::MAX as u128] {
            let material = generate_mini_rsa_from_seed(seed);
            assert_ne!(material.p, material.q);
            assert!(is_prime(material.p));
            assert!(is_prime(material.q));
            assert_eq!(material.n, material.p * material.q);
            assert_eq!(material.phi, (material.p - 1) * (material.q - 1));
            assert_eq!(gcd(material.e, material.phi), 1);
            assert_eq!((material.e * material.d) % material.phi, 1);
        }
    }

    #[test]
    fn generated_key_material_is_prompt_friendly() {
        let mini = generate(KeyScheme::MiniRsa);
        assert_eq!(mini.scheme, "mini-rsa");
        assert!(mini
            .public_material
            .as_deref()
            .unwrap()
            .starts_with("mrsa_e"));
        assert!(mini.private_key.starts_with("mrsa_d"));
        assert!(!mini.private_key.chars().any(char::is_whitespace));

        let random = generate(KeyScheme::Random);
        assert_eq!(random.scheme, "random");
        assert!(random.public_material.is_none());
        assert!(random.private_key.starts_with("wk_"));
        assert!(!random.private_key.chars().any(char::is_whitespace));

        let ed25519 = generate(KeyScheme::Ed25519);
        assert_eq!(ed25519.scheme, signed_auth::SIGNATURE_SCHEME);
        assert!(ed25519
            .public_material
            .as_deref()
            .unwrap()
            .starts_with(signed_auth::PUBLIC_KEY_PREFIX));
        assert!(ed25519
            .private_key
            .starts_with(signed_auth::PRIVATE_KEY_PREFIX));
        assert!(!ed25519.private_key.chars().any(char::is_whitespace));
    }
}
