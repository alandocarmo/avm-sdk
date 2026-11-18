#![forbid(unsafe_code)]

//! One contract, parent and child at once. Its init accepts constructor data
//! 00 and refuses 01 (status 1400). `probe.spawn` (owner key) sends an
//! internal deploy of this same code, in this DApp, with a callback:
//!
//! - mode 0: constructor data 01 (refused), the deploy carries SHELL;
//! - mode 1: constructor data 01 (refused), no SHELL (the address is funded
//!   beforehand);
//! - mode 2: constructor data 00 (accepted), the deploy carries SHELL.
//!
//! `probe.result` records the callback's status and the SHELL it returned.
//! Data: "IRP1" | owner key 32 | package, WASM and descriptor hashes 96 |
//! selection 32 | last status u32 | SHELL returned u128 | callbacks u8.

use avm_contract_sdk::crypto::blake3;
use avm_contract_sdk::set_owner_key;
use avm_contract_sdk::AccountAddress;
use avm_contract_sdk::CallbackIntent;
use avm_contract_sdk::Caller;
use avm_contract_sdk::CanonicalMessage;
use avm_contract_sdk::Contract;
use avm_contract_sdk::CurrencyAmount;
use avm_contract_sdk::MessageInput;
use avm_contract_sdk::MessageOutput;
use avm_contract_sdk::OutboundIntent;
use avm_contract_sdk::StateUpdate;
use avm_contract_sdk::OWNER_KEY_BYTES;

pub mod generated {
    include!(env!("AVM_CONTRACT_BINDINGS"));
}

const SHELL_CURRENCY_ID: u32 = 2;
const CHILD_SHELL: u128 = 1_000_000_000;
const MAGIC: &[u8; 4] = b"IRP1";
// Matches `owner_key_offset` in avm-contract.toml.
const OWNER_KEY_OFFSET: usize = 4;
const TEMPLATE: usize = OWNER_KEY_OFFSET + OWNER_KEY_BYTES;
const SELECTION: usize = TEMPLATE + 96;
const STATUS: usize = SELECTION + 32;
const RETURNED: usize = STATUS + 4;
const CALLBACKS: usize = RETURNED + 16;
const DATA_BYTES: usize = CALLBACKS + 1;
const ENVELOPE_HEADER_BYTES: usize = 288;

struct InitRefusalProbe;

/// The internal deploy of this code with owner `owner`, a one-byte salt and
/// one byte of constructor data: its envelope and its account id.
fn deploy_of(state: &[u8], salt: u8, init: u8) -> (Vec<u8>, [u8; 32]) {
    let owner = &state[OWNER_KEY_OFFSET..TEMPLATE];
    let template = &state[TEMPLATE..SELECTION];
    let init_hash = blake3(&[init]);
    let mut envelope = vec![0; ENVELOPE_HEADER_BYTES];
    envelope[..4].copy_from_slice(b"ACDP");
    envelope[8..40].copy_from_slice(&state[SELECTION..STATUS]);
    envelope[40..136].copy_from_slice(template);
    envelope[136..168].copy_from_slice(owner);
    envelope[168..200].copy_from_slice(&init_hash);
    envelope[266..268].copy_from_slice(&1u16.to_le_bytes());
    envelope[268..272].copy_from_slice(&1u32.to_le_bytes());
    envelope.push(salt);
    envelope.push(init);
    // blake3("AVM_ACCOUNT_ID" | u16 0 | package | owner | u16 salt length |
    // salt | blake3(constructor data)).
    let mut preimage = b"AVM_ACCOUNT_ID".to_vec();
    preimage.extend_from_slice(&[0, 0]);
    preimage.extend_from_slice(&template[..32]);
    preimage.extend_from_slice(owner);
    preimage.extend_from_slice(&1u16.to_le_bytes());
    preimage.push(salt);
    preimage.extend_from_slice(&init_hash);
    (envelope, blake3(&preimage))
}

