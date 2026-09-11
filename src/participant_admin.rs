use std::error::Error;

use clap::{Subcommand, ValueEnum};

use crate::participant_key::{self, KeyScheme};

type DynResult<T = ()> = Result<T, Box<dyn Error + Send + Sync>>;

#[derive(Clone, Copy, Debug, Eq, PartialEq, ValueEnum)]
pub enum GeneratorScheme {
    Random,
    MiniRsa,
    Ed25519,
}

#[derive(Debug, Subcommand)]
pub enum ParticipantCommand {
    /// Generate participant key material without registering it.
    Generate {
        #[arg(long, value_enum, default_value_t = GeneratorScheme::Random)]
        scheme: GeneratorScheme,
    },
}

pub fn dispatch(command: ParticipantCommand) -> DynResult {
    match command {
        ParticipantCommand::Generate { scheme } => {
            let scheme = match scheme {
                GeneratorScheme::Random => KeyScheme::Random,
                GeneratorScheme::MiniRsa => KeyScheme::MiniRsa,
                GeneratorScheme::Ed25519 => KeyScheme::Ed25519,
            };
            let material = participant_key::generate(scheme);
            print_material(&material);
            Ok(())
        }
    }
}

fn print_material(material: &participant_key::GeneratedKeyMaterial) {
    println!("PARTICIPANT KEY MATERIAL");
    println!("scheme          : {}", material.scheme);
    println!(
        "public_material : {}",
        material.public_material.as_deref().unwrap_or("")
    );
    println!("private_key     : {}", material.private_key);
    println!("note            : {}", material.note);
    println!();
    if material.scheme == crate::signed_auth::SIGNATURE_SCHEME {
        println!(
            "Register only the public material with `web set-signing-key --participant-id <id> --public-key <public_material>`. Keep the private key with the participant."
        );
    } else {
        println!(
            "Register this material with `web provision --key <private_key>` or use your own externally generated key material."
        );
    }
}