impl Contract for InitRefusalProbe {
    fn init(input: MessageInput<'_>) -> MessageOutput {
        let Ok(deploy) = input.deploy() else {
            return MessageOutput::failure(generated::errors::INVALID_CALL);
        };
        match deploy.init_data() {
            [0] => {}
            [1] => return MessageOutput::failure(generated::errors::REFUSED),
            _ => return MessageOutput::failure(generated::errors::INVALID_CALL),
        }
        let body = input.message.body();
        if body.len() < ENVELOPE_HEADER_BYTES || &body[..4] != b"ACDP" {
            return MessageOutput::failure(generated::errors::INVALID_CALL);
        }
        let mut data = vec![0; DATA_BYTES];
        data[..4].copy_from_slice(MAGIC);
        if set_owner_key(&mut data, OWNER_KEY_OFFSET as u32, deploy.owner_id()).is_err() {
            return MessageOutput::failure(generated::errors::INVALID_CALL);
        }
        data[TEMPLATE..SELECTION].copy_from_slice(&body[40..136]);
        data[SELECTION..STATUS].copy_from_slice(&input.selected_code_id);
        MessageOutput::success(StateUpdate::FullReplace(data))
    }

    fn process_message(input: MessageInput<'_>) -> MessageOutput {
        if input.state.len() != DATA_BYTES || &input.state[..4] != MAGIC {
            return MessageOutput::failure(generated::errors::INVALID_CALL);
        }
        if input.message.is_callback() {
            let CanonicalMessage::Internal(message) = input.message else {
                return MessageOutput::failure(generated::errors::INVALID_CALL);
            };
            let Some(header) = message.callback else {
                return MessageOutput::failure(generated::errors::INVALID_CALL);
            };
            if message.method_id != generated::methods::PROBE_RESULT {
                return MessageOutput::failure(generated::errors::INVALID_CALL);
            }
            let returned: u128 = input
                .message
                .extra()
                .iter()
                .filter(|c| c.currency_id == SHELL_CURRENCY_ID)
                .map(|c| c.amount)
                .sum();
            let mut data = input.state.to_vec();
            data[STATUS..RETURNED].copy_from_slice(&header.status_code.to_le_bytes());
            data[RETURNED..CALLBACKS].copy_from_slice(&returned.to_le_bytes());
            data[CALLBACKS] = data[CALLBACKS].wrapping_add(1);
            return MessageOutput::success(StateUpdate::FullReplace(data));
        }
        let (Caller::External(_), generated::methods::PROBE_SPAWN) =
            (input.caller, input.message.method_id())
        else {
            return MessageOutput::failure(generated::errors::INVALID_CALL);
        };
        let [mode, salt] = input.message.body() else {
            return MessageOutput::failure(generated::errors::INVALID_CALL);
        };
        let (init, shell) = match mode {
            0 => (1, CHILD_SHELL),
            1 => (1, 0),
            2 => (0, CHILD_SHELL),
            _ => return MessageOutput::failure(generated::errors::INVALID_CALL),
        };
        let (envelope, account_id) = deploy_of(input.state, *salt, init);
        let mut out = MessageOutput::success(StateUpdate::FullReplace(input.state.to_vec()));
        out.outbound_intents.push(OutboundIntent {
            input_index: 0,
            destination: AccountAddress {
                dapp_id: input.destination.dapp_id,
                account_id,
            },
            fuel_fee_shells: 1000,
            extra: if shell == 0 {
                vec![]
            } else {
                vec![CurrencyAmount {
                    currency_id: SHELL_CURRENCY_ID,
                    amount: shell,
                }]
            },
            // An internal deploy carries method id 0.
            method_id: 0,
            body: envelope,
        });
        out.callback_intents.push(CallbackIntent {
            outbound_index: 0,
            handler_method_id: generated::methods::PROBE_RESULT,
        });
        out
    }
}

avm_contract_sdk::export_message_contract!(InitRefusalProbe);
